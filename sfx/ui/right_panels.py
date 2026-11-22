# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Правая колонка как в AE: Предпросмотр, Выравнивание, Звук, Эффекты и шаблоны."""
from __future__ import annotations

from ..i18n import tr

from PySide6.QtCore import QMimeData, Qt, QTimer
from PySide6.QtGui import QDrag
from PySide6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QProgressBar, QToolButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..effects import CATEGORIES, EFFECTS, category_label, effect_for_layer
from ..render import layer_corners
from .effect_controls import MIME_EFFECT, MIME_PRESET
from .widgets import Hint, LevelMeter, ScrubValue


_ICON_NAMES = {"⏮": "first", "◂": "prev", "▶": "play", "▸": "next", "⏭": "last"}


def _tool(text, tip, fn, w=30):
    b = QToolButton()
    if text in _ICON_NAMES:
        from PySide6.QtCore import QSize
        from .icons import icon
        b.setIcon(icon(_ICON_NAMES[text]))
        b.setIconSize(QSize(16, 16))
    else:
        b.setText(text)
    b.setToolTip(tip)
    b.setFocusPolicy(Qt.NoFocus)
    b.setFixedWidth(w)
    b.setStyleSheet("padding:2px 0;")
    b.clicked.connect(fn)
    return b


class PreviewPanel(QWidget):
    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.ed = editor
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        h = QHBoxLayout()
        ed = editor
        h.addWidget(_tool("⏮", tr("В начало рабочей области"), lambda: ed.set_time(ed.comp.work_in)))
        h.addWidget(_tool("◂", tr("Предыдущий кадр"), lambda: ed.step(-1)))
        self.play = _tool("▶", tr("Воспроизвести / пауза (Пробел)"), ed.toggle_play, 40)
        h.addWidget(self.play)
        h.addWidget(_tool("▸", tr("Следующий кадр"), lambda: ed.step(1)))
        h.addWidget(_tool("⏭", tr("В конец рабочей области"), lambda: ed.set_time(ed.comp.work_out - 1)))
        h.addStretch(1)
        v.addLayout(h)
        self.ram = QToolButton()
        self.ram.setText(tr("Пререндер (Num 0)"))
        self.ram.setToolTip(tr("Просчитать рабочую область в память и проиграть без тормозов, как RAM Preview в AE.\n"
                               "Зелёная полоска над таймлайном — готовые кадры."))
        self.ram.setFocusPolicy(Qt.NoFocus)
        self.ram.setStyleSheet("QToolButton { padding:3px 8px; color:#7fd67f; }")
        self.ram.clicked.connect(lambda: ed.ram_preview(getattr(ed, "preview_transparent", False)))
        v.addWidget(self.ram)
        self.bar = QProgressBar()
        self.bar.setFixedHeight(6)
        self.bar.setTextVisible(False)
        self.bar.setStyleSheet("QProgressBar { background:#1b1b1b; border:none; }"
                               " QProgressBar::chunk { background:#3fbf3f; }")
        self.bar.hide()
        v.addWidget(self.bar)
        editor.prerender_progress.connect(self._progress)
        self.audio = QCheckBox(tr("Звук в превью"))
        self.audio.setChecked(editor.audio_preview)
        self.audio.setFocusPolicy(Qt.NoFocus)
        self.audio.toggled.connect(self._audio)
        v.addWidget(self.audio)
        self.idle = QCheckBox(tr("Пререндер в фоне (в простое)"))
        self.idle.setToolTip(tr("Пока ты ничего не трогаешь, кадры рабочей области сами считаются в память\n"
                                "(как Cache Frames When Idle в AE). Потом всё играет без тормозов."))
        self.idle.setChecked(editor.idle_cache)
        self.idle.setFocusPolicy(Qt.NoFocus)
        self.idle.toggled.connect(editor.set_idle_cache)
        v.addWidget(self.idle)
        from .icons import icon
        editor.playing_changed.connect(lambda on: self.play.setIcon(icon("pause" if on else "play")))

    def _progress(self, done, total):
        if done < 0:
            self.bar.hide()
            self.ram.setText(tr("Пререндер (Num 0)"))
            return
        self.bar.show()
        self.bar.setRange(0, max(1, total))
        self.bar.setValue(done)
        self.ram.setText(tr("Стоп"))

    def _audio(self, on):
        self.ed.audio_preview = on
        if self.ed.timer.isActive():
            self.ed.stop()
            self.ed.toggle_play()


class AlignPanel(QWidget):
    """Выровнять выделенные слои по композиции (или по выделению, если слоёв несколько)."""

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.ed = editor
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        row = QHBoxLayout()
        row.addWidget(QLabel(tr("Выровнять по:")))
        self.target = QComboBox()
        self.target.setFocusPolicy(Qt.NoFocus)
        self.target.addItems([tr("Композиции"), tr("Выделению")])
        row.addWidget(self.target, 1)
        v.addLayout(row)
        g = QGridLayout()
        for i, (txt, tip, mode) in enumerate((("⇤", tr("По левому краю"), "left"), ("↔", tr("По центру (гориз.)"), "hc"),
                                               ("⇥", tr("По правому краю"), "right"), ("⤒", tr("По верху"), "top"),
                                               ("↕", tr("По центру (верт.)"), "vc"), ("⤓", tr("По низу"), "bottom"))):
            g.addWidget(_tool(txt, tip, lambda _=False, m=mode: self.align(m), 36), 0, i)
        v.addLayout(g)

    def align(self, mode):
        ed = self.ed
        sel = [l for l in ed.selected() if l.kind not in ("audio", "camera", "light")]
        if not sel:
            return
        t = ed.time
        boxes = []
        for l in sel:
            pts = layer_corners(l, t, ed.comp)
            xs, ys = [p.x() for p in pts], [p.y() for p in pts]
            boxes.append((min(xs), min(ys), max(xs), max(ys)))
        if self.target.currentIndex() == 1 and len(sel) > 1:
            ref = (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))
        else:
            ref = (0, 0, ed.comp.width, ed.comp.height)
        ed.snapshot()
        for l, (x0, y0, x1, y1) in zip(sel, boxes):
            dx = dy = 0.0
            if mode == "left":
                dx = ref[0] - x0
            elif mode == "right":
                dx = ref[2] - x1
            elif mode == "hc":
                dx = (ref[0] + ref[2]) / 2 - (x0 + x1) / 2
            elif mode == "top":
                dy = ref[1] - y0
            elif mode == "bottom":
                dy = ref[3] - y1
            elif mode == "vc":
                dy = (ref[1] + ref[3]) / 2 - (y0 + y1) / 2
            f = l.local(t)
            pos = l.props["position"].value_at(f)
            pos[0] += dx
            pos[1] += dy
            l.props["position"].set_value_at(f, [round(v, 3) for v in pos])
        ed.notify()


class AudioPanel(QWidget):
    """Индикатор громкости при воспроизведении и «Уровни звука» выделенного слоя."""

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.ed = editor
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        self.meter = LevelMeter()
        v.addWidget(self.meter)
        row = QHBoxLayout()
        self.lab = QLabel(tr("Уровни звука:"))
        row.addWidget(self.lab)
        self.levels = ScrubValue(1, 0.1, " dB", lo=-96, hi=24)
        self.levels.begin.connect(lambda: self.ed.snapshot("audio_levels"))
        self.levels.changed.connect(self._set_levels)
        row.addWidget(self.levels, 1)
        v.addLayout(row)
        v.addWidget(Hint(tr("L на таймлайне — показать уровни, LL — волну. Звуковые эффекты (эквалайзер, "
                            "компрессор, реверб…) — в «Эффекты и шаблоны» → Звук.")))
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(40)
        editor.selection_changed.connect(self._sync)
        editor.changed.connect(self._sync)
        editor.time_changed.connect(lambda _: self._sync())
        self._sync()

    def _layer(self):
        l = self.ed.current()
        return l if l is not None and "audio_levels" in l.props else None

    def _sync(self):
        l = self._layer()
        self.levels.setEnabled(l is not None)
        if l is not None:
            self.levels.set_values(l.props["audio_levels"].value_at(l.local(self.ed.time)))

    def _set_levels(self, vals):
        l = self._layer()
        if l is None:
            return
        l.props["audio_levels"].set_value_at(l.local(self.ed.time), vals)
        self.ed.notify()

    def _tick(self):
        if not self.isVisible():
            return
        lv = self.ed.player.levels() if self.ed.player.playing else (0.0, 0.0)
        self.meter.set_levels(*lv)


class _EffectTree(QTreeWidget):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.setHeaderHidden(True)
        self.setDragEnabled(True)
        self.setRootIsDecorated(True)
        self.setFocusPolicy(Qt.ClickFocus)

    def startDrag(self, actions):
        it = self.currentItem()
        t = it.data(0, Qt.UserRole) if it else None
        if not t:
            return
        md = QMimeData()
        if t.startswith("preset:"):
            md.setData(MIME_PRESET, t[7:].encode())
        else:
            md.setData(MIME_EFFECT, t.encode())
            md.setText(EFFECTS[t].label)
        drag = QDrag(self)
        drag.setMimeData(md)
        drag.exec(Qt.CopyAction)


class EffectsPresetsPanel(QWidget):
    """«Эффекты и шаблоны»: поиск, категории; двойной клик — применить к выделенным слоям, или перетащить."""

    def __init__(self, editor, on_message=None, parent=None, on_preset=None):
        super().__init__(parent)
        self.ed = editor
        self.on_message = on_message
        self.on_preset = on_preset
        v = QVBoxLayout(self)
        v.setContentsMargins(4, 4, 4, 4)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("🔍 Поиск эффектов"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._fill)
        v.addWidget(self.search)
        self.tree = _EffectTree(self)
        self.tree.setMinimumHeight(180)
        self.tree.itemDoubleClicked.connect(self._apply)
        v.addWidget(self.tree, 1)
        v.addWidget(Hint(tr("Двойной клик — к выделенным слоям, или перетащи на слой (таймлайн, окно композиции, "
                            "панель эффектов). Ctrl+Пробел — быстрый поиск.")))
        self._fill()

    def _fill(self):
        from ..presets import PRESETS
        q = self.search.text().strip().lower()
        self.tree.clear()
        items = [(pid, pr) for pid, pr in PRESETS.items() if not q or q in pr.ru.lower() or q in pr.en.lower()]
        if items:
            top = QTreeWidgetItem([tr("★ Шаблоны анимации")])
            for pid, pr in items:
                it = QTreeWidgetItem([pr.label])
                it.setData(0, Qt.UserRole, f"preset:{pid}")
                it.setToolTip(0, tr("Ставит ключи от текущего кадра на выделенные слои"))
                top.addChild(it)
            self.tree.addTopLevelItem(top)
            top.setExpanded(bool(q))
        for cat in CATEGORIES:
            items = [(t, d) for t, d in EFFECTS.items() if d.category == cat
                     and (not q or q in d.ru.lower() or q in d.en.lower())]
            if not items:
                continue
            top = QTreeWidgetItem([category_label(cat)])
            top.setFlags(top.flags() & ~Qt.ItemIsDragEnabled)
            for t, d in items:
                it = QTreeWidgetItem([d.label])
                it.setData(0, Qt.UserRole, t)
                it.setToolTip(0, f"{d.ru} / {d.en}")
                top.addChild(it)
            self.tree.addTopLevelItem(top)
            top.setExpanded(bool(q))

    def _apply(self, it, _col=0):
        t = it.data(0, Qt.UserRole)
        if not t:
            return
        if t.startswith("preset:"):
            if self.on_preset:
                self.on_preset(t[7:])
            return
        sel = self.ed.selected()
        if not sel:
            if self.on_message:
                self.on_message(tr("Сначала выдели слой"))
            return
        if not any(effect_for_layer(t, l.kind) for l in sel):
            if self.on_message:
                self.on_message(tr("Этот эффект не подходит выделенному слою"))
            return
        self.ed.add_effect(t)
