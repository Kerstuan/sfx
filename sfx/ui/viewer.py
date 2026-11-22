# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Панель «Композиция»: превью кадра и инструменты как в AE.

V — выделение (и правка вершин масок/контуров), H — рука, Z — лупа, W — поворот, Y — опорная точка,
Q — прямоугольник/эллипс/звезда (маска на выделенный слой или фигура в шейп-слой), G — перо,
Ctrl+T — текст, C — облёт камеры (средняя кнопка — сдвиг, правая/колесо — наезд).
"""
from __future__ import annotations

from ..i18n import tr

import math
import time

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from ..render import layer_corners, layer_hit, layer_transform, parent_world, render_frame, visible_layers
from ..shapes import path_from, shape_path, verts
from . import theme

MASK_COLOR = "#e8c547"
SHAPE_COLOR = "#4ab0e8"
CURSORS = {"hand": Qt.OpenHandCursor, "zoom": Qt.CrossCursor, "rotate": Qt.CrossCursor, "anchor": Qt.SizeAllCursor,
           "rect": Qt.CrossCursor, "ellipse": Qt.CrossCursor, "star": Qt.CrossCursor, "pen": Qt.CrossCursor,
           "text": Qt.IBeamCursor, "orbit": Qt.SizeAllCursor}


class Viewer(QWidget):
    new_comp_requested = Signal()
    from_footage_requested = Signal()
    message = Signal(str)
    context_requested = Signal(QPoint, object)   # глобальная точка, слой под курсором (или None)

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.ed = editor
        self.setMinimumSize(320, 200)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setAcceptDrops(True)
        self._img = None
        self._img_key = None
        self._drag = None
        self.transparency = False      # сетка прозрачности вместо фона композиции
        self.vzoom = None              # None — вписать; иначе масштаб вида (1.0 = 100%)
        self.vpan = QPointF(0, 0)
        self._pen = None               # перо: (layer|None, [вершины в координатах композиции], target)
        self._hover = None
        self._welcome = []
        editor.changed.connect(self.invalidate)
        editor.time_changed.connect(lambda _: self.invalidate())
        editor.frame_arrived.connect(lambda f: self.update() if f == self.ed.time else None)
        editor.selection_changed.connect(self.update)
        editor.tool_changed.connect(self._tool_changed)
        editor.color_pick_changed.connect(
            lambda on: self.setCursor(Qt.CrossCursor if on else CURSORS.get(self.ed.tool, Qt.ArrowCursor)))

    def _tool_changed(self, tool):
        if self._pen and tool != "pen":
            self._finish_pen(closed=False)
        self.setCursor(CURSORS.get(tool, Qt.ArrowCursor))
        self.update()

    def invalidate(self):
        self._img_key = None
        self.update()

    def fit(self):
        self.vzoom = None
        self.vpan = QPointF(0, 0)
        self.update()

    # --- геометрия -----------------------------------------------------------
    def _fit_scale(self):
        comp = self.ed.comp
        m = 16
        return min((self.width() - 2 * m) / comp.width, (self.height() - 2 * m) / comp.height)

    def view_scale(self):
        return self.vzoom if self.vzoom is not None else self._fit_scale()

    def _view_rect(self) -> QRectF:
        comp = self.ed.comp
        s = self.view_scale()
        w, h = comp.width * s, comp.height * s
        return QRectF((self.width() - w) / 2 + self.vpan.x(), (self.height() - h) / 2 + self.vpan.y(), w, h)

    def _to_comp(self, p: QPointF) -> QPointF:
        r = self._view_rect()
        comp = self.ed.comp
        return QPointF((p.x() - r.x()) / r.width() * comp.width, (p.y() - r.y()) / r.height() * comp.height)

    def _to_view(self, p: QPointF) -> QPointF:
        r = self._view_rect()
        comp = self.ed.comp
        return QPointF(r.x() + p.x() / comp.width * r.width(), r.y() + p.y() / comp.height * r.height())

    def zoom_at(self, factor, at: QPointF | None = None):
        at = at if at is not None else QPointF(self.width() / 2, self.height() / 2)
        cp = self._to_comp(at)
        self.vzoom = max(0.02, min(16.0, self.view_scale() * factor))
        nv = self._to_view(cp)
        self.vpan += at - nv
        self.update()

    # --- рисование ------------------------------------------------------------
    def _draw_light(self, p, light, comp, t):
        """Выделенный свет: значок в Position и линия к Point of Interest (у направленных), как в AE."""
        import numpy as np
        from ..three import _xyz, view_projection
        vp = view_projection(comp, t)
        f = light.local(t)

        def proj(v):
            q = vp @ np.array(list(_xyz(v)) + [1.0])
            return None if q[2] <= 1e-3 else self._to_view(QPointF(q[0] / q[2], q[1] / q[2]))
        pos = proj(light.props["position"].value_at(f))
        col = QColor("#e0c860")
        p.setPen(QPen(col, 1.5))
        p.setBrush(Qt.NoBrush)
        if light.light["type"] in ("spot", "parallel"):
            poi = proj(light.props["poi"].value_at(f))
            if pos is not None and poi is not None:
                p.drawLine(pos, poi)
                p.drawEllipse(poi, 4, 4)
        if pos is not None:
            p.drawEllipse(pos, 7, 7)
            for i in range(8):          # лучи «солнышка»
                d = QPointF(math.cos(i * math.pi / 4), math.sin(i * math.pi / 4))
                p.drawLine(pos + d * 10, pos + d * 15)

    def _view_name(self, comp):
        from ..three import VIEWS
        names = ("Активная камера", "Спереди", "Слева", "Сверху", "Сзади", "Справа", "Снизу", "Свой вид")
        v = getattr(comp, "view3d", "active")
        return tr(names[VIEWS.index(v)]) if v in VIEWS else ""

    def _draw_camera(self, p, cam, comp, t, selected):
        """Значок камеры в Position и линия к Point of Interest — видно только в не-активных 3D-видах."""
        import numpy as np
        from ..three import NEAR, _xyz, view_projection
        vp = view_projection(comp, t)
        f = cam.local(t)

        def proj(v):
            q = vp @ np.array(list(_xyz(v)) + [1.0])
            return None if q[2] <= NEAR else self._to_view(QPointF(q[0] / q[2], q[1] / q[2]))
        a, b = proj(cam.props["position"].value_at(f)), proj(cam.props["poi"].value_at(f))
        col = theme.c(theme.ACCENT) if selected else QColor("#b0b0b0")
        p.setPen(QPen(col, 1.5))
        p.setBrush(Qt.NoBrush)
        if a is not None and b is not None:
            p.drawLine(a, b)
            p.drawEllipse(b, 3, 3)
        if a is not None:
            p.drawRect(QRectF(a.x() - 9, a.y() - 6, 13, 12))
            p.drawPolygon(QPolygonF([QPointF(a.x() + 4, a.y() - 2), QPointF(a.x() + 10, a.y() - 6),
                                     QPointF(a.x() + 10, a.y() + 6), QPointF(a.x() + 4, a.y() + 2)]))

    def _camera_icon_at(self, pos):
        """Камера под курсором (по значку) — только в не-активных 3D-видах, где значок рисуется."""
        import numpy as np
        from ..three import NEAR, _xyz, view_override, view_projection
        comp, t = self.ed.comp, self.ed.time
        if not view_override(comp):
            return None
        vp = view_projection(comp, t)
        for cl in comp.layers:
            if cl.kind == "camera" and cl.active_at(t) and not cl.locked:
                q = vp @ np.array(list(_xyz(cl.props["position"].value_at(cl.local(t)))) + [1.0])
                if q[2] > NEAR:
                    a = self._to_view(QPointF(q[0] / q[2], q[1] / q[2]))
                    if abs(a.x() - pos.x()) <= 11 and abs(a.y() - pos.y()) <= 9:
                        return cl
        return None

    RING_PX = 70            # радиус колец поворота на экране, px
    RING_KEYS = ("rot_x", "rot_y", "rotation")

    def _rings(self):
        """Кольца поворота 3D-слоя (инструмент W), как в AE: для X/Y/Z Rotation — окружность вокруг той оси,
        вокруг которой реально крутит это свойство (Orientation → X → Y → Z, с родителем).
        → (слой, центр на экране, [(i, [точки на экране], [параметр θ точек])]) или None."""
        import numpy as np
        from ..three import NEAR, _rx, _ry, _xyz, local_matrix, view_projection, world_matrix
        l = self.ed.current()
        t = self.ed.time
        if l is None or not l.threed or not l.active_at(t) or l.locked:
            return None
        comp = self.ed.comp
        f = l.local(t)
        par = world_matrix(l, comp, t) @ np.linalg.inv(local_matrix(l, t, comp.fps))
        ox, oy, oz = l.props["orientation"].value_at(f)
        rx = l.props["rot_x"].value_at(f)[0]
        ry = l.props["rot_y"].value_at(f)[0]
        from ..three import _rz
        o = _rx(math.radians(ox)) @ _ry(math.radians(oy)) @ _rz(math.radians(oz))
        frames = [o, o @ _rx(math.radians(rx)), o @ _rx(math.radians(rx)) @ _ry(math.radians(ry))]
        pos = np.array(list(_xyz(l.props["position"].value_at(f))), float)
        vp = view_projection(comp, t)
        R = par[:3, :3]

        def proj(pw):
            q = vp @ np.array([pw[0], pw[1], pw[2], 1.0])
            return None if q[2] <= NEAR else self._to_view(QPointF(q[0] / q[2], q[1] / q[2]))
        cw = (par @ np.array([pos[0], pos[1], pos[2], 1.0]))[:3]
        c = proj(cw)
        if c is None:
            return None
        # радиус в мире — чтобы на экране было ~RING_PX
        # радиус в мире — чтобы на экране было ~RING_PX: px на единицу мира по осям экрана вида в центре
        from ..three import camera_params
        _, basis, _ = camera_params(comp, t)
        e1, e2 = proj(cw + basis[0]), proj(cw + basis[1])
        ppu = max(1e-6, (math.hypot(*(e1 - c).toTuple()) if e1 else 0) + (math.hypot(*(e2 - c).toTuple()) if e2 else 0)) / 2
        r = self.RING_PX / ppu
        # пары (u, v), u × v = ось: X → (y, z), Y → (z, x), Z → (x, y)
        uv = ((1, 2), (2, 0), (0, 1))
        out = []
        th = np.linspace(0, 2 * math.pi, 73)
        for i in range(3):
            m = R @ frames[i][:3, :3]
            u, v = m[:, uv[i][0]], m[:, uv[i][1]]
            pts = [proj(cw + r * (math.cos(a) * u + math.sin(a) * v)) for a in th]
            if any(pp is None for pp in pts):
                continue
            # «раскрытость» кольца на экране: у стоящего ребром ≈ 0 (рисуется линией)
            arr = np.array([pp.toTuple() for pp in pts])
            ev = np.linalg.eigvalsh(np.cov(arr.T))
            out.append((i, pts, list(th), float(math.sqrt(max(ev[0], 0.0)))))
        return l, c, out

    def _ring_at(self, pos):
        g = self._rings()
        if g is None:
            return None
        l, c, rings = g
        best = None
        for i, pts, th, opened in rings:
            flat = 6.0 if opened < 0.2 * self.RING_PX else 0.0     # ребром стоящее кольцо уступает открытому
            for j in range(len(pts) - 1):
                a, b = pts[j], pts[j + 1]
                ab = b - a
                tt = max(0.0, min(1.0, QPointF.dotProduct(pos - a, ab) / max(1e-9, QPointF.dotProduct(ab, ab))))
                dist = math.hypot(*((pos - (a + ab * tt)).toTuple()))
                if dist <= 6 and (best is None or dist + flat < best[0]):
                    ln = math.hypot(ab.x(), ab.y())
                    tan = QPointF(ab.x() / ln, ab.y() / ln) if ln > 1e-6 else QPointF(1, 0)
                    # постоянная чувствительность: сдвиг вдоль касательной на радиус кольца = 1 радиан
                    best = (dist + flat, i, tan, float(self.RING_PX))
        return None if best is None else (l, best[1], best[2], best[3])

    def _paint_rings(self, p):
        g = self._rings()
        if g is None:
            return
        _, c, rings = g
        drag_i = self._drag[2] if self._drag and self._drag[0] == "ring" else None
        hov = self._ring_at(self._hover) if self._hover is not None and self._drag is None else None
        p.setBrush(Qt.NoBrush)
        for i, pts, _, _ in rings:
            col = QColor(self.GIZMO_COLS[i])
            if i == drag_i or (hov is not None and hov[1] == i):
                col = col.lighter(150)
            p.setPen(QPen(col, 2.0 if i == drag_i else 1.6))
            p.drawPolyline(QPolygonF(pts))

    GIZMO_LEN = 64          # длина стрелки оси на экране, px
    GIZMO_COLS = ("#e04848", "#48c048", "#4890f0")

    def _gizmo(self):
        """Гизмо 3D-перемещения (оси X/Y/Z, как в AE в режиме World Axis) у выделенного 3D-слоя, света или камеры.
        → (слой, начало оси в координатах вида, [(ось, единичное направление на экране, px на единицу мира)])."""
        import numpy as np
        from ..three import NEAR, local_matrix, view_projection, world_matrix
        l = self.ed.current()
        t = self.ed.time
        if l is None or not l.active_at(t) or "position" not in l.props or l.locked:
            return None
        if not (l.threed or l.kind in ("camera", "light")):
            return None
        comp = self.ed.comp
        pos = list(l.props["position"].value_at(l.local(t))) + [0.0]
        pos = np.array(pos[:3], float)
        if l.kind in ("camera", "light"):
            par = np.eye(4)        # shortcut: родитель камеры/света не учитывается, пока его нет в UI гизмо
        else:
            par = world_matrix(l, comp, t) @ np.linalg.inv(local_matrix(l, t, comp.fps))
        vp = view_projection(comp, t)

        def proj(v):
            q = vp @ (par @ np.array([v[0], v[1], v[2], 1.0]))
            return None if q[2] <= NEAR else self._to_view(QPointF(q[0] / q[2], q[1] / q[2]))
        o = proj(pos)
        if o is None:
            return None
        axes = []
        for i in range(3):
            step = 100.0
            q = proj(pos + np.eye(3)[i] * step)
            dv = (q - o) if q is not None else QPointF(0, 0)
            ln = math.hypot(dv.x(), dv.y())
            if ln < 8:      # ось смотрит в камеру (Z при виде спереди): тянем по диагонали, 2 ед. мира на px
                axes.append((i, QPointF(0.7071, -0.7071), 0.5))
            else:
                axes.append((i, QPointF(dv.x() / ln, dv.y() / ln), ln / step))
        return l, o, axes

    def _gizmo_axis_at(self, pos):
        g = self._gizmo()
        if g is None:
            return None
        l, o, axes = g
        best = None
        for i, d, ppu in axes:
            a, b = o + d * 10, o + d * (self.GIZMO_LEN + 6)
            ab = b - a
            tt = max(0.0, min(1.0, QPointF.dotProduct(pos - a, ab) / max(1e-9, QPointF.dotProduct(ab, ab))))
            dist = math.hypot(*((pos - (a + ab * tt)).toTuple()))
            if dist <= 7 and (best is None or dist < best[0]):
                best = (dist, i, d, ppu)
        return None if best is None else (l, best[1], best[2], best[3])

    def _paint_gizmo(self, p):
        g = self._gizmo()
        if g is None:
            return
        _, o, axes = g
        drag_axis = self._drag[2] if self._drag and self._drag[0] == "axis" else None
        hov = self._gizmo_axis_at(self._hover) if self._hover is not None and self._drag is None else None
        for i, d, _ in axes:
            col = QColor(self.GIZMO_COLS[i])
            if i == drag_axis or (hov is not None and hov[1] == i):
                col = col.lighter(150)
            e = o + d * self.GIZMO_LEN
            p.setPen(QPen(col, 2.2))
            p.drawLine(o, e)
            n = QPointF(-d.y(), d.x())
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawPolygon(QPolygonF([e + d * 9, e + n * 4.5, e - n * 4.5]))
            p.setPen(col)
            p.drawText(e + d * 12 + QPointF(-3, 4), "XYZ"[i])

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#191919"))
        comp = self.ed.comp
        if not comp.layers:
            self._paint_welcome(p)
            return
        r = self._view_rect()
        img = self.ed.preview_image(self.ed.time, self.transparency)  # кэш, сразу или в фоновых процессах
        if img is not None:
            self._img = img
        if self._img is None:
            p.drawRect(r)
            return
        if self.transparency:   # шахматка, как Toggle Transparency Grid
            p.save()
            p.setClipRect(r)
            cs = 10
            for iy in range(int(r.height() // cs) + 1):
                for ix in range(int(r.width() // cs) + 1):
                    p.fillRect(QRectF(r.x() + ix * cs, r.y() + iy * cs, cs, cs),
                               QColor("#cfcfcf" if (ix + iy) % 2 else "#ffffff"))
            p.restore()
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage(r, self._img)
        p.setPen(QPen(QColor("#000"), 1))
        p.drawRect(r)
        p.setRenderHint(QPainter.Antialiasing)
        t = self.ed.time
        for layer in self.ed.selected():
            if layer.kind == "light" and layer.active_at(t):
                self._draw_light(p, layer, comp, t)
            if layer.kind in ("audio", "camera", "light") or not layer.active_at(t):
                continue
            world = layer_transform(layer, t, comp)
            pts = [self._to_view(q) for q in layer_corners(layer, t, comp)]
            p.setPen(QPen(theme.c(theme.ACCENT), 1))
            p.setBrush(Qt.NoBrush)
            p.drawPolygon(QPolygonF(pts))
            # ручки масштаба: углы и середины сторон (крупные, как в AE)
            for _, q in self._handles(layer):
                hov = self._hover is not None and abs(q.x() - self._hover.x()) <= 7 and abs(q.y() - self._hover.y()) <= 7
                p.setPen(QPen(theme.c(theme.ACCENT), 1.5))
                p.setBrush(QColor("#ffffff") if hov else theme.c(theme.ACCENT))
                p.drawRect(QRectF(q.x() - 4.5, q.y() - 4.5, 9, 9))
            # опорная точка
            f = layer.local(t)
            a = self._to_view(world.map(QPointF(*layer.props["anchor"].value_at(f)[:2])))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(a, 5, 5)
            p.drawLine(a - QPointF(8, 0), a + QPointF(8, 0))
            p.drawLine(a - QPointF(0, 8), a + QPointF(0, 8))
            # контуры масок и фигур с вершинами
            for _, vals, closed, col in self._editable_paths(layer, f):
                path = path_from(vals, closed)
                self._draw_path(p, path, world, col)
                for v in verts(vals):
                    q = self._to_view(world.map(QPointF(v[0], v[1])))
                    p.setPen(QPen(QColor(col), 1))
                    p.setBrush(QColor("#191919"))
                    p.drawRect(QRectF(q.x() - 3.5, q.y() - 3.5, 7, 7))
            if layer.kind == "shape":
                for sh in layer.shapes:
                    if sh.get("type") != "path":
                        self._draw_path(p, shape_path(layer, sh, f), world, SHAPE_COLOR, dash=True)
        from ..three import view_override
        if view_override(comp):            # в видах Front/Top/Custom камеры видны значком (как в AE)
            from ..three import layer_outlines
            for ol, pts in layer_outlines(comp, t):          # слой ребром к виду виден хотя бы линией
                if ol.id not in self.ed.sel_layers:
                    p.setPen(QPen(QColor(150, 150, 150, 150), 1))
                    p.setBrush(Qt.NoBrush)
                    p.drawPolygon(QPolygonF([self._to_view(QPointF(x, y)) for x, y in pts]))
            for cl in comp.layers:
                if cl.kind == "camera" and cl.active_at(t):
                    self._draw_camera(p, cl, comp, t, cl.id in self.ed.sel_layers)
            p.setPen(QColor(theme.TEXT_DIM))
            p.drawText(QRectF(r.left() + 6, r.bottom() - 20, 300, 18), Qt.AlignLeft, self._view_name(comp))
        if self.ed.tool == "select":
            self._paint_gizmo(p)
        elif self.ed.tool == "rotate":
            self._paint_rings(p)
        # маркеры камеры
        cam = comp.active_camera(t)
        if self.ed.tool == "orbit":
            p.setPen(QColor(theme.TEXT_DIM))
            p.drawText(QRectF(r.left() + 6, r.top() + 4, 400, 18), Qt.AlignLeft,
                       (tr("Камера: {}").format(cam.name) if cam else tr("Нет камеры — потяни, чтобы создать")))
        # перо в процессе
        if self._pen:
            pts = self._pen[1]
            if pts:
                path = QPainterPath()
                for i, v in enumerate(pts):
                    q = self._to_view(QPointF(v[0], v[1]))
                    if i == 0:
                        path.moveTo(q)
                    else:
                        a = pts[i - 1]
                        c1 = self._to_view(QPointF(a[0] + a[4], a[1] + a[5]))
                        c2 = self._to_view(QPointF(v[0] + v[2], v[1] + v[3]))
                        path.cubicTo(c1, c2, q)
                p.setPen(QPen(QColor(MASK_COLOR), 1.3))
                p.setBrush(Qt.NoBrush)
                p.drawPath(path)
                if self._hover is not None:
                    p.setPen(QPen(QColor(MASK_COLOR), 1, Qt.DashLine))
                    p.drawLine(self._to_view(QPointF(pts[-1][0], pts[-1][1])), self._hover)
                for i, v in enumerate(pts):
                    q = self._to_view(QPointF(v[0], v[1]))
                    p.setBrush(QColor(MASK_COLOR) if i == 0 else QColor("#191919"))
                    p.drawRect(QRectF(q.x() - 3.5, q.y() - 3.5, 7, 7))
        # прямоугольник/эллипс в процессе
        d = self._drag
        if d and d[0] == "draw":
            a, b = self._to_view(d[1]), self._to_view(d[2])
            rr = QRectF(a, b).normalized()
            p.setPen(QPen(QColor(MASK_COLOR if d[3] == "mask" else SHAPE_COLOR), 1, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            if d[4] == "ellipse":
                p.drawEllipse(rr)
            else:
                p.drawRect(rr)
        if d and d[0] == "zoombox":
            p.setPen(QPen(theme.c(theme.ACCENT), 1, Qt.DashLine))
            p.drawRect(QRectF(d[1], d[2]).normalized())
        if d and d[0] == "scale" and self._hover is not None:
            sc = d[1].props["scale"].value_at(d[1].local(self.ed.time))
            txt = tr("Масштаб: {:.1f}%, {:.1f}%").format(sc[0], sc[1])
            p.setFont(QFont(self.font().family(), 9))
            r2 = QRectF(self._hover.x() + 14, self._hover.y() + 10, 190, 20)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 170))
            p.drawRoundedRect(r2, 3, 3)
            p.setPen(QColor("#ffffff"))
            p.drawText(r2, Qt.AlignCenter, txt)
        # масштаб вида
        p.setPen(QColor(theme.TEXT_DIM))
        p.setFont(QFont(self.font().family(), 8))
        p.drawText(QRectF(6, self.height() - 18, 200, 16), Qt.AlignLeft,
                   f"{self.view_scale() * 100:.0f}%")

    def _draw_path(self, p, path, world, col, dash=False):
        mapped = world.map(path)
        r = self._view_rect()
        comp = self.ed.comp
        from PySide6.QtGui import QTransform
        to_view = QTransform.fromTranslate(r.x(), r.y())
        to_view.scale(r.width() / comp.width, r.height() / comp.height)
        p.setPen(QPen(QColor(col), 1.2, Qt.DashLine if dash else Qt.SolidLine))
        p.setBrush(Qt.NoBrush)
        p.drawPath(to_view.map(mapped))

    def _editable_paths(self, layer, f):
        """[(ключ свойства, значения, замкнут, цвет)] — контуры, вершины которых можно тянуть."""
        out = []
        for m in layer.masks:
            k = f"mask/{m['id']}/path"
            if k in layer.props:
                out.append((k, layer.props[k].value_at(f), m.get("closed", True), MASK_COLOR))
        for sh in layer.shapes:
            k = f"shape/{sh['id']}/path"
            if sh.get("type") == "path" and k in layer.props:
                out.append((k, layer.props[k].value_at(f), sh.get("closed", True), SHAPE_COLOR))
        return out

    def _paint_welcome(self, p):
        """Пустая композиция — две большие кнопки, как стартовый экран AE."""
        w, h = 200, 150
        gap = 40
        x0 = (self.width() - (2 * w + gap)) / 2
        y0 = (self.height() - h) / 2
        self._welcome = []
        p.setRenderHint(QPainter.Antialiasing)
        for i, (title, sig) in enumerate(((tr("Новая композиция"), "new"),
                                          (tr("Новая композиция\nиз видео"), "footage"))):
            r = QRectF(x0 + i * (w + gap), y0, w, h)
            hover = self._hover is not None and r.contains(self._hover)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#2a2a2a" if hover else "#222222"))
            p.drawRoundedRect(r, 6, 6)
            p.setPen(QPen(QColor("#9a9a9a"), 1.5))
            p.setBrush(Qt.NoBrush)
            ic = QRectF(r.center().x() - 34, r.top() + 26, 68, 44)
            if sig == "footage":
                p.drawRect(ic.adjusted(-10, 6, -30, 6))
                ic = ic.adjusted(10, 0, 10, 0)
            p.drawRect(ic)
            p.drawEllipse(QPointF(ic.center().x() + 6, ic.center().y() + 4), 10, 10)
            p.drawPolygon(QPolygonF([QPointF(ic.left() + 14, ic.bottom() - 6), QPointF(ic.left() + 26, ic.top() + 10),
                                     QPointF(ic.left() + 38, ic.bottom() - 6)]))
            p.setPen(QColor("#d0d0d0"))
            p.drawText(QRectF(r.left(), r.top() + 84, w, 50), Qt.AlignHCenter | Qt.AlignTop, title)
            self._welcome.append((r, sig))
        p.setPen(QColor("#666"))
        p.drawText(QRectF(0, y0 + h + 20, self.width(), 40), Qt.AlignHCenter,
                   tr("…или перетащи видео, картинки и звук в окно (Ctrl+I — импорт)"))

    # --- мышь ----------------------------------------------------------------
    def _layer_at(self, pt):
        for layer in reversed(visible_layers(self.ed.comp, self.ed.time)):
            if not layer.locked and layer_hit(layer, self.ed.time, pt, self.ed.comp):
                return layer
        return None

    def _handles(self, layer):
        """[(id ручки, точка в окне)]: углы nw ne se sw и стороны n e s w — у 2D-слоя."""
        if layer.threed or layer.kind in ("audio", "camera", "light") or len(self.ed.selected()) != 1:
            return []
        t = self.ed.time
        if not layer.active_at(t):
            return []
        world = layer_transform(layer, t, self.ed.comp)
        x, y, w, h = layer.local_rect()
        loc = {"nw": (x, y), "n": (x + w / 2, y), "ne": (x + w, y), "e": (x + w, y + h / 2),
               "se": (x + w, y + h), "s": (x + w / 2, y + h), "sw": (x, y + h), "w": (x, y + h / 2)}
        return [(k, self._to_view(world.map(QPointF(*v)))) for k, v in loc.items()]

    def _handle_at(self, pos):
        for layer in self.ed.selected():
            if layer.locked:
                continue
            for hid, q in self._handles(layer):
                if abs(q.x() - pos.x()) <= 7 and abs(q.y() - pos.y()) <= 7:
                    return layer, hid
        return None

    def _vertex_at(self, pos):
        t = self.ed.time
        for layer in self.ed.selected():
            if layer.kind in ("audio", "camera", "light") or layer.locked:
                continue
            world = layer_transform(layer, t, self.ed.comp)
            f = layer.local(t)
            for key, vals, _, _ in self._editable_paths(layer, f):
                for i, v in enumerate(verts(vals)):
                    q = self._to_view(world.map(QPointF(v[0], v[1])))
                    if abs(q.x() - pos.x()) <= 6 and abs(q.y() - pos.y()) <= 6:
                        return layer, key, i
        return None

    def mousePressEvent(self, e):
        pos = e.position()
        if not self.ed.comp.layers:
            for r, sig in self._welcome:
                if r.contains(pos):
                    (self.new_comp_requested if sig == "new" else self.from_footage_requested).emit()
            return
        if self.ed.color_pick:   # пипетка из Effect Controls: ЛКМ — взять цвет, ПКМ — отмена
            if e.button() == Qt.LeftButton:
                cp = self._to_comp(pos)
                self.ed.pick_color_at(cp.x(), cp.y())
            else:
                self.ed.cancel_color_pick()
            return
        tool = self.ed.tool
        if e.button() == Qt.RightButton and tool != "orbit":
            self.context_requested.emit(e.globalPosition().toPoint(), self._layer_at(self._to_comp(pos)))
            return
        if e.button() == Qt.MiddleButton and tool != "orbit":
            self._drag = ("pan", pos, QPointF(self.vpan))
            return
        if tool == "orbit":
            return self._orbit_press(e)
        if e.button() != Qt.LeftButton:
            return
        pt = self._to_comp(pos)
        if tool == "hand":
            self.setCursor(Qt.ClosedHandCursor)
            self._drag = ("pan", pos, QPointF(self.vpan))
            return
        if tool == "zoom":
            if e.modifiers() & Qt.AltModifier:
                self.zoom_at(1 / 2, pos)
            else:
                self._drag = ("zoombox", pos, pos)
            return
        if tool == "text":
            self.ed.add_text()
            l = self.ed.current()
            if l:
                l.props["position"].value = [round(pt.x(), 1), round(pt.y(), 1)]
                self.ed.notify()
            self.ed.set_tool("select")
            return
        if tool in ("rect", "ellipse", "star"):
            target = self._shape_target()
            self.ed.stop()
            self._drag = ("draw", pt, pt, target, tool)
            return
        if tool == "pen":
            return self._pen_press(pt)
        self._select_press(e, pt, tool)

    def _shape_target(self):
        """Маска — если выделен обычный слой; фигура — если выделен шейп-слой или ничего."""
        cur = self.ed.current()
        if cur is not None and cur.kind not in ("shape", "audio", "camera", "light"):
            return "mask"
        return "shape"

    def _select_press(self, e, pt, tool):
        ed = self.ed
        t = ed.time
        if tool == "rotate":
            ra = self._ring_at(e.position())
            if ra is not None:                 # кольцо 3D-поворота: только это свойство (X/Y/Z Rotation)
                layer, i, tan, ppr = ra
                ed.stop()
                ed.snapshot()
                key = self.RING_KEYS[i]
                self._drag = ("ring", layer, i, e.position(), layer.props[key].value_at(layer.local(t))[0], tan, ppr)
                return
        if tool == "select":
            ga = self._gizmo_axis_at(e.position())
            if ga is not None:                 # тянем стрелку оси: только эта координата Position
                layer, i, d, ppu = ga
                ed.stop()
                ed.snapshot()
                self._drag = ("axis", layer, i, e.position(), list(layer.props["position"].value_at(layer.local(t))), d, ppu)
                return
            hh = self._handle_at(e.position())
            if hh is not None:
                layer, hid = hh
                ed.stop()
                ed.snapshot()
                f = layer.local(t)
                inv, _ = layer_transform(layer, t, ed.comp).inverted()
                x, y, w, h = layer.local_rect()
                hx = {"nw": x, "w": x, "sw": x, "ne": x + w, "e": x + w, "se": x + w}.get(hid)
                hy = {"nw": y, "n": y, "ne": y, "sw": y + h, "s": y + h, "se": y + h}.get(hid)
                self._drag = ("scale", layer, hid, inv, layer.props["anchor"].value_at(f)[:2], hx, hy,
                              layer.props["scale"].value_at(f))
                return
            v = self._vertex_at(e.position())
            if v is not None:
                layer, key, idx = v
                ed.stop()
                ed.snapshot()
                inv, _ = layer_transform(layer, t, ed.comp).inverted()
                self._drag = ("vertex", layer, key, idx, inv)
                return
        hit = self._camera_icon_at(e.position()) or self._layer_at(pt)
        add = bool(e.modifiers() & Qt.ShiftModifier)
        if hit is None:
            if not add:
                ed.deselect()
            return
        if hit.id not in ed.sel_layers or add:
            ed.select([hit.id], add=add)
        if hit.id not in ed.sel_layers:
            return
        ed.stop()
        ed.snapshot()
        sel = ed.selected()
        if tool == "rotate":
            world = layer_transform(hit, t, ed.comp)
            a = world.map(QPointF(*hit.props["anchor"].value_at(hit.local(t))[:2]))
            self._drag = ("rotate", a, math.degrees(math.atan2(pt.y() - a.y(), pt.x() - a.x())),
                          [(l, l.props["rotation"].value_at(l.local(t))[0]) for l in sel])
            return
        if tool == "anchor":
            inv, _ = layer_transform(hit, t, ed.comp).inverted()
            self._drag = ("anchor", hit, inv)
            return
        ids = {l.id for l in sel}
        moving = []
        for l in sel:
            if l.parent in ids:  # ребёнок и так едет вместе с выделенным родителем
                continue
            inv, ok = parent_world(l, ed.comp, t).inverted()
            moving.append((l, l.props["position"].value_at(l.local(t)), inv if ok else None))
        self._drag = ("move", pt, moving)

    # --- перо ---------------------------------------------------------------
    def _pen_press(self, pt):
        if self._pen is None:
            self._pen = (self._shape_target(), [])
        target, pts = self._pen
        if len(pts) >= 2:
            first = self._to_view(QPointF(pts[0][0], pts[0][1]))
            if (first - self._to_view(pt)).manhattanLength() < 10:
                self._finish_pen(closed=True)
                return
        pts.append([pt.x(), pt.y(), 0.0, 0.0, 0.0, 0.0])
        self._drag = ("pen_tangent", len(pts) - 1, pt)
        self.update()

    def _finish_pen(self, closed: bool):
        pen, self._pen = self._pen, None
        if not pen or len(pen[1]) < 2:
            self.update()
            return
        target, pts = pen
        ed = self.ed
        ed.snapshot()
        if target == "mask" and ed.current() is not None:
            layer = ed.current()
            inv, _ = layer_transform(layer, ed.time, ed.comp).inverted()
            vals = []
            for v in pts:
                q = inv.map(QPointF(v[0], v[1]))
                qi = inv.map(QPointF(v[0] + v[2], v[1] + v[3])) - q
                qo = inv.map(QPointF(v[0] + v[4], v[1] + v[5])) - q
                vals += [q.x(), q.y(), qi.x(), qi.y(), qo.x(), qo.y()]
            ed.add_mask(layer, "path", path_vals=vals, closed=closed)
        else:
            layer = ed.current() if (ed.current() and ed.current().kind == "shape") else ed.add_shape_layer()
            inv, _ = layer_transform(layer, ed.time, ed.comp).inverted()
            vals = []
            for v in pts:
                q = inv.map(QPointF(v[0], v[1]))
                vals += [q.x(), q.y(), v[2], v[3], v[4], v[5]]
            ed.add_shape(layer, "path", path_vals=vals, closed=closed)
        ed.notify()
        ed.selection_changed.emit()
        self.update()

    def dragEnterEvent(self, e):
        from .panels import MIME_ITEM
        from .effect_controls import MIME_EFFECT, MIME_PRESET
        md = e.mimeData()
        if md.hasFormat(MIME_ITEM) or md.hasUrls() or md.hasFormat(MIME_EFFECT) or md.hasFormat(MIME_PRESET):
            e.acceptProposedAction()

    def dropEvent(self, e):
        """Из «Проекта» или с диска: слой встаёт в точку, куда отпустили."""
        pt = self._to_comp(e.position())
        w = self.window()
        if hasattr(w, "handle_drop"):
            comp = self.ed.comp
            inside = 0 <= pt.x() <= comp.width and 0 <= pt.y() <= comp.height and comp.layers
            w.handle_drop(e.mimeData(), None, pt if inside else None, self._layer_at(pt))   # время — по настройке
            e.acceptProposedAction()

    def keyPressEvent(self, e):
        if self._pen and e.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._finish_pen(closed=False)
            return
        if self._pen and e.key() == Qt.Key_Escape:
            self._pen = None
            self.update()
            return
        super().keyPressEvent(e)

    # --- камера -------------------------------------------------------------
    def _orbit_press(self, e):
        ed = self.ed
        from ..three import view_override
        v = view_override(ed.comp)
        if v == "custom":                  # в «Своём виде» C облетает сам вид, камера не меняется (как в AE)
            mode = "track" if (e.button() == Qt.MiddleButton or e.modifiers() & Qt.ShiftModifier) else "orbit"
            self._drag = ("view", mode, e.position())
            return
        if v is not None:                  # ортогональный вид: тянуть — сдвиг вида
            self._drag = ("view", "track", e.position())
            return
        cam = ed.comp.active_camera(ed.time)
        if cam is None:
            cam = ed.add_camera()
            self.message.emit(tr("Добавлена камера — 3D-слои теперь видны через неё"))
        ed.stop()
        ed.snapshot()
        btn = e.button()
        mode = "orbit"
        if btn == Qt.MiddleButton or e.modifiers() & Qt.ShiftModifier:
            mode = "track"
        elif btn == Qt.RightButton or e.modifiers() & Qt.ControlModifier:
            mode = "dolly"
        self._drag = ("cam", mode, cam, e.position())

    def wheelEvent(self, e):
        dy = e.angleDelta().y()
        from ..three import view_override, zoom_view
        v = view_override(self.ed.comp)
        if v is not None:                  # в видах Top/Custom… колесо меняет сам вид (кадр вьювера — размером с композицию)
            zoom_view(self.ed.comp, v, 1.1 if dy > 0 else 1 / 1.1)
            self.ed.notify()
            return
        if self.ed.tool == "orbit":
            cam = self.ed.comp.active_camera(self.ed.time)
            if cam is not None:
                from ..three import dolly
                self.ed.snapshot("dolly")
                dolly(cam, self.ed.comp, cam.local(self.ed.time), dy / 120 * 60)
                self.ed.notify()
            return
        self.zoom_at(1.15 if dy > 0 else 1 / 1.15, e.position())

    def mouseDoubleClickEvent(self, e):
        if self.ed.tool in ("hand", "zoom"):
            self.fit()
        elif self.ed.tool == "pen" and self._pen:
            self._finish_pen(closed=False)

    def mouseMoveEvent(self, e):
        pos = e.position()
        self._hover = pos
        d = self._drag
        if d is None:
            if not self.ed.comp.layers or self._pen:
                self.update()
            elif self.ed.tool == "select":
                hh = self._handle_at(pos)
                if self._gizmo_axis_at(pos) is not None:
                    self.setCursor(Qt.SizeAllCursor)
                elif hh is not None:
                    cur = {"n": Qt.SizeVerCursor, "s": Qt.SizeVerCursor, "e": Qt.SizeHorCursor, "w": Qt.SizeHorCursor,
                           "nw": Qt.SizeFDiagCursor, "se": Qt.SizeFDiagCursor}.get(hh[1], Qt.SizeBDiagCursor)
                    self.setCursor(cur)
                else:
                    self.setCursor(Qt.PointingHandCursor if self._vertex_at(pos) else Qt.ArrowCursor)
                self.update()
            elif self.ed.tool == "rotate" and self.ed.current() is not None and self.ed.current().threed:
                self.update()              # подсветка кольца под курсором
            return
        ed = self.ed
        kind = d[0]
        pt = self._to_comp(pos)
        t = ed.time
        if kind == "pan":
            self.vpan = d[2] + (pos - d[1])
            if self.vzoom is None:
                self.vzoom = self._fit_scale()
            self.update()
        elif kind == "zoombox":
            self._drag = ("zoombox", d[1], pos)
            self.update()
        elif kind == "draw":
            b = pt
            if e.modifiers() & Qt.ShiftModifier:  # квадрат / круг
                dx, dy = b.x() - d[1].x(), b.y() - d[1].y()
                s = max(abs(dx), abs(dy))
                b = QPointF(d[1].x() + math.copysign(s, dx), d[1].y() + math.copysign(s, dy))
            self._drag = ("draw", d[1], b, d[3], d[4])
            self.update()
        elif kind == "pen_tangent":
            idx, start = d[1], d[2]
            v = self._pen[1][idx]
            ox, oy = pt.x() - start.x(), pt.y() - start.y()
            if math.hypot(ox, oy) > 2:
                v[2], v[3], v[4], v[5] = -ox, -oy, ox, oy
            self.update()
        elif kind == "ring":
            _, layer, i, p0, r0, tan, ppr = d
            deg = math.degrees(QPointF.dotProduct(pos - p0, tan) / ppr)
            if e.modifiers() & Qt.ShiftModifier:          # Shift — шагом 45°, как у 2D-поворота
                deg = round(deg / 45) * 45
            layer.props[self.RING_KEYS[i]].set_value_at(layer.local(t), [round(r0 + deg, 2)])
            ed.notify()
        elif kind == "axis":
            _, layer, i, p0, v0, dv, ppu = d
            mv = QPointF.dotProduct(pos - p0, dv) / ppu
            if e.modifiers() & Qt.AltModifier:          # Alt — точнее (в 4 раза медленнее), как у масштаба
                mv /= 4
            new = (list(v0) + [0.0, 0.0])[:max(3, len(v0))]
            new[i] = round(v0[i] + mv if i < len(v0) else mv, 1)
            layer.props["position"].set_value_at(layer.local(t), new)
            ed.notify()
        elif kind == "scale":
            _, layer, hid, inv, (ax, ay), hx, hy, s0 = d
            q = inv.map(pt)
            fx = (q.x() - ax) / (hx - ax) if hx is not None and abs(hx - ax) > 1e-6 else 1.0
            fy = (q.y() - ay) / (hy - ay) if hy is not None and abs(hy - ay) > 1e-6 else 1.0
            corner = hx is not None and hy is not None
            if corner and (e.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier)):
                fx = fy = fx if abs(fx - 1) > abs(fy - 1) else fy   # пропорционально
            if e.modifiers() & Qt.AltModifier:                       # Alt — точнее (в 4 раза медленнее)
                fx, fy = 1 + (fx - 1) / 4, 1 + (fy - 1) / 4
            new = list(s0)
            new[0] = round(s0[0] * fx, 1)
            new[1] = round(s0[1] * fy, 1)
            layer.props["scale"].set_value_at(layer.local(t), new)
            ed.notify()
        elif kind == "vertex":
            _, layer, key, idx, inv = d
            q = inv.map(pt)
            prop = layer.props[key]
            lf = layer.local(t)
            vals = prop.value_at(lf)
            vals[idx * 6], vals[idx * 6 + 1] = round(q.x(), 2), round(q.y(), 2)
            prop.set_value_at(lf, vals)
            ed.notify()
        elif kind == "rotate":
            _, a, a0, items = d
            ang = math.degrees(math.atan2(pt.y() - a.y(), pt.x() - a.x()))
            delta = ang - a0
            if e.modifiers() & Qt.ShiftModifier:
                delta = round(delta / 45) * 45
            for l, r0 in items:
                l.props["rotation"].set_value_at(l.local(t), [round(r0 + delta, 2)])
            ed.notify()
        elif kind == "anchor":
            _, layer, inv = d
            q = inv.map(pt)
            f = layer.local(t)
            layer.props["anchor"].set_value_at(f, [round(q.x(), 2), round(q.y(), 2)])
            # компенсируем положение, чтобы картинка слоя осталась на месте (как Pan Behind в AE)
            inv_p, ok = parent_world(layer, ed.comp, t).inverted()
            if ok and not layer.threed:
                pos_v = layer.props["position"].value_at(f)
                cur = layer_transform(layer, t, ed.comp).map(QPointF(q.x(), q.y()))
                diff = inv_p.map(pt) - inv_p.map(cur)
                pos_v[0] += diff.x()
                pos_v[1] += diff.y()
                layer.props["position"].set_value_at(f, [round(v, 2) for v in pos_v])
            ed.notify()
        elif kind == "view":
            from ..three import custom_view, orbit_view
            _, mode, p0 = d
            dx, dy = pos.x() - p0.x(), pos.y() - p0.y()
            self._drag = ("view", mode, pos)
            if mode == "orbit":
                orbit_view(ed.comp, -dx * 0.4, dy * 0.4)
            else:                          # сдвиг вида по экрану (Custom и ортогональные)
                from ..three import pan_view, view_override
                k = ed.comp.width / max(1, self._view_rect().width())
                pan_view(ed.comp, view_override(ed.comp), t, dx * k, dy * k)
            ed.notify()
        elif kind == "cam":
            from ..three import dolly, orbit, track
            _, mode, cam, p0 = d
            dx, dy = pos.x() - p0.x(), pos.y() - p0.y()
            self._drag = ("cam", mode, cam, pos)
            f = cam.local(t)
            s = ed.comp.width / max(1, self._view_rect().width())
            if mode == "orbit":
                orbit(cam, f, -dx * 0.4, dy * 0.4)
            elif mode == "track":
                track(cam, ed.comp, f, dx * s, dy * s)
            else:
                dolly(cam, ed.comp, f, -dy * s * 2)
            ed.notify()
        elif kind == "move":
            start, moving = d[1], d[2]
            dx, dy = pt.x() - start.x(), pt.y() - start.y()
            if e.modifiers() & Qt.ShiftModifier:  # Shift — только по одной оси
                if abs(dx) > abs(dy):
                    dy = 0
                else:
                    dx = 0
            from ..three import camera_params, view_override
            vo = view_override(ed.comp)
            for l, opos, inv in moving:
                if vo and (l.threed or l.kind in ("camera", "light")) and not l.parent:
                    # в виде Top/Left/Custom мышь двигает по осям экрана этого вида, в мировых единицах
                    import numpy as np
                    cpos, basis, zoom = camera_params(ed.comp, t)
                    o3 = np.array((list(opos) + [0.0, 0.0])[:3], float)
                    k = max(1.0, float(basis[2] @ (o3 - cpos))) / zoom
                    n3 = o3 + (basis[0] * dx + basis[1] * dy) * k
                    l.props["position"].set_value_at(l.local(t), [round(float(x), 2) for x in n3])
                    continue
                if inv is not None:  # сдвиг в системе координат родителя
                    dd = inv.map(QPointF(start.x() + dx, start.y() + dy)) - inv.map(start)
                    ddx, ddy = dd.x(), dd.y()
                else:
                    ddx, ddy = dx, dy
                l.props["position"].set_value_at(l.local(t), [round(opos[0] + ddx, 2), round(opos[1] + ddy, 2)])
            ed.notify()

    def mouseReleaseEvent(self, e):
        d = self._drag
        self._drag = None
        if d is None:
            return
        if self.ed.tool == "hand":
            self.setCursor(Qt.OpenHandCursor)
        if d[0] == "zoombox":
            a, b = d[1], d[2]
            if (a - b).manhattanLength() < 6:
                self.zoom_at(2.0, a)
            else:
                rr = QRectF(a, b).normalized()
                ca = self._to_comp(rr.center())
                s = min(self.width() / rr.width(), self.height() / rr.height())
                self.vzoom = max(0.02, min(16.0, self.view_scale() * s))
                self.vpan = QPointF(0, 0)
                self.vpan += QPointF(self.width() / 2, self.height() / 2) - self._to_view(ca)
                self.update()
        elif d[0] == "draw":
            self._finish_draw(d)

    def _finish_draw(self, d):
        _, a, b, target, tool = d
        if abs(b.x() - a.x()) < 3 or abs(b.y() - a.y()) < 3:
            self.update()
            return
        ed = self.ed
        ed.snapshot()
        if target == "mask" and ed.current() is not None:
            layer = ed.current()
            inv, _ = layer_transform(layer, ed.time, ed.comp).inverted()
            qa, qb = inv.map(a), inv.map(b)
            rr = QRectF(qa, qb).normalized()
            ed.add_mask(layer, "ellipse" if tool == "ellipse" else "rect",
                        rect=(rr.x(), rr.y(), rr.width(), rr.height()))
            self.message.emit(tr("Маска добавлена на «{}». M — показать в таймлайне").format(layer.name))
        else:
            cur = ed.current()
            layer = cur if (cur and cur.kind == "shape") else ed.add_shape_layer()
            inv, _ = layer_transform(layer, ed.time, ed.comp).inverted()
            rr = QRectF(inv.map(a), inv.map(b)).normalized()
            ed.add_shape(layer, tool, rect=(rr.x(), rr.y(), rr.width(), rr.height()))
        ed.notify()
        ed.selection_changed.emit()
