# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Свет, тени, материалы и глубина резкости для 3D-слоёв — как в After Effects (Classic 3D).

3D-слой — плоскость. Для каждого пикселя слоя на кадре находим его точку в мире (обратная гомография),
освещаем по Блинну–Фонгу: Ambient / Diffuse / Specular / Shininess / Metal из Material Options слоя.
Свет: Parallel (направление position → poi), Spot (конус, растушёвка), Point, Ambient; затухание
None / Smooth / Inverse Square Clamped. Тени: лучи от точки к свету пересекаются с плоскостями слоёв
«Отбрасывает тени», альфа отбрасывающего слоя затеняет. Нет ни одного света — слои не освещаются (как в AE).
Массивы — premultiplied BGRA float 0..255 (как effects.to_array)."""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtGui import QTransform

from .three import _xyz, camera_params, view_projection, world_matrix


def active_lights(comp, frame) -> list:
    return [l for l in comp.layers
            if l.kind == "light" and l.visible and l.in_point <= frame < l.out_point]


def lit(layer, comp, frame) -> bool:
    """Нужен ли слою проход освещения (иначе рендер идёт быстрым путём QPainter)."""
    if not layer.threed or layer.kind in ("camera", "light"):
        return False
    mat = getattr(layer, "material", None) or {}
    return bool(mat.get("accepts_lights", True) and active_lights(comp, frame))


def dof_radius(layer, comp, frame, scale) -> float:
    """Радиус размытия 3D-слоя от глубины резкости камеры (0 — резко).
    shortcut: один радиус на слой по глубине его центра (в AE — по каждому пикселю); для наклонённых
    к камере слоёв размытие одинаковое по всей плоскости. Улучшить — размывать полосами по глубине."""
    from .three import layer_depth, view_override
    cam = comp.active_camera(frame)
    if cam is None or not getattr(cam, "dof", False) or not layer.threed or view_override(comp):
        return 0.0              # в видах Front/Top/Custom глубины резкости нет (как в AE)
    f = cam.local(frame)
    focus = max(1.0, cam.props["focus_dist"].value_at(f)[0])
    ap = max(0.0, cam.props["aperture"].value_at(f)[0])
    lvl = max(0.0, cam.props["blur_level"].value_at(f)[0]) / 100.0
    d = layer_depth(layer, comp, frame)
    if d <= 1.0:
        return 0.0
    # кружок нерезкости тонкой линзы в пикселях кадра: A·|d − f| / d
    return ap * abs(d - focus) / d * lvl * scale


def _light(l, frame):
    f = l.local(frame)
    p = l.props
    col = np.array(p["light_color"].value_at(f)[:3], np.float64) / 255.0
    return {
        "type": l.light["type"], "falloff": l.light.get("falloff", "none"),
        "shadows": bool(l.light.get("shadows")) and l.light["type"] != "ambient",
        "pos": np.array(_xyz(p["position"].value_at(f)), np.float64),
        "poi": np.array(_xyz(p["poi"].value_at(f)), np.float64),
        "i": max(0.0, p["light_intensity"].value_at(f)[0]) / 100.0,
        "col": col[::-1],                       # RGB → BGR, как в массиве кадра
        "cone": max(0.0, min(180.0, p["cone_angle"].value_at(f)[0])),
        "feather": max(0.0, min(100.0, p["cone_feather"].value_at(f)[0])) / 100.0,
        "radius": max(0.0, p["light_radius"].value_at(f)[0]),
        "fdist": max(1e-3, p["falloff_dist"].value_at(f)[0]),
        "dark": max(0.0, min(100.0, p["shadow_darkness"].value_at(f)[0])) / 100.0,
        "diff": max(0.0, p["shadow_diffusion"].value_at(f)[0]),
    }


def _plane(layer, comp, frame):
    """Плоскость слоя в мире: начало O, оси ex, ey (на 1 px слоя), нормаль n."""
    m = world_matrix(layer, comp, frame)
    o, ex, ey = m[:3, 3], m[:3, 0], m[:3, 1]
    n = np.cross(ex, ey)
    nn = np.linalg.norm(n)
    return o, ex, ey, (n / nn if nn > 1e-12 else np.array([0.0, 0.0, -1.0]))


def _local_alpha(layer, comp, frame, pool, scale):
    """Альфа слоя в его собственных координатах (для теней): (массив, x0, y0, масштаб)."""
    from .effects import to_array
    from .render import _new_canvas, _painter, draw_layer
    x, y, w, h = layer.local_rect()
    s = min(scale, 2048.0 / max(w, h, 1.0))
    img = _new_canvas(max(1, int(w * s)), max(1, int(h * s)))
    p = _painter(img)
    p.setTransform(QTransform(1, 0, 0, 1, -x, -y) * QTransform.fromScale(s, s))
    draw_layer(p, layer, comp, frame, pool, scale)
    p.end()
    return to_array(img)[..., 3] / 255.0, x, y, s


GRID = 4   # свет считается в узлах сетки через GRID пикселей и интерполируется: на плоскости он плавный


def _upsample(g: np.ndarray, k: int, bw: int, bh: int) -> np.ndarray:
    """Билинейно и плотно: сетка g (gh, gw, C) с шагом k → прямоугольник (bh, bw, C); по осям по очереди."""
    if k == 1:
        return g[:bh, :bw]
    jx, jy = np.arange(bw), np.arange(bh)
    ix, iy = jx // k, jy // k
    tx = ((jx % k) / k).astype(np.float32)[None, :, None]
    ty = ((jy % k) / k).astype(np.float32)[:, None, None]
    row = g[:, ix] * (1 - tx) + g[:, ix + 1] * tx            # (gh, bw, C)
    return row[iy] * (1 - ty) + row[iy + 1] * ty           # (bh, bw, C)


def shade(arr: np.ndarray, layer, comp, frame, scale: float, pool) -> np.ndarray:
    """Осветить слой на холсте композиции (arr — слой уже нарисован в кадр, h×w×4)."""
    lights = active_lights(comp, frame)
    if not lights:
        return arr
    h, w = arr.shape[:2]
    alpha = arr[..., 3]
    ys, xs = np.nonzero(alpha > 0)
    if xs.size == 0:
        return arr
    # пиксель кадра → координаты слоя (обратная гомография) → точка в мире
    hm = view_projection(comp, frame) @ world_matrix(layer, comp, frame)
    hm = np.diag([w / comp.width, h / comp.height, 1.0]) @ hm[:, [0, 1, 3]]
    try:
        inv = np.linalg.inv(hm)
    except np.linalg.LinAlgError:
        return arr
    # узлы сетки над прямоугольником слоя (мелкий слой — каждый пиксель)
    bx0, by0 = int(xs.min()), int(ys.min())
    bw, bh = int(xs.max()) - bx0 + 1, int(ys.max()) - by0 + 1
    k = GRID if bw * bh > 20000 else 1
    gw, gh = bw // k + 2, bh // k + 2
    gx, gy = np.meshgrid(bx0 + np.arange(gw) * k + 0.5, by0 + np.arange(gh) * k + 0.5)
    pix = np.stack([gx.ravel(), gy.ravel(), np.ones(gx.size)])
    uvw = inv @ pix
    wz = np.where(np.abs(uvw[2]) > 1e-12, uvw[2], 1e-12)
    u, v = uvw[0] / wz, uvw[1] / wz
    o, ex, ey, n = _plane(layer, comp, frame)
    P = o[None, :] + u[:, None] * ex[None, :] + v[:, None] * ey[None, :]
    cam_pos = camera_params(comp, frame)[0]
    V = cam_pos[None, :] - P
    V /= np.maximum(np.linalg.norm(V, axis=1, keepdims=True), 1e-9)
    # двусторонняя плоскость: нормаль смотрит на камеру
    if float(n @ (cam_pos - o)) < 0:
        n = -n
    f = layer.local(frame)

    def mv(key, d):
        return layer.props[key].value_at(f)[0] / 100.0 if key in layer.props else d
    k_amb, k_dif, k_spec = mv("mat_ambient", 1.0), mv("mat_diffuse", 0.5), mv("mat_spec", 0.5)
    shine, metal = mv("mat_shine", 0.05), mv("mat_metal", 1.0)
    expo = 2.0 + 200.0 * shine ** 2            # 5% (по умолчанию AE) — широкий блик, 100% — точечный
    mat = getattr(layer, "material", None) or {}
    casters = []
    if mat.get("accepts_shadows", True):
        casters = [c for c in comp.layers if c is not layer and c.threed and c.visible
                   and c.kind not in ("camera", "light") and c.in_point <= frame < c.out_point
                   and (getattr(c, "material", None) or {}).get("casts_shadows")]
    N = P.shape[0]
    diff = np.zeros((N, 3))
    spec = np.zeros((N, 3))
    for L in (_light(l, frame) for l in lights):
        c = L["col"] * L["i"]
        if L["type"] == "ambient":
            diff += k_amb * c[None, :]
            continue
        if L["type"] == "parallel":
            d = L["pos"] - L["poi"]
            d = d / max(np.linalg.norm(d), 1e-9)
            Ld = np.broadcast_to(d, P.shape)
            att = np.ones(N)
        else:
            Lv = L["pos"][None, :] - P
            dist = np.maximum(np.linalg.norm(Lv, axis=1), 1e-9)
            Ld = Lv / dist[:, None]
            att = np.ones(N)
            if L["falloff"] == "smooth":          # полная яркость до радиуса, линейно гаснет за fdist
                att = np.clip(1.0 - (dist - L["radius"]) / L["fdist"], 0.0, 1.0)
            elif L["falloff"] == "inverse_square":
                r = max(L["radius"], 1e-3)
                att = np.minimum(1.0, (r / dist) ** 2)
            if L["type"] == "spot":
                axis = L["poi"] - L["pos"]
                axis = axis / max(np.linalg.norm(axis), 1e-9)
                ang = np.arccos(np.clip(-(Ld @ axis), -1.0, 1.0))
                half = math.radians(L["cone"] / 2.0)
                inner = half * (1.0 - L["feather"])
                att = att * np.clip((half - ang) / max(half - inner, 1e-6), 0.0, 1.0)
        ndl = np.clip(Ld @ n, 0.0, 1.0)
        if L["shadows"] and casters:
            occ = _occlusion(P, Ld, L, casters, comp, frame, pool, scale)
            if L["diff"] > 0 and occ.any():       # рассеивание тени — размытие карты тени на сетке
                from .effects import blur
                m = np.zeros((gh, gw, 4), np.float32)
                m[..., 0] = occ.reshape(gh, gw) * 255.0
                occ = blur(m, L["diff"] * scale / k)[..., 0].ravel() / 255.0
            att = att * (1.0 - L["dark"] * occ)
        diff += (k_dif / 0.5) * (ndl * att)[:, None] * c[None, :]   # Diffuse 50% (по умолчанию AE) = полный свет
        Hv = Ld + V
        Hv = Hv / np.maximum(np.linalg.norm(Hv, axis=1, keepdims=True), 1e-9)
        sp = k_spec * np.power(np.clip(Hv @ n, 0.0, 1.0), expo) * att * (ndl > 0)
        spec += sp[:, None] * c[None, :]
    # сетка → прямоугольник слоя (пустые пиксели: rgb = a = 0, результат тоже 0)
    both = _upsample(np.concatenate([diff, spec], 1).reshape(gh, gw, 6).astype(np.float32), k, bw, bh)
    dpx, spx = both[..., :3], both[..., 3:]
    region = arr[by0:by0 + bh, bx0:bx0 + bw]
    rgb, a = region[..., :3], region[..., 3:4]
    # блик: металл 100% — цвета поверхности, 0% — цвета света
    tint = metal * rgb / np.maximum(a, 1e-6) + (1.0 - metal) * 255.0
    out_rgb = rgb * dpx + a * spx * tint * (1.0 / 255.0)
    res = arr.copy()
    res[by0:by0 + bh, bx0:bx0 + bw, :3] = np.clip(out_rgb, 0.0, a)
    return res


def _occlusion(P, Ld, L, casters, comp, frame, pool, scale) -> np.ndarray:
    """Доля света, перекрытая слоями-тенями (0..1), для каждой точки P."""
    occ = np.zeros(P.shape[0])
    for c in casters:
        o, ex, ey, nc = _plane(c, comp, frame)
        if L["type"] == "parallel":
            D, tmax = Ld, np.inf
        else:
            D, tmax = L["pos"][None, :] - P, 1.0
        den = D @ nc
        ok = np.abs(den) > 1e-9
        t = np.where(ok, ((o[None, :] - P) @ nc) / np.where(ok, den, 1.0), -1.0)
        hit = ok & (t > 1e-6) & (t < tmax)
        if not hit.any():
            continue
        Hp = P + t[:, None] * D - o[None, :]
        g = np.array([[ex @ ex, ex @ ey], [ey @ ex, ey @ ey]])
        try:
            gi = np.linalg.inv(g)
        except np.linalg.LinAlgError:
            continue
        uv = np.stack([Hp @ ex, Hp @ ey], 1) @ gi.T
        am, x0, y0, s = _local_alpha(c, comp, frame, pool, scale)
        ix = np.floor((uv[:, 0] - x0) * s).astype(np.int64)
        iy = np.floor((uv[:, 1] - y0) * s).astype(np.int64)
        inside = hit & (ix >= 0) & (iy >= 0) & (ix < am.shape[1]) & (iy < am.shape[0])
        a = np.zeros(P.shape[0])
        a[inside] = am[iy[inside], ix[inside]]
        occ = 1.0 - (1.0 - occ) * (1.0 - a)
    return occ
