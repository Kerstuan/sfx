# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Дополнительные 3D-виды рядом с главным вьювером (раскладки «2 вида» / «4 вида», как Select View Layout в AE).

Каждый боковой вид держит СВОЙ вид, масштаб и сдвиг (не в проекте): при рендере они подставляются в композицию
на время одного кадра и сразу возвращаются. Поэтому прокрутка в боковом виде не меняет проект и не сбрасывает
кэш главного вьювера. Кадр считается в окне программы (не в фоновых процессах) и не пересчитывается во время
проигрывания — только после остановки."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QComboBox, QVBoxLayout, QWidget

from ..i18n import tr
from . import theme

VIEW_NAMES = ("Активная камера", "Спереди", "Слева", "Сверху", "Сзади", "Справа", "Снизу", "Свой вид")


class _Canvas(QWidget):
    def __init__(self, owner):
        super().__init__(owner)
        self.o = owner
        self.setMinimumSize(120, 90)
        self._drag = None

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#191919"))
        img = self.o.image()
        if img is None:
            return
        r = self.o.img_rect()
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage(r, img)
        p.setPen(QPen(QColor("#000"), 1))
        p.drawRect(r)
        p.setRenderHint(QPainter.Antialiasing)
        for pts, sel in self.o.outlines():           # контуры 3D-слоёв: ребром стоящий слой виден линией
            p.setPen(QPen(theme.c(theme.ACCENT) if sel else QColor(150, 150, 150, 150), 1))
            p.drawPolygon(QPolygonF(pts))
        for a, b, sel in self.o.camera_marks():
            p.setPen(QPen(theme.c(theme.ACCENT) if sel else QColor("#b0b0b0"), 1.5))
            if b is not None:
                p.drawLine(a, b)
            p.drawRect(QRectF(a.x() - 7, a.y() - 5, 10, 10))
        p.setPen(QColor(theme.TEXT_DIM))
        p.drawText(QRectF(r.left() + 5, r.bottom() - 18, 260, 16), Qt.AlignLeft, tr(VIEW_NAMES[self.o.view_index()]))

    def wheelEvent(self, e):
        self.o.zoom(1.1 if e.angleDelta().y() > 0 else 1 / 1.1)

    def mousePressEvent(self, e):
        self._drag = (e.button(), e.position())

    def mouseMoveEvent(self, e):
        if self._drag is None:
            return
        btn, p0 = self._drag
        d = e.position() - p0
        self._drag = (btn, e.position())
        if btn == Qt.LeftButton and self.o.view == "custom" and not (e.modifiers() & Qt.ShiftModifier):
            self.o.orbit(-d.x() * 0.4, d.y() * 0.4)
        else:                                  # средняя кнопка / Shift / ортогональный вид — сдвиг вида
            self.o.pan(d.x(), d.y())

    def mouseReleaseEvent(self, e):
        self._drag = None


class SideView(QWidget):
    def __init__(self, editor, view="top", parent=None):
        super().__init__(parent)
        from ..three import VIEWS
        self.ed = editor
        self.view = view
        self.ortho: dict = {}            # {вид: {"scale", "target"}} — только для этого бокового вида
        self.custom = None
        self._img = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.canvas = _Canvas(self)
        lay.addWidget(self.canvas, 1)
        self.combo = QComboBox()
        self.combo.setFocusPolicy(Qt.NoFocus)
        self.combo.addItems([tr(x) for x in VIEW_NAMES])
        self.combo.setCurrentIndex(VIEWS.index(view))
        self.combo.setToolTip(tr("Вид этой панели. Колесо — масштаб, тянуть — сдвиг (в «Своём виде» — облёт, Shift — сдвиг)"))
        self.combo.currentIndexChanged.connect(lambda i: self.set_view(VIEWS[i]))
        lay.addWidget(self.combo)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.refresh)
        editor.changed.connect(self.schedule)
        editor.time_changed.connect(lambda _: self.schedule())
        editor.playing_changed.connect(lambda on: self._timer.stop() if on else self.schedule())

    def view_index(self) -> int:
        from ..three import VIEWS
        return VIEWS.index(self.view)

    def set_view(self, view):
        self.view = view
        self.combo.blockSignals(True)
        self.combo.setCurrentIndex(self.view_index())
        self.combo.blockSignals(False)
        self.refresh()

    # --------------------------------------------------------------- подстановка вида в композицию
    def _swap(self):
        """Композиция временно видится этим видом. Возвращает функцию отката (вид/масштаб панели сохраняются в ней)."""
        c = self.ed.comp
        saved = (getattr(c, "view3d", "active"), getattr(c, "view_ortho", None), getattr(c, "view_custom", None))
        c.view3d, c.view_ortho, c.view_custom = self.view, dict(self.ortho) or None, self.custom

        def back():
            self.ortho = dict(c.view_ortho or {})
            self.custom = c.view_custom
            c.view3d, c.view_ortho, c.view_custom = saved
        return back

    def _ensure_fit(self):
        from ..three import fit_ortho
        if self.view not in ("active", "custom") and self.view not in self.ortho:
            back = self._swap()
            try:
                fit_ortho(self.ed.comp, self.view, self.ed.time)
            finally:
                back()

    # --------------------------------------------------------------- рендер
    def schedule(self):
        if not self.isVisible() or self.ed.timer.isActive():   # проигрывание: догоним после остановки
            return
        self._timer.start(40)

    def refresh(self):
        if self.ed.timer.isActive():       # кадры проигрывания важнее — боковой вид подождёт
            return
        if not self.isVisible() or not self.ed.comp.layers:
            self._img = None
            self.canvas.update()
            return
        from ..render import render_frame
        self._ensure_fit()
        c = self.ed.comp
        sc = max(0.05, min(1.0, min(self.canvas.width() / c.width, self.canvas.height() / c.height)))
        back = self._swap()
        try:
            self._img = render_frame(c, self.ed.time, self.ed.pool, sc)
        finally:
            back()
        self.canvas.update()

    def image(self):
        return self._img

    def img_rect(self) -> QRectF:
        c = self.ed.comp
        W, H = self.canvas.width(), self.canvas.height()
        s = min(W / c.width, H / c.height)
        w, h = c.width * s, c.height * s
        return QRectF((W - w) / 2, (H - h) / 2, w, h)

    def outlines(self):
        from ..three import layer_outlines, view_override
        c, t = self.ed.comp, self.ed.time
        back = self._swap()
        try:
            if view_override(c) is None:
                return []
            res = layer_outlines(c, t)
        finally:
            back()
        r = self.img_rect()
        k = r.width() / c.width
        return [([QPointF(r.left() + x * k, r.top() + y * k) for x, y in pts], l.id in self.ed.sel_layers)
                for l, pts in res]

    def camera_marks(self):
        """Камеры в этом виде на холсте: (позиция, точка интереса | None, выделена)."""
        import numpy as np
        from ..three import NEAR, _xyz, view_override, view_projection
        out = []
        c, t = self.ed.comp, self.ed.time
        back = self._swap()
        try:
            if view_override(c) is None:
                return out
            vp = view_projection(c, t)
        finally:
            back()
        r = self.img_rect()
        k = r.width() / c.width

        def proj(v):
            q = vp @ np.array(list(_xyz(v)) + [1.0])
            return None if q[2] <= NEAR else QPointF(r.left() + q[0] / q[2] * k, r.top() + q[1] / q[2] * k)
        for l in c.layers:
            if l.kind == "camera" and l.active_at(t):
                f = l.local(t)
                a = proj(l.props["position"].value_at(f))
                if a is not None:
                    out.append((a, proj(l.props["poi"].value_at(f)), l.id in self.ed.sel_layers))
        return out

    # --------------------------------------------------------------- навигация
    def _nav(self, fn):
        if self.view == "active":
            return
        self._ensure_fit()
        back = self._swap()
        try:
            fn(self.ed.comp)
        finally:
            back()
        self.refresh()

    def zoom(self, factor):
        from ..three import zoom_view
        self._nav(lambda c: zoom_view(c, self.view, factor))

    def pan(self, dx, dy):
        from ..three import pan_view
        k = self.ed.comp.width / max(1.0, self.img_rect().width())
        self._nav(lambda c: pan_view(c, self.view, self.ed.time, dx * k, dy * k))

    def orbit(self, dx, dy):
        from ..three import orbit_view
        self._nav(lambda c: orbit_view(c, dx, dy))

    def showEvent(self, e):
        super().showEvent(e)
        self.schedule()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.schedule()
