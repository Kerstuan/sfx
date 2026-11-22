# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""Быстрый поиск эффектов и шаблонов по Ctrl+Space (как FX Console у Video Copilot)."""
from __future__ import annotations

from ..i18n import tr

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QVBoxLayout

from ..effects import EFFECTS, category_label, effect_for_layer
from ..presets import PRESETS


def _score(q: str, *names: str) -> int:
    """Чем меньше — тем лучше. Совпадение с начала слова важнее, чем где-то в середине."""
    best = 999
    for n in names:
        n = n.lower()
        if n.startswith(q):
            best = min(best, 0)
        elif any(w.startswith(q) for w in n.replace("-", " ").replace("/", " ").split()):
            best = min(best, 1)
        elif q in n:
            best = min(best, 2)
        else:   # буквы по порядку: «gblr» найдёт Gaussian Blur
            i = 0
            for ch in n:
                if i < len(q) and ch == q[i]:
                    i += 1
            if i == len(q):
                best = min(best, 3)
    return best


class FxConsole(QDialog):
    def __init__(self, win):
        super().__init__(win, Qt.Popup | Qt.FramelessWindowHint)
        self.win = win
        self.setObjectName("FxConsole")
        self.setStyleSheet("#FxConsole { background:#232323; border:1px solid #2d8ceb; border-radius:6px; }"
                           " QListWidget { background:#1b1b1b; border:none; font-size:13px; }"
                           " QListWidget::item { padding:4px 8px; }"
                           " QListWidget::item:selected { background:#2d8ceb; color:white; }")
        self.resize(460, 360)
        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText(tr("Эффект или шаблон… (Enter — применить, Esc — закрыть)"))
        self.edit.setStyleSheet("font-size:15px; padding:6px;")
        self.edit.textChanged.connect(self._fill)
        self.edit.installEventFilter(self)
        v.addWidget(self.edit)
        self.list = QListWidget()
        self.list.itemActivated.connect(self._apply)
        v.addWidget(self.list, 1)
        self.hint = QLabel()
        self.hint.setStyleSheet("color:#7a7a7a; font-size:11px;")
        v.addWidget(self.hint)
        self._fill()

    def popup(self):
        g = self.win.geometry()
        self.move(g.x() + (g.width() - self.width()) // 2, g.y() + g.height() // 4)
        self.edit.clear()
        self._fill()
        sel = self.win.ed.selected()
        self.hint.setText(tr("Применится к: {}").format(", ".join(l.name for l in sel)) if sel
                          else tr("Нет выделенного слоя — сначала выдели слой"))
        self.show()
        self.raise_()
        self.edit.setFocus()

    def _fill(self):
        q = self.edit.text().strip().lower()
        kinds = {l.kind for l in self.win.ed.selected()}
        rows = []
        for t, d in EFFECTS.items():
            if kinds and not any(effect_for_layer(t, k) for k in kinds):
                continue
            sc = _score(q, d.ru, d.en) if q else 0
            if sc < 999:
                rows.append((sc, d.label, f"fx  ·  {category_label(d.category)}", f"fx:{t}"))
        for pid, pr in PRESETS.items():
            sc = _score(q, pr.ru, pr.en) if q else 1
            if sc < 999:
                rows.append((sc, pr.label, tr("★  шаблон анимации"), f"preset:{pid}"))
        rows.sort(key=lambda r: (r[0], r[1].lower()))
        self.list.clear()
        for _, label, cat, ref in rows:
            it = QListWidgetItem(f"{label}      {cat}")
            it.setData(Qt.UserRole, ref)
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    def eventFilter(self, obj, e):
        from PySide6.QtCore import QEvent
        if obj is self.edit and e.type() == QEvent.KeyPress:
            k = e.key()
            if k in (Qt.Key_Down, Qt.Key_Up):
                r = self.list.currentRow() + (1 if k == Qt.Key_Down else -1)
                self.list.setCurrentRow(max(0, min(self.list.count() - 1, r)))
                return True
            if k in (Qt.Key_Return, Qt.Key_Enter):
                self._apply(self.list.currentItem())
                return True
            if k == Qt.Key_Escape:
                self.close()
                return True
        return super().eventFilter(obj, e)

    def _apply(self, it):
        if it is None:
            return
        ref = it.data(Qt.UserRole)
        self.close()
        if ref.startswith("fx:"):
            self.win._add_effect(ref[3:])
        else:
            self.win.apply_preset(ref[7:])
