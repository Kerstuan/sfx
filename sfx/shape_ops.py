# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Операторы шейп-слоя, как в Contents у After Effects.

Каждая фигура (sh) — это группа: путь + свои операторы + заливка/обводка + своя трансформация.
Операторы лежат в sh["ops"] = [{"id", "type", "on"}], их анимируемые параметры — свойства слоя
"shape/<id фигуры>/op_<id оператора>_<параметр>", поэтому у них есть ключи, выражения и граф.

Порядок как в AE: операторы применяются сверху вниз к контуру фигуры; Trim Paths режет итоговый
контур; Repeater размножает уже готовую фигуру с заливкой и обводкой.
"""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPainterPath, QPainterPathStroker

from .i18n import LANG


def _l(ru, en):
    return en if LANG == "en" else ru


# параметр: (ru, en, по умолчанию, шаг, суффикс, мин, макс)
OPS = {
    "trim": (("Обрезка контура", "Trim Paths"), {
        "start": ("Начало", "Start", [0.0], 0.5, "%", 0.0, 100.0),
        "end": ("Конец", "End", [100.0], 0.5, "%", 0.0, 100.0),
        "offset": ("Смещение", "Offset", [0.0], 0.5, "°", None, None)}),
    "repeater": (("Повторитель", "Repeater"), {
        "copies": ("Копии", "Copies", [3.0], 0.05, "", 0.0, 200.0),
        "roffset": ("Сдвиг копий", "Offset", [0.0], 0.05, "", None, None),
        "pos": ("Положение", "Position", [100.0, 0.0], 1.0, "", None, None),
        "anchor": ("Опорная точка", "Anchor Point", [0.0, 0.0], 1.0, "", None, None),
        "scale": ("Масштаб", "Scale", [100.0, 100.0], 0.5, "%", None, None),
        "rot": ("Поворот", "Rotation", [0.0], 0.5, "°", None, None),
        "sop": ("Начальная непрозрачность", "Start Opacity", [100.0], 0.5, "%", 0.0, 100.0),
        "eop": ("Конечная непрозрачность", "End Opacity", [100.0], 0.5, "%", 0.0, 100.0)}),
    "offset": (("Сдвиг контура", "Offset Paths"), {
        "amount": ("Величина", "Amount", [10.0], 0.5, " px", None, None)}),
    "pucker": (("Сжатие и раздувание", "Pucker & Bloat"), {
        "amount": ("Величина", "Amount", [30.0], 0.5, "%", -100.0, 100.0)}),
    "zigzag": (("Зигзаг", "Zig Zag"), {
        "size": ("Размер", "Size", [10.0], 0.5, " px", None, None),
        "ridges": ("Зубцов на сегмент", "Ridges per segment", [5.0], 0.05, "", 0.0, 100.0),
        "smooth": ("Плавно", "Smooth", [0.0], 1.0, "", 0.0, 1.0)}),
    "twist": (("Скручивание", "Twist"), {
        "angle": ("Угол", "Angle", [90.0], 0.5, "°", None, None),
        "center": ("Центр", "Center", [0.0, 0.0], 1.0, "", None, None)}),
    "wiggle": (("Дрожание контура", "Wiggle Paths"), {
        "size": ("Размер", "Size", [10.0], 0.5, " px", 0.0, None),
        "detail": ("Детализация", "Detail", [10.0], 0.5, " px", 1.0, 1000.0),
        "wps": ("Колебаний в секунду", "Wiggles/Second", [2.0], 0.05, "", 0.0, 60.0),
        "seed": ("Случайность", "Random Seed", [0.0], 1.0, "", None, None)}),
    "round": (("Скругление углов", "Round Corners"), {
        "radius": ("Радиус", "Radius", [10.0], 0.5, " px", 0.0, 10000.0)}),
    "merge": (("Объединить с фигурой ниже", "Merge Paths"), {
        "mode": ("Режим (0 слить, 1 вычесть, 2 пересечь, 3 исключить)", "Mode (0 add, 1 subtract, 2 intersect, 3 exclude)",
                 [0.0], 0.05, "", 0.0, 3.0)}),
}

# свойства самой фигуры (группы): трансформация группы, градиенты, штрихи
GROUP_PROPS = {
    "tr_anchor": ("Опорная точка фигуры", "Shape Anchor Point", [0.0, 0.0], 1.0, "", None, None),
    "tr_pos": ("Положение фигуры", "Shape Position", [0.0, 0.0], 1.0, "", None, None),
    "tr_scale": ("Масштаб фигуры", "Shape Scale", [100.0, 100.0], 0.5, "%", None, None),
    "tr_rot": ("Поворот фигуры", "Shape Rotation", [0.0], 0.5, "°", None, None),
    "tr_opacity": ("Непрозрачность фигуры", "Shape Opacity", [100.0], 0.5, "%", 0.0, 100.0),
    "g_start": ("Начало градиента заливки", "Fill Gradient Start", [-100.0, 0.0], 1.0, "", None, None),
    "g_end": ("Конец градиента заливки", "Fill Gradient End", [100.0, 0.0], 1.0, "", None, None),
    "sg_start": ("Начало градиента обводки", "Stroke Gradient Start", [-100.0, 0.0], 1.0, "", None, None),
    "sg_end": ("Конец градиента обводки", "Stroke Gradient End", [100.0, 0.0], 1.0, "", None, None),
    "stroke_opacity": ("Непрозрачность обводки", "Stroke Opacity", [100.0], 0.5, "%", 0.0, 100.0),
    "dash": ("Штрих", "Dash", [0.0], 0.5, " px", 0.0, 10000.0),
    "gap": ("Промежуток", "Gap", [0.0], 0.5, " px", 0.0, 10000.0),
    "dash_offset": ("Смещение штриха", "Dash Offset", [0.0], 0.5, " px", None, None),
}
CUSTOM: dict = {}   # тип оператора от плагина -> fn(path, values: dict, t_sec) -> QPainterPath

GROUP_ORDER = ["stroke_opacity", "dash", "gap", "dash_offset", "g_start", "g_end", "sg_start", "sg_end",
               "tr_anchor", "tr_pos", "tr_scale", "tr_rot", "tr_opacity"]


def op_name(t: str) -> str:
    return _l(*OPS[t][0]) if t in OPS else t


def op_key(sid: str, oid: str, param: str) -> str:
    return f"shape/{sid}/op_{oid}_{param}"


def split_sub(sub: str):
    """'op_<oid>_<param>' -> (oid, param)."""
    if not sub.startswith("op_"):
        return None
    rest = sub[3:]
    if "_" not in rest:
        return None
    oid, param = rest.split("_", 1)
    return oid, param


def param_info(layer, key):
    """(подпись, шаг, суффикс, мин, макс) для свойства оператора или группы; None — не наше."""
    parts = key.split("/", 2)
    if len(parts) != 3 or parts[0] != "shape":
        return None
    sid, sub = parts[1], parts[2]
    if sub in GROUP_PROPS:
        g = GROUP_PROPS[sub]
        return _l(g[0], g[1]), g[3], g[4], g[5], g[6]
    so = split_sub(sub)
    if not so:
        return None
    oid, param = so
    sh = layer.shape_by_id(sid) if hasattr(layer, "shape_by_id") else None
    op = next((o for o in (sh or {}).get("ops", []) if o["id"] == oid), None)
    if op is None or op["type"] not in OPS or param not in OPS[op["type"]][1]:
        return None
    pd = OPS[op["type"]][1][param]
    return f"{op_name(op['type'])} · {_l(pd[0], pd[1])}", pd[3], pd[4], pd[5], pd[6]


# ---------------------------------------------------------------- геометрия
def _polys(path: QPainterPath) -> list[tuple[list[QPointF], bool]]:
    """Контур → список (точки, замкнут ли). Кривые раскладываются в ломаную."""
    out = []
    for poly in path.toSubpathPolygons():
        pts = [QPointF(p) for p in poly]
        closed = len(pts) > 2 and (pts[0] - pts[-1]).manhattanLength() < 1e-3
        if closed:
            pts = pts[:-1]
        if len(pts) >= 2:
            out.append((pts, closed))
    return out


def _to_path(polys) -> QPainterPath:
    path = QPainterPath()
    for pts, closed in polys:
        if len(pts) < 2:
            continue
        path.moveTo(pts[0])
        for q in pts[1:]:
            path.lineTo(q)
        if closed:
            path.closeSubpath()
    return path


def _resample(pts, closed, step):
    """Равномерные точки через step px; у каждой — доля u внутри исходного отрезка (0 и 1 — вершины)."""
    segs = list(zip(pts, pts[1:] + ([pts[0]] if closed else [])))
    out = []
    for a, b in segs:
        d = math.hypot(b.x() - a.x(), b.y() - a.y())
        n = max(1, int(d / max(0.5, step)))
        for i in range(n):
            u = i / n
            out.append((QPointF(a.x() + (b.x() - a.x()) * u, a.y() + (b.y() - a.y()) * u), u))
    if not closed:
        out.append((QPointF(pts[-1]), 1.0))
    return out


def _center(polys):
    xs = [p.x() for pts, _ in polys for p in pts]
    ys = [p.y() for pts, _ in polys for p in pts]
    if not xs:
        return QPointF(0, 0)
    return QPointF((min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2)


def _hash(i: int, seed: float) -> float:
    x = math.sin(i * 127.1 + seed * 311.7) * 43758.5453
    return (x - math.floor(x)) * 2 - 1


def _noise(x: float, seed: float) -> float:
    i = math.floor(x)
    f = x - i
    u = f * f * (3 - 2 * f)
    return _hash(i, seed) * (1 - u) + _hash(i + 1, seed) * u


def op_round(path, radius):
    if radius <= 0:
        return path
    res = []
    for pts, closed in _polys(path):
        n = len(pts)
        if n < 3:
            res.append((pts, closed))
            continue
        new = []
        rng = range(n) if closed else range(1, n - 1)
        if not closed:
            new.append(pts[0])
        for i in rng:
            p0, p1, p2 = pts[i - 1], pts[i], pts[(i + 1) % n]
            v1 = QPointF(p0.x() - p1.x(), p0.y() - p1.y())
            v2 = QPointF(p2.x() - p1.x(), p2.y() - p1.y())
            l1, l2 = math.hypot(v1.x(), v1.y()), math.hypot(v2.x(), v2.y())
            if l1 < 1e-6 or l2 < 1e-6:
                continue
            cosang = (v1.x() * v2.x() + v1.y() * v2.y()) / (l1 * l2)
            if cosang < -0.985:          # почти прямая (точки кривой) — не скругляем
                new.append(p1)
                continue
            r = min(radius, l1 / 2, l2 / 2)
            a = QPointF(p1.x() + v1.x() / l1 * r, p1.y() + v1.y() / l1 * r)
            b = QPointF(p1.x() + v2.x() / l2 * r, p1.y() + v2.y() / l2 * r)
            for k in range(7):           # квадратичная дуга a → (угол) → b
                t = k / 6
                x = (1 - t) ** 2 * a.x() + 2 * (1 - t) * t * p1.x() + t * t * b.x()
                y = (1 - t) ** 2 * a.y() + 2 * (1 - t) * t * p1.y() + t * t * b.y()
                new.append(QPointF(x, y))
        if not closed:
            new.append(pts[-1])
        res.append((new, closed))
    return _to_path(res)


def op_pucker(path, amount):
    """Pucker & Bloat: +  — вершины к центру, рёбра наружу (раздувание); − — наоборот (сжатие)."""
    if abs(amount) < 1e-3:
        return path
    polys = _polys(path)
    c = _center(polys)
    k = amount / 100.0
    res = []
    for pts, closed in polys:
        new = []
        for p, u in _resample(pts, closed, 3.0):
            w = abs(math.cos(math.pi * u))          # 1 у вершины, 0 в середине ребра
            f = k * (-w + (1 - w))
            new.append(QPointF(p.x() + (p.x() - c.x()) * f, p.y() + (p.y() - c.y()) * f))
        res.append((new, closed))
    return _to_path(res)


def op_zigzag(path, size, ridges, smooth):
    if abs(size) < 1e-3 or ridges < 1:
        return path
    res = []
    for pts, closed in _polys(path):
        segs = list(zip(pts, pts[1:] + ([pts[0]] if closed else [])))
        n_per = max(1, int(round(ridges)))
        new = []
        sign = 1
        for a, b in segs:
            dx, dy = b.x() - a.x(), b.y() - a.y()
            ln = math.hypot(dx, dy)
            if ln < 1e-6:
                continue
            nx, ny = -dy / ln, dx / ln
            steps = max(2, n_per * 2) if ln > 6 else 1
            for i in range(steps):
                t = i / steps
                off = size * sign if i % 2 == 1 else 0.0
                if i % 2 == 1:
                    sign = -sign
                new.append(QPointF(a.x() + dx * t + nx * off, a.y() + dy * t + ny * off))
        if not closed and pts:
            new.append(pts[-1])
        res.append((new, closed))
    path = _to_path(res)
    if smooth >= 0.5:   # плавные волны: скругляем изломы
        path = op_round(path, abs(size) * 1.5)
    return path


def op_twist(path, angle, center):
    if abs(angle) < 1e-3:
        return path
    polys = _polys(path)
    c = _center(polys)
    c = QPointF(c.x() + center[0], c.y() + center[1])
    rmax = max((math.hypot(p.x() - c.x(), p.y() - c.y()) for pts, _ in polys for p in pts), default=1.0) or 1.0
    res = []
    for pts, closed in polys:
        new = []
        for p, _u in _resample(pts, closed, 3.0):
            dx, dy = p.x() - c.x(), p.y() - c.y()
            a = math.radians(angle) * (1 - math.hypot(dx, dy) / rmax)   # центр крутится сильнее края
            ca, sa = math.cos(a), math.sin(a)
            new.append(QPointF(c.x() + dx * ca - dy * sa, c.y() + dx * sa + dy * ca))
        res.append((new, closed))
    return _to_path(res)


def op_wiggle(path, size, detail, wps, seed, t_sec):
    if size <= 0:
        return path
    res = []
    tt = t_sec * wps
    idx = 0
    for pts, closed in _polys(path):
        new = []
        for p, _u in _resample(pts, closed, max(1.0, detail)):
            idx += 1
            dx = _noise(tt + idx * 0.37, seed + 1.7) * size
            dy = _noise(tt + idx * 0.37 + 50.0, seed + 9.1) * size
            new.append(QPointF(p.x() + dx, p.y() + dy))
        res.append((new, closed))
    return _to_path(res)


def op_offset(path, amount):
    if abs(amount) < 1e-3:
        return path
    st = QPainterPathStroker()
    st.setWidth(abs(amount) * 2)
    st.setJoinStyle(Qt.RoundJoin)
    outline = st.createStroke(path)
    return path.united(outline).simplified() if amount > 0 else path.subtracted(outline).simplified()


def op_trim(path, start, end, offset):
    """Trim Paths: оставить часть контура от start% до end% (со смещением в градусах, как в AE)."""
    s, e = min(start, end) / 100.0, max(start, end) / 100.0
    if s <= 0.0 and e >= 1.0:
        return path
    sh = (offset / 360.0) % 1.0
    res = []
    for pts, closed in _polys(path):
        ring = pts + ([pts[0]] if closed else [])
        lens = [0.0]
        for a, b in zip(ring, ring[1:]):
            lens.append(lens[-1] + math.hypot(b.x() - a.x(), b.y() - a.y()))
        total = lens[-1]
        if total < 1e-6:
            continue

        def at(d):
            d = max(0.0, min(total, d))
            for i in range(1, len(lens)):
                if lens[i] >= d:
                    seg = lens[i] - lens[i - 1]
                    t = (d - lens[i - 1]) / seg if seg > 1e-9 else 0.0
                    a, b = ring[i - 1], ring[i]
                    return QPointF(a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t)
            return QPointF(ring[-1])

        def piece(a0, a1):
            d0, d1 = a0 * total, a1 * total
            out = [at(d0)]
            for i, d in enumerate(lens):
                if d0 < d < d1:
                    out.append(QPointF(ring[i]))
            out.append(at(d1))
            return out
        a0, a1 = s + sh, e + sh
        if a1 <= 1.0 or not closed:
            if e - s > 1e-6:
                res.append((piece(min(a0, 1.0), min(a1, 1.0)), False))
        else:   # кусок переходит через начало замкнутого контура
            if a0 >= 1.0:
                res.append((piece(a0 - 1.0, a1 - 1.0), False))
            else:
                p1 = piece(a0, 1.0)
                p2 = piece(0.0, a1 - 1.0)
                res.append((p1 + p2[1:], False))
    return _to_path(res)


def op_merge(path, other: QPainterPath | None, mode: int):
    if other is None:
        return path
    if mode == 1:
        return other.subtracted(path)
    if mode == 2:
        return path.intersected(other)
    if mode == 3:
        return path.united(other).subtracted(path.intersected(other))
    return path.united(other)
