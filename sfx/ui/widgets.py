# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""Общие виджеты в духе AE: протягиваемые числа, секундомер, сворачиваемые секции, индикатор звука, кривая EQ."""
from __future__ import annotations

from ..i18n import tr

import math

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QLabel, QLineEdit, QToolButton, QVBoxLayout, QWidget

from . import theme
from .theme import c


def fmt_angle(v: float) -> str:
    rev = int(v / 360)
    return f"{rev}x{v - rev * 360:+.1f}°"


class ScrubValue(QWidget):
    """Синее число как в AE: тянуть влево-вправо (Shift ×10, Ctrl ×0.1), клик — ввести с клавиатуры.
    Несколько измерений (x, y) — несколько чисел в строку."""

    begin = Signal()
    changed = Signal(list)

    def __init__(self, dims=1, step=1.0, suffix="", angle=False, lo=None, hi=None, parent=None):
        super().__init__(parent)
        self.dims, self.step, self.suffix, self.angle = dims, step, suffix, angle
        self.lo, self.hi = lo, hi
        self.values = [0.0] * dims
        self.expr = False
        self._drag = None
        self._edit = None
        self._rects: list[QRectF] = []
        self.setMinimumHeight(20)
        self.setCursor(Qt.SizeHorCursor)
        self.setFocusPolicy(Qt.NoFocus)

    def set_values(self, vals):
        if self._drag is None and self._edit is None:
            self.values = list(vals)[: self.dims] + [0.0] * max(0, self.dims - len(vals))
            self.update()

    def _texts(self):
        out = []
        for i, v in enumerate(self.values):
            if self.angle:
                out.append(fmt_angle(v))
            else:
                # мелкий шаг — больше знаков (0.35 не должно показываться как 0.3)
                dec = 3 if self.step < 0.009 else 2 if self.step < 0.05 else 1
                out.append(f"{v:.{dec}f}" + (self.suffix if i == len(self.values) - 1 else ""))
        return out

    def sizeHint(self):
        from PySide6.QtCore import QSize
        fm = QFontMetricsF(self.font())
        w = sum(fm.horizontalAdvance(t) + 14 for t in self._texts())
        return QSize(int(w) + 4, 20)

    def paintEvent(self, _):
        p = QPainter(self)
        fm = QFontMetricsF(self.font())
        x = 0.0
        self._rects = []
        col = "#e5677d" if self.expr else theme.VALUE
        for i, t in enumerate(self._texts()):
            w = fm.horizontalAdvance(t) + 4
            r = QRectF(x, 0, w, self.height())
            p.setPen(c(col))
            p.drawText(r, Qt.AlignVCenter | Qt.AlignLeft, t)
            p.setPen(QPen(c(col, 110), 1, Qt.DotLine))
            p.drawLine(QPointF(x, self.height() - 3), QPointF(x + w - 4, self.height() - 3))
            self._rects.append(r)
            x += w
            if i < len(self.values) - 1:
                p.setPen(c(theme.TEXT_DIM))
                p.drawText(QRectF(x, 0, 10, self.height()), Qt.AlignCenter, ",")
                x += 10

    def _dim_at(self, x):
        for i, r in enumerate(self._rects):
            if r.left() - 5 <= x <= r.right() + 5:
                return i
        return None

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        d = self._dim_at(e.position().x())
        if d is None:
            return
        self._drag = (d, e.position().x(), list(self.values), False)

    def mouseMoveEvent(self, e):
        if not self._drag:
            return
        d, x0, orig, moved = self._drag
        dx = e.position().x() - x0
        if not moved:
            if abs(dx) < 3:
                return
            self.begin.emit()
            self._drag = (d, x0, orig, True)
        mult = 10.0 if e.modifiers() & Qt.ShiftModifier else (0.1 if e.modifiers() & Qt.ControlModifier else 1.0)
        v = list(orig)
        v[d] = self._clamp(orig[d] + dx * self.step * mult)
        self.values = v
        self.update()
        self.changed.emit(list(v))

    def _clamp(self, v):
        if self.lo is not None:
            v = max(self.lo, v)
        if self.hi is not None:
            v = min(self.hi, v)
        return round(v, 3)

    def mouseReleaseEvent(self, e):
        if not self._drag:
            return
        d, _, _, moved = self._drag
        self._drag = None
        if not moved:
            self._open_edit(d)

    def _open_edit(self, d):
        r = self._rects[d] if d < len(self._rects) else QRectF(0, 0, 60, 20)
        w = QLineEdit(self)
        w.setText(f"{self.values[d]:.2f}".rstrip("0").rstrip("."))
        w.selectAll()
        w.setGeometry(int(r.x()), 0, max(60, int(r.width()) + 20), self.height())
        w.show()
        w.setFocus()
        self._edit = w

        def done():
            if self._edit is not w:
                return
            self._edit = None
            txt = w.text().replace(",", ".").replace("%", "").replace("°", "").strip()
            w.hide()
            w.deleteLater()
            try:
                val = float(txt)
            except ValueError:
                return
            v = list(self.values)
            v[d] = self._clamp(val)
            self.values = v
            self.begin.emit()
            self.changed.emit(v)
            self.update()
        w.editingFinished.connect(done)


class Stopwatch(QToolButton):
    """Секундомер свойства: клик — анимация вкл/выкл, Alt+клик — выражение."""

    alt_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.on = False
        self.setFixedSize(18, 18)
        self.setFocusPolicy(Qt.NoFocus)
        self.setAutoRaise(True)
        self.setStyleSheet("QToolButton { border:none; background:transparent; padding:0; }")
        self.setToolTip(tr("Анимация вкл/выкл. Alt+клик — выражение"))

    def mousePressEvent(self, e):
        if e.modifiers() & Qt.AltModifier:
            self.alt_clicked.emit()
            return
        super().mousePressEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        col = c(theme.ACCENT if self.on else theme.TEXT_DIM)
        p.setPen(QPen(col, 1.4))
        p.setBrush(c(theme.ACCENT, 70) if self.on else Qt.NoBrush)
        cx, cy = self.width() / 2, self.height() / 2 + 1
        p.drawEllipse(QPointF(cx, cy), 5.5, 5.5)
        p.drawLine(QPointF(cx, cy), QPointF(cx, cy - 3.5))
        p.drawLine(QPointF(cx, cy), QPointF(cx + 2.5, cy))
        p.drawLine(QPointF(cx - 1.5, cy - 7.5), QPointF(cx + 1.5, cy - 7.5))


class Section(QWidget):
    """Сворачиваемая панель справа (Preview, Properties, Align, Audio, Effects & Presets)."""

    def __init__(self, title: str, body: QWidget, opened=True, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.head = QToolButton()
        self.head.setText(title.replace("&", "&&"))  # «&» иначе станет подчёркиванием горячей клавиши
        self.head.setCheckable(True)
        self.head.setChecked(opened)
        self.head.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.head.setArrowType(Qt.DownArrow if opened else Qt.RightArrow)
        self.head.setSizePolicy(self.head.sizePolicy().horizontalPolicy().Expanding,
                                self.head.sizePolicy().verticalPolicy())
        self.head.setStyleSheet("QToolButton { background:#1e1e1e; border:none; border-bottom:1px solid #111;"
                                " padding:5px 6px; text-align:left; font-weight:bold; color:#e6e6e6; }"
                                " QToolButton:checked { background:#1e1e1e; color:#e6e6e6; }")
        self.head.setFocusPolicy(Qt.NoFocus)
        self.head.toggled.connect(self._toggle)
        self.body = body
        lay.addWidget(self.head)
        lay.addWidget(body)
        body.setVisible(opened)

    def _toggle(self, on):
        self.head.setArrowType(Qt.DownArrow if on else Qt.RightArrow)
        self.body.setVisible(on)

    def set_open(self, on: bool):
        self.head.setChecked(on)


class LevelMeter(QWidget):
    """Индикатор громкости L/R в дБ, с удержанием пика (как панель Audio в AE)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.levels = [0.0, 0.0]
        self.peaks = [0.0, 0.0]
        self.setMinimumSize(90, 150)

    def set_levels(self, l, r):
        self.levels = [l, r]
        self.peaks = [max(p * 0.97, v) for p, v in zip(self.peaks, self.levels)]
        self.update()

    @staticmethod
    def _db(v):
        return 20 * math.log10(max(1e-6, v))

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), c("#181818"))
        top, bot = 8, self.height() - 8
        h = bot - top

        def y_of(db):
            return top + (6 - max(-48, min(6, db))) / 54 * h

        p.setFont(QFont(self.font().family(), 7))
        for db in (6, 0, -6, -12, -18, -24, -36, -48):
            y = y_of(db)
            p.setPen(c("#555"))
            p.drawLine(QPointF(4, y), QPointF(10, y))
            p.setPen(c(theme.TEXT_DIM))
            p.drawText(QRectF(12, y - 7, 30, 14), Qt.AlignVCenter | Qt.AlignLeft, f"{db:+d}" if db else "0")
        bw = (self.width() - 52) / 2 - 3
        for i, (lv, pk) in enumerate(zip(self.levels, self.peaks)):
            x = 46 + i * (bw + 6)
            p.fillRect(QRectF(x, top, bw, h), c("#101010"))
            db = self._db(lv)
            y = y_of(db)
            # зелёный до -12, жёлтый до 0, красный выше
            for lo, hi, col in ((-48, -12, "#3fbf5a"), (-12, 0, "#e0c040"), (0, 6, "#e04040")):
                if db > lo:
                    y0, y1 = y_of(min(db, hi)), y_of(lo)
                    p.fillRect(QRectF(x, y0, bw, y1 - y0), c(col))
            py = y_of(self._db(pk))
            p.fillRect(QRectF(x, py - 1, bw, 2), c("#ffffff"))
        p.setPen(c(theme.TEXT_DIM))
        p.drawText(QRectF(46, bot - 2, bw, 12), Qt.AlignCenter, "L")
        p.drawText(QRectF(46 + bw + 6, bot - 2, bw, 12), Qt.AlignCenter, "R")


# ---------------------------------------------------------------- кривая параметрического эквалайзера
def _biquad_db(kind, f0, gain, q, freqs, sr=48000.0):
    """АЧХ биквада (формулы RBJ) в дБ на частотах freqs."""
    a = 10 ** (gain / 40)
    w0 = 2 * math.pi * f0 / sr
    cw, sw = math.cos(w0), math.sin(w0)
    if kind == "peak":
        alpha = sw / (2 * q)
        b0, b1, b2 = 1 + alpha * a, -2 * cw, 1 - alpha * a
        a0, a1, a2 = 1 + alpha / a, -2 * cw, 1 - alpha / a
    else:
        alpha = sw / 2 * math.sqrt(2)  # S = 1
        sa = 2 * math.sqrt(a) * alpha
        if kind == "low":
            b0 = a * ((a + 1) - (a - 1) * cw + sa)
            b1 = 2 * a * ((a - 1) - (a + 1) * cw)
            b2 = a * ((a + 1) - (a - 1) * cw - sa)
            a0 = (a + 1) + (a - 1) * cw + sa
            a1 = -2 * ((a - 1) + (a + 1) * cw)
            a2 = (a + 1) + (a - 1) * cw - sa
        else:
            b0 = a * ((a + 1) + (a - 1) * cw + sa)
            b1 = -2 * a * ((a - 1) + (a + 1) * cw)
            b2 = a * ((a + 1) + (a - 1) * cw - sa)
            a0 = (a + 1) - (a - 1) * cw + sa
            a1 = 2 * ((a - 1) - (a + 1) * cw)
            a2 = (a + 1) - (a - 1) * cw - sa
    z = np.exp(-1j * 2 * np.pi * freqs / sr)
    hz = (b0 + b1 * z + b2 * z * z) / (a0 + a1 * z + a2 * z * z)
    return 20 * np.log10(np.maximum(1e-9, np.abs(hz)))


class EQCurve(QWidget):
    """АЧХ параметрического эквалайзера; точки полос тянутся мышью (X — частота, Y — усиление, колесо — Q)."""

    begin = Signal()
    changed = Signal(str, float)   # ключ параметра (low_f, m1_g…), значение

    BANDS = [("low", "low_f", "low_g", None, "#e8a33d"), ("peak", "m1_f", "m1_g", "m1_q", "#5fc35f"),
             ("peak", "m2_f", "m2_g", "m2_q", "#4ab0e8"), ("peak", "m3_f", "m3_g", "m3_q", "#b07ae8"),
             ("high", "high_f", "high_g", None, "#e86a6a")]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.vals: dict[str, float] = {}
        self.setMinimumHeight(130)
        self._drag = None
        self.setMouseTracking(True)

    def set_values(self, vals: dict):
        if self._drag is None:
            self.vals = dict(vals)
            self.update()

    def _f2x(self, f):
        return 30 + (math.log10(f) - math.log10(20)) / (math.log10(20000) - math.log10(20)) * (self.width() - 40)

    def _x2f(self, x):
        t = (x - 30) / max(1, self.width() - 40)
        return 10 ** (math.log10(20) + t * (math.log10(20000) - math.log10(20)))

    def _g2y(self, g):
        return 8 + (24 - g) / 48 * (self.height() - 16)

    def _y2g(self, y):
        return 24 - (y - 8) / max(1, self.height() - 16) * 48

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), c("#141414"))
        p.setFont(QFont(self.font().family(), 7))
        for f in (50, 100, 200, 500, 1000, 2000, 5000, 10000):
            x = self._f2x(f)
            p.setPen(c("#262626"))
            p.drawLine(QPointF(x, 4), QPointF(x, self.height() - 4))
            p.setPen(c("#666"))
            p.drawText(QPointF(x + 2, self.height() - 3), f"{f // 1000}k" if f >= 1000 else str(f))
        for g in (-18, -12, -6, 0, 6, 12, 18):
            y = self._g2y(g)
            p.setPen(c("#3a3a3a" if g == 0 else "#222"))
            p.drawLine(QPointF(30, y), QPointF(self.width() - 6, y))
            p.setPen(c("#666"))
            p.drawText(QRectF(0, y - 6, 26, 12), Qt.AlignRight | Qt.AlignVCenter, f"{g:+d}" if g else "0")
        if not self.vals:
            return
        n = max(64, self.width() - 40)
        freqs = np.logspace(math.log10(20), math.log10(20000), n)
        total = np.zeros(n)
        for kind, fk, gk, qk, _ in self.BANDS:
            g = self.vals.get(gk, 0.0)
            if abs(g) > 0.01:
                total += _biquad_db(kind, self.vals.get(fk, 1000.0), g, self.vals.get(qk, 1.0) if qk else 0.707, freqs)
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        for i, (f, g) in enumerate(zip(freqs, total)):
            pt = QPointF(self._f2x(f), self._g2y(max(-24, min(24, g))))
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        fill = QPainterPath(path)
        fill.lineTo(QPointF(self._f2x(20000), self._g2y(0)))
        fill.lineTo(QPointF(self._f2x(20), self._g2y(0)))
        p.fillPath(fill, c(theme.ACCENT, 40))
        p.setPen(QPen(c("#e6e6e6"), 1.6))
        p.drawPath(path)
        for i, (kind, fk, gk, qk, col) in enumerate(self.BANDS):
            x, y = self._f2x(self.vals.get(fk, 1000.0)), self._g2y(self.vals.get(gk, 0.0))
            p.setPen(QPen(c("#000"), 1))
            p.setBrush(c(col))
            p.drawEllipse(QPointF(x, y), 5.5, 5.5)
            p.setPen(c("#000"))
            p.drawText(QRectF(x - 6, y - 6, 12, 12), Qt.AlignCenter, str(i + 1))

    def _band_at(self, pos):
        best, bd = None, 12.0
        for i, (kind, fk, gk, qk, _) in enumerate(self.BANDS):
            x, y = self._f2x(self.vals.get(fk, 1000.0)), self._g2y(self.vals.get(gk, 0.0))
            d = math.hypot(pos.x() - x, pos.y() - y)
            if d < bd:
                best, bd = i, d
        return best

    def mousePressEvent(self, e):
        b = self._band_at(e.position())
        if b is not None:
            self.begin.emit()
            self._drag = b

    def mouseMoveEvent(self, e):
        if self._drag is None:
            self.setCursor(Qt.PointingHandCursor if self._band_at(e.position()) is not None else Qt.ArrowCursor)
            return
        kind, fk, gk, qk, _ = self.BANDS[self._drag]
        f = max(20.0, min(20000.0, self._x2f(e.position().x())))
        g = max(-24.0, min(24.0, self._y2g(e.position().y())))
        if e.modifiers() & Qt.ShiftModifier:
            g = round(g)
        self.vals[fk], self.vals[gk] = round(f, 1), round(g, 2)
        self.update()
        self.changed.emit(fk, self.vals[fk])
        self.changed.emit(gk, self.vals[gk])

    def mouseReleaseEvent(self, e):
        self._drag = None

    def wheelEvent(self, e):
        b = self._band_at(e.position())
        if b is None:
            return
        qk = self.BANDS[b][3]
        if not qk:
            return
        q = self.vals.get(qk, 1.0) * (1.15 if e.angleDelta().y() > 0 else 1 / 1.15)
        self.begin.emit()
        self.vals[qk] = round(max(0.1, min(20.0, q)), 3)
        self.update()
        self.changed.emit(qk, self.vals[qk])


class Hint(QLabel):
    def __init__(self, text, parent=None):
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setStyleSheet("color:#7a7a7a; font-size:11px; padding:2px 4px;")


class CurvesWidget(QWidget):
    """Кривые: клик — новая точка, тянуть — двигать, правая кнопка — удалить точку."""

    begin = Signal()
    changed = Signal(list)

    COLORS = {"rgb": "#e6e6e6", "r": "#e05757", "g": "#5fc35f", "b": "#5a8ef0"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.points = [[0.0, 0.0], [1.0, 1.0]]
        self.channel = "rgb"
        self._drag = None
        self.setMinimumHeight(190)
        self.setMouseTracking(True)

    def set_curve(self, channel, points):
        if self._drag is None:
            self.channel = channel
            self.points = [list(p) for p in points]
            self.update()

    def _r(self):
        s = min(self.width() - 16, self.height() - 16)
        return QRectF((self.width() - s) / 2, 8, s, s)

    def _to(self, x, y):
        r = self._r()
        return QPointF(r.left() + x * r.width(), r.bottom() - y * r.height())

    def _from(self, pt):
        r = self._r()
        return [max(0.0, min(1.0, (pt.x() - r.left()) / r.width())), max(0.0, min(1.0, (r.bottom() - pt.y()) / r.height()))]

    def paintEvent(self, _):
        from ..effects import curve_lut
        p = QPainter(self)
        p.fillRect(self.rect(), c("#141414"))
        r = self._r()
        p.setPen(c("#262626"))
        for i in range(1, 4):
            p.drawLine(QPointF(r.left() + r.width() * i / 4, r.top()), QPointF(r.left() + r.width() * i / 4, r.bottom()))
            p.drawLine(QPointF(r.left(), r.top() + r.height() * i / 4), QPointF(r.right(), r.top() + r.height() * i / 4))
        p.setPen(c("#3a3a3a"))
        p.drawRect(r)
        p.drawLine(self._to(0, 0), self._to(1, 1))
        p.setRenderHint(QPainter.Antialiasing)
        lut = curve_lut(self.points)
        path = QPainterPath()
        for i, v in enumerate(lut):
            pt = self._to(i / 255, float(v))
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        p.setPen(QPen(c(self.COLORS.get(self.channel, "#e6e6e6")), 1.6))
        p.drawPath(path)
        for x, y in self.points:
            q = self._to(x, y)
            p.setPen(QPen(c("#000"), 1))
            p.setBrush(c(self.COLORS.get(self.channel, "#e6e6e6")))
            p.drawRect(QRectF(q.x() - 4, q.y() - 4, 8, 8))

    def _hit(self, pos):
        for i, (x, y) in enumerate(self.points):
            q = self._to(x, y)
            if abs(q.x() - pos.x()) <= 7 and abs(q.y() - pos.y()) <= 7:
                return i
        return None

    def mousePressEvent(self, e):
        i = self._hit(e.position())
        if e.button() == Qt.RightButton:
            if i is not None and 0 < i < len(self.points) - 1 or (i is not None and len(self.points) > 2):
                self.begin.emit()
                self.points.pop(i)
                self.update()
                self.changed.emit([list(p) for p in self.points])
            return
        self.begin.emit()
        if i is None:
            self.points.append(self._from(e.position()))
            self.points.sort()
            i = self._hit(e.position())
        self._drag = i
        self.update()

    def mouseMoveEvent(self, e):
        if self._drag is None:
            self.setCursor(Qt.PointingHandCursor if self._hit(e.position()) is not None else Qt.CrossCursor)
            return
        x, y = self._from(e.position())
        i = self._drag
        lo = self.points[i - 1][0] + 0.01 if i > 0 else 0.0
        hi = self.points[i + 1][0] - 0.01 if i < len(self.points) - 1 else 1.0
        self.points[i] = [max(lo, min(hi, x)), y]
        self.update()
        self.changed.emit([list(p) for p in self.points])

    def mouseReleaseEvent(self, e):
        self._drag = None
