# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Эффекты времени (Posterize Time, Echo) и стилизации (Deep Glow-подобное свечение, Motion Tile).

Эффекты времени не пиксельные: render._composited рисует слой в другие моменты (time_sources) и смешивает.
Как в AE, время меняется у содержимого слоя и его эффектов, трансформация остаётся на текущем кадре.
Модуль импортируется в конце effects.py и дописывает EFFECTS/FILTERS. Код свой.
"""
from __future__ import annotations

import math

import numpy as np

from .effects import CATEGORIES, COMPOSITING, EFFECTS, FILTERS, EffectDef, Param, _grid, _lum, _remap, active_effects

CATEGORIES["time"] = ("Время", "Time")


# ============================================================================ время
def time_effects(layer):
    return [fx for fx in active_effects(layer) if EFFECTS[fx["type"]].time]


def _posterize(layer, frame, fps, fx):
    from .effects import values
    rate = max(0.01, values(layer, fx, layer.local(frame))["rate"])
    step = fps / rate
    if step <= 1.0:
        return frame
    lf = layer.local(frame)
    return layer.start + math.floor(lf / step + 1e-6) * step   # время слоя, а не композиции, как в AE


def time_sources(layer, frame, fps):
    """([(кадр композиции, вес)], оператор Echo или None); None — у слоя нет эффектов времени."""
    from .effects import values
    fxs = time_effects(layer)
    if not fxs:
        return None
    srcs, op = [(float(frame), 1.0)], None
    for fx in fxs:
        if fx["type"] == "posterizetime":
            srcs = [(_posterize(layer, f, fps, fx), w) for f, w in srcs]
        elif fx["type"] == "echo" and op is None:
            v = values(layer, fx, layer.local(frame))
            n = max(0, int(round(v["num"])))
            base = srcs[0][0]
            srcs = [(base + k * v["etime"] * fps, v["start"] * v["decay"] ** k) for k in range(n + 1)]
            op = int(v["op"])
    return srcs, op


ECHO_OPS = [("Сложение", "Add"), ("Максимум", "Maximum"), ("Минимум", "Minimum"), ("Экран", "Screen"),
            ("Наложение сзади", "Composite In Back"), ("Наложение спереди", "Composite In Front"),
            ("Смешивание", "Blend")]


def echo_combine(frames, op):
    """frames — [(arr, вес)], первый — текущий кадр. Операторы Echo из AE."""
    if op is None:
        return frames[0][0] * frames[0][1]
    if op == 6:                                   # Blend — среднее взвешенное
        tot = sum(w for _, w in frames) or 1.0
        out = sum(a * w for a, w in frames) / tot
    else:
        out = None
        for a, w in frames:
            a = a * w
            if out is None:
                out = a
            elif op == 0:
                out = out + a
            elif op == 1:
                out = np.maximum(out, a)
            elif op == 2:
                out = np.minimum(out, a)
            elif op == 3:
                out = out + a - out * a / 255.0
            elif op == 4:                         # эхо позади текущего кадра
                out = out + a * (1 - out[..., 3:4] / 255.0)
            else:                                 # 5 — эхо поверх
                out = a + out * (1 - a[..., 3:4] / 255.0)
    out = np.clip(out, 0, 255)
    out[..., :3] = np.minimum(out[..., :3], out[..., 3:4])
    return out


# ============================================================================ Deep Glow-подобное свечение
def _f_deepglow(arr, v, scale=1.0, **_):
    """Свечение с длинным «физическим» хвостом: сумма гауссов (glow_image из sapphire.py), Exposure в стопах."""
    from .sapphire import glow_image
    a = arr[..., 3:4]
    rgb = np.where(a > 0, arr[..., :3] / np.maximum(a, 1e-3) * 255.0, 0.0)
    if v["thon"]:
        lum = _lum(rgb) / 255.0
        lo, hi = v["thresh"] - v["smooth"] / 2, v["thresh"] + v["smooth"] / 2
        t = np.clip((lum - lo) / max(hi - lo, 1e-4), 0, 1)
        src = arr * (t * t * (3 - 2 * t))[..., None]
    else:
        src = arr
    ca = v["ca"] / 100.0
    rels = (1.0 + ca, 1.0, max(0.05, 1.0 - ca))           # R шире, B уже — цветная кайма
    ang = math.radians(v["angle"])
    ar = max(0.05, v["aspect"])
    wx = abs(math.cos(ang)) * ar + abs(math.sin(ang))
    wy = abs(math.sin(ang)) * ar + abs(math.cos(ang))
    g = glow_image(src, v["radius"] * scale, wx, wy, rels) * (2.0 ** v["exposure"])
    if v["tinton"]:
        col = np.array([v["tint"][2], v["tint"][1], v["tint"][0]], np.float32) / 255.0
        gl = _lum(g[..., :3])[..., None]
        g[..., :3] = g[..., :3] + (gl * col - g[..., :3]) * (v["tintmix"] / 100.0)
    if v["comp"] == 1:                                     # только свечение
        out = g.copy()
        out[..., 3] = np.maximum(out[..., 3], g[..., :3].max(axis=2))
    else:
        base = arr * (v["srcop"] / 100.0)
        if v["blend"] == 1:
            gg = np.clip(g[..., :3], 0, 255)
            rgbo = base[..., :3] + gg - base[..., :3] * gg / 255.0
        else:
            rgbo = base[..., :3] + g[..., :3]
        out = arr.copy()
        out[..., :3] = rgbo
        out[..., 3] = np.maximum(base[..., 3], g[..., :3].max(axis=2))
    out = np.clip(out, 0, 255)
    out[..., :3] = np.minimum(out[..., :3], out[..., 3:4])
    return out


# ============================================================================ Motion Tile (как в AE: Stylize)
def _f_motiontile(arr, v, scale=1.0, **_):
    """Кадр размножается плиткой вокруг Tile Center. Tile Width/Height — размер плитки в % кадра,
    Output Width/Height — область вывода, Mirror Edges — соседние плитки зеркальны,
    Phase — сдвиг соседних столбцов (с Horizontal Phase Shift — строк)."""
    h, w = arr.shape[:2]
    gx, gy = _grid(h, w)
    # как в AE: плитка — это сам слой, а не весь кадр композиции. Эффекты считаются на холсте композиции,
    # поэтому границы слоя берём по непрозрачным пикселям.
    # shortcut: у повёрнутого слоя это описанный прямоугольник с прозрачными углами; точнее — передавать local_rect слоя.
    alpha = arr[..., 3]
    amax = float(alpha.max())
    if amax <= 0:
        return arr
    ys, xs = np.nonzero(alpha >= amax * 0.99)   # без сглаженного края — иначе тёмные швы между плитками
    x0, y0 = float(xs.min()), float(ys.min())
    bw, bh = float(xs.max()) + 1 - x0, float(ys.max()) + 1 - y0
    bcx, bcy = x0 + bw / 2, y0 + bh / 2
    # Tile Center по умолчанию — центр композиции = центр слоя; сдвиг точки сдвигает сетку плиток
    cx, cy = bcx + v["center"][0] * scale - w / 2, bcy + v["center"][1] * scale - h / 2
    tw, th = max(1.0, bw * v["tw"] / 100.0), max(1.0, bh * v["th"] / 100.0)
    u = (gx - cx) / tw + 0.5
    t = (gy - cy) / th + 0.5
    ph = v["phase"] / 360.0
    if ph:
        if v["hshift"]:
            u = u + ph * (np.floor(t) % 2)
        else:
            t = t + ph * (np.floor(u) % 2)
    iu, it = np.floor(u), np.floor(t)
    fu, ft = u - iu, t - it
    if v["mirror"]:
        fu = np.where(iu % 2 != 0, 1.0 - fu, fu)
        ft = np.where(it % 2 != 0, 1.0 - ft, ft)
    out = _remap(arr, np.clip(x0 + fu * bw, x0, x0 + bw - 1).astype(np.float32),
                 np.clip(y0 + ft * bh, y0, y0 + bh - 1).astype(np.float32))
    # Output Width/Height: 100% — размер слоя, больше — плитки заполняют место вокруг слоя
    ow, oh = bw * v["ow"] / 100.0, bh * v["oh"] / 100.0
    inside = (np.abs(gx + 0.5 - bcx) <= ow / 2) & (np.abs(gy + 0.5 - bcy) <= oh / 2)
    return (out * inside[..., None]).astype(np.float32)


_GLOW_T = ("Порог", "Threshold")
_GLOW_CA = ("Хроматическая аберрация", "Chromatic Aberration")
_GLOW_TINT = ("Тонировка", "Tint")
_GLOW_STYLE = ("Стиль", "Style")
_NEW = [
    EffectDef("posterizetime", "Постеризация времени", "Posterize Time", [
        Param("rate", "Частота кадров", "Frame Rate", [12.0], 0.1, "", 0.1, 99)], category="time", time=True),
    EffectDef("echo", "Эхо", "Echo", [
        Param("etime", "Время эха (сек)", "Echo Time (seconds)", [-0.033], 0.001, "", -10, 10),
        Param("num", "Количество эхо", "Number Of Echoes", [1.0], 1.0, "", 0, 50),
        Param("start", "Начальная интенсивность", "Starting Intensity", [1.0], 0.01, "", 0, 1),
        Param("decay", "Затухание", "Decay", [1.0], 0.01, "", 0, 1),
        Param("op", "Оператор эха", "Echo Operator", [0.0], kind="choice", choices=ECHO_OPS)],
        category="time", time=True),
    # Параметры Deep Glow (Plugin Everything) — по памяти о его панели; значения по умолчанию подобраны,
    # официальная документация при разработке была недоступна (403)
    EffectDef("deepglow", "Глубокое свечение (Deep Glow)", "Deep Glow", [
        Param("radius", "Радиус", "Radius", [100.0], 0.5, " px", 0, 5000),
        Param("exposure", "Экспозиция", "Exposure", [0.0], 0.01, "", -10, 10),
        Param("thon", "Включить порог", "Threshold Enable", [1.0], kind="checkbox", group=_GLOW_T),
        Param("thresh", "Порог", "Threshold", [0.6], 0.005, "", 0, 1, group=_GLOW_T),
        Param("smooth", "Мягкость порога", "Threshold Smooth", [0.2], 0.005, "", 0, 1, group=_GLOW_T),
        Param("ca", "Сила", "Amount", [0.0], 0.5, "%", 0, 100, group=_GLOW_CA),
        Param("tinton", "Включить тонировку", "Tint Enable", [0.0], kind="checkbox", group=_GLOW_TINT),
        Param("tint", "Цвет", "Color", [255.0, 140.0, 40.0], kind="color", group=_GLOW_TINT),
        Param("tintmix", "Смешивание", "Mix", [100.0], 0.5, "%", 0, 100, group=_GLOW_TINT),
        Param("aspect", "Соотношение сторон", "Aspect Ratio", [1.0], 0.01, "", 0.05, 20, group=_GLOW_STYLE),
        Param("angle", "Угол", "Angle", [0.0], 0.5, "°", kind="angle", group=_GLOW_STYLE),
        Param("blend", "Режим наложения", "Blend Mode", [0.0], kind="choice",
              choices=[("Сложение", "Add"), ("Экран", "Screen")], group=_GLOW_STYLE),
        Param("comp", "Вывод", "Composite", [0.0], kind="choice",
              choices=[("Поверх исходника", "On Top"), ("Только свечение", "Glow Only")], group=_GLOW_STYLE),
        Param("srcop", "Непрозрачность исходника", "Source Opacity", [100.0], 0.5, "%", 0, 100, group=_GLOW_STYLE),
    ], category="stylize"),
    EffectDef("motiontile", "Мозаичная плитка (Motion Tile)", "Motion Tile", [
        Param("center", "Центр плитки", "Tile Center", [0.5, 0.5], 1.0, kind="point"),
        Param("tw", "Ширина плитки", "Tile Width", [100.0], 0.5, "%", 1, 1000),
        Param("th", "Высота плитки", "Tile Height", [100.0], 0.5, "%", 1, 1000),
        Param("ow", "Ширина вывода", "Output Width", [100.0], 0.5, "%", 0, 1000),
        Param("oh", "Высота вывода", "Output Height", [100.0], 0.5, "%", 0, 1000),
        Param("mirror", "Зеркальные края", "Mirror Edges", [0.0], kind="checkbox"),
        Param("phase", "Фаза", "Phase", [0.0], 0.5, "°", kind="angle"),
        Param("hshift", "Горизонтальный сдвиг фазы", "Horizontal Phase Shift", [0.0], kind="checkbox"),
    ], category="stylize"),
]
for _d in _NEW:
    if not _d.time:
        _d.params.append(Param("mix", "Непрозрачность эффекта", "Effect Opacity", [100.0], 0.5, "%", 0, 100,
                               group=COMPOSITING))
    EFFECTS[_d.type] = _d
FILTERS.update({"deepglow": _f_deepglow, "motiontile": _f_motiontile})
