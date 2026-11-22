# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""Панели «Проект» и «Свойства слоя», диалог настроек композиции."""
from __future__ import annotations

from ..i18n import tr

import os

from PySide6.QtCore import QMimeData, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDrag, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
                               QFontComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMenu, QPlainTextEdit, QPushButton, QScrollArea, QSpinBox,
                               QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..effects import EFFECTS, prop_key
from ..media import thumbnail
from ..render import update_text_size


def panel(title: str, body: QWidget) -> QWidget:
    """Обёртка с заголовком, как вкладка панели в AE."""
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    t = QLabel(title)
    t.setObjectName("PanelTitle")
    lay.addWidget(t)
    lay.addWidget(body, 1)
    w.title_label = t
    return w


MIME_ITEM = "application/x-sfx-item"   # перетаскивание из «Проекта»: "comp:<id>" или "media:<путь>"


class _ProjectTree(QTreeWidget):
    def __init__(self, panel):
        super().__init__()
        self.panel = panel
        self.setHeaderLabels([tr("Имя"), tr("Тип"), tr("Сведения")])
        self.setRootIsDecorated(False)
        self.setIconSize(QSize(32, 32))
        self.setDragEnabled(True)
        self.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.setFocusPolicy(Qt.ClickFocus)
        self.setColumnWidth(0, 190)
        self.setColumnWidth(1, 70)

    def startDrag(self, actions):
        items = [it.data(0, Qt.UserRole) for it in self.selectedItems() if it.data(0, Qt.UserRole)]
        if not items:
            return
        md = QMimeData()
        md.setData(MIME_ITEM, "\n".join(items).encode())
        drag = QDrag(self)
        drag.setMimeData(md)
        drag.exec(Qt.CopyAction)


class ProjectPanel(QWidget):
    """«Проект» как в AE: композиции и медиа. Двойной клик: композиция — открыть, файл — в композицию.
    Можно перетаскивать в окно композиции и на таймлайн."""

    add_requested = Signal(str)
    open_comp_requested = Signal(str)
    action_requested = Signal(str, str)   # действие, элемент ("comp:<id>" / "media:<путь>")

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.ed = editor
        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        self.info = QLabel("")
        self.info.setWordWrap(True)
        self.info.setStyleSheet("color:#8a8a8a; padding:4px;")
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("🔍 Поиск"))
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh)
        self.list = _ProjectTree(self)
        self.list.itemDoubleClicked.connect(self._double)
        self.list.currentItemChanged.connect(self._show_info)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._menu)
        self.btn = QPushButton(tr("Импорт… (Ctrl+I)"))
        self.btn.setFocusPolicy(Qt.NoFocus)
        self.add_btn = QPushButton(tr("В композицию"))
        self.add_btn.setFocusPolicy(Qt.NoFocus)
        self.add_btn.clicked.connect(self._add_current)
        lay.addWidget(self.info)
        lay.addWidget(self.search)
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        row.addWidget(self.btn)
        row.addWidget(self.add_btn)
        lay.addLayout(row)
        hint = QLabel(tr("Двойной клик или перетаскивание — в композицию. Правая кнопка — меню"))
        hint.setStyleSheet("color:#666; font-size:11px;")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        editor.media_changed.connect(self.refresh)
        editor.comp_switched.connect(self.refresh)
        editor.changed.connect(self._maybe_refresh)
        self._sig = None

    def _signature(self):
        return (tuple((c.id, c.name, c.width, c.height, c.duration, len(c.layers)) for c in self.ed.project.comps),
                tuple(self.ed.project.media), self.ed.project.active)

    def _maybe_refresh(self):
        if self._signature() != self._sig:
            self.refresh()

    def _add_current(self):
        it = self.list.currentItem()
        if it and it.data(0, Qt.UserRole):
            self.add_requested.emit(it.data(0, Qt.UserRole))

    def _double(self, it, _col=0):
        ref = it.data(0, Qt.UserRole)
        if not ref:
            return
        if ref.startswith("comp:"):
            self.open_comp_requested.emit(ref[5:])
        else:
            self.add_requested.emit(ref)

    def refresh(self):
        self._sig = self._signature()
        q = self.search.text().strip().lower()
        cur = self.list.currentItem().data(0, Qt.UserRole) if self.list.currentItem() else None
        self.list.clear()
        todo = self._thumb_todo = []
        icons = {"video": "▶", "image": "▣", "audio": "♪"}
        kinds = {"video": tr("Видео"), "image": tr("Картинка"), "audio": tr("Звук")}
        for c in self.ed.project.comps:
            if q and q not in c.name.lower():
                continue
            it = QTreeWidgetItem([f"▦  {c.name}", tr("Композиция"),
                                  f"{c.width}×{c.height}, {c.fps:g} fps, {c.duration / c.fps:.1f} {tr('с')}"])
            it.setData(0, Qt.UserRole, f"comp:{c.id}")
            if c.id == self.ed.project.active:
                f = it.font(0)
                f.setBold(True)
                it.setFont(0, f)
            self.list.addTopLevelItem(it)
            if f"comp:{c.id}" == cur:
                self.list.setCurrentItem(it)
        for path in self.ed.project.media:
            name = os.path.basename(path)
            if q and q not in name.lower():
                continue
            try:
                info = self.ed.pool.info(path)
                kind = info.kind
                det = f"{info.width}×{info.height}" if info.width else f"{info.duration:.1f} {tr('с')}"
            except Exception:
                kind, det = "?", tr("нет файла")
            it = QTreeWidgetItem([f"{icons.get(kind, '?')}  {name}", kinds.get(kind, "?"), det])
            it.setData(0, Qt.UserRole, f"media:{path}")
            it.setToolTip(0, path)
            if kind in ("video", "image"):
                todo.append((it, path))
            self.list.addTopLevelItem(it)
            if f"media:{path}" == cur:
                self.list.setCurrentItem(it)
        self._show_info(self.list.currentItem())
        QTimer.singleShot(0, lambda t=todo: self._next_thumb() if t is self._thumb_todo else None)

    def _next_thumb(self):
        """Миниатюры по одной за тик: 4K-клип декодируется ~80 мс, окно не должно замирать."""
        todo = self._thumb_todo
        if not todo:
            return
        it, path = todo.pop(0)
        img = thumbnail(path)
        if img is not None:
            name = os.path.basename(path)
            it.setText(0, name)
            it.setIcon(0, QIcon(QPixmap.fromImage(img)))
        if todo:   # после перестройки списка старые элементы удалены — их не трогаем
            QTimer.singleShot(0, lambda t=todo: self._next_thumb() if t is self._thumb_todo else None)

    def _show_info(self, it, *_):
        ref = it.data(0, Qt.UserRole) if it else None
        if not ref:
            self.info.setText(tr("{} файл(ов)").format(len(self.ed.project.media)) + ", "
                              + tr("{} композ.").format(len(self.ed.project.comps)))
            return
        if ref.startswith("comp:"):
            c = self.ed.project.comp_by_id(ref[5:])
            if c:
                self.info.setText(f"{c.name}\n{c.width} × {c.height}, {c.fps:g} fps\n"
                                  + tr("{} слоёв, {:.2f} с").format(len(c.layers), c.duration / c.fps))
            return
        try:
            i = self.ed.pool.info(ref[6:])
        except Exception as e:
            self.info.setText(str(e))
            return
        lines = [i.name]
        if i.kind != "audio":
            lines.append(f"{i.width} × {i.height}")
        if i.kind == "video":
            lines.append(tr("{} fps, {} с").format(f"{i.fps:g}", f"{i.duration:.2f}") + (tr(", со звуком") if i.has_audio else ""))
        elif i.kind == "audio":
            lines.append(tr("звук, {} с").format(f"{i.duration:.2f}"))
        self.info.setText("\n".join(lines))

    def _menu(self, pos):
        it = self.list.itemAt(pos)
        ref = it.data(0, Qt.UserRole) if it else ""
        m = QMenu(self)
        m.setAttribute(Qt.WA_DeleteOnClose)   # иначе каждое меню ПКМ остаётся ребёнком окна навсегда
        A = lambda text, act: m.addAction(text, lambda: self.action_requested.emit(act, ref))  # noqa: E731
        A(tr("Новая композиция…"), "new_comp")
        A(tr("Импорт…"), "import")
        if ref.startswith("comp:"):
            m.addSeparator()
            A(tr("Открыть"), "open")
            A(tr("Добавить в текущую композицию"), "add")
            A(tr("Рендер композиции…"), "render_comp")
            A(tr("Настройки композиции…"), "comp_settings")
            A(tr("Переименовать…"), "rename")
            A(tr("Дублировать"), "dup_comp")
            A(tr("Удалить композицию"), "delete_comp")
        elif ref.startswith("media:"):
            m.addSeparator()
            A(tr("Добавить в композицию"), "add")
            A(tr("Новая композиция из этого файла"), "comp_from")
            A(tr("Заменить исходник выделенного слоя"), "replace")
            A(tr("Показать в папке"), "reveal")
            A(tr("Убрать из проекта"), "remove_media")
        m.exec(self.list.viewport().mapToGlobal(pos))


class ColorButton(QPushButton):
    color_changed = Signal(str)

    def __init__(self, color="#ffffff"):
        super().__init__()
        self.setFocusPolicy(Qt.NoFocus)
        self.setFixedWidth(60)
        self.set_color(color)
        self.clicked.connect(self._pick)

    def set_color(self, color):
        self._color = color
        self.setStyleSheet(f"background:{color}; border:1px solid #111; min-height:18px;")

    def _pick(self):
        col = QColorDialog.getColor(QColor(self._color), self, tr("Цвет"))
        if col.isValid():
            self.set_color(col.name())
            self.color_changed.emit(col.name())


class PropertiesPanel(QWidget):
    """Аналог Effect Controls / Character: параметры выделенного слоя."""

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.ed = editor
        self._layer = None
        self._loading = False
        self._labels: list[QLabel] = []
        self._fx_sig = None
        self._spins: list[tuple[QDoubleSpinBox, str]] = []   # (виджет, ключ свойства)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        inner = QWidget()
        scroll.setWidget(inner)
        outer.addWidget(scroll)
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(8, 8, 8, 8)
        self.title = QLabel(tr("Нет выделенного слоя"))
        self.title.setStyleSheet("color:#e6e6e6; font-weight:bold;")
        self.title.setWordWrap(True)
        lay.addWidget(self.title)
        self.form_host = QWidget()
        self.form = QFormLayout(self.form_host)
        self.form.setContentsMargins(0, 6, 0, 0)
        lay.addWidget(self.form_host)
        self.extra_host = QWidget()
        self.extra = QVBoxLayout(self.extra_host)
        self.extra.setContentsMargins(0, 8, 0, 0)
        self.extra.setSpacing(4)
        lay.addWidget(self.extra_host)
        lay.addStretch(1)

        self.text = QPlainTextEdit()
        self.text.setFixedHeight(80)
        self.text.textChanged.connect(self._text_changed)
        self.font_box = QFontComboBox()
        self.font_box.currentFontChanged.connect(lambda f: self._set("font_family", f.family()))
        self.size = QDoubleSpinBox()
        self.size.setRange(1, 2000)
        self.size.setDecimals(0)
        self.size.valueChanged.connect(lambda v: self._set("font_size", v))
        self.bold = QCheckBox(tr("Жирный"))
        self.bold.toggled.connect(lambda v: self._set("bold", v))
        self.color = ColorButton()
        self.color.color_changed.connect(lambda col: self._set("color", col))
        self.audio = QCheckBox(tr("Звук включён"))
        self.audio.toggled.connect(lambda v: self._set("audio", v))
        self.reset = QPushButton(tr("Сбросить трансформацию"))
        self.reset.setFocusPolicy(Qt.NoFocus)
        self.reset.clicked.connect(self._reset_transform)
        for w in (self.text, self.font_box, self.size, self.bold, self.color, self.audio, self.reset):
            w.setParent(self.form_host)
            w.hide()
        editor.selection_changed.connect(self.refresh)
        editor.changed.connect(self._maybe_refresh)
        editor.time_changed.connect(lambda _: self._update_values())

    def _signature(self, l):
        if l is None:
            return None
        comp = self.ed.comp
        return (l.id, l.parent, l.adjustment, l.motion_blur, l.fx_on, l.name, l.threed,
                str(getattr(l, "light", "")), str(getattr(l, "material", "")), getattr(l, "dof", None),
                tuple((fx["id"], fx.get("on", True), tuple(sorted(fx.get("opts", {}).items()))) for fx in l.effects),
                tuple((o.id, o.name) for o in comp.layers))

    def _maybe_refresh(self):
        cur = self.ed.current()
        if cur is not self._layer:
            self.refresh()
        elif self._signature(cur) != self._fx_sig:
            self._build_extra()
        else:
            self._update_values()

    def _update_values(self):
        l = self._layer
        if l is None:
            return
        f = l.local(self.ed.time)
        for spin, key in self._spins:
            prop = l.props.get(key)
            if prop is None or spin.hasFocus():
                continue
            spin.blockSignals(True)
            spin.setValue(prop.value_at(f)[0])
            spin.blockSignals(False)

    # --- блок слоя и эффектов -----------------------------------------------------
    def _clear_extra(self):
        self._spins = []
        while self.extra.count():
            it = self.extra.takeAt(0)
            w = it.widget()
            if w:
                w.hide()
                w.deleteLater()

    def _build_extra(self):
        self._clear_extra()
        l = self._layer
        self._fx_sig = self._signature(l)
        if l is None or l.kind == "audio":
            return
        ed = self.ed
        comp = ed.comp
        # родитель
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(QLabel(tr("Родитель")))
        par = QComboBox()
        par.setFocusPolicy(Qt.NoFocus)
        par.setMinimumContentsLength(6)
        par.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        par.addItem(tr("Нет"), None)
        for i, o in enumerate(comp.layers):
            if o is l or o.kind == "audio":
                continue
            par.addItem(f"{i + 1}. {o.name}", o.id)
            if not ed.can_parent(l, o):
                par.model().item(par.count() - 1).setEnabled(False)
        par.setCurrentIndex(max(0, par.findData(l.parent)) if l.parent else 0)
        par.currentIndexChanged.connect(lambda _: ed.set_parent(l, comp.layer_by_id(par.currentData())
                                                                if par.currentData() else None))
        h.addWidget(par, 1)
        self.extra.addWidget(row)
        if l.kind == "camera":
            dof = QCheckBox(tr("Глубина резкости"))
            dof.setFocusPolicy(Qt.NoFocus)
            dof.setChecked(bool(getattr(l, "dof", False)))
            dof.setToolTip(tr("Размывает 3D-слои вне фокуса: Фокусное расстояние, Диафрагма, Уровень размытия — "
                              "в «Параметрах камеры» на таймлайне"))
            dof.toggled.connect(lambda v: (ed.snapshot(), setattr(l, "dof", bool(v)), ed.notify()))
            self.extra.addWidget(dof)
            return
        if l.kind == "light":
            from ..i18n import LANG
            from ..model import FALLOFF_TYPES, LIGHT_TYPES
            for key, label, items in (("type", tr("Тип света"), LIGHT_TYPES), ("falloff", tr("Затухание"), FALLOFF_TYPES)):
                r = QWidget()
                hh = QHBoxLayout(r)
                hh.setContentsMargins(0, 0, 0, 0)
                hh.addWidget(QLabel(label))
                cb = QComboBox()
                cb.setFocusPolicy(Qt.NoFocus)
                for k, ru, en in items:
                    cb.addItem(en if LANG == "en" else ru, k)
                cb.setCurrentIndex(max(0, cb.findData(l.light.get(key))))
                cb.currentIndexChanged.connect(lambda _i, c=cb, k=key: ed.set_light_option(l, k, c.currentData()))
                hh.addWidget(cb, 1)
                self.extra.addWidget(r)
            sh = QCheckBox(tr("Отбрасывает тени"))
            sh.setFocusPolicy(Qt.NoFocus)
            sh.setChecked(bool(l.light.get("shadows")))
            sh.setToolTip(tr("Тени дают только 3D-слои с включённым «Отбрасывает тени»"))
            sh.toggled.connect(lambda v: ed.set_light_option(l, "shadows", bool(v)))
            self.extra.addWidget(sh)
            return
        for attr, text in (("adjustment", tr("Корректирующий слой")), ("motion_blur", tr("Размытие в движении"))):
            cb = QCheckBox(text)
            cb.setFocusPolicy(Qt.NoFocus)
            cb.setChecked(getattr(l, attr))
            cb.toggled.connect(lambda _v, a=attr: ed.toggle_switch(a, [l]))
            self.extra.addWidget(cb)
        cb3 = QCheckBox(tr("3D-слой"))
        cb3.setFocusPolicy(Qt.NoFocus)
        cb3.setChecked(l.threed)
        cb3.toggled.connect(lambda v: ed.set_3d([l], v))
        self.extra.addWidget(cb3)
        if l.threed and isinstance(getattr(l, "material", None), dict):
            for key, text in (("accepts_lights", tr("Принимает свет")), ("casts_shadows", tr("Отбрасывает тени")),
                              ("accepts_shadows", tr("Принимает тени"))):
                mc = QCheckBox(text)
                mc.setFocusPolicy(Qt.NoFocus)
                mc.setChecked(bool(l.material.get(key)))
                mc.toggled.connect(lambda v, k=key: ed.set_material_option([l], k, v))
                self.extra.addWidget(mc)

        self._update_values()

    def _fx_on(self, fx, val):
        self.ed.snapshot()
        fx["on"] = val
        self.ed.notify()

    def _set_param(self, key, val):
        l = self._layer
        if l is None or key not in l.props:
            return
        self.ed.snapshot(f"{l.id}.{key}")
        l.props[key].set_value_at(l.local(self.ed.time), [val])
        self.ed.notify()

    def refresh(self):
        self._loading = True
        fields = (self.text, self.font_box, self.size, self.bold, self.color, self.audio, self.reset)
        while self.form.rowCount():
            self.form.takeRow(0)
        for lab in self._labels:
            lab.hide()
            lab.setParent(None)
            lab.deleteLater()
        self._labels = []
        for w in fields:
            w.hide()
        l = self._layer = self.ed.current()
        if l is None:
            self.title.setText(tr("Нет выделенного слоя"))
            self._build_extra()
            self._loading = False
            return
        names = {k: tr(v) for k, v in {"video": "Видео", "image": "Картинка", "solid": "Солид", "text": "Текст",
                                        "audio": "Звук", "shape": "Шейп-слой", "camera": "Камера", "light": "Свет"}.items()}
        kind = tr("Корректирующий") if l.adjustment else names.get(l.kind, l.kind)
        self.title.setText(f"{kind}: {l.name}")
        if l.kind == "text":
            self.text.setPlainText(l.text)
            self.font_box.setCurrentFont(QFont(l.font_family))
            self.size.setValue(l.font_size)
            self.bold.setChecked(l.bold)
            self.color.set_color(l.color)
            self._row(tr("Текст"), self.text)
            self._row(tr("Шрифт"), self.font_box)
            self._row(tr("Размер"), self.size)
            self._row("", self.bold)
            self._row(tr("Цвет"), self.color)
        elif l.kind == "solid" and not l.adjustment:
            self.color.set_color(l.color)
            self._row(tr("Цвет"), self.color)
        if l.kind in ("video", "audio") and l.has_audio:
            self.audio.setChecked(l.audio)
            self._row("", self.audio)
        if l.kind not in ("audio", "camera", "light"):
            self._row("", self.reset)
        for i in range(self.form.rowCount()):
            for role in (QFormLayout.LabelRole, QFormLayout.FieldRole):
                it = self.form.itemAt(i, role)
                if it and it.widget():
                    it.widget().show()
        self._build_extra()
        self._loading = False

    def _row(self, label: str, widget):
        lab = QLabel(label, self.form_host)
        self._labels.append(lab)
        self.form.addRow(lab, widget)

    def _set(self, attr, value):
        l = self._layer
        if self._loading or l is None:
            return
        self.ed.snapshot(f"{l.id}.{attr}")
        setattr(l, attr, value)
        if l.kind == "text":
            update_text_size(l)
        self.ed.notify()

    def _text_changed(self):
        self._set("text", self.text.toPlainText())

    def _reset_transform(self):
        if self._layer:
            self.ed.reset_transform(self._layer)


class CompSettingsDialog(QDialog):
    def __init__(self, comp, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Настройки композиции"))
        f = QFormLayout(self)
        self.name = QLineEdit(comp.name)
        self.w = QSpinBox()
        self.w.setRange(16, 8192)
        self.w.setValue(comp.width)
        self.h = QSpinBox()
        self.h.setRange(16, 8192)
        self.h.setValue(comp.height)
        self.preset = QComboBox()
        self.preset.addItems(["—", "1920×1080 (16:9)", tr("1080×1920 (вертикальное, TikTok/Reels)"),
                              "3840×2160 (4K)", "1280×720", tr("1080×1080 (квадрат)")])
        self.preset.currentIndexChanged.connect(self._preset)
        self.fps = QDoubleSpinBox()
        self.fps.setRange(1, 240)
        self.fps.setDecimals(3)
        self.fps.setValue(comp.fps)
        self.dur = QDoubleSpinBox()
        self.dur.setRange(0.1, 36000)
        self.dur.setDecimals(2)
        self.dur.setSuffix(tr(" с"))
        self.dur.setValue(comp.duration / comp.fps)
        self.bg = ColorButton(comp.bg)
        f.addRow(tr("Имя"), self.name)
        f.addRow(tr("Пресет"), self.preset)
        f.addRow(tr("Ширина"), self.w)
        f.addRow(tr("Высота"), self.h)
        f.addRow(tr("Кадров в секунду"), self.fps)
        f.addRow(tr("Длительность"), self.dur)
        f.addRow(tr("Фон"), self.bg)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

    def _preset(self, i):
        sizes = {1: (1920, 1080), 2: (1080, 1920), 3: (3840, 2160), 4: (1280, 720), 5: (1080, 1080)}
        if i in sizes:
            self.w.setValue(sizes[i][0])
            self.h.setValue(sizes[i][1])

    def apply(self, comp):
        """Применить к композиции; при смене fps пересчитываем все кадры."""
        old_fps = comp.fps
        new_fps = self.fps.value()
        comp.name = self.name.text() or comp.name
        comp.width, comp.height = self.w.value(), self.h.value()
        comp.bg = self.bg._color
        if abs(new_fps - old_fps) > 1e-6:
            r = new_fps / old_fps
            comp.work_in = round(comp.work_in * r)
            comp.work_out = round(comp.work_out * r)
            for m in comp.markers:
                m["frame"] = round(m["frame"] * r)
            for l in comp.layers:
                l.start, l.in_point, l.out_point = round(l.start * r), round(l.in_point * r), round(l.out_point * r)
                if l.source_frames:
                    l.source_frames = round(l.source_frames * r)
                for p in l.props.values():
                    for k in p.keyframes:
                        k.frame = round(k.frame * r)
                for m in l.markers:
                    m["frame"] = round(m["frame"] * r)
            comp.fps = new_fps
        comp.duration = max(1, round(self.dur.value() * comp.fps))
        comp.work_out = min(comp.work_out, comp.duration) if comp.work_out > comp.work_in else comp.duration
        if comp.work_out <= comp.work_in:
            comp.work_in, comp.work_out = 0, comp.duration


class SolidSettingsDialog(QDialog):
    """«Настройки солида» как в AE: имя, размер (с замком пропорций), «Размер композиции», цвет с пипеткой."""

    def __init__(self, comp, parent=None, layer=None, default_color="#808080"):
        super().__init__(parent)
        self.setWindowTitle(tr("Настройки солида"))
        self.setMinimumWidth(420)
        self.comp = comp
        f = QFormLayout(self)
        n = sum(1 for l in comp.layers if l.kind == "solid" and not l.adjustment) + 1
        self.name = QLineEdit(layer.name if layer else tr("Солид {}").format(n))
        f.addRow(tr("Имя"), self.name)
        size = QLabel(tr("Размер"))
        size.setStyleSheet("color:#e6e6e6; font-weight:bold; padding-top:6px;")
        f.addRow(size)
        self.w = QSpinBox()
        self.w.setRange(1, 30000)
        self.w.setSuffix(" px")
        self.w.setValue(int(layer.width) if layer else comp.width)
        self.h = QSpinBox()
        self.h.setRange(1, 30000)
        self.h.setSuffix(" px")
        self.h.setValue(int(layer.height) if layer else comp.height)
        self.lock = QCheckBox(tr("Сохранять пропорции {}").format(self._ratio()))
        self.lock.toggled.connect(lambda _: self.lock.setText(tr("Сохранять пропорции {}").format(self._ratio())))
        self._aspect = self.w.value() / max(1, self.h.value())
        self.w.valueChanged.connect(self._w_changed)
        self.h.valueChanged.connect(self._h_changed)
        f.addRow(tr("Ширина"), self.w)
        f.addRow(tr("Высота"), self.h)
        f.addRow("", self.lock)
        mk = QPushButton(tr("Размер композиции"))
        mk.setFocusPolicy(Qt.NoFocus)
        mk.clicked.connect(self._comp_size)
        f.addRow("", mk)
        col = QLabel(tr("Цвет"))
        col.setStyleSheet("color:#e6e6e6; font-weight:bold; padding-top:6px;")
        f.addRow(col)
        row = QHBoxLayout()
        self.color = ColorButton(layer.color if layer else default_color)
        self.color.setFixedWidth(90)
        row.addWidget(self.color)
        pick = QPushButton("💧 " + tr("Пипетка"))
        pick.setToolTip(tr("Взять цвет с экрана"))
        pick.setFocusPolicy(Qt.NoFocus)
        pick.clicked.connect(self._eyedropper)
        row.addWidget(pick)
        row.addStretch(1)
        f.addRow("", row)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        self._busy = False

    def _ratio(self):
        from math import gcd
        w, h = self.w.value(), self.h.value()
        g = gcd(w, h) or 1
        return f"{w // g}:{h // g} ({w / max(1, h):.2f})"

    def _w_changed(self, v):
        if self.lock.isChecked() and not self._busy:
            self._busy = True
            self.h.setValue(max(1, round(v / self._aspect)))
            self._busy = False
        elif not self._busy:
            self._aspect = v / max(1, self.h.value())

    def _h_changed(self, v):
        if self.lock.isChecked() and not self._busy:
            self._busy = True
            self.w.setValue(max(1, round(v * self._aspect)))
            self._busy = False
        elif not self._busy:
            self._aspect = self.w.value() / max(1, v)

    def _comp_size(self):
        self._busy = True
        self.w.setValue(self.comp.width)
        self.h.setValue(self.comp.height)
        self._busy = False
        self._aspect = self.comp.width / max(1, self.comp.height)

    def _eyedropper(self):
        dlg = QColorDialog(QColor(self.color._color), self)
        dlg.setOption(QColorDialog.DontUseNativeDialog, True)
        dlg.setWindowTitle(tr("Цвет — кнопка «Pick Screen Color» берёт цвет с экрана"))
        if dlg.exec():
            self.color.set_color(dlg.selectedColor().name())

    def values(self):
        return self.name.text().strip() or tr("Солид"), self.w.value(), self.h.value(), self.color._color


class SpeedDialog(QDialog):
    """«Скорость клипа»: 200% — в 2 раза быстрее, 50% — в 2 раза медленнее."""

    def __init__(self, layer, fps, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Скорость клипа"))
        self.layer, self.fps = layer, fps
        f = QFormLayout(self)
        p = layer.props["speed"]
        self.spin = QDoubleSpinBox()
        self.spin.setRange(1, 10000)
        self.spin.setDecimals(1)
        self.spin.setSuffix(" %")
        self.spin.setValue(p.value_at(0)[0])
        self.spin.valueChanged.connect(self._info)
        f.addRow(tr("Скорость"), self.spin)
        row = QHBoxLayout()
        for v in (25, 50, 100, 150, 200, 400):
            b = QPushButton(f"{v}%")
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(lambda _=False, v=v: self.spin.setValue(v))
            row.addWidget(b)
        f.addRow("", row)
        self.fit = QCheckBox(tr("Менять длину слоя вместе со скоростью"))
        self.fit.setChecked(True)
        f.addRow("", self.fit)
        self.rev = QCheckBox(tr("Обратное воспроизведение"))
        self.rev.setChecked(layer.reverse)
        f.addRow("", self.rev)
        self.blend = QCheckBox(tr("Смешивание кадров (плавнее при замедлении)"))
        self.blend.setChecked(layer.frame_blend)
        self.blend.setEnabled(layer.kind == "video")
        f.addRow("", self.blend)
        self.info = QLabel()
        self.info.setStyleSheet("color:#8a8a8a;")
        self.info.setWordWrap(True)
        f.addRow("", self.info)
        if p.animated:
            warn = QLabel(tr("У скорости есть ключи (спид-рамп) — они будут заменены одним значением."))
            warn.setStyleSheet("color:#e0a040;")
            warn.setWordWrap(True)
            f.addRow("", warn)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)
        self._info()

    def _info(self):
        l = self.layer
        if l.source_frames:
            sec = l.source_frames / self.fps * 100 / max(1, self.spin.value())
            self.info.setText(tr("Весь исходник займёт {:.2f} с. Для плавной смены скорости (спид-рамп) — "
                                 "ключи у свойства «Время → Скорость» на таймлайне.").format(sec))
