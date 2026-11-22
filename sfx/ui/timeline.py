# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""Панель «Таймлайн» в стиле AE: слои, свойства, ключи, линейка, граф-эдитор (Shift+F3)."""
from __future__ import annotations

from ..i18n import tr

import math

import numpy as np

from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QColorDialog, QLineEdit, QMenu, QPlainTextEdit, QWidget

from ..effects import EFFECTS, effect_display_name, find_param
from ..model import (LAYER_COLORS, MASK_PROPS, prop_kind, prop_label, prop_step, prop_suffix, timecode)
from ..shapes import MASK_MODES
from . import theme
from .theme import c

ROW_H = 22
HEADER_H = 30
LEFT_W = 780
# колонки строки слоя (как в AE: переключатели, имя, режимы, родитель)
COL_SOLO, COL_LOCK, COL_LABEL, COL_NUM, COL_TWIRL, COL_NAME = 39, 57, 78, 90, 114, 130
COL_FX, COL_MB, COL_ADJ, COL_3D = LEFT_W - 482, LEFT_W - 462, LEFT_W - 442, LEFT_W - 422
COL_MODE, MODE_W = LEFT_W - 398, 92        # режим наложения
COL_MATTE, MATTE_W = LEFT_W - 300, 96      # трек-матт
COL_PARENT, PARENT_W = LEFT_W - 198, 120
VALUES_X = 270
MARKER_COL = "#e8a33d"
SNAP_PX = 8          # радиус прилипания с Shift, в пикселях
SNAP_COLOR = "#f2c14e"
# цвета кривых, когда в граф-эдиторе сразу несколько свойств (как метки свойств в AE)
CURVE_PALETTE = ["#d6c45a", "#e88ad0", "#6fc2e8", "#f0a050", "#a58cf0", "#e8e8e8", "#7ad68a"]
KIND_ICON = {"video": "▶", "image": "▣", "solid": "■", "text": "T", "audio": "♪", "shape": "★", "camera": "◰",
             "comp": "▦", "null": "□", "light": "☀"}
WAVE_H = 92          # высота строки с волной звука (L — «график для музыки»)
EXPR_COLOR = "#e5677d"
GROUP_NAMES = {"time": tr("Время"), "text": tr("Текст"), "masks": tr("Маски"), "contents": tr("Содержимое"), "effects": tr("Эффекты"),
               "transform": tr("Трансформирование"), "audio": tr("Звук"), "camera": tr("Параметры камеры"),
               "light": tr("Параметры света"), "material": tr("Параметры материала")}
MODE_NAMES = {"add": tr("Сложение"), "subtract": tr("Вычитание"), "intersect": tr("Пересечение"),
              "difference": tr("Разность"), "none": tr("Нет")}


def fmt_angle(v: float) -> str:
    """0x+10.0° как в AE: обороты и остаток."""
    rev = int(v / 360)
    return f"{rev}x{v - rev * 360:+.1f}°"


def group_of(key: str) -> str:
    if key.startswith("mask/"):
        return "masks"
    if key.startswith("shape/"):
        return "contents"
    if key.startswith("fx/"):
        return "effects"
    if key in ("audio_levels", "wave"):
        return "audio"
    if key == "text_reveal":
        return "text"
    if key in ("speed", "time_remap"):
        return "time"
    from ..model import CAMERA_OPTS, LIGHT_OPTS, MATERIAL_OPTS
    if key in CAMERA_OPTS:
        return "camera"
    if key in LIGHT_OPTS:
        return "light"
    if key in MATERIAL_OPTS:
        return "material"
    return "transform"


def nice_step(span: float, lines: int = 5) -> float:
    raw = max(span / lines, 1e-9)
    mag = 10 ** math.floor(math.log10(raw))
    for m in (1, 2, 5, 10):
        if m * mag >= raw:
            return m * mag
    return 10 * mag


def key_polys(cx: float, cy: float, s: float, k) -> list[QPolygonF]:
    """Иконка ключа: левая половина — вход, правая — выход. Ромб = линейно, бабочка = easy ease, квадрат = hold."""
    P = QPointF
    if k.is_eased_in():
        left = [P(cx, cy), P(cx - s, cy - s), P(cx - s, cy + s)]
    else:
        left = [P(cx, cy - s), P(cx - s, cy), P(cx, cy + s)]
    if k.hold:
        right = [P(cx, cy - s), P(cx + s * 0.8, cy - s), P(cx + s * 0.8, cy + s), P(cx, cy + s)]
    elif k.is_eased_out():
        right = [P(cx, cy), P(cx + s, cy - s), P(cx + s, cy + s)]
    else:
        right = [P(cx, cy - s), P(cx + s, cy), P(cx, cy + s)]
    return [QPolygonF(left), QPolygonF(right)]


class _InlineEdit(QLineEdit):
    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.parent()._commit_edit(cancel=True)
            return
        super().keyPressEvent(e)


class _ExprEdit(QPlainTextEdit):
    """Поле выражения: Ctrl+Enter или клик мимо — применить, Esc — отмена."""

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Escape:
            self.parent()._commit_edit(cancel=True)
            return
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and (e.modifiers() & Qt.ControlModifier or e.key() == Qt.Key_Enter):
            self.parent()._commit_edit()
            return
        super().keyPressEvent(e)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        if self.parent()._edit and self.parent()._edit[0] is self:
            self.parent()._commit_edit()


class Timeline(QWidget):
    context_requested = Signal(QPoint, object)   # правая кнопка: глобальная точка, слой (или None)
    open_comp_requested = Signal(str)

    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.ed = editor
        self.setAcceptDrops(True)
        self.setMinimumHeight(180)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.ClickFocus)
        self.view_start = 0.0
        self.ppf = 4.0
        self._last_dur = None
        self.scroll_y = 0.0
        self.shown: dict[str, list[str]] = {}
        self.graph_mode = False
        self.speed_graph = False
        self.graph_all = False     # граф-эдитор: показывать все видимые анимированные свойства
        self._snap_line = None     # кадр, к которому только что прилипли (рисуем жёлтую линию)
        self._pick = None          # pickwhip: (слой, свойство, текущая точка)
        self._row_geom = []        # [(y, h)] строк — для прокрутки и попаданий
        self._sel_item = None      # выбранная маска/фигура: ("mask"|"shape", layer_id, id)
        self._open_expr_after_paint = None
        self.graph_props: set[tuple[str, str]] = set()
        self._multi = []            # другие выделенные слои при перетаскивании значения
        self._prev_shown = {}
        self._scrub_x, self._scrub_mods = 0.0, Qt.NoModifier
        from PySide6.QtCore import QTimer as _QT
        self._edge_timer = _QT(self)
        self._edge_timer.setInterval(30)
        self._edge_timer.timeout.connect(self._edge_scroll)
        self._hits: list = []
        self._drag = None
        self._gmap = None
        self._frozen_gmap = None
        self._edit = None
        self.font_small = QFont()
        self.font_small.setPixelSize(11)
        self.font_tc = QFont()
        self.font_tc.setPixelSize(17)
        self.font_tc.setBold(True)
        editor.changed.connect(self.update)
        # зелёная полоска кэша: кадры приходят до 100+/с — перерисовываемся не чаще ~8 раз/с
        self._cache_timer = QTimer(self, singleShot=True, interval=120, timeout=self.update)
        editor.cache_changed.connect(lambda: self._cache_timer.isActive() or self._cache_timer.start())
        editor.time_changed.connect(self._on_time)
        editor.selection_changed.connect(self.update)

    # ================================================================ геометрия
    @property
    def track_rect(self) -> QRectF:
        return QRectF(LEFT_W, HEADER_H, max(1, self.width() - LEFT_W), max(1, self.height() - HEADER_H))

    def f2x(self, f: float) -> float:
        return LEFT_W + (f - self.view_start) * self.ppf

    def x2f(self, x: float) -> float:
        return (x - LEFT_W) / self.ppf + self.view_start

    def fit(self):
        dur = max(1, self.ed.comp.duration)
        self.ppf = max(0.02, (self.width() - LEFT_W - 24) / dur)
        self.view_start = 0.0
        self.update()

    def zoom(self, factor: float, anchor_x: float | None = None):
        ax = anchor_x if anchor_x is not None else self.f2x(self.ed.time)
        ax = max(LEFT_W, ax)
        fa = self.x2f(ax)
        self.ppf = max(0.02, min(80.0, self.ppf * factor))
        self.view_start = fa - (ax - LEFT_W) / self.ppf
        self.update()

    def _on_time(self, f):
        x = self.f2x(f)
        if x > self.width() - 10 or x < LEFT_W:
            if self.ed.timer.isActive() or self._drag is None:
                self.view_start = f - 20 / self.ppf
        self.update()

    def resizeEvent(self, e):
        if self._last_dur is None:
            self._last_dur = self.ed.comp.duration
            self.fit()
        super().resizeEvent(e)

    # ================================================================ строки
    def rows(self):
        """Строки таймлайна: слой, группы (Маски/Содержимое/Эффекты/Трансформирование/Звук), свойства,
        строки выражений и волны. В shown[layer.id] лежат открытые свойства и элементы (mask/<id>, fx/<id>…)."""
        out = []
        for l in self.ed.comp.layers:
            out.append(("layer", l, None))
            shown = set(self.shown.get(l.id, ()))
            if not shown:
                continue

            def props(keys):
                for k in keys:
                    if k in shown and k in l.props:
                        out.append(("prop", l, k))
                        if l.props[k].expr:
                            out.append(("expr", l, k))
            if any(k in shown and k in l.props for k in ("time_remap", "speed")):
                out.append(("group", l, "time"))
                props(["time_remap", "speed"])
            if "text_reveal" in shown and "text_reveal" in l.props:
                out.append(("group", l, "text"))
                props(["text_reveal"])
            # маски
            ms = [m for m in l.masks if f"mask/{m['id']}" in shown or any(k in shown for k in l.mask_keys(m))]
            if ms:
                out.append(("group", l, "masks"))
                for m in ms:
                    out.append(("mask", l, m["id"]))
                    props(l.mask_keys(m))
            shs = [sh for sh in l.shapes if f"shape/{sh['id']}" in shown or any(k in shown for k in l.shape_keys(sh))]
            if shs:
                out.append(("group", l, "contents"))
                for sh in shs:
                    out.append(("shape", l, sh["id"]))
                    props(l.shape_keys(sh))
            fxs = [fx for fx in l.effects if f"fx/{fx['id']}" in shown or any(k in shown for k in l.effect_keys(fx))]
            if fxs:
                out.append(("group", l, "effects"))
                for fx in fxs:
                    out.append(("fx", l, fx["id"]))
                    props(l.effect_keys(fx))
            tk = [k for k in l.transform_keys() if k in shown]
            if tk:
                out.append(("group", l, "transform"))
                props(tk)
            ok = [k for k in l.option_keys() if k in shown]
            if ok:
                out.append(("group", l, group_of(ok[0])))
                props(ok)
            if ("audio_levels" in shown or "wave" in shown) and "audio_levels" in l.props:
                out.append(("group", l, "audio"))
                props(["audio_levels"])
                if "wave" in shown:
                    out.append(("wave", l, None))
        return out

    def _row_h(self, kind):
        return WAVE_H if kind == "wave" else ROW_H

    def _layout(self, rows):
        """[(y, h)] для каждой строки с учётом прокрутки."""
        y = HEADER_H - self.scroll_y
        geo = []
        for kind, _, _ in rows:
            h = self._row_h(kind)
            geo.append((y, h))
            y += h
        return geo

    @staticmethod
    @staticmethod
    def _all_keys(l, effects_only=False):
        keys = []
        if not effects_only:
            for m in l.masks:
                keys += [f"mask/{m['id']}"] + l.mask_keys(m)
            for sh in l.shapes:
                keys += [f"shape/{sh['id']}"] + l.shape_keys(sh)
        for fx in l.effects:
            keys += [f"fx/{fx['id']}"] + l.effect_keys(fx)
        if not effects_only:
            if "text_reveal" in l.props:
                keys.append("text_reveal")
            if "time_remap" in l.props:
                keys.append("time_remap")
            if "speed" in l.props:
                keys.append("speed")
            keys += l.transform_keys()
            keys += l.option_keys()
            if "audio_levels" in l.props:
                keys += ["audio_levels", "wave"]
        return keys

    def toggle_twirl(self, l):
        self.shown[l.id] = [] if self.shown.get(l.id) else self._all_keys(l)
        self.update()

    def toggle_group(self, l, gid):
        """Свернуть/развернуть группу (Трансформирование, Эффекты, Маски…)."""
        cur = list(self.shown.get(l.id, []))
        mine = [k for k in self._all_keys(l) if group_of(k) == gid]
        if any(k in cur for k in mine):
            cur = [k for k in cur if group_of(k) != gid]
        else:
            cur += [k for k in mine if k not in cur]
        self.shown[l.id] = cur
        self.update()

    def toggle_item(self, l, token, keys):
        """Свернуть/развернуть маску, фигуру или эффект."""
        cur = list(self.shown.get(l.id, []))
        if any(k in cur for k in keys):
            cur = [k for k in cur if k not in keys]
            if token not in cur:
                cur.append(token)
        else:
            cur += [k for k in keys if k not in cur]
        self.shown[l.id] = cur
        self.update()

    def show_props(self, key: str, additive: bool = False):
        """P/S/R/T/A/U/E/M/L как в AE; с Shift — добавить/убрать к уже открытым."""
        layers = list(self.ed.selected() or self.ed.comp.layers)
        if not layers:
            return
        self._prev_shown = {l.id: list(self.shown.get(l.id, [])) for l in layers}
        if key in ("animated", "effects", "masks", "audio"):
            for l in layers:
                if key == "animated":
                    want = [k for k in l.prop_keys() if l.props[k].animated or l.props[k].expr]
                elif key == "effects":
                    want = self._all_keys(l, effects_only=True)
                elif key == "masks":
                    want = [k for m in l.masks for k in (f"mask/{m['id']}", f"mask/{m['id']}/path")]
                else:
                    want = ["audio_levels", "wave"] if "audio_levels" in l.props else []
                cur = self.shown.get(l.id, [])
                if additive:
                    self.shown[l.id] = list(dict.fromkeys(cur + want))
                else:
                    self.shown[l.id] = want
            if not additive:
                wants = {l.id: self.shown[l.id] for l in layers}
                # второе нажатие (U, E, M, L) сворачивает всё — даже если у слоёв разный набор свойств
                if all(set(self._prev_shown.get(lid, [])) == set(w) for lid, w in wants.items()) and \
                        any(wants.values()):
                    for l in layers:
                        self.shown[l.id] = []
        else:
            layers = [l for l in layers if key in l.props]
            if not layers:
                return
            all_solo = all(self.shown.get(l.id) == [key] for l in layers)
            for l in layers:
                cur = list(self.shown.get(l.id, []))
                if additive:
                    cur.remove(key) if key in cur else cur.append(key)
                    self.shown[l.id] = cur
                else:
                    self.shown[l.id] = [] if all_solo else [key]
        self._reveal(layers)
        self.update()

    def _reveal(self, layers):
        """После P/S/R/T/U… прокрутить так, чтобы открытые свойства были видны (у нижних слоёв тоже)."""
        rows = self.rows()
        geo = self._layout(rows)
        ids = {l.id for l in layers if self.shown.get(l.id)}
        if not ids:
            return
        ys = [(y, h) for (k, l, _), (y, h) in zip(rows, geo) if l.id in ids]
        if not ys:
            return
        view_top, view_bot = HEADER_H, self.height()
        top = min(y for y, _ in ys)
        bot = max(y + h for y, h in ys)
        if bot > view_bot:
            self.scroll_y = min(self._max_scroll(), self.scroll_y + (bot - view_bot) + 4)
            top -= (bot - view_bot) + 4
        if top < view_top:
            self.scroll_y = max(0.0, self.scroll_y - (view_top - top))

    def _max_scroll(self):
        total = sum(self._row_h(k) for k, _, _ in self.rows())
        return max(0.0, total - (self.height() - HEADER_H) + ROW_H * 4)   # запас снизу — рамкой выделять слои

    def _hit(self, kind, rect, data=None):
        self._hits.append((kind, rect, data))

    def _hit_at(self, pos):
        for h in reversed(self._hits):
            if h[1].contains(pos):
                return h
        return None

    # ================================================================ рисование
    def paintEvent(self, _):
        comp = self.ed.comp
        if comp.duration != self._last_dur:
            # Длина композиции поменялась (Ctrl+Z, новый клип, скорость). Вписываем заново, только если
            # вся композиция и так была вписана — масштаб, выставленный руками, не трогаем (как в AE).
            old = max(1, self._last_dur or 1)
            was_fit = self.view_start <= 0.5 and abs(self.ppf - max(0.02, (self.width() - LEFT_W - 24) / old)) \
                < 0.02 * self.ppf
            self._last_dur = comp.duration
            if was_fit:
                self.fit()
        p = QPainter(self)
        self._hits = []
        W, H = self.width(), self.height()
        p.fillRect(self.rect(), c(theme.PANEL))
        track = self.track_rect
        # затемнение за пределами композиции
        end_x = self.f2x(comp.duration)
        if end_x < W:
            p.fillRect(QRectF(max(LEFT_W, end_x), HEADER_H, W, H), c("#151515"))

        rows = self.rows()
        self._row_geom = self._layout(rows)
        p.save()
        p.setClipRect(QRectF(0, HEADER_H, W, H - HEADER_H))
        for i, ((kind, l, key), (y, rh)) in enumerate(zip(rows, self._row_geom)):
            if y + rh < HEADER_H or y > H:
                continue
            self._paint_left(p, i, kind, l, key, y, rh)
            if not self.graph_mode:
                p.save()
                p.setClipRect(QRectF(LEFT_W, max(HEADER_H, y), track.width(), rh))
                self._paint_track(p, kind, l, key, y, rh)
                p.restore()
        if not rows:
            p.setPen(c(theme.TEXT_DIM))
            p.drawText(QRectF(0, HEADER_H, LEFT_W, 80), Qt.AlignCenter,
                       tr("Пусто. Импорт — Ctrl+I,\nили перетащи файлы в окно"))
        if self.graph_mode:
            p.save()
            p.setClipRect(track)
            self._paint_graph(p)
            p.restore()
        p.restore()

        self._paint_header(p)
        # CTI — синяя линия как в AE
        x = self.f2x(self.ed.time)
        if x >= LEFT_W:
            p.setPen(QPen(c(theme.ACCENT), 1))
            p.drawLine(QPointF(x, HEADER_H - 12), QPointF(x, H))
            p.setRenderHint(QPainter.Antialiasing)
            p.setBrush(c(theme.ACCENT))
            p.drawPolygon(QPolygonF([QPointF(x - 6, HEADER_H - 14), QPointF(x + 6, HEADER_H - 14),
                                     QPointF(x + 6, HEADER_H - 6), QPointF(x, HEADER_H),
                                     QPointF(x - 6, HEADER_H - 6)]))
            p.setRenderHint(QPainter.Antialiasing, False)
        if self._snap_line is not None and self._drag:
            sx = self.f2x(self._snap_line)
            if sx >= LEFT_W:
                p.setPen(QPen(c(SNAP_COLOR), 1))
                p.drawLine(QPointF(sx, 0), QPointF(sx, H))
        p.fillRect(QRectF(LEFT_W - 1, 0, 1, H), c(theme.BORDER))
        if self._open_expr_after_paint:
            from PySide6.QtCore import QTimer
            args, self._open_expr_after_paint = self._open_expr_after_paint, None
            QTimer.singleShot(0, lambda: self._open_expr_edit(*args))
        if self._pick is not None:   # линия «пики» (pickwhip) к свойству
            l0, k0, a, b = self._pick
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(c(EXPR_COLOR), 1.5))
            p.drawLine(a, b)
            p.setRenderHint(QPainter.Antialiasing, False)
        if self._drag and self._drag[0] in ("marquee", "lmarquee"):
            p.setPen(QPen(c(theme.ACCENT), 1, Qt.DashLine))
            p.setBrush(c(theme.ACCENT, 40))
            p.drawRect(QRectF(self._drag[1], self._drag[2]).normalized())
        if self._drag and self._drag[0] == "reorder" and self._drag[3] and self._drag[4] is not None:
            # куда встанут перетаскиваемые слои — синяя линия, как в AE
            ly = self._layer_top_y(self._drag[4])
            p.fillRect(QRectF(0, ly - 1, W, 3), c(theme.ACCENT))

    def _text(self, p, rect, text, color=theme.TEXT, align=Qt.AlignVCenter | Qt.AlignLeft, font=None):
        if font:
            p.setFont(font)
        p.setPen(c(color))
        p.drawText(rect, align, text)

    def _paint_left(self, p, i, kind, l, key, y, rh=ROW_H):
        sel = l.id in self.ed.sel_layers
        comp = self.ed.comp
        p.setFont(self.font())
        if kind == "layer":
            p.fillRect(QRectF(0, y, LEFT_W, ROW_H), c(theme.ROW_SEL if sel else (theme.ROW_ALT if i % 2 else theme.ROW)))
            er = QRectF(3, y + 3, 16, 16)
            self._text(p, er, "◉" if l.visible else "○", theme.TEXT_HI if l.visible else theme.TEXT_DIM, Qt.AlignCenter)
            self._hit("eye", er, l)
            if l.kind in ("video", "audio") and l.has_audio:
                ar = QRectF(21, y + 3, 16, 16)
                self._text(p, ar, "♪" if l.audio else "×", theme.TEXT_HI if l.audio else theme.TEXT_DIM, Qt.AlignCenter)
                self._hit("audio", ar, l)
            if l.kind not in ("audio", "camera", "light"):
                sr = QRectF(COL_SOLO, y + 3, 16, 16)
                self._paint_solo(p, sr, l.solo)
                self._hit("solo", sr, l)
            lr = QRectF(COL_LOCK, y + 3, 16, 16)
            self._paint_lock(p, lr, l.locked)
            self._hit("lock", lr, l)
            lab_r = QRectF(COL_LABEL, y + 6, 10, 10)
            p.fillRect(lab_r, c(l.color_label()))
            self._hit("label", lab_r.adjusted(-3, -3, 3, 3), l)
            self._text(p, QRectF(COL_NUM, y, 22, ROW_H), str(comp.layers.index(l) + 1), theme.TEXT, Qt.AlignCenter)
            tw = QRectF(COL_TWIRL, y, 14, ROW_H)
            self._text(p, tw, "▼" if self.shown.get(l.id) else "▶", theme.TEXT_DIM, Qt.AlignCenter, self.font_small)
            self._hit("twirl", tw, l)
            nr = QRectF(COL_NAME, y, COL_FX - COL_NAME - 6, ROW_H)
            icon = "◐" if l.adjustment else KIND_ICON.get(l.kind, "")
            name_col = theme.TEXT_DIM if l.locked else (theme.TEXT_HI if sel else theme.TEXT)
            p.save()
            p.setClipRect(nr)
            self._text(p, nr, f"{icon}  {l.name}", name_col, Qt.AlignVCenter | Qt.AlignLeft, self.font())
            p.restore()
            self._hit("name", nr, l)
            if l.kind not in ("audio", "camera", "light"):
                # переключатели: fx, размытие в движении, корректирующий слой, 3D
                if l.effects:
                    fr = QRectF(COL_FX, y + 3, 18, 16)
                    self._switch(p, fr, "fx", l.fx_on)
                    self._hit("fx_on", fr, l)
                mr = QRectF(COL_MB, y + 3, 18, 16)
                self._switch(p, mr, "mb", l.motion_blur)
                self._hit("motion_blur", mr, l)
                jr = QRectF(COL_ADJ, y + 3, 18, 16)
                self._switch(p, jr, "adj", l.adjustment)
                self._hit("adjustment", jr, l)
                dr = QRectF(COL_3D, y + 3, 18, 16)
                self._switch(p, dr, "3d", l.threed)
                self._hit("threed", dr, l)
            elif l.kind == "audio" and l.effects:
                fr = QRectF(COL_FX, y + 3, 18, 16)
                self._switch(p, fr, "fx", l.fx_on)
                self._hit("fx_on", fr, l)
            if l.kind not in ("audio", "camera", "light", "null"):
                from ..model import BLEND_MODES, MATTE_MODES
                from ..i18n import LANG as _L
                br = QRectF(COL_MODE, y + 3, MODE_W, 16)
                p.fillRect(br, c("#1a1a1a"))
                bname = next(((en if _L == "en" else ru) for k, ru, en in BLEND_MODES if k == l.blend), l.blend)
                self._text(p, br.adjusted(4, 0, -12, 0), bname, theme.TEXT if l.blend != "normal" else theme.TEXT_DIM,
                           Qt.AlignVCenter | Qt.AlignLeft, self.font_small)
                self._text(p, QRectF(br.right() - 12, br.top(), 12, 16), "▾", theme.TEXT_DIM, Qt.AlignCenter, self.font_small)
                self._hit("blend", br, l)
                mr_ = QRectF(COL_MATTE, y + 3, MATTE_W, 16)
                p.fillRect(mr_, c("#1a1a1a"))
                mt = l.matte
                ml = comp.layer_by_id(mt["layer"]) if mt else None
                if ml is not None:
                    mname = next(((en if _L == "en" else ru) for k, ru, en in MATTE_MODES if k == mt.get("mode")), "")
                    mtxt = f"{comp.layers.index(ml) + 1}. {mname}"
                else:
                    mtxt = tr("Нет")
                p.save()
                p.setClipRect(mr_.adjusted(0, 0, -12, 0))
                self._text(p, mr_.adjusted(4, 0, 0, 0), mtxt, theme.TEXT if ml else theme.TEXT_DIM,
                           Qt.AlignVCenter | Qt.AlignLeft, self.font_small)
                p.restore()
                self._text(p, QRectF(mr_.right() - 12, mr_.top(), 12, 16), "▾", theme.TEXT_DIM, Qt.AlignCenter, self.font_small)
                self._hit("matte", mr_, l)
            if l.kind != "audio":
                # родитель
                par = comp.layer_by_id(l.parent) if l.parent else None
                pr = QRectF(COL_PARENT, y + 3, PARENT_W, 16)
                p.fillRect(pr, c("#1a1a1a"))
                ptxt = f"@ {comp.layers.index(par) + 1}. {par.name}" if par else tr("@ Нет")
                p.save()
                p.setClipRect(pr.adjusted(0, 0, -12, 0))
                self._text(p, pr.adjusted(4, 0, 0, 0), ptxt, theme.TEXT if par else theme.TEXT_DIM,
                           Qt.AlignVCenter | Qt.AlignLeft, self.font_small)
                p.restore()
                self._text(p, QRectF(pr.right() - 12, pr.top(), 12, 16), "▾", theme.TEXT_DIM, Qt.AlignCenter, self.font_small)
                self._hit("parent", pr, l)
            self._text(p, QRectF(LEFT_W - 72, y, 66, ROW_H), timecode(l.in_point, comp.fps), theme.TEXT_DIM,
                       Qt.AlignVCenter | Qt.AlignRight, self.font_small)
        elif kind == "group":
            p.fillRect(QRectF(0, y, LEFT_W, ROW_H), c("#1f1f1f"))
            tw = QRectF(COL_TWIRL, y, 14, ROW_H)
            self._text(p, tw, "▼", theme.TEXT_DIM, Qt.AlignCenter, self.font_small)
            self._hit("group", QRectF(COL_TWIRL, y, 160, ROW_H), (l, key))
            self._text(p, QRectF(COL_NAME, y, 160, ROW_H), GROUP_NAMES.get(key, key), theme.TEXT_HI)
            if key in ("transform", "camera"):
                rr = QRectF(VALUES_X, y, 60, ROW_H)
                self._text(p, rr, tr("Сброс"), theme.VALUE)
                self._hit("reset_tr", rr, l)
        elif kind == "fx":
            fx = l.effect_by_id(key)
            p.fillRect(QRectF(0, y, LEFT_W, ROW_H), c("#2b3a4a" if self.ed.sel_effect == (l.id, key) else "#202020"))
            self._hit("fx_row", QRectF(0, y, LEFT_W, ROW_H), (l, key))
            cb = QRectF(100, y + 3, 16, 16)
            self._switch(p, cb, "fx", fx.get("on", True))
            self._hit("fx_item_on", cb, (l, key))
            tw = QRectF(COL_NAME, y, 12, ROW_H)
            open_ = any(k in self.shown.get(l.id, []) for k in l.effect_keys(fx))
            self._text(p, tw, "▼" if open_ else "▶", theme.TEXT_DIM, Qt.AlignCenter, self.font_small)
            self._hit("item_twirl", tw, (l, f"fx/{key}", l.effect_keys(fx)))
            bold = QFont(self.font())
            bold.setBold(True)
            self._text(p, QRectF(COL_NAME + 14, y, VALUES_X - COL_NAME - 14, ROW_H), effect_display_name(fx),
                       theme.TEXT_HI if fx.get("on", True) else theme.TEXT_DIM, Qt.AlignVCenter | Qt.AlignLeft, bold)
            rr = QRectF(VALUES_X, y, 50, ROW_H)
            self._text(p, rr, tr("Сброс"), theme.VALUE)
            self._hit("reset_fx", rr, (l, key))
            xr = QRectF(COL_FX - 24, y + 3, 16, 16)
            self._text(p, xr, "✕", theme.TEXT_DIM, Qt.AlignCenter, self.font_small)
            self._hit("fx_del", xr, (l, key))
        elif kind == "mask":
            m = l.mask_by_id(key)
            p.fillRect(QRectF(0, y, LEFT_W, ROW_H), c("#202020"))
            p.fillRect(QRectF(100, y + 6, 10, 10), c("#e8c547"))
            tw = QRectF(COL_NAME, y, 12, ROW_H)
            open_ = any(k in self.shown.get(l.id, []) for k in l.mask_keys(m))
            self._text(p, tw, "▼" if open_ else "▶", theme.TEXT_DIM, Qt.AlignCenter, self.font_small)
            self._hit("item_twirl", tw, (l, f"mask/{key}", l.mask_keys(m)))
            nr = QRectF(COL_NAME + 14, y, VALUES_X - COL_NAME - 18, ROW_H)
            self._text(p, nr, m["name"], theme.TEXT_HI)
            self._hit("mask_name", nr, (l, key))
            mr = QRectF(VALUES_X, y + 3, 100, 16)
            p.fillRect(mr, c("#1a1a1a"))
            self._text(p, mr.adjusted(4, 0, 0, 0), MODE_NAMES.get(m.get("mode", "add")) + " ▾", theme.TEXT,
                       Qt.AlignVCenter | Qt.AlignLeft, self.font_small)
            self._hit("mask_mode", mr, (l, key))
            ir = QRectF(VALUES_X + 108, y + 3, 90, 16)
            self._text(p, ir, ("☑ " if m.get("inverted") else "☐ ") + tr("Инвертировать"), theme.TEXT,
                       Qt.AlignVCenter | Qt.AlignLeft, self.font_small)
            self._hit("mask_inv", ir, (l, key))
            xr = QRectF(COL_FX - 24, y + 3, 16, 16)
            self._text(p, xr, "✕", theme.TEXT_DIM, Qt.AlignCenter, self.font_small)
            self._hit("mask_del", xr, (l, key))
        elif kind == "shape":
            sh = l.shape_by_id(key)
            p.fillRect(QRectF(0, y, LEFT_W, ROW_H), c("#202020"))
            vr = QRectF(100, y + 3, 16, 16)
            self._text(p, vr, "◉" if sh.get("visible", True) else "○", theme.TEXT_HI, Qt.AlignCenter)
            self._hit("shape_vis", vr, (l, key))
            tw = QRectF(COL_NAME, y, 12, ROW_H)
            open_ = any(k in self.shown.get(l.id, []) for k in l.shape_keys(sh))
            self._text(p, tw, "▼" if open_ else "▶", theme.TEXT_DIM, Qt.AlignCenter, self.font_small)
            self._hit("item_twirl", tw, (l, f"shape/{key}", l.shape_keys(sh)))
            nr = QRectF(COL_NAME + 14, y, VALUES_X - COL_NAME - 18, ROW_H)
            self._text(p, nr, sh["name"], theme.TEXT_HI)
            self._hit("shape_name", nr, (l, key))
            x = VALUES_X
            for attr, label in (("fill", tr("Заливка")), ("stroke", tr("Обводка"))):
                on = sh.get(attr, attr == "fill")
                cr = QRectF(x, y + 3, 14, 14)
                self._text(p, QRectF(x, y, 14, ROW_H), "☑" if on else "☐", theme.TEXT, Qt.AlignCenter)
                self._hit("shape_toggle", cr, (l, key, attr))
                sw = QRectF(x + 16, y + 5, 22, 12)
                p.fillRect(sw, c(sh.get(f"{attr}_color", "#ffffff")))
                p.setPen(QPen(c("#000"), 1))
                p.drawRect(sw)
                self._hit("shape_color", sw, (l, key, attr))
                self._text(p, QRectF(x + 42, y, 60, ROW_H), label, theme.TEXT_DIM, font=self.font_small)
                x += 104
            xr = QRectF(COL_FX - 24, y + 3, 16, 16)
            self._text(p, xr, "✕", theme.TEXT_DIM, Qt.AlignCenter, self.font_small)
            self._hit("shape_del", xr, (l, key))
        elif kind == "wave":
            p.fillRect(QRectF(0, y, LEFT_W, rh), c("#1c1c1c"))
            self._text(p, QRectF(140, y, 200, ROW_H), tr("Волна"), theme.TEXT)
        elif kind == "expr":
            prop = l.props[key]
            p.fillRect(QRectF(0, y, LEFT_W, ROW_H), c("#1a1a1a"))
            er = QRectF(100, y + 3, 16, 16)
            self._text(p, er, "=" if prop.expr_on else "≠", EXPR_COLOR if prop.expr_on else theme.TEXT_DIM,
                       Qt.AlignCenter, self.font())
            self._hit("expr_on", er, (l, key))
            pw = QRectF(118, y + 3, 16, 16)
            self._paint_spiral(p, pw)
            self._hit("pickwhip", pw, (l, key))
            lab = tr("Выражение: {}").format(prop_label(l, key))
            if prop.expr_error and prop.expr_on:
                lab = "⚠ " + lab
            nr = QRectF(140, y, LEFT_W - 150, ROW_H)
            self._text(p, nr, lab, "#e05050" if (prop.expr_error and prop.expr_on) else theme.TEXT_DIM,
                       font=self.font_small)
            self._hit("expr_text", nr, (l, key))
        else:
            prop = l.props[key]
            p.fillRect(QRectF(0, y, LEFT_W, ROW_H), c("#1c1c1c"))
            lf = l.local(self.ed.time)
            # навигатор ключей: ◀ ◆ ▶
            if prop.animated:
                for dx, txt, d in ((30, "◀", -1), (58, "▶", 1)):
                    ar = QRectF(dx - 6, y + 3, 12, 16)
                    self._text(p, ar, txt, theme.TEXT_DIM, Qt.AlignCenter, self.font_small)
                    self._hit("keynav", ar, (l, key, d))
            kr = QRectF(36, y + 3, 16, 16)
            if prop.animated:
                has = prop.keyframe_at(lf) is not None
                p.setRenderHint(QPainter.Antialiasing)
                p.setPen(QPen(c(theme.KEY), 1))
                p.setBrush(c(theme.KEY) if has else Qt.NoBrush)
                cx, cy = kr.center().x(), kr.center().y()
                p.drawPolygon(QPolygonF([QPointF(cx, cy - 5), QPointF(cx + 5, cy), QPointF(cx, cy + 5), QPointF(cx - 5, cy)]))
                p.setRenderHint(QPainter.Antialiasing, False)
            self._hit("keybtn", kr, (l, key))
            # секундомер (Alt+клик — выражение) и значок графика
            sw = QRectF(100, y + 3, 16, 16)
            self._paint_stopwatch(p, sw, prop.animated)
            self._hit("stopwatch", sw, (l, key))
            gr = QRectF(118, y + 3, 16, 16)
            in_graph = (l.id, key) in self.graph_props
            self._paint_graph_icon(p, gr, in_graph)
            self._hit("graph_toggle", gr, (l, key))
            nr = QRectF(138, y, VALUES_X - 142, ROW_H)
            if in_graph:
                p.fillRect(nr, c("#34404c"))
            p.save()
            p.setClipRect(nr)
            self._text(p, nr, prop_label(l, key), theme.TEXT_HI if in_graph else theme.TEXT)
            p.restore()
            self._hit("propname", nr, (l, key))
            self._paint_values(p, l, key, prop, lf, y)
            if prop.expr:
                pw = QRectF(LEFT_W - 22, y + 3, 16, 16)
                self._paint_spiral(p, pw)
                self._hit("pickwhip", pw, (l, key))
        p.fillRect(QRectF(0, y + rh - 1, self.width(), 1), c("#161616"))

    def _paint_values(self, p, l, key, prop, lf, y):
        kind = prop_kind(l, key)
        vals = prop.value_at(lf)
        col = EXPR_COLOR if prop.has_expr else theme.VALUE
        x = VALUES_X
        if key == "scale" and len(vals) >= 2:
            lr = QRectF(x, y + 3, 14, 16)
            self._text(p, lr, "⛓" if l.scale_link else "⛓̸", theme.TEXT if l.scale_link else theme.TEXT_DIM,
                       Qt.AlignCenter, self.font_small)
            self._hit("scale_link", lr, l)
            x += 16
        if kind == "checkbox":
            r = QRectF(x, y, 60, ROW_H)
            self._text(p, r, "☑" if round(vals[0]) else "☐", col)
            self._hit("vcheck", r, (l, key))
            return
        if kind == "choice":
            fp = find_param(l, key)
            labels = fp[2].choice_labels() if fp else []
            i = max(0, min(len(labels) - 1, int(round(vals[0])))) if labels else 0
            r = QRectF(x, y + 3, 150, 16)
            p.fillRect(r, c("#1a1a1a"))
            self._text(p, r.adjusted(4, 0, 0, 0), (labels[i] if labels else "?") + "  ▾", col,
                       Qt.AlignVCenter | Qt.AlignLeft, self.font_small)
            self._hit("vchoice", r, (l, key))
            return
        if kind == "color":
            r = QRectF(x, y + 5, 30, 12)
            p.fillRect(r, QColor(*[int(max(0, min(255, v))) for v in vals[:3]]))
            p.setPen(QPen(c("#000"), 1))
            p.drawRect(r)
            self._hit("vcolor", r, (l, key))
            return
        if kind == "path":
            r = QRectF(x, y, 80, ROW_H)
            self._text(p, r, tr("Форма…"), col)
            return
        fm = QFontMetricsF(self.font())
        for d, v in enumerate(vals):
            if kind == "angle":
                s_ = fmt_angle(v)
            else:
                s_ = f"{v:.1f}" + (prop_suffix(l, key) if d == len(vals) - 1 else "")
            w = fm.horizontalAdvance(s_) + 4
            vr = QRectF(x, y, w, ROW_H)
            self._text(p, vr, s_, col, Qt.AlignVCenter | Qt.AlignLeft)
            p.setPen(QPen(c(col, 120), 1, Qt.DotLine))
            p.drawLine(QPointF(x, y + ROW_H - 5), QPointF(x + w - 4, y + ROW_H - 5))
            self._hit("value", vr, (l, key, d))
            x += w
            if d < len(vals) - 1:
                self._text(p, QRectF(x, y, 10, ROW_H), ",", theme.TEXT_DIM)
                x += 10

    def _paint_spiral(self, p, r):
        """Значок «пики» (pickwhip) — спираль."""
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(c(EXPR_COLOR), 1.2))
        p.setBrush(Qt.NoBrush)
        cx, cy = r.center().x(), r.center().y()
        path = QPainterPath()
        for i in range(40):
            a = i / 39 * 4 * math.pi
            rad = 1 + i / 39 * 5
            pt = QPointF(cx + rad * math.cos(a), cy + rad * math.sin(a))
            path.moveTo(pt) if i == 0 else path.lineTo(pt)
        p.drawPath(path)
        p.setRenderHint(QPainter.Antialiasing, False)

    def _paint_graph_icon(self, p, r, on):
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(c(theme.ACCENT if on else "#5a5a5a"), 1.3))
        p.setBrush(Qt.NoBrush)
        path = QPainterPath(QPointF(r.left() + 2, r.bottom() - 3))
        path.cubicTo(QPointF(r.left() + 8, r.bottom() - 3), QPointF(r.left() + 7, r.top() + 3),
                     QPointF(r.right() - 2, r.top() + 3))
        p.drawPath(path)
        p.setRenderHint(QPainter.Antialiasing, False)

    def _switch(self, p, r, text, on, frame=True):
        """Квадратный переключатель как в колонке Switches; mb / adj / 3d — рисованные значки."""
        if frame:
            p.setPen(QPen(c("#3a3a3a"), 1))
            p.setBrush(c("#2f2f2f") if on else Qt.NoBrush)
            p.drawRect(r.adjusted(0.5, 0.5, -0.5, -0.5))
        col = c(theme.TEXT_HI if on else "#5a5a5a")
        if text not in ("mb", "adj", "3d"):
            self._text(p, r, text, theme.TEXT_HI if on else "#555555", Qt.AlignCenter, self.font_small)
            return
        p.save()
        p.setRenderHint(QPainter.Antialiasing)
        cx, cy = r.center().x(), r.center().y()
        if text == "mb":            # размытие в движении: шар со шлейфом из двух «призраков»
            for dx, a in ((-5, 70), (-2.5, 140)):
                g = QColor(col)
                g.setAlpha(a)
                p.setPen(Qt.NoPen)
                p.setBrush(g)
                p.drawEllipse(QPointF(cx + dx, cy), 3.2, 3.2)
            p.setBrush(col)
            p.drawEllipse(QPointF(cx + 2, cy), 3.6, 3.6)
        elif text == "adj":         # корректирующий слой: круг «контраст» — половина залита
            p.setPen(QPen(col, 1.2))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), 5, 5)
            path = QPainterPath()
            path.moveTo(cx, cy - 5)
            path.arcTo(QRectF(cx - 5, cy - 5, 10, 10), 90, 180)
            path.closeSubpath()
            p.fillPath(path, col)
        else:                       # 3D-слой: каркасный куб
            p.setPen(QPen(col, 1.1))
            p.setBrush(Qt.NoBrush)
            f = QRectF(cx - 5, cy - 2.5, 7, 7)
            b = f.translated(3, -3)
            p.drawRect(b)
            p.drawRect(f)
            for a, bb in ((f.topLeft(), b.topLeft()), (f.topRight(), b.topRight()),
                          (f.bottomRight(), b.bottomRight()), (f.bottomLeft(), b.bottomLeft())):
                p.drawLine(a, bb)
        p.restore()

    def _paint_solo(self, p, r, on):
        p.setRenderHint(QPainter.Antialiasing)
        cx, cy = r.center().x(), r.center().y()
        p.setPen(QPen(c(theme.TEXT_HI if on else "#555555"), 1.2))
        p.setBrush(c(theme.TEXT_HI) if on else Qt.NoBrush)
        p.drawEllipse(QPointF(cx, cy), 4.5, 4.5)
        p.setRenderHint(QPainter.Antialiasing, False)

    def _paint_lock(self, p, r, on):
        p.setRenderHint(QPainter.Antialiasing)
        cx, cy = r.center().x(), r.center().y()
        col = c(theme.TEXT_HI if on else "#444444")
        p.setPen(QPen(col, 1.3))
        p.setBrush(Qt.NoBrush)
        if on:
            p.drawArc(QRectF(cx - 3.5, cy - 7, 7, 8), 0, 180 * 16)
            p.setBrush(col)
        else:
            p.drawArc(QRectF(cx - 3.5, cy - 7, 7, 8), 0, 150 * 16)
        p.drawRect(QRectF(cx - 5, cy - 2, 10, 7))
        p.setRenderHint(QPainter.Antialiasing, False)

    def _paint_stopwatch(self, p, r, on):
        p.setRenderHint(QPainter.Antialiasing)
        col = c(theme.ACCENT if on else theme.TEXT_DIM)
        p.setPen(QPen(col, 1.4))
        p.setBrush(c(theme.ACCENT, 70) if on else Qt.NoBrush)
        cx, cy = r.center().x(), r.center().y() + 1
        p.drawEllipse(QPointF(cx, cy), 5.5, 5.5)
        p.drawLine(QPointF(cx, cy), QPointF(cx, cy - 3.5))
        p.drawLine(QPointF(cx, cy), QPointF(cx + 2.5, cy))
        p.drawLine(QPointF(cx - 1.5, cy - 7.5), QPointF(cx + 1.5, cy - 7.5))
        p.setRenderHint(QPainter.Antialiasing, False)

    def _paint_track(self, p, kind, l, key, y, rh=ROW_H):
        sel = l.id in self.ed.sel_layers
        if kind == "layer":
            # «призрак» полной длины исходника (видно, сколько можно вытянуть)
            end = l.local_end(self.ed.comp.fps)
            # тень — только у выделенного: иначе у клипа из длинной записи она закрывала всю дорожку,
            # и клик по пустому месту выделял клип (и начинал slip)
            if end is not None and sel:
                g0, g1 = self.f2x(l.start), self.f2x(l.start + end)
                ghost = QRectF(g0, y + 3, g1 - g0, ROW_H - 6)
                p.fillRect(QRectF(g0, y + 8, g1 - g0, ROW_H - 16), c("#ffffff", 18))
                self._hit("ghost", ghost, l)    # тянуть тень — slip (полоска клипа добавлена позже и важнее)
            x0, x1 = self.f2x(l.in_point), self.f2x(l.out_point)
            bar = QRectF(x0, y + 3, max(2.0, x1 - x0), ROW_H - 6)
            col = c(l.color_label())
            if not sel:
                col = col.darker(150)
            if not l.visible:
                col.setAlpha(90)
            p.fillRect(bar, col)
            if sel:
                p.setPen(QPen(c("#ffffff", 160), 1))
                p.setBrush(Qt.NoBrush)
                p.drawRect(bar.adjusted(0, 0, -1, -1))
            p.save()
            p.setClipRect(bar.intersected(self.track_rect))
            self._text(p, bar.adjusted(6, 0, 0, 0), l.name, "#151515", Qt.AlignVCenter | Qt.AlignLeft, self.font_small)
            p.restore()
            self._hit("bar", bar, l)
            self._paint_transition(p, l, y)
            self._paint_layer_markers(p, l, y)
        elif kind == "prop":
            prop = l.props[key]
            p.setRenderHint(QPainter.Antialiasing)
            for k in prop.keyframes:
                x = self.f2x(l.start + k.frame)
                if x < LEFT_W - 8 or x > self.width() + 8:
                    continue
                selk = (l.id, key, k.frame) in self.ed.sel_keys
                col = c(theme.KEY_SEL if selk else theme.KEY)
                p.setPen(QPen(c("#000000"), 1))
                p.setBrush(col)
                for poly in key_polys(x, y + ROW_H / 2, 5.5, k):
                    p.drawPolygon(poly)
                self._hit("kf", QRectF(x - 6, y + 3, 12, ROW_H - 6), (l, key, k))
            p.setRenderHint(QPainter.Antialiasing, False)
        elif kind == "expr":
            prop = l.props[key]
            r = QRectF(LEFT_W + 6, y, self.width() - LEFT_W - 12, ROW_H)
            first = prop.expr.strip().splitlines()[0] if prop.expr.strip() else ""
            more = " …" if len(prop.expr.strip().splitlines()) > 1 else ""
            txt = first + more
            if prop.expr_error and prop.expr_on:
                txt += "    ⚠ " + prop.expr_error
            mono = QFont("monospace")
            mono.setPixelSize(11)
            self._text(p, r, txt, "#e05050" if (prop.expr_error and prop.expr_on) else EXPR_COLOR,
                       Qt.AlignVCenter | Qt.AlignLeft, mono)
            self._hit("expr_text", r, (l, key))
        elif kind == "wave":
            self._paint_wave(p, l, y, rh)

    def _paint_layer_markers(self, p, l, y):
        """Маркеры слоя — флажки на полоске слоя (внизу), как в AE; хит тот же, что у маркера композиции."""
        p.setRenderHint(QPainter.Antialiasing)
        b = y + ROW_H - 3
        for m in l.markers:
            x = self.f2x(l.start + m["frame"])
            if x < LEFT_W - 6 or x > self.width() + 6:
                continue
            p.setPen(QPen(c("#000000"), 1))
            p.setBrush(c(MARKER_COL))
            p.drawPolygon(QPolygonF([QPointF(x - 4, b - 9), QPointF(x + 4, b - 9), QPointF(x + 4, b - 4),
                                     QPointF(x, b), QPointF(x - 4, b - 4)]))
            if m.get("name"):
                p.setPen(c("#151515"))
                p.drawText(QPointF(x + 6, b - 2), m["name"])
            self._hit("marker", QRectF(x - 5, b - 10, 10, 11), m)
        p.setRenderHint(QPainter.Antialiasing, False)

    def _paint_transition(self, p, l, y):
        """Окно перехода у точки входа слоя: штриховка, край тянется — меняет Duration."""
        from ..transitions import transition_fxs
        for fx in transition_fxs(l):
            self._paint_one_transition(p, l, y, fx)

    def _paint_one_transition(self, p, l, y, fx):
        from ..transitions import window
        w0, w1, _ = window(l, fx)
        r = QRectF(self.f2x(w0), y + 3, max(4.0, self.f2x(w1) - self.f2x(w0)), ROW_H - 6)
        sel = self.ed.sel_effect == (l.id, fx["id"])
        p.save()
        p.setClipRect(r.intersected(self.track_rect))
        p.fillRect(r, c("#ffffff", 70 if sel else 40))
        p.setPen(QPen(c("#ffffff", 120), 1))
        for i in range(int(-r.height()), int(r.width()), 6):
            p.drawLine(QPointF(r.left() + i, r.bottom()), QPointF(r.left() + i + r.height(), r.top()))
        p.setPen(QPen(c("#ffffff", 220), 1))
        p.setBrush(Qt.NoBrush)
        p.drawRect(r.adjusted(0, 0, -1, -1))
        p.restore()
        self._hit("trans", r, (l, fx))

    def _paint_wave(self, p, l, y, rh):
        """Волна звука для монтажа под музыку: пики + громкость (RMS), при приближении — по сэмплам,
        с учётом скорости клипа; оранжевые метки — найденные удары (к ним липнет Shift-перетаскивание)."""
        from ..audio import SR, beats, decode, peaks
        try:
            pk = peaks(l.source)
            raw = decode(l.source)
        except Exception:
            return
        if not len(pk):
            return
        fps = self.ed.comp.fps
        x0, x1 = max(LEFT_W, self.f2x(l.in_point)), min(self.width(), self.f2x(l.out_point))
        if x1 <= x0:
            return
        p.fillRect(QRectF(x0, y + 1, x1 - x0, rh - 2), c("#16201a"))
        mid, amp = y + rh / 2, rh / 2 - 4
        p.setPen(QPen(c("#2b3d30"), 1))
        for db in (-6, -12):                     # линии уровней −6 и −12 дБ
            h = 10 ** (db / 20) * amp
            p.drawLine(QPointF(x0, mid - h), QPointF(x1, mid - h))
            p.drawLine(QPointF(x0, mid + h), QPointF(x1, mid + h))
        p.drawLine(QPointF(x0, mid), QPointF(x1, mid))
        al = l.props.get("audio_levels")
        pen_pk, pen_rms = QPen(c("#5f9a5c"), 1), QPen(c("#3f6b3d"), 1)
        x = int(x0)
        while x < x1:
            f0, f1 = self.x2f(x), self.x2f(x + 1)
            gain = 10 ** (al.value_at(f0 - l.start)[0] / 20) if al is not None else 1.0
            ta, tb = sorted((l.src_time(f0 - l.start, fps), l.src_time(f1 - l.start, fps)))
            s0, s1 = int(ta * SR), max(int(ta * SR) + 1, int(tb * SR))
            if 0 <= s0 < len(raw):
                if s1 - s0 <= 4800:              # приближено — точно по сэмплам
                    blk = raw[s0:min(len(raw), s1)].mean(axis=1)
                    lo, hi = float(blk.min()), float(blk.max())
                    rms = float(np.sqrt((blk * blk).mean()))
                else:
                    i0, i1 = int(ta * 100), max(int(ta * 100) + 1, int(tb * 100))
                    b = pk[i0:min(len(pk), i1)]
                    lo, hi = float(b[:, 0].min()), float(b[:, 1].max())
                    rms = (hi - lo) * 0.3
                lo, hi, rms = lo * gain, hi * gain, rms * gain
                p.setPen(pen_pk)
                p.drawLine(QPointF(x, mid - max(-1, min(1, hi)) * amp), QPointF(x, mid - max(-1, min(1, lo)) * amp))
                r = min(1.0, rms) * amp
                p.setPen(pen_rms)
                p.drawLine(QPointF(x, mid - r), QPointF(x, mid + r))
            x += 1
        try:
            bt = beats(l.source)
        except Exception:
            bt = []
        for t in self._beat_frames(l, bt):    # удары: линия через всю волну + треугольник сверху
            bx = self.f2x(t)
            if x0 <= bx <= x1:
                p.setPen(QPen(c("#ff9d2e", 150), 1))
                p.drawLine(QPointF(bx, y + 2), QPointF(bx, y + rh - 2))
                p.setPen(Qt.NoPen)
                p.setBrush(c("#ff9d2e"))
                p.drawPolygon(QPolygonF([QPointF(bx - 4, y + 1), QPointF(bx + 4, y + 1), QPointF(bx, y + 7)]))
                p.setBrush(Qt.NoBrush)

    def _beat_frames(self, l, bt):
        """Секунды ударов исходника → кадры композиции (с учётом скорости и обрезки)."""
        fps = self.ed.comp.fps
        if not len(bt):
            return []
        sp = l.props.get("speed")
        k = (sp.value[0] if sp is not None and not sp.animated else 100.0) / 100.0
        if getattr(l, "reverse", False) or k <= 0:
            return []
        out = []
        for t in bt:
            f = l.start + t * fps / k
            if l.in_point <= f < l.out_point:
                out.append(f)
        return out

    def _paint_header(self, p):
        comp = self.ed.comp
        W = self.width()
        p.fillRect(QRectF(0, 0, W, HEADER_H), c(theme.PANEL_2))
        tcr = QRectF(10, 0, 150, HEADER_H)
        self._text(p, tcr, timecode(self.ed.time, comp.fps), theme.VALUE, Qt.AlignVCenter | Qt.AlignLeft, self.font_tc)
        self._hit("timecode", tcr, None)
        self._text(p, QRectF(165, 0, COL_FX - 170, HEADER_H),
                   f"{self.ed.time:05d}  ({comp.fps:g} fps)", theme.TEXT_DIM,
                   Qt.AlignVCenter | Qt.AlignLeft, self.font_small)
        for x, t, kind in ((COL_FX, "fx", "h_fx"), (COL_MB, "mb", "h_mb"), (COL_ADJ, "adj", "h_adj"),
                           (COL_3D, "3d", "h_3d")):
            hr = QRectF(x, 0, 18, HEADER_H)
            self._switch(p, hr, t, False, frame=False)
            self._hit(kind, hr, None)
        self._text(p, QRectF(COL_PARENT + 4, 0, PARENT_W, HEADER_H), tr("Родитель"), theme.TEXT_DIM,
                   Qt.AlignVCenter | Qt.AlignLeft, self.font_small)
        self._text(p, QRectF(COL_MODE + 4, 0, MODE_W, HEADER_H), tr("Режим"), theme.TEXT_DIM,
                   Qt.AlignVCenter | Qt.AlignLeft, self.font_small)
        self._text(p, QRectF(COL_MATTE + 4, 0, MATTE_W, HEADER_H), tr("Трек-матт"), theme.TEXT_DIM,
                   Qt.AlignVCenter | Qt.AlignLeft, self.font_small)
        # рабочая область
        wx0, wx1 = max(LEFT_W, self.f2x(comp.work_in)), self.f2x(comp.work_out)
        if wx1 > wx0:
            p.fillRect(QRectF(wx0, 2, wx1 - wx0, 7), c(theme.WORK))
            self._hit("work_bar", QRectF(wx0 + 5, 0, max(0.0, wx1 - wx0 - 10), 11), None)   # тянуть — сдвиг всей области
        # ручки начала и конца (как в AE): широкая зона захвата, узкая видимая ручка
        for kind, fx_ in (("work_in", self.f2x(comp.work_in)), ("work_out", self.f2x(comp.work_out))):
            if LEFT_W - 6 <= fx_ <= self.width() + 6:
                p.fillRect(QRectF(fx_ - (0 if kind == "work_in" else 3), 0, 3, 11), c(theme.ACCENT))
                self._hit(kind, QRectF(fx_ - 6, 0, 12, 11), None)
        # зелёная полоска — кадры, уже просчитанные в память (RAM Preview), как в AE
        cached = sorted(self.ed.cached_frames(getattr(self.ed, "preview_transparent", False)))
        run0 = prev = None
        for f in cached + [None]:
            if f is not None and prev is not None and f == prev + 1:
                prev = f
                continue
            if run0 is not None:
                x0, x1 = max(LEFT_W, self.f2x(run0)), self.f2x(prev + 1)
                if x1 > x0:
                    p.fillRect(QRectF(x0, 9, x1 - x0, 3), c("#3fbf3f"))
            run0 = prev = f
        # деления
        fps_i = max(1, round(comp.fps))
        cands = sorted({1, 2, 5, 10, max(1, fps_i // 2), fps_i, 2 * fps_i, 5 * fps_i, 10 * fps_i,
                        15 * fps_i, 30 * fps_i, 60 * fps_i, 120 * fps_i, 300 * fps_i, 600 * fps_i})
        major = next((s for s in cands if s * self.ppf >= 80), cands[-1])
        minor = max(1, major // 5) if major >= 5 else 1
        f0 = max(0, int(self.x2f(LEFT_W)) // minor * minor)
        f1 = int(self.x2f(W)) + 1
        p.setFont(self.font_small)
        if minor * self.ppf >= 5:
            f = f0
            while f <= f1:
                x = self.f2x(f)
                if f % major == 0:
                    p.setPen(c(theme.TEXT_DIM))
                    p.drawLine(QPointF(x, 12), QPointF(x, HEADER_H))
                    sec = f // fps_i
                    lab = f"{sec // 60}:{sec % 60:02d}" if f % fps_i == 0 else f"{sec}:{f % fps_i:02d}f"
                    p.setPen(c(theme.TEXT))
                    p.drawText(QPointF(x + 3, 22), lab)
                else:
                    p.setPen(c("#4a4a4a"))
                    p.drawLine(QPointF(x, HEADER_H - 6), QPointF(x, HEADER_H))
                f += minor
        p.fillRect(QRectF(0, HEADER_H - 1, W, 1), c(theme.BORDER))
        # маркеры композиции — оранжевые флажки внизу линейки, как в AE
        p.setRenderHint(QPainter.Antialiasing)
        for m in comp.markers:
            x = self.f2x(m["frame"])
            if x < LEFT_W - 6 or x > W + 6:
                continue
            p.setPen(Qt.NoPen)
            p.setBrush(c(MARKER_COL))
            p.drawPolygon(QPolygonF([QPointF(x - 4, HEADER_H - 12), QPointF(x + 4, HEADER_H - 12),
                                     QPointF(x + 4, HEADER_H - 5), QPointF(x, HEADER_H - 1), QPointF(x - 4, HEADER_H - 5)]))
            if m.get("name"):
                p.setPen(c(MARKER_COL))
                p.drawText(QPointF(x + 6, HEADER_H - 3), m["name"])
            self._hit("marker", QRectF(x - 5, HEADER_H - 13, 10, 13), m)
        p.setRenderHint(QPainter.Antialiasing, False)

    # ================================================================ граф-эдитор
    def graph_targets(self):
        comp = self.ed.comp
        out = []
        if self.graph_all:
            for kind, l, key in self.rows():
                if kind == "prop" and l.props[key].animated and (l, key) not in out:
                    out.append((l, key))
            return out
        for lid, key in sorted(self.graph_props):
            l = comp.layer_by_id(lid)
            if l and key in l.props and l.props[key].animated:
                out.append((l, key))
        if not out:
            for lid, key in sorted({(a, b) for a, b, _ in self.ed.sel_keys}):
                l = comp.layer_by_id(lid)
                if l and key in l.props and l.props[key].animated:
                    out.append((l, key))
        if not out:
            for l in self.ed.selected():
                for key in self.shown.get(l.id, []):
                    if key in l.props and l.props[key].animated:
                        out.append((l, key))
        return out

    def _y2v(self, y):
        vmin, vmax, top, h = self._frozen_gmap or self._gmap
        return vmin + (top + h - y) / h * (vmax - vmin)

    def _paint_graph(self, p):
        R = self.track_rect
        p.fillRect(R, c("#1a1a1a"))
        targets = self.graph_targets()
        if not targets:
            self._gmap = None
            self._text(p, R, tr("Граф-эдитор: кликни по названию анимированного свойства\n"
                             "(или выдели его ключи) — здесь появится кривая.\n"
                             "«Все видимые» — сразу все кривые раскрытых свойств (U).\n"
                             "Shift+F3 / Alt+F3 — назад к слоям."), theme.TEXT_DIM, Qt.AlignCenter)
            return
        fps = self.ed.comp.fps
        n = max(2, int(R.width() / 2))
        f0, f1 = self.x2f(R.left()), self.x2f(R.right())
        colors_2 = [theme.CURVE_X, theme.CURVE_Y]
        curves = []  # (color, [(x, v)])
        multi = len(targets) > 1
        for ti, (l, key) in enumerate(targets):
            prop = l.props[key]
            pts_by_dim = [[] for _ in range(prop.dims)] if not self.speed_graph else [[]]
            for i in range(n + 1):
                f = f0 + (f1 - f0) * i / n
                x = self.f2x(f)
                lf = l.local(f)
                if self.speed_graph:
                    a, b = prop.value_at(lf - 0.25), prop.value_at(lf + 0.25)
                    sp = math.sqrt(sum((bb - aa) ** 2 for aa, bb in zip(a, b))) * 2 * fps
                    pts_by_dim[0].append((x, sp))
                else:
                    for d, v in enumerate(prop.value_at(lf)):
                        pts_by_dim[d].append((x, v))
            for d, pts in enumerate(pts_by_dim):
                if self.speed_graph or prop.dims == 1:
                    col = CURVE_PALETTE[ti % len(CURVE_PALETTE)] if multi else theme.CURVE_1
                else:
                    col = colors_2[d % 2]
                curves.append((col, pts))

        handles = self._graph_handles(targets)
        if self._frozen_gmap:
            vmin, vmax = self._frozen_gmap[0], self._frozen_gmap[1]
        else:
            vals = [v for _, pts in curves for _, v in pts] + [h[1] for h in handles]
            vmin, vmax = min(vals), max(vals)
            if self.speed_graph:
                vmin = 0.0
            if vmax - vmin < 1e-6:
                vmin, vmax = vmin - 1, vmax + 1
            pad = (vmax - vmin) * 0.1
            vmin, vmax = vmin - (0 if self.speed_graph else pad), vmax + pad
        top, h = R.top() + 10, R.height() - 20
        mode = tr("ГРАФ-ЭДИТОР") + (tr(" (скорость)") if self.speed_graph else "")
        self._text(p, QRectF(R.left(), R.top() + 2, R.width() - 10, 16), mode, theme.TEXT_DIM,
                   Qt.AlignTop | Qt.AlignRight, self.font_small)
        self._gmap = (vmin, vmax, top, h)

        def v2y(v):
            return top + h - (v - vmin) / (vmax - vmin) * h

        # сетка
        step = nice_step(vmax - vmin)
        v = math.ceil(vmin / step) * step
        p.setFont(self.font_small)
        while v <= vmax:
            y = v2y(v)
            p.setPen(c("#2a2a2a"))
            p.drawLine(QPointF(R.left(), y), QPointF(R.right(), y))
            p.setPen(c(theme.TEXT_DIM))
            p.drawText(QPointF(R.left() + 4, y - 2), f"{v:g}" + (tr(" /с") if self.speed_graph else ""))
            v += step
        # кривые
        p.setRenderHint(QPainter.Antialiasing)
        for col, pts in curves:
            p.setPen(QPen(c(col), 1.6))
            p.drawPolyline(QPolygonF([QPointF(x, v2y(v)) for x, v in pts]))
        # ключи
        for l, key in targets:
            prop = l.props[key]
            for k in prop.keyframes:
                x = self.f2x(l.start + k.frame)
                selk = (l.id, key, k.frame) in self.ed.sel_keys
                if self.speed_graph:
                    lf = k.frame
                    a, b = prop.value_at(lf - 0.25), prop.value_at(lf + 0.25)
                    vals = [math.sqrt(sum((bb - aa) ** 2 for aa, bb in zip(a, b))) * 2 * fps]
                    dims = [None]
                else:
                    vals, dims = k.value, list(range(len(k.value)))
                for d, val in zip(dims, vals):
                    y = v2y(val)
                    p.setPen(QPen(c("#000"), 1))
                    p.setBrush(c(theme.KEY_SEL if selk else "#e8e8e8"))
                    r = QRectF(x - 4, y - 4, 8, 8)
                    p.drawRect(r)
                    self._hit("gkey", r.adjusted(-3, -3, 3, 3), (l, key, k, d))
        # ручки
        for (hx, hv, kx, kv, data) in handles:
            hy, ky = v2y(hv), v2y(kv)
            p.setPen(QPen(c("#f0d050"), 1))
            p.drawLine(QPointF(kx, ky), QPointF(hx, hy))
            p.setBrush(c("#f0d050"))
            p.drawEllipse(QPointF(hx, hy), 4, 4)
            self._hit("ghandle", QRectF(hx - 7, hy - 7, 14, 14), data)
        p.setRenderHint(QPainter.Antialiasing, False)

    def _graph_handles(self, targets):
        """Ручки кривых для выделенных ключей: (x, value, key_x, key_value, data).
        В графике значений — ручки Безье; в графике скорости — влияние (по X) и скорость (по Y), как в AE."""
        out = []
        fps = self.ed.comp.fps
        for l, key in targets:
            ks = l.props[key].keyframes
            for i, k in enumerate(ks):
                if (l.id, key, k.frame) not in self.ed.sel_keys:
                    continue
                segs = []
                if i + 1 < len(ks) and not k.hold:
                    segs.append(("out", k, ks[i + 1]))
                if i > 0 and not ks[i - 1].hold:
                    segs.append(("in", ks[i - 1], k))
                for side, k0, k1 in segs:
                    dt = k1.frame - k0.frame
                    if dt <= 0:
                        continue
                    if self.speed_graph:
                        dist = math.dist(k0.value, k1.value)
                        if dist < 1e-9:
                            continue
                        base = dist / dt * fps
                        if side == "out":
                            t = k0.frame + k0.out_x * dt
                            v = base * k0.out_y / max(k0.out_x, 1e-6)
                            kx = l.start + k0.frame
                        else:
                            t = k1.frame - k1.in_x * dt
                            v = base * k1.in_y / max(k1.in_x, 1e-6)
                            kx = l.start + k1.frame
                        out.append((self.f2x(l.start + t), v, self.f2x(kx), v, (l, key, side, k0, k1, None)))
                        continue
                    d = max(range(len(k0.value)), key=lambda j: abs(k1.value[j] - k0.value[j]))
                    v0, v1 = k0.value[d], k1.value[d]
                    if side == "out":
                        t = k0.frame + k0.out_x * dt
                        v = v0 + k0.out_y * (v1 - v0)
                        kx, kv = l.start + k0.frame, v0
                    else:
                        t = k1.frame - k1.in_x * dt
                        v = v1 - k1.in_y * (v1 - v0)
                        kx, kv = l.start + k1.frame, v1
                    out.append((self.f2x(l.start + t), v, self.f2x(kx), kv, (l, key, side, k0, k1, d)))
        return out

    def _collect_moving_keys(self):
        moving = []
        for lid, key, fr in self.ed.sel_keys:
            l = self.ed.comp.layer_by_id(lid)
            if l:
                k = l.props[key].keyframe_at(fr)
                if k:
                    moving.append((l, key, k, k.frame, list(k.value)))
        return moving

    def _sync_sel_keys(self, moving):
        self.ed.sel_keys = {(l.id, key, k.frame) for l, key, k, _, _ in moving}

    def _select_key(self, l, key, k, toggle, extend=False):
        """toggle (Ctrl) — переключить ключ; extend (Shift) — добавить, не снимая (чтобы тянуть с прилипанием)."""
        t = (l.id, key, k.frame)
        if toggle:
            self.ed.sel_keys.symmetric_difference_update({t})
            self.ed.sel_effect = None
        elif extend:
            self.ed.sel_keys.add(t)
            self.ed.sel_effect = None
        elif t not in self.ed.sel_keys:
            self.ed.sel_keys = {t}
        if l.id not in self.ed.sel_layers:
            self.ed.sel_layers = [l.id]
            self.ed.selection_changed.emit()

    def mousePressEvent(self, e):
        pos = e.position()
        x, y = pos.x(), pos.y()
        mods = e.modifiers()
        add = bool(mods & (Qt.ShiftModifier | Qt.ControlModifier))
        ctrl, shift = bool(mods & Qt.ControlModifier), bool(mods & Qt.ShiftModifier)
        if self._edit:
            self._commit_edit()
        if e.button() == Qt.MiddleButton:
            self._drag = ("pan", x, self.view_start, y, self.scroll_y)
            return
        if e.button() == Qt.RightButton:
            hit = self._hit_at(pos)
            layer = None
            if hit:
                d = hit[2]
                layer = d if hasattr(d, "props") else (d[0] if isinstance(d, tuple) and d and hasattr(d[0], "props") else None)
            if hit and hit[0] in ("fx_row", "reset_fx", "fx_item_on", "fx_del"):
                l, fid = hit[2]
                self.ed.sel_effect = (l.id, fid)
                ed = self.ed
                m = QMenu(self)
                m.setAttribute(Qt.WA_DeleteOnClose)   # иначе каждое меню ПКМ остаётся ребёнком окна навсегда
                m.addAction(tr("Удалить эффект") + "\tDel", lambda: ed.remove_effect(l, fid))
                m.addAction(tr("Сбросить"), lambda: ed.reset_effect(l, fid))
                m.addAction(tr("Дублировать"), lambda: ed.duplicate_effect(l, fid))
                m.addAction(tr("Копировать") + "\tCtrl+C",
                            lambda: (setattr(ed, "sel_effect", (l.id, fid)), ed.copy_selection()))
                if ed.clipboard and ed.clipboard[0] == "effects":
                    m.addAction(tr("Вставить эффект") + "\tCtrl+V", lambda: (ed.select([l.id]), ed.paste()))
                m.exec(e.globalPosition().toPoint())
                return
            if hit and hit[0] in ("shape_name", "shape_vis", "shape_toggle", "shape_color", "shape_del"):
                self._shape_menu(hit[2][0], hit[2][1], e.globalPosition().toPoint())
                return
            if hit and hit[0] in ("propname", "value") and isinstance(hit[2], tuple):
                self._prop_menu(hit[2][0], hit[2][1], e.globalPosition().toPoint())
                return
            if layer is None and x >= 0:   # строка слоя по вертикали
                layer = self._layer_at_y(y)
            self.context_requested.emit(e.globalPosition().toPoint(), layer)
            return
        if e.button() != Qt.LeftButton:
            return
        mk = self._hit_at(pos)
        if mk is not None and mk[0] == "marker":
            if ctrl:                       # Ctrl+клик — удалить маркер, как в AE
                self.ed.remove_marker(mk[2])
            else:
                self.ed.snapshot()
                self._drag = ("marker", mk[2])
            return
        if mk is not None and mk[0] in ("work_in", "work_out", "work_bar"):
            self.ed.stop()
            self.ed.snapshot()
            c_ = self.ed.comp
            self._drag = (mk[0], self.x2f(x), c_.work_in, c_.work_out)
            return
        if y < HEADER_H and x >= LEFT_W:
            self.ed.stop()
            self._drag = ("scrub",)
            self._scrub_to(x, mods)
            return
        hit = self._hit_at(pos)
        if hit is None:
            if self.graph_mode and x >= LEFT_W and not self.graph_all:
                # как в AE: щелчок по пустому месту графика снимает выделение ключей,
                # но кривые остаются на экране — не надо заново выбирать свойство
                self.graph_props |= {(l.id, key) for l, key in self.graph_targets()}
            if not add:
                self.ed.sel_keys.clear()
                if x < LEFT_W or not self.graph_mode:
                    self.ed.deselect()
            if x >= LEFT_W:
                self._drag = ("marquee", pos, pos, set(self.ed.sel_keys))
            else:   # рамкой слева выделяем слои (на свободном месте под списком)
                self._drag = ("lmarquee", pos, pos, list(self.ed.sel_layers) if add else [])
            self.update()
            return
        kind, rect, data = hit
        ed = self.ed
        if kind == "eye":
            ed.snapshot()
            data.visible = not data.visible
            ed.notify()
        elif kind == "audio":
            ed.snapshot()
            data.audio = not data.audio
            ed.notify()
        elif kind == "blend":
            from ..model import BLEND_MODES
            from ..i18n import LANG as _L
            targets = ed.selected() if data.id in ed.sel_layers else [data]
            m = QMenu(self)
            m.setAttribute(Qt.WA_DeleteOnClose)   # иначе каждое меню ПКМ остаётся ребёнком окна навсегда
            for k, ru, en in BLEND_MODES:
                a = m.addAction(en if _L == "en" else ru)
                a.setCheckable(True)
                a.setChecked(data.blend == k)
                a.triggered.connect(lambda _=False, k=k: self._set_blend(targets, k))
            m.exec(self.mapToGlobal(rect.bottomLeft().toPoint()))
        elif kind == "matte":
            self._matte_menu(data, rect)
        elif kind == "label":
            from ..model import LABELS
            from ..i18n import LANG as _L
            from PySide6.QtGui import QIcon, QPixmap
            targets = ed.selected() if data.id in ed.sel_layers else [data]
            m = QMenu(self)
            m.setAttribute(Qt.WA_DeleteOnClose)   # иначе каждое меню ПКМ остаётся ребёнком окна навсегда
            for ru, en, col in LABELS:
                pm = QPixmap(12, 12)
                pm.fill(QColor(col))
                m.addAction(QIcon(pm), en if _L == "en" else ru, lambda _=False, c_=col: ed.set_label(targets, c_))
            m.addSeparator()
            m.addAction(tr("По типу слоя"), lambda: ed.set_label(targets, None))
            m.exec(self.mapToGlobal(rect.bottomLeft().toPoint()))
        elif kind == "threed":
            ed.set_3d(ed.selected() if data.id in ed.sel_layers else [data])
        elif kind == "group":
            self.toggle_group(*data)
        elif kind == "item_twirl":
            self.toggle_item(*data)
        elif kind == "reset_tr":
            ed.reset_transform(data)
        elif kind == "reset_fx":
            ed.reset_effect(*data)
        elif kind in ("mask_name", "shape_name"):
            if data[0].id not in ed.sel_layers:
                ed.select([data[0].id])
            self._sel_item = (kind.split("_")[0], data[0].id, data[1])
            self.update()
        elif kind == "mask_mode":
            self._mask_mode_menu(*data, rect)
        elif kind == "mask_inv":
            l, mid = data
            ed.snapshot()
            m = l.mask_by_id(mid)
            m["inverted"] = not m.get("inverted")
            ed.notify()
        elif kind == "mask_del":
            ed.remove_mask(*data)
        elif kind == "shape_del":
            ed.remove_shape(*data)
        elif kind == "shape_vis":
            l, sid = data
            ed.snapshot()
            sh = l.shape_by_id(sid)
            sh["visible"] = not sh.get("visible", True)
            ed.notify()
        elif kind == "shape_toggle":
            l, sid, attr = data
            ed.snapshot()
            sh = l.shape_by_id(sid)
            sh[attr] = not sh.get(attr, attr == "fill")
            ed.notify()
        elif kind == "shape_color":
            l, sid, attr = data
            sh = l.shape_by_id(sid)
            col = QColorDialog.getColor(QColor(sh.get(f"{attr}_color", "#ffffff")), self, tr("Цвет"))
            if col.isValid():
                ed.snapshot()
                sh[f"{attr}_color"] = col.name()
                ed.notify()
        elif kind == "expr_on":
            l, key = data
            ed.snapshot()
            l.props[key].expr_on = not l.props[key].expr_on
            ed.notify()
        elif kind == "pickwhip":
            l, key = data
            self._drag = ("pick", l, key)
            self._pick = (l, key, rect.center(), pos)
        elif kind == "expr_text":
            self._open_expr_edit(*data)
        elif kind == "keynav":
            l, key, d = data
            lf = l.local(ed.time)
            frames = [k.frame for k in l.props[key].keyframes]
            nxt = [f for f in frames if f > lf] if d > 0 else [f for f in frames if f < lf]
            if nxt:
                ed.set_time(l.start + (nxt[0] if d > 0 else nxt[-1]))
        elif kind == "graph_toggle":
            self.graph_props.symmetric_difference_update({(data[0].id, data[1])})
            self.update()
        elif kind == "scale_link":
            ed.snapshot()
            data.scale_link = not data.scale_link
            ed.notify()
        elif kind in ("vcheck", "vchoice", "vcolor") and data[0].locked:
            return
        elif kind == "vcheck":
            l, key = data
            pr = l.props[key]
            lf = l.local(ed.time)
            ed.snapshot()
            pr.set_value_at(lf, [0.0 if round(pr.raw_at(lf)[0]) else 1.0])
            self._hold_key(pr, lf)
            ed.notify()
        elif kind == "vchoice":
            self._choice_menu(*data, rect)
        elif kind == "vcolor":
            l, key = data
            pr = l.props[key]
            lf = l.local(ed.time)
            v = pr.raw_at(lf)
            col = QColorDialog.getColor(QColor(*[int(max(0, min(255, x))) for x in v[:3]]), self, tr("Цвет"))
            if col.isValid():
                ed.snapshot()
                pr.set_value_at(lf, [col.red(), col.green(), col.blue()])
                ed.notify()
        elif kind in ("solo", "lock", "fx_on", "motion_blur", "adjustment"):
            attr = {"lock": "locked"}.get(kind, kind)
            targets = ed.selected() if data.id in ed.sel_layers else [data]
            ed.toggle_switch(attr, targets)
        elif kind == "parent":
            self._parent_menu(data, rect)
        elif kind == "fx_item_on":
            l, fid = data
            fx = l.effect_by_id(fid)
            if fx:
                ed.snapshot()
                fx["on"] = not fx.get("on", True)
                ed.notify()
        elif kind == "fx_row":
            l, fid = data
            if l.id not in ed.sel_layers:
                ed.select([l.id])
            ed.sel_effect = (l.id, fid)
            ed.sel_keys = set()
            ed.changed.emit()
        elif kind == "fx_del":
            ed.remove_effect(*data)
        elif kind == "twirl":
            self.toggle_twirl(data)
        elif kind == "name":
            if shift and not ctrl:
                ed.select_range(data.id)
            elif not (data.id in ed.sel_layers and not add):
                ed.select([data.id], add=add)
            if not add:
                ed.sel_keys = set()   # как в AE: щелчок по слою снимает выделение ключей (Ctrl+C скопирует слой)
            if data.id in ed.sel_layers:
                self._drag = ("reorder", data, y, False, None)
        elif kind in ("stopwatch", "keybtn", "value", "kf") and data[0].locked:
            return
        elif kind == "stopwatch" and mods & Qt.AltModifier:
            # Alt+клик по секундомеру — включить/убрать выражение, как в AE
            l, key = data
            ed.snapshot()
            pr = l.props[key]
            if pr.expr:
                pr.expr, pr.expr_on, pr.expr_error = "", False, ""
            else:
                pr.expr, pr.expr_on = "value", True
                self.shown.setdefault(l.id, [])
                if key not in self.shown[l.id]:
                    self.shown[l.id].append(key)
            ed.notify()
            if pr.expr:
                self.update()
                self._open_expr_after_paint = (l, key)
        elif kind == "stopwatch":
            l, key = data
            ed.snapshot()
            l.props[key].toggle_stopwatch(l.local(ed.time))
            ed.sel_keys = {t for t in ed.sel_keys if not (t[0] == l.id and t[1] == key)}
            ed.notify()
        elif kind == "keybtn":
            l, key = data
            prop = l.props[key]
            ed.snapshot()
            lf = l.local(ed.time)
            k = prop.keyframe_at(lf)
            if not prop.animated:
                prop.toggle_stopwatch(lf)
            elif k:
                if len(prop.keyframes) == 1:
                    prop.value = list(k.value)
                prop.keyframes.remove(k)
            else:
                prop.add_keyframe(lf)
            ed.notify()
        elif kind == "propname":
            l, key = data
            if l.id not in ed.sel_layers:
                ed.select([l.id])
            keys = {(l.id, key, k.frame) for k in l.props[key].keyframes}
            if add:
                self.graph_props.symmetric_difference_update({(l.id, key)})
                ed.sel_keys |= keys
            else:
                self.graph_props = {(l.id, key)}
                ed.sel_keys = keys
            ed.sel_prop = (l.id, key)
            if not keys and key.startswith("fx/"):  # клик по параметру эффекта = выделить сам эффект
                ed.sel_effect = (l.id, key.split("/")[1])
                ed.changed.emit()
            self.update()
        elif kind == "value":
            l, key, d = data
            if key.startswith("fx/"):
                ed.sel_effect = (l.id, key.split("/")[1])
            ed.snapshot()
            self._multi = [(o, o.props[key].value_at(o.local(ed.time))) for o in ed.selected()
                           if o is not l and key in o.props and not o.locked] if l.id in ed.sel_layers else []
            self._drag = ("value", l, key, d, l.props[key].value_at(l.local(ed.time)), x, False, rect)
            self.setCursor(Qt.SizeHorCursor)
        elif kind == "timecode":
            self._open_edit(rect, timecode(ed.time, ed.comp.fps), self._goto_text)
        elif kind == "kf":
            l, key, k = data
            self._select_key(l, key, k, ctrl, shift)
            ed.snapshot()
            self._drag = ("keys", x, self._collect_moving_keys())
            self.update()
        elif kind == "bar":
            l = data
            if ctrl:
                ed.select([l.id], add=True)
            elif shift:
                if l.id not in ed.sel_layers:
                    ed.select_range(l.id)
            elif l.id not in ed.sel_layers:
                ed.select([l.id])
            if not ctrl and not shift:
                ed.sel_keys = set()
            if l.id not in ed.sel_layers:
                return
            ed.snapshot()
            if x - rect.left() < 6:
                self._drag = ("trim_in", l)
            elif rect.right() - x < 6:
                self._drag = ("trim_out", l)
            else:
                orig = [(s, s.start, s.in_point, s.out_point) for s in ed.selected()]
                self._drag = ("move", x, l, orig)
        elif kind == "ghost":                  # slip: клип на месте, исходник под ним едет
            if data.id not in ed.sel_layers:
                ed.select([data.id])
            ed.snapshot()
            self._drag = ("slip", x, data, 0)
        elif kind == "trans":
            l, fx = data
            ed.select([l.id])
            ed.sel_effect = (l.id, fx["id"])
            ed.changed.emit()
            ed.snapshot()
            self._drag = ("trans", l, fx)
            self.setCursor(Qt.SizeHorCursor)
        elif kind == "gkey":
            l, key, k, d = data
            self._select_key(l, key, k, ctrl, shift)
            ed.snapshot()
            self._frozen_gmap = self._gmap
            self._drag = ("gkey", x, y, d, self._collect_moving_keys(), bool(mods & Qt.ShiftModifier))
            self.update()
        elif kind == "ghandle":
            ed.snapshot()
            self._frozen_gmap = self._gmap
            self._drag = ("ghandle", data)

    def _set_blend(self, layers, mode):
        self.ed.snapshot()
        for l in layers:
            l.blend = mode
        self.ed.notify()

    def _matte_menu(self, l, rect):
        """Трек-матт: слой-матт и тип (альфа / яркость / инверсия). Матт-слой скрывается, как в AE."""
        from ..model import MATTE_MODES
        from ..i18n import LANG as _L
        comp = self.ed.comp
        m = QMenu(self)
        m.setAttribute(Qt.WA_DeleteOnClose)   # иначе каждое меню ПКМ остаётся ребёнком окна навсегда
        none = m.addAction(tr("Нет"))
        none.setCheckable(True)
        none.setChecked(not l.matte)
        none.triggered.connect(lambda _=False: self.ed.set_matte(l, None, None))
        m.addSeparator()
        for i, o in enumerate(comp.layers):
            if o is l or o.kind in ("audio", "camera", "light"):
                continue
            sub = m.addMenu(f"{i + 1}. {o.name}")
            for k, ru, en in MATTE_MODES:
                a = sub.addAction(en if _L == "en" else ru)
                a.setCheckable(True)
                a.setChecked(bool(l.matte) and l.matte.get("layer") == o.id and l.matte.get("mode") == k)
                a.triggered.connect(lambda _=False, o=o, k=k: self.ed.set_matte(l, o, k))
        m.exec(self.mapToGlobal(rect.bottomLeft().toPoint()))

    def _finish_pick(self, pos):
        """Отпустили «пику» над свойством — пишем ссылку на него в выражение."""
        from ..expressions import reference
        l0, k0 = self._pick[0], self._pick[1]
        self._pick = None
        hit = self._hit_at(pos)
        if hit and hit[0] in ("propname", "value", "keybtn", "stopwatch"):
            l1, k1 = hit[2][0], hit[2][1]
            if (l1, k1) != (l0, k0):
                ref = reference(l0, l1, k1)
                if ref:
                    self.ed.snapshot()
                    pr = l0.props[k0]
                    pr.expr, pr.expr_on = ref, True
                    self.ed.notify()
        self.update()

    def _hold_key(self, prop, lf):
        """Флажки и списки меняются скачком — ключ делаем «hold»."""
        k = prop.keyframe_at(lf)
        if k:
            k.hold = True

    def _choice_menu(self, l, key, rect):
        fp = find_param(l, key)
        if not fp:
            return
        pr = l.props[key]
        lf = l.local(self.ed.time)
        cur = int(round(pr.raw_at(lf)[0]))
        m = QMenu(self)
        m.setAttribute(Qt.WA_DeleteOnClose)   # иначе каждое меню ПКМ остаётся ребёнком окна навсегда
        for i, lab in enumerate(fp[2].choice_labels()):
            a = m.addAction(lab)
            a.setCheckable(True)
            a.setChecked(i == cur)

            def pick(_=False, i=i):
                self.ed.snapshot()
                pr.set_value_at(lf, [float(i)])
                self._hold_key(pr, lf)
                self.ed.notify()
            a.triggered.connect(pick)
        m.exec(self.mapToGlobal(rect.bottomLeft().toPoint()))

    def _mask_mode_menu(self, l, mid, rect):
        msk = l.mask_by_id(mid)
        m = QMenu(self)
        m.setAttribute(Qt.WA_DeleteOnClose)   # иначе каждое меню ПКМ остаётся ребёнком окна навсегда
        for mode in MASK_MODES:
            a = m.addAction(MODE_NAMES[mode])
            a.setCheckable(True)
            a.setChecked(msk.get("mode", "add") == mode)

            def pick(_=False, mode=mode):
                self.ed.snapshot()
                msk["mode"] = mode
                self.ed.notify()
            a.triggered.connect(pick)
        m.exec(self.mapToGlobal(rect.bottomLeft().toPoint()))

    def _open_expr_edit(self, l, key):
        """Редактор выражения под строкой (в области дорожек, как в AE)."""
        rows = self.rows()
        geo = self._layout(rows)
        y = None
        for (kind, rl, rk), (ry, rh) in zip(rows, geo):
            if kind == "expr" and rl is l and rk == key:
                y = ry
        if y is None:
            return
        if self._edit:
            self._commit_edit()
        w = _ExprEdit(self)
        mono = QFont("monospace")
        mono.setPixelSize(12)
        w.setFont(mono)
        w.setPlainText(l.props[key].expr)
        w.setStyleSheet(f"background:#151515; color:{EXPR_COLOR}; border:1px solid {theme.ACCENT};")
        w.setToolTip(tr("Ctrl+Enter — применить, Esc — отмена"))
        x0 = LEFT_W + 2
        w.setGeometry(int(x0), int(y), max(300, self.width() - int(x0) - 6), ROW_H * 4)
        w.show()
        w.setFocus()

        def apply(text, l=l, key=key):
            pr = l.props[key]
            if text != pr.expr:
                self.ed.snapshot()
                pr.expr = text
                pr.expr_on = bool(text.strip())
                self.ed.notify()
        self._edit = (w, apply)

    def _parent_menu(self, l, rect):
        comp = self.ed.comp
        m = QMenu(self)
        m.setAttribute(Qt.WA_DeleteOnClose)   # иначе каждое меню ПКМ остаётся ребёнком окна навсегда
        none = m.addAction(tr("Нет"))
        none.setCheckable(True)
        none.setChecked(not l.parent)
        none.triggered.connect(lambda: self.ed.set_parent(l, None))
        m.addSeparator()
        for i, other in enumerate(comp.layers):
            if other is l or other.kind == "audio":
                continue
            a = m.addAction(f"{i + 1}. {other.name}")
            a.setCheckable(True)
            a.setChecked(l.parent == other.id)
            a.setEnabled(self.ed.can_parent(l, other))
            a.triggered.connect(lambda _=False, o=other: self.ed.set_parent(l, o))
        m.exec(self.mapToGlobal(rect.bottomLeft().toPoint()))

    def rename_layer(self, l, rect=None):
        if rect is None:
            rows = self.rows()
            for (kind, rl, _), (ry, rh) in zip(rows, self._layout(rows)):
                if kind == "layer" and rl is l:
                    rect = QRectF(COL_NAME, ry, COL_FX - COL_NAME - 6, ROW_H)
            if rect is None:
                return

        def rename(t, l=l):
            if t.strip():
                self.ed.snapshot()
                l.name = t.strip()
                self.ed.notify()
        self._open_edit(rect, l.name, rename)

    def _layer_at_y(self, y):
        rows = self.rows()
        for (kind, l, _), (ry, rh) in zip(rows, self._layout(rows)):
            if ry <= y < ry + rh:
                return l
        return None

    def dragEnterEvent(self, e):
        from .panels import MIME_ITEM
        from .effect_controls import MIME_EFFECT, MIME_PRESET
        md = e.mimeData()
        if md.hasFormat(MIME_ITEM) or md.hasUrls() or md.hasFormat(MIME_EFFECT) or md.hasFormat(MIME_PRESET):
            e.acceptProposedAction()

    def dragMoveEvent(self, e):
        e.acceptProposedAction()

    def dropEvent(self, e):
        """Из «Проекта»: слой встаёт на время под курсором. Alt + отпустить на слой — заменить его исходник."""
        from .panels import MIME_ITEM
        pos = e.position()
        md = e.mimeData()
        if e.modifiers() & Qt.AltModifier and md.hasFormat(MIME_ITEM):
            target = self._layer_at_y(pos.y())
            refs = bytes(md.data(MIME_ITEM)).decode().split("\n")
            media = [r[6:] for r in refs if r.startswith("media:")]
            if target is not None and media and self.ed.replace_source(target, media[0]):
                e.acceptProposedAction()
                return
        at = max(0, round(self.x2f(pos.x()))) if pos.x() >= LEFT_W else self.ed.time
        w = self.window()
        if hasattr(w, "handle_drop"):
            w.handle_drop(md, at, None, self._layer_at_y(pos.y()))
            e.acceptProposedAction()

    def mouseDoubleClickEvent(self, e):
        if e.position().y() < 12 and e.position().x() >= LEFT_W:   # полоса рабочей области (рисуется на y 2–9)
            self._drag = None
            self.ed.set_work("all")
            return
        hit = self._hit_at(e.position())
        if not hit:
            return
        kind, rect, data = hit
        if kind == "marker":
            self._drag = None
            self._open_edit(QRectF(rect.left(), 0 if rect.top() < HEADER_H else rect.top() - 8, 160, HEADER_H),
                            data.get("name", ""),
                            lambda t, m=data: self.ed.set_marker(m, name=t.strip()))
            return
        if kind == "name":
            if data.kind == "comp":     # как в AE: двойной клик по прекомпу открывает его
                self.open_comp_requested.emit(data.source)
                return
            self.rename_layer(data, rect)
        elif kind == "bar" and data.kind == "comp":
            self.open_comp_requested.emit(data.source)
        elif kind == "value":
            self._drag = None
            self._open_value_edit(rect, *data)
        elif kind in ("mask_name", "shape_name"):
            l, iid = data
            item = l.mask_by_id(iid) if kind == "mask_name" else l.shape_by_id(iid)

            def rename_item(t, item=item):
                if t.strip():
                    self.ed.snapshot()
                    item["name"] = t.strip()
                    self.ed.notify()
            self._open_edit(rect, item["name"], rename_item)
        elif kind == "expr_text":
            self._open_expr_edit(*data)
        elif kind == "kf":
            l, key, k = data
            self.ed.set_time(l.start + k.frame)

    # ================================================================ прилипание (Shift, как в AE)
    def _beat_targets(self, skip_layers=()) -> set[int]:
        """Удары музыки у видимых звуковых слоёв — тоже цели прилипания (как маркеры в AE)."""
        from ..audio import beats
        out = set()
        for l in self.ed.comp.layers:
            if l in skip_layers or l.kind not in ("audio", "video") or not l.has_audio or not l.audio:
                continue
            if "wave" not in self.shown.get(l.id, []):
                continue
            try:
                out |= {int(round(f)) for f in self._beat_frames(l, beats(l.source))}
            except Exception:
                pass
        return out

    def _snap_targets(self, skip_layers=(), skip_keys=(), with_cti=True) -> set[int]:
        """Кадры, к которым можно прилипнуть: курсор, края слоёв, ключи, рабочая область, начало/конец."""
        comp = self.ed.comp
        out = {0, comp.duration, comp.work_in, comp.work_out} | {m["frame"] for m in comp.markers}
        out |= {l.start + m["frame"] for l in comp.layers for m in l.markers}
        if with_cti:
            out.add(self.ed.time)
        skip_l = {l.id for l in skip_layers}
        for l in comp.layers:
            if l.id in skip_l:
                continue
            out.add(l.in_point)
            out.add(l.out_point)
            for prop in l.props.values():
                for k in prop.keyframes:
                    if id(k) not in skip_keys:
                        out.add(l.start + k.frame)
        return out | self._beat_targets([l for l in comp.layers if l.id in skip_l])

    def _snap_delta(self, cands, targets) -> float:
        """Сдвиг (в кадрах, дробный), чтобы ближайший из cands лёг на цель; 0 — если рядом ничего нет.
        cands передаём неокруглёнными: при сильном зуме 8 px меньше кадра, и округление «перепрыгивало» цель."""
        best = None
        lim = SNAP_PX / max(self.ppf, 1e-6)
        for f in cands:
            for t in targets:
                d = t - f
                if abs(d) <= lim and (best is None or abs(d) < abs(best[0])):
                    best = (d, t)
        if best is None:
            self._snap_line = None
            return 0
        self._snap_line = best[1]
        return best[0]

    def _snapping(self, mods) -> bool:
        """Shift — прилипание всегда; галка «Прилипание» на панели инструментов — и без Shift."""
        return bool(mods & Qt.ShiftModifier) or self.ed.snapping

    def _snap(self, f: float) -> float:
        """Прилипание к CTI в пределах 6 пикселей."""
        if abs((f - self.ed.time) * self.ppf) < 6:
            return self.ed.time
        return f

    def _edge_scroll(self):
        """Тянешь курсор времени за край таймлайна — таймлайн сам едет (как в AE)."""
        if not self._drag or self._drag[0] != "scrub":
            self._edge_timer.stop()
            return
        x = self._scrub_x
        right, left = self.width() - 16, LEFT_W + 8
        if x > right:
            self.view_start += min(40.0, (x - right) * 0.6) / self.ppf * 2
        elif x < left and self.view_start > 0:
            self.view_start = max(0.0, self.view_start - min(40.0, (left - x) * 0.6) / self.ppf * 2)
        else:
            self._edge_timer.stop()
            return
        self._scrub_to(x, self._scrub_mods)
        if not self._edge_timer.isActive():
            self._edge_timer.start()
        self.update()

    def _layer_rows(self):
        return [(l, y, h) for (k, l, _), (y, h) in zip(self.rows(), self._layout(self.rows())) if k == "layer"]

    def _drop_index(self, y) -> int:
        """Индекс слоя, перед которым вставим перетаскиваемые (len — в самый низ)."""
        lr = self._layer_rows()
        for i, (l, ly, lh) in enumerate(lr):
            if y < ly + lh / 2:
                return i
        return len(lr)

    def _layer_top_y(self, idx) -> float:
        lr = self._layer_rows()
        if not lr:
            return HEADER_H
        if idx < len(lr):
            return lr[idx][1]
        rows = self.rows()
        geo = self._layout(rows)
        return geo[-1][0] + geo[-1][1]

    def _shape_menu(self, l, sid, gpos):
        """Правая кнопка по фигуре: Add (операторы, как Contents → Add в AE), градиенты, штрихи, концы линий."""
        from ..shape_ops import OPS, op_name
        ed = self.ed
        sh = l.shape_by_id(sid)
        if sh is None:
            return
        m = QMenu(self)
        m.setAttribute(Qt.WA_DeleteOnClose)   # иначе каждое меню ПКМ остаётся ребёнком окна навсегда
        add = m.addMenu(tr("Добавить оператор"))
        for t in ["trim", "repeater", "offset", "pucker", "round", "zigzag", "twist", "wiggle", "merge"] + \
                [t for t in OPS if "/" in t]:   # операторы из плагинов
            add.addAction(op_name(t), lambda _=False, t=t: (ed.add_shape_op(l, sid, t), self._show_shape(l, sid)))
        ops = sh.get("ops", [])
        if ops:
            om = m.addMenu(tr("Операторы фигуры"))
            for o in ops:
                sub = om.addMenu(("✓ " if o.get("on", True) else "✗ ") + op_name(o["type"]))
                sub.addAction(tr("Вкл / выкл"), lambda _=False, o=o: ed.toggle_shape_op(l, sid, o["id"]))
                sub.addAction(tr("Выше"), lambda _=False, o=o: ed.move_shape_op(l, sid, o["id"], -1))
                sub.addAction(tr("Ниже"), lambda _=False, o=o: ed.move_shape_op(l, sid, o["id"], 1))
                sub.addAction(tr("Удалить"), lambda _=False, o=o: ed.remove_shape_op(l, sid, o["id"]))
        m.addSeparator()
        m.addAction(tr("Трансформация фигуры"), lambda: (ed.enable_shape_group(l, sid, "transform"),
                                                         self._show_shape(l, sid)))
        for attr, what, label in (("fill", "fill_grad", tr("Заливка")), ("stroke", "stroke_grad", tr("Обводка"))):
            gm = m.addMenu(label)
            for kind, name in (("solid", tr("Сплошной цвет")), ("linear", tr("Линейный градиент")),
                               ("radial", tr("Радиальный градиент"))):
                def setk(_=False, kind=kind, attr=attr, what=what):
                    if kind != "solid":
                        ed.enable_shape_group(l, sid, what)
                    ed.set_shape_attr(l, sid, f"{attr}_type", kind)
                    self._show_shape(l, sid)
                a = gm.addAction(("● " if sh.get(f"{attr}_type", "solid") == kind else "   ") + name, setk)
            gm.addAction(tr("Второй цвет градиента…"), lambda _=False, attr=attr: self._pick_shape_color2(l, sid, attr))
        st = m.addMenu(tr("Обводка: штрих и концы"))
        st.addAction(tr("Штрих (пунктир)"), lambda: (ed.enable_shape_group(l, sid, "dash"), self._show_shape(l, sid)))
        st.addAction(tr("Непрозрачность обводки"), lambda: (ed.enable_shape_group(l, sid, "stroke_opacity"),
                                                           self._show_shape(l, sid)))
        for val, name in (("round", tr("Концы: круглые")), ("flat", tr("Концы: срезанные")),
                          ("square", tr("Концы: квадратные"))):
            st.addAction(("● " if sh.get("cap", "round") == val else "   ") + name,
                         lambda _=False, v=val: ed.set_shape_attr(l, sid, "cap", v))
        for val, name in (("round", tr("Углы: круглые")), ("miter", tr("Углы: острые")), ("bevel", tr("Углы: срезанные"))):
            st.addAction(("● " if sh.get("join", "round") == val else "   ") + name,
                         lambda _=False, v=val: ed.set_shape_attr(l, sid, "join", v))
        m.exec(gpos)

    def _show_shape(self, l, sid):
        sh = l.shape_by_id(sid)
        if sh is None:
            return
        cur = list(self.shown.get(l.id, []))
        for k in [f"shape/{sid}"] + l.shape_keys(sh):
            if k not in cur:
                cur.append(k)
        self.shown[l.id] = cur
        self.update()

    def _pick_shape_color2(self, l, sid, attr):
        sh = l.shape_by_id(sid)
        col = QColorDialog.getColor(QColor(sh.get(f"{attr}_color2", "#000000")), self, tr("Второй цвет градиента"))
        if col.isValid():
            self.ed.set_shape_attr(l, sid, f"{attr}_color2", col.name())

    def _prop_menu(self, l, key, gpos):
        """Правая кнопка по свойству: сброс только его, копия/вставка значения, разворот ключей."""
        ed = self.ed
        if l.id not in ed.sel_layers:
            ed.select([l.id])
        ed.sel_prop = (l.id, key)
        m = QMenu(self)
        m.setAttribute(Qt.WA_DeleteOnClose)   # иначе каждое меню ПКМ остаётся ребёнком окна навсегда
        a = m.addAction(tr("Сбросить свойство"), lambda: ed.reset_prop(l, key))
        a.setEnabled(ed.prop_default(l, key) is not None)
        m.addSeparator()

        def copy_value():
            ed.sel_keys = set()
            ed.sel_prop = (l.id, key)
            ed.copy_selection()
        m.addAction(tr("Копировать значение") + "\tCtrl+C", copy_value)
        if ed.clipboard and ed.clipboard[0] == "value" and ed.clipboard[1][0] == key:
            m.addAction(tr("Вставить значение") + "\tCtrl+V", ed.paste)
        keys_here = [t for t in ed.sel_keys if t[0] == l.id and t[1] == key]
        if len(keys_here) >= 2:
            m.addSeparator()
            m.addAction(tr("Развернуть ключи во времени"), ed.time_reverse_keys)
        m.exec(gpos)

    def _scrub_to(self, x, mods):
        fr = self.x2f(x)
        f = round(fr)
        if self._snapping(mods):
            f = round(fr + self._snap_delta([fr], self._snap_targets(with_cti=False)))
        else:
            self._snap_line = None
        self.ed.set_time(max(0, min(self.ed.comp.duration - 1, f)))

    def _trim_frame(self, l, x, mods) -> int:
        if self._snapping(mods):
            fr = self.x2f(x)
            return round(fr + self._snap_delta([fr], self._snap_targets(skip_layers=[l])))
        self._snap_line = None
        return round(self._snap(self.x2f(x)))

    def mouseMoveEvent(self, e):
        pos = e.position()
        x, y = pos.x(), pos.y()
        d = self._drag
        if d is None:
            hit = self._hit_at(pos)
            cur = Qt.ArrowCursor
            if hit:
                if hit[0] in ("value", "trans"):
                    cur = Qt.SizeHorCursor
                elif hit[0] == "bar" and (x - hit[1].left() < 6 or hit[1].right() - x < 6):
                    cur = Qt.SizeHorCursor
                elif hit[0] in ("ghandle", "gkey", "kf"):
                    cur = Qt.PointingHandCursor
            self.setCursor(cur)
            tips = {"fx_on": tr("Эффекты слоя вкл/выкл (fx)"), "h_fx": tr("Эффекты слоя вкл/выкл (fx)"),
                    "motion_blur": tr("Размытие в движении"), "h_mb": tr("Размытие в движении"),
                    "adjustment": tr("Корректирующий слой: эффекты действуют на всё, что ниже"),
                    "h_adj": tr("Корректирующий слой: эффекты действуют на всё, что ниже"),
                    "threed": tr("3D-слой: вращение по X/Y/Z, работа с камерой"),
                    "h_3d": tr("3D-слой: вращение по X/Y/Z, работа с камерой"),
                    "trans": tr("Переход: потяните край, чтобы изменить длительность")}
            self.setToolTip(tips.get(hit[0], "") if hit else "")
            return
        ed = self.ed
        kind = d[0]
        if kind == "pick":
            self._pick = (self._pick[0], self._pick[1], self._pick[2], pos)
            self.update()
            return
        if kind == "pan":
            self.view_start = d[2] - (x - d[1]) / self.ppf
            self.scroll_y = max(0.0, min(self._max_scroll(), d[4] - (y - d[3])))
            self.update()
        elif kind == "scrub":
            self._scrub_x, self._scrub_mods = x, e.modifiers()
            self._scrub_to(x, e.modifiers())
            self._edge_scroll()
        elif kind == "reorder":
            _, l, y0, moved, _t = d
            if not moved and abs(y - y0) < 5:
                return
            self._drag = ("reorder", l, y0, True, self._drop_index(y))
            self.setCursor(Qt.ClosedHandCursor)
            self.update()
        elif kind == "lmarquee":
            self._drag = ("lmarquee", d[1], pos, d[3])
            top, bot = sorted((d[1].y(), y))
            ids = list(d[3])
            for (rk, rl, _), (ry, rh) in zip(self.rows(), self._layout(self.rows())):
                if rk == "layer" and ry + rh > top and ry < bot and rl.id not in ids and not rl.locked:
                    ids.append(rl.id)
            ed.sel_layers = ids
            ed.selection_changed.emit()
            self.update()
        elif kind == "value":
            _, l, key, dim, orig, x0, moved, rect = d
            dx = x - x0
            if not moved and abs(dx) < 3:
                return
            self._drag = d[:6] + (True, rect)
            mult = 10.0 if e.modifiers() & Qt.ShiftModifier else (0.1 if e.modifiers() & Qt.ControlModifier else 1.0)
            new = list(orig)
            new[dim] = orig[dim] + dx * prop_step(l, key) * mult
            if key == "scale" and len(new) >= 2 and l.scale_link:  # пропорционально, как с цепочкой в AE
                other = 1 - dim
                new[other] = orig[other] * new[dim] / orig[dim] if abs(orig[dim]) > 1e-9 else new[dim]
            l.props[key].set_value_at(l.local(ed.time), [round(v, 2) for v in new])
            delta = new[dim] - orig[dim]
            for o, ov in getattr(self, "_multi", []):   # как в AE: меняются все выделенные слои сразу
                nv = list(ov)
                if dim < len(nv):
                    nv[dim] = ov[dim] + delta
                    if key == "scale" and len(nv) >= 2 and l.scale_link:
                        other = 1 - dim
                        nv[other] = ov[other] * nv[dim] / ov[dim] if abs(ov[dim]) > 1e-9 else nv[dim]
                o.props[key].set_value_at(o.local(ed.time), [round(v, 2) for v in nv])
            ed.notify()
        elif kind == "keys":
            _, x0, moving = d
            dr = (x - x0) / self.ppf
            df = round(dr)
            if self._snapping(e.modifiers()):
                tg = self._snap_targets(skip_keys={id(m[2]) for m in moving})
                df = round(dr + self._snap_delta([m[0].start + m[3] + dr for m in moving], tg))
            else:
                self._snap_line = None
            for l, key, k, of, _ in moving:
                k.frame = of + df
            for l, key in {(m[0], m[1]) for m in moving}:
                l.props[key].sort()
            self._sync_sel_keys(moving)
            ed.notify()
        elif kind == "move":
            _, x0, lead, orig = d
            dr = (x - x0) / self.ppf
            df = round(dr)
            lead_o = next(o for o in orig if o[0] is lead)
            if self._snapping(e.modifiers()):
                tg = self._snap_targets(skip_layers=[o[0] for o in orig])
                cands = [o[2] + dr for o in orig] + [o[3] + dr for o in orig]
                df = round(dr + self._snap_delta(cands, tg))
            else:
                self._snap_line = None
                for edge in (lead_o[2], lead_o[3]):
                    snapped = self._snap(edge + df)
                    if snapped != edge + df:
                        df = snapped - edge
                        break
            for s, st, i, o in orig:
                s.start, s.in_point, s.out_point = st + df, i + df, o + df
            ed.notify()
        elif kind == "trans":
            # длительность по курсору: центр стыка — вдвое от расстояния, иначе — расстояние до стыка
            l, fx = d[1], d[2]
            from ..effects import prop_key, values
            f = round(self.x2f(x))
            vv = values(l, fx, l.local(l.in_point))
            cut = l.out_point if vv.get("edge", 0) == 1 else l.in_point
            dur = 2 * abs(f - cut) if vv["align"] == 0 else abs(f - cut)
            l.props[prop_key(fx["id"], "dur")].set_value_at(l.local(l.in_point), [max(1, dur)])
            ed.notify()
        elif kind in ("work_in", "work_out", "work_bar"):
            _, f0, a0, b0 = d
            comp = ed.comp
            fr = self.x2f(x)
            snap = self._snapping(e.modifiers())
            if kind == "work_bar":
                dlt = fr - f0
                if snap:
                    dlt += self._snap_delta([a0 + dlt, b0 + dlt], self._snap_targets() - {a0, b0})
                dlt = max(-a0, min(comp.duration - b0, round(dlt)))
                comp.work_in, comp.work_out = a0 + dlt, b0 + dlt
            else:
                if snap:
                    fr += self._snap_delta([fr], self._snap_targets() - {a0 if kind == "work_in" else b0})
                fr = round(fr)
                if kind == "work_in":
                    comp.work_in = max(0, min(comp.work_out - 1, fr))
                else:
                    comp.work_out = min(comp.duration, max(comp.work_in + 1, fr))
            ed.notify()
        elif kind == "marker":
            fr = self.x2f(x)
            if self._snapping(e.modifiers()):
                fr += self._snap_delta([fr], self._snap_targets() - {ed.marker_comp_frame(d[1])})
            ed.set_marker(d[1], frame=round(fr), snapshot=False)
        elif kind == "slip":
            _, x0, l, done = d
            want = round((x - x0) / self.ppf)
            got = ed.slip(l, want - done, snapshot=False)
            self._drag = ("slip", x0, l, done + got)
        elif kind == "trim_in":
            l = d[1]
            nf = self._trim_frame(l, x, e.modifiers())
            if l.source_frames:
                nf = max(nf, l.start)
            l.in_point = min(nf, l.out_point - 1)
            ed.notify()
        elif kind == "trim_out":
            l = d[1]
            nf = self._trim_frame(l, x, e.modifiers())
            end = l.local_end(ed.comp.fps)
            if end is not None:
                nf = min(nf, l.start + int(round(end)))
            l.out_point = max(nf, l.in_point + 1)
            ed.notify()
        elif kind == "marquee":
            self._drag = ("marquee", d[1], pos, d[3])
            r = QRectF(d[1], pos).normalized()
            hits = {(h[2][0].id, h[2][1], h[2][2].frame) for h in self._hits
                    if h[0] in ("kf", "gkey") and h[1].intersects(r)}
            ed.sel_keys = set(d[3]) | hits
            self.update()
        elif kind == "gkey":
            _, x0, y0, dim, moving, _shift = d
            dr = (x - x0) / self.ppf
            df = round(dr)
            dv = self._y2v(y) - self._y2v(y0)
            self._snap_line = None
            if e.modifiers() & Qt.ShiftModifier:  # Shift — только по одной оси + прилипание по времени
                if abs(x - x0) > abs(y - y0):
                    dv = 0
                    tg = self._snap_targets(skip_keys={id(m[2]) for m in moving})
                    df = round(dr + self._snap_delta([m[0].start + m[3] + dr for m in moving], tg))
                else:
                    df = 0
            for l, key, k, of, ov in moving:
                k.frame = of + df
                if dim is not None and dim < len(k.value):
                    k.value[dim] = ov[dim] + dv
            for l, key in {(m[0], m[1]) for m in moving}:
                l.props[key].sort()
            self._sync_sel_keys(moving)
            ed.notify()
        elif kind == "ghandle":
            l, key, side, k0, k1, dim = d[1]
            t = self.x2f(x) - l.start
            v = self._y2v(y)
            dt = max(1e-6, k1.frame - k0.frame)
            if dim is None:   # график скорости: X — влияние, Y — скорость
                base = math.dist(k0.value, k1.value) / dt * ed.comp.fps
                if base > 1e-9:
                    if side == "out":
                        k0.out_x = max(0.01, min(1.0, (t - k0.frame) / dt))
                        k0.out_y = max(0.0, v) / base * k0.out_x
                    else:
                        k1.in_x = max(0.01, min(1.0, (k1.frame - t) / dt))
                        k1.in_y = max(0.0, v) / base * k1.in_x
                ed.notify()
                return
            dv = k1.value[dim] - k0.value[dim]
            if side == "out":
                k0.out_x = max(0.01, min(1.0, (t - k0.frame) / dt))
                if abs(dv) > 1e-9:
                    k0.out_y = (v - k0.value[dim]) / dv
            else:
                k1.in_x = max(0.01, min(1.0, (k1.frame - t) / dt))
                if abs(dv) > 1e-9:
                    k1.in_y = (k1.value[dim] - v) / dv
            ed.notify()

    def mouseReleaseEvent(self, e):
        d = self._drag
        self._drag = None
        self._frozen_gmap = None
        self._snap_line = None
        if d is None:
            return
        kind = d[0]
        if kind == "pick":
            self._finish_pick(e.position())
            return
        if kind == "trans":
            self.setCursor(Qt.ArrowCursor)
            return
        if kind == "reorder":
            self.setCursor(Qt.ArrowCursor)
            if d[3] and d[4] is not None:
                self.ed.move_layers_to(self.ed.selected(), d[4])
            self.update()
            return
        if kind == "value":
            self.setCursor(Qt.ArrowCursor)
            if not d[6]:  # просто клик — открыть ввод числа
                self._open_value_edit(d[7], d[1], d[2], d[3])
        elif kind in ("keys", "gkey"):
            moving = d[2] if kind == "keys" else d[4]
            self._resolve_collisions(moving)
        self.update()

    def _resolve_collisions(self, moving):
        """После перетаскивания ключей: если ключ лёг на чужой — заменяем чужой (как в AE)."""
        moved_ids = {id(m[2]) for m in moving}
        for l, key in {(m[0], m[1]) for m in moving}:
            prop = l.props[key]
            by_frame = {}
            for k in prop.keyframes:
                if k.frame not in by_frame or id(k) in moved_ids:
                    by_frame[k.frame] = k
            prop.keyframes = sorted(by_frame.values(), key=lambda k: k.frame)
        self._sync_sel_keys(moving)
        self.ed.notify()

    def wheelEvent(self, e):
        if self._edit:   # поле ввода числа не «едет» вместе с таймлайном — применяем и закрываем
            self._commit_edit()
        dy, dx = e.angleDelta().y(), e.angleDelta().x()
        mods = e.modifiers()
        dd = dy or dx
        if mods & (Qt.AltModifier | Qt.ControlModifier):
            x = e.position().x()
            self.zoom(1.2 if dd > 0 else 1 / 1.2, x if x >= LEFT_W else None)
        elif mods & Qt.ShiftModifier or (dx and not dy):
            self.view_start -= dd / 120 * 60 / self.ppf
            self.update()
        else:
            self.scroll_y = max(0.0, min(self._max_scroll(), self.scroll_y - dy / 120 * ROW_H * 2))
            self.update()

    # ================================================================ ввод с клавиатуры
    def _open_edit(self, rect: QRectF, text: str, on_commit):
        w = _InlineEdit(self)
        w.setText(text)
        w.selectAll()
        r = rect.toRect()
        w.setGeometry(r.x(), r.y() + 1, max(70, r.width() + 30), r.height() - 2)
        w.show()
        w.setFocus()
        self._edit = (w, on_commit)
        w.returnPressed.connect(self._commit_edit)
        w.editingFinished.connect(self._commit_edit)

    def _commit_edit(self, cancel=False):
        if not self._edit:
            return
        w, fn = self._edit
        self._edit = None
        text = w.toPlainText() if isinstance(w, QPlainTextEdit) else w.text()
        w.hide()
        w.deleteLater()
        if not cancel:
            fn(text)
        self.update()

    def _open_value_edit(self, rect, l, key, dim):
        cur = l.props[key].value_at(l.local(self.ed.time))[dim]

        def apply(text):
            try:
                v = float(text.replace(",", ".").replace("%", "").replace("°", "").strip())
            except ValueError:
                return
            self.ed.snapshot()
            targets = [l] + ([o for o in self.ed.selected() if o is not l and key in o.props and not o.locked]
                             if l.id in self.ed.sel_layers else [])
            for o in targets:   # введённое число — всем выделенным слоям (как в AE)
                vals = o.props[key].value_at(o.local(self.ed.time))
                if dim < len(vals):
                    vals[dim] = v
                    if key == "scale" and len(vals) >= 2 and o.scale_link:
                        vals[1 - dim] = v
                    o.props[key].set_value_at(o.local(self.ed.time), vals)
            self.ed.notify()
        self._open_edit(rect, f"{cur:.2f}".rstrip("0").rstrip("."), apply)

    def _goto_text(self, text):
        t = text.strip().replace(";", ":").replace(".", ":")
        if not t:
            return
        fps_i = max(1, round(self.ed.comp.fps))
        try:
            parts = [int(x) for x in t.split(":")]
        except ValueError:
            return
        if len(parts) == 1:
            f = parts[0]
        else:
            parts = [0] * (4 - len(parts)) + parts
            hh, mm, ss, ff = parts[-4:]
            f = ((hh * 60 + mm) * 60 + ss) * fps_i + ff
        self.ed.set_time(f)
