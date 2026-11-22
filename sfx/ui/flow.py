# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Панель Flow — как одноимённый плагин для AE.

Кривая сегмента между ключами в виде cubic-bezier(x1, y1, x2, y2), библиотека
пресетов (Sine/Quad/Cubic/.../Back), свои пресеты, «Применить» и «Прочитать с ключей».
Связь с моделью: k0.out = (x1, y1), k1.in = (1 - x2, 1 - y2).
"""
from __future__ import annotations

import json
import os
import time

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListView, QListWidget,
                               QListWidgetItem, QMenu, QPushButton, QToolButton, QVBoxLayout, QWidget)

from ..i18n import tr
from ..model import bezier_ease
from . import theme

T = 1 / 3
BUILTIN = [
    ("Linear", (T, T, 1 - T, 1 - T)),
    ("Easy Ease", (T, 0.0, 1 - T, 1.0)),
    ("Easy Ease In", (T, T, 1 - T, 1.0)),
    ("Easy Ease Out", (T, 0.0, 1 - T, 1 - T)),
    ("Sine In", (0.12, 0.0, 0.39, 0.0)),
    ("Sine Out", (0.61, 1.0, 0.88, 1.0)),
    ("Sine InOut", (0.37, 0.0, 0.63, 1.0)),
    ("Quad In", (0.11, 0.0, 0.5, 0.0)),
    ("Quad Out", (0.5, 1.0, 0.89, 1.0)),
    ("Quad InOut", (0.45, 0.0, 0.55, 1.0)),
    ("Cubic In", (0.32, 0.0, 0.67, 0.0)),
    ("Cubic Out", (0.33, 1.0, 0.68, 1.0)),
    ("Cubic InOut", (0.65, 0.0, 0.35, 1.0)),
    ("Quart In", (0.5, 0.0, 0.75, 0.0)),
    ("Quart Out", (0.25, 1.0, 0.5, 1.0)),
    ("Quart InOut", (0.76, 0.0, 0.24, 1.0)),
    ("Quint In", (0.64, 0.0, 0.78, 0.0)),
    ("Quint Out", (0.22, 1.0, 0.36, 1.0)),
    ("Quint InOut", (0.83, 0.0, 0.17, 1.0)),
    ("Expo In", (0.7, 0.0, 0.84, 0.0)),
    ("Expo Out", (0.16, 1.0, 0.3, 1.0)),
    ("Expo InOut", (0.87, 0.0, 0.13, 1.0)),
    ("Circ In", (0.55, 0.0, 1.0, 0.45)),
    ("Circ Out", (0.0, 0.55, 0.45, 1.0)),
    ("Circ InOut", (0.85, 0.0, 0.15, 1.0)),
    ("Back In", (0.36, 0.0, 0.66, -0.56)),
    ("Back Out", (0.34, 1.56, 0.64, 1.0)),
    ("Back InOut", (0.68, -0.6, 0.32, 1.6)),
    ("Snap", (0.9, 0.0, 0.1, 1.0)),
    ("Whip", (1.0, 0.0, 0.0, 1.0)),
]
Y_LO, Y_HI = -0.7, 1.7  # видимый диапазон по вертикали (для перелётов Back)


def presets_path() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "sfx", "flow_presets.json")


def load_user_presets() -> list[tuple[str, tuple]]:
    try:
        with open(presets_path(), encoding="utf-8") as f:
            data = json.load(f)
        return [(str(d["name"]), tuple(float(v) for v in d["curve"][:4])) for d in data]
    except (OSError, ValueError, KeyError, TypeError):
        return []


def save_user_presets(items):
    path = presets_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump([{"name": n, "curve": list(c)} for n, c in items], f, ensure_ascii=False, indent=1)


def fmt_curve(c) -> str:
    return ", ".join(f"{v:.2f}" for v in c)


def parse_curve(text: str):
    """«0.33, 0, 0.67, 1» или «cubic-bezier(.33,0,.67,1)» -> кортеж или None."""
    t = text.strip()
    if "(" in t:
        t = t[t.index("(") + 1:].rstrip(") ;")
    try:
        vals = [float(v) for v in t.replace(";", ",").split(",") if v.strip()]
    except ValueError:
        return None
    if len(vals) != 4:
        return None
    x1, y1, x2, y2 = vals
    return (min(1.0, max(0.0, x1)), y1, min(1.0, max(0.0, x2)), y2)


def curve_icon(c, size=44, color=theme.ACCENT) -> QIcon:
    pm = QPixmap(size, size)
    pm.fill(QColor("#1b1b1b"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    m = 6
    w = size - 2 * m

    # компактно: 0..1 по y плюс запас для перелёта
    def pt2(x, y):
        lo, hi = min(-0.1, min(c[1], c[3]) - 0.05), max(1.1, max(c[1], c[3]) + 0.05)
        return QPointF(m + x * w, m + w - (y - lo) / (hi - lo) * w)

    p.setPen(QPen(QColor("#333"), 1))
    p.drawLine(pt2(0, 0), pt2(1, 0))
    p.drawLine(pt2(0, 1), pt2(1, 1))
    path = QPainterPath(pt2(0, 0))
    path.cubicTo(pt2(c[0], c[1]), pt2(c[2], c[3]), pt2(1, 1))
    p.setPen(QPen(QColor(color), 2))
    p.drawPath(path)
    p.end()
    return QIcon(pm)


class FlowCurve(QWidget):
    """Большой редактор кривой: тянем две ручки, двойной щелчок — Easy Ease."""

    def __init__(self, on_change, parent=None):
        super().__init__(parent)
        self.c = list(BUILTIN[1][1])
        self.on_change = on_change
        self._drag = None
        self.setMinimumSize(200, 200)
        self.setMouseTracking(True)

    def set_curve(self, c):
        self.c = list(c)
        self.update()

    def heightForWidth(self, w):
        return w

    def hasHeightForWidth(self):
        return True

    def _rect(self) -> QRectF:
        s = min(self.width(), self.height()) - 16
        return QRectF((self.width() - s) / 2, (self.height() - s) / 2, s, s)

    def _to_px(self, x, y) -> QPointF:
        r = self._rect()
        return QPointF(r.left() + x * r.width(), r.bottom() - (y - Y_LO) / (Y_HI - Y_LO) * r.height())

    def _from_px(self, p):
        r = self._rect()
        x = (p.x() - r.left()) / r.width()
        y = Y_LO + (r.bottom() - p.y()) / r.height() * (Y_HI - Y_LO)
        return min(1.0, max(0.0, x)), min(Y_HI, max(Y_LO, y))

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.PANEL))
        r = self._rect()
        p.fillRect(r, QColor("#1b1b1b"))
        a, b = self._to_px(0, 0), self._to_px(1, 1)
        p.fillRect(QRectF(QPointF(a.x(), b.y()), QPointF(b.x(), a.y())), QColor("#202020"))
        p.setPen(QPen(QColor("#2c2c2c"), 1))
        for i in range(1, 4):
            x = r.left() + r.width() * i / 4
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
        for y in (0.25, 0.5, 0.75):
            q = self._to_px(0, y)
            p.drawLine(QPointF(r.left(), q.y()), QPointF(r.right(), q.y()))
        p.setPen(QPen(QColor("#444"), 1))
        for y in (0.0, 1.0):
            q = self._to_px(0, y)
            p.drawLine(QPointF(r.left(), q.y()), QPointF(r.right(), q.y()))
        p.setPen(QPen(QColor("#3a3a3a"), 1, Qt.DashLine))
        p.drawLine(a, b)
        x1, y1, x2, y2 = self.c
        h1, h2 = self._to_px(x1, y1), self._to_px(x2, y2)
        p.setPen(QPen(QColor(theme.TEXT_DIM), 1))
        p.drawLine(a, h1)
        p.drawLine(b, h2)
        path = QPainterPath(a)
        path.cubicTo(h1, h2, b)
        p.setPen(QPen(QColor(theme.ACCENT), 2.5))
        p.drawPath(path)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(theme.TEXT_HI))
        p.drawEllipse(a, 3, 3)
        p.drawEllipse(b, 3, 3)
        for i, h in enumerate((h1, h2)):
            p.setBrush(QColor(theme.CURVE_1 if self._drag == i else theme.ACCENT))
            p.setPen(QPen(QColor(theme.TEXT_HI), 1.5))
            p.drawEllipse(h, 6, 6)
        p.setPen(QColor(theme.TEXT_DIM))
        p.drawText(QRectF(r.left() + 4, r.top() + 2, r.width() - 8, 16), Qt.AlignLeft,
                   "cubic-bezier(" + fmt_curve(self.c) + ")")
        p.end()

    def _hit(self, pos):
        best, bd = None, 14 ** 2
        for i in (0, 1):
            h = self._to_px(self.c[2 * i], self.c[2 * i + 1])
            d = (h.x() - pos.x()) ** 2 + (h.y() - pos.y()) ** 2
            if d < bd:
                best, bd = i, d
        return best

    def mousePressEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        hit = self._hit(e.position())
        if hit is None:  # щелчок мимо — хватаем ближайшую ручку
            x, _y = self._from_px(e.position())
            hit = 0 if x < 0.5 else 1
        self._drag = hit
        self.mouseMoveEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag is None:
            self.setCursor(Qt.PointingHandCursor if self._hit(e.position()) is not None else Qt.ArrowCursor)
            return
        x, y = self._from_px(e.position())
        if e.modifiers() & Qt.ShiftModifier:  # Shift — ручка горизонтально (чистый ease)
            y = 0.0 if self._drag == 0 else 1.0
        self.c[2 * self._drag], self.c[2 * self._drag + 1] = round(x, 3), round(y, 3)
        self.update()
        self.on_change(tuple(self.c), False)

    def mouseReleaseEvent(self, _e):
        if self._drag is not None:
            self._drag = None
            self.update()
            self.on_change(tuple(self.c), True)

    def mouseDoubleClickEvent(self, _e):
        self.c = list(BUILTIN[1][1])
        self.update()
        self.on_change(tuple(self.c), True)


class FlowPreview(QWidget):
    """Шарик, бегущий по кривой (зацикленно), чтобы видеть характер движения."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.c = BUILTIN[1][1]
        self.setFixedHeight(26)
        self.t0 = time.monotonic()
        self.timer = QTimer(self)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self.update)

    def showEvent(self, e):
        self.timer.start()
        super().showEvent(e)

    def hideEvent(self, e):
        self.timer.stop()
        super().hideEvent(e)

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#1b1b1b"))
        m = 12
        w = self.width() - 2 * m
        cy = self.height() / 2
        p.setPen(QPen(QColor("#333"), 1))
        p.drawLine(QPointF(m, cy), QPointF(m + w, cy))
        cyc = (time.monotonic() - self.t0) % 1.6
        u = min(1.0, cyc / 1.0)
        for k in range(6, 0, -1):  # шлейф
            uu = max(0.0, u - k * 0.025)
            v = bezier_ease(uu, *self.c)
            col = QColor(theme.ACCENT)
            col.setAlpha(25 + 10 * (6 - k))
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawEllipse(QPointF(m + v * w, cy), 5, 5)
        v = bezier_ease(u, *self.c)
        p.setBrush(QColor(theme.TEXT_HI))
        p.drawEllipse(QPointF(m + v * w, cy), 6, 6)
        p.end()


class FlowPanel(QWidget):
    def __init__(self, editor, on_message=None, parent=None):
        super().__init__(parent)
        self.ed = editor
        self.msg = on_message or (lambda m: None)
        self.user = load_user_presets()
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.setSpacing(4)

        self.curve = FlowCurve(self._curve_changed)
        v.addWidget(self.curve)
        self.preview = FlowPreview()
        v.addWidget(self.preview)

        row = QHBoxLayout()
        self.edit = QLineEdit(fmt_curve(self.curve.c))
        self.edit.setToolTip(tr("Значения кривой x1, y1, x2, y2 (можно вставить cubic-bezier из CSS)"))
        self.edit.editingFinished.connect(self._edit_done)
        row.addWidget(self.edit, 1)
        cp = QToolButton()
        cp.setText("⧉")
        cp.setToolTip(tr("Копировать значения"))
        cp.setFocusPolicy(Qt.NoFocus)
        cp.clicked.connect(lambda: QApplication.clipboard().setText(self.edit.text()))
        row.addWidget(cp)
        v.addLayout(row)

        row = QHBoxLayout()
        self.apply_btn = QPushButton(tr("Применить"))
        self.apply_btn.setToolTip(tr("Применить кривую к выделенным ключам (между соседними выделенными)"))
        self.apply_btn.setStyleSheet(f"QPushButton {{ background:{theme.ACCENT}; color:white; font-weight:bold;"
                                     " padding:4px 10px; } QPushButton:disabled { background:#333; color:#777; }")
        self.apply_btn.clicked.connect(self.apply)
        row.addWidget(self.apply_btn, 1)
        self.read_btn = QPushButton(tr("Прочитать"))
        self.read_btn.setToolTip(tr("Взять кривую с выделенных ключей"))
        self.read_btn.clicked.connect(self.read)
        row.addWidget(self.read_btn)
        save = QPushButton("+")
        save.setFixedWidth(28)
        save.setToolTip(tr("Сохранить кривую как свой пресет"))
        save.clicked.connect(self.save_preset)
        row.addWidget(save)
        v.addLayout(row)

        self.info = QLabel()
        self.info.setStyleSheet(f"color:{theme.TEXT_DIM};")
        self.info.setWordWrap(True)
        v.addWidget(self.info)

        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Поиск пресета…"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._fill)
        v.addWidget(self.search)

        self.list = QListWidget()
        self.list.setViewMode(QListView.IconMode)
        self.list.setIconSize(QSize(44, 44))
        self.list.setGridSize(QSize(74, 84))
        self.list.setTextElideMode(Qt.ElideNone)
        self.list.setResizeMode(QListView.Adjust)
        self.list.setMovement(QListView.Static)
        self.list.setWordWrap(True)
        self.list.setMinimumHeight(220)
        self.list.setFocusPolicy(Qt.NoFocus)
        self.list.setStyleSheet("QListWidget { background:#1b1b1b; } QListWidget::item { font-size:10px; color:#c8c8c8; }"
                                " QListWidget::item:selected { background:#2b3a4a; color:white; }")
        self.list.itemClicked.connect(self._pick)
        self.list.itemDoubleClicked.connect(lambda it: (self._pick(it), self.apply()))
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        v.addWidget(self.list, 1)
        self._fill()

        editor.changed.connect(self._sync)
        self._sync()

    # --- список пресетов
    def _fill(self, *_):
        q = self.search.text().strip().lower()
        self.list.clear()
        for user, items in ((True, self.user), (False, BUILTIN)):
            for name, c in items:
                if q and q not in name.lower():
                    continue
                it = QListWidgetItem(curve_icon(c, color=theme.CURVE_1 if user else theme.ACCENT), name)
                it.setData(Qt.UserRole, (name, c, user))
                it.setToolTip(f"{name}\ncubic-bezier({fmt_curve(c)})" +
                              ("\n" + tr("Свой пресет") if user else ""))
                self.list.addItem(it)

    def _pick(self, it):
        _name, c, _user = it.data(Qt.UserRole)
        self.set_curve(c)

    def _menu(self, pos):
        it = self.list.itemAt(pos)
        if not it:
            return
        name, c, user = it.data(Qt.UserRole)
        m = QMenu(self)
        m.setAttribute(Qt.WA_DeleteOnClose)   # иначе каждое меню ПКМ остаётся ребёнком окна навсегда
        m.addAction(tr("Применить"), lambda: (self.set_curve(c), self.apply()))
        m.addAction(tr("Копировать значения"), lambda: QApplication.clipboard().setText(fmt_curve(c)))
        if user:
            m.addSeparator()
            m.addAction(tr("Удалить пресет"), lambda: self.delete_preset(name))
        m.exec(self.list.mapToGlobal(pos))

    def save_preset(self, name: str | None = None):
        if name is None:
            name, ok = QInputDialog.getText(self, tr("Сохранить кривую"), tr("Имя пресета:"))
            if not ok:
                return
        name = name.strip()
        if not name:
            return
        self.user = [(n, c) for n, c in self.user if n != name] + [(name, tuple(self.curve.c))]
        try:
            save_user_presets(self.user)
        except OSError as e:
            self.msg(str(e))
        self._fill()

    def delete_preset(self, name):
        self.user = [(n, c) for n, c in self.user if n != name]
        try:
            save_user_presets(self.user)
        except OSError as e:
            self.msg(str(e))
        self._fill()

    # --- кривая
    def set_curve(self, c):
        self.curve.set_curve(c)
        self._curve_changed(tuple(c), True)

    def _curve_changed(self, c, _final):
        self.preview.c = c
        self.edit.setText(fmt_curve(c))

    def _edit_done(self):
        c = parse_curve(self.edit.text())
        if c is None:
            self.edit.setText(fmt_curve(self.curve.c))
            return
        self.set_curve(c)

    def apply(self):
        n = self.ed.apply_curve(tuple(self.curve.c))
        self.msg(tr("Flow: кривая применена к сегментам: {n}").format(n=n) if n
                 else tr("Flow: выделите ключи на таймлайне"))

    def read(self):
        c = self.ed.read_curve()
        if c is None:
            self.msg(tr("Flow: выделите два соседних ключа"))
            return
        self.set_curve(c)

    def _sync(self):
        n = len(self.ed.curve_segments())
        self.apply_btn.setEnabled(n > 0)
        self.read_btn.setEnabled(n > 0)
        self.info.setText(tr("Сегментов выделено: {n}").format(n=n) if n
                          else tr("Выделите ключи на таймлайне. Двойной щелчок по пресету применяет его сразу."))
