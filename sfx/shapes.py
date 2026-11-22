# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""Контуры масок и фигур шейп-слоёв.

Контур хранится в свойстве как плоский список чисел: на каждую вершину 6 значений
[x, y, in_x, in_y, out_x, out_y], где in/out — касательные относительно вершины (как в AE).
Так контур анимируется ключами обычным свойством (при одинаковом числе вершин).
"""
from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPainterPathStroker, QPen, QTransform

KAPPA = 0.5522847498

MASK_MODES = ["add", "subtract", "intersect", "difference", "none"]
SHAPE_TYPES = ["rect", "ellipse", "star", "polygon", "path"]


# ---------------------------------------------------------------- вершины
def verts(vals) -> list[tuple]:
    n = len(vals) // 6
    return [tuple(vals[i * 6:(i + 1) * 6]) for i in range(n)]


def flat(vs) -> list[float]:
    out = []
    for v in vs:
        out += [float(x) for x in v]
    return out


def rect_vals(x, y, w, h) -> list[float]:
    return flat([(x, y, 0, 0, 0, 0), (x + w, y, 0, 0, 0, 0), (x + w, y + h, 0, 0, 0, 0), (x, y + h, 0, 0, 0, 0)])


def ellipse_vals(cx, cy, rx, ry) -> list[float]:
    kx, ky = rx * KAPPA, ry * KAPPA
    return flat([(cx, cy - ry, -kx, 0, kx, 0), (cx + rx, cy, 0, -ky, 0, ky),
                 (cx, cy + ry, kx, 0, -kx, 0), (cx - rx, cy, 0, ky, 0, -ky)])


def path_from(vals, closed=True) -> QPainterPath:
    vs = verts(vals)
    path = QPainterPath()
    if not vs:
        return path
    path.moveTo(vs[0][0], vs[0][1])
    segs = list(zip(vs, vs[1:])) + ([(vs[-1], vs[0])] if closed and len(vs) > 2 else [])
    for a, b in segs:
        if a[4] == 0 and a[5] == 0 and b[2] == 0 and b[3] == 0:
            path.lineTo(b[0], b[1])
        else:
            path.cubicTo(a[0] + a[4], a[1] + a[5], b[0] + b[2], b[1] + b[3], b[0], b[1])
    if closed and len(vs) > 2:
        path.closeSubpath()
    return path


def expand(path: QPainterPath, amount: float) -> QPainterPath:
    if abs(amount) < 0.01:
        return path
    st = QPainterPathStroker()
    st.setWidth(abs(amount) * 2)
    st.setJoinStyle(Qt.RoundJoin)
    stroke = st.createStroke(path)
    return path.united(stroke) if amount > 0 else path.subtracted(stroke)


# ---------------------------------------------------------------- маски
def mask_path(layer, m, lf: float) -> QPainterPath:
    base = f"mask/{m['id']}/"
    vals = layer.props[base + "path"].value_at(lf)
    path = path_from(vals, m.get("closed", True))
    return expand(path, layer.props[base + "expansion"].value_at(lf)[0])


def active_masks(layer) -> list[dict]:
    return [m for m in layer.masks if m.get("mode", "add") != "none"]


def mask_alpha(layer, lf: float, w: int, h: int, transform: QTransform, scale: float) -> np.ndarray | None:
    """Альфа (H, W, 1) в 0..1 от всех масок слоя в координатах кадра; None — масок нет."""
    from .effects import blur
    ms = active_masks(layer)
    if not ms:
        return None
    acc = None
    for i, m in enumerate(ms):
        base = f"mask/{m['id']}/"
        img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        img.fill(0)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.setTransform(transform)
        p.fillPath(mask_path(layer, m, lf), QColor("#ffffff"))
        p.end()
        buf = np.frombuffer(img.constBits(), np.uint8, count=h * img.bytesPerLine()).reshape(h, img.bytesPerLine())
        a = buf[:, : w * 4].reshape(h, w, 4)[..., 3:4].astype(np.float32)
        feather = layer.props[base + "feather"].value_at(lf)[0] * scale
        if feather >= 0.5:
            a = blur(np.repeat(a, 4, axis=2), feather / 2)[..., 3:4]
        a = np.clip(a / 255.0, 0, 1)
        if m.get("inverted"):
            a = 1 - a
        a = a * (layer.props[base + "opacity"].value_at(lf)[0] / 100.0)
        mode = m.get("mode", "add")
        if acc is None:
            acc = 1 - a if mode == "subtract" else a
            continue
        if mode == "add":
            acc = acc + a - acc * a
        elif mode == "subtract":
            acc = acc * (1 - a)
        elif mode == "intersect":
            acc = acc * a
        elif mode == "difference":
            acc = np.abs(acc - a)
    return acc


# ---------------------------------------------------------------- фигуры шейп-слоя
def _v(layer, sh, key, lf, default):
    p = layer.props.get(f"shape/{sh['id']}/{key}")
    return p.value_at(lf) if p else default


def shape_path(layer, sh, lf: float) -> QPainterPath:
    t = sh.get("type", "rect")
    path = QPainterPath()
    if t == "path":
        return path_from(_v(layer, sh, "path", lf, []), sh.get("closed", True))
    px, py = _v(layer, sh, "position", lf, [0.0, 0.0])
    if t in ("rect", "ellipse"):
        w, h = _v(layer, sh, "size", lf, [100.0, 100.0])
        r = QRectF(px - w / 2, py - h / 2, w, h)
        if t == "ellipse":
            path.addEllipse(r)
        else:
            rd = min(_v(layer, sh, "roundness", lf, [0.0])[0], w / 2, h / 2)
            if rd > 0:
                path.addRoundedRect(r, rd, rd)
            else:
                path.addRect(r)
        return path
    # звезда / многоугольник
    n = max(3, int(round(_v(layer, sh, "points", lf, [5.0])[0])))
    ro = _v(layer, sh, "outer", lf, [100.0])[0]
    ri = _v(layer, sh, "inner", lf, [50.0])[0]
    rot = math.radians(_v(layer, sh, "srot", lf, [0.0])[0])
    pts = []
    steps = n if t == "polygon" else n * 2
    for i in range(steps):
        r = ro if (t == "polygon" or i % 2 == 0) else ri
        a = rot - math.pi / 2 + i * 2 * math.pi / steps
        pts.append(QPointF(px + r * math.cos(a), py + r * math.sin(a)))
    path.moveTo(pts[0])
    for q in pts[1:]:
        path.lineTo(q)
    path.closeSubpath()
    return path


def _gv(layer, sh, key, lf):
    from .shape_ops import GROUP_PROPS
    return _v(layer, sh, key, lf, GROUP_PROPS[key][2])


def _fps(layer) -> float:
    comp = getattr(layer, "_comp", None)
    return float(getattr(comp, "fps", 30.0) or 30.0)


def shape_geometry(layer, sh, lf: float, below=None) -> QPainterPath:
    """Контур фигуры после всех её операторов (кроме Repeater — он размножает при рисовании)."""
    from . import shape_ops as so
    path = shape_path(layer, sh, lf)
    ops = [o for o in sh.get("ops", []) if o.get("on", True) and o["type"] in so.OPS]

    def val(o, param):
        default = so.OPS[o["type"]][1][param][2]
        p = layer.props.get(so.op_key(sh["id"], o["id"], param))
        return p.value_at(lf) if p else default
    for o in ops:
        t = o["type"]
        if t == "round":
            path = so.op_round(path, val(o, "radius")[0])
        elif t == "pucker":
            path = so.op_pucker(path, val(o, "amount")[0])
        elif t == "zigzag":
            path = so.op_zigzag(path, val(o, "size")[0], val(o, "ridges")[0], val(o, "smooth")[0])
        elif t == "twist":
            path = so.op_twist(path, val(o, "angle")[0], val(o, "center"))
        elif t == "wiggle":
            path = so.op_wiggle(path, val(o, "size")[0], val(o, "detail")[0], val(o, "wps")[0],
                                val(o, "seed")[0], lf / _fps(layer))
        elif t == "offset":
            path = so.op_offset(path, val(o, "amount")[0])
        elif t in so.CUSTOM:   # оператор из плагина
            vals = {prm: (x[0] if len(x) == 1 else list(x)) for prm in so.OPS[t][1] for x in [val(o, prm)]}
            path = so.CUSTOM[t](path, vals, lf / _fps(layer))
        elif t == "merge":
            path = so.op_merge(path, shape_path(layer, below, lf) if below is not None else None,
                               int(round(val(o, "mode")[0])))
    for o in ops:   # Trim Paths — по уже готовому контуру
        if o["type"] == "trim":
            path = so.op_trim(path, val(o, "start")[0], val(o, "end")[0], val(o, "offset")[0])
    return path


def _pivot(layer, sh, lf) -> QPointF:
    """Центр фигуры — от него считаются опорная точка группы и Repeater (как у групп в AE)."""
    return shape_path(layer, sh, lf).boundingRect().center()


def _group_transform(layer, sh, lf) -> QTransform:
    c = _pivot(layer, sh, lf)
    ax, ay = _gv(layer, sh, "tr_anchor", lf)[:2]
    px, py = _gv(layer, sh, "tr_pos", lf)[:2]
    sx, sy = _gv(layer, sh, "tr_scale", lf)[:2]
    ax, ay = ax + c.x(), ay + c.y()
    px, py = px + ax, py + ay
    t = QTransform()
    t.translate(px, py)
    t.rotate(_gv(layer, sh, "tr_rot", lf)[0])
    t.scale(sx / 100.0, sy / 100.0)
    t.translate(-ax, -ay)
    return t


def _brush(layer, sh, lf, attr, color_key):
    """Сплошной цвет или градиент (линейный/радиальный), как Gradient Fill / Gradient Stroke в AE."""
    from PySide6.QtGui import QBrush, QLinearGradient, QRadialGradient
    kind = sh.get(f"{attr}_type", "solid")
    c1 = QColor(sh.get(color_key, "#ffffff"))
    if kind == "solid":
        return QBrush(c1)
    pre = "g" if attr == "fill" else "sg"
    a = _gv(layer, sh, f"{pre}_start", lf)
    b = _gv(layer, sh, f"{pre}_end", lf)
    c2 = QColor(sh.get(color_key + "2", "#000000"))
    if kind == "radial":
        g = QRadialGradient(QPointF(a[0], a[1]), max(1.0, math.hypot(b[0] - a[0], b[1] - a[1])))
    else:
        g = QLinearGradient(QPointF(a[0], a[1]), QPointF(b[0], b[1]))
    g.setColorAt(0.0, c1)
    g.setColorAt(1.0, c2)
    return QBrush(g)


_CAPS = {"round": Qt.RoundCap, "flat": Qt.FlatCap, "square": Qt.SquareCap}
_JOINS = {"round": Qt.RoundJoin, "miter": Qt.MiterJoin, "bevel": Qt.BevelJoin}


def _paint_shape(p: QPainter, layer, sh, lf, path):
    if sh.get("fill", True):
        op = max(0.0, min(1.0, _v(layer, sh, "fill_opacity", lf, [100.0])[0] / 100.0))
        if op > 0:
            p.save()
            p.setOpacity(p.opacity() * op)
            p.fillPath(path, _brush(layer, sh, lf, "fill", "fill_color"))
            p.restore()
    if sh.get("stroke", False):
        sw = _v(layer, sh, "stroke_width", lf, [4.0])[0]
        so_ = max(0.0, min(1.0, _gv(layer, sh, "stroke_opacity", lf)[0] / 100.0))
        if sw > 0 and so_ > 0:
            pen = QPen(_brush(layer, sh, lf, "stroke", "stroke_color"), sw)
            pen.setJoinStyle(_JOINS.get(sh.get("join", "round"), Qt.RoundJoin))
            pen.setCapStyle(_CAPS.get(sh.get("cap", "round"), Qt.RoundCap))
            dash = _gv(layer, sh, "dash", lf)[0]
            if dash > 0:   # штрихи, как Dashes у обводки в AE (в Qt — в долях толщины)
                gap = _gv(layer, sh, "gap", lf)[0] or dash
                pen.setDashPattern([max(0.01, dash / sw), max(0.01, gap / sw)])
                pen.setDashOffset(_gv(layer, sh, "dash_offset", lf)[0] / sw)
            p.save()
            p.setOpacity(p.opacity() * so_)
            p.strokePath(path, pen)
            p.restore()


def draw_shapes(p: QPainter, layer, lf: float):
    """Фигуры рисуются снизу вверх: последняя в списке — нижняя (как Contents в AE).
    У каждой — свои операторы, трансформация группы и Repeater."""
    from . import shape_ops as so
    shapes = layer.shapes
    merged_away = set()   # фигуры, которые «съел» Merge Paths фигуры выше
    for i, sh in enumerate(shapes):
        if any(o["type"] == "merge" and o.get("on", True) for o in sh.get("ops", [])) and i + 1 < len(shapes):
            merged_away.add(shapes[i + 1]["id"])
    for i in range(len(shapes) - 1, -1, -1):
        sh = shapes[i]
        if not sh.get("visible", True) or sh["id"] in merged_away:
            continue
        below = shapes[i + 1] if i + 1 < len(shapes) else None
        path = shape_geometry(layer, sh, lf, below)
        gt = _group_transform(layer, sh, lf)
        gop = max(0.0, min(1.0, _gv(layer, sh, "tr_opacity", lf)[0] / 100.0))
        reps = [o for o in sh.get("ops", []) if o["type"] == "repeater" and o.get("on", True)]
        copies = [(QTransform(), 1.0)]
        piv = _pivot(layer, sh, lf)
        for o in reps:
            def rv(param, o=o):
                pr = layer.props.get(so.op_key(sh["id"], o["id"], param))
                return pr.value_at(lf) if pr else so.OPS["repeater"][1][param][2]
            n = max(0, int(round(rv("copies")[0])))
            off = rv("roffset")[0]
            ax, ay = rv("anchor")[:2]
            ax, ay = ax + piv.x(), ay + piv.y()
            px, py = rv("pos")[:2]
            sx, sy = rv("scale")[:2]
            rot = rv("rot")[0]
            sop, eop = rv("sop")[0] / 100.0, rv("eop")[0] / 100.0
            new = []
            for base_t, base_o in copies:
                for k in range(n):
                    kk = k + off
                    t = QTransform()
                    t.translate(ax + px * kk, ay + py * kk)
                    t.rotate(rot * kk)
                    t.scale((sx / 100.0) ** kk, (sy / 100.0) ** kk)
                    t.translate(-ax, -ay)
                    f = k / (n - 1) if n > 1 else 0.0
                    new.append((t * base_t, base_o * (sop + (eop - sop) * f)))
            copies = new
        for ct, co in reversed(copies):
            p.save()
            p.setTransform(ct * gt * p.transform())
            p.setOpacity(p.opacity() * gop * co)
            _paint_shape(p, layer, sh, lf, path)
            p.restore()


def shapes_bounds(layer) -> tuple[float, float, float, float]:
    """Габариты всех фигур (по ключам на первом кадре) — для рамки выделения и попадания мышью."""
    rect = QRectF()
    for sh in layer.shapes:
        lf = 0.0
        p = layer.props.get(f"shape/{sh['id']}/path") or layer.props.get(f"shape/{sh['id']}/position")
        if p and p.keyframes:
            lf = p.keyframes[0].frame
        br = _group_transform(layer, sh, lf).map(shape_geometry(layer, sh, lf)).boundingRect()
        if sh.get("stroke"):
            sw = _v(layer, sh, "stroke_width", lf, [0.0])[0]
            br = br.adjusted(-sw / 2, -sw / 2, sw / 2, sw / 2)
        rect = br if rect.isNull() else rect.united(br)
    if rect.isNull():
        return (-50.0, -50.0, 100.0, 100.0)
    return (rect.x(), rect.y(), max(1.0, rect.width()), max(1.0, rect.height()))
