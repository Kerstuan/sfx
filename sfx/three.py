# Copyright (c) 2026 Kerstuan. All rights reserved.
"""3D-слои и камера.

Координаты как в AE: x вправо, y вниз, z — от зрителя вглубь. Камера по умолчанию стоит в (w/2, h/2, -zoom)
и смотрит в (w/2, h/2, 0), поэтому 3D-слой с z=0 без поворотов выглядит так же, как 2D.

Плоский слой после 3D-поворота и перспективы — это проективное преобразование плоскости (гомография),
а его QTransform умеет. Поэтому рисование, маски, эффекты и попадание мышью работают как с 2D-слоями.
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtGui import QTransform

from .model import DEFAULT_ZOOM

NEAR = 1.0


def _rx(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[1, 0, 0, 0], [0, c, -s, 0], [0, s, c, 0], [0, 0, 0, 1]], float)


def _ry(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0, s, 0], [0, 1, 0, 0], [-s, 0, c, 0], [0, 0, 0, 1]], float)


def _rz(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0, 0], [s, c, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], float)


def _t(x, y, z):
    m = np.eye(4)
    m[:3, 3] = (x, y, z)
    return m


def _s(x, y, z=1.0):
    return np.diag([x, y, z, 1.0])


def _xyz(v):
    return (v[0], v[1], v[2] if len(v) > 2 else 0.0)


def local_matrix(layer, comp_frame: float, fps: float) -> np.ndarray:
    """Матрица слоя 4x4 относительно родителя. 2D-слой тоже можно так представить (z=0)."""
    from .effects import shake_offset
    f = layer.local(comp_frame)
    ax, ay, az = _xyz(layer.props["anchor"].value_at(f))
    px, py, pz = _xyz(layer.props["position"].value_at(f))
    sx, sy = layer.props["scale"].value_at(f)[:2]
    rz = layer.props["rotation"].value_at(f)[0]
    if layer.effects:
        dx, dy, dr, ds = shake_offset(layer, fps, comp_frame)
        px, py, rz = px + dx, py + dy, rz + dr
        sx, sy = sx * ds, sy * ds
    m = _t(px, py, pz)
    if layer.threed:
        ox, oy, oz = layer.props["orientation"].value_at(f)
        rx = layer.props["rot_x"].value_at(f)[0]
        ry = layer.props["rot_y"].value_at(f)[0]
        m = m @ _rx(math.radians(ox)) @ _ry(math.radians(oy)) @ _rz(math.radians(oz))
        m = m @ _rx(math.radians(rx)) @ _ry(math.radians(ry))
    m = m @ _rz(math.radians(rz)) @ _s(sx / 100.0, sy / 100.0) @ _t(-ax, -ay, -az)
    return m


def world_matrix(layer, comp, comp_frame: float) -> np.ndarray:
    from .render import parent_chain
    m = local_matrix(layer, comp_frame, comp.fps)
    for par in parent_chain(layer, comp):
        if par.kind == "camera":
            continue
        m = local_matrix(par, comp_frame, comp.fps) @ m
    return m


# ---------------------------------------------------------------- 3D-виды (3D View во вьювере, как в AE)
# Active Camera — рендер через камеру; Front/Left/Top/Back/Right/Bottom — ортогональные виды; Custom — облёт.
VIEWS = ("active", "front", "left", "top", "back", "right", "bottom", "custom")
_ORTHO_DIR = {"front": (0, 0, 1), "back": (0, 0, -1), "left": (1, 0, 0), "right": (-1, 0, 0),
              "top": (0, 1, 0), "bottom": (0, -1, 0)}
# shortcut: ортогональ = камера в ORTHO_D px с zoom = ORTHO_D (масштаб 1:1); перспективная ошибка ~ глубина сцены / 1e6
ORTHO_D = 1.0e6
CUSTOM_DEFAULT = {"yaw": -30.0, "pitch": -20.0, "dist": 2.2, "target": None}   # dist — в «зумах» по умолчанию


def view_override(comp) -> str | None:
    """Вид, выбранный во вьювере, — только для композиции, открытой сейчас (не для вложенных прекомпов).
    None — рендер через активную камеру (так всегда считается экспорт)."""
    v = getattr(comp, "view3d", "active")
    if v not in VIEWS or v == "active":
        return None
    proj = getattr(comp, "_project", None)
    return v if proj is not None and proj.active == comp.id else None


def _look(pos, poi, roll_deg=0.0):
    """Базис камеры (строки: right, down, forward) по позиции и точке интереса; «вниз» мира = +y."""
    fwd = np.asarray(poi, float) - np.asarray(pos, float)
    n = np.linalg.norm(fwd)
    fwd = fwd / n if n > 1e-9 else np.array([0.0, 0.0, 1.0])
    right = np.cross(np.array([0.0, 1.0, 0.0]), fwd)
    if np.linalg.norm(right) < 1e-6:          # смотрим строго вверх/вниз
        right = np.array([1.0, 0.0, 0.0])
    right /= np.linalg.norm(right)
    up = np.cross(fwd, right)
    roll = math.radians(roll_deg)
    if roll:
        c, s = math.cos(roll), math.sin(roll)
        right, up = right * c + up * s, -right * s + up * c
    return np.array([right, up, fwd])


def custom_view(comp) -> dict:
    cv = dict(CUSTOM_DEFAULT)
    cv.update(getattr(comp, "view_custom", None) or {})
    if cv["target"] is None:
        cv["target"] = [comp.width / 2, comp.height / 2, 0.0]
    return cv


def _view_camera(comp, view):
    w, h = comp.width, comp.height
    if view in _ORTHO_DIR:
        fwd = np.array(_ORTHO_DIR[view], float)
        vo = (getattr(comp, "view_ortho", None) or {}).get(view) or {}
        t = np.array(vo.get("target") or [w / 2, h / 2, 0.0], float)
        pos = t - fwd * ORTHO_D
        return pos, _look(pos, t), ORTHO_D * float(vo.get("scale", 1.0))
    cv = custom_view(comp)
    zoom = DEFAULT_ZOOM * w / 1920.0
    r = max(1.0, cv["dist"] * zoom)
    yaw, pitch = math.radians(cv["yaw"]), math.radians(cv["pitch"])
    t = np.array(cv["target"], float)
    pos = t + np.array([r * math.cos(pitch) * math.sin(yaw), r * math.sin(pitch), -r * math.cos(pitch) * math.cos(yaw)])
    return pos, _look(pos, t), zoom


def fit_ortho(comp, view: str, frame: float, margin: float = 0.8):
    """Вписать сцену в ортогональный вид: 3D-слои, камеры, свет и точки интереса (масштаб не больше 1:1)."""
    if view not in _ORTHO_DIR:
        return
    w, h = comp.width, comp.height
    fwd = np.array(_ORTHO_DIR[view], float)
    basis = _look(np.zeros(3), fwd)
    pts = []
    for l in comp.layers:
        if not l.active_at(int(frame)):
            continue
        if l.kind in ("camera", "light"):
            f = l.local(frame)
            pts.append(_xyz(l.props["position"].value_at(f)))
            if "poi" in l.props:
                pts.append(_xyz(l.props["poi"].value_at(f)))
        elif l.threed:
            x, y, lw, lh = l.local_rect()
            m = world_matrix(l, comp, frame)
            pts += [tuple((m @ np.array([cx, cy, 0.0, 1.0]))[:3]) for cx, cy in
                    ((x, y), (x + lw, y), (x, y + lh), (x + lw, y + lh))]
    if not pts:
        pts = [(0.0, 0.0, 0.0), (w, h, 0.0)]
    P = np.array(pts, float)
    sx, sy = P @ basis[0], P @ basis[1]
    bw, bh = float(sx.max() - sx.min()), float(sy.max() - sy.min())
    scale = min(1.0, margin * min(w / max(bw, 1.0), h / max(bh, 1.0)))
    cx, cy = (sx.max() + sx.min()) / 2, (sy.max() + sy.min()) / 2
    depth = float(fwd @ np.array([w / 2, h / 2, 0.0]))
    target = basis[0] * cx + basis[1] * cy + fwd * depth
    vo = dict(getattr(comp, "view_ortho", None) or {})
    vo[view] = {"scale": scale, "target": [float(v) for v in target]}
    comp.view_ortho = vo


def zoom_view(comp, view: str, factor: float):
    """Колесо в ортогональном виде: масштаб вида (вьювер показывает кадр размером с композицию)."""
    if view == "custom":
        return dolly_view(comp, 1 / factor)
    vo = dict(getattr(comp, "view_ortho", None) or {})
    cur = dict(vo.get(view) or {})
    cur["scale"] = max(0.005, min(20.0, float(cur.get("scale", 1.0)) * factor))
    vo[view] = cur
    comp.view_ortho = vo


def pan_view(comp, view: str, frame: float, dx: float, dy: float):
    """Сдвиг вида на (dx, dy) px композиции — цель вида едет в обратную сторону."""
    pos, basis, zoom = camera_params(comp, frame)
    if view == "custom":
        cv = custom_view(comp)
        k = np.linalg.norm(np.array(cv["target"], float) - pos) / zoom
        cv["target"] = [float(v) for v in np.array(cv["target"], float) - (basis[0] * dx + basis[1] * dy) * k]
        comp.view_custom = cv
        return
    vo = dict(getattr(comp, "view_ortho", None) or {})
    cur = dict(vo.get(view) or {})
    k = ORTHO_D / zoom
    t = np.array(cur.get("target") or [comp.width / 2, comp.height / 2, 0.0], float)
    cur["target"] = [float(v) for v in t - (basis[0] * dx + basis[1] * dy) * k]
    vo[view] = cur
    comp.view_ortho = vo


def layer_outlines(comp, frame: float) -> list:
    """Контуры 3D-слоёв в текущем виде (в px композиции): [(слой, [4 точки (x, y)])].
    В видах Top/Left слой, стоящий ребром, не рисуется картинкой — контур показывает его линией, как в AE."""
    vp = view_projection(comp, frame)
    out = []
    for l in comp.layers:
        if not l.threed or not l.active_at(int(frame)) or l.kind in ("camera", "light", "audio"):
            continue
        x, y, w, h = l.local_rect()
        m = vp @ world_matrix(l, comp, frame)
        pts = []
        for cx, cy in ((x, y), (x + w, y), (x + w, y + h), (x, y + h)):
            q = m @ np.array([cx, cy, 0.0, 1.0])
            if q[2] <= NEAR:
                break
            pts.append((float(q[0] / q[2]), float(q[1] / q[2])))
        if len(pts) == 4:
            out.append((l, pts))
    return out


def orbit_view(comp, dx_deg: float, dy_deg: float):
    """Облёт вида Custom (инструмент C в виде Custom меняет вид, а не камеру — как в AE)."""
    cv = custom_view(comp)
    cv["yaw"] = cv["yaw"] + dx_deg
    cv["pitch"] = max(-89.0, min(89.0, cv["pitch"] + dy_deg))
    comp.view_custom = cv


def dolly_view(comp, factor: float):
    cv = custom_view(comp)
    cv["dist"] = max(0.05, min(50.0, cv["dist"] * factor))
    comp.view_custom = cv


def camera_params(comp, comp_frame: float):
    """(позиция, базис камеры 3x3 (строки: right, down, forward), zoom)."""
    v = view_override(comp)
    if v is not None:
        return _view_camera(comp, v)
    cam = comp.active_camera(comp_frame)
    w, h = comp.width, comp.height
    if cam is None:
        zoom = DEFAULT_ZOOM * w / 1920.0
        return np.array([w / 2, h / 2, -zoom]), np.eye(3), zoom
    f = cam.local(comp_frame)
    pos = np.array(_xyz(cam.props["position"].value_at(f)), float)
    poi = np.array(_xyz(cam.props["poi"].value_at(f)), float)
    zoom = max(1.0, cam.props["zoom"].value_at(f)[0])
    return pos, _look(pos, poi, cam.props["rotation"].value_at(f)[0]), zoom


def view_projection(comp, comp_frame: float) -> np.ndarray:
    """Матрица 3x4: мировые координаты -> (X·W, Y·W, W) на экране композиции; W = глубина."""
    pos, basis, zoom = camera_params(comp, comp_frame)
    view = np.eye(4)
    view[:3, :3] = basis
    view[:3, 3] = -basis @ pos
    k = np.array([[zoom, 0, comp.width / 2], [0, zoom, comp.height / 2], [0, 0, 1.0]])
    return k @ view[:3, :]


def layer_homography(layer, comp, comp_frame: float) -> QTransform | None:
    """Гомография слоя в кадр. None — слой (частично) за камерой и не рисуется."""
    p = view_projection(comp, comp_frame) @ world_matrix(layer, comp, comp_frame)
    x, y, w, h = layer.local_rect()
    for cx, cy in ((x, y), (x + w, y), (x + w, y + h), (x, y + h)):
        if p[2, 0] * cx + p[2, 1] * cy + p[2, 3] < NEAR:
            return None
    hm = p[:, [0, 1, 3]]  # плоскость z=0 слоя
    # QTransform: x' = (m11 x + m21 y + m31) / (m13 x + m23 y + m33)
    return QTransform(hm[0, 0], hm[1, 0], hm[2, 0], hm[0, 1], hm[1, 1], hm[2, 1], hm[0, 2], hm[1, 2], hm[2, 2])


def layer_depth(layer, comp, comp_frame: float) -> float:
    """Глубина центра слоя от камеры — для сортировки 3D-слоёв (дальние рисуются первыми)."""
    pos, basis, _ = camera_params(comp, comp_frame)
    x, y, w, h = layer.local_rect()
    c = world_matrix(layer, comp, comp_frame) @ np.array([x + w / 2, y + h / 2, 0, 1.0])
    return float(basis[2] @ (c[:3] - pos))


# ---------------------------------------------------------------- управление камерой (инструменты)
def orbit(cam, f: int, dx_deg: float, dy_deg: float):
    """Облёт камеры вокруг точки интереса."""
    pos = np.array(_xyz(cam.props["position"].value_at(f)), float)
    poi = np.array(_xyz(cam.props["poi"].value_at(f)), float)
    v = pos - poi
    r = np.linalg.norm(v)
    if r < 1e-6:
        return
    yaw = math.atan2(v[0], -v[2]) + math.radians(dx_deg)
    pitch = math.asin(max(-1.0, min(1.0, v[1] / r))) + math.radians(dy_deg)
    pitch = max(-math.radians(89), min(math.radians(89), pitch))
    nv = np.array([r * math.cos(pitch) * math.sin(yaw), r * math.sin(pitch), -r * math.cos(pitch) * math.cos(yaw)])
    cam.props["position"].set_value_at(f, [round(x, 3) for x in poi + nv])


def track(cam, comp, f: int, dx: float, dy: float, dz: float = 0.0):
    """Сдвиг камеры вместе с точкой интереса (dx, dy — по экрану; dz — наезд вперёд)."""
    _, basis, _ = camera_params(comp, cam.start + f)
    d = -basis[0] * dx - basis[1] * dy + basis[2] * dz
    for key in ("position", "poi"):
        v = np.array(_xyz(cam.props[key].value_at(f)), float) + d
        cam.props[key].set_value_at(f, [round(x, 3) for x in v])


def dolly(cam, comp, f: int, amount: float):
    """Наезд: двигаем только камеру к точке интереса."""
    _, basis, _ = camera_params(comp, cam.start + f)
    v = np.array(_xyz(cam.props["position"].value_at(f)), float) + basis[2] * amount
    cam.props["position"].set_value_at(f, [round(x, 3) for x in v])
