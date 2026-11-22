# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Переходы на стыке двух клипов — в духе Sapphire Transitions / переходов Premiere.

Переход — эффект категории «transition» на входящем (верхнем) слое B. Окно перехода стоит у точки входа B
(In) и задаётся параметрами Duration и Alignment. На это время рендер берёт слой A — ближайший под B, который
начался раньше стыка, — и рисует вместо обоих T(A, B, прогресс 0..1). Если A нет, переход идёт из прозрачного.
Код свой: поведение повторяем по виду, а не реализацию Boris FX. Кадр — BGRA premultiplied float32 0..255.
"""
from __future__ import annotations

import math
import zlib

import numpy as np

from .effects import (CATEGORIES, EFFECTS, FILTERS, EffectDef, Param, _grid, _remap, active_effects, blur,
                      shift, values)

CATEGORIES["transition"] = ("Переходы", "Transitions")
TIMING = ("Время", "Timing")
ALIGN = [("По центру стыка", "Center at Cut"), ("Начало на стыке", "Start at Cut"), ("Конец на стыке", "End at Cut")]


EDGES = [("Вход слоя (из клипа ниже)", "Layer In (from clip below)"), ("Выход слоя (в то, что ниже)", "Layer Out (to what is below)")]


def _timing(dur=16.0, ease=1.0):
    return [Param("edge", "Край", "Edge", [0.0], kind="choice", choices=EDGES, group=TIMING),
            Param("dur", "Длительность", "Duration", [dur], 1.0, " fr", 1, 600, group=TIMING),
            Param("align", "Выравнивание", "Alignment", [0.0], kind="choice", choices=ALIGN, group=TIMING),
            Param("ease", "Сглаживание", "Ease", [ease], kind="choice",
                  choices=[("Линейно", "Linear"), ("Плавно", "Ease In-Out")], group=TIMING)]


# ============================================================================ окно и партнёр
def transition_fxs(layer):
    """Включённые эффекты-переходы слоя (на входе и/или на выходе)."""
    return [fx for fx in active_effects(layer) if EFFECTS[fx["type"]].transition]


def transition_fx(layer):
    fxs = transition_fxs(layer)
    return fxs[0] if fxs else None


def window(layer, fx) -> tuple[int, int, dict]:
    """Кадры композиции [w0, w1) окна перехода и значения параметров (берутся на точке входа).
    Край «Выход» — окно у Out слоя: слой уходит в то, что под ним."""
    v = values(layer, fx, layer.local(layer.in_point))
    d = max(1, int(round(v["dur"])))
    c = layer.out_point if v.get("edge", 0) == 1 else layer.in_point
    w0 = c - d // 2 if v["align"] == 0 else (c if v["align"] == 1 else c - d)
    return w0, w0 + d, v


def partner(comp, b):
    """Слой A под B: начался до стыка, а его конец ближе всех к стыку."""
    c, best = b.in_point, None
    for l in comp.layers[comp.layers.index(b) + 1:]:
        if not l.visible or l.adjustment or l.kind in ("audio", "camera", "light", "null") or l.in_point >= c:
            continue
        if best is None or abs(l.out_point - c) < abs(best.out_point - c):
            best = l
    return best


def transitions_at(comp, frame) -> dict:
    """{id слоя B: (A или None, прогресс 0..1, fx, values)} для переходов, идущих на этом кадре.
    Для края «Выход» A = None, а в values ключ edge == 1: render подставит слой как A и пустоту как B."""
    out = {}
    for b in comp.layers:
        if not b.visible or b.adjustment or b.kind in ("audio", "camera", "light", "null"):
            continue
        for fx in transition_fxs(b):
            w0, w1, v = window(b, fx)
            if w0 <= frame < w1:
                p = (frame - w0 + 0.5) / (w1 - w0)
                if v["ease"] == 1:
                    p = p * p * (3 - 2 * p)
                out[b.id] = (None if v.get("edge", 0) == 1 else partner(comp, b), p, fx, v)
                break
    return out


def apply_transition(fx, a, b, p, v, scale, frame, fps):
    seed = zlib.crc32(fx["id"].encode()) % 997
    out = TRANSITIONS[fx["type"]](a, b, p, v, scale=scale, frame=frame, fps=fps, seed=seed)
    out[..., 3] = np.clip(out[..., 3], 0, 255)
    out[..., :3] = np.minimum(np.clip(out[..., :3], 0, 255), out[..., 3:4])   # premultiplied: RGB ≤ A
    return out


# ============================================================================ помощники
def _mix(a, b, t):
    return a + (b - a) * t


def _bell(p, width=1.0):
    """0 по краям окна, 1 в середине (ширина — доля окна)."""
    x = 1.0 - abs(p - 0.5) / max(width / 2, 1e-6)
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def _smooth(e0, e1, x):
    t = np.clip((x - e0) / max(e1 - e0, 1e-6), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _bgr(col):
    return np.array([col[2], col[1], col[0]], np.float32)


def run_filter(t, arr, over, scale, frame, fps, seed):
    """Вызвать готовый фильтр (S_DigitalDamage, S_LensFlare…) со значениями по умолчанию + over."""
    h, w = arr.shape[:2]
    v = {}
    for prm in EFFECTS[t].params:
        val = list(prm.default)
        if prm.kind == "point" and all(abs(x) <= 1 for x in val):
            val = [val[0] * w / scale, val[1] * h / scale]   # доли кадра → пиксели композиции
        v[prm.key] = int(round(val[0])) if prm.kind in ("checkbox", "choice") else (val[0] if len(val) == 1 else val)
    v.update(over)
    return FILTERS[t](arr, v, scale=scale, frame=frame, fps=fps, seed=seed)


def _zoom(arr, z, cx, cy):
    """Масштаб кадра z вокруг (cx, cy); за краем — прозрачно."""
    h, w = arr.shape[:2]
    gx, gy = _grid(h, w)
    sx, sy = cx + (gx - cx) / z, cy + (gy - cy) / z
    out = _remap(arr, sx, sy)
    out *= ((sx >= -0.5) & (sx <= w - 0.5) & (sy >= -0.5) & (sy <= h - 0.5))[..., None]
    return out


def _zoom_blur(arr, z, spread, cx, cy, n=6):
    """Радиальное размытие «в движении»: среднее нескольких масштабов z…z·(1+spread)."""
    if spread < 0.004:
        return _zoom(arr, z, cx, cy)
    acc = np.zeros_like(arr)
    for i in range(n):
        acc += _zoom(arr, z * (1 + spread * i / (n - 1)), cx, cy)
    return acc / n


# ============================================================================ переходы
def _t_dissolve(a, b, p, v, **_):
    return _mix(a, b, p)


def _t_dip(a, b, p, v, **_):
    col = np.empty_like(a)
    col[..., :3] = _bgr(v["color"])
    col[..., 3] = 255.0
    e = (1 - min(0.9, max(0.0, v["hold"]))) / 2      # hold — доля окна, когда кадр полностью цвета
    if p < 0.5:
        return _mix(a, col, min(1.0, p / max(e, 1e-6)))
    return _mix(col, b, max(0.0, (p - 1 + e) / max(e, 1e-6)))


def _t_flash(a, b, p, v, **_):
    out = _mix(a, b, float(_smooth(0.5 - v["width"] / 4, 0.5 + v["width"] / 4, p)))
    k = _bell(p, v["width"]) * v["bright"]
    out[..., 3] = np.maximum(out[..., 3], 255.0 * min(1.0, k))
    out[..., :3] += _bgr(v["color"]) * k
    return out


def _t_glow(a, b, p, v, scale=1.0, **_):
    from .sapphire import _GLOW_UNIT, _threshold_src, glow_image
    out = _mix(a, b, p)
    k = _bell(p)
    if k <= 0.01:
        return out
    w = out.shape[1]
    src = _threshold_src(out, v["thresh"], [0.0, 0.0, 0.0], 0)
    g = glow_image(src, v["width"] * _GLOW_UNIT * w * (0.3 + 0.7 * k), 1.0, 1.0, (1.0, 1.0, 1.0))
    out[..., :3] += g[..., :3] * (_bgr(v["color"]) / 255.0) * v["bright"] * k
    out[..., 3] = np.maximum(out[..., 3], np.clip(g[..., :3].max(axis=2) * v["bright"] * k, 0, 255))
    return out


def _t_blur(a, b, p, v, scale=1.0, **_):
    r = v["amount"] * scale * _bell(p)
    if r >= 0.5:
        a, b = blur(a, r), blur(b, r)
    return _mix(a, b, float(_smooth(0.25, 0.75, p)))


def _t_zoom(a, b, p, v, scale=1.0, **_):
    cx, cy = v["center"][0] * scale, v["center"][1] * scale
    amt, mb = v["amount"], v["blur"] * _bell(p) * 0.25
    za = _zoom_blur(a, 1 + amt * p, mb, cx, cy)
    zb = _zoom_blur(b, 1 + amt * (1 - p), mb, cx, cy)   # B «влетает» из увеличенного к 100%
    return _mix(za, zb, float(_smooth(0.3, 0.7, p)))


_DIRS = [("Влево", "Left"), ("Вправо", "Right"), ("Вверх", "Up"), ("Вниз", "Down")]


def _t_push(a, b, p, v, **_):
    h, w = a.shape[:2]
    ux, uy = [(-1, 0), (1, 0), (0, -1), (0, 1)][min(3, max(0, v["dir"]))]
    size = w if ux else h
    spread = v["blur"] * _bell(p) * size * 0.06    # размытие в движении — сильнее в середине
    n = 6 if spread >= 1 else 1
    acc = np.zeros_like(a)
    for i in range(n):
        o = int(round(p * size + (spread * (i / (n - 1) - 0.5) if n > 1 else 0.0)))
        acc += shift(a, ux * o, uy * o) + shift(b, ux * (o - size), uy * (o - size))
    return acc / n


def _t_wipe(a, b, p, v, scale=1.0, **_):
    h, w = a.shape[:2]
    gx, gy = _grid(h, w)
    ang = math.radians(v["angle"])
    ca, sa = math.cos(ang), math.sin(ang)
    d = gx * ca + gy * sa                                # проекция на направление шторки
    corners = [x * ca + y * sa for x in (0, w) for y in (0, h)]
    f = max(0.0, v["feather"] * scale)
    edge = min(corners) - f + (max(corners) - min(corners) + 2 * f) * p
    m = (d < edge).astype(np.float32) if f == 0 else 1.0 - _smooth(edge - f, edge, d)   # 1 — уже B
    return _mix(a, b, m[..., None])


def _t_iris(a, b, p, v, scale=1.0, **_):
    h, w = a.shape[:2]
    gx, gy = _grid(h, w)
    cx, cy = v["center"][0] * scale, v["center"][1] * scale
    dist = np.hypot(gx - cx, gy - cy)
    far = max(math.hypot(x - cx, y - cy) for x in (0, w) for y in (0, h))
    f = max(0.5, v["feather"] * scale)
    r = (far + f) * p
    return _mix(a, b, (1.0 - _smooth(r - f, r, dist))[..., None])


def _t_glitch(a, b, p, v, scale=1.0, frame=0, fps=30.0, seed=1):
    rng = np.random.default_rng(seed * 7919 + int(frame))
    use_b = rng.random() < float(_smooth(0.3, 0.7, p))   # у стыка картинка «скачет» между A и B
    src = (b if use_b else a).copy()
    k = _bell(p) * v["amount"]
    if k <= 0.01:
        return src
    return run_filter("digitaldamage", src, {"intensity": k, "size": v["size"], "tint": 3.0},
                      scale, frame, fps, seed)


def _t_warp(a, b, p, v, scale=1.0, frame=0, fps=30.0, seed=1):
    """Растворение с волновым искажением: обе картинки «плывут» сильнее всего в середине."""
    k = _bell(p) * v["amount"]
    if k > 0.002:
        over = {"amp": k * 0.1, "freq": v["freq"], "phase": p * v["speed"]}
        a = run_filter("warpwaves", a, over, scale, frame, fps, seed)
        b = run_filter("warpwaves", b, dict(over, angle=45.0), scale, frame, fps, seed)
    return _mix(a, b, float(_smooth(0.3, 0.7, p)))


def _t_flare(a, b, p, v, scale=1.0, frame=0, fps=30.0, seed=1):
    out = _mix(a, b, float(_smooth(0.3, 0.7, p)))
    k = _bell(p)
    if k <= 0.01:
        return out
    w = out.shape[1]
    # блик проходит по кадру: Hotspot сдвигается по горизонтали на Travel (доля ширины) за время перехода
    hs = [v["hotspot"][0] + (p - 0.5) * v["travel"] * w / scale, v["hotspot"][1]]
    return run_filter("lensflare", out, {"bright": v["bright"] * k, "hbright": v["bright"] * k, "hotspot": hs,
                                          "color": v["color"]}, scale, frame, fps, seed)


_NEW = [
    EffectDef("t_dissolve", "Растворение", "Cross Dissolve", _timing(ease=0.0), category="transition",
              transition=True),
    EffectDef("t_dip", "Через цвет", "Dip to Color", [
        Param("color", "Цвет", "Color", [0.0, 0.0, 0.0], kind="color"),
        Param("hold", "Задержка на цвете", "Hold Color", [0.1], 0.01, "", 0, 0.9),
        *_timing()], category="transition", transition=True),
    EffectDef("t_flash", "Вспышка", "Flash Dissolve", [
        Param("bright", "Яркость вспышки", "Flash Brightness", [1.5], 0.01, "", 0, 10),
        Param("color", "Цвет вспышки", "Flash Color", [255.0, 255.0, 255.0], kind="color"),
        Param("width", "Ширина вспышки", "Flash Width", [0.6], 0.01, "", 0.05, 1),
        *_timing(12.0)], category="transition", transition=True),
    EffectDef("t_glow", "Растворение со свечением", "Glow Dissolve", [
        Param("bright", "Яркость свечения", "Glow Brightness", [2.0], 0.01, "", 0, 20),
        Param("width", "Ширина свечения", "Glow Width", [0.6], 0.01, "", 0, 10),
        Param("thresh", "Порог", "Threshold", [0.5], 0.005, "", 0, 0.999),
        Param("color", "Цвет свечения", "Glow Color", [255.0, 240.0, 220.0], kind="color"),
        *_timing(20.0)], category="transition", transition=True),
    EffectDef("t_blur", "Растворение с размытием", "Blur Dissolve", [
        Param("amount", "Размытие", "Blur Amount", [60.0], 0.5, " px", 0, 1000),
        *_timing()], category="transition", transition=True),
    EffectDef("t_zoom", "Зум", "Zoom Dissolve", [
        Param("amount", "Сила зума", "Zoom Amount", [1.0], 0.01, "", 0, 10),
        Param("blur", "Размытие в движении", "Motion Blur", [1.0], 0.01, "", 0, 4),
        Param("center", "Центр", "Center", [0.5, 0.5], 1.0, kind="point"),
        *_timing(12.0)], category="transition", transition=True),
    EffectDef("t_push", "Сдвиг", "Push", [
        Param("dir", "Направление", "Direction", [0.0], kind="choice", choices=_DIRS),
        Param("blur", "Размытие в движении", "Motion Blur", [1.0], 0.01, "", 0, 4),
        *_timing(12.0)], category="transition", transition=True),
    EffectDef("t_wipe", "Линейная шторка", "Linear Wipe", [
        Param("angle", "Угол", "Wipe Angle", [0.0], 0.5, "°", kind="angle"),
        Param("feather", "Растушёвка", "Feather", [40.0], 0.5, " px", 0, 4000),
        *_timing(ease=0.0)], category="transition", transition=True),
    EffectDef("t_iris", "Круг", "Iris Wipe", [
        Param("center", "Центр", "Center", [0.5, 0.5], 1.0, kind="point"),
        Param("feather", "Растушёвка", "Feather", [60.0], 0.5, " px", 0, 4000),
        *_timing()], category="transition", transition=True),
    EffectDef("t_glitch", "Глитч", "Glitch", [
        Param("amount", "Сила повреждений", "Damage Amount", [1.5], 0.01, "", 0, 8),
        Param("size", "Размер повреждений", "Damage Size", [1.0], 0.01, "", 0.05, 20),
        *_timing(10.0, 0.0)], category="transition", transition=True),
    EffectDef("t_warp", "Растворение с волнами", "Warp Dissolve", [
        Param("amount", "Сила искажения", "Warp Amount", [1.0], 0.01, "", 0, 10),
        Param("freq", "Частота волн", "Frequency", [6.0], 0.05, "", 0, 100),
        Param("speed", "Скорость волн", "Phase Speed", [2.0], 0.01, "", -50, 50),
        *_timing(16.0)], category="transition", transition=True),
    EffectDef("t_flare", "Блик объектива", "Lens Flare Dissolve", [
        Param("bright", "Яркость блика", "Flare Brightness", [1.5], 0.01, "", 0, 20),
        Param("hotspot", "Горячая точка", "Hotspot", [0.35, 0.4], 1.0, kind="point"),
        Param("travel", "Пробег блика", "Flare Travel", [0.4], 0.01, "", -2, 2),
        Param("color", "Цвет", "Color", [255.0, 230.0, 200.0], kind="color"),
        *_timing(20.0)], category="transition", transition=True),
]
for _d in _NEW:
    EFFECTS[_d.type] = _d

TRANSITIONS = {"t_dissolve": _t_dissolve, "t_dip": _t_dip, "t_flash": _t_flash, "t_glow": _t_glow,
               "t_blur": _t_blur, "t_zoom": _t_zoom, "t_push": _t_push, "t_wipe": _t_wipe, "t_iris": _t_iris,
               "t_glitch": _t_glitch, "t_flare": _t_flare, "t_warp": _t_warp}
