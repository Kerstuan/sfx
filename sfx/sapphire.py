# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Эффекты в духе Sapphire (вторая партия, по видео «I Tested EVERY Sapphire Effect… PART 2»).

Код свой: повторяем поведение и набор параметров по тому, что видно в Effect Controls на видео,
а не реализацию Boris FX. Модуль импортируется в конце effects.py и дописывает EFFECTS/FILTERS.
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QTransform

from .effects import COMPOSITING, EFFECTS, FILTERS, EffectDef, Param, _grid, _remap, to_array, to_image

# ============================================================================ общие помощники
_SPR = 256          # сторона текстуры штампа


def _smooth(e0, e1, x):
    t = np.clip((x - e0) / max(e1 - e0, 1e-6), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def _value_noise(n: int, cells: int, rng) -> np.ndarray:
    """Шум значений n×n из сетки cells×cells, билинейно и со сглаживанием — 0..1."""
    g = rng.random((cells + 1, cells + 1)).astype(np.float32)
    t = np.linspace(0, cells, n, endpoint=False, dtype=np.float32)
    i = t.astype(np.int32)
    f = t - i
    f = f * f * (3 - 2 * f)
    a = g[i][:, i] * (1 - f)[None, :] + g[i][:, i + 1] * f[None, :]
    b = g[i + 1][:, i] * (1 - f)[None, :] + g[i + 1][:, i + 1] * f[None, :]
    return a * (1 - f)[:, None] + b * f[:, None]


def _fbm2(n: int, cells: int, octaves: int, rng) -> np.ndarray:
    tot, amp, norm = np.zeros((n, n), np.float32), 1.0, 0.0
    for o in range(octaves):
        tot += _value_noise(n, min(n // 2, cells * 2 ** o), rng) * amp
        norm += amp
        amp *= 0.5
    return tot / norm


def _radial(n: int):
    c = (np.arange(n, dtype=np.float32) + 0.5) / n * 2 - 1
    return np.sqrt(c[None, :] ** 2 + c[:, None] ** 2)


def _qimage_alpha(img: QImage) -> np.ndarray:
    return to_array(img)[..., 3] / 255.0


# ============================================================================ S_Grunge
GRUNGE_STAMPS = [("Нет", "None"), ("Облака", "Clouds"), ("Пол гаража", "Garage Floor"),
                 ("Брызги краски", "Paint Spray"), ("Песок", "Grit"), ("Царапины", "Scratches"),
                 ("Клякса", "Splatter"), ("Дым", "Smoke")]
_VARIANTS = 3
_SPRITES: dict = {}


def _stamp_shape(kind: int, variant: int, soft: float) -> tuple[np.ndarray, np.ndarray]:
    """Форма штампа: (яркость 0..1, альфа 0..1) — процедурная текстура _SPR×_SPR."""
    key = (kind, variant, round(soft, 2))
    got = _SPRITES.get(key)
    if got is not None:
        return got
    n = _SPR
    rng = np.random.default_rng(kind * 1009 + variant * 7919 + 17)
    r = _radial(n)
    warp = _fbm2(n, 3, 3, rng)
    re = r + (warp - 0.5) * 0.45                   # рваный край вместо идеального круга
    fall = np.clip((1.0 - re) / max(soft, 0.02), 0.0, 1.0)
    fall = fall * fall * (3 - 2 * fall)
    if kind == 1:      # облака — мягкий шум
        f = _fbm2(n, 4, 5, rng)
        lum, alpha = f, _smooth(0.3, 0.75, f) * fall
    elif kind == 7:    # дым — «гребни» шума, тонкие волокна
        f = _fbm2(n, 3, 5, rng)
        rid = 1.0 - np.abs(f - 0.5) * 2
        lum, alpha = f, _smooth(0.55, 0.95, rid) * fall * 0.9
    elif kind == 2:    # пол гаража — пятна + мелкие крапинки
        f = _fbm2(n, 5, 6, rng)
        spk = _value_noise(n, 96, rng)
        stain = _smooth(0.52, 0.6, f)
        lum = _fbm2(n, 16, 3, rng)
        film = 0.25 + 0.35 * _fbm2(n, 10, 4, rng)          # сплошной грязный налёт под пятнами
        alpha = np.clip(film + stain * 0.7 + _smooth(0.8, 0.88, spk) * 0.8, 0, 1) * fall
        lum = lum * (1.0 - stain * 0.6)                     # пятна темнее налёта
    elif kind == 4:    # песок — мелкое зерно
        g = _value_noise(n, 128, rng) * 0.6 + _fbm2(n, 8, 3, rng) * 0.4
        lum, alpha = g, _smooth(0.6, 0.7, g) * fall
    elif kind in (3, 5, 6):   # рисуем QPainter-ом: брызги, царапины, клякса
        img = QImage(n, n, QImage.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255))
        if kind == 3:      # брызги: облако капель, гуще к центру
            for _ in range(700):
                rr = abs(rng.normal(0, 0.33)) * n / 2
                a = rng.random() * 2 * math.pi
                d = max(0.6, rng.exponential(1.4))
                p.setOpacity(float(rng.uniform(0.4, 1.0)))
                p.drawEllipse(QPointF(n / 2 + math.cos(a) * rr, n / 2 + math.sin(a) * rr), d, d)
        elif kind == 5:    # царапины: тонкие слегка изогнутые штрихи
            for _ in range(14):
                x0, y0 = rng.uniform(0.1, 0.9, 2) * n
                a = rng.random() * math.pi
                ln = rng.uniform(0.2, 0.7) * n
                pen = QPen(QColor(255, 255, 255), float(rng.uniform(0.6, 2.0)))
                p.setPen(pen)
                p.setOpacity(float(rng.uniform(0.3, 1.0)))
                pts = [QPointF(x0 + math.cos(a + k * 0.04) * ln * k / 6, y0 + math.sin(a + k * 0.04) * ln * k / 6)
                       for k in range(7)]
                for q0, q1 in zip(pts, pts[1:]):
                    p.drawLine(q0, q1)
        else:              # клякса: центральное пятно + капли вокруг
            for _ in range(60):
                a = rng.random() * 2 * math.pi
                rr = rng.uniform(0.25, 0.48) * n
                d = rng.uniform(1.0, 6.0)
                p.drawEllipse(QPointF(n / 2 + math.cos(a) * rr, n / 2 + math.sin(a) * rr), d, d)
        p.end()
        a = _qimage_alpha(img)
        if kind == 6:
            a = np.maximum(a, _smooth(0.45, 0.5, 1.0 - re * 0.9 + (_fbm2(n, 6, 3, rng) - 0.5) * 0.3))
        lum = _fbm2(n, 8, 3, rng)
        alpha = a * np.clip((1.0 - r) / 0.15, 0, 1) if kind != 3 else a
    else:
        lum = alpha = np.zeros((n, n), np.float32)
    got = (lum.astype(np.float32), np.clip(alpha, 0, 1).astype(np.float32))
    _SPRITES[key] = got
    while len(_SPRITES) > 96:
        _SPRITES.pop(next(iter(_SPRITES)))
    return got


def _colored_sprite(kind, variant, soft, c1, c2, bright) -> QImage:
    lum, alpha = _stamp_shape(kind, variant, soft)
    t = np.clip(lum * bright, 0.0, 1.5)[..., None]
    c1 = np.array([c1[2], c1[1], c1[0]], np.float32)   # B G R
    c2 = np.array([c2[2], c2[1], c2[0]], np.float32)
    rgb = np.clip(c1 + (c2 - c1) * t, 0, 255) * alpha[..., None]
    arr = np.concatenate([rgb, alpha[..., None] * 255.0], axis=2).astype(np.float32)
    return to_image(arr)


def _h01(i: int, salt: float) -> float:
    x = math.sin(i * 12.9898 + salt * 78.233) * 43758.5453
    return x - math.floor(x)


def _grunge_frame_mask(h, w, v, scale):
    """Эллипс «рамки»: внутри грязь ослаблена на Frame Amount (или снаружи, если инверсия)."""
    amt = v["frame_amt"]
    if amt <= 1e-4:
        return None
    cx, cy = v["frame_center"][0] * scale, v["frame_center"][1] * scale
    rx = max(1.0, v["frame_radius"] * scale)
    ry = max(1.0, rx * max(0.01, v["frame_relh"]))
    ys = (np.arange(h, dtype=np.float32) - cy) / ry
    xs = (np.arange(w, dtype=np.float32) - cx) / rx
    e = np.sqrt(xs[None, :] ** 2 + ys[:, None] ** 2)
    s = max(0.01, v["frame_soft"])
    inside = 1.0 - _smooth(1.0 - s, 1.0 + s * 0.5, e)
    if v["frame_invert"]:
        inside = 1.0 - inside
    return (1.0 - amt * inside)[..., None].astype(np.float32)


def _f_grunge(arr, v, scale=1.0, frame=0, fps=30.0, seed=1, **_):
    h, w = arr.shape[:2]
    comp_w, comp_h = w / max(scale, 1e-6), h / max(scale, 1e-6)
    layer = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    layer.fill(0)
    p = QPainter(layer)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    ox = (v["center"][0] - comp_w / 2) * scale
    oy = (v["center"][1] - comp_h / 2) * scale
    blink = max(0.0, v["blink"])
    bslot = int(frame / max(1e-3, fps / max(0.01, v["blink_speed"]))) if blink > 0 else 0
    soft = round(max(0.02, v["fade"]) * 20) / 20
    drew = False
    for si in (1, 2, 3):
        kind = v[f"s{si}"]
        if kind <= 0:
            continue
        count = int(round(v["density"] * v[f"s{si}_dens"] / (3.0 if si > 1 else 1.0)))
        count = max(0, min(4000, count))
        op0 = v["opacity"] * v[f"s{si}_op"]
        if count == 0 or op0 <= 0:
            continue
        base = 0.08 * max(comp_w, comp_h) * v["size"] * v[f"s{si}_size"] * scale
        vb = v[f"s{si}_vbright"]
        levels = [1.0] if vb <= 1e-3 else [1.0 - vb * 0.5, 1.0 - vb * 0.25, 1.0, 1.0 + vb * 0.25, 1.0 + vb * 0.5]
        br = v["bright"] * v[f"s{si}_bright"]
        sprites = [[_colored_sprite(kind, var, soft, v[f"s{si}_c1"], v[f"s{si}_c2"], br * lv) for lv in levels]
                   for var in range(_VARIANTS)]
        salt = seed * 3.1 + si * 101.0
        for i in range(count):
            u = [_h01(i, salt + k * 17.0) for k in range(7)]
            d = base * (1.0 - v[f"s{si}_vsize"] * u[2])
            if d < 1.0:
                continue
            op = op0 * (1.0 - v[f"s{si}_vop"] * u[3])
            if blink > 0:
                op *= 1.0 - blink * _h01(i * 31 + bslot, salt + 5.0)
            if op <= 0.004:
                continue
            x = -d / 2 + u[0] * (w + d) + ox
            y = -d / 2 + u[1] * (h + d) + oy
            spr = sprites[int(u[4] * _VARIANTS) % _VARIANTS][int(u[5] * len(levels)) % len(levels)]
            t = QTransform()
            t.translate(x, y)
            t.rotate(u[6] * 360.0)
            t.scale(d / _SPR, d / _SPR)
            t.translate(-_SPR / 2, -_SPR / 2)
            p.setTransform(t)
            p.setOpacity(min(1.0, op))
            p.drawImage(QPointF(0, 0), spr)
            drew = True
    p.end()
    if not drew:
        return arr
    g = to_array(layer)
    m = _grunge_frame_mask(h, w, v, scale)
    if m is not None:
        g *= m
    sa = arr[..., 3:4] / 255.0
    out = arr * (1.0 - g[..., 3:4] / 255.0)
    out[..., :3] += g[..., :3] * sa
    out[..., 3] = arr[..., 3]
    return out


def _stamp_params(i: int, kind: int, dens: float, c1, c2):
    grp = (f"Штамп {i} — детали", f"Stamp{i} Details")
    return [
        Param(f"s{i}", f"Штамп {i}", f"Stamp{i}", [float(kind)], kind="choice", choices=GRUNGE_STAMPS),
        Param(f"s{i}_dens", "Отн. плотность", f"Stamp{i} Rel Density", [dens], 0.01, "", 0, 10, group=grp),
        Param(f"s{i}_c1", "Цвет 1 (тени)", f"Stamp{i} Color1", list(c1), kind="color", group=grp),
        Param(f"s{i}_c2", "Цвет 2 (света)", f"Stamp{i} Color2", list(c2), kind="color", group=grp),
        Param(f"s{i}_bright", "Отн. яркость", f"Stamp{i} Rel Brightness", [1.0], 0.01, "", 0, 10, group=grp),
        Param(f"s{i}_op", "Отн. непрозрачность", f"Stamp{i} Rel Opacity", [1.0], 0.01, "", 0, 4, group=grp),
        Param(f"s{i}_size", "Отн. размер", f"Stamp{i} Rel Size", [1.0], 0.01, "", 0, 20, group=grp),
        Param(f"s{i}_vbright", "Разброс яркости", f"Vary Stamp{i} Brightness", [0.0], 0.01, "", 0, 1, group=grp),
        Param(f"s{i}_vop", "Разброс непрозрачности", f"Vary Stamp{i} Opacity", [0.75], 0.01, "", 0, 1, group=grp),
        Param(f"s{i}_vsize", "Разброс размера", f"Vary Stamp{i} Size", [0.5], 0.01, "", 0, 1, group=grp),
    ]


FRAME = ("Рамка — детали", "Frame Details")
BLINK = ("Мерцание — детали", "Blink Details")

NEW_EFFECTS = [
    EffectDef("grunge", "Грязь и пятна (Grunge)", "Grunge", [
        Param("density", "Плотность штампов", "Stamp Density", [500.0], 1.0, "", 0, 4000),
        Param("size", "Размер штампов", "Stamp Size", [1.0], 0.01, "", 0, 20),
        Param("opacity", "Непрозрачность штампов", "Stamp Opacity", [0.8], 0.01, "", 0, 1),
        Param("bright", "Яркость штампов", "Stamp Brightness", [1.0], 0.01, "", 0, 10),
        Param("center", "Центр штампов", "Stamp Center", [0.5, 0.5], 1.0, kind="point"),
        Param("fade", "Мягкость краёв", "Fade Softness", [0.3], 0.01, "", 0, 1),
        Param("blink", "Мерцание", "Blink Amount", [0.0], 0.01, "", 0, 1),
        Param("blink_speed", "Смен в секунду", "Blink Rate", [12.0], 0.1, "", 0.1, 120, group=BLINK),
        Param("frame_amt", "Сила рамки", "Frame Amount", [0.35], 0.01, "", 0, 1),
        Param("frame_soft", "Мягкость рамки", "Frame Softness", [0.5], 0.01, "", 0, 1, group=FRAME),
        Param("frame_center", "Центр рамки", "Frame Center", [0.5, 0.5], 1.0, kind="point", group=FRAME),
        Param("frame_radius", "Радиус рамки", "Frame Radius", [480.0], 1.0, " px", 0, 20000, group=FRAME),
        Param("frame_relh", "Отн. высота рамки", "Frame Rel Height", [0.75], 0.01, "", 0.01, 10, group=FRAME),
        Param("frame_invert", "Инвертировать рамку", "Invert Frame", [0.0], kind="checkbox", group=FRAME),
        *_stamp_params(1, 2, 1.0, (0, 0, 0), (80, 80, 80)),
        *_stamp_params(2, 3, 0.5, (32, 4, 4), (60, 22, 10)),
        *_stamp_params(3, 0, 0.5, (0, 0, 0), (255, 255, 255)),
    ], opts={"seed": 1}, category="sapphire"),
]


# ============================================================================ S_WarpWaves
WRAPS = [("Чёрный", "Black"), ("Повтор", "Repeat"), ("Отражение", "Reflect"), ("Край", "Clamp")]


def _wrap_coords(c, n, mode):
    """Координата выборки за краем кадра: 0 чёрный (маска), 1 повтор, 2 отражение (в _remap), 3 край."""
    if mode == 1:
        return np.mod(c, n), None
    if mode == 3:
        return np.clip(c, 0, n - 1), None
    if mode == 0:
        return c, (c >= -0.5) & (c <= n - 0.5)
    return c, None


def warp_sample(arr, sx, sy, wrap_x=2, wrap_y=2, filt=True):
    """Общая выборка для варпов Sapphire: режимы Wrap X/Y и Filter (билинейно / ближайший пиксель)."""
    h, w = arr.shape[:2]
    sx, mx = _wrap_coords(sx, w, wrap_x)
    sy, my = _wrap_coords(sy, h, wrap_y)
    out = _remap(arr, sx, sy, nearest=not filt)
    m = mx if my is None else (my if mx is None else mx & my)
    if m is not None:
        out *= m[..., None].astype(np.float32)
    return out


def _zoom_coords(xs, ys, w, h, zdist):
    """Z Dist: >1 — картинка ближе (крупнее), <1 — дальше. Масштаб от центра кадра."""
    z = max(0.01, zdist)
    if abs(z - 1.0) < 1e-6:
        return xs, ys
    cx, cy = (w - 1) / 2, (h - 1) / 2
    return cx + (xs - cx) / z, cy + (ys - cy) / z


def _f_warpwaves(arr, v, scale=1.0, frame=0, fps=30.0, **_):
    """S_WarpWaves: синусоидальное смещение. Волна бежит вдоль Angle (Frequency волн на ширину кадра),
    пиксели сдвигаются в направлении Angle + Displace Angle на Amplitude (доля ширины кадра)."""
    h, w = arr.shape[:2]
    xs, ys = _grid(h, w)
    a = math.radians(v["angle"])
    da = math.radians(v["angle"] + v["dangle"])
    ph = v["phase"] + v["speed"] * frame / max(fps, 1.0)
    cx, cy = (w - 1) / 2, (h - 1) / 2
    u = ((xs - cx) * math.cos(a) + (ys - cy) * math.sin(a)) / w        # позиция вдоль волны, в ширинах кадра
    disp = np.sin((u * v["freq"] - ph) * 2 * math.pi) * (v["amp"] * w * 0.5)
    sx = xs + disp * math.cos(da)
    sy = ys + disp * math.sin(da)
    sx, sy = _zoom_coords(sx, sy, w, h, v["zdist"])
    return warp_sample(arr, sx, sy, v["wrapx"], v["wrapy"], bool(v["filter"]))


def _warp_common(wrap_default=2):
    return [
        Param("zdist", "Приближение Z", "Z Dist", [1.0], 0.005, "", 0.01, 20),
        Param("wrapx", "Край по X", "Wrap X", [float(wrap_default)], kind="choice", choices=WRAPS),
        Param("wrapy", "Край по Y", "Wrap Y", [float(wrap_default)], kind="choice", choices=WRAPS),
        Param("filter", "Сглаживание (фильтр)", "Filter", [1.0], kind="checkbox"),
    ]


NEW_EFFECTS.append(EffectDef("warpwaves", "Волны (WarpWaves)", "Warp Waves", [
    Param("amp", "Амплитуда", "Amplitude", [0.1], 0.001, "", -10, 10),
    Param("freq", "Частота", "Frequency", [8.0], 0.01, "", 0, 500),
    Param("angle", "Угол волны", "Angle", [-45.0], 0.5, "°", kind="angle"),
    Param("dangle", "Угол смещения", "Displace Angle", [-90.0], 0.5, "°", kind="angle"),
    Param("phase", "Начальная фаза", "Phase Start", [0.0], 0.001, "", -1000, 1000),
    Param("speed", "Скорость фазы", "Phase Speed", [0.0], 0.001, "", -1000, 1000),
    *_warp_common()], category="sapphire"))


# ============================================================================ S_WarpVortex
def _f_warpvortex(arr, v, scale=1.0, frame=0, fps=30.0, **_):
    """S_WarpVortex: закрутка как в водовороте — поворот спадает обратно пропорционально радиусу
    (Vortex = оборотов на расстоянии 100 px от центра), внутри Inner Radius кадр вращается целиком."""
    h, w = arr.shape[:2]
    xs, ys = _grid(h, w)
    cx, cy = v["center"][0] * scale, v["center"][1] * scale
    dx, dy = xs - cx, ys - cy
    r = np.sqrt(dx * dx + dy * dy)
    vort = v["vstart"] + v["vspeed"] * frame / max(fps, 1.0)
    inner = max(1.0, v["inner"] * scale)
    th = (vort * 2 * math.pi * 100.0 * scale) / np.maximum(r, inner) + math.radians(v["aoff"])
    c, s_ = np.cos(th), np.sin(th)
    sx = cx + dx * c - dy * s_
    sy = cy + dx * s_ + dy * c
    sx, sy = _zoom_coords(sx, sy, w, h, v["zdist"])
    return warp_sample(arr, sx, sy, v["wrapx"], v["wrapy"], bool(v["filter"]))


NEW_EFFECTS.append(EffectDef("warpvortex", "Воронка (WarpVortex)", "Warp Vortex", [
    Param("vstart", "Закрутка", "Vortex Start", [0.0], 0.01, "", -1000, 1000),
    Param("vspeed", "Скорость закрутки", "Vortex Speed", [0.0], 0.01, "", -1000, 1000),
    Param("aoff", "Сдвиг угла", "Angle Offset", [0.0], 0.5, "°", kind="angle"),
    Param("center", "Центр", "Center XY", [0.5, 0.5], 1.0, kind="point"),
    Param("inner", "Внутренний радиус", "Inner Radius", [40.0], 0.5, " px", 0, 10000),
    *_warp_common()], category="sapphire"))


# ============================================================================ S_BokehLights
BOKEH_SHAPES = [("Круг", "Circle"), ("3 стороны", "3 sides"), ("4 стороны", "4 sides"), ("5 сторон", "5 sides"),
                ("6 сторон", "6 sides"), ("7 сторон", "7 sides"), ("8 сторон", "8 sides")]
_BOKEH_SPR: dict = {}
_BK = 128


def _bokeh_shape(sides, round_, bokeh, lnoise, nfreq, nfx, feather):
    """Форма огня (альфа-маска 0..1) _BK×_BK: многоугольник/круг, яркий ободок (Bokeh), шум линзы."""
    key = (sides, round(round_, 2), round(bokeh, 2), round(lnoise, 2), round(nfreq, 1), round(nfx, 2),
           round(feather, 3))
    got = _BOKEH_SPR.get(key)
    if got is not None:
        return got
    n = _BK
    c = (np.arange(n, dtype=np.float32) + 0.5) / n * 2 - 1
    x, y = c[None, :], c[:, None]
    r = np.sqrt(x * x + y * y)
    if sides >= 3:
        a = np.arctan2(y, x) + math.pi / 2
        seg = 2 * math.pi / sides
        poly = r * np.cos(np.mod(a, seg) - seg / 2) / math.cos(math.pi / sides)
        d = poly + (r - poly) * round_
    else:
        d = r
    edge = 0.97
    f = max(0.004, feather)
    alpha = np.clip((edge - d) / f + 0.5, 0, 1)
    rim = _smooth(edge - 0.25, edge, d)
    body = (1.0 - bokeh * 0.3) + bokeh * 0.6 * rim              # ободок ярче середины — как у оптики
    if lnoise > 0:
        rng = np.random.default_rng(int(nfreq * 10) + 7)
        cells = max(2, int(nfreq))
        nz = _value_noise(n, cells, rng)
        if abs(nfx - 1.0) > 0.01:      # Noise Freq Rel X — растягиваем шум по X
            idx = np.clip((np.arange(n) * nfx).astype(np.int32) % n, 0, n - 1)
            nz = nz[:, idx]
        body = body * (1.0 - lnoise * 0.4 * nz)
    got = (np.clip(alpha * body, 0, 2)).astype(np.float32)
    _BOKEH_SPR[key] = got
    while len(_BOKEH_SPR) > 32:
        _BOKEH_SPR.pop(next(iter(_BOKEH_SPR)))
    return got


def _hue_shift(rgb, dh, ds):
    """Сдвиг тона (в оборотах) и насыщенности цвета 0..1 — через HSV."""
    import colorsys
    h, s_, v = colorsys.rgb_to_hsv(*[min(1.0, max(0.0, c)) for c in rgb])
    h = (h + dh) % 1.0
    s_ = min(1.0, max(0.0, s_ * (1.0 + ds)))
    return colorsys.hsv_to_rgb(h, s_, v)


def _f_bokeh(arr, v, scale=1.0, frame=0, fps=30.0, seed=1, **_):
    """S_BokehLights: размытые огни расфокуса — дрейфуют, «дышат» размером, сдвигаются, складываются по Add."""
    from .effects import _fbm, blur
    h, w = arr.shape[:2]
    t = frame / max(fps, 1.0)
    n = int(max(0, min(2000, round(v["lights"]))))
    if n == 0 or v["bright"] <= 0:
        return arr
    size0 = max(1.0, v["size"] * scale)
    relh = max(0.001, v["relh"])
    feather = max(0.0, v["soft"] * scale) / size0 * 2
    alpha = _bokeh_shape(int(v["shape"]) + (2 if v["shape"] > 0 else 0), v["round"], v["bokeh"], v["lnoise"],
                         v["nfreq"], v["nfx"], feather)
    srcblur = None
    usrc = v["usesrc"]
    if usrc > 0:
        srcblur = blur(arr, max(1.0, v["smoothsrc"] * scale))
    base_col = [c / 255.0 for c in v["color"]]
    octaves = 1 + (1.0 - min(1.0, max(0.0, v["dsmooth"]))) * 3
    layer = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    layer.fill(0)
    p = QPainter(layer)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.setCompositionMode(QPainter.CompositionMode_Plus)
    salt = seed * 1.7
    shx = (v["shiftx0"] + v["shiftsx"] * t) * scale
    shy = (v["shifty0"] + v["shiftsy"] * t) * scale
    for i in range(n):
        u = [_h01(i, salt + k * 13.0) for k in range(8)]
        sz = size0 * (1.0 - v["vsize"] * u[2])
        sz *= max(0.05, 1.0 + v["dsized"] * _fbm(t * v["dsizes"] * 2 + u[3] * 50, salt + i, octaves))
        bw, bh = sz, sz * relh
        mx, my = w + bw, h + bh
        x = (u[0] * mx + shx + v["ddist"] * w * 0.15 * _fbm(t * v["dspeed"] + u[4] * 90, salt + i * 2, octaves))
        y = (u[1] * my + shy + v["ddist"] * w * 0.15 * _fbm(t * v["dspeed"] + u[5] * 90, salt + i * 2 + 1, octaves))
        x = np.mod(x, mx) - bw / 2
        y = np.mod(y, my) - bh / 2
        col = _hue_shift(base_col, (u[6] - 0.5) * v["vhue"], (u[7] - 0.5) * 2 * v["vsat"])
        if srcblur is not None:
            sx_, sy_ = int(min(w - 1, max(0, x))), int(min(h - 1, max(0, y)))
            pa = max(1.0, float(srcblur[sy_, sx_, 3]))
            sc = [float(srcblur[sy_, sx_, k]) / pa for k in (2, 1, 0)]
            col = [c + (s_ - c) * usrc for c, s_ in zip(col, sc)]
        b = v["bright"] * 3.0 * (1.0 - v["vbright"] * _h01(i, salt + 99.0) ** 2)
        rgb = [c * b for c in col]
        over = max(0.0, max(rgb) - 1.0)                   # пересвет уходит в белый
        spill = 0.3 * (1.0 - math.exp(-over * 0.3))
        rgb = [min(1.0, c + spill) for c in rgb]
        spr = np.empty((_BK, _BK, 4), np.float32)
        a = np.clip(alpha, 0, 1) * 255.0
        bright = np.clip(alpha, 0, 2)
        for k, c in zip((2, 1, 0), rgb):
            spr[..., k] = np.minimum(a, bright * c * 255.0)
        spr[..., 3] = a * min(1.0, max(rgb))
        spr[..., :3] = np.minimum(spr[..., :3], spr[..., 3:4])
        tr = QTransform()
        tr.translate(float(x), float(y))
        tr.rotate(v["rot"])
        tr.scale(bw / _BK, bh / _BK)
        tr.translate(-_BK / 2, -_BK / 2)
        p.setTransform(tr)
        p.drawImage(QPointF(0, 0), to_image(spr))
    p.end()
    L = to_array(layer)
    out = arr.copy()
    out[..., :3] = np.minimum(255.0, arr[..., :3] + L[..., :3])
    out[..., 3] = np.maximum(arr[..., 3], L[..., 3])
    out[..., :3] = np.minimum(out[..., :3], out[..., 3:4])
    return out


SHAPE_D = ("Форма — детали", "Shape Details")
DRIFT = ("Дрейф", "Drift")
NEW_EFFECTS.append(EffectDef("bokeh", "Огни боке (BokehLights)", "Bokeh Lights", [
    Param("bright", "Яркость", "Brightness", [0.5], 0.01, "", 0, 100),
    Param("color", "Цвет", "Color", [255.0, 255.0, 64.0], kind="color"),
    Param("vhue", "Разброс тона", "Vary Hue", [0.0], 0.01, "", 0, 1),
    Param("vsat", "Разброс насыщенности", "Vary Saturation", [0.0], 0.01, "", 0, 1),
    Param("vbright", "Разброс яркости", "Vary Brightness", [1.0], 0.01, "", 0, 1),
    Param("size", "Размер", "Size", [72.0], 0.5, " px", 0, 5000),
    Param("relh", "Отн. высота", "Rel Height", [1.0], 0.001, "", 0.001, 20),
    Param("vsize", "Разброс размера", "Vary Size", [0.2], 0.01, "", 0, 1),
    Param("soft", "Мягкость", "Softness", [2.4], 0.05, " px", 0, 500),
    Param("lights", "Количество огней", "Lights", [30.0], 0.2, "", 0, 2000),
    Param("dspeed", "Скорость дрейфа", "Drift Speed", [0.2], 0.01, "", 0, 50, group=DRIFT),
    Param("ddist", "Дальность дрейфа", "Drift Distance", [0.5], 0.01, "", 0, 20, group=DRIFT),
    Param("dsizes", "Скорость пульсации", "Drift Size Speed", [0.1], 0.01, "", 0, 50, group=DRIFT),
    Param("dsized", "Сила пульсации", "Drift Size Distance", [0.3], 0.01, "", 0, 5, group=DRIFT),
    Param("dsmooth", "Плавность дрейфа", "Drift Smoothness", [0.65], 0.01, "", 0, 1, group=DRIFT),
    Param("shiftsx", "Сдвиг X, px/с", "Shift Speed X", [0.0], 1.0, "", -100000, 100000),
    Param("shiftsy", "Сдвиг Y, px/с", "Shift Speed Y", [0.0], 1.0, "", -100000, 100000),
    Param("shiftx0", "Начальный сдвиг X", "Shift Start X", [0.0], 1.0, " px", -100000, 100000),
    Param("shifty0", "Начальный сдвиг Y", "Shift Start Y", [0.0], 1.0, " px", -100000, 100000),
    Param("usesrc", "Цвет из исходника", "Use Source Color", [0.25], 0.01, "", 0, 1),
    Param("smoothsrc", "Сглаживание цвета исходника", "Smooth Source Color", [96.0], 0.5, " px", 0, 2000),
    Param("shape", "Форма", "Shape", [5.0], kind="choice", choices=BOKEH_SHAPES),
    Param("round", "Скруглённость", "Roundness", [0.3], 0.01, "", 0, 1, group=SHAPE_D),
    Param("rot", "Поворот", "Rotate", [0.0], 0.5, "°", kind="angle", group=SHAPE_D),
    Param("bokeh", "Ободок (боке)", "Bokeh", [0.5], 0.01, "", 0, 1, group=SHAPE_D),
    Param("lnoise", "Шум линзы", "Lens Noise", [0.5], 0.01, "", 0, 1, group=SHAPE_D),
    Param("nfreq", "Частота шума", "Noise Freq", [20.0], 0.1, "", 1, 64, group=SHAPE_D),
    Param("nfx", "Отн. частота шума по X", "Noise Freq Rel X", [1.0], 0.01, "", 0.1, 10, group=SHAPE_D),
], opts={"seed": 1}, category="sapphire"))


# ============================================================================ S_Rays
def _zoom_toward(img: QImage, cx, cy, f: float, opacity: float) -> QImage:
    """Один проход лучей: смесь картинки с её копией, масштабированной от центра на f."""
    out = QImage(img)
    p = QPainter(out)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    t = QTransform()
    t.translate(cx, cy)
    t.scale(f, f)
    t.translate(-cx, -cy)
    p.setTransform(t)
    p.setOpacity(opacity)
    p.drawImage(QPointF(0, 0), img)
    p.end()
    return out


def radial_streaks(arr, cx, cy, length, reverse=0.0, passes=8):
    """Радиальное размытие «лучами» за O(log n): каждый проход смешивает картинку с копией,
    растянутой от центра, и удваивает шаг — лучи длиной до length×(доля расстояния до центра)."""
    if length <= 1e-4:
        return arr
    img = to_image(arr)
    # итоговый масштаб последнего слоя ≈ 1 + length; делим на проходы геометрически
    k = (1.0 + length) ** (1.0 / (2 ** passes - 1))
    if reverse > 0:
        k = 1.0 / k if reverse >= 0.5 else k
    f = k
    for _ in range(passes):          # строго 0.5: все сэмплы равного веса, иначе луч «двоится»
        img = _zoom_toward(img, cx, cy, f, 0.5)
        f *= f
    return to_array(img)


def radial_rays(arr, cx, cy, length, reverse=0.0):
    """Лучи с затуханием к концу: сумма равномерных лучей длиной L, L/3, L/9 (ярче у источника)."""
    if length <= 1e-4:
        return arr
    h, w = arr.shape[:2]
    if w >= 1280:      # лучи размытые — считаем в половинном кадре
        from .effects import _downsample, _resize
        cut = _downsample(arr, 2)
        return _resize(radial_rays(cut, cx / 2, cy / 2, length, reverse), w, h)
    out = radial_streaks(arr, cx, cy, length, reverse) * 0.3
    out += radial_streaks(arr, cx, cy, length / 3, reverse) * 0.3
    out += radial_streaks(arr, cx, cy, length / 9, reverse, passes=6) * 0.4
    return out


def _aniso_blur(arr, radius, relx, rely):
    from .effects import _resize, blur
    if radius <= 0.3:
        return arr
    if abs(relx - rely) < 1e-3:
        return blur(arr, radius * relx)
    h, w = arr.shape[:2]
    rx, ry = max(0.05, relx), max(0.05, rely)
    m = max(rx, ry)
    small = _resize(arr, max(2, int(w * m / rx)), max(2, int(h * m / ry))) if m > 0 else arr
    small = blur(small, radius * m)
    return _resize(small, w, h)


def _rays_source(arr, v, scale, frame, fps, seed):
    """Что светится: яркое выше порога (или альфа), умноженное на цвет; плюс «атмосфера» (шум)."""
    a = arr[..., 3:4] / 255.0
    lum = (arr[..., 0] * 0.114 + arr[..., 1] * 0.587 + arr[..., 2] * 0.299) / 255.0
    lum = lum + (arr[..., 3] / 255.0 - lum) * v["fromalpha"]
    th = v["thresh"]
    m = np.clip((lum - th) / max(1e-3, 1.0 - th), 0, 1)[..., None]
    src = arr * m
    add = np.array([v["thcolor"][2], v["thcolor"][1], v["thcolor"][0], 0.0], np.float32)
    if add[:3].any():
        src = src + add * m * a
        src[..., :3] = np.minimum(src[..., :3], 255.0)
    if v["atmo"] > 0:   # «атмосфера»: лучи проходят сквозь неоднородную дымку
        h, w = arr.shape[:2]
        rng = np.random.default_rng(int(seed) + int(frame * v["atmospeed"] / max(fps, 1)))
        n = max(8, int(v["atmofreq"]))
        nz = _value_noise(256, n, rng)
        nz = to_array(to_image(np.repeat(nz[..., None] * 255.0, 4, axis=2).astype(np.float32))
                      .scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))[..., :1] / 255.0
        src = src * (1.0 - v["atmo"] + v["atmo"] * nz * 2.0)
    return src


def _f_rays(arr, v, scale=1.0, frame=0, fps=30.0, seed=1, **_):
    """S_Rays (Light Rays): яркие части кадра дают лучи от центра; лучи складываются с исходником."""
    h, w = arr.shape[:2]
    cx, cy = v["center"][0] * scale, v["center"][1] * scale
    src = _rays_source(arr, v, scale, frame, fps, seed)
    L = v["length"]
    lens = (L * v["lr"], L * v["lg"], L * v["lb"])
    rev = v["reverse"]
    if max(lens) - min(lens) < 1e-3:
        rays = radial_rays(src, cx, cy, lens[0], rev)
    else:   # у каждого канала своя длина — радужные края лучей
        rays = np.empty_like(src)
        for ch, ln in ((2, lens[0]), (1, lens[1]), (0, lens[2])):
            plane = np.repeat(src[..., ch:ch + 1], 4, axis=2)
            rays[..., ch] = radial_rays(plane, cx, cy, ln, rev)[..., 0]
        rays[..., 3] = radial_rays(src, cx, cy, max(lens), rev)[..., 3]
    rays = _aniso_blur(rays, v["blur"] * scale, v["blurx"], v["blury"])
    if v["shimmer"] > 0:   # мерцание: яркость лучей меняется по углу и во времени
        xs, ys = _grid(h, w)
        ang = np.arctan2(ys - cy, xs - cx)
        ph = frame / max(fps, 1.0) * v["shimspeed"]
        sh = 0.5 + 0.5 * np.sin(ang * v["shimfreq"] + ph * 2 * math.pi) * np.sin(ang * v["shimfreq"] * 0.37 + 1.3 + ph * 4.1)
        rays[..., :3] *= (1.0 - v["shimmer"] + v["shimmer"] * sh * 2.0)[..., None]
    if v["bias"] != 0:
        xs, ys = _grid(h, w)
        r = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2) / max(w, h)
        rays[..., :3] *= np.clip(1.0 + v["bias"] * (r * 2.0 - 0.5), 0, None)[..., None]
    col = np.array([v["color"][2], v["color"][1], v["color"][0]], np.float32) / 255.0
    rgb = rays[..., :3] * col * v["bright"]
    out = arr.copy()
    out[..., :3] = arr[..., :3] + rgb
    if v["affalpha"] > 0:
        ra = np.clip(rgb.max(axis=2), 0, 255)
        out[..., 3] = np.maximum(arr[..., 3], ra * v["affalpha"])
    out[..., :3] = np.minimum(out[..., :3], 255.0)
    out[..., :3] = np.minimum(out[..., :3], out[..., 3:4])
    return out


SHIMMER = ("Мерцание", "Shimmer")
ATMO = ("Атмосфера", "Atmosphere")
NEW_EFFECTS.append(EffectDef("rays", "Лучи света (Rays)", "Rays", [
    Param("center", "Центр", "Center XY", [0.5, 0.5], 1.0, kind="point"),
    Param("length", "Длина лучей", "Rays Length", [1.0], 0.005, "", 0, 50),
    Param("lr", "Длина красного", "Length Red", [1.0], 0.005, "", 0, 10),
    Param("lg", "Длина зелёного", "Length Green", [1.0], 0.005, "", 0, 10),
    Param("lb", "Длина синего", "Length Blue", [1.0], 0.005, "", 0, 10),
    Param("reverse", "Обратные лучи", "Reverse Rays", [0.0], 0.01, "", 0, 1),
    Param("bright", "Яркость лучей", "Rays Brightness", [3.0], 0.01, "", 0, 100),
    Param("blur", "Размытие лучей", "Blur Rays", [0.0], 0.1, " px", 0, 500),
    Param("blurx", "Размытие: отн. X", "Blur Rays Rel X", [1.0], 0.01, "", 0, 10),
    Param("blury", "Размытие: отн. Y", "Blur Rays Rel Y", [1.0], 0.01, "", 0, 10),
    Param("color", "Цвет лучей", "Rays Color", [255.0, 255.0, 255.0], kind="color"),
    Param("bias", "Яркость к краям", "Bias Outer Bright", [0.0], 0.01, "", -1, 4),
    Param("thresh", "Порог", "Threshold", [0.5], 0.005, "", 0, 0.999),
    Param("thcolor", "Цвет поверх порога", "Threshold Add Color", [0.0, 0.0, 0.0], kind="color"),
    Param("shimmer", "Сила мерцания", "Shimmer Amount", [0.0], 0.01, "", 0, 1, group=SHIMMER),
    Param("shimfreq", "Частота мерцания", "Shimmer Freq", [24.0], 0.1, "", 1, 400, group=SHIMMER),
    Param("shimspeed", "Скорость мерцания", "Shimmer Speed", [0.5], 0.01, "", -50, 50, group=SHIMMER),
    Param("atmo", "Сила атмосферы", "Atmosphere Amount", [0.0], 0.01, "", 0, 1, group=ATMO),
    Param("atmofreq", "Зерно атмосферы", "Atmosphere Freq", [12.0], 0.1, "", 2, 128, group=ATMO),
    Param("atmospeed", "Скорость атмосферы", "Atmosphere Speed", [0.0], 0.01, "", 0, 60, group=ATMO),
    Param("affalpha", "Влияет на альфу", "Affect Alpha", [1.0], 0.01, "", 0, 1),
    Param("fromalpha", "Лучи из альфы", "Rays From Alpha", [0.0], 0.01, "", 0, 1),
], opts={"seed": 1}, category="sapphire"))


# ============================================================================ S_Glint
def _shift_streak(img: QImage, dx: float, dy: float, passes: int) -> QImage:
    """Луч в одну сторону длиной ~(dx, dy) за log-проходы: смесь со сдвинутой копией, шаг удваивается."""
    sx, sy = dx / (2 ** passes - 1), dy / (2 ** passes - 1)
    for _ in range(passes):
        out = QImage(img)
        p = QPainter(out)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.setOpacity(0.5)
        p.drawImage(QPointF(sx, sy), img)
        p.end()
        img = out
        sx, sy = sx * 2, sy * 2
    return img


def _down_factor(w, length):
    """Длинные размытые лучи считаем в уменьшенном кадре — на вид то же, в разы быстрее."""
    d = 1
    while d < 4 and length / (d * 2) > 24 and w / (d * 2) >= 320:
        d *= 2
    return d


def star_streaks(arr, length, rays=4, angle=0.0, passes=7):
    """Звезда: от каждой точки лучи в rays направлений; затухание — сумма длин L, L/3, L/9."""
    if length <= 0.5 or rays < 1:
        return np.zeros_like(arr)
    h, w = arr.shape[:2]
    d = _down_factor(w, length)
    if d > 1:
        from .effects import _downsample, _resize
        return _resize(star_streaks(_downsample(arr, d), length / d, rays, angle, passes), w, h)
    src = to_image(arr)
    acc = np.zeros_like(arr)
    for k in range(int(rays)):
        a = math.radians(angle + 360.0 * k / int(rays))
        for frac, wgt in ((1.0, 0.3), (1 / 3, 0.3), (1 / 9, 0.4)):
            L = length * frac
            acc += to_array(_shift_streak(src, math.cos(a) * L, math.sin(a) * L,
                                          max(3, min(passes, int(math.log2(max(2.0, L))) + 1)))) * wgt
    return acc * (2.0 / max(1, int(rays)))


def _f_glint(arr, v, scale=1.0, frame=0, fps=30.0, seed=1, **_):
    """S_Glint: звёздные блики-«кресты» от ярких точек (как от звёздного фильтра на объективе)."""
    from .effects import blur
    a = arr[..., 3:4] / 255.0
    lum = (arr[..., 0] * 0.114 + arr[..., 1] * 0.587 + arr[..., 2] * 0.299) / 255.0
    lum = lum + (arr[..., 3] / 255.0 - lum) * v["fromalpha"]
    th = v["thresh"]
    m = np.clip((lum - th) / max(1e-3, 1.0 - th), 0, 1)[..., None]
    src = arr * m
    add = np.array([v["thcolor"][2], v["thcolor"][1], v["thcolor"][0], 0.0], np.float32)
    if add[:3].any():
        src = src + add * m * a
    if v["thblur"] > 0.3:
        src = blur(src, v["thblur"] * scale)
    L = v["size"] * scale * 0.5
    rays, rot = int(v["rays"]), v["rotate"]
    rels = (v["sr"], v["sg"], v["sb"])
    if max(rels) - min(rels) < 1e-3:
        g = star_streaks(src, L * rels[0], rays, rot)
    else:   # у каждого канала своя длина лучей — радужные концы
        g = np.zeros_like(src)
        for ch, rel in ((2, rels[0]), (1, rels[1]), (0, rels[2])):
            g[..., ch] = star_streaks(np.repeat(src[..., ch:ch + 1], 4, axis=2), L * rel, rays, rot)[..., 0]
        g[..., 3] = g[..., :3].max(axis=2)
    if v["blurg"] > 0.3:
        g = blur(g, v["blurg"] * scale)
    gain = np.array([v["br"], v["bg"], v["bb"]], np.float32) * v["bright"] * 8.0
    rgb = g[..., :3] * gain
    if abs(v["hue"]) > 1e-3:   # сдвиг тона бликов — поворот вокруг серой оси
        th_ = v["hue"] * 2 * math.pi
        c, s_ = math.cos(th_), math.sin(th_)
        k = (1 - c) / 3.0
        sq = math.sqrt(1 / 3) * s_
        M = np.array([[c + k, k - sq, k + sq], [k + sq, c + k, k - sq], [k - sq, k + sq, c + k]], np.float32)
        rgb = np.maximum(rgb @ M.T, 0)
    srcw = arr * v["srcop"]
    under = v["under"]
    out = np.empty_like(arr)
    out[..., :3] = srcw[..., :3] * v["bgbright"] + rgb * (1.0 - under * a)
    ga = np.clip(rgb.max(axis=2), 0, 255) * v["affalpha"]
    out[..., 3] = np.maximum(srcw[..., 3], ga)
    out[..., :3] = np.minimum(np.minimum(out[..., :3], 255.0), out[..., 3:4])
    return out


BR_D = ("Яркость — детали", "Brightness Details")
SIZE_D = ("Размер — детали", "Size Details")
NEW_EFFECTS.append(EffectDef("glint", "Звёздные блики (Glint)", "Glint", [
    Param("bright", "Яркость", "Brightness", [0.14], 0.001, "", 0, 50),
    Param("hue", "Сдвиг тона", "Hue Shift", [0.0], 0.001, "", -1, 1),
    Param("br", "Яркость красного", "Brightness Red", [1.0], 0.01, "", 0, 10, group=BR_D),
    Param("bg", "Яркость зелёного", "Brightness Green", [1.0], 0.01, "", 0, 10, group=BR_D),
    Param("bb", "Яркость синего", "Brightness Blue", [1.0], 0.01, "", 0, 10, group=BR_D),
    Param("thresh", "Порог", "Threshold", [0.7], 0.005, "", 0, 0.999),
    Param("thcolor", "Цвет поверх порога", "Threshold Add Color", [0.0, 0.0, 0.0], kind="color"),
    Param("thblur", "Размытие порога", "Threshold Blur", [5.38], 0.05, " px", 0, 500),
    Param("size", "Размер", "Size", [480.0], 1.0, " px", 0, 10000),
    Param("rays", "Число лучей", "Num Rays", [4.0], 0.05, "", 1, 16, group=SIZE_D),
    Param("rotate", "Поворот лучей", "Rotate", [90.0], 0.5, "°", kind="angle", group=SIZE_D),
    Param("sr", "Размер красного", "Size Red", [1.0], 0.01, "", 0, 10, group=SIZE_D),
    Param("sg", "Размер зелёного", "Size Green", [0.8], 0.01, "", 0, 10, group=SIZE_D),
    Param("sb", "Размер синего", "Size Blue", [0.6], 0.01, "", 0, 10, group=SIZE_D),
    Param("blurg", "Размытие бликов", "Blur Glint", [0.0], 0.1, " px", 0, 500),
    Param("affalpha", "Влияет на альфу", "Affect Alpha", [1.0], 0.01, "", 0, 1),
    Param("fromalpha", "Блики из альфы", "Glint From Alpha", [0.0], 0.01, "", 0, 1),
    Param("under", "Блики под исходником", "Glint Under Source", [0.0], 0.01, "", 0, 1),
    Param("srcop", "Непрозрачность исходника", "Source Opacity", [1.0], 0.01, "", 0, 1),
    Param("bgbright", "Яркость фона", "Bg Brightness", [1.0], 0.01, "", 0, 4),
], category="sapphire"))


# ============================================================================ S_Hotspots
def _f_hotspots(arr, v, scale=1.0, **_):
    """S_Hotspots: оставляет только яркие места выше порога (мягкий спад к порогу), остальное — в чёрное."""
    from .effects import blur
    src = blur(arr, v["blurin"] * scale) if v["blurin"] > 0.3 else arr
    lum = (src[..., 0] * 0.114 + src[..., 1] * 0.587 + src[..., 2] * 0.299) / 255.0
    th = v["thresh"]
    m = np.clip((lum - th) / max(1e-3, 1.0 - th), 0, 1)[..., None]
    out = arr.copy()
    rgb = arr[..., :3]
    if abs(v["sat"] - 1.0) > 1e-3:
        g = (rgb[..., 0] * 0.114 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.299)[..., None]
        rgb = g + (rgb - g) * v["sat"]
    add = np.array([v["thcolor"][2], v["thcolor"][1], v["thcolor"][0]], np.float32)
    out[..., :3] = np.clip((rgb * v["bright"] + add) * m, 0, None)
    out[..., :3] = np.minimum(out[..., :3], out[..., 3:4])
    return out


NEW_EFFECTS.append(EffectDef("hotspots", "Яркие пятна (Hotspots)", "Hotspots", [
    Param("blurin", "Размытие входа", "Blur Input", [0.0], 0.1, " px", 0, 500),
    Param("thresh", "Порог", "Threshold", [0.71], 0.005, "", 0, 0.999),
    Param("thcolor", "Цвет поверх порога", "Threshold Add Color", [0.0, 0.0, 0.0], kind="color"),
    Param("sat", "Насыщенность", "Saturation", [1.0], 0.01, "", 0, 4),
    Param("bright", "Яркость", "Brightness", [1.0], 0.01, "", 0, 10),
], category="sapphire"))


# ============================================================================ S_HalfTone / S_HalfToneColor
def _halftone_cover(plane, w, h, v, scale, angle_add=0.0, shift=(0.0, 0.0)):
    """Покрытие точками 0..1 для одного «канала»: plane — значение 0..1 (сколько краски), берётся
    в центре ячейки повёрнутой сетки; радиус точки ~ √покрытия, край — по Dots Sharpness."""
    xs, ys = _grid(h, w)
    cell = max(1.5, w / max(1.0, v["freq"]))           # Dots Frequency — точек на ширину кадра
    a = math.radians(v["angle"] + angle_add)
    ca, sa = math.cos(a), math.sin(a)
    relw = max(0.05, v["relw"])
    u = (xs * ca + ys * sa) / (cell * relw) + v["shx"] + shift[0]
    t = (-xs * sa + ys * ca) / cell + v["shy"] + shift[1]
    cu0, ct0 = np.floor(u) + 0.5, np.floor(t) + 0.5
    su, st = np.sign(u - cu0), np.sign(t - ct0)
    soft = 0.5 / max(0.2, v["sharp"] * 2.0)
    cov = None
    # своя ячейка + 3 соседние в сторону пикселя: крупные точки (>50%) заходят к соседям и сливаются
    for ou, ot in ((0, 0), (1, 0), (0, 1), (1, 1)):
        cu, ct = cu0 + su * ou, ct0 + st * ot
        pu, pt = (cu - v["shx"] - shift[0]) * cell * relw, (ct - v["shy"] - shift[1]) * cell
        px, py = pu * ca - pt * sa, pu * sa + pt * ca   # центр ячейки в пикселях — там берём значение
        val = _remap(plane[..., None], np.clip(px, 0, w - 1), np.clip(py, 0, h - 1), nearest=True)[..., 0]
        val = np.clip(val - v["lighten"], 0, 1)
        r = np.sqrt(val) * 0.72                          # 0.72 — при 100% точки смыкаются
        fu, ft = u - cu, t - ct
        c = np.clip((r - np.sqrt(fu * fu + ft * ft)) / soft + 0.5, 0, 1) * (val > 0.002)
        cov = c if cov is None else np.maximum(cov, c)
    return cov


def _ht_prep(arr, v, scale):
    from .effects import blur
    src = blur(arr, v["smooth"] * scale) if v["smooth"] > 0.3 else arr
    a = np.maximum(src[..., 3:4], 1.0)
    return np.clip(src[..., :3] / a, 0, 1)               # B G R без премультипликации, 0..1


def _f_halftone(arr, v, scale=1.0, **_):
    h, w = arr.shape[:2]
    rgb = _ht_prep(arr, v, scale)
    lum = rgb[..., 0] * 0.114 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.299
    white = v["dots"] == 1
    cov = _halftone_cover(lum if white else 1.0 - lum, w, h, v, scale)[..., None]
    c1 = np.array([v["c1"][2], v["c1"][1], v["c1"][0]], np.float32)
    c0 = np.array([v["c0"][2], v["c0"][1], v["c0"][0]], np.float32)
    ink, paper = (c1, c0) if white else (c0, c1)
    out = arr.copy()
    out[..., :3] = (paper + (ink - paper) * cov) * (arr[..., 3:4] / 255.0)
    return out


HT_COLOR_MODES = [("CMY (печать)", "CMY"), ("RGB (экран)", "RGB")]


def _f_halftonecolor(arr, v, scale=1.0, **_):
    h, w = arr.shape[:2]
    rgb = _ht_prep(arr, v, scale)
    if abs(v["sat"] - 1.0) > 1e-3:
        g = (rgb[..., 0] * 0.114 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.299)[..., None]
        rgb = np.clip(g + (rgb - g) * v["sat"], 0, 1)
    out = arr.copy()
    cmy = v["mode"] == 0
    # классические углы растра: C 15°, M 75°, Y 0° (относительно Dots Angle); у RGB — те же сдвиги
    for ch, add, key in ((2, 15.0, "r"), (1, 75.0, "g"), (0, 0.0, "b")):
        sh = (v[f"sh{key}x"], v[f"sh{key}y"])
        if cmy:
            c = _halftone_cover(1.0 - rgb[..., ch], w, h, v, scale, add, sh)
            val = 1.0 - c
        else:
            val = _halftone_cover(rgb[..., ch], w, h, v, scale, add, sh)
        out[..., ch] = val * arr[..., 3]
    return out


def _ht_common(freq):
    return [
        Param("freq", "Частота точек", "Dots Frequency", [freq], 0.5, "", 2, 2000),
        Param("angle", "Угол точек", "Dots Angle", [-30.0], 0.5, "°", kind="angle"),
        Param("relw", "Отн. ширина точек", "Dots Rel Width", [1.0], 0.005, "", 0.05, 10),
        Param("sharp", "Резкость точек", "Dots Sharpness", [4.0], 0.01, "", 0.2, 50),
        Param("lighten", "Осветление точек", "Dots Lighten", [0.0], 0.005, "", -1, 1),
        Param("smooth", "Сглаживание исходника", "Smooth Source", [0.0], 0.1, " px", 0, 200),
    ]


SHIFT_RGB = ("Сдвиг точек по каналам", "Dots Shift RGB")
NEW_EFFECTS.append(EffectDef("halftone", "Растр (HalfTone)", "HalfTone", [
    Param("dots", "Точки", "Dots", [0.0], kind="choice", choices=[("Чёрные", "Black"), ("Белые", "White")]),
    *_ht_common(76.0),
    Param("c1", "Цвет 1 (бумага)", "Color1", [255.0, 255.0, 255.0], kind="color"),
    Param("c0", "Цвет 0 (точки)", "Color0", [0.0, 0.0, 0.0], kind="color"),
    Param("shx", "Сдвиг точек X", "Dots Shift X", [0.0], 0.005, "", -1000, 1000),
    Param("shy", "Сдвиг точек Y", "Dots Shift Y", [0.0], 0.005, "", -1000, 1000),
], category="sapphire"))
NEW_EFFECTS.append(EffectDef("halftonecolor", "Цветной растр (HalfToneColor)", "HalfTone Color", [
    Param("mode", "Цвет точек", "Dots Color", [0.0], kind="choice", choices=HT_COLOR_MODES),
    *_ht_common(162.0),
    Param("sat", "Насыщенность", "Saturation", [1.0], 0.01, "", 0, 4),
    Param("shx", "Сдвиг точек X", "Dots Shift X", [0.0], 0.005, "", -1000, 1000),
    Param("shy", "Сдвиг точек Y", "Dots Shift Y", [0.0], 0.005, "", -1000, 1000),
    *[Param(f"sh{c}{ax}", f"Сдвиг {nm} {ax.upper()}", f"Shift {en} {ax.upper()}", [0.0], 0.005, "", -100, 100,
            group=SHIFT_RGB)
      for c, nm, en in (("r", "красного", "Red"), ("g", "зелёного", "Green"), ("b", "синего", "Blue"))
      for ax in ("x", "y")],
], category="sapphire"))


# ============================================================================ повреждения: общие помощники
def _frame_rng(seed, frame, salt=0):
    return np.random.default_rng([int(abs(seed) * 1000) % 2147483647, int(frame) & 0x7FFFFFFF, salt])


def _lum01(arr):
    return (arr[..., 0] * 0.114 + arr[..., 1] * 0.587 + arr[..., 2] * 0.299) / 255.0


# ============================================================================ S_DigitalDamage
DD_TYPES = ("freeze", "shift", "brights", "pixelate", "blocknoise", "invert", "flow")


def _f_digitaldamage(arr, v, scale=1.0, frame=0, fps=30.0, seed=1, **_):
    """S_DigitalDamage: битые блоки цифрового потока — сдвиг, «заморозка» (чужой кусок кадра), пикселизация,
    шумовые блоки, инверсия, «течение» (датамош), шум в светах. Intensity — доля битых блоков,
    Time Intensity — как часто картина повреждений меняется."""
    h, w = arr.shape[:2]
    on = [t for t in DD_TYPES if v[t]]
    if not on or v["intensity"] <= 0:
        return arr
    bh = max(4, int(48 * v["size"] * scale))
    bw = max(4, int(bh * max(0.05, v["relx"]) * 2))
    slot = int(frame * max(0.01, v["tint"]) / 2)          # при Time Intensity 1 — смена каждые 2 кадра
    rng = _frame_rng(v["seed"] + seed, slot)
    out = arr.copy()
    ny, nx = (h + bh - 1) // bh, (w + bw - 1) // bw
    hit = rng.random((ny, nx)) < min(1.0, 0.12 * v["intensity"])
    # повреждения идут «полосами», как в настоящем потоке: соседний справа блок часто тоже битый
    hit |= np.roll(hit, 1, axis=1) & (rng.random((ny, nx)) < 0.6)
    kinds = rng.integers(0, len(on), (ny, nx))
    for by, bx in zip(*np.nonzero(hit)):
        y0, x0 = by * bh, bx * bw
        y1, x1 = min(h, y0 + bh), min(w, x0 + bw)
        blk = out[y0:y1, x0:x1]
        kind = on[kinds[by, bx]]
        if kind == "shift":
            blk[:] = np.roll(arr[y0:y1], int(rng.integers(-w // 6, w // 6)), axis=1)[:, x0:x1]
        elif kind == "freeze":     # чужой кусок кадра (у нас нет прошлых кадров — берём из другого места)
            sy = int(rng.integers(0, max(1, h - (y1 - y0))))
            sx = int(rng.integers(0, max(1, w - (x1 - x0))))
            blk[:] = arr[sy:sy + y1 - y0, sx:sx + x1 - x0]
        elif kind == "pixelate":
            k = max(2, (y1 - y0) // 4)
            sub = arr[y0:y1:k, x0:x1:k]
            blk[:] = np.repeat(np.repeat(sub, k, 0), k, 1)[:y1 - y0, :x1 - x0]
        elif kind == "blocknoise":
            k = max(2, (y1 - y0) // 6)
            nz = rng.random(((y1 - y0 + k - 1) // k, (x1 - x0 + k - 1) // k, 3)).astype(np.float32) * 255
            nz = np.repeat(np.repeat(nz, k, 0), k, 1)[:y1 - y0, :x1 - x0]
            blk[..., :3] = nz * (blk[..., 3:4] / 255.0)
        elif kind == "invert":
            blk[..., :3] = blk[..., 3:4] - blk[..., :3]
        elif kind == "flow":       # верхняя строка блока «течёт» вниз
            blk[:] = arr[y0:y0 + 1, x0:x1]
        elif kind == "brights":
            m = (_lum01(blk) > 0.55)[..., None]
            nz = rng.random(blk[..., :3].shape).astype(np.float32) * 255
            blk[..., :3] = np.where(m, nz * (blk[..., 3:4] / 255.0), blk[..., :3])
    return out


# ============================================================================ S_FilmDamage
def _f_filmdamage(arr, v, scale=1.0, frame=0, fps=30.0, seed=1, **_):
    """S_FilmDamage: старая плёнка — зерно, пятна, пыль, волоски, царапины, тряска кадра, виньетка,
    мерцание яркости, плавающий расфокус. Всё случайное зависит от кадра и Seed."""
    from .effects import blur, transform_array
    h, w = arr.shape[:2]
    sd = v["seed"] * 1000 + seed
    rng = _frame_rng(sd, frame)
    out = arr
    if v["shake"] > 0:         # тряска: кадр «прыгает» в фильмовом канале, в основном по вертикали
        out = transform_array(out, rng.normal(0, 1) * v["shake"] * 6 * scale,
                              rng.normal(0, 1) * v["shake"] * 14 * scale, 0.0)
    if v["defocus"] > 0:
        r = v["defocus"] * 6 * scale * (0.5 + 0.5 * abs(math.sin(frame * 0.37 + sd)))
        if r > 0.3:
            out = blur(out, r)
    out = out.copy()
    a = out[..., 3:4]
    if v["flicker"] > 0:
        out[..., :3] *= max(0.0, 1.0 + rng.normal(0, 1) * v["flicker"] * 0.06)
    if v["vignette"] > 0:
        ys = (np.arange(h, dtype=np.float32) / h - 0.5)[:, None]
        xs = (np.arange(w, dtype=np.float32) / w - 0.5)[None, :]
        out[..., :3] *= (1.0 - v["vignette"] * np.clip((xs * xs + ys * ys) * 2.6, 0, 1) ** 1.5)[..., None]
    if v["grain"] > 0:         # зерно — по яркости, слегка цветное, зерно ~1.5 px
        g = rng.normal(0, 1, (max(1, int(h / 1.5)), max(1, int(w / 1.5)), 1)).astype(np.float32)
        g = np.repeat(np.repeat(g, 2, 0), 2, 1)[:h, :w] if g.shape[0] * 2 >= h else np.resize(g, (h, w, 1))
        out[..., :3] += g[:h, :w] * v["grain"] * 9.0 * (a / 255.0)
    # штрихи и пятна рисуем QPainter-ом поверх: тёмные (на позитиве) и иногда светлые
    over = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    over.fill(0)
    p = QPainter(over)
    p.setRenderHint(QPainter.Antialiasing)
    p.setPen(Qt.NoPen)
    n_st = rng.poisson(v["stains"] * 2)
    for _ in range(n_st):      # пятна — крупные мягкие кляксы
        r = rng.uniform(20, 90) * scale
        col = QColor(40, 30, 20, int(rng.uniform(40, 110)))
        p.setBrush(col)
        p.drawEllipse(QPointF(rng.uniform(0, w), rng.uniform(0, h)), r, r * rng.uniform(0.5, 1.0))
    for _ in range(rng.poisson(v["dust"])):       # пыль
        r = max(0.6, rng.exponential(1.6) * scale)
        c = 0 if rng.random() < 0.75 else 235
        p.setBrush(QColor(c, c, c, int(rng.uniform(120, 230))))
        p.drawEllipse(QPointF(rng.uniform(0, w), rng.uniform(0, h)), r, r * rng.uniform(0.4, 1.0))
    for _ in range(rng.poisson(v["hairs"])):      # волоски — изогнутые тонкие линии
        pen = QPen(QColor(10, 10, 10, 200), max(0.8, 1.3 * scale))
        p.setPen(pen)
        x, y = rng.uniform(0, w), rng.uniform(0, h)
        ang = rng.uniform(0, 2 * math.pi)
        ln = rng.uniform(20, 120) * scale
        for _k in range(10):
            ang += rng.normal(0, 0.35)
            nx_, ny_ = x + math.cos(ang) * ln / 10, y + math.sin(ang) * ln / 10
            p.drawLine(QPointF(x, y), QPointF(nx_, ny_))
            x, y = nx_, ny_
        p.setPen(Qt.NoPen)
    srng = np.random.default_rng(int(sd) + 77)    # царапины живут несколько кадров и медленно ползут
    for k in range(int(v["scratches"])):
        life = int(srng.integers(8, 40))
        ph = (frame + int(srng.integers(0, 100))) // life
        lr = np.random.default_rng([int(sd) + 7, k, ph])
        if lr.random() > 0.7:
            continue
        x = lr.uniform(0, w) + (frame % life) * lr.normal(0, 0.6) * scale
        light = lr.random() < 0.5
        c = 230 if light else 20
        p.setPen(QPen(QColor(c, c, c, int(lr.uniform(60, 160))), max(0.7, lr.uniform(0.6, 2.0) * scale)))
        p.drawLine(QPointF(x, lr.uniform(-h * 0.2, h * 0.3)), QPointF(x + lr.normal(0, 3) * scale, lr.uniform(h * 0.6, h * 1.2)))
    p.end()
    o = to_array(over)
    if v["flip"]:
        o = o[::-1]
    out[..., :3] = out[..., :3] * (1 - o[..., 3:4] / 255.0) + o[..., :3] * (a / 255.0)
    out[..., :3] = np.clip(out[..., :3], 0, None)
    out[..., :3] = np.minimum(out[..., :3], a)
    return out


# ============================================================================ S_JpegDamage
_JQ_Y = np.array([16, 11, 10, 16, 24, 40, 51, 61, 12, 12, 14, 19, 26, 58, 60, 55, 14, 13, 16, 24, 40, 57, 69, 56,
                  14, 17, 22, 29, 51, 87, 80, 62, 18, 22, 37, 56, 68, 109, 103, 77, 24, 35, 55, 64, 81, 104, 113, 92,
                  49, 64, 78, 87, 103, 121, 120, 101, 72, 92, 95, 98, 112, 100, 103, 99], np.float32).reshape(8, 8)
_JQ_C = np.full((8, 8), 99.0, np.float32)
_JQ_C[:4, :4] = [[17, 18, 24, 47], [18, 21, 26, 66], [24, 26, 56, 99], [47, 66, 99, 99]]


def _dct_mat():
    k = np.arange(8)
    m = np.cos((2 * k[None, :] + 1) * k[:, None] * math.pi / 16) * math.sqrt(2 / 8)
    m[0] /= math.sqrt(2)
    return m.astype(np.float32)


_DCT = _dct_mat()


def _f_jpegdamage(arr, v, scale=1.0, frame=0, fps=30.0, seed=1, **_):
    """S_JpegDamage: настоящее JPEG-сжатие 8×8 (DCT + квантование) с управляемым качеством, весами
    частот, ошибками блоков (битые коэффициенты) — отсюда «квадраты» и цветные артефакты."""
    from .effects import _resize
    h, w = arr.shape[:2]
    # блоки 8×8 — в пикселях композиции (в превью их столько же, сколько в рендере)
    res = max(1.0, v["res"]) * max(scale, 0.05)
    sw, sh = max(8, int(w / (res * max(0.05, v["resx"])))), max(8, int(h / res))
    small = _resize(arr, sw, sh) if (sw, sh) != (w, h) else arr
    a = np.maximum(small[..., 3:4], 1.0)
    bgr = np.clip(small[..., :3] / a * 255.0, 0, 255)
    b_, g_, r_ = bgr[..., 0], bgr[..., 1], bgr[..., 2]
    Y = 0.299 * r_ + 0.587 * g_ + 0.114 * b_ - 128
    Cb = -0.168736 * r_ - 0.331264 * g_ + 0.5 * b_
    Cr = 0.5 * r_ - 0.418688 * g_ - 0.081312 * b_
    H8, W8 = (sh + 7) // 8 * 8, (sw + 7) // 8 * 8
    q = max(0.001, v["quality"])
    qs = (1.0 / q) * 0.5                                   # 0.05 → очень грубо, 1 → почти без потерь
    fy, fx = np.mgrid[0:8, 0:8]
    band = np.where(fy + fx <= 2, v["low"], np.where(fy + fx <= 7, v["mid"], v["high"])).astype(np.float32)
    fsc = band * v["all"] * (v["xf"] * (fx > 0) + v["yf"] * (fy > 0) + (fx == 0) * (fy == 0)) / \
        np.maximum(1, (fx > 0).astype(np.float32) + (fy > 0))
    rng = _frame_rng(v["rseed"] * 1000 + seed, frame // max(1, int(v["jitter"])))
    chans = []
    for plane, tab, aff in ((Y, _JQ_Y, v["luma"]), (Cb, _JQ_C, v["chroma"]), (Cr, _JQ_C, v["chroma"])):
        P = np.pad(plane, ((0, H8 - sh), (0, W8 - sw)), mode="edge")
        B = P.reshape(H8 // 8, 8, W8 // 8, 8).transpose(0, 2, 1, 3)
        D = _DCT @ B @ _DCT.T
        Q = np.maximum(1.0, tab * qs * np.maximum(fsc, 1e-3))
        Dq = np.round(D / Q) * Q
        if v["errrate"] > 0:               # ошибки: в части блоков коэффициенты «битые»
            nb = B.shape[0] * B.shape[1]
            m = rng.random((B.shape[0], B.shape[1])) < v["errrate"] * 0.1
            if v["errcoh"] > 0:            # когерентность: ошибки тянутся по строке, как при сбое потока
                m |= np.roll(m, 1, axis=1) & (rng.random(m.shape) < v["errcoh"] * 0.8)
            if m.any() and rng.random() < max(0.05, v["errdens"]):
                noise = rng.normal(0, 60 * v["erramp"], (int(m.sum()), 8, 8)).astype(np.float32)
                Dq[m] += noise * (np.arange(64).reshape(8, 8) < 10)
            del nb
        R = _DCT.T @ Dq @ _DCT
        R = R.transpose(0, 2, 1, 3).reshape(H8, W8)[:sh, :sw]
        chans.append(plane + (R - plane) * aff)
    Y2, Cb2, Cr2 = chans
    r2 = Y2 + 128 + 1.402 * Cr2
    g2 = Y2 + 128 - 0.344136 * Cb2 - 0.714136 * Cr2
    b2 = Y2 + 128 + 1.772 * Cb2
    out = np.stack([b2, g2, r2], axis=2)
    out = out * v["lights"] + v["darks"] * 255.0
    if abs(v["sat"] - 1.0) > 1e-3:
        gg = (out[..., 0] * 0.114 + out[..., 1] * 0.587 + out[..., 2] * 0.299)[..., None]
        out = gg + (out - gg) * v["sat"]
    out = np.clip(out, 0, 255) * (a / 255.0)
    res_arr = np.concatenate([out, small[..., 3:4]], axis=2).astype(np.float32)
    if (sw, sh) != (w, h):     # крупные блоки при Res Factor > 1 — увеличиваем без сглаживания
        ys = (np.arange(h) * sh // h).clip(0, sh - 1)
        xs = (np.arange(w) * sw // w).clip(0, sw - 1)
        res_arr = res_arr[ys][:, xs]
    return res_arr


# ============================================================================ S_TVDamage
def _f_tvdamage(arr, v, scale=1.0, frame=0, fps=30.0, seed=1, **_):
    """S_TVDamage: плохой приём аналогового ТВ / VHS — статика, наводки (волнистые строки), двоение,
    срыв строчной и кадровой синхронизации, бегущие полосы, цветные полосы, «перемотка», выпадения ленты."""
    h, w = arr.shape[:2]
    m = v["master"]
    rng = _frame_rng(v["seed"] * 1000 + seed, frame)
    t = frame / max(fps, 1.0)
    ys = np.arange(h, dtype=np.float32)
    xs_, ys_ = _grid(h, w)
    # горизонтальное смещение строк: наводки + срыв строчной синхронизации + перемотка
    off = np.zeros(h, np.float32)
    if v["interf"] > 0:
        off += np.sin(ys / (h / 6.0) * 2 * math.pi + t * 7.0) * v["interf"] * 6 * scale
        off += rng.normal(0, 1, h).astype(np.float32) * v["interf"] * 1.5 * scale
    if v["hhold"] > 0 and rng.random() < v["hhold"] * m * 0.6:
        y0 = int(rng.integers(0, h))
        span = int(rng.integers(h // 20, h // 3))
        tear = np.clip((ys - y0) / max(1, span), 0, 1) * (ys >= y0)
        off += tear * rng.uniform(-1, 1) * w * 0.15 * v["hhold"]
    if v["ffwd"] > 0:
        band = (ys / h + t * 0.7) % 1.0
        off += (band < 0.12 * v["ffwd"]) * rng.normal(0, 1, h).astype(np.float32) * 40 * scale * v["ffwd"]
    off *= m
    vroll = 0.0
    if v["vhold"] > 0:          # кадр «уезжает» по вертикали
        vroll = ((t * v["vhold"] * 0.35 * m) % 1.0) * h if rng.random() < 0.97 else 0.0
    sx = xs_ + off[:, None]
    sy = ys_ + vroll
    out = _remap(arr, np.mod(sx, w), np.mod(sy, h), nearest=True)
    if vroll > 0:               # чёрная полоса гашения между кадрами
        bar = ((ys + vroll) % h) > h - 0.04 * h
        out[bar] *= 0.05
    if v["ghost"] > 0:
        gsh = int(w * 0.03 * scale / max(scale, 1e-6))
        out[..., :3] += np.roll(out[..., :3], gsh, axis=1) * v["ghost"] * 0.35 * m
    if v["bars"] > 0:           # бегущие по кадру светлые полосы
        b = 0.5 + 0.5 * np.sin((ys / h - t * 0.25) * 2 * math.pi * 1.5)
        out[..., :3] *= (1 + (b ** 6 * v["bars"] * m))[:, None, None]
    if v["stripes"] > 0:        # цветные полосы — сдвиг цветности по строкам
        ph = (ys / h * 9 + t * 1.3) * 2 * math.pi
        amp = v["stripes"] * m * 40
        out[..., 2] += (np.sin(ph) * amp)[:, None]
        out[..., 0] += (np.sin(ph + 2.1) * amp)[:, None]
    if v["static"] > 0:         # «снег»
        dens = v["sdens"]
        nz = rng.random((h, w)).astype(np.float32)
        mask = (rng.random((h, w)) < dens).astype(np.float32)
        out[..., :3] = out[..., :3] * (1 - v["static"] * m * 0.6 * mask[..., None]) + \
            (nz * 255 * v["static"] * m * 0.6 * mask)[..., None]
    if v["drop"] > 0:           # выпадения ленты — короткие белые штрихи
        for _ in range(int(rng.poisson(6 * v["drop"] * m))):
            y = int(rng.integers(0, h))
            x0 = int(rng.integers(0, w))
            ln = int(rng.integers(w // 30, w // 5))
            out[y:y + max(1, int(2 * scale)), x0:x0 + ln, :3] = 255 * min(1.0, v["drop"])
    if v["vignette"] > 0:
        yy = (ys / h - 0.5)[:, None]
        xx = (np.arange(w, dtype=np.float32) / w - 0.5)[None, :]
        out[..., :3] *= (1.0 - v["vignette"] * np.clip((xx * xx + yy * yy) * 2.6, 0, 1))[..., None]
    if v["mode"] == 1:
        g = (out[..., 0] * 0.114 + out[..., 1] * 0.587 + out[..., 2] * 0.299)[..., None]
        out[..., :3] = g
    out[..., :3] = np.clip(out[..., :3], 0, None)
    out[..., :3] = np.minimum(out[..., :3], out[..., 3:4])
    return out


def _toggle(key, ru, en):
    return Param(key, ru, en, [1.0], kind="checkbox")


DD_D = ("Повреждения — детали", "Damage Details")
FREQ = ("Частоты", "Frequency")
ERR = ("Ошибки", "Errors")
STATIC = ("Статика", "Static")
NEW_EFFECTS += [
    EffectDef("digitaldamage", "Цифровые повреждения (DigitalDamage)", "Digital Damage", [
        Param("intensity", "Интенсивность", "Intensity", [1.0], 0.01, "", 0, 8),
        Param("tint", "Частота смены", "Time Intensity", [1.0], 0.01, "", 0, 10),
        Param("size", "Размер повреждений", "Damage Size", [1.0], 0.01, "", 0.05, 20),
        Param("relx", "Отн. размер по X", "Damage Size Rel X", [1.0], 0.01, "", 0.05, 20),
        _toggle("freeze", "Заморозка", "Freeze"), _toggle("shift", "Сдвиг", "Shift"),
        _toggle("brights", "Шум в светах", "Brights Noise"), _toggle("pixelate", "Пикселизация", "Pixelate"),
        _toggle("blocknoise", "Шумовые блоки", "Block Noise"), _toggle("invert", "Инверсия", "Invert"),
        _toggle("flow", "Течение (датамош)", "Flow"),
        Param("seed", "Случайность", "Seed", [1.23], 0.01, "", 0, 1000),
    ], opts={"seed": 1}, category="sapphire"),
    EffectDef("filmdamage", "Повреждения плёнки (FilmDamage)", "Film Damage", [
        Param("grain", "Зерно", "Grain Amp", [0.1], 0.005, "", 0, 10),
        Param("stains", "Плотность пятен", "Stain Density", [0.2], 0.01, "", 0, 20),
        Param("dust", "Плотность пыли", "Dust Density", [30.0], 0.1, "", 0, 1000),
        Param("hairs", "Волоски", "Hairs", [2.0], 0.05, "", 0, 50),
        Param("scratches", "Царапины", "Scratches", [5.0], 0.05, "", 0, 100),
        Param("shake", "Тряска кадра", "Shake Amplitude", [0.0], 0.005, "", 0, 10),
        Param("vignette", "Виньетка", "Vignette Darkness", [0.1], 0.005, "", 0, 1),
        Param("flicker", "Мерцание", "Flicker", [0.2], 0.005, "", 0, 10),
        Param("defocus", "Расфокус", "Defocus", [0.0], 0.005, "", 0, 10),
        Param("seed", "Случайность", "Seed", [0.123], 0.001, "", 0, 1000),
        Param("flip", "Отразить штампы по вертикали", "Flip Stamps Vertically", [0.0], kind="checkbox"),
    ], opts={"seed": 1}, category="sapphire"),
    EffectDef("jpegdamage", "Артефакты сжатия (JpegDamage)", "Jpeg Damage", [
        Param("quality", "Качество", "Quality", [0.056], 0.001, "", 0.001, 1),
        Param("res", "Укрупнение блоков", "Res Factor", [1.0], 0.05, "", 1, 32),
        Param("resx", "Отн. укрупнение по X", "Res Rel X", [1.0], 0.01, "", 0.1, 10),
        Param("all", "Все частоты", "All Freq Scale", [1.0], 0.01, "", 0.01, 20, group=FREQ),
        Param("xf", "Частоты по X", "X Freq Scale", [1.0], 0.01, "", 0.01, 20, group=FREQ),
        Param("yf", "Частоты по Y", "Y Freq Scale", [1.0], 0.01, "", 0.01, 20, group=FREQ),
        Param("low", "Низкие частоты", "Low Freq Scale", [1.0], 0.01, "", 0.01, 20, group=FREQ),
        Param("mid", "Средние частоты", "Mid Freq Scale", [1.0], 0.01, "", 0.01, 20, group=FREQ),
        Param("high", "Высокие частоты", "High Freq Scale", [1.0], 0.01, "", 0.01, 20, group=FREQ),
        Param("luma", "Влияет на яркость", "Affect Luma", [1.0], 0.01, "", 0, 1, group=FREQ),
        Param("chroma", "Влияет на цвет", "Affect Chroma", [0.5], 0.01, "", 0, 1, group=FREQ),
        Param("errrate", "Частота ошибок", "Error Rate", [0.0], 0.005, "", 0, 10, group=ERR),
        Param("errdens", "Плотность битых блоков", "Err Block Density", [0.75], 0.01, "", 0, 1, group=ERR),
        Param("erramp", "Сила ошибок", "Error Amp", [1.0], 0.01, "", 0, 10, group=ERR),
        Param("errcoh", "Связность ошибок", "Error Coherence", [1.0], 0.01, "", 0, 1, group=ERR),
        Param("jitter", "Держать ошибки (кадры)", "Jitter Frames", [1.0], 0.05, "", 1, 100, group=ERR),
        Param("rseed", "Случайность", "Rand Seed", [0.123], 0.001, "", 0, 1000, group=ERR),
        Param("lights", "Масштаб светов", "Scale Lights", [1.0], 0.01, "", 0, 4),
        Param("darks", "Сдвиг теней", "Offset Darks", [0.0], 0.005, "", -1, 1),
        Param("sat", "Насыщенность", "Saturation", [1.0], 0.01, "", 0, 4),
    ], opts={"seed": 1}, category="sapphire"),
    EffectDef("tvdamage", "Помехи ТВ (TVDamage)", "TV Damage", [
        Param("mode", "Эффект", "Effect", [0.0], kind="choice",
              choices=[("Цветной ТВ", "TVDamage Color"), ("Чёрно-белый ТВ", "TVDamage BW")]),
        Param("master", "Качество приёма (общая сила)", "Reception Master", [0.42], 0.005, "", 0, 4),
        Param("interf", "Наводки", "Interference Amp", [0.6], 0.005, "", 0, 10),
        Param("ghost", "Двоение", "Ghost Amp", [0.6], 0.005, "", 0, 4),
        Param("hhold", "Срыв строк", "Horizontal Hold", [0.5], 0.005, "", 0, 4),
        Param("vhold", "Срыв кадра", "Vertical Hold", [0.8], 0.005, "", 0, 4),
        Param("bars", "Яркость полос", "Bars Brightness", [0.3], 0.005, "", 0, 4),
        Param("stripes", "Цветные полосы", "Color Stripes Amplitude", [0.2], 0.005, "", 0, 4),
        Param("ffwd", "Перемотка", "Fast Forward Amount", [0.0], 0.005, "", 0, 4),
        Param("drop", "Выпадения ленты", "Tape Dropout Brightness", [0.0], 0.005, "", 0, 4),
        Param("vignette", "Виньетка", "Vignette Darkness", [0.0], 0.005, "", 0, 1),
        Param("static", "Сила статики", "Static Amplitude", [0.8], 0.005, "", 0, 4, group=STATIC),
        Param("sdens", "Плотность статики", "Static Density", [0.7], 0.005, "", 0, 1, group=STATIC),
        Param("seed", "Случайность", "Seed", [0.123], 0.001, "", 0, 1000),
    ], opts={"seed": 1}, category="sapphire"),
]


# ============================================================================ атмосфера (общая для S_Glow, S_EdgeRays, S_LensFlare)
def atmosphere(h, w, amp, freq, detail, seed, speed, t):
    """Множитель «пыльного воздуха» (H, W, 1): 1 ± amp·шум. Шум плавно меняется во времени (Speed)."""
    if amp <= 0:
        return None
    ph = t * speed
    k0 = math.floor(ph)
    f = ph - k0
    f = f * f * (3 - 2 * f)
    cells = max(2, int(3 * freq))
    octaves = 1 + int(round(max(0.0, min(1.0, detail)) * 4))

    def field(k):
        rng = np.random.default_rng([int(seed * 1000) % 2147483647, int(k) & 0x7FFFFFFF])
        return _fbm2(256, cells, octaves, rng)
    nz = field(k0) * (1 - f) + field(k0 + 1) * f
    img = to_image(np.repeat(nz[..., None] * 255.0, 4, axis=2).astype(np.float32))
    up = to_array(img.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation))[..., :1] / 255.0
    return np.clip(1.0 + (up - 0.5) * 2.0 * amp, 0, None)


COMBINE = [("Умножение", "Mult"), ("Сложение", "Add"), ("Экран", "Screen"), ("Разница", "Difference"),
           ("Перекрытие", "Overlay")]


def combine(src_rgb, glow_rgb, mode):
    """Режимы Combine у Sapphire: 0 Mult, 1 Add, 2 Screen, 3 Difference, 4 Overlay (значения 0..255)."""
    a, b = src_rgb / 255.0, glow_rgb / 255.0
    if mode == 0:
        r = a * (1 + b)
    elif mode == 1:
        r = a + b
    elif mode == 3:
        r = np.abs(a - b)
    elif mode == 4:
        r = np.where(a < 0.5, 2 * a * (0.5 + b * 0.5), 1 - 2 * (1 - a) * (1 - (0.5 + b * 0.5)))
    else:
        r = 1 - (1 - np.clip(a, 0, 1)) * (1 - np.clip(b, 0, None))
    return r * 255.0


def _threshold_src(arr, thresh, thcolor, fromalpha):
    """Что светится (общая логика Sapphire): яркость выше порога; Threshold Add Color повышает порог
    для этого цвета; Glow From Alpha — светиться по альфе."""
    a = arr[..., 3:4] / 255.0
    rgb = arr[..., :3] / np.maximum(a, 1e-3) / 255.0
    lum = rgb[..., 0] * 0.114 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.299
    tc = np.array([thcolor[2], thcolor[1], thcolor[0]], np.float32) / 255.0
    if tc.any():
        lum = lum - (rgb * tc).sum(axis=2) / max(1e-3, tc.sum())
    lum = lum + (a[..., 0] - lum) * fromalpha
    m = np.clip((lum - thresh) / max(1e-3, 1.0 - thresh), 0, 1)[..., None]
    return arr * m


# ============================================================================ S_Glow
_GLOW_UNIT = 0.06     # Glow Width 1.0 ≈ 6% ширины кадра (у Boris FX единица не документирована — подобрано по виду)
_GLOW_TAPS = ((0.06, 0.30), (0.15, 0.25), (0.35, 0.20), (0.7, 0.15), (1.4, 0.10))   # (доля ширины, вес)


def glow_image(src, width_px, wx, wy, rels):
    """Свечение с длинным «хвостом»: сумма гауссов разных радиусов (как у оптического ореола),
    у каждого канала свой масштаб ширины (Width Red/Green/Blue)."""
    h, w = src.shape[:2]
    d = 1   # мягкий ореол — считаем в уменьшенном кадре (самый узкий слой ≥ ~3 px), потом увеличиваем
    while d < 8 and width_px * min(rels or (1,)) * _GLOW_TAPS[0][0] * 2 / (d * 2) >= 1.5 and w / (d * 2) >= 200:
        d *= 2
    if d > 1:
        from .effects import _downsample, _resize
        return _resize(glow_image(_downsample(src, d), width_px / d, wx, wy, rels), w, h)
    out = np.zeros_like(src)
    groups = {}
    for ch, rel in zip((2, 1, 0), rels):
        groups.setdefault(round(rel, 3), []).append(ch)
    for rel, chans in groups.items():
        acc = np.zeros_like(src)
        for frac, wgt in _GLOW_TAPS:
            acc += _aniso_blur(src, max(0.3, width_px * rel * frac * 2), wx, wy) * wgt
        for ch in chans:
            out[..., ch] = acc[..., ch]
        out[..., 3] = np.maximum(out[..., 3], acc[..., 3])
    return out


def _f_sglow(arr, v, scale=1.0, frame=0, fps=30.0, seed=1, **_):
    h, w = arr.shape[:2]
    src = _threshold_src(arr, v["thresh"], v["thcolor"], v["fromalpha"])
    am = atmosphere(h, w, v["atmo"], v["atmofreq"], v["atmodet"], v["atmoseed"], v["atmospeed"], frame / max(fps, 1))
    if am is not None:
        src = src * am
    if v["show"] == 1:
        return src
    width = v["width"] * _GLOW_UNIT * w
    g = glow_image(src, width, max(0.0, v["wx"]), max(0.0, v["wy"]), (v["wr"], v["wg"], v["wb"]))
    col = np.array([v["color"][2], v["color"][1], v["color"][0]], np.float32) / 255.0
    # Glow Width 0 всё равно подсвечивает яркие места (как в документации): добавляем сам порог
    grgb = (g[..., :3] + src[..., :3] * 0.25) * col * v["bright"]
    a = arr[..., 3:4] / 255.0
    base = arr[..., :3] * v["srcop"]
    if v["lightbg"] > 0:   # свет «падает» на фон: умножаем фон на свечение
        lit = base * (1.0 + grgb / 255.0 * 2.0)
        base = base + (lit - base) * v["lightbg"]
    mixed = combine(base, grgb, int(v["combine"]))
    under = v["under"] * a
    rgb = mixed * (1 - under) + (base * v["bgbright"] + grgb * (1 - a)) * under
    out = arr.copy()
    out[..., 3] = np.maximum(arr[..., 3] * v["srcop"], np.clip(grgb.max(axis=2), 0, 255) * v["affalpha"])
    out[..., :3] = np.minimum(np.clip(rgb, 0, 255), out[..., 3:4])
    return out


ATMO2 = ("Атмосфера", "Atmosphere")


def _atmo_params(amp=0.0):
    return [Param("atmo", "Сила атмосферы", "Atmosphere Amp", [amp], 0.01, "", 0, 10, group=ATMO2),
            Param("atmofreq", "Частота атмосферы", "Atmosphere Freq", [1.0], 0.01, "", 0.1, 20, group=ATMO2),
            Param("atmodet", "Детали атмосферы", "Atmosphere Detail", [0.6], 0.01, "", 0, 1, group=ATMO2),
            Param("atmoseed", "Случайность атмосферы", "Atmosphere Seed", [0.123], 0.001, "", 0, 1000, group=ATMO2),
            Param("atmospeed", "Скорость атмосферы", "Atmosphere Speed", [1.0], 0.01, "", -50, 50, group=ATMO2)]


NEW_EFFECTS.append(EffectDef("sglow", "Свечение (S_Glow)", "S_Glow", [
    Param("bright", "Яркость", "Brightness", [2.0], 0.01, "", 0, 100),
    Param("color", "Цвет", "Color", [255.0, 255.0, 255.0], kind="color"),
    Param("thresh", "Порог", "Threshold", [0.5], 0.005, "", 0, 0.999),
    Param("thcolor", "Цвет поверх порога", "Threshold Add Color", [0.0, 0.0, 0.0], kind="color"),
    Param("width", "Ширина свечения", "Glow Width", [0.4], 0.005, "", 0, 20),
    Param("wx", "Ширина по X", "Width X", [1.0], 0.01, "", 0, 10),
    Param("wy", "Ширина по Y", "Width Y", [1.0], 0.01, "", 0, 10),
    Param("wr", "Ширина красного", "Width Red", [1.0], 0.01, "", 0, 10),
    Param("wg", "Ширина зелёного", "Width Green", [1.2], 0.01, "", 0, 10),
    Param("wb", "Ширина синего", "Width Blue", [1.4], 0.01, "", 0, 10),
    Param("show", "Показать", "Show", [0.0], kind="choice", choices=[("Результат", "Result"), ("Порог", "Threshold")]),
    Param("combine", "Наложение", "Combine", [2.0], kind="choice", choices=COMBINE),
    Param("affalpha", "Влияет на альфу", "Affect Alpha", [1.0], 0.01, "", 0, 4),
    Param("fromalpha", "Свечение из альфы", "Glow From Alpha", [0.0], 0.01, "", 0, 1),
    Param("under", "Свечение под исходником", "Glow Under Source", [0.0], 0.01, "", 0, 1),
    Param("lightbg", "Освещать фон", "Light Background", [0.0], 0.01, "", 0, 1),
    Param("srcop", "Непрозрачность исходника", "Source Opacity", [1.0], 0.01, "", 0, 1),
    Param("bgbright", "Яркость фона", "Bg Brightness", [1.0], 0.01, "", 0, 4),
    *_atmo_params(),
], category="sapphire"))


# ============================================================================ S_Blur
def _box_blur(arr, rx, ry, passes):
    from .effects import _box
    out = arr
    for _ in range(passes):
        out = _box(_box(out, int(round(rx)), 1), int(round(ry)), 0)
    return out


def _f_sblur(arr, v, scale=1.0, **_):
    """S_Blur: Blur Amount (доля ширины кадра) × Blur Rel X/Y; фильтр Box / Triangle (2 box) / Gauss;
    Mono — сначала в ч/б; Brightness, Scale Opacity, Offset Darks, Mix With Source."""
    h, w = arr.shape[:2]
    rad = v["amount"] * _GLOW_UNIT * w
    rx, ry = rad * max(0.0, v["relx"]), rad * max(0.0, v["rely"])
    src = arr
    if v["mode"] == 1:
        g = (arr[..., 0] * 0.114 + arr[..., 1] * 0.587 + arr[..., 2] * 0.299)[..., None]
        src = np.concatenate([g, g, g, arr[..., 3:4]], axis=2)
    if max(rx, ry) < 0.3:
        out = src.copy()
    elif v["filter"] == 2:
        m = max(rx, ry)
        out = _aniso_blur(src, m, rx / m, ry / m)
    else:
        n = 1 if v["filter"] == 0 else 2
        out = _box_blur(src, rx / math.sqrt(n), ry / math.sqrt(n), n)
    out[..., :3] = out[..., :3] * v["bright"] + v["darks"] * 255.0 * (out[..., 3:4] / 255.0)
    out[..., 3] = np.clip(out[..., 3] * v["opscale"], 0, 255)
    mix = v["srcmix"]
    if mix > 0:
        out = out + (arr - out) * mix
    out[..., :3] = np.minimum(np.clip(out[..., :3], 0, None), out[..., 3:4])
    return out


NEW_EFFECTS.append(EffectDef("sblur", "Размытие (S_Blur)", "S_Blur", [
    Param("mode", "Режим", "Mode", [0.0], kind="choice", choices=[("Цветное", "Blur Color"), ("Ч/б", "Blur Mono")]),
    Param("amount", "Сила размытия", "Blur Amount", [0.4], 0.005, "", 0, 50),
    Param("relx", "Отн. по X", "Blur Rel X", [1.0], 0.01, "", 0, 10),
    Param("rely", "Отн. по Y", "Blur Rel Y", [1.0], 0.01, "", 0, 10),
    Param("bright", "Яркость", "Brightness", [1.0], 0.01, "", 0, 10),
    Param("opscale", "Масштаб непрозрачности", "Scale Opacity", [1.0], 0.01, "", 0, 10),
    Param("darks", "Сдвиг теней", "Offset Darks", [0.0], 0.005, "", -1, 1),
    Param("srcmix", "Смешать с исходником", "Mix With Source", [0.0], 0.01, "", 0, 1),
    Param("filter", "Фильтр", "Filter", [2.0], kind="choice",
          choices=[("Прямоугольный", "Box"), ("Треугольный", "Triangle"), ("Гаусс", "Gauss")]),
], category="sapphire"))


# ============================================================================ общие: поворот тона
def hue_rotate(rgb, revs):
    """Сдвиг тона на revs оборотов (поворот вокруг серой оси), rgb (..., 3) в порядке B G R."""
    if abs(revs) < 1e-4:
        return rgb
    th = revs * 2 * math.pi
    c, s_ = math.cos(th), math.sin(th)
    k = (1 - c) / 3.0
    sq = math.sqrt(1 / 3) * s_
    M = np.array([[c + k, k - sq, k + sq], [k + sq, c + k, k - sq], [k - sq, k + sq, c + k]], np.float32)
    return np.maximum(rgb @ M.T, 0)


def _noise_tex(h, w, freq, seed, t_shift=(0.0, 0.0), speed=(0.0, 0.0), t=0.0, octaves=3):
    """Текстура шума 0..1 (H, W): плитка 512² на ширину кадра, freq — ячеек на ширину (2…128);
    Shift/Speed сдвигают плитку (в долях ширины кадра, Speed — в секунду). Края плитки повторяются."""
    n = 512
    cells = int(max(2, min(128, round(freq))))
    rng = np.random.default_rng(int(abs(seed) * 1000) % 2147483647)
    tex = _value_noise(n, cells, rng) * 0.6 + _fbm2(n, min(n // 4, cells * 2), max(1, octaves - 1), rng) * 0.4
    tex = (tex - tex.min()) / max(1e-6, float(tex.max() - tex.min()))
    xs, ys = _grid(h, w)
    u = (xs / w + t_shift[0] + speed[0] * t) * n
    v_ = (ys / w + t_shift[1] + speed[1] * t) * n
    x0, y0 = np.floor(u).astype(np.int32), np.floor(v_).astype(np.int32)
    fx, fy = u - x0, v_ - y0
    x0, y0 = x0 % n, y0 % n
    x1, y1 = (x0 + 1) % n, (y0 + 1) % n
    top = tex[y0, x0] * (1 - fx) + tex[y0, x1] * fx       # билинейно, текстура повторяется по кругу
    bot = tex[y1, x0] * (1 - fx) + tex[y1, x1] * fx
    return top * (1 - fy) + bot * fy


# ============================================================================ S_Distort
def _f_sdistort(arr, v, scale=1.0, frame=0, fps=30.0, seed=1, **_):
    """S_Distort: картинка смещается по градиенту «линзы» — как сквозь стекло произвольной формы.
    Линза: яркость самого слоя или встроенная текстура (входа «Lens» с другого слоя у нас пока нет)."""
    from .effects import blur
    h, w = arr.shape[:2]
    if v["lens"] == 0:
        lens = _lum01(arr)
    else:
        lens = _noise_tex(h, w, v["lfreq"] * 8, v["lseed"], speed=(v["lspeed"], v["lspeed"] * 0.7),
                          t=frame / max(fps, 1.0))
    bl = max(0.5, v["blurlens"] * _GLOW_UNIT * w * 0.5)
    lens = blur(np.repeat(lens[..., None] * 255.0, 4, axis=2).astype(np.float32), bl)[..., 0] / 255.0
    gy, gx = np.gradient(lens)
    a = math.radians(v["rot"])
    ca, sa = math.cos(a), math.sin(a)
    gx, gy = ca * gx - sa * gy, sa * gx + ca * gy
    amt = v["amount"] / (100.0 if v["fine"] else 1.0)
    # характерный размер «линзы»: ширина размытого края или половина ячейки текстуры;
    # перепад яркости линзы на этом размере → сдвиг ~3% ширины кадра при Amount 1
    size = bl if v["lens"] == 0 else max(bl, w / max(0.1, v["lfreq"] * 8) / 2)
    k = amt * size * 0.03 * w
    rx, ry = (v["relx"], v["rely"]) if amt > 0 else (1.0, 1.0)
    xs, ys = _grid(h, w)
    return warp_sample(arr, xs + gx * k * rx, ys + gy * k * ry, v["wrapx"], v["wrapy"], bool(v["filter"]))


NEW_EFFECTS.append(EffectDef("sdistort", "Стеклянное искажение (S_Distort)", "S_Distort", [
    Param("lens", "Линза", "Lens", [1.0], kind="choice",
          choices=[("Яркость самого слоя", "Source Luma"), ("Встроенная текстура", "Built-in Texture")]),
    Param("lfreq", "Частота текстуры", "Lens Texture Freq", [1.0], 0.01, "", 0.05, 50),
    Param("lseed", "Случайность текстуры", "Lens Texture Seed", [0.123], 0.001, "", 0, 1000),
    Param("lspeed", "Скорость текстуры", "Lens Texture Speed", [0.0], 0.01, "", -100, 100),
    Param("amount", "Сила", "Amount", [1.0], 0.01, "", -50, 50),
    Param("fine", "Тонко (÷100)", "Fine", [0.0], kind="checkbox"),
    Param("blurlens", "Размытие линзы", "Blur Lens", [0.4], 0.005, "", 0, 20),
    Param("rot", "Поворот направления", "Rotate Warp Dir", [0.0], 0.5, "°", kind="angle"),
    Param("relx", "Отн. сила по X", "Amount Rel X", [1.0], 0.01, "", 0, 10),
    Param("rely", "Отн. сила по Y", "Amount Rel Y", [1.0], 0.01, "", 0, 10),
    Param("wrapx", "Край по X", "Wrap X", [2.0], kind="choice", choices=WRAPS),
    Param("wrapy", "Край по Y", "Wrap Y", [2.0], kind="choice", choices=WRAPS),
    Param("filter", "Сглаживание (фильтр)", "Filter", [1.0], kind="checkbox"),
], category="sapphire"))


# ============================================================================ S_EdgeRays
def _f_edgerays(arr, v, scale=1.0, frame=0, fps=30.0, seed=1, **_):
    """S_EdgeRays: лучи не от ярких мест, а от КРАЁВ (контуров) изображения — цвет берётся с краёв."""
    from .effects import _sobel, blur, _downsample, _resize
    h, w = arr.shape[:2]
    t = frame / max(fps, 1.0)
    cx, cy = v["center"][0] * scale, v["center"][1] * scale
    a = arr[..., 3] / 255.0
    lum = _lum01(arr)
    lum = lum + (a - lum) * v["fromalpha"]
    gx, gy = _sobel(lum)
    edge = np.sqrt(gx * gx + gy * gy)
    th = max(0.3, v["ethick"] * 0.05 * w)        # толщина контура (Edge Thickness — доля ~кадра)
    em = blur(np.repeat(edge[..., None] * 255.0, 4, axis=2).astype(np.float32), th * 0.5)[..., 0] / 255.0
    # Собель на перепаде 0→1 даёт ~4; после размытия по толщине — меньше. Нормируем так, чтобы
    # резкий контур был ~1, а слабая текстура почти не давала лучей
    em = np.clip((em * 0.5 * (1.0 + 2.0 / th)) ** 1.5 * v["ebright"], 0, 1)[..., None]
    src = arr * em
    if v["shimmer"] > 0:
        nz = _noise_tex(h, w, v["shimfreq"], v["shimseed"], (v["shimshx"], v["shimshy"]),
                        (v["shimspx"], v["shimspy"]), t)
        src = src * (1.0 - v["shimmer"] + v["shimmer"] * nz[..., None] * 2.0)
    if v["show"] == 1:
        return src
    l = v["length"]
    rev = 1.0 if l < 0 else v["reverse"]
    l = abs(l)
    L = min(50.0, l / max(1e-3, 1.0 - l)) if l < 0.999 else 50.0     # 1.0 — лучи «до бесконечности»
    d = (1, 2, 4)[int(v["res"])]
    work, wcx, wcy = src, cx, cy
    if d > 1:
        work, wcx, wcy = _downsample(src, d), cx / d, cy / d
    lens = (L * v["lr"], L * v["lg"], L * v["lb"])
    if max(lens) - min(lens) < 1e-3:
        rays = radial_rays(work, wcx, wcy, lens[0], rev)
    else:
        rays = np.empty_like(work)
        for ch, ln in ((2, lens[0]), (1, lens[1]), (0, lens[2])):
            rays[..., ch] = radial_rays(np.repeat(work[..., ch:ch + 1], 4, axis=2), wcx, wcy, ln, rev)[..., 0]
        rays[..., 3] = rays[..., :3].max(axis=2)
    if d > 1:
        rays = _resize(rays, w, h)
    rays = _aniso_blur(rays, v["blur"] * scale, v["blurx"], v["blury"])
    am = atmosphere(h, w, v["atmo"], v["atmofreq"], v["atmodet"], 0.123, v["atmospeed"], t)
    if am is not None:
        rays = rays * am
    if v["bias"] != 0:
        xs, ys = _grid(h, w)
        r = np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2) / max(w, h)
        rays[..., :3] *= np.clip(1.0 + v["bias"] * (r * 3.0 - 0.5), 0, None)[..., None]
    col = np.array([v["color"][2], v["color"][1], v["color"][0]], np.float32) / 255.0
    rgb = rays[..., :3] * col * v["bright"]
    if v["show"] == 2:
        out = np.zeros_like(arr)
        out[..., :3] = np.clip(rgb, 0, 255)
        out[..., 3] = out[..., :3].max(axis=2)
        return out
    base = arr[..., :3] * v["srcop"]
    add = rgb * (1 - v["under"] * a[..., None])
    if v["dark"]:     # тёмные лучи: тёмный цвет лучей затемняет картинку
        dark = (1.0 - col) * rays[..., :3] / 255.0 * v["bright"]
        base = base * np.clip(1.0 - dark, 0, 1)
    out = arr.copy()
    out[..., 3] = np.maximum(arr[..., 3] * v["srcop"], np.clip(rgb.max(axis=2), 0, 255) * v["affalpha"])
    out[..., :3] = np.minimum(np.clip(base + add, 0, 255), out[..., 3:4])
    return out


SHIM2 = ("Мерцание", "Shimmer")
NEW_EFFECTS.append(EffectDef("edgerays", "Лучи от контуров (S_EdgeRays)", "S_EdgeRays", [
    Param("center", "Центр", "Center", [0.5, 0.5], 1.0, kind="point"),
    Param("length", "Длина лучей", "Rays Length", [0.25], 0.005, "", -5, 3),
    Param("lr", "Длина красного", "Length Red", [1.0], 0.01, "", 0, 10),
    Param("lg", "Длина зелёного", "Length Green", [1.0], 0.01, "", 0, 10),
    Param("lb", "Длина синего", "Length Blue", [1.0], 0.01, "", 0, 10),
    Param("reverse", "Обратные лучи", "Reverse Rays", [0.0], 0.01, "", 0, 1),
    Param("bright", "Яркость лучей", "Rays Brightness", [2.0], 0.01, "", 0, 100),
    Param("blur", "Размытие лучей", "Blur Rays", [0.0], 0.1, " px", 0, 500),
    Param("blurx", "Размытие: отн. X", "Blur Rays Rel X", [1.0], 0.01, "", 0, 10),
    Param("blury", "Размытие: отн. Y", "Blur Rays Rel Y", [1.0], 0.01, "", 0, 10),
    Param("color", "Цвет лучей", "Rays Color", [255.0, 255.0, 255.0], kind="color"),
    Param("dark", "Тёмные лучи", "Enable Dark Rays", [0.0], kind="checkbox"),
    Param("bias", "Яркость к концам", "Bias Outer Bright", [0.0], 0.01, "", 0, 1),
    Param("res", "Разрешение лучей", "Rays Res", [0.0], kind="choice",
          choices=[("Полное", "Full"), ("Половина", "Half"), ("Четверть", "Quarter")]),
    Param("show", "Показать", "Show", [0.0], kind="choice",
          choices=[("Результат", "Result"), ("Контуры", "Edges"), ("Только лучи", "Rays")]),
    Param("ethick", "Толщина контуров", "Edge Thickness", [0.022], 0.001, "", 0, 2),
    Param("ebright", "Яркость контуров", "Edge Brightness", [1.0], 0.01, "", 0, 20),
    Param("shimmer", "Сила мерцания", "Shimmer Amp", [0.5], 0.01, "", 0, 4, group=SHIM2),
    Param("shimfreq", "Частота мерцания", "Shimmer Freq", [40.0], 0.1, "", 0.1, 400, group=SHIM2),
    Param("shimseed", "Случайность мерцания", "Shimmer Seed", [0.123], 0.001, "", 0, 1000, group=SHIM2),
    Param("shimshx", "Сдвиг мерцания X", "Shimmer Shift X", [0.0], 0.01, "", -1000, 1000, group=SHIM2),
    Param("shimshy", "Сдвиг мерцания Y", "Shimmer Shift Y", [0.0], 0.01, "", -1000, 1000, group=SHIM2),
    Param("shimspx", "Скорость мерцания X", "Shimmer Speed X", [0.0], 0.01, "", -1000, 1000, group=SHIM2),
    Param("shimspy", "Скорость мерцания Y", "Shimmer Speed Y", [0.0], 0.01, "", -1000, 1000, group=SHIM2),
    Param("atmo", "Сила атмосферы", "Atmosphere Amp", [0.0], 0.01, "", 0, 10, group=ATMO2),
    Param("atmofreq", "Частота атмосферы", "Atmosphere Freq", [1.0], 0.01, "", 0.1, 20, group=ATMO2),
    Param("atmodet", "Детали атмосферы", "Atmosphere Detail", [0.6], 0.01, "", 0, 1, group=ATMO2),
    Param("atmospeed", "Скорость атмосферы", "Atmosphere Speed", [1.0], 0.01, "", -50, 50, group=ATMO2),
    Param("affalpha", "Влияет на альфу", "Affect Alpha", [1.0], 0.01, "", 0, 4),
    Param("fromalpha", "Лучи из альфы", "Rays From Alpha", [0.0], 0.01, "", 0, 1),
    Param("under", "Лучи под исходником", "Rays Under Source", [0.0], 0.01, "", 0, 1),
    Param("srcop", "Непрозрачность исходника", "Source Opacity", [1.0], 0.01, "", 0, 1),
], category="sapphire"))


# ============================================================================ S_LensFlare
# элемент: (тип, позиция на линии 0=hotspot…1=pivot, размер (доля ширины кадра), цвет RGB 0..1, яркость, доп.)
# тип: glow — ореол, ring — кольцо-гало, ghost — «призрак» (многоугольник), disc — круглый призрак,
#      rays — звезда лучей, streak — анаморфная полоса
_LENSES = {
    0: ("Классика 50 мм", "Classic 50mm", [
        ("glow", 0.0, 0.35, (1.0, 0.85, 0.65), 0.9, {}), ("glow", 0.0, 0.08, (1, 1, 1), 1.0, {}),
        ("rays", 0.0, 0.30, (1.0, 0.9, 0.8), 0.6, {"n": 12, "thick": 0.012}),
        ("ring", 0.0, 0.42, (0.6, 0.8, 1.0), 0.18, {"band": 0.06}),
        ("ghost", 0.35, 0.05, (0.4, 1.0, 0.5), 0.25, {"sides": 6}), ("ghost", 0.55, 0.09, (0.5, 0.6, 1.0), 0.2, {"sides": 6}),
        ("disc", 0.8, 0.03, (1.0, 0.6, 0.3), 0.35, {}), ("ghost", 1.25, 0.14, (0.9, 0.5, 1.0), 0.15, {"sides": 6}),
        ("disc", 1.6, 0.06, (0.4, 0.9, 1.0), 0.2, {}), ("ring", 1.9, 0.12, (1.0, 0.7, 0.3), 0.18, {"band": 0.25})]),
    1: ("Анаморфная синяя", "Anamorphic Blue", [
        ("glow", 0.0, 0.25, (0.55, 0.75, 1.0), 1.0, {}), ("glow", 0.0, 0.05, (1, 1, 1), 1.0, {}),
        ("streak", 0.0, 1.6, (0.35, 0.6, 1.0), 0.9, {"relh": 0.012}),
        ("streak", 0.0, 0.7, (0.8, 0.9, 1.0), 0.6, {"relh": 0.006}),
        ("ghost", 0.5, 0.06, (0.3, 0.5, 1.0), 0.25, {"sides": 0, "relx": 0.5}),
        ("ghost", 1.2, 0.1, (0.4, 0.6, 1.0), 0.18, {"sides": 0, "relx": 0.5}),
        ("disc", 1.7, 0.04, (0.5, 0.8, 1.0), 0.25, {})]),
    2: ("Солнце", "Sun", [
        ("glow", 0.0, 0.6, (1.0, 0.75, 0.4), 0.8, {}), ("glow", 0.0, 0.12, (1.0, 0.95, 0.85), 1.0, {}),
        ("rays", 0.0, 0.55, (1.0, 0.85, 0.6), 0.45, {"n": 48, "thick": 0.006}),
        ("ring", 0.0, 0.7, (1.0, 0.6, 0.5), 0.1, {"band": 0.04}),
        ("ghost", 0.6, 0.12, (1.0, 0.7, 0.3), 0.12, {"sides": 7}), ("disc", 1.1, 0.05, (0.8, 1.0, 0.6), 0.2, {}),
        ("ghost", 1.5, 0.2, (0.9, 0.5, 0.3), 0.1, {"sides": 7})]),
    3: ("Прожектор", "Spotlight", [
        ("glow", 0.0, 0.18, (1, 1, 1), 1.0, {}), ("rays", 0.0, 0.5, (1, 1, 1), 0.8, {"n": 4, "thick": 0.02}),
        ("rays", 0.0, 0.25, (0.8, 0.9, 1.0), 0.5, {"n": 8, "thick": 0.008, "rot": 22.5}),
        ("ring", 0.0, 0.2, (0.8, 0.9, 1.0), 0.2, {"band": 0.1})]),
    4: ("Тёплая плёночная", "Warm Film", [
        ("glow", 0.0, 0.4, (1.0, 0.55, 0.25), 0.9, {}), ("glow", 0.0, 0.07, (1.0, 0.9, 0.7), 1.0, {}),
        ("streak", 0.0, 0.9, (1.0, 0.5, 0.2), 0.5, {"relh": 0.02}),
        ("disc", 0.3, 0.04, (1.0, 0.6, 0.2), 0.3, {}), ("ghost", 0.7, 0.08, (1.0, 0.4, 0.3), 0.2, {"sides": 5}),
        ("ring", 1.3, 0.16, (1.0, 0.75, 0.4), 0.15, {"band": 0.2}), ("disc", 1.8, 0.07, (0.9, 0.4, 0.6), 0.15, {})]),
}


def _qcol(rgb, a):
    return QColor.fromRgbF(min(1.0, max(0.0, rgb[0])), min(1.0, max(0.0, rgb[1])), min(1.0, max(0.0, rgb[2])),
                           min(1.0, max(0.0, a)))


def _draw_flare(w, h, hx, hy, px, py, v, scale, seed, t):
    from PySide6.QtGui import QBrush, QPolygonF, QRadialGradient, QLinearGradient
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setCompositionMode(QPainter.CompositionMode_Plus)
    p.setPen(Qt.NoPen)
    lens = _LENSES.get(int(v["lens"]), _LENSES[0])[2]
    sw = v["scale"] * w
    relh = max(0.01, v["relh"])
    for i, (kind, pos, size, col, br, ex) in enumerate(lens):
        hot = pos == 0.0
        x, y = hx + (px - hx) * pos, hy + (py - hy) * pos
        if hot:
            b = br * v["hbright"]
            c = [col[k] * v["hcolor"][2 - k] / 255.0 for k in range(3)]
            r = size * sw
        else:
            b = br * v["obright"]
            c = [col[k] * v["ocolor"][2 - k] / 255.0 for k in range(3)]
            r = size * sw * v["owidth"]
        if kind == "rays":
            b *= v["rbright"]
        if b <= 0 or r < 0.5:
            continue
        c = (c[2], c[1], c[0])     # → R G B для QColor
        p.save()
        p.translate(x, y)
        if kind in ("glow", "disc", "ring", "ghost"):
            p.scale(ex.get("relx", 1.0), relh)
            g = QRadialGradient(QPointF(0, 0), r)
            if kind == "glow":     # ореол: яркое ядро и длинный мягкий спад
                for st, k in ((0.0, 1.0), (0.08, 0.6), (0.25, 0.22), (0.5, 0.07), (1.0, 0.0)):
                    g.setColorAt(st, _qcol(c, b * k))
            elif kind == "ring":
                band = ex.get("band", 0.1)
                g.setColorAt(0.0, _qcol(c, 0))
                g.setColorAt(max(0.0, 1 - band * 2), _qcol(c, 0))
                g.setColorAt(1 - band, _qcol(c, b))
                g.setColorAt(1.0, _qcol(c, 0))
            else:                  # призраки: почти ровная заливка с чуть ярким краем
                g.setColorAt(0.0, _qcol(c, b * 0.5))
                g.setColorAt(0.8, _qcol(c, b * 0.65))
                g.setColorAt(0.93, _qcol(c, b))
                g.setColorAt(1.0, _qcol(c, 0))
            p.setBrush(QBrush(g))
            sides = ex.get("sides", 0) if kind == "ghost" else 0
            if sides >= 3:
                pts = [QPointF(math.cos(2 * math.pi * k / sides + 0.3) * r, math.sin(2 * math.pi * k / sides + 0.3) * r)
                       for k in range(sides)]
                p.drawPolygon(QPolygonF(pts))
            else:
                p.drawEllipse(QPointF(0, 0), r, r)
        elif kind == "streak":
            ln = r * v["rlength"]
            g = QLinearGradient(QPointF(-ln, 0), QPointF(ln, 0))
            for st, k in ((0.0, 0), (0.35, 0.25), (0.5, 1.0), (0.65, 0.25), (1.0, 0)):
                g.setColorAt(st, _qcol(c, b * k))
            p.setBrush(QBrush(g))
            p.drawEllipse(QPointF(0, 0), ln, max(0.6, sw * ex.get("relh", 0.01) * v["rthick"]))
        elif kind == "rays":
            n = max(1, int(round(ex.get("n", 8) * v["rnum"])))
            rng = np.random.default_rng(i * 31 + 7)
            p.rotate(v["rrot"] + ex.get("rot", 0.0))
            for k in range(n):
                ln = r * v["rlength"] * float(rng.uniform(0.55, 1.0))
                th = max(0.8, sw * ex.get("thick", 0.01) * v["rthick"])
                g = QLinearGradient(QPointF(0, 0), QPointF(ln, 0))
                g.setColorAt(0.0, _qcol(c, b))
                g.setColorAt(0.3, _qcol(c, b * 0.35))
                g.setColorAt(1.0, _qcol(c, 0))
                p.setBrush(QBrush(g))
                p.drawPolygon(QPolygonF([QPointF(0, -th), QPointF(ln, 0), QPointF(0, th)]))
                p.rotate(360.0 / n)
        p.restore()
    p.end()
    return to_array(img)


def _trigger(dist, radius, falloff):
    if radius <= 0:
        return 0.0
    k = max(0.0, 1.0 - dist / radius)
    return k ** max(0.01, falloff)


def _f_lensflare(arr, v, scale=1.0, frame=0, fps=30.0, seed=1, **_):
    """S_LensFlare: блик объектива из элементов вдоль линии Hotspot → Pivot; триггеры у краёв и центра,
    атмосфера, мерцание, цветокоррекция блика (Gamma, Saturation, Hue Shift)."""
    from .effects import blur
    h, w = arr.shape[:2]
    t = frame / max(fps, 1.0)
    hx, hy = v["hotspot"][0] * scale, v["hotspot"][1] * scale
    px, py = v["pivot"][0] * scale, v["pivot"][1] * scale
    bmul, wmul = 1.0, 1.0
    # край: чем ближе горячая точка к краю кадра, тем сильнее (Edge Triggers)
    edge_d = min(hx, hy, w - hx, h - hy) / max(w, h) - v["eshift"]
    k = _trigger(max(0.0, edge_d), v["ewidth"] * 0.5, v["efall"])
    bmul *= 1 + (v["esb"] - 1) * k
    wmul *= 1 + (v["esw"] - 1) * k
    k = _trigger(math.hypot(hx - px, hy - py) / max(w, h), v["cradius"], v["cfall"])
    bmul *= 1 + (v["csb"] - 1) * k
    wmul *= 1 + (v["csw"] - 1) * k
    if v["flicker"] > 0:       # мерцание: спокойно, с редкими всплесками (Randomness)
        from .effects import _fbm
        n = _fbm(t * v["flspeed"] * 6, seed + 3.3, 2)
        n = math.copysign(abs(n) ** (1.0 + v["flrand"] * 3), n)
        bmul *= max(0.0, 1.0 + n * v["flicker"])
    vv = dict(v, scale=v["scale"] * wmul)
    fl = _draw_flare(w, h, hx, hy, px, py, vv, scale, seed, t)
    rgb = fl[..., :3] / 255.0
    rgb = rgb * (np.array([v["color"][2], v["color"][1], v["color"][0]], np.float32) / 255.0) * v["bright"] * bmul
    if abs(v["gamma"] - 1.0) > 1e-3:
        rgb = np.power(np.clip(rgb, 0, None), 1.0 / max(0.1, v["gamma"]))
    if abs(v["sat"] - 1.0) > 1e-3:
        g = (rgb[..., 0] * 0.114 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.299)[..., None]
        rgb = np.clip(g + (rgb - g) * v["sat"], 0, None)
    rgb = hue_rotate(rgb, v["hue"])
    am = atmosphere(h, w, v["atmo"], v["atmofreq"], v["atmodet"], v["atmoseed"], v["atmospeed"], t)
    if am is not None:
        rgb = rgb * am
    rgb = rgb * 255.0
    if v["blurf"] > 0.3:
        tmp = np.concatenate([rgb, rgb.max(axis=2, keepdims=True)], axis=2).astype(np.float32)
        rgb = blur(tmp, v["blurf"] * scale)[..., :3]
    base = arr[..., :3] * v["bgbright"]
    mode = int(v["combine"])
    if mode == 2:
        res = rgb
    elif mode == 1:
        res = base + rgb
    else:
        res = combine(base, rgb, 2)
        if v["tintw"]:         # на белом фоне Screen «съедает» цвет — подмешиваем цвет блика
            res = res * (1 - 0.3 * np.clip(base / 255.0, 0, 1)) + rgb * 0.3 * np.clip(base / 255.0, 0, 1)
    out = arr.copy()
    out[..., 3] = np.maximum(arr[..., 3] if mode != 2 else 0, np.clip(rgb.max(axis=2), 0, 255) * v["affalpha"])
    out[..., :3] = np.minimum(np.clip(res, 0, 255), out[..., 3:4])
    return out


_FLARE_RAYS = ("Лучи", "Rays")
_FLARE_FL = ("Мерцание", "Flicker")
_FLARE_ET = ("Триггер у краёв", "Edge Triggers")
_FLARE_CT = ("Триггер у центра", "Center Triggers")
NEW_EFFECTS.append(EffectDef("lensflare", "Блик объектива (S_LensFlare)", "S_LensFlare", [
    Param("lens", "Объектив", "Lens", [0.0], kind="choice", choices=[(r, e) for r, e, _ in _LENSES.values()]),
    Param("scale", "Масштаб элементов", "Scale Widths", [1.15], 0.005, "", 0, 20),
    Param("relh", "Отн. высота", "Rel Heights", [1.0], 0.01, "", 0.01, 10),
    Param("rrot", "Поворот лучей", "Rays Rotate", [0.0], 0.5, "°", kind="angle"),
    Param("hotspot", "Горячая точка", "Hotspot", [0.28, 0.4], 1.0, kind="point"),
    Param("pivot", "Опорная точка", "Pivot", [0.5, 0.5], 1.0, kind="point"),
    Param("bright", "Яркость", "Brightness", [1.0], 0.01, "", 0, 50),
    Param("color", "Цвет", "Color", [255.0, 255.0, 255.0], kind="color"),
    Param("gamma", "Гамма", "Gamma", [1.0], 0.01, "", 0.1, 10),
    Param("sat", "Насыщенность", "Saturation", [1.0], 0.01, "", -2, 8),
    Param("hue", "Сдвиг тона", "Hue Shift", [0.0], 0.001, "", -10, 10),
    Param("hbright", "Яркость центра", "Hotspot Bright", [1.0], 0.01, "", 0, 50),
    Param("hcolor", "Цвет центра", "Hotspot Color", [255.0, 255.0, 255.0], kind="color"),
    Param("obright", "Яркость остальных", "Other Brightness", [1.0], 0.01, "", 0, 50),
    Param("ocolor", "Цвет остальных", "Other Color", [255.0, 255.0, 255.0], kind="color"),
    Param("owidth", "Размер остальных", "Other Width", [1.0], 0.01, "", 0, 20),
    Param("rbright", "Яркость лучей", "Rays Brightness", [1.0], 0.01, "", 0, 50, group=_FLARE_RAYS),
    Param("rnum", "Число лучей ×", "Rays Num Scale", [1.0], 0.01, "", 0, 10, group=_FLARE_RAYS),
    Param("rlength", "Длина лучей", "Rays Length", [1.0], 0.01, "", 0, 20, group=_FLARE_RAYS),
    Param("rthick", "Толщина лучей", "Rays Thickness", [1.0], 0.01, "", 0, 20, group=_FLARE_RAYS),
    Param("atmo", "Сила атмосферы", "Atmosphere Amp", [0.0], 0.01, "", 0, 10, group=ATMO2),
    Param("atmofreq", "Частота атмосферы", "Atmosphere Freq", [1.0], 0.01, "", 0.1, 20, group=ATMO2),
    Param("atmodet", "Детали атмосферы", "Atmosphere Detail", [0.6], 0.01, "", 0, 1, group=ATMO2),
    Param("atmoseed", "Случайность атмосферы", "Atmosphere Seed", [0.123], 0.001, "", 0, 1000, group=ATMO2),
    Param("atmospeed", "Скорость атмосферы", "Atmosphere Speed", [1.0], 0.01, "", -50, 50, group=ATMO2),
    Param("flicker", "Сила мерцания", "Flicker Amp", [0.0], 0.01, "", 0, 4, group=_FLARE_FL),
    Param("flspeed", "Скорость мерцания", "Flicker Speed", [1.0], 0.01, "", 0, 50, group=_FLARE_FL),
    Param("flrand", "Случайность мерцания", "Flicker Randomness", [0.6], 0.01, "", 0, 1, group=_FLARE_FL),
    Param("blurf", "Размытие блика", "Blur Flare", [0.0], 0.1, " px", 0, 500),
    Param("bgbright", "Яркость фона", "Bg Brightness", [1.0], 0.01, "", 0, 4),
    Param("combine", "Наложение", "Combine", [0.0], kind="choice",
          choices=[("Экран", "Screen"), ("Сложение", "Add"), ("Только блик", "Flare Only")]),
    Param("tintw", "Тонировать белый фон", "Tint Bg Whites", [0.0], kind="checkbox"),
    Param("affalpha", "Влияет на альфу", "Affect Alpha", [1.0], 0.01, "", 0, 4),
    Param("ewidth", "Ширина зоны края", "Edge Width", [0.3], 0.01, "", 0, 2, group=_FLARE_ET),
    Param("efall", "Спад у края", "Edge Falloff", [2.0], 0.01, "", 0.01, 10, group=_FLARE_ET),
    Param("eshift", "Сдвиг зоны", "Shift Out", [0.0], 0.01, "", -1, 1, group=_FLARE_ET),
    Param("esb", "Яркость у края ×", "Edge Scale Brightness", [1.5], 0.01, "", 0, 10, group=_FLARE_ET),
    Param("esw", "Размер у края ×", "Edge Scale Widths", [1.2], 0.01, "", 0, 10, group=_FLARE_ET),
    Param("cradius", "Радиус зоны центра", "Center Radius", [0.3], 0.01, "", 0, 2, group=_FLARE_CT),
    Param("cfall", "Спад у центра", "Center Falloff", [1.0], 0.01, "", 0.01, 10, group=_FLARE_CT),
    Param("csb", "Яркость у центра ×", "Center Scale Brightness", [1.5], 0.01, "", 0, 10, group=_FLARE_CT),
    Param("csw", "Размер у центра ×", "Center Scale Widths", [1.2], 0.01, "", 0, 10, group=_FLARE_CT),
], opts={"seed": 1}, category="sapphire"))

for _d in NEW_EFFECTS:
    if not any(p.key == "mix" for p in _d.params):
        _d.params.append(Param("mix", "Непрозрачность эффекта", "Effect Opacity", [100.0], 0.5, "%", 0, 100,
                               group=COMPOSITING))
    EFFECTS[_d.type] = _d

FILTERS.update({"grunge": _f_grunge, "warpwaves": _f_warpwaves, "warpvortex": _f_warpvortex, "bokeh": _f_bokeh, "rays": _f_rays, "glint": _f_glint, "hotspots": _f_hotspots, "halftone": _f_halftone, "halftonecolor": _f_halftonecolor,
                "digitaldamage": _f_digitaldamage, "filmdamage": _f_filmdamage, "jpegdamage": _f_jpegdamage,
                "tvdamage": _f_tvdamage, "sglow": _f_sglow, "sblur": _f_sblur, "sdistort": _f_sdistort, "edgerays": _f_edgerays,
                "lensflare": _f_lensflare})
