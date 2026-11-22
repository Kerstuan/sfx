# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""Главное окно: раскладка панелей как в AE, меню и все горячие клавиши."""
from __future__ import annotations

from ..i18n import EN, LANG, LANGS, set_lang, tr

import os
import time

from PySide6.QtCore import QEvent, QKeyCombination, QObject, QPointF, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QIcon, QKeySequence
from PySide6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QColorDialog, QComboBox, QDialog, QFileDialog, QFrame,
                               QHBoxLayout, QLabel, QMainWindow, QMenu, QMessageBox, QProgressDialog, QPushButton,
                               QScrollArea, QSplitter, QTabWidget, QTextBrowser, QToolBar, QToolButton, QVBoxLayout,
                               QWidget)

from ..export import ExportThread
from ..media import AUDIO_EXT, IMAGE_EXT, VIDEO_EXT
from ..effects import EFFECTS
from ..model import PROP_NAMES, timecode
from .effect_controls import EffectControlsPanel
from .icons import icon
from .panels import (MIME_ITEM, CompSettingsDialog, ProjectPanel, PropertiesPanel, SolidSettingsDialog, SpeedDialog,
                     panel)
from .render_dialog import RenderDialog
from .flow import FlowPanel
from .right_panels import AlignPanel, AudioPanel, EffectsPresetsPanel, PreviewPanel
from .widgets import Section

TOOLS = [  # (id, значок, подпись, хоткей)
    ("select", "➤", "Выделение", "V"), ("hand", "✋", "Рука", "H"), ("zoom", "🔍", "Лупа", "Z"),
    ("rotate", "⟳", "Поворот", "W"), ("orbit", "◎", "Камера: облёт (средняя кнопка — сдвиг, правая — наезд)", "C"),
    ("anchor", "✥", "Опорная точка (Pan Behind)", "Y"), ("rect", "▭", "Фигура / маска (Q — сменить)", "Q"),
    ("pen", "✒", "Перо", "G"), ("text", "T", "Текст", "Ctrl+T"),
]
SHAPE_TOOLS = ["rect", "ellipse", "star"]
SHAPE_ICONS = {"rect": "▭", "ellipse": "◯", "star": "☆"}
WORKSPACES = [("default", "По умолчанию"), ("animation", "Анимация"), ("effects", "Эффекты"),
              ("audio", "Звук"), ("minimal", "Минимум")]
from .timeline import Timeline
from .viewer import Viewer

# Чтобы хоткеи работали и на русской раскладке
RU = dict(zip("qwertyuiop[]asdfghjkl;'zxcvbnm,.", "йцукенгшщзхъфывапролджэячсмитьбю"))


def _ru_variant(seq: str) -> str | None:
    parts = seq.split("+")
    last = parts[-1]
    if len(last) == 1 and last.lower() in RU:
        parts[-1] = RU[last.lower()].upper()
        return "+".join(parts)
    return None


# Физические клавиши (код xkb = evdev + 8) -> латинская клавиша. Так хоткеи работают на любой раскладке:
# русской, украинской, белорусской и т. д. — Qt сам на не-латинской раскладке их не находит.
_SCAN = {}
for _row, _start in (("1234567890-=", 10), ("qwertyuiop[]", 24), ("asdfghjkl;'`", 38), ("zxcvbnm,./", 52)):
    for _i, _ch in enumerate(_row):
        _SCAN[_start + _i] = _ch
_SCAN[51] = "\\"
_CH2KEY = {"-": Qt.Key_Minus, "=": Qt.Key_Equal, "[": Qt.Key_BracketLeft, "]": Qt.Key_BracketRight,
           ";": Qt.Key_Semicolon, "'": Qt.Key_Apostrophe, "`": Qt.Key_QuoteLeft, ",": Qt.Key_Comma,
           ".": Qt.Key_Period, "/": Qt.Key_Slash, "\\": Qt.Key_Backslash}


_CYR2LAT = {v: k for k, v in RU.items()}
_CYR2LAT.update({"і": "s", "ї": "]", "є": "'", "ґ": "`", "ё": "`", "ў": "o"})


def latin_key_char(ch: str):
    if not ch:
        return None
    if ch.isalpha():
        return getattr(Qt, f"Key_{ch.upper()}", None)
    if ch.isdigit():
        return getattr(Qt, f"Key_{ch}")
    return _CH2KEY.get(ch)


def latin_key(scan: int):
    ch = _SCAN.get(scan)
    if ch is None:
        return None
    if ch.isalpha():
        return getattr(Qt, f"Key_{ch.upper()}")
    if ch.isdigit():
        return getattr(Qt, f"Key_{ch}")
    return _CH2KEY.get(ch)


class LayoutShortcuts(QObject):
    """Фильтр событий: если раскладка не латинская, ищем хоткей по физической клавише и запускаем его."""

    def __init__(self, window):
        super().__init__(window)
        self.win = window

    _LOG = os.environ.get("SFX_KEYLOG")

    def eventFilter(self, obj, e):
        if e.type() not in (QEvent.ShortcutOverride, QEvent.KeyPress):
            return False
        key = e.key()
        key = key.value if hasattr(key, "value") else int(key)
        if self._LOG and e.type() == QEvent.KeyPress:   # отладка раскладок: SFX_KEYLOG=файл
            try:
                with open(self._LOG, "a", encoding="utf-8") as f:
                    m = e.modifiers()
                    f.write(f"key=0x{key:x} text={e.text()!r} scan={e.nativeScanCode()} "
                            f"vk=0x{e.nativeVirtualKey():x} mods=0x{(m.value if hasattr(m, 'value') else int(m)):x} "
                            f"obj={type(obj).__name__}\n")
            except OSError:
                pass
        if key == int(Qt.Key_Escape.value if hasattr(Qt.Key_Escape, "value") else Qt.Key_Escape) and e.type() == QEvent.KeyPress:
            from PySide6.QtWidgets import QLineEdit
            fw = QApplication.focusWidget()
            if isinstance(fw, QLineEdit) and fw.window() is self.win and not fw.parent().__class__.__name__.startswith("Timeline"):
                fw.clearFocus()
                self.win.timeline.setFocus()
                return False
        if key < 0x80 or key >= 0x01000000:
            return False   # латинская клавиша или служебная (F1, стрелки…) — Qt справится сам
        lk = latin_key(e.nativeScanCode())
        if lk is None:
            # запасной путь: скан-кода нет (бывает на Wayland) — переводим букву по раскладке ЙЦУКЕН
            ch = _CYR2LAT.get((e.text() or "").lower())
            if ch is None:
                try:
                    ch = _CYR2LAT.get(chr(key).lower())
                except ValueError:
                    ch = None
            lk = latin_key_char(ch) if ch else None
        if lk is None:
            return False
        mods = e.modifiers() & (Qt.ControlModifier | Qt.ShiftModifier | Qt.AltModifier | Qt.MetaModifier)
        from PySide6.QtWidgets import QAbstractSpinBox, QLineEdit, QPlainTextEdit, QTextEdit
        fw = QApplication.focusWidget()
        typing = isinstance(fw, (QLineEdit, QPlainTextEdit, QTextEdit, QAbstractSpinBox))
        if typing and not (mods & (Qt.ControlModifier | Qt.AltModifier)):
            return False   # в полях ввода буквы печатаются как обычно
        if not self.win.isActiveWindow() and QApplication.activeModalWidget() is not None:
            return False
        seq = QKeySequence(QKeyCombination(Qt.KeyboardModifier(mods.value if hasattr(mods, "value") else int(mods)),
                                           Qt.Key(lk)))
        for a in self.win.actions():
            if not a.isEnabled():
                continue
            if any(sc.matches(seq) == QKeySequence.ExactMatch for sc in a.shortcuts()):
                if e.type() == QEvent.ShortcutOverride:
                    e.accept()           # не даём виджету съесть клавишу — дальше придёт KeyPress
                    return True
                a.trigger()
                return True
        return False


def _dur(sec: float) -> str:
    sec = int(sec)
    return f"{sec // 60}:{sec % 60:02d}"


class MainWindow(QMainWindow):
    def __init__(self, editor):
        super().__init__()
        self.ed = editor
        self.resize(1600, 950)
        self.setAcceptDrops(True)
        self._export = None
        self.actions_list: list[tuple[str, str, str]] = []  # (меню, название, хоткей) — для справки

        self.viewer = Viewer(editor)
        self.timeline = Timeline(editor)
        self.project_panel = ProjectPanel(editor)
        self.props_panel = PropertiesPanel(editor)
        self.fx_panel = EffectControlsPanel(editor)
        self.project_panel.btn.clicked.connect(self.import_dialog)
        self.project_panel.add_requested.connect(lambda ref: self.drop_items([ref]))
        self.project_panel.open_comp_requested.connect(self.ed.open_comp)
        self.project_panel.action_requested.connect(self.project_action)
        self.viewer.context_requested.connect(self.layer_menu)
        self.timeline.context_requested.connect(self.layer_menu)
        self.timeline.open_comp_requested.connect(self.ed.open_comp)
        editor.comp_switched.connect(self._comp_switched)
        self.viewer.new_comp_requested.connect(self.new_comp)
        self.viewer.from_footage_requested.connect(self.footage_comp)
        self.viewer.message.connect(lambda m: self.statusBar().showMessage(m, 5000))

        # --- панель инструментов (как верхняя полоса AE)
        self._build_toolbar()

        # --- слева вкладки: Проект | Элементы управления эффектами
        self.left_tabs = QTabWidget()
        self.left_tabs.setDocumentMode(True)
        self.left_tabs.addTab(self.project_panel, tr("Проект"))
        self.left_tabs.addTab(self.fx_panel, tr("Эффекты слоя"))
        self.left_tabs.setTabToolTip(1, tr("Элементы управления эффектами (F3). Правая кнопка — отдельной панелью"))
        self.ec_mode = "tab"           # tab | column (своя панель рядом) | window (отдельное окно)
        self._ec_box = None
        self._ec_win = None
        self.left_tabs.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self.left_tabs.tabBar().customContextMenuRequested.connect(self._tabs_menu)

        viewer_box = QWidget()
        vl = QVBoxLayout(viewer_box)
        vl.setContentsMargins(0, 0, 0, 0)
        vl.setSpacing(0)
        from PySide6.QtWidgets import QGridLayout
        self.view_grid = QWidget()
        self._grid = QGridLayout(self.view_grid)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(2)
        self._grid.addWidget(self.viewer, 0, 0)
        self.side_views = []           # боковые 3D-виды (раскладки «2 вида» / «4 вида»)
        self.view_layout = 1
        vl.addWidget(self.view_grid, 1)
        vl.addWidget(self._viewer_bar())
        self.viewer_panel = panel(tr("Композиция"), viewer_box)

        # --- справа сворачиваемые панели: Предпросмотр, Свойства, Выравнивание, Звук, Эффекты и шаблоны
        self.preview_panel = PreviewPanel(editor)
        self.align_panel = AlignPanel(editor)
        self.audio_panel = AudioPanel(editor)
        self.presets_panel = EffectsPresetsPanel(editor, lambda m: self.statusBar().showMessage(m, 3000),
                                                 on_preset=lambda pid: self.apply_preset(pid))
        self.flow_panel = FlowPanel(editor, lambda m: self.statusBar().showMessage(m, 3000))
        editor.prerender_progress.connect(self._prerender_progress)
        editor.color_pick_changed.connect(
            lambda on: self.statusBar().showMessage(tr("Пипетка: щёлкни по фону во вьювере (ПКМ — отмена)"))
            if on else self.statusBar().clearMessage())
        self.props_panel.setMinimumHeight(240)
        self.sections = {
            "preview": Section(tr("Предпросмотр"), self.preview_panel),
            "props": Section(tr("Свойства слоя"), self.props_panel),
            "align": Section(tr("Выравнивание"), self.align_panel, opened=False),
            "audio": Section(tr("Звук"), self.audio_panel, opened=False),
            "flow": Section("Flow", self.flow_panel, opened=False),
            "presets": Section(tr("Эффекты и шаблоны"), self.presets_panel),
        }
        right_inner = QWidget()
        rl = QVBoxLayout(right_inner)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(2)
        for sec in self.sections.values():
            rl.addWidget(sec)
        rl.addStretch(1)
        self.right = QScrollArea()
        self.right.setWidgetResizable(True)
        self.right.setFrameShape(QFrame.NoFrame)
        self.right.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.right.setWidget(right_inner)

        self.top = QSplitter(Qt.Horizontal)
        self.top.addWidget(self.left_tabs)
        self.top.addWidget(self.viewer_panel)
        self.top.addWidget(self.right)
        self._set_top_sizes([290, 1000, 310])
        self.top.setStretchFactor(1, 1)

        tl_box = QWidget()
        tl = QVBoxLayout(tl_box)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(0)
        from PySide6.QtWidgets import QTabBar
        self.comp_tabs = QTabBar()
        self.comp_tabs.setDocumentMode(True)
        self.comp_tabs.setExpanding(False)
        self.comp_tabs.setTabsClosable(True)
        self.comp_tabs.setFocusPolicy(Qt.NoFocus)
        self.comp_tabs.currentChanged.connect(self._tab_changed)
        self.comp_tabs.tabCloseRequested.connect(self._tab_close)
        self._open_comps: list[str] = []
        tl.addWidget(self.comp_tabs)
        tl.addWidget(self._timeline_bar())
        tl.addWidget(self.timeline, 1)
        self.timeline_panel = panel(tr("Таймлайн"), tl_box)

        self.main_split = QSplitter(Qt.Vertical)
        self.main_split.addWidget(self.top)
        self.main_split.addWidget(self.timeline_panel)
        self.main_split.setSizes([560, 390])
        for sp in (self.main_split, self.top):   # ручки между панелями шире — легко тянуть таймлайн вверх/вниз
            sp.setHandleWidth(6)
            sp.setChildrenCollapsible(False)
        self.setCentralWidget(self.main_split)

        # строка состояния: справа время рендера кадра, как Frame Render Time в AE
        self.render_time = QLabel()
        self.render_time.setStyleSheet("color:#7a7a7a; padding-right:8px;")
        self.statusBar().addPermanentWidget(self.render_time)
        self._rt_timer = QTimer(self)
        self._rt_timer.timeout.connect(
            lambda: self.render_time.setText(
                tr("Рендер кадра: {:.0f} мс").format(self.ed.render_ms) + "   ·   " +
                tr("Кэш: {n} кадр. ({mb} МБ)").format(n=len(self.ed.cache.frames),
                                                      mb=self.ed.cache.bytes // (1024 * 1024))))
        self._rt_timer.start(300)
        # прокси: прогресс в строке состояния; звуковая карта — инициализировать заранее (первый Play без паузы)
        self.proxy_label = QLabel()
        self.proxy_label.setStyleSheet("color:#7fd67f; padding-right:8px;")
        self.statusBar().addPermanentWidget(self.proxy_label)
        editor.proxies.progress.connect(self._proxy_progress)
        editor.proxies.finished.connect(self._proxy_done)
        QTimer.singleShot(800, editor.player.available)

        self._build_menus()
        self.preview_panel.idle.toggled.connect(self.idle_action.setChecked)
        self._layout_filter = LayoutShortcuts(self)
        QApplication.instance().installEventFilter(self._layout_filter)
        # поля поиска не забирают фокус сами — иначе они «съедают» буквенные хоткеи (V, P, S…)
        from PySide6.QtWidgets import QLineEdit
        for le in self.findChildren(QLineEdit):
            le.setFocusPolicy(Qt.ClickFocus)
        QTimer.singleShot(0, self.timeline.setFocus)
        editor.changed.connect(self._update_title)
        editor.changed.connect(lambda: self.mb_action.setChecked(self.ed.comp.motion_blur))
        editor.time_changed.connect(self._update_time_label)
        editor.playing_changed.connect(lambda on: self.play_btn.setIcon(icon("pause" if on else "play")))
        self._update_title()
        self._update_time_label(0)
        self.statusBar().showMessage(tr("Ctrl+I — импорт, или перетащи файлы в окно. Справка по хоткеям — F1."))

    # ================================================================ панели инструментов
    def _viewer_bar(self):
        bar = QWidget()
        bar.setObjectName("Bar")
        bar.setStyleSheet("#Bar { background:#1e1e1e; }")  # только сама панель, кнопки берут общий стиль
        h = QHBoxLayout(bar)
        h.setContentsMargins(6, 3, 6, 3)
        self.play_btn = QToolButton()
        self.play_btn.setIcon(icon("play"))
        self.play_btn.setFocusPolicy(Qt.NoFocus)
        self.play_btn.clicked.connect(self.ed.toggle_play)
        self.res = QComboBox()
        self.res.setFocusPolicy(Qt.NoFocus)
        self.res.addItems([tr(x) for x in ("Полное", "Половина", "Треть", "Четверть")])
        self.res.setCurrentIndex(1)
        self.res.currentIndexChanged.connect(self._set_res)
        self.time_label = QLabel()
        self.time_label.setStyleSheet("color:#3d9bf2; font-weight:bold;")
        self.comp_label = QLabel()
        self.comp_label.setStyleSheet("color:#7a7a7a;")
        h.addWidget(self.play_btn)
        h.addWidget(self.time_label)
        h.addStretch(1)
        h.addWidget(self.comp_label)
        from ..three import VIEWS
        self.view3d = QComboBox()
        self.view3d.setFocusPolicy(Qt.NoFocus)
        self.view3d.addItems([tr(x) for x in ("Активная камера", "Спереди", "Слева", "Сверху", "Сзади", "Справа",
                                              "Снизу", "Свой вид")])
        self.view3d.setToolTip(tr("3D-вид (как 3D View в AE): ортогональные виды и свой вид для расстановки 3D-слоёв, "
                                  "света и камеры. Рендер в файл всегда через активную камеру. "
                                  "F10 — спереди, F11 — свой вид, F12 — активная камера; в «Своём виде» инструмент C облетает вид"))
        self.view3d.currentIndexChanged.connect(lambda i: self.set_view3d(VIEWS[i]))
        h.addWidget(self.view3d)
        self.layout_box = QComboBox()
        self.layout_box.setFocusPolicy(Qt.NoFocus)
        self.layout_box.addItems([tr("1 вид"), tr("2 вида"), tr("4 вида")])
        self.layout_box.setToolTip(tr("Раскладка видов, как Select View Layout в AE: рядом с главным — виды Сверху, "
                                      "Спереди, Справа. У каждого свой список видов, колесо — масштаб, тянуть — сдвиг"))
        self.layout_box.currentIndexChanged.connect(lambda i: self.set_view_layout((1, 2, 4)[i]))
        h.addWidget(self.layout_box)
        h.addSpacing(6)
        h.addWidget(QLabel(tr("Разрешение:")))
        h.addWidget(self.res)
        self.render_btn = QPushButton(tr("⏺ Рендер"))
        self.render_btn.setToolTip(tr("Рендер в файл: формат, кодек, fps, качество (Ctrl+M)"))
        self.render_btn.setFocusPolicy(Qt.NoFocus)
        self.render_btn.setStyleSheet("QPushButton { background:#2d8ceb; color:white; font-weight:bold; "
                                      "padding:3px 14px; } QPushButton:hover { background:#4a9ef0; }")
        self.render_btn.clicked.connect(self.export_dialog)
        h.addSpacing(10)
        h.addWidget(self.render_btn)
        return bar

    def _timeline_bar(self):
        bar = QWidget()
        bar.setObjectName("Bar")
        bar.setStyleSheet("#Bar { background:#1e1e1e; }")  # только сама панель, кнопки берут общий стиль
        h = QHBoxLayout(bar)
        h.setContentsMargins(6, 2, 6, 2)

        def btn(text, tip, fn, checkable=False):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.setCheckable(checkable)
            b.setFocusPolicy(Qt.NoFocus)
            b.clicked.connect(fn)
            h.addWidget(b)
            return b

        self.graph_btn = btn(tr("Граф-эдитор"), "Shift+F3 / Alt+F3", lambda: self.toggle_graph(), True)
        self.speed_btn = btn(tr("Скорость"), tr("Показать график скорости вместо графика значений"), self.toggle_speed, True)
        self.all_btn = btn(tr("Все видимые"), tr("Граф-эдитор: все кривые раскрытых свойств сразу (а не только выбранного)"),
                           self.toggle_graph_all, True)
        h.addSpacing(8)
        btn("Easy Ease", "F9", lambda: self.ed.set_ease("ease"))
        btn("Ease In", "Shift+F9", lambda: self.ed.set_ease("in"))
        btn("Ease Out", "Ctrl+Shift+F9", lambda: self.ed.set_ease("out"))
        btn(tr("Линейно"), tr("Сделать выделенные ключи линейными"), lambda: self.ed.set_ease("linear"))
        btn("Hold", "Ctrl+Alt+H", lambda: self.ed.set_ease("hold"))
        h.addStretch(1)
        btn("−", tr("Отдалить (-)"), lambda: self.timeline.zoom(1 / 1.5))
        btn(tr("Вписать"), tr("Показать всю композицию"), self.timeline.fit)
        btn("+", tr("Приблизить (=)"), lambda: self.timeline.zoom(1.5))
        return bar

    def _build_toolbar(self):
        tb = QToolBar(tr("Инструменты"))
        tb.setMovable(False)
        tb.setObjectName("Tools")
        tb.setStyleSheet("QToolBar { background:#1e1e1e; border-bottom:1px solid #111; spacing:2px; padding:2px 6px; }"
                         " QToolButton { padding:3px 7px; min-width:22px; }")
        self.addToolBar(Qt.TopToolBarArea, tb)
        self.tool_group = QButtonGroup(self)
        self.tool_group.setExclusive(True)
        self.tool_btns = {}
        for tid, _glyph, name, key in TOOLS:
            b = QToolButton()
            b.setIcon(icon(tid))
            b.setIconSize(QSize(20, 20))
            b.setCheckable(True)
            b.setFocusPolicy(Qt.NoFocus)
            b.setToolTip(f"{tr(name)} ({key})")
            b.clicked.connect(lambda _=False, t=tid: self.set_tool(t))
            self.tool_group.addButton(b)
            tb.addWidget(b)
            self.tool_btns[tid] = b
        shape_menu = QMenu(self)
        for t, label in (("rect", tr("Прямоугольник")), ("ellipse", tr("Эллипс")), ("star", tr("Звезда"))):
            shape_menu.addAction(icon(t), label, lambda _=False, t=t: self.set_tool(t))
        self.tool_btns["rect"].setMenu(shape_menu)
        self.tool_btns["rect"].setPopupMode(QToolButton.DelayedPopup)
        self.tool_btns["select"].setChecked(True)
        tb.addSeparator()
        self.snap_cb = QCheckBox(tr("Прилипание"))
        self.snap_cb.setFocusPolicy(Qt.NoFocus)
        self.snap_cb.setToolTip(tr("Прилипать на таймлайне без Shift (с Shift — всегда)"))
        self.snap_cb.toggled.connect(lambda v: setattr(self.ed, "snapping", v))
        tb.addWidget(self.snap_cb)
        spacer = QWidget()
        spacer.setSizePolicy(spacer.sizePolicy().horizontalPolicy().Expanding, spacer.sizePolicy().verticalPolicy())
        tb.addWidget(spacer)
        self.ws_btns = {}
        self._ws_group = QButtonGroup(self)
        self._ws_bar = tb
        for wid, name in WORKSPACES:
            self._add_ws_button(wid, tr(name))
        for name in self._user_workspaces():
            self._add_ws_button("user:" + name, name)
        plus = QToolButton()
        plus.setText("+")
        plus.setToolTip(tr("Сохранить текущую раскладку окон как свою рабочую среду"))
        plus.setFocusPolicy(Qt.NoFocus)
        plus.setStyleSheet("QToolButton { border:none; background:transparent; color:#9a9a9a; padding:3px 8px; }")
        plus.clicked.connect(self.save_workspace)
        self._ws_plus = tb.addWidget(plus)
        self.ws_btns["default"].setChecked(True)
        self.ed.tool_changed.connect(self._on_tool)

    # ---- свои рабочие среды (Window → Workspace → Save as New Workspace в AE)
    def _add_ws_button(self, wid, label):
        b = QToolButton()
        b.setText(label)
        b.setCheckable(True)
        b.setFocusPolicy(Qt.NoFocus)
        b.setStyleSheet("QToolButton { border:none; background:transparent; color:#9a9a9a; padding:3px 8px; }"
                        " QToolButton:checked { color:#3d9bf2; border-bottom:2px solid #3d9bf2; }")
        b.clicked.connect(lambda _=False, w=wid: self.set_workspace(w))
        if wid.startswith("user:"):
            b.setContextMenuPolicy(Qt.CustomContextMenu)
            b.customContextMenuRequested.connect(lambda _p, w=wid: self._ws_menu(w))
            b.setToolTip(tr("Своя рабочая среда. Правая кнопка — обновить или удалить"))
        self._ws_group.addButton(b)
        if getattr(self, "_ws_plus", None) is not None:
            self._ws_bar.insertWidget(self._ws_plus, b)
        else:
            self._ws_bar.addWidget(b)
        self.ws_btns[wid] = b

    @staticmethod
    def _ws_settings():
        from PySide6.QtCore import QSettings
        return QSettings("sfx", "sfx")

    def _user_workspaces(self) -> dict:
        import json
        try:
            return json.loads(self._ws_settings().value("workspaces", "{}") or "{}")
        except (ValueError, TypeError):
            return {}

    def _layout_state(self) -> dict:
        return {"main": self.main_split.sizes(), "top": self.top.sizes(),
                "left": self.left_tabs.isVisible(), "right": self.right.isVisible(),
                "sections": {k: sec.head.isChecked() for k, sec in self.sections.items()},
                "tab": self.left_tabs.currentIndex(), "graph": self.timeline.graph_mode,
                "swap": self.top.indexOf(self.right) == 0,
                "tl_top": self.main_split.indexOf(self.timeline_panel) == 0, "ec": self.ec_mode,
                "order": [self._top_names().get(id(self.top.widget(i)), "ec") for i in range(self.top.count())]}

    def _top_names(self) -> dict:
        return {id(self.left_tabs): "left", id(self.viewer_panel): "viewer", id(self.right): "right"}

    def _apply_layout_state(self, st: dict):
        self.set_ec_mode(st.get("ec", "tab"))
        self.set_columns_swapped(bool(st.get("swap")))
        self.set_timeline_on_top(bool(st.get("tl_top")))
        # порядок колонок: «поменять местами» и «эффекты панелью» в разном порядке дают разную раскладку
        by_name = {"left": self.left_tabs, "viewer": self.viewer_panel, "right": self.right}
        if self._ec_box is not None:
            by_name["ec"] = self._ec_box
        order = st.get("order") or []
        if sorted(order) == sorted(by_name):     # старые сохранения без order или с другим набором — пропускаем
            for i, n in enumerate(order):
                self.top.insertWidget(i, by_name[n])
        self.left_tabs.setVisible(st.get("left", True))
        self.right.setVisible(st.get("right", True))
        for k, on in st.get("sections", {}).items():
            if k in self.sections:
                self.sections[k].set_open(on)
        self.left_tabs.setCurrentIndex(int(st.get("tab", 0)))
        if bool(st.get("graph")) != self.timeline.graph_mode:
            self.toggle_graph()
        if st.get("main"):
            self.main_split.setSizes(st["main"])
        if st.get("top"):
            self.top.setSizes(st["top"])

    def save_last_layout(self):
        """Раскладка панелей и размер окна — на следующий запуск."""
        import json
        st = self._ws_settings()
        st.setValue("last_layout", json.dumps(self._layout_state()))
        st.setValue("geometry", self.saveGeometry())

    def restore_last_layout(self):
        """Вызывать после show(): до показа сплиттеры пересчитывают размеры."""
        import json
        st = self._ws_settings()
        geo = st.value("geometry")
        if geo:
            self.restoreGeometry(geo)
        try:
            lay = json.loads(st.value("last_layout", "") or "null")
        except (ValueError, TypeError):
            lay = None
        if isinstance(lay, dict):
            self._apply_layout_state(lay)
        try:
            n = int(st.value("view_layout", 1))
        except (TypeError, ValueError):
            n = 1
        if n != 1:
            self.set_view_layout(n)
            # новые панели раскладываются на следующем цикле и сбивают ширины — ставим их ещё раз
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: (self.main_split.setSizes(lay.get("main") or self.main_split.sizes()),
                                          self.top.setSizes(lay.get("top") or self.top.sizes())))

    def save_workspace(self, name: str | None = None):
        import json
        from PySide6.QtWidgets import QInputDialog
        if not name:
            name, ok = QInputDialog.getText(self, tr("Новая рабочая среда"), tr("Имя рабочей среды:"),
                                            text=tr("Моя"))
            if not ok or not name.strip():
                return
        name = name.strip()
        ws = self._user_workspaces()
        ws[name] = self._layout_state()
        self._ws_settings().setValue("workspaces", json.dumps(ws))
        wid = "user:" + name
        if wid not in self.ws_btns:
            self._add_ws_button(wid, name)
        self.ws_btns[wid].setChecked(True)
        self.statusBar().showMessage(tr("Рабочая среда «{}» сохранена").format(name), 3000)

    def _ws_menu(self, wid):
        import json
        name = wid[5:]
        m = QMenu(self)
        m.setAttribute(Qt.WA_DeleteOnClose)   # иначе каждое меню ПКМ остаётся ребёнком окна навсегда
        m.addAction(tr("Сохранить текущую раскладку сюда"), lambda: self.save_workspace(name))

        def delete():
            ws = self._user_workspaces()
            ws.pop(name, None)
            self._ws_settings().setValue("workspaces", json.dumps(ws))
            b = self.ws_btns.pop(wid)
            self._ws_group.removeButton(b)
            b.deleteLater()
        m.addAction(tr("Удалить рабочую среду"), delete)
        m.exec(self.ws_btns[wid].mapToGlobal(self.ws_btns[wid].rect().bottomLeft()))

    def set_columns_swapped(self, on: bool):
        """Поменять местами левую колонку (Проект / Эффекты слоя) и правую (Предпросмотр, Эффекты…)."""
        if on == (self.top.indexOf(self.right) == 0):
            return
        sizes = self.top.sizes()
        if on:
            self.top.insertWidget(0, self.right)
            self.top.insertWidget(2, self.left_tabs)
        else:
            self.top.insertWidget(0, self.left_tabs)
            self.top.insertWidget(2, self.right)
        self.top.setSizes(list(reversed(sizes)))

    def set_timeline_on_top(self, on: bool):
        if on == (self.main_split.indexOf(self.timeline_panel) == 0):
            return
        sizes = self.main_split.sizes()
        self.main_split.insertWidget(0 if on else 1, self.timeline_panel)
        self.main_split.setSizes(list(reversed(sizes)))

    def set_tool(self, tool):
        self.ed.set_tool(tool)

    def _cycle_shape_tool(self):
        cur = self.ed.tool
        nxt = SHAPE_TOOLS[(SHAPE_TOOLS.index(cur) + 1) % 3] if cur in SHAPE_TOOLS else "rect"
        self.set_tool(nxt)

    def _on_tool(self, tool):
        btn_id = "rect" if tool in SHAPE_TOOLS else tool
        if btn_id in self.tool_btns:
            self.tool_btns[btn_id].setChecked(True)
        if tool in SHAPE_TOOLS:
            self.tool_btns["rect"].setIcon(icon(tool))
        hints = {"rect": tr("Тяни в окне композиции: на выделенный слой — маска, иначе — фигура в шейп-слой. Shift — квадрат"),
                 "ellipse": tr("Тяни в окне композиции: на выделенный слой — маска, иначе — фигура. Shift — круг"),
                 "star": tr("Тяни в окне композиции — звезда в шейп-слой"),
                 "pen": tr("Перо: клик — точка, тянуть — изгиб, клик по первой точке — замкнуть, Enter — оставить открытым"),
                 "orbit": tr("Камера: тяни — облёт, средняя кнопка или Shift — сдвиг, правая или Ctrl/колесо — наезд"),
                 "rotate": tr("Поворот: тяни слой вокруг опорной точки, Shift — шаг 45°"),
                 "anchor": tr("Опорная точка: тяни — слой остаётся на месте"),
                 "hand": tr("Рука: тяни вид, двойной клик — вписать"),
                 "zoom": tr("Лупа: клик — приблизить, Alt+клик — отдалить, рамка — приблизить область"),
                 "text": tr("Текст: клик в окне композиции — новый текстовый слой")}
        if tool in hints:
            self.statusBar().showMessage(hints[tool], 8000)

    def set_workspace(self, wid):
        """Рабочие среды как в AE: другая раскладка панелей под задачу."""
        self.ws_btns[wid].setChecked(True)
        if wid.startswith("user:"):
            st = self._user_workspaces().get(wid[5:])
            if st:
                self._apply_layout_state(st)
            return
        self.set_columns_swapped(False)
        self.set_timeline_on_top(False)
        secs = self.sections
        self.left_tabs.show()
        self.right.show()
        if wid == "default":
            self.main_split.setSizes([560, 390])
            self._set_top_sizes([290, 1000, 310])
            for k, sec in secs.items():
                sec.set_open(k in ("preview", "props", "presets"))
            self.left_tabs.setCurrentIndex(0)
        elif wid == "animation":
            self.main_split.setSizes([430, 520])
            self._set_top_sizes([260, 1040, 300])
            for k, sec in secs.items():
                sec.set_open(k in ("preview", "props", "flow"))
            if not self.timeline.graph_mode:
                self.toggle_graph()
        elif wid == "effects":
            self.main_split.setSizes([560, 390])
            self._set_top_sizes([360, 900, 340])
            for k, sec in secs.items():
                sec.set_open(k in ("preview", "presets"))
            self.set_ec_mode("column")       # среда «Эффекты»: панель эффектов всегда на виду, как в AE
        elif wid == "audio":
            self.main_split.setSizes([520, 430])
            for k, sec in secs.items():
                sec.set_open(k in ("preview", "audio", "presets"))
            self.presets_panel.search.setText("")
            self.timeline.show_props("audio")
        elif wid == "minimal":
            self.left_tabs.hide()
            self.right.hide()
            self.main_split.setSizes([600, 350])
        if wid != "animation" and self.timeline.graph_mode and wid != "minimal":
            self.toggle_graph()

    def footage_comp(self):
        """«Новая композиция из видео»: выбрать файл — композиция подстроится под него."""
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXT | IMAGE_EXT))
        path, _ = QFileDialog.getOpenFileName(self, tr("Новая композиция из видео"), os.path.expanduser("~"),
                                              tr("Медиа ({})").format(exts))
        if not path:
            return
        if self.ed.import_files([path]):
            return
        path = os.path.abspath(path)
        if not self.ed.comp.layers:
            self.add_media(path, 0)   # пустая композиция сама подстроится под ролик
        else:
            self.ed.comp_from_footage(path)
        self.timeline.fit()

    def new_comp(self):
        """«Новая композиция» со стартового экрана: настройки композиции."""
        self.comp_settings()

    def show_effect_controls(self):
        if self.ec_mode == "tab":
            self.left_tabs.show()
            self.left_tabs.setCurrentIndex(1)
        elif self.ec_mode == "column":
            self._ec_box.show()
        else:
            self._ec_win.show()
            self._ec_win.raise_()

    def _tabs_menu(self, pos):
        m = QMenu(self)
        m.setAttribute(Qt.WA_DeleteOnClose)   # иначе каждое меню ПКМ остаётся ребёнком окна навсегда
        m.addAction(tr("«Эффекты слоя» — отдельной панелью рядом"), lambda: self.set_ec_mode("column"))
        m.addAction(tr("«Эффекты слоя» — в отдельном окне"), lambda: self.set_ec_mode("window"))
        m.addAction(tr("«Эффекты слоя» — вкладкой рядом с «Проектом»"), lambda: self.set_ec_mode("tab"))
        m.exec(self.left_tabs.tabBar().mapToGlobal(pos))

    def _set_top_sizes(self, three):
        """Размеры колонок «левая / вьювер / правая»; отдельная панель «Эффекты слоя» получает свою долю."""
        left, view, right = three
        order = [self.top.widget(i) for i in range(self.top.count())]
        width = {self.left_tabs: left, self.viewer_panel: view, self.right: right}
        if self._ec_box is not None:
            width[self._ec_box] = 320
            width[self.viewer_panel] = max(400, view - 320)
        self.top.setSizes([width.get(w, 300) for w in order])

    def set_ec_mode(self, mode: str):
        """Где живёт панель «Эффекты слоя»: вкладкой, отдельной панелью (как в AE рядом с Project)
        или в отдельном окне, которое можно поставить хоть на второй монитор."""
        if mode == self.ec_mode:
            self.show_effect_controls()
            return
        from PySide6.QtWidgets import QVBoxLayout, QWidget
        from .panels import panel
        # вынимаем панель оттуда, где она сейчас
        if self.ec_mode == "tab":
            self.left_tabs.removeTab(self.left_tabs.indexOf(self.fx_panel))
        elif self.ec_mode == "column" and self._ec_box is not None:
            self.fx_panel.setParent(None)
            self._ec_box.hide()
            self._ec_box.setParent(None)
            self._ec_box.deleteLater()
            self._ec_box = None
        elif self.ec_mode == "window" and self._ec_win is not None:
            self.fx_panel.setParent(None)
            self._ec_win.hide()
            self._ec_win.deleteLater()
            self._ec_win = None
        self.ec_mode = mode
        if mode == "tab":
            self.left_tabs.insertTab(1, self.fx_panel, tr("Эффекты слоя"))
            self.left_tabs.setCurrentIndex(1)
        elif mode == "column":
            self._ec_box = panel(tr("Эффекты слоя"), self.fx_panel)
            self.top.insertWidget(self.top.indexOf(self.left_tabs) + 1, self._ec_box)
            sizes = self.top.sizes()
            i = self.top.indexOf(self._ec_box)
            sizes[i] = 320
            self.top.setSizes(sizes)
            self.fx_panel.show()
        else:
            w = QWidget(self, Qt.Tool)
            w.setWindowTitle(tr("Эффекты слоя"))
            lay = QVBoxLayout(w)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.addWidget(self.fx_panel)
            self.fx_panel.show()
            w.resize(360, 600)
            w.show()
            self._ec_win = w

    def _selected_prop(self):
        """Свойство, выбранное кликом по названию в таймлайне (для «Добавить выражение»)."""
        for lid, key in sorted(self.timeline.graph_props):
            l = self.ed.comp.layer_by_id(lid)
            if l and key in l.props:
                return l, key
        return None, None

    def add_expression(self):
        l, key = self._selected_prop()
        if l is None:
            self.statusBar().showMessage(tr("Кликни по названию свойства в таймлайне, затем Alt+Shift+= (или Alt+клик по секундомеру)"), 5000)
            return
        p = l.props[key]
        self.ed.snapshot()
        if not p.expr:
            p.expr, p.expr_on = "value", True
        self.timeline.shown.setdefault(l.id, [])
        if key not in self.timeline.shown[l.id]:
            self.timeline.shown[l.id].append(key)
        self.ed.notify()
        self.timeline._open_expr_after_paint = (l, key)
        self.timeline.update()

    def new_mask(self):
        l = self.ed.current()
        if l is None or l.kind in ("audio", "camera", "light"):
            self.statusBar().showMessage(tr("Сначала выдели слой"), 3000)
            return
        self.ed.snapshot()
        x, y, w, h = l.local_rect()
        self.ed.add_mask(l, "rect", rect=(x, y, w, h))
        self.ed.notify()
        self.timeline.show_props("masks", additive=True)

    def toggle_mask_invert(self):
        l = self.ed.current()
        if l is None or not l.masks:
            return
        self.ed.snapshot()
        for m in l.masks:
            m["inverted"] = not m.get("inverted")
        self.ed.notify()

    def remove_masks(self):
        l = self.ed.current()
        if l is None or not l.masks:
            return
        for m in list(l.masks):
            self.ed.remove_mask(l, m["id"])

    def new_shape_layer(self):
        self.ed.snapshot()
        l = self.ed.add_shape_layer()
        c = self.ed.comp
        self.ed.add_shape(l, "rect", rect=(c.width / 2 - 150, c.height / 2 - 100, 300, 200))
        self.ed.notify()
        self.ed.selection_changed.emit()

    def toggle_audio_levels(self):
        """L — уровни звука и волна выделенных слоёв (как L / LL в AE)."""
        self.timeline.show_props("audio")

    def _set_res(self, i):
        self.ed.preview_scale = [1.0, 0.5, 1 / 3, 0.25][i]
        self.viewer.invalidate()

    def _toggle_layers_at_start(self, on=None):
        on = self._act_layers_at_start.isChecked() if on is None or not isinstance(on, bool) else on
        self.ed.layers_at_start = bool(on)
        self._ws_settings().setValue("layers_at_start", bool(on))

    def set_view_layout(self, n: int):
        """1, 2 или 4 вида. Боковые виды создаются один раз и потом только прячутся."""
        from .side_view import SideView
        n = n if n in (1, 2, 4) else 1
        defaults = ("top", "front", "right")
        while len(self.side_views) < n - 1:
            sv = SideView(self.ed, defaults[len(self.side_views)])
            self.side_views.append(sv)
        cells = [(0, 1), (1, 0), (1, 1)] if n == 4 else [(0, 1)]
        for i, sv in enumerate(self.side_views):
            self._grid.removeWidget(sv)
            if i < n - 1:
                self._grid.addWidget(sv, *cells[i])
                sv.show()
                sv.schedule()
            else:
                sv.hide()
        for k in range(2):                 # пустые строки/колонки не занимают места
            self._grid.setRowStretch(k, 1 if (k == 0 or n == 4) else 0)
            self._grid.setColumnStretch(k, 1 if (k == 0 or n > 1) else 0)
        self.view_layout = n
        self._ws_settings().setValue("view_layout", n)
        if hasattr(self, "layout_box") and self.layout_box.currentIndex() != (1, 2, 4).index(n):
            self.layout_box.blockSignals(True)
            self.layout_box.setCurrentIndex((1, 2, 4).index(n))
            self.layout_box.blockSignals(False)

    def set_view3d(self, view: str):
        """3D-вид вьювера для открытой композиции (хранится в ней и сохраняется в проекте; экспорт его не видит)."""
        c = self.ed.comp
        if getattr(c, "view3d", "active") != view:
            c.view3d = view
            from ..three import fit_ortho
            if view not in ("active", "custom") and view not in (getattr(c, "view_ortho", None) or {}):
                fit_ortho(c, view, self.ed.time)      # первый раз — вписать сцену, дальше вид помнит масштаб
            self.ed.notify()
            self.viewer.invalidate()
        self._sync_view3d()

    def _sync_view3d(self):
        from ..three import VIEWS
        v = getattr(self.ed.comp, "view3d", "active")
        i = VIEWS.index(v) if v in VIEWS else 0
        if hasattr(self, "view3d") and self.view3d.currentIndex() != i:
            self.view3d.blockSignals(True)
            self.view3d.setCurrentIndex(i)
            self.view3d.blockSignals(False)

    def _update_time_label(self, f):
        self.time_label.setText(timecode(f, self.ed.comp.fps))
        c = self.ed.comp
        self._sync_view3d()
        self.comp_label.setText(f"{c.width}×{c.height}, {c.fps:g} fps, {timecode(c.duration, c.fps)}")

    def _update_title(self):
        name = os.path.basename(self.ed.path) if self.ed.path else tr("Без названия")
        self.setWindowTitle(f"{tr('SFX')} — {name}{' *' if self.ed.dirty else ''}")
        self._update_time_label(self.ed.time)

    # ================================================================ меню и хоткеи
    def _act(self, menu, text, shortcut, fn, menu_name):
        a = QAction(text, self)
        seqs = []
        for s in ([shortcut] if isinstance(shortcut, str) else (shortcut or [])):
            seqs.append(QKeySequence(s))
            ru = _ru_variant(s)
            if ru:
                seqs.append(QKeySequence(ru))
        if seqs:
            a.setShortcuts(seqs)
            a.setShortcutContext(Qt.WindowShortcut)
        a.triggered.connect(fn)
        menu.addAction(a)
        self.addAction(a)
        label = shortcut if isinstance(shortcut, str) else " / ".join(shortcut or [])
        self.actions_list.append((menu_name, text, label or ""))
        return a

    def _build_menus(self):
        ed, tl = self.ed, self.timeline
        mb = self.menuBar()

        m = mb.addMenu(tr("Файл"))
        A = lambda *a: self._act(m, *a, tr("Файл"))  # noqa: E731
        A(tr("Проекты (главный экран)…"), None, self.show_home)
        A(tr("Новый проект"), "Ctrl+Alt+N", self.new_project)
        A(tr("Новая композиция…"), "Ctrl+N", self.new_comp_dialog)
        A(tr("Открыть проект…"), "Ctrl+O", self.open_dialog)
        A(tr("Сохранить"), "Ctrl+S", self.save)
        A(tr("Сохранить как…"), "Ctrl+Shift+S", self.save_as)
        m.addSeparator()
        A(tr("Импорт…"), "Ctrl+I", self.import_dialog)
        A(tr("Рендер в файл…"), "Ctrl+M", self.export_dialog)
        m.addSeparator()
        A(tr("Выход"), "Ctrl+Q", self.close)

        m = mb.addMenu(tr("Правка"))
        A = lambda *a: self._act(m, *a, tr("Правка"))  # noqa: E731
        A(tr("Отменить"), "Ctrl+Z", ed.undo)
        A(tr("Повторить"), "Ctrl+Shift+Z", ed.redo)
        m.addSeparator()
        A(tr("Копировать"), "Ctrl+C", self.copy_sel)
        A(tr("Вырезать"), "Ctrl+X", self.cut_sel)
        A(tr("Вставить"), "Ctrl+V", self.paste_sel)
        A(tr("Дублировать"), "Ctrl+D", ed.duplicate)
        A(tr("Разрезать слой"), "Ctrl+Shift+D", ed.split)
        A(tr("Удалить"), ["Del", "Backspace"], ed.delete_selection)
        A(tr("Удалить со сдвигом (Ripple Delete)"), "Shift+Del", ed.ripple_delete)
        A(tr("Поднять рабочую область (Lift Work Area)"), None, lambda: ed.lift_work_area(False))
        A(tr("Извлечь рабочую область (Extract Work Area)"), None, lambda: ed.lift_work_area(True))
        m.addSeparator()
        a = A(tr("Создавать слои в начале композиции"), None, self._toggle_layers_at_start)
        a.setCheckable(True)
        ed.layers_at_start = self._ws_settings().value("layers_at_start", True, type=bool)
        a.setChecked(ed.layers_at_start)
        a.setToolTip(tr("Как настройка AE «Create Layers at Composition Start Time»: импорт, двойной клик в «Проекте», "
                        "новые солиды и текст начинаются с 0:00. Выключено — под курсором. При перетаскивании "
                        "на таймлайн слой встаёт туда, куда отпустили"))
        self._act_layers_at_start = a
        m.addSeparator()
        A(tr("Выделить все слои"), "Ctrl+A", ed.select_all)
        A(tr("Выделить все ключи"), "Ctrl+Alt+A", ed.select_all_keys)
        A(tr("Снять выделение"), "F2", ed.deselect)
        A(tr("Слой выше"), "Ctrl+Up", lambda: ed.select_neighbor(-1))
        A(tr("Слой ниже"), "Ctrl+Down", lambda: ed.select_neighbor(1))

        m = mb.addMenu(tr("Композиция"))
        A = lambda *a: self._act(m, *a, tr("Композиция"))  # noqa: E731
        A(tr("Настройки композиции…"), "Ctrl+K", self.comp_settings)
        A(tr("Цвет фона композиции…"), "Ctrl+Shift+B", self.comp_bg_color)
        A(tr("Прекомпоновать…"), "Ctrl+Shift+C", self.precompose)
        A(tr("Начало рабочей области"), "B", lambda: ed.set_work("in"))
        A(tr("Конец рабочей области"), "N", lambda: ed.set_work("out"))
        A(tr("К началу рабочей области"), "Shift+Home", lambda: ed.set_time(ed.comp.work_in))
        A(tr("К концу рабочей области"), "Shift+End", lambda: ed.set_time(ed.comp.work_out - 1))
        A(tr("Рабочая область по выделенным слоям"), "Ctrl+Alt+B", ed.work_area_to_selection)
        A(tr("Рабочая область — вся композиция"), None, lambda: ed.set_work("all"))
        A(tr("Добавить маркер"), ["*", "Shift+*"], ed.add_marker_auto)   # выделены слои — маркер слоя
        A(tr("Удалить все маркеры"), None, self._clear_markers)
        A(tr("Перейти ко времени…"), "Alt+Shift+J", self._goto_time_dialog)
        m.addSeparator()
        A(tr("Воспроизвести / пауза"), "Space", ed.toggle_play)
        A(tr("Пререндер в память и проиграть (RAM Preview)"), "Num+0", ed.ram_preview)
        A(tr("Очистить кэш кадров"), None, self._purge_cache)
        m.addSeparator()
        self.proxy_action = A(tr("Прокси для превью (быстрая перемотка)"), None, self._toggle_proxies)
        self.proxy_action.setCheckable(True)
        self.proxy_action.setChecked(ed.proxies.enabled)
        A(tr("Удалить все прокси"), None, self._clear_proxies)
        self.idle_action = A(tr("Пререндер в фоне (в простое)"), None,
                             lambda: self.preview_panel.idle.setChecked(self.idle_action.isChecked()))
        self.idle_action.setCheckable(True)
        self.idle_action.setChecked(ed.idle_cache)
        A(tr("В начало"), "Home", lambda: ed.set_time(0))
        A(tr("В конец"), "End", lambda: ed.set_time(ed.comp.duration - 1))
        A(tr("Следующий кадр"), ["PgDown", "Ctrl+Right"], lambda: ed.step(1))
        A(tr("Предыдущий кадр"), ["PgUp", "Ctrl+Left"], lambda: ed.step(-1))
        A(tr("+10 кадров"), ["Shift+PgDown", "Ctrl+Shift+Right"], lambda: ed.step(10))
        A(tr("−10 кадров"), ["Shift+PgUp", "Ctrl+Shift+Left"], lambda: ed.step(-10))
        A(tr("Следующий ключ"), "K", lambda: ed.goto_key(1))
        A(tr("Предыдущий ключ"), "J", lambda: ed.goto_key(-1))
        A(tr("К началу слоя"), "I", self._goto_in)
        A(tr("К концу слоя"), "O", self._goto_out)
        m.addSeparator()
        A(tr("Разрешение: полное"), "Ctrl+J", lambda: self.res.setCurrentIndex(0))
        A(tr("Разрешение: половина"), "Ctrl+Shift+J", lambda: self.res.setCurrentIndex(1))
        A(tr("Разрешение: четверть"), "Ctrl+Alt+Shift+J", lambda: self.res.setCurrentIndex(3))
        m.addSeparator()
        self.mb_action = A(tr("Размытие в движении (вся композиция)"), None, self._toggle_comp_mb)
        self.mb_action.setCheckable(True)
        self.mb_action.setChecked(ed.comp.motion_blur)

        m = mb.addMenu(tr("Слой"))
        A = lambda *a: self._act(m, *a, tr("Слой"))  # noqa: E731
        A(tr("Новый текст"), "Ctrl+Alt+Shift+T", ed.add_text)
        A(tr("Новый солид…"), "Ctrl+Y", self.new_solid)
        A(tr("Настройки солида…"), "Ctrl+Shift+Y", self.solid_settings)
        A(tr("Новый нуль-объект"), "Ctrl+Alt+Shift+Y", self.ed.add_null)
        m.addSeparator()
        A(tr("Скорость клипа (ускорить / замедлить)…"), "Ctrl+R", self.speed_dialog)
        A(tr("Спид-рамп: показать скорость с ключами"), None, self.show_speed)
        A(tr("Обратное воспроизведение"), None, lambda: self.ed.toggle_layer_flag(self.ed.selected(), "reverse"))
        A(tr("Смешивание кадров"), None, lambda: self.ed.toggle_layer_flag(self.ed.selected(), "frame_blend"))
        A(tr("Включить переназначение времени (Time Remap)"), "Ctrl+Alt+T", self.time_remap)
        A(tr("Стоп-кадр с удержанием…"), "Shift+Alt+F", self.freeze_dialog)
        A(tr("Стоп-кадр до конца слоя (Freeze Frame)"), None, lambda: self._freeze())
        A(tr("Стоп-кадр на последнем кадре"), None, lambda: self._freeze(last=True))
        A(tr("Новый корректирующий слой"), "Ctrl+Alt+Y", ed.add_adjustment)
        A(tr("Новый шейп-слой"), None, self.new_shape_layer)
        A(tr("Новая камера"), "Ctrl+Alt+Shift+C", ed.add_camera)
        A(tr("Новый свет"), "Ctrl+Alt+Shift+L", lambda: ed.add_light())
        m.addSeparator()
        A(tr("3D-слой вкл/выкл"), None, lambda: ed.set_3d(ed.selected()))
        A(tr("Новая маска"), "Ctrl+Shift+N", self.new_mask)
        A(tr("Инвертировать маски"), "Ctrl+Shift+I", self.toggle_mask_invert)
        A(tr("Удалить маски слоя"), None, self.remove_masks)
        m.addSeparator()
        A(tr("Соло"), None, lambda: ed.toggle_switch("solo"))
        A(tr("Заблокировать / разблокировать"), "Ctrl+L", lambda: ed.toggle_switch("locked"))
        A(tr("Разблокировать все слои"), "Ctrl+Shift+L", ed.unlock_all)
        A(tr("Размытие в движении"), None, lambda: ed.toggle_switch("motion_blur"))
        A(tr("Корректирующий слой"), None, lambda: ed.toggle_switch("adjustment"))
        A(tr("Убрать родителя"), None, lambda: [ed.set_parent(l, None) for l in ed.selected()])
        m.addSeparator()
        A(tr("Начало слоя к курсору"), "[", lambda: ed.move_layers_to_time("in"))
        A(tr("Конец слоя к курсору"), "]", lambda: ed.move_layers_to_time("out"))
        A(tr("Обрезать начало по курсору"), "Alt+[", lambda: ed.trim("in"))
        A(tr("Обрезать конец по курсору"), "Alt+]", lambda: ed.trim("out"))
        m.addSeparator()
        A(tr("Поднять слой"), "Ctrl+]", lambda: ed.reorder(1))
        A(tr("Опустить слой"), "Ctrl+[", lambda: ed.reorder(-1))
        A(tr("Наверх"), "Ctrl+Shift+]", lambda: ed.reorder(10_000))
        A(tr("Вниз"), "Ctrl+Shift+[", lambda: ed.reorder(-10_000))
        A(tr("Вписать в композицию"), "Ctrl+Alt+F", ed.fit_to_comp)
        A(tr("Опорную точку в центр слоя"), "Ctrl+Alt+Home", ed.center_anchor)
        A(tr("Слой в центр композиции"), "Ctrl+Home", ed.center_layer)
        A(tr("Развернуть слой во времени (реверс)"), "Ctrl+Alt+R",
          lambda: ed.toggle_layer_flag(ed.selected(), "reverse"))

        m = mb.addMenu(tr("Анимация"))
        A = lambda *a: self._act(m, *a, tr("Анимация"))  # noqa: E731
        for letter, key in (("P", "position"), ("S", "scale"), ("R", "rotation"), ("T", "opacity"), ("A", "anchor")):

            A(tr("Показать: {}").format(PROP_NAMES[key]), letter, lambda _=False, k=key: tl.show_props(k))
            A(tr("Добавить к показанным: {}").format(PROP_NAMES[key]), f"Shift+{letter}", lambda _=False, k=key: tl.show_props(k, True))
            A(tr("Ключ: {}").format(PROP_NAMES[key]), f"Alt+Shift+{letter}", lambda _=False, k=key: ed.add_keyframes(k))
            m.addSeparator()
        A(tr("Показать анимированные свойства"), "U", lambda: tl.show_props("animated"))
        A(tr("Показать эффекты"), "E", lambda: tl.show_props("effects"))
        A(tr("Показать маски"), "M", lambda: tl.show_props("masks"))
        A(tr("Показать уровни звука и волну"), "L", self.toggle_audio_levels)
        m.addSeparator()
        A(tr("Добавить выражение к выбранному свойству"), "Alt+Shift+=", self.add_expression)
        m.addSeparator()
        A("Easy Ease", "F9", lambda: ed.set_ease("ease"))
        A("Easy Ease In", "Shift+F9", lambda: ed.set_ease("in"))
        A("Easy Ease Out", "Ctrl+Shift+F9", lambda: ed.set_ease("out"))
        A(tr("Hold (заморозка)"), "Ctrl+Alt+H", lambda: ed.set_ease("hold"))
        A(tr("Развернуть ключи во времени"), None, ed.time_reverse_keys)
        A(tr("Flow: применить текущую кривую"), "Ctrl+Shift+F", self.flow_panel.apply)
        A(tr("Flow: прочитать кривую с ключей"), "Ctrl+Alt+Shift+F", self.flow_panel.read)
        m.addSeparator()
        A(tr("Расставить слои по очереди…"), None, self.sequence_dialog)
        A(tr("Сдвинуть слои по очереди (offset)…"), None, self.stagger_dialog)
        A(tr("Линейная интерполяция"), None, lambda: ed.set_ease("linear"))

        m = mb.addMenu(tr("Эффект"))
        A = lambda *a: self._act(m, *a, tr("Эффект"))  # noqa: E731
        from ..effects import CATEGORIES, category_label
        for cat in CATEGORIES:
            sub = m.addMenu(category_label(cat))
            for fx_type, d in EFFECTS.items():
                if d.category == cat:
                    self._act(sub, d.label, None, lambda _=False, t=fx_type: self._add_effect(t), tr("Эффект"))
        m.addSeparator()
        A(tr("Удалить все эффекты"), "Ctrl+Alt+Shift+E", ed.clear_effects)
        A(tr("Быстрый поиск эффектов (FX Console)"), "Ctrl+Space", self.fx_console)
        A(tr("Плагины…"), None, self.plugins_dialog)

        m = mb.addMenu(tr("Вид"))
        A = lambda *a: self._act(m, *a, tr("Вид"))  # noqa: E731
        A(tr("Граф-эдитор"), ["Shift+F3", "Alt+F3"], self.toggle_graph)
        A(tr("Элементы управления эффектами"), "F3", self.show_effect_controls)
        A(tr("Панель Flow (кривые)"), "F4", self.show_flow)
        A(tr("Вписать композицию в окно"), "Shift+/", self.viewer.fit)
        A(tr("3D-вид: спереди"), "F10", lambda: self.set_view3d("front"))
        A(tr("3D-вид: свой вид"), "F11", lambda: self.set_view3d("custom"))
        A(tr("3D-вид: активная камера"), "F12", lambda: self.set_view3d("active"))
        A(tr("Раскладка: 1 вид"), None, lambda: self.set_view_layout(1))
        A(tr("Раскладка: 2 вида"), None, lambda: self.set_view_layout(2))
        A(tr("Раскладка: 4 вида"), None, lambda: self.set_view_layout(4))
        A(tr("Развернуть панель под курсором на всё окно"), "`", self.toggle_maximize_panel)
        self.grid_action = A(tr("Сетка прозрачности"), "Ctrl+Shift+H", self._toggle_grid)
        self.grid_action.setCheckable(True)
        A(tr("Приблизить вид"), ".", lambda: self.viewer.zoom_at(1.5))
        A(tr("Отдалить вид"), ",", lambda: self.viewer.zoom_at(1 / 1.5))
        m.addSeparator()
        ws = m.addMenu(tr("Рабочая среда"))
        for wid, name in WORKSPACES:
            ws.addAction(tr(name), lambda _=False, w=wid: self.set_workspace(w))
        ws.addSeparator()
        ws.addAction(tr("Сохранить как новую рабочую среду…"), lambda: self.save_workspace())
        A(tr("Поменять местами левую и правую панели"), None,
          lambda: self.set_columns_swapped(self.top.indexOf(self.right) != 0))
        A(tr("«Эффекты слоя» — отдельной панелью"), None, lambda: self.set_ec_mode("column"))
        A(tr("«Эффекты слоя» — в отдельном окне"), None, lambda: self.set_ec_mode("window"))
        A(tr("«Эффекты слоя» — вкладкой"), None, lambda: self.set_ec_mode("tab"))
        A(tr("Таймлайн сверху / снизу"), None,
          lambda: self.set_timeline_on_top(self.main_split.indexOf(self.timeline_panel) != 0))

        m = mb.addMenu(tr("Инструменты"))
        A = lambda *a: self._act(m, *a, tr("Инструменты"))  # noqa: E731
        for tid, glyph, name, key in TOOLS:
            if tid == "rect":
                A(tr("Фигура / маска (прямоугольник → эллипс → звезда)"), "Q", self._cycle_shape_tool)
            else:
                A(f"{glyph}  {tr(name)}", key, lambda _=False, t=tid: self.set_tool(t))
        m.addSeparator()
        A(tr("Roto Brush (своя нейросеть SAM 2)…"), "Alt+W", self.open_roto)
        A(tr("3D-трекер камеры…"), "Ctrl+Alt+Shift+K", self.open_tracker)
        m.addSeparator()
        A(tr("Приблизить таймлайн"), "=", lambda: tl.zoom(1.5))
        A(tr("Отдалить таймлайн"), "-", lambda: tl.zoom(1 / 1.5))
        A(tr("Вся композиция на таймлайне"), "Shift+=", tl.fit)

        m = mb.addMenu(tr("Справка"))
        A = lambda *a: self._act(m, *a, tr("Справка"))  # noqa: E731
        A(tr("Горячие клавиши"), "F1", self.show_help)

        lm = mb.addMenu(tr("Язык / Language"))
        group = QActionGroup(self)
        group.setExclusive(True)
        for code, title in LANGS.items():
            a = QAction(title, self, checkable=True)
            a.setChecked(code == LANG)
            a.triggered.connect(lambda _=False, c=code: self.choose_lang(c))
            group.addAction(a)
            lm.addAction(a)

    def choose_lang(self, code):
        set_lang(code)
        if code != LANG:
            msg = {"ru": "Язык изменится после перезапуска программы.",
                   "en": "The language will change after restarting the app."}[code]
            QMessageBox.information(self, "Language / Язык", msg)

    def _toggle_comp_mb(self):
        self.ed.snapshot()
        self.ed.comp.motion_blur = self.mb_action.isChecked()
        self.ed.notify()

    def _add_effect(self, fx_type):
        if not self.ed.selected():
            self.statusBar().showMessage(tr("Сначала выдели слой"), 3000)
            return
        if not self.ed.add_effect(fx_type):
            self.statusBar().showMessage(tr("Этот эффект не подходит выделенному слою"), 3000)
            return
        self.show_effect_controls()

    # ================================================================ действия
    def _goto_in(self):
        l = self.ed.current()
        if l:
            self.ed.set_time(l.in_point)

    def _goto_out(self):
        l = self.ed.current()
        if l:
            self.ed.set_time(l.out_point - 1)

    def show_flow(self):
        sec = self.sections["flow"]
        self.right.show()
        sec.set_open(not sec.head.isChecked() or not sec.isVisible())
        if sec.head.isChecked():
            QTimer.singleShot(0, lambda: self.right.ensureWidgetVisible(sec, 0, 0))

    def toggle_graph(self):
        self.timeline.graph_mode = not self.timeline.graph_mode
        self.graph_btn.setChecked(self.timeline.graph_mode)
        self.timeline.update()

    def toggle_graph_all(self):
        self.timeline.graph_all = self.all_btn.isChecked()
        if self.timeline.graph_all and not self.timeline.graph_mode:
            self.toggle_graph()
        self.timeline.update()

    def toggle_speed(self):
        self.timeline.speed_graph = self.speed_btn.isChecked()
        if self.timeline.speed_graph and not self.timeline.graph_mode:
            self.toggle_graph()
        self.timeline.update()

    def new_solid(self):
        """Ctrl+Y — окно «Настройки солида», как в AE."""
        dlg = SolidSettingsDialog(self.ed.comp, self)
        if dlg.exec() == QDialog.Accepted:
            name, w, h, col = dlg.values()
            self.ed.add_solid(col, name, w, h)

    def solid_settings(self):
        """Ctrl+Shift+Y — изменить выделенный солид."""
        l = self.ed.current()
        if l is None or l.kind != "solid" or l.adjustment:
            self.statusBar().showMessage(tr("Выдели солид"), 3000)
            return
        dlg = SolidSettingsDialog(self.ed.comp, self, layer=l)
        if dlg.exec() == QDialog.Accepted:
            self.ed.update_solid(l, *dlg.values())

    def comp_bg_color(self):
        """Ctrl+Shift+B — цвет фона композиции."""
        col = QColorDialog.getColor(QColor(self.ed.comp.bg), self, tr("Цвет фона композиции"))
        if col.isValid():
            self.ed.snapshot()
            self.ed.comp.bg = col.name()
            self.ed.notify()

    def new_comp_dialog(self):
        """Ctrl+N — новая композиция с настройками."""
        from ..model import Composition
        tmp = Composition(tr("Композиция {}").format(len(self.ed.project.comps) + 1), self.ed.comp.width,
                          self.ed.comp.height, self.ed.comp.fps, self.ed.comp.duration)
        dlg = CompSettingsDialog(tmp, self)
        dlg.setWindowTitle(tr("Новая композиция"))
        if dlg.exec() == QDialog.Accepted:
            dlg.apply(tmp)
            self.ed.new_comp(tmp.name, tmp.width, tmp.height, tmp.fps, tmp.duration, tmp.bg)
            self.timeline.fit()

    def precompose(self):
        """Ctrl+Shift+C — окно «Прекомпоновать», как в AE."""
        sel = self.ed.selected()
        if not sel:
            self.statusBar().showMessage(tr("Сначала выдели слои"), 3000)
            return
        from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QLineEdit, QRadioButton,
                                       QVBoxLayout, QLabel)
        n = sum(1 for c in self.ed.project.comps if c.name.startswith(tr("Прекомп"))) + 1
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Прекомпоновать"))
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(tr("Имя новой композиции:")))
        name = QLineEdit(tr("Прекомп {}").format(n))
        name.selectAll()
        v.addWidget(name)
        one = len(sel) == 1 and sel[0].kind not in ("camera", "light", "null", "audio")
        leave = QRadioButton(tr("Оставить все атрибуты в «{}»").format(self.ed.comp.name))
        leave.setToolTip(tr("Эффекты, маски и трансформация остаются снаружи, внутри — чистый клип"))
        move = QRadioButton(tr("Переместить все атрибуты в новую композицию"))
        leave.setEnabled(one)
        move.setChecked(True)
        v.addWidget(leave)
        v.addWidget(move)
        fit = QCheckBox(tr("Подогнать длину композиции под выделенные слои"))
        fit.setToolTip(tr("Прекомп длиной ровно с клипы и на их месте — а не до конца композиции"))
        fit.setChecked(True)
        v.addWidget(fit)
        opn = QCheckBox(tr("Открыть новую композицию"))
        v.addWidget(opn)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        if dlg.exec() == QDialog.Accepted:
            self.ed.precompose(name.text().strip() or None, fit_duration=fit.isChecked(),
                               leave_attrs=leave.isChecked(), open_new=opn.isChecked())
            self.statusBar().showMessage(tr("Готово. Двойной клик по слою-прекомпу — открыть его"), 5000)

    # --- вкладки композиций -------------------------------------------------
    def _comp_switched(self):
        cid = self.ed.project.active
        self._open_comps = [c for c in self._open_comps if self.ed.project.comp_by_id(c)]
        if cid not in self._open_comps:
            self._open_comps.append(cid)
        self.comp_tabs.blockSignals(True)
        while self.comp_tabs.count():
            self.comp_tabs.removeTab(0)
        for c_id in self._open_comps:
            c = self.ed.project.comp_by_id(c_id)
            i = self.comp_tabs.addTab(icon("comp"), c.name)
            self.comp_tabs.setTabData(i, c_id)
        self.comp_tabs.setCurrentIndex(self._open_comps.index(cid))
        self.comp_tabs.blockSignals(False)
        # Масштаб таймлайна и вьювера помним для каждой композиции: Ctrl+Z тоже шлёт этот сигнал,
        # и раньше после отмены приходилось заново приближать.
        views = self.__dict__.setdefault("_comp_views", {})
        prev = getattr(self, "_view_comp", None)
        if prev is None:              # первый сигнал после запуска — вид уже настроен, просто запоминаем
            self._view_comp = prev = cid
        if prev == cid:
            self._update_title()
            return
        if prev is not None:
            views[prev] = (self.timeline.ppf, self.timeline.view_start, self.viewer.vzoom, QPointF(self.viewer.vpan))
        self._view_comp = cid
        if cid in views:
            self.timeline.ppf, self.timeline.view_start, self.viewer.vzoom, self.viewer.vpan = views[cid]
            self.timeline.update()
            self.viewer.update()
        else:
            self.viewer.fit()
            self.timeline.fit()
        self._update_title()

    def _tab_changed(self, i):
        if i >= 0:
            self.ed.open_comp(self.comp_tabs.tabData(i))

    def _tab_close(self, i):
        cid = self.comp_tabs.tabData(i)
        if len(self._open_comps) <= 1:
            return
        self._open_comps.remove(cid)
        if cid == self.ed.project.active:
            self.ed.open_comp(self._open_comps[max(0, i - 1)])
        else:
            self._comp_switched()

    # --- перетаскивание из «Проекта» и контекстные меню ---------------------
    def drop_items(self, refs, at=None, point=None):
        """Элементы из «Проекта» в текущую композицию: файлы — слоями, композиции — прекомпами."""
        added = []
        if at is None:                      # несколько элементов — встают друг за другом от начала по настройке
            at = self.ed.new_layer_time()
        for ref in refs:
            if ref.startswith("comp:"):
                l = self.ed.add_comp_layer(ref[5:], at)
                if l is None:
                    self.statusBar().showMessage(tr("Нельзя вложить композицию саму в себя"), 4000)
            else:
                path = ref[6:] if ref.startswith("media:") else ref
                l = self.add_media(path, at)
            if l is not None:
                added.append(l)
                if point is not None and l.kind not in ("audio",):
                    l.props["position"].value = [round(point.x(), 1), round(point.y(), 1)] + l.props["position"].value[2:]
                if l.kind in ("video", "audio", "comp") and at is not None:
                    at = l.out_point
        if added and point is not None:
            self.ed.notify()
        return added

    def handle_drop(self, mime, at=None, point=None, target=None):
        """Сюда приходят drop'ы окна композиции и таймлайна: элементы проекта, файлы с диска,
        эффекты и шаблоны анимаций (на слой target)."""
        from .effect_controls import MIME_EFFECT, MIME_PRESET
        if mime.hasFormat(MIME_EFFECT) or mime.hasFormat(MIME_PRESET):
            if target is None:
                self.statusBar().showMessage(tr("Отпусти эффект прямо на слой"), 4000)
                return False
            self.ed.select([target.id])
            if mime.hasFormat(MIME_EFFECT):
                self._add_effect(bytes(mime.data(MIME_EFFECT)).decode())
            else:
                self.apply_preset(bytes(mime.data(MIME_PRESET)).decode())
            return True
        if mime.hasFormat(MIME_ITEM):
            refs = bytes(mime.data(MIME_ITEM)).decode().split("\n")
            self.drop_items([r for r in refs if r], at, point)
            return True
        if mime.hasUrls():
            paths = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
            if any(p.lower().endswith(".skl") for p in paths):
                if self._confirm_discard():
                    self.open_path([p for p in paths if p.lower().endswith(".skl")][0])
                return True
            errors = self.ed.import_files(paths)
            if errors:
                QMessageBox.warning(self, tr("Импорт"), tr("Не удалось импортировать:\n") + "\n".join(errors))
            self.drop_items([os.path.abspath(p) for p in paths if os.path.abspath(p) in self.ed.project.media], at, point)
            return True
        return False

    def project_action(self, act, ref):
        ed = self.ed
        cid = ref[5:] if ref.startswith("comp:") else None
        path = ref[6:] if ref.startswith("media:") else None
        if act == "new_comp":
            self.new_comp_dialog()
        elif act == "import":
            self.import_dialog()
        elif act == "open" and cid:
            ed.open_comp(cid)
        elif act == "add":
            self.drop_items([ref])
        elif act == "render_comp" and cid:          # рендер этой композиции, не открывая её
            self.export_dialog(ed.project.comp_by_id(cid))
        elif act == "comp_settings" and cid:
            ed.open_comp(cid)
            self.comp_settings()
        elif act == "rename" and cid:
            from PySide6.QtWidgets import QInputDialog
            c = ed.project.comp_by_id(cid)
            name, ok = QInputDialog.getText(self, tr("Переименовать"), tr("Имя:"), text=c.name)
            if ok and name.strip():
                ed.snapshot()
                c.name = name.strip()
                for o in ed.project.comps:
                    for l in o.layers:
                        if l.kind == "comp" and l.source == cid:
                            l.name = c.name
                ed.media_changed.emit()
                self._comp_switched()
                ed.notify()
        elif act == "dup_comp" and cid:
            import copy as _copy
            from ..model import Composition
            c = ed.project.comp_by_id(cid)
            d = _copy.deepcopy(c.to_dict())
            d.pop("id", None)
            nc = Composition.from_dict(d)
            nc.name = c.name + " 2"
            ed.snapshot()
            ed.project.add_comp(nc)
            ed.media_changed.emit()
            ed.notify()
        elif act == "delete_comp" and cid:
            if not ed.delete_comp(cid):
                QMessageBox.information(self, tr("Проект"), tr("Нельзя удалить: это единственная композиция "
                                                                 "или она используется как прекомп"))
        elif act == "comp_from" and path:
            ed.comp_from_footage(path)
        elif act == "replace" and path:
            l = ed.current()
            if l is None or not ed.replace_source(l, path):
                self.statusBar().showMessage(tr("Выдели слой с видео, картинкой или звуком"), 4000)
        elif act == "reveal" and path:
            import subprocess
            subprocess.Popen(["xdg-open", os.path.dirname(path)])
        elif act == "remove_media" and path:
            used = any(l.source == path for c in ed.project.comps for l in c.layers)
            if used:
                QMessageBox.information(self, tr("Проект"), tr("Файл используется в композиции — сначала удали слои"))
                return
            ed.project.media.remove(path)
            ed.dirty = True
            ed.media_changed.emit()

    def layer_menu(self, gpos, layer=None):
        """Правая кнопка в окне композиции и на таймлайне — меню как в AE."""
        ed = self.ed
        if layer is not None and layer.id not in ed.sel_layers:
            ed.select([layer.id])
        sel = ed.selected()
        m = QMenu(self)
        m.setAttribute(Qt.WA_DeleteOnClose)   # иначе каждое меню ПКМ остаётся ребёнком окна навсегда
        new = m.addMenu(tr("Создать"))
        new.addAction(tr("Текст"), ed.add_text)
        new.addAction(tr("Солид…"), self.new_solid)
        new.addAction(tr("Нуль-объект"), ed.add_null)
        new.addAction(tr("Шейп-слой"), self.new_shape_layer)
        new.addAction(tr("Корректирующий слой"), ed.add_adjustment)
        new.addAction(tr("Камера"), ed.add_camera)
        new.addAction(tr("Свет"), lambda: ed.add_light())
        if sel:
            l = sel[0]
            m.addSeparator()
            if l.kind == "solid" and not l.adjustment:
                m.addAction(tr("Настройки солида…"), self.solid_settings)
            if l.kind == "comp":
                m.addAction(tr("Открыть прекомпозицию"), lambda: ed.open_comp(l.source))
                m.addAction(tr("Рендер композиции…"), lambda: self.export_dialog(ed.project.comp_by_id(l.source)))
            m.addAction(tr("Переименовать"), lambda: self.timeline.rename_layer(l))
            m.addAction(tr("Сбросить трансформацию"), lambda: [ed.reset_transform(x) for x in sel])
            if any(x.effects for x in sel):
                m.addAction(tr("Удалить все эффекты"), ed.clear_effects)
            lab = m.addMenu(tr("Метка (цвет)"))
            self._label_menu(lab, sel)
            m.addSeparator()
            fxm = m.addMenu(tr("Эффект"))
            from ..effects import CATEGORIES, category_label, effect_for_layer
            for cat in CATEGORIES:
                items = [(t, d) for t, d in EFFECTS.items() if d.category == cat and effect_for_layer(t, l.kind)]
                if items:
                    cm = fxm.addMenu(category_label(cat))
                    for t, d in items:
                        cm.addAction(d.label, lambda _=False, t=t: self._add_effect(t))
            if l.effects:
                fxm.addSeparator()
                fxm.addAction(tr("Удалить все эффекты"), ed.clear_effects)
            if any("speed" in x.props for x in sel):
                tim = m.addMenu(tr("Время (скорость)"))
                tim.addAction(tr("Скорость клипа…") + "\tCtrl+R", self.speed_dialog)
                for v in (25, 50, 200, 400):
                    tim.addAction(f"{v}%", lambda _=False, v=v: ed.set_speed(sel, v))
                tim.addAction(tr("Спид-рамп (ключи скорости)"), self.show_speed)
                a1 = tim.addAction(tr("Обратное воспроизведение"))
                a1.setCheckable(True)
                a1.setChecked(all(x.reverse for x in sel))
                a1.triggered.connect(lambda: ed.toggle_layer_flag(sel, "reverse"))
                tim.addSeparator()
                tim.addAction(tr("Включить переназначение времени (Time Remap)") + "\tCtrl+Alt+T", self.time_remap)
                tim.addAction(tr("Стоп-кадр с удержанием…") + "\tShift+Alt+F", self.freeze_dialog)
                tim.addAction(tr("Стоп-кадр до конца слоя (Freeze Frame)"), lambda: self._freeze())
                tim.addAction(tr("Стоп-кадр на последнем кадре"), lambda: self._freeze(last=True))
                a2 = tim.addAction(tr("Смешивание кадров"))
                a2.setCheckable(True)
                a2.setChecked(all(x.frame_blend for x in sel))
                a2.triggered.connect(lambda: ed.toggle_layer_flag(sel, "frame_blend"))
            pm = m.addMenu(tr("Шаблоны анимации"))
            from ..presets import PRESETS
            for pid, pr in PRESETS.items():
                pm.addAction(pr.label, lambda _=False, pid=pid: self.apply_preset(pid))
            mm = m.addMenu(tr("Маска"))
            mm.addAction(tr("Новая маска"), self.new_mask)
            mm.addAction(tr("Инвертировать маски"), self.toggle_mask_invert)
            mm.addAction(tr("Удалить маски слоя"), self.remove_masks)
            tm = m.addMenu(tr("Трансформирование"))
            tm.addAction(tr("Сбросить"), lambda: [ed.reset_transform(x) for x in sel])
            tm.addAction(tr("Вписать в композицию"), ed.fit_to_comp)
            tm.addAction(tr("Отразить по горизонтали"), lambda: self._flip(sel, 0))
            tm.addAction(tr("Отразить по вертикали"), lambda: self._flip(sel, 1))
            km = m.addMenu(tr("Помощник ключей"))
            km.addAction("Easy Ease", lambda: ed.set_ease("ease"))
            km.addAction("Easy Ease In", lambda: ed.set_ease("in"))
            km.addAction("Easy Ease Out", lambda: ed.set_ease("out"))
            km.addAction(tr("Линейно"), lambda: ed.set_ease("linear"))
            km.addAction("Hold", lambda: ed.set_ease("hold"))
            km.addSeparator()
            km.addAction(tr("Расставить слои по очереди…"), self.sequence_dialog)
            km.addAction(tr("Сдвинуть слои по очереди (offset)…"), self.stagger_dialog)
            sw = m.addMenu(tr("Переключатели"))
            for attr, text in (("threed", tr("3D-слой")), ("motion_blur", tr("Размытие в движении")),
                               ("adjustment", tr("Корректирующий слой")), ("solo", tr("Соло")),
                               ("locked", tr("Заблокировать")), ("visible", tr("Видимость"))):
                a = sw.addAction(text)
                a.setCheckable(True)
                a.setChecked(all(getattr(x, attr) for x in sel))
                if attr == "threed":
                    a.triggered.connect(lambda: ed.set_3d(sel))
                else:
                    a.triggered.connect(lambda _=False, at=attr: ed.toggle_switch(at, sel))
            m.addSeparator()
            m.addAction(tr("Прекомпоновать…") + "\tCtrl+Shift+C", self.precompose)
            m.addAction(tr("Дублировать") + "\tCtrl+D", ed.duplicate)
            m.addAction(tr("Разрезать слой") + "\tCtrl+Shift+D", ed.split)
            if ed.project.media and l.kind in ("video", "image", "audio"):
                rm = m.addMenu(tr("Заменить исходник"))
                for path in ed.project.media:
                    rm.addAction(os.path.basename(path), lambda _=False, p=path: ed.replace_source(l, p))
            m.addAction(tr("Удалить") + "\tDel", ed.delete_selection)
        else:
            m.addSeparator()
            m.addAction(tr("Настройки композиции…") + "\tCtrl+K", self.comp_settings)
            m.addAction(tr("Цвет фона композиции…") + "\tCtrl+Shift+B", self.comp_bg_color)
            m.addAction(tr("Новая композиция…") + "\tCtrl+N", self.new_comp_dialog)
        m.exec(gpos)

    def _label_menu(self, menu, layers):
        from PySide6.QtGui import QPixmap
        from ..model import LABELS
        from ..i18n import LANG as _L
        for ru, en, col in LABELS:
            pm = QPixmap(12, 12)
            pm.fill(QColor(col))
            menu.addAction(QIcon(pm), en if _L == "en" else ru, lambda _=False, c=col: self.ed.set_label(layers, c))
        menu.addSeparator()
        menu.addAction(tr("По типу слоя"), lambda: self.ed.set_label(layers, None))

    def _flip(self, layers, axis):
        ed = self.ed
        ed.snapshot()
        for l in layers:
            f = l.local(ed.time)
            sc = l.props["scale"].value_at(f)
            sc[axis] = -sc[axis]
            l.props["scale"].set_value_at(f, sc)
        ed.notify()

    def _proxy_progress(self, src, frac):
        self.proxy_label.setText(tr("Прокси: {name} {p}%").format(name=os.path.basename(src)[:24], p=int(frac * 100)))

    def _proxy_done(self, src, ok):
        self.proxy_label.setText("" if ok else tr("Прокси не создан: {}").format(os.path.basename(src)[:24]))
        if ok:
            self.statusBar().showMessage(tr("Прокси готов — перемотка «{}» теперь быстрая").format(
                os.path.basename(src)), 4000)

    def _toggle_proxies(self):
        on = self.proxy_action.isChecked()
        self.ed.proxies.enabled = on
        self.ed.pool.use_proxies = on
        if self.ed.render_service is not None:
            self.ed.render_service.opts["proxies"] = on
        self.ed.pool._proxy.clear()
        if on:
            self.ed.ensure_proxies()
        else:
            self.ed.proxies.stop()
            self.proxy_label.setText("")
        self.ed.notify()

    def _clear_proxies(self):
        from ..proxy import clear_all
        self.ed.proxies.stop()
        self.ed.pool.close()
        mb = clear_all() // (1024 * 1024)
        self.proxy_label.setText("")
        self.statusBar().showMessage(tr("Прокси удалены, освобождено {} МБ").format(mb), 4000)
        self.ed.notify()

    def plugins_dialog(self):
        """Менеджер плагинов: что загружено, ошибки, включить/выключить (после перезапуска)."""
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtWidgets import (QDialog, QDialogButtonBox, QLabel, QListWidget, QListWidgetItem,
                                       QPushButton, QVBoxLayout)
        from .. import plugins as P
        P.load_all()
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Плагины"))
        dlg.resize(560, 420)
        v = QVBoxLayout(dlg)
        v.addWidget(QLabel(tr("Папка плагинов: {}").format(P.plugins_dir())))
        lst = QListWidget()
        status_txt = {"loaded": tr("загружен"), "disabled": tr("выключен"), "error": tr("ошибка")}
        for pid, info in sorted(P.REGISTRY.items()):
            what = len(info["effects"]) + len(info["ops"])
            text = f"{info['name']} {info['version']} — {status_txt.get(info['status'], info['status'])}"
            if info["status"] == "loaded":
                text += "  · " + tr("добавлено: {}").format(what)
            err = info.get("error") or info.get("runtime_error")
            if err:
                text += "\n    ⚠ " + err
            it = QListWidgetItem(text)
            it.setData(Qt.UserRole, pid)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Unchecked if info["status"] == "disabled" else Qt.Checked)
            lst.addItem(it)
        if not P.REGISTRY:
            lst.addItem(tr("Плагинов нет. Положи .py-файл плагина в папку выше (пример — ~/sfx/plugins_examples)."))
        v.addWidget(lst, 1)
        v.addWidget(QLabel(tr("Включение и выключение применяется после перезапуска программы.")))
        op = QPushButton(tr("Открыть папку плагинов"))

        def open_dir():
            os.makedirs(P.plugins_dir(), exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(P.plugins_dir()))
        op.clicked.connect(open_dir)
        v.addWidget(op)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        v.addWidget(bb)
        if dlg.exec() == QDialog.Accepted:
            for i in range(lst.count()):
                it = lst.item(i)
                pid = it.data(Qt.UserRole)
                if pid:
                    P.set_enabled(pid, it.checkState() == Qt.Checked)

    def _goto_time_dialog(self):
        from PySide6.QtWidgets import QInputDialog
        from ..model import timecode
        t, ok = QInputDialog.getText(self, tr("Перейти ко времени"), tr("Время (0:00:05:00 или номер кадра):"),
                                     text=timecode(self.ed.time, self.ed.comp.fps))
        if ok:
            self.timeline._goto_text(t)

    def toggle_maximize_panel(self):
        """Клавиша ` (тильда), как в AE: панель под мышью — на всё окно, ещё раз — обратно."""
        from PySide6.QtGui import QCursor
        if getattr(self, "_maxed", None):
            for w, vis in self._maxed:
                w.setVisible(vis)
            self._maxed = None
            return
        under = QApplication.widgetAt(QCursor.pos())
        tl = under is not None and (under is self.timeline_panel or self.timeline_panel.isAncestorOf(under))
        hide = [self.top] if tl else [self.timeline_panel, self.left_tabs, self.right]
        self._maxed = [(w, w.isVisible()) for w in hide]
        for w in hide:
            w.hide()

    _CLIP_MSG = {"value": "Значение скопировано — выдели слои и нажми Ctrl+V",
                 "effect": "Эффект скопирован — выдели слой(и) и нажми Ctrl+V",
                 "keys": "Ключи скопированы — поставь курсор и нажми Ctrl+V",
                 "layers": "Слои скопированы"}

    def copy_sel(self):
        what = self.ed.copy_selection()
        self.statusBar().showMessage(tr(self._CLIP_MSG[what]) if what else tr("Нечего копировать"), 3000)

    def cut_sel(self):
        what = self.ed.cut_selection()
        if what:
            self.statusBar().showMessage(tr("Вырезано — Ctrl+V, чтобы вставить"), 3000)

    def paste_sel(self):
        clip = self.ed.clipboard
        n = self.ed.paste()
        if not n and clip and clip[0] in ("effects", "keys"):
            self.statusBar().showMessage(tr("Выдели слой, куда вставить"), 3000)
        elif n:
            self.statusBar().showMessage(tr("Вставлено: {}").format(n), 2000)

    def _purge_cache(self):
        self.ed.cache.clear()
        self.ed.cache_changed.emit()
        self.statusBar().showMessage(tr("Кэш кадров очищен"), 3000)

    def _prerender_progress(self, done, total):
        if done < 0:
            self.statusBar().clearMessage()
        else:
            self.statusBar().showMessage(tr("Пререндер: {d} / {t} кадров (Num 0 или Пробел — остановить)")
                                         .format(d=done, t=total))

    def _toggle_grid(self):
        self.ed.preview_transparent = self.grid_action.isChecked()
        self.viewer.transparency = self.grid_action.isChecked()
        self.viewer.invalidate()

    def sequence_dialog(self):
        """Как Sequence Layers в AE: в порядке выделения, каждый следующий — после предыдущего."""
        from PySide6.QtWidgets import QInputDialog
        if len(self.ed.selected()) < 2:
            self.statusBar().showMessage(tr("Выдели 2+ слоя (по порядку, Ctrl+клик)"), 4000)
            return
        v, ok = QInputDialog.getInt(self, tr("Расставить по очереди"), tr("Перекрытие, кадров (0 — встык):"), 0, 0, 10000)
        if ok:
            self.ed.sequence_layers(overlap=v)

    def stagger_dialog(self):
        from PySide6.QtWidgets import QInputDialog
        if len(self.ed.selected()) < 2:
            self.statusBar().showMessage(tr("Выдели 2+ слоя (по порядку, Ctrl+клик)"), 4000)
            return
        v, ok = QInputDialog.getInt(self, tr("Сдвинуть по очереди"), tr("Каждый следующий позже на, кадров:"), 2, -10000, 10000)
        if ok:
            self.ed.sequence_layers(stagger=v)

    def speed_dialog(self):
        sel = [l for l in self.ed.selected() if "speed" in l.props]
        if not sel:
            self.statusBar().showMessage(tr("Выдели видео, звук или прекомп"), 4000)
            return
        dlg = SpeedDialog(sel[0], self.ed.comp.fps, self)
        if dlg.exec() == QDialog.Accepted:
            self.ed.set_speed(sel, dlg.spin.value(), dlg.fit.isChecked())
            for l in sel:
                l.reverse = dlg.rev.isChecked()
                l.frame_blend = dlg.blend.isChecked() and l.kind == "video"
            self.ed.notify()

    def _show_remap(self):
        for l in self.ed.selected():
            if "time_remap" in l.props:
                cur = self.timeline.shown.setdefault(l.id, [])
                if "time_remap" not in cur:
                    cur.append("time_remap")
        self.timeline.update()

    def time_remap(self):
        if not self.ed.toggle_time_remap():
            self.statusBar().showMessage(tr("Выдели видео или прекомп"), 4000)
            return
        self._show_remap()

    def _clear_markers(self):
        if self.ed.comp.markers or any(l.markers for l in self.ed.comp.layers):
            self.ed.snapshot()
            self.ed.comp.markers = []
            for l in self.ed.comp.layers:
                l.markers = []
            self.ed.notify()

    def _freeze(self, last=False, hold=None, ripple=False):
        if not self.ed.freeze_frame(last=last, hold=hold, ripple=ripple):
            self.statusBar().showMessage(tr("Выдели видео или прекомп под курсором времени"), 4000)
            return
        self._show_remap()

    def freeze_dialog(self):
        """Стоп-кадр на кадре под курсором: сколько держать, потом клип играет дальше с того же кадра."""
        from PySide6.QtWidgets import QCheckBox, QDialogButtonBox, QDoubleSpinBox, QFormLayout
        fps = self.ed.comp.fps
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Стоп-кадр"))
        f = QFormLayout(dlg)
        sec = QDoubleSpinBox()
        sec.setRange(1.0 / fps, 3600.0)
        sec.setDecimals(2)
        sec.setValue(1.0)
        sec.setSuffix(tr(" с"))
        f.addRow(tr("Удерживать кадр под курсором:"), sec)
        rip = QCheckBox(tr("Сдвинуть следующие клипы на длину паузы"))
        rip.setChecked(self._ws_settings().value("freeze_ripple", "true") == "true")
        rip.setToolTip(tr("Клипы, которые начинаются после этого, уедут вправо — продолжение после паузы не закроется"))
        f.addRow(rip)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(dlg.accept)
        bb.rejected.connect(dlg.reject)
        f.addRow(bb)
        if dlg.exec() == QDialog.Accepted:
            self._ws_settings().setValue("freeze_ripple", "true" if rip.isChecked() else "false")
            self._freeze(hold=max(1, round(sec.value() * fps)), ripple=rip.isChecked())

    def show_speed(self):
        """Показать «Время → Скорость» на таймлайне: секундомер + ключи = спид-рамп."""
        for l in self.ed.selected():
            if "speed" in l.props:
                cur = self.timeline.shown.setdefault(l.id, [])
                if "speed" not in cur:
                    cur.append("speed")
        self.timeline.update()
        self.statusBar().showMessage(tr("Включи секундомер у «Скорость» и ставь ключи: 100% → 300% → 30% — это спид-рамп"), 8000)

    def fx_console(self):
        from .fx_console import FxConsole
        if not hasattr(self, "_fxc"):
            self._fxc = FxConsole(self)
        self._fxc.popup()

    def apply_preset(self, pid):
        from ..presets import apply_preset
        sel = self.ed.selected()
        if not sel:
            self.statusBar().showMessage(tr("Сначала выдели слой"), 3000)
            return
        self.ed.snapshot()
        for l in sel:
            apply_preset(pid, l, self.ed)
        self.ed.notify()
        self.timeline.show_props("animated")

    def comp_settings(self):
        dlg = CompSettingsDialog(self.ed.comp, self)
        if dlg.exec() == QDialog.Accepted:
            self.ed.snapshot()
            dlg.apply(self.ed.comp)
            self.ed.set_time(min(self.ed.time, self.ed.comp.duration - 1))
            self.ed.notify()
            self.timeline.fit()

    # --- импорт ----------------------------------------------------------------
    def import_dialog(self):
        exts = " ".join(f"*{e}" for e in sorted(VIDEO_EXT | IMAGE_EXT | AUDIO_EXT))
        paths, _ = QFileDialog.getOpenFileNames(self, tr("Импорт"), os.path.expanduser("~"),
                                                tr("Медиа ({})").format(exts) + ";;" + tr("Все файлы (*)"))
        if paths:
            self._import(paths, add=False)

    def _import(self, paths, add=True):
        errors = self.ed.import_files(paths)
        if errors:
            QMessageBox.warning(self, tr("Импорт"), tr("Не удалось импортировать:\n") + "\n".join(errors))
        if add:
            # как в CapCut: несколько клипов встают друг за другом
            at = self.ed.new_layer_time()
            for p in paths:
                p = os.path.abspath(p)
                if p in self.ed.project.media:
                    layer = self.add_media(p, at)
                    if layer and layer.kind in ("video", "audio"):
                        at = layer.out_point
        else:
            self.statusBar().showMessage(tr("Импортировано: {}. Двойной клик — в композицию.").format(len(paths) - len(errors)))

    def add_media(self, path, at=None):
        try:
            return self.ed.add_media_layer(path, at)
        except Exception as e:
            QMessageBox.warning(self, tr("Ошибка"), str(e))
            return None

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasFormat(MIME_ITEM):
            e.acceptProposedAction()

    def dropEvent(self, e):
        self.handle_drop(e.mimeData())

    # --- файлы проекта ---------------------------------------------------------
    def _confirm_discard(self) -> bool:
        if not self.ed.dirty:
            return True
        r = QMessageBox.question(self, tr("SFX"), tr("Сохранить изменения в проекте?"),
                                 QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
        if r == QMessageBox.Save:
            return self.save()
        return r == QMessageBox.Discard

    @staticmethod
    def _looks_like_project(path) -> bool:
        """Проект — это JSON-текст; бинарные медиафайлы сюда не проходят."""
        try:
            with open(path, "rb") as f:
                head = f.read(64).lstrip()
            return head.startswith(b"{")
        except OSError:
            return False

    def new_project(self):
        if self._confirm_discard():
            self.ed.new_project()
            self.timeline.shown.clear()
            self.timeline.fit()

    def open_dialog(self):
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, tr("Открыть проект"), os.path.expanduser("~"),
                                              tr("Проект SFX (*.skl)") + ";;" + tr("Все файлы (*)"))
        if path:
            self.open_path(path)

    def open_path(self, path):
        # Через «Открыть» выбрали видео/картинку/звук — не ругаемся, а импортируем как медиа
        if not path.lower().endswith(".skl") and not self._looks_like_project(path):
            self._import([path], add=True)
            return
        try:
            self.ed.load(path)
        except (UnicodeDecodeError, ValueError, KeyError, TypeError):
            QMessageBox.critical(self, tr("Ошибка"), tr("Файл не является проектом SFX (.skl):\n{}").format(path))
            return
        except Exception as e:
            QMessageBox.critical(self, tr("Ошибка"), tr("Не удалось открыть проект:\n{}").format(e))
            return
        missing = [m for m in self.ed.project.media if not os.path.exists(m)]
        if missing:
            QMessageBox.warning(self, tr("Нет файлов"), tr("Не найдены файлы:\n") + "\n".join(missing))
        self.timeline.shown.clear()
        self.timeline.fit()
        if os.path.dirname(os.path.abspath(path)) != os.path.abspath(self.ed.autosave_dir()):
            from .home import add_recent
            add_recent(path)

    def show_home(self, startup=False):
        """Окно «Проекты» (как Home в AE). startup — при запуске: только если включено «Показывать при запуске»."""
        from .home import HomeDialog, _settings
        if startup is True and not _settings().value("home_on_start", True, type=bool):
            return
        dlg = HomeDialog(self, self.ed.autosave_dir())
        if dlg.exec() != QDialog.Accepted or not dlg.choice:
            return
        what, path = dlg.choice
        if what == "new":
            self.new_project()
        elif what == "open_dialog":
            self.open_dialog()
        elif what == "open" and self._confirm_discard():
            self.open_path(path)

    def save(self) -> bool:
        if not self.ed.path:
            return self.save_as()
        try:
            self.ed.save(self.ed.path)
        except Exception as e:
            QMessageBox.critical(self, tr("Ошибка"), str(e))
            return False
        self.statusBar().showMessage(tr("Сохранено: {}").format(self.ed.path), 4000)
        from .home import add_recent
        add_recent(self.ed.path)
        return True

    def save_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(self, tr("Сохранить проект"), os.path.expanduser("~/" + tr("проект.skl")),
                                              tr("Проект SFX (*.skl)"))
        if not path:
            return False
        if not path.endswith(".skl"):
            path += ".skl"
        self.ed.path = path
        return self.save()

    # --- Roto Brush и 3D-трекер (отдельные окна-инструменты) --------------------
    def open_roto(self):
        from .roto_panel import RotoPanel
        if getattr(self, "_roto", None) is None:
            self._roto = RotoPanel(self.ed, lambda m: self.statusBar().showMessage(m, 4000), self)
            self._roto.setWindowFlag(Qt.Window, True)
        self._roto.show()
        self._roto.raise_()
        self._roto.activateWindow()

    def open_tracker(self):
        from .tracker_panel import TrackerPanel
        if getattr(self, "_tracker", None) is None:
            self._tracker = TrackerPanel(self.ed, lambda m: self.statusBar().showMessage(m, 4000), self)
            self._tracker.setWindowFlag(Qt.Window, True)
        self._tracker.show()
        self._tracker.raise_()
        self._tracker.activateWindow()

    # --- экспорт ---------------------------------------------------------------
    def export_dialog(self, comp=None):
        """Рендер в файл. comp — какую композицию предложить (по умолчанию открытую); в диалоге можно выбрать любую."""
        if self._export:
            return
        self.ed.stop()
        dlg = RenderDialog(comp or self.ed.comp, self, self.ed.path, comps=self.ed.project.comps)
        if dlg.exec() != QDialog.Accepted:
            return
        comp = dlg.comp
        st, path = dlg.result_settings, dlg.result_path
        total = len(st.frame_map(comp))
        prog = QProgressDialog(tr("Рендер…"), tr("Отмена"), 0, total, self)
        prog.setWindowTitle(tr("Рендер"))
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.setMinimumWidth(420)
        pd = self.ed.project.to_dict()
        pd["active"] = comp.id                 # рендерится выбранная композиция (прекомпы берутся из проекта)
        th = ExportThread(pd, path, self, settings=st)
        self._export = th
        t0 = time.monotonic()

        def on_progress(d, t):
            el = time.monotonic() - t0
            left = el / d * (t - d) if d else 0
            prog.setValue(d)
            prog.setLabelText(tr("Рендер… кадр {} из {}").format(d, t) + "\n"
                              + tr("Прошло {}, осталось ~{}").format(_dur(el), _dur(left)))
        th.progress.connect(on_progress)
        prog.canceled.connect(th.cancel)
        self.render_btn.setEnabled(False)

        def done_ok(p):
            prog.close()
            self._export = None
            self.render_btn.setEnabled(True)
            shown = p.replace("_%05d", "_*") if "%05d" in p else p
            QMessageBox.information(self, tr("Рендер"), tr("Готово за {}:\n{}").format(_dur(time.monotonic() - t0), shown))

        def done_fail(msg):
            prog.close()
            self._export = None
            self.render_btn.setEnabled(True)
            if msg != tr("Экспорт отменён"):
                QMessageBox.critical(self, tr("Рендер"), tr("Ошибка экспорта:\n{}").format(msg))
        th.finished_ok.connect(done_ok)
        th.failed.connect(done_fail)
        th.start()

    # --- справка ---------------------------------------------------------------
    def show_help(self):
        rows = []
        cur = None
        for menu, text, key in self.actions_list:
            if not key:
                continue
            if menu != cur:
                rows.append(f"<tr><td colspan=2 style='padding-top:10px;color:#3d9bf2'><b>{menu}</b></td></tr>")
                cur = menu
            rows.append(f"<tr><td style='padding-right:24px'><code>{key}</code></td><td>{text}</td></tr>")
        extra = f"""
        <p style='color:#3d9bf2'><b>{tr('Мышь')}</b></p>
        <ul>
        <li>Синие числа у свойств — тяни влево/вправо (Shift ×10, Ctrl ×0.1), клик — ввести число</li>
        <li>Секундомер — включить анимацию свойства; ◆ слева — ключ на текущем кадре</li>
        <li>Полоска слоя: тянуть — сдвиг, края — обрезка; липнет к курсору</li>
        <li><b>Shift</b> при перетаскивании (слоя, краёв, ключей, курсора) — прилипание к ключам, краям слоёв,
            курсору и рабочей области, как в AE; жёлтая линия показывает, куда прилипло</li>
        <li>Shift+клик — добавить к выделению, Ctrl+клик — переключить</li>
        <li>Граф-эдитор (Shift+F3 или Alt+F3): «Все видимые» — все кривые раскрытых свойств; Shift — по одной оси</li>
        <li>График скорости: жёлтые ручки — влево/вправо влияние, вверх/вниз скорость (как в AE)</li>
        <li>У свойства: ◀ ◆ ▶ — к предыдущему ключу / ключ / к следующему; значок кривой — показать в граф-эдиторе</li>
        <li><b>Выражения</b>: Alt+клик по секундомеру (или Alt+Shift+=). Клик по тексту выражения — редактор
            (Ctrl+Enter — применить). Спираль — тяни на название другого свойства (pickwhip).
            Есть time, value, wiggle(), loopOut(), linear(), ease(), thisComp.layer("Имя").effect("Слайдер")("Слайдер"),
            для сложения массивов — add(a, b)</li>
        <li>Группы в таймлайне (Маски, Содержимое, Эффекты, Трансформирование, Звук) сворачиваются по ▼, «Сброс» — значения по умолчанию</li>
        <li>Окно композиции: колесо — зум, средняя кнопка — панорама; инструменты на верхней панели</li>
        <li>Эквалайзер в «Эффекты слоя»: тяни цветные точки, колесо — ширина полосы</li>
        <li><b>Ручки масштаба</b> у выделенного слоя: боковые — только X, верх/низ — только Y, углы — свободно,
            с Ctrl (или Shift) — пропорционально, с Alt — точнее (медленнее)</li>
        <li>Из «Проекта» файлы и композиции можно перетаскивать в окно композиции (встанут в эту точку)
            и на таймлайн (на это время); Alt + отпустить на слой — заменить его исходник</li>
        <li>Правая кнопка в окне композиции и на таймлайне — меню слоя; клик по цветному квадратику — метка</li>
        <li>Двойной клик по слою-прекомпу — открыть его; вкладки композиций — над таймлайном</li>
        <li><b>Скорость клипа</b>: Ctrl+R или правая кнопка → «Время»; спид-рамп — ключи у «Время → Скорость»</li>
        <li>Удалить эффект: кликни по нему в «Эффекты слоя» и нажми Delete, или 🗑 / правая кнопка</li>
        <li>Эффекты и шаблоны можно перетаскивать на слой (таймлайн, окно композиции); Ctrl+Пробел — быстрый поиск</li>
        <li>Колонки «Режим» и «Трек-матт» на таймлайне; хоткеи работают на любой раскладке</li>
        <li>Клик по названию свойства — выделить его ключи и показать в граф-эдиторе</li>
        <li>Граф-эдитор: тяни ключи и жёлтые ручки кривых</li>
        <li>Alt/Ctrl + колесо — зум таймлайна, Shift + колесо — прокрутка по времени, средняя кнопка — панорама</li>
        <li>В окне композиции слой можно таскать мышью (Shift — по одной оси)</li>
        <li>Колонки слоя: ● соло, замок — блокировка, fx — эффекты вкл/выкл, ◍ — размытие в движении,
            ◐ — корректирующий слой, «Родитель» — привязать слой к другому</li>
        </ul>"""
        if LANG == "en":
            extra = f"<p style='color:#3d9bf2'><b>Mouse</b></p><ul>{EN['help_mouse']}</ul>"
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("Горячие клавиши"))
        dlg.resize(620, 720)
        v = QVBoxLayout(dlg)
        tb = QTextBrowser()
        tb.setHtml("<table>" + "".join(rows) + "</table>" + extra)
        v.addWidget(tb)
        b = QPushButton(tr("Закрыть"))
        b.clicked.connect(dlg.accept)
        v.addWidget(b)
        dlg.exec()

    def start_autosave(self, interval_ms: int = 120000):
        """Автосохранение раз в interval_ms и восстановление после сбоя (вызывает main.py).
        Метка running лежит в папке автосохранения, пока программа открыта; при обычном выходе удаляется.
        Если при запуске метка уже есть — прошлый запуск упал: предлагаем открыть последнее автосохранение."""
        import glob
        d = self.ed.autosave_dir()
        os.makedirs(d, exist_ok=True)
        self._running_mark = os.path.join(d, "running")
        saves = sorted(glob.glob(os.path.join(d, "*_autosave.skl")), key=os.path.getmtime)
        if os.path.exists(self._running_mark) and saves:
            r = QMessageBox.question(self, tr("SFX"), tr("Прошлый сеанс завершился аварийно. Открыть автосохранение?\n{}")
                                     .format(saves[-1]), QMessageBox.Yes | QMessageBox.No)
            if r == QMessageBox.Yes:
                self.open_path(saves[-1])
                self.ed.path = None          # «Сохранить» спросит, куда — не перезаписываем копию
                self.ed.dirty = True
        open(self._running_mark, "w").close()
        self._autosave_timer = QTimer(self)
        self._autosave_timer.timeout.connect(self._autosave_tick)
        self._autosave_timer.start(interval_ms)

    def _autosave_tick(self):
        try:
            p = self.ed.autosave()
        except OSError as err:
            self.statusBar().showMessage(tr("Автосохранение не удалось: {}").format(err), 6000)
            return
        if p:
            self.statusBar().showMessage(tr("Автосохранение: {}").format(p), 3000)

    def closeEvent(self, e):
        if not self._confirm_discard():
            e.ignore()
            return
        self.save_last_layout()
        mark = getattr(self, "_running_mark", None)
        if mark and os.path.exists(mark):
            os.remove(mark)
        if self._export:
            self._export.cancel()
            self._export.wait(3000)
        self.ed.stop()
        self.ed.shutdown()
        self.ed.pool.close()
        e.accept()
