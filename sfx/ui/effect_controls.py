# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Панель «Элементы управления эффектами» (Effect Controls) как в AE: эффекты выделенного слоя,
сворачиваемые группы параметров, «Сброс», секундомеры, протягиваемые значения, списки и флажки."""
from __future__ import annotations

from ..i18n import tr

import shiboken6
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QFrame, QHBoxLayout, QInputDialog, QLabel,
                               QMenu, QPushButton, QScrollArea, QSizePolicy, QSpinBox, QToolButton, QVBoxLayout,
                               QWidget)

from ..effects import EFFECTS, effect_display_name, prop_key
from .panels import ColorButton
from .widgets import EQCurve, Hint, ScrubValue, Stopwatch

MIME_EFFECT = "application/x-sfx-effect"
MIME_PRESET = "application/x-sfx-preset"


class _Swatch(QPushButton):
    """Анимируемый цвет-параметр: [r, g, b] 0..255."""

    def __init__(self, on_pick):
        super().__init__()
        self.setFixedSize(46, 18)
        self.setFocusPolicy(Qt.NoFocus)
        self._rgb = [255, 255, 255]
        self.clicked.connect(lambda: on_pick(self))

    def set_rgb(self, v):
        self._rgb = [int(max(0, min(255, x))) for x in v[:3]]
        self.setStyleSheet(f"background: rgb({self._rgb[0]},{self._rgb[1]},{self._rgb[2]}); border:1px solid #111;")


class EffectControlsPanel(QWidget):
    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.ed = editor
        self._layer = None
        self._sig = None
        self._editors: list[tuple] = []     # (виджет, ключ, вид)
        self._stopwatches: list[tuple] = []
        self.collapsed: set = set()
        self.setAcceptDrops(True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.title = QLabel()
        self.title.setStyleSheet("color:#e6e6e6; font-weight:bold; padding:6px 8px; background:#1e1e1e;")
        outer.addWidget(self.title)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll, 1)
        self.inner = None
        self._retired = None            # прошлая панель — удаляется на следующем refresh (см. refresh)
        self._keep_scroll = None        # прокрутка, которую вернуть после пересборки панели того же слоя
        self.scroll.verticalScrollBar().rangeChanged.connect(self._restore_scroll)
        editor.selection_changed.connect(self.refresh)
        editor.changed.connect(self._maybe_refresh)
        editor.time_changed.connect(lambda _: self._update_values())
        self.refresh()

    # ------------------------------------------------------------------ структура
    def _signature(self, l):
        if l is None:
            return None
        return (l.id, l.name, l.fx_on, tuple(sorted(self.collapsed, key=str)), self.ed.sel_effect,
                tuple((fx["id"], fx.get("on", True), fx.get("name"), tuple(sorted((k, v) for k, v in fx.get("opts", {}).items() if not isinstance(v, list))),
                       tuple(bool(l.props[k].expr) for k in l.effect_keys(fx) if k in l.props))
                      for fx in l.effects))

    def _maybe_refresh(self):
        cur = self.ed.current()
        if cur is not self._layer or self._signature(cur) != self._sig:
            self.refresh()
        else:
            self._update_values()

    def refresh(self):
        prev = self._layer
        l = self._layer = self.ed.current()
        # клик по эффекту выделяет его → панель пересобирается; без этого прокрутка улетала наверх
        same = prev is not None and l is not None and prev.id == l.id and self.inner is not None
        self._keep_scroll = self.scroll.verticalScrollBar().value() if same else None
        self._sig = self._signature(l)
        self._editors, self._stopwatches = [], []
        self.inner = QWidget()
        lay = QVBoxLayout(self.inner)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        if l is None:
            self.title.setText(tr("Элементы управления эффектами (нет)"))
            lay.addWidget(Hint(tr("Выдели слой. Эффекты — меню «Эффект» или панель «Эффекты и шаблоны» "
                                  "(двойной клик или перетащить сюда).")))
        else:
            self.title.setText(tr("Элементы управления эффектами: {}").format(l.name))
            if not l.effects:
                lay.addWidget(Hint(tr("У слоя нет эффектов. Перетащи эффект сюда из «Эффекты и шаблоны».")))
            for fx in l.effects:
                if fx["type"] in EFFECTS:
                    box = self._effect_box(l, fx)
                    for w in [box] + box.findChildren(QWidget):
                        w.installEventFilter(self)
                    lay.addWidget(box)
        lay.addStretch(1)
        # setWidget удаляет старую панель сразу; если refresh пришёл из потери фокуса поля ввода внутри неё,
        # Qt продолжает работать с уже удалённым полем — падение (SIGSEGV). Поэтому старая панель живёт
        # до следующего refresh и удаляется тогда. deleteLater не годится: без цикла событий (тесты, вложенные
        # циклы) панели копились — ~5 МБ на пересборку.
        old = self.scroll.takeWidget()
        if self._retired is not None:
            shiboken6.delete(self._retired)
        self._retired = old
        if old is not None:
            old.hide()
        self.scroll.setWidget(self.inner)
        self._restore_scroll()
        self._update_values()

    def _restore_scroll(self, *_):
        """Новая панель получает диапазон прокрутки после раскладки — тогда и возвращаем прежнее место."""
        want = self._keep_scroll
        if want is None:
            return
        sb = self.scroll.verticalScrollBar()
        sb.setValue(min(want, sb.maximum()))
        if sb.value() == want:
            self._keep_scroll = None

    def _effect_box(self, l, fx):
        ed = self.ed
        d = EFFECTS[fx["type"]]
        fid = fx["id"]
        box = QFrame()
        box.setObjectName("FxBox")
        selected = self.ed.sel_effect == (l.id, fid)
        box.setStyleSheet("#FxBox { background:#1b1b1b; border:1px solid %s; }" % ("#2d8ceb" if selected else "#2c2c2c"))
        box.mousePressEvent = lambda e, l=l, fid=fid: self._select_fx(l, fid, e)
        box.setProperty("fx_sel", (l.id, fid))
        box.setContextMenuPolicy(Qt.CustomContextMenu)
        box.customContextMenuRequested.connect(lambda pos, b=box, l=l, fid=fid: self._fx_menu(l, fid, b.mapToGlobal(pos)))
        v = QVBoxLayout(box)
        v.setContentsMargins(4, 2, 4, 4)
        v.setSpacing(1)
        head = QHBoxLayout()
        head.setSpacing(4)
        on = QCheckBox("fx")
        on.setFocusPolicy(Qt.NoFocus)
        on.setChecked(fx.get("on", True))
        on.setToolTip(tr("Эффект вкл/выкл"))
        on.toggled.connect(lambda val: self._fx_on(fx, val))
        head.addWidget(on)
        tw = QToolButton()
        tw.setAutoRaise(True)
        tw.setFocusPolicy(Qt.NoFocus)
        opened = fid not in self.collapsed
        tw.setArrowType(Qt.DownArrow if opened else Qt.RightArrow)
        tw.clicked.connect(lambda: self._toggle(fid))
        head.addWidget(tw)
        name = QLabel(effect_display_name(fx))
        name.setStyleSheet("font-weight:bold; color:#e6e6e6;" if fx.get("on", True) else "font-weight:bold; color:#777;")
        name.mouseDoubleClickEvent = lambda _e: self._rename(l, fx)
        name.setToolTip(tr("Двойной клик — переименовать (имя используется в выражениях)"))
        name.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)  # длинное имя не раздвигает панель
        head.addWidget(name, 1)
        reset = QPushButton(tr("Сброс"))
        reset.setFlat(True)
        reset.setFocusPolicy(Qt.NoFocus)
        reset.setStyleSheet("QPushButton { color:#3d9bf2; border:none; background:transparent; padding:0 4px; }"
                            " QPushButton:hover { text-decoration: underline; }")
        reset.clicked.connect(lambda: ed.reset_effect(l, fid))
        head.addWidget(reset)
        for txt, tip, fn in (("▲", tr("Выше"), lambda _=False: ed.move_effect(l, fid, -1)),
                             ("▼", tr("Ниже"), lambda _=False: ed.move_effect(l, fid, 1)),
                             ("🗑", tr("Удалить эффект (или выдели эффект и нажми Delete)"),
                              lambda _=False: ed.remove_effect(l, fid))):
            b = QToolButton()
            b.setText(txt)
            b.setToolTip(tip)
            b.setFocusPolicy(Qt.NoFocus)
            b.setAutoRaise(True)
            b.setFixedSize(20, 18)
            b.setStyleSheet("padding:0;")
            b.clicked.connect(fn)
            head.addWidget(b)
        v.addLayout(head)
        if not opened:
            return box
        if fx["type"] == "eq":
            eq = EQCurve()
            eq.begin.connect(lambda: ed.snapshot(f"{l.id}.{fid}.eq"))
            eq.changed.connect(lambda k, val: self._set_param(prop_key(fid, k), [val], snapshot=False))
            self._editors.append((eq, fid, "eq"))
            v.addWidget(eq)
            v.addWidget(Hint(tr("Тяни точки: влево-вправо — частота, вверх-вниз — усиление; колесо — ширина (Q). "
                                "Shift — усиление по 1 дБ.")))
        if fx["type"] == "curves":
            from .widgets import CurvesWidget
            cw = CurvesWidget()
            cw.begin.connect(lambda: ed.snapshot(f"{l.id}.{fid}.curves"))
            cw.changed.connect(lambda pts, cw=cw: self._set_curve(l, fid, cw.channel, pts))
            self._editors.append((cw, fid, "curves"))
            v.addWidget(cw)
            v.addWidget(Hint(tr("Клик — точка, тяни — меняй, правая кнопка — удалить точку. Канал — ниже.")))
        if d.audio:
            v.addWidget(Hint(tr("Звуковой эффект: слышно в превью (Пробел) и в рендере. "
                                "Значения берутся на начале слоя.")))
        groups: dict = {}
        for prm in d.params:
            groups.setdefault(prm.group_label, []).append(prm)
        for gname, prms in groups.items():
            indent = 0
            if gname is not None:
                gid = (fid, gname)
                g_open = gid not in self.collapsed
                gh = QHBoxLayout()
                gh.setContentsMargins(22, 0, 0, 0)
                gb = QToolButton()
                gb.setAutoRaise(True)
                gb.setFocusPolicy(Qt.NoFocus)
                gb.setArrowType(Qt.DownArrow if g_open else Qt.RightArrow)
                gb.clicked.connect(lambda _=False, g=gid: self._toggle(g))
                gh.addWidget(gb)
                gl = QLabel(gname)
                gl.setStyleSheet("color:#c8c8c8;")
                gh.addWidget(gl, 1)
                v.addLayout(gh)
                if not g_open:
                    continue
                indent = 18
            for prm in prms:
                v.addWidget(self._param_row(l, fx, prm, indent))
        opts = fx.get("opts", {})
        if "color" in opts:
            row = QHBoxLayout()
            row.setContentsMargins(42, 0, 0, 0)
            row.addWidget(QLabel(tr("Цвет")), 1)
            cb = ColorButton(opts["color"])
            cb.color_changed.connect(lambda col: ed.set_effect_opt(l, fid, "color", col))
            row.addWidget(cb)
            v.addLayout(row)
        if "seed" in opts and "seed" in EFFECTS[fx["type"]].opts:   # старые проекты могут хранить ненужный seed (Glint)
            row = QHBoxLayout()
            row.setContentsMargins(42, 0, 0, 0)
            row.addWidget(QLabel(tr("Случайность")), 1)
            seed = QSpinBox()
            seed.setRange(0, 9999)
            seed.setValue(int(opts["seed"]))
            seed.setKeyboardTracking(False)
            seed.valueChanged.connect(lambda val: ed.set_effect_opt(l, fid, "seed", val))
            row.addWidget(seed)
            v.addLayout(row)
        return box

    def _param_row(self, l, fx, prm, indent):
        key = prop_key(fx["id"], prm.key)
        prop = l.props.get(key)
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(4 + indent, 0, 0, 0)
        h.setSpacing(4)
        sw = Stopwatch()
        sw.clicked.connect(lambda: self._stopwatch(l, key))
        sw.alt_clicked.connect(lambda: self._toggle_expr(l, key))
        self._stopwatches.append((sw, key))
        h.addWidget(sw)
        lab = QLabel(prm.label + ("  =" if prop is not None and prop.expr else ""))
        lw = max(70, 122 - indent)                    # подпись фиксированной ширины, длинная — с «…»
        lab.setFixedWidth(lw)
        lab.setText(lab.fontMetrics().elidedText(lab.text(), Qt.ElideRight, lw - 4))
        lab.setToolTip(prm.label)
        if prop is not None and prop.expr:
            lab.setStyleSheet("color:#e5677d;")
            lab.setToolTip(prop.expr)
        h.addWidget(lab)
        kind = prm.kind
        if kind == "checkbox":
            ed_w = QCheckBox()
            ed_w.setFocusPolicy(Qt.NoFocus)
            ed_w.toggled.connect(lambda val: self._set_param(key, [1.0 if val else 0.0], hold=True))
        elif kind == "choice":
            ed_w = QComboBox()
            ed_w.setFocusPolicy(Qt.NoFocus)
            ed_w.addItems(prm.choice_labels())
            ed_w.currentIndexChanged.connect(lambda i: self._set_param(key, [float(i)], hold=True))
        elif kind == "color":
            ed_w = _Swatch(lambda b, k=key: self._pick_color(b, k))
            from .icons import icon
            drop = QToolButton()
            drop.setIcon(icon("eyedropper", size=16))
            drop.setAutoRaise(True)
            drop.setFocusPolicy(Qt.NoFocus)
            drop.setToolTip(tr("Пипетка: щёлкни по картинке во вьювере, чтобы взять цвет"))
            drop.clicked.connect(lambda _=False, k=key, lid=l.id: self.ed.start_color_pick(lid, k))
            h.addWidget(ed_w)
            h.addWidget(drop)
            self._editors.append((ed_w, key, kind))
            h.addStretch(1)
            return w
        else:
            ed_w = ScrubValue(dims=len(prm.default), step=prm.step, suffix=prm.suffix, angle=(kind == "angle"),
                              lo=prm.lo, hi=prm.hi)
            ed_w.begin.connect(lambda _=False, k=key: self.ed.snapshot(f"{l.id}.{k}"))
            ed_w.changed.connect(lambda vals, k=key: self._set_param(k, vals, snapshot=False))
        h.addWidget(ed_w)
        h.addStretch(1)
        self._editors.append((ed_w, key, kind))
        return w

    # ------------------------------------------------------------------ значения
    def _update_values(self):
        l = self._layer
        if l is None:
            return
        f = l.local(self.ed.time)
        for w, key, kind in self._editors:
            if kind == "curves":
                fx = l.effect_by_id(key)
                if fx:
                    ch = int(round(l.props[prop_key(key, "channel")].value_at(f)[0]))
                    name = ["rgb", "r", "g", "b"][max(0, min(3, ch))]
                    w.set_curve(name, fx.get("opts", {}).get(name, [[0, 0], [1, 1]]))
                continue
            if kind == "eq":
                fx = l.effect_by_id(key)
                if fx:
                    vals = {}
                    for k in l.effect_keys(fx):
                        p = l.props.get(k)
                        if p:
                            vals[k.rsplit("/", 1)[1]] = p.value_at(f)[0]
                    w.set_values(vals)
                continue
            prop = l.props.get(key)
            if prop is None:
                continue
            v = prop.value_at(f)
            w.blockSignals(True)
            if kind == "checkbox":
                w.setChecked(bool(round(v[0])))
            elif kind == "choice":
                w.setCurrentIndex(int(round(v[0])))
            elif kind == "color":
                w.set_rgb(v)
            else:
                w.expr = prop.has_expr
                w.set_values(v)
            w.blockSignals(False)
        for sw, key in self._stopwatches:
            prop = l.props.get(key)
            sw.on = bool(prop and prop.animated)
            sw.update()

    def _set_curve(self, l, fid, channel, pts):
        fx = l.effect_by_id(fid)
        if fx:
            fx.setdefault("opts", {})[channel] = pts
            self.ed.notify()

    def _set_param(self, key, vals, snapshot=True, hold=False):
        l = self._layer
        if l is None or key not in l.props:
            return
        if snapshot:
            self.ed.snapshot(f"{l.id}.{key}")
        lf = l.local(self.ed.time)
        p = l.props[key]
        p.set_value_at(lf, vals)
        if hold:
            k = p.keyframe_at(lf)
            if k:
                k.hold = True
        self.ed.notify()

    def _pick_color(self, btn, key):
        col = QColorDialog.getColor(QColor(*btn._rgb), self, tr("Цвет"))
        if col.isValid():
            self._set_param(key, [col.red(), col.green(), col.blue()])

    def _stopwatch(self, l, key):
        self.ed.snapshot()
        l.props[key].toggle_stopwatch(l.local(self.ed.time))
        self.ed.notify()

    def _toggle_expr(self, l, key):
        p = l.props[key]
        self.ed.snapshot()
        if p.expr:
            p.expr, p.expr_on, p.expr_error = "", False, ""
        else:
            p.expr, p.expr_on = "value", True
        self.ed.notify()

    def eventFilter(self, obj, ev):
        """Нажатие на любой элемент внутри эффекта (значение, секундомер, подпись) выделяет эффект,
        чтобы Delete удалял эффект, а не ключи или весь слой."""
        if ev.type() == QEvent.MouseButtonPress and ev.button() == Qt.LeftButton:
            w = obj
            while w is not None and w.property("fx_sel") is None:
                w = w.parentWidget()
            if w is not None:
                sel = tuple(w.property("fx_sel"))
                if self.ed.sel_effect != sel:
                    self.ed.sel_effect = sel
                    self.ed.sel_keys = set()
                    for b in self.inner.findChildren(QFrame, "FxBox"):
                        on = tuple(b.property("fx_sel") or ()) == sel
                        b.setStyleSheet("#FxBox { background:#1b1b1b; border:1px solid %s; }"
                                        % ("#2d8ceb" if on else "#2c2c2c"))
        return False

    def _select_fx(self, l, fid, e=None):
        """Клик по эффекту — выделить его (Delete удалит, как в AE)."""
        if e is not None and e.button() != Qt.LeftButton:
            return
        self.ed.sel_effect = (l.id, fid)
        self.ed.sel_keys = set()
        self.refresh()
        self.ed.changed.emit()

    def _fx_menu(self, l, fid, gpos):
        self.ed.sel_effect = (l.id, fid)
        ed = self.ed
        m = QMenu(self)
        m.setAttribute(Qt.WA_DeleteOnClose)   # иначе каждое меню ПКМ остаётся ребёнком окна навсегда
        m.addAction(tr("Удалить эффект") + "\tDel", lambda: ed.remove_effect(l, fid))
        m.addAction(tr("Сбросить"), lambda: ed.reset_effect(l, fid))
        m.addAction(tr("Дублировать"), lambda: ed.duplicate_effect(l, fid))
        m.addAction(tr("Копировать") + "\tCtrl+C", lambda: (setattr(ed, "sel_effect", (l.id, fid)), ed.copy_selection()))
        if ed.clipboard and ed.clipboard[0] == "effects":
            m.addAction(tr("Вставить эффект") + "\tCtrl+V", lambda: (ed.select([l.id]), ed.paste()))
        m.addAction(tr("Переименовать…"), lambda: self._rename(l, l.effect_by_id(fid)))
        m.addSeparator()
        m.addAction(tr("Выше"), lambda: ed.move_effect(l, fid, -1))
        m.addAction(tr("Ниже"), lambda: ed.move_effect(l, fid, 1))
        m.addSeparator()
        m.addAction(tr("Удалить все эффекты слоя"), lambda: ed.clear_effects())
        m.exec(gpos)

    def _fx_on(self, fx, val):
        self.ed.snapshot()
        fx["on"] = val
        self.ed.notify()

    def _toggle(self, token):
        self.collapsed.symmetric_difference_update({token})
        self.refresh()

    def _rename(self, l, fx):
        name, ok = QInputDialog.getText(self, tr("Имя эффекта"), tr("Имя:"), text=effect_display_name(fx))
        if ok and name.strip():
            self.ed.snapshot()
            fx["name"] = name.strip()
            self.ed.notify()

    # ------------------------------------------------------------------ перетаскивание эффектов
    def dragEnterEvent(self, e):
        if e.mimeData().hasFormat(MIME_EFFECT):
            e.acceptProposedAction()

    def dropEvent(self, e):
        t = bytes(e.mimeData().data(MIME_EFFECT)).decode()
        if self._layer is not None:
            self.ed.add_effect(t, [self._layer])

    def contextMenuEvent(self, e):
        if self._layer is None:
            return
        m = QMenu(self)
        m.setAttribute(Qt.WA_DeleteOnClose)   # иначе каждое меню ПКМ остаётся ребёнком окна навсегда
        sub = m.addMenu(tr("Добавить эффект"))
        from ..effects import CATEGORIES, category_label, effect_for_layer
        for cat in CATEGORIES:
            items = [(t, d) for t, d in EFFECTS.items() if d.category == cat and effect_for_layer(t, self._layer.kind)]
            if not items:
                continue
            cm = sub.addMenu(category_label(cat))
            for t, d in items:
                cm.addAction(d.label, lambda _=False, t=t: self.ed.add_effect(t, [self._layer]))
        m.exec(e.globalPos())
