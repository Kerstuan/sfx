# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""Состояние редактора: проект, текущее время, выделение, undo/redo и все операции над слоями."""
from __future__ import annotations

from .i18n import tr

import copy
import json
import math
import os
import time as _time
import uuid
from dataclasses import asdict

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtCore import QPointF
from PySide6.QtGui import QTransform

from .audio import AudioPlayer
from .cache import FrameCache
from .media import MediaPool
from .effects import EFFECTS, effect_for_layer, prop_key
from .model import DEFAULT_ZOOM, EASE, LINEAR, MASK_PROPS, Composition, Keyframe, Layer, Project, Property
from .shapes import ellipse_vals, rect_vals
from .render import local_transform, parent_chain, parent_world, update_text_size


class Editor(QObject):
    changed = Signal()            # данные проекта изменились — перерисовать всё
    time_changed = Signal(int)
    selection_changed = Signal()
    media_changed = Signal()
    playing_changed = Signal(bool)
    tool_changed = Signal(str)
    color_pick_changed = Signal(bool)  # пипетка включена/выключена
    frame_arrived = Signal(int)   # воркер досчитал кадр
    cache_changed = Signal()      # в RAM-кэше появились/пропали кадры (зелёная полоска)
    prerender_progress = Signal(int, int)  # просчитано, всего
    comp_switched = Signal()      # открыта другая композиция (вкладки, панели)

    def __init__(self):
        super().__init__()
        self.project = Project()
        self.pool = MediaPool(use_proxies=True)
        self.layers_at_start = True    # новые слои — в начале композиции (настройка «Правка → …», QSettings)
        from .proxy import ProxyManager
        self.proxies = ProxyManager(self)   # лёгкие копии видео для быстрой перемотки (на видеокарте)
        self.path: str | None = None
        self.dirty = False
        self.time = 0
        self.sel_layers: list[str] = []                 # id слоёв
        self.sel_effect = None         # (id слоя, id эффекта) — выделенный эффект: Delete удалит его
        self.sel_keys: set[tuple[str, str, int]] = set()  # (layer_id, prop, frame)
        self.cache = FrameCache()
        self._state_hash = None
        self._prerender = None         # (список кадров, сколько всего) пока идёт пререндер
        self.render_service = None     # процессы-рендереры (start_render_service)
        self._state_json = "{}"
        self.adaptive = True           # черновик пониженного разрешения, пока полный кадр в работе
        self._drafts: dict = {}
        self.draft_ms = 0.0
        self._draft_t = 0.0
        self.est_ms = 0.0              # сколько стоит кадр сейчас: лёгкий — считаем сразу, тяжёлый — в воркерах
        self._idle_running = False
        self.sel_prop = None           # (id слоя, ключ) — свойство, выделенное щелчком по названию
        self.clipboard = None          # Ctrl+C: ("effects"|"keys"|"layers", данные)
        self.color_pick = None         # (id слоя, ключ свойства) — ждём щелчка пипеткой во вьювере
        self.preview_scale = 0.5
        self._undo: list[str] = []
        self._redo: list[str] = []
        self._last_tag = None
        self._last_tag_time = 0.0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        for sig in (self.changed, self.media_changed, self.comp_switched):
            sig.connect(self._state_changed)
        self.media_changed.connect(self.ensure_proxies)
        self.media_changed.connect(self.warm_audio)
        # Фоновый пререндер, как «Cache Frames When Idle» в AE: через 0,6 с простоя
        # кадры рабочей области сами считаются в память, порциями, не мешая работе.
        self.idle_cache = True
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.setInterval(600)
        self._idle_timer.timeout.connect(self._idle_step)
        for sig in (self.changed, self.time_changed, self.selection_changed, self.media_changed,
                    self.comp_switched):
            sig.connect(self._idle_restart)
        self.player = AudioPlayer()
        self.audio_preview = True      # звук в превью
        self.tool = "select"           # select | hand | zoom | rotate | orbit | anchor | rect | ellipse | star | pen | text
        self.snapping = False          # «Прилипание» на панели инструментов: липнуть без Shift
        self.render_ms = 0.0           # время последней отрисовки кадра (строка состояния)

    # ------------------------------------------------------------------ базовое
    @property
    def sel_keys(self) -> set:
        return self._sel_keys

    @sel_keys.setter
    def sel_keys(self, keys):
        """Выделили ключи — выделение эффекта снимается (Delete удаляет то, что выделено последним)."""
        self._sel_keys = keys
        if keys:
            self.sel_effect = None

    @property
    def comp(self) -> Composition:
        return self.project.comp

    def notify(self):
        self.dirty = True
        self.changed.emit()

    def selected(self) -> list[Layer]:
        return [l for l in self.comp.layers if l.id in self.sel_layers]

    def current(self) -> Layer | None:
        s = self.selected()
        return s[0] if s else None

    # ------------------------------------------------------------------ undo
    def new_layer_time(self) -> int:
        """Где начинается новый слой без явного места (импорт, двойной клик в «Проекте», Ctrl+Y…).
        Как настройка AE «Create Layers at Composition Start Time» (по умолчанию вкл.) — в начале композиции.
        Раньше всегда было под курсором, а курсор восстанавливается из проекта: после повторного открытия
        клипы вставали, например, на 6,4 с, и начало композиции оставалось пустым."""
        return 0 if self.layers_at_start else int(self.time)

    def snapshot(self, tag: str | None = None):
        """Сохранить состояние перед изменением. Одинаковые теги подряд (<1с) склеиваются."""
        now = _time.monotonic()
        if tag and tag == self._last_tag and now - self._last_tag_time < 1.0:
            self._last_tag_time = now
            return
        self._last_tag, self._last_tag_time = tag, now
        self._undo.append(json.dumps(self.project.comps_dict()))
        if len(self._undo) > 200:
            self._undo.pop(0)
        self._redo.clear()

    def _restore(self, data: str):
        d = json.loads(data)
        if "comps" not in d:          # старый формат стека (одна композиция)
            d = {"comps": [d], "active": d.get("id")}
        active_before = self.project.active
        self.project.load_comps(d)
        if self.project.comp_by_id(active_before):
            self.project.active = active_before
        ids = {l.id for l in self.comp.layers}
        self.sel_layers = [i for i in self.sel_layers if i in ids]
        self.sel_keys.clear()
        self._last_tag = None
        self.notify()
        self.selection_changed.emit()

    def undo(self):
        if self._undo:
            self._redo.append(json.dumps(self.project.comps_dict()))
            self._restore(self._undo.pop())
            self.media_changed.emit()
            self.comp_switched.emit()

    def redo(self):
        if self._redo:
            self._undo.append(json.dumps(self.project.comps_dict()))
            self._restore(self._redo.pop())
            self.media_changed.emit()
            self.comp_switched.emit()

    # ------------------------------------------------------------------ композиции
    def open_comp(self, cid: str):
        """Открыть композицию (вкладка таймлайна, двойной клик по прекомпу или в «Проекте»)."""
        target = self.project.comp_by_id(cid)
        if target is None or target is self.comp:
            return
        self.stop()
        self.comp.time = self.time
        self.project.active = cid
        self.sel_layers, self.sel_keys = [], set()
        self.time = max(0, min(target.duration - 1, getattr(target, "time", 0)))
        self.comp_switched.emit()
        self.changed.emit()
        self.selection_changed.emit()
        self.time_changed.emit(self.time)

    def new_comp(self, name=None, width=None, height=None, fps=None, duration=None, bg=None, open_it=True):
        base = self.comp
        n = len(self.project.comps) + 1
        c = Composition(name or tr("Композиция {}").format(n), width or base.width, height or base.height,
                        fps or base.fps, duration or base.duration)
        if bg:
            c.bg = bg
        self.snapshot()
        self.project.add_comp(c)
        self.dirty = True
        self.media_changed.emit()
        if open_it:
            self.open_comp(c.id)
        return c

    def comp_from_footage(self, path: str):
        """«Новая композиция из видео»: размер, fps и длительность берутся у ролика."""
        info = self.pool.info(path)
        fps = round(info.fps, 3) if info.kind == "video" else self.comp.fps
        dur = max(1, int(round((info.duration or 10) * fps)))
        w, h = (info.width, info.height) if info.width else (self.comp.width, self.comp.height)
        c = self.new_comp(os.path.splitext(os.path.basename(path))[0], w, h, fps, dur)
        self.add_media_layer(path, 0)
        return c

    def delete_comp(self, cid: str):
        if len(self.project.comps) <= 1:
            return False
        c = self.project.comp_by_id(cid)
        if c is None or any(l.kind == "comp" and l.source == cid for o in self.project.comps for l in o.layers):
            return False
        self.snapshot()
        was_active = c is self.comp
        self.project.comps.remove(c)
        if was_active:
            self.project.active = self.project.comps[0].id
            self.sel_layers, self.sel_keys = [], set()
            self.comp_switched.emit()
            self.selection_changed.emit()
        self.media_changed.emit()
        self.notify()
        return True

    def add_comp_layer(self, cid: str, at: int | None = None):
        """Композиция как слой (прекомпозиция) в текущую композицию."""
        nested = self.project.comp_by_id(cid)
        if nested is None or self.project.uses(nested, self.comp.id):
            return None   # нельзя вложить композицию в саму себя
        self.snapshot()
        comp = self.comp
        at = self.new_layer_time() if at is None else at
        layer = Layer("comp", nested.name, nested.width, nested.height, comp, nested.id)
        layer.has_audio = True
        length = max(1, int(round(nested.duration / nested.fps * comp.fps)))
        layer.start = layer.in_point = at
        layer.out_point = at + length
        layer.source_frames = length
        comp.layers.insert(0, layer)
        self.sel_layers = [layer.id]
        self.notify()
        self.selection_changed.emit()
        return layer

    def precompose(self, name: str | None = None, fit_duration: bool = True, leave_attrs: bool = False,
                   open_new: bool = False):
        """Ctrl+Shift+C, как в AE.
        fit_duration — «Подогнать длину композиции под выделенные слои»: прекомп длиной ровно с клипы,
          слой-прекомп стоит там же, где стояли клипы (а не тянется до конца композиции).
        leave_attrs — «Оставить все атрибуты» (только один слой): эффекты, маски и трансформация
          остаются снаружи на слое-прекомпе, внутри — чистый клип."""
        sel = self.selected()
        if not sel:
            return None
        if leave_attrs and (len(sel) != 1 or sel[0].kind in ("camera", "light", "null", "audio")):
            leave_attrs = False
        comp = self.comp
        self.snapshot()
        n = sum(1 for c in self.project.comps if c.name.startswith(tr("Прекомп"))) + 1
        top = min(comp.layers.index(l) for l in sel)
        a = min(l.in_point for l in sel) if fit_duration else 0
        b = max(l.out_point for l in sel) if fit_duration else comp.duration
        dur = max(1, b - a)
        if leave_attrs:
            src = sel[0]
            nc = Composition(name or tr("Прекомп {}").format(n), int(src.width) or comp.width,
                             int(src.height) or comp.height, comp.fps, dur)
            nc.bg = comp.bg
            inner = Layer(src.kind, src.name, src.width, src.height, nc, src.source)
            for attr in ("has_audio", "source_frames", "reverse", "frame_blend", "color", "text", "font_family",
                         "font_size", "bold", "audio", "shapes"):
                if hasattr(src, attr):
                    setattr(inner, attr, copy.deepcopy(getattr(src, attr)))
            inner.start, inner.in_point, inner.out_point = src.start - a, src.in_point - a, src.out_point - a
            if "speed" in src.props:   # время (скорость) — внутри, оформление — снаружи
                inner.props["speed"] = Property.from_dict("speed", src.props["speed"].to_dict())
            inner.refresh_meta()
            nc.layers = [inner]
            outer = Layer.from_dict(copy.deepcopy(src.to_dict()), comp)
            outer.kind, outer.source, outer.name = "comp", nc.id, nc.name
            outer.width, outer.height = float(nc.width), float(nc.height)
            outer.reverse, outer.frame_blend = False, False
            outer.props.pop("speed", None)
            outer.refresh_meta()
            if "speed" in outer.props:
                outer.props["speed"].value, outer.props["speed"].keyframes = [100.0], []
            shift = a - src.start          # ключи оформления были от начала клипа, теперь от начала прекомпа
            for p in outer.props.values():
                for k in p.keyframes:
                    k.frame -= shift
            comp.layers = [l for l in comp.layers if l.id != src.id]
            layer = outer
        else:
            nc = Composition(name or tr("Прекомп {}").format(n), comp.width, comp.height, comp.fps, dur)
            nc.bg = comp.bg
            ids = {l.id for l in sel}
            moved = [l for l in comp.layers if l.id in ids]
            for l in comp.layers:   # связи с родителями через границу прекомпа рвём
                if l.id not in ids and l.parent in ids:
                    l.parent = None
            for l in moved:
                if l.parent and l.parent not in ids:
                    l.parent = None
                l._comp = nc
                l.start, l.in_point, l.out_point = l.start - a, l.in_point - a, l.out_point - a
            comp.layers = [l for l in comp.layers if l.id not in ids]
            nc.layers = moved
            layer = Layer("comp", nc.name, nc.width, nc.height, comp, nc.id)
        nc.work_in, nc.work_out = 0, nc.duration
        self.project.add_comp(nc)
        layer.has_audio = True
        layer.start, layer.in_point, layer.out_point = a, a, a + dur
        layer.source_frames = dur
        comp.layers.insert(min(top, len(comp.layers)), layer)
        self.sel_layers, self.sel_keys = [layer.id], set()
        self.media_changed.emit()
        self.notify()
        self.selection_changed.emit()
        if open_new:
            self.open_comp(nc.id)
        return layer

    def update_solid(self, layer: Layer, name, w, h, color):
        """Изменить солид (Ctrl+Shift+Y): размер меняется вокруг центра, слой остаётся на месте."""
        self.snapshot()
        ow, oh = layer.width, layer.height
        layer.name, layer.width, layer.height, layer.color = name, float(w), float(h), color
        a = layer.props["anchor"]
        if not a.animated and abs(a.value[0] - ow / 2) < 0.01 and abs(a.value[1] - oh / 2) < 0.01:
            a.value = [w / 2, h / 2] + a.value[2:]
        self.notify()

    # ------------------------------------------------------------------ Time Remap / стоп-кадр (Layer → Time в AE)
    def toggle_time_remap(self, layers=None) -> bool:
        """Enable Time Remapping (Ctrl+Alt+T): ключи «время исходника» в начале и конце слоя; повтор — выключить."""
        layers = [l for l in (layers if layers is not None else self.selected()) if l.kind in ("video", "comp")]
        if not layers:
            return False
        self.snapshot()
        fps = self.comp.fps
        on = not all("time_remap" in l.props for l in layers)
        for l in layers:
            if not on:
                l.props.pop("time_remap", None)
                continue
            if "time_remap" in l.props:
                continue
            a, b = l.in_point - l.start, l.out_point - l.start
            ta, tb = l.src_time(a, fps), l.src_time(b, fps)
            prop = Property("time_remap", [ta])
            prop.keyframes = [Keyframe(a, [ta]), Keyframe(b, [tb])]
            l.props["time_remap"] = prop
            l.refresh_meta()
        self.notify()
        return True

    def freeze_frame(self, layers=None, last: bool = False, hold: int | None = None, ripple: bool = False) -> bool:
        """Стоп-кадр через Time Remap с ключом-удержанием (hold), как Layer → Time в AE.
        hold=None — Freeze Frame: текущий кадр до конца слоя; hold=N — удержать N кадров и играть дальше
        (слой удлиняется на N, ключи после курсора сдвигаются; длину паузы потом меняют, двигая ключ);
        last=True — Freeze on Last Frame: последний кадр исходника до конца композиции.
        ripple (с hold) — слои, которые начинаются после конца замороженного, сдвигаются на N кадров:
        следующий клип монтажа не закрывает продолжение после паузы."""
        layers = [l for l in (layers if layers is not None else self.selected()) if l.kind in ("video", "comp")]
        if not last:   # стоп-кадр берётся под курсором — курсор должен быть на слое
            layers = [l for l in layers if l.in_point <= self.time < l.out_point]
        if not layers:
            return False
        self.snapshot()
        fps = self.comp.fps
        if ripple and hold and not last:
            n = max(1, int(hold))
            ends = min(l.out_point for l in layers)
            frozen = {l.id for l in layers}
            for o in self.comp.layers:
                if o.id not in frozen and o.in_point >= ends:
                    o.start += n
                    o.in_point += n
                    o.out_point += n            # ключи слоя в локальном времени — едут вместе со start
            last_out = max((o.out_point for o in self.comp.layers), default=0)
            if last_out > self.comp.duration:      # монтаж стал длиннее — композиция тоже
                if self.comp.work_out == self.comp.duration:
                    self.comp.work_out = last_out
                self.comp.duration = last_out
        for l in layers:
            a, b = int(l.in_point - l.start), int(l.out_point - l.start)
            old = l.props.get("time_remap")
            keys = [Keyframe(k.frame, list(k.value), k.out_x, k.out_y, k.in_x, k.in_y, k.hold)
                    for k in old.keyframes] if old and old.keyframes else \
                [Keyframe(a, [l.src_time(a, fps)]), Keyframe(b, [l.src_time(b, fps)])]
            if last:
                end = l.local_end(fps) if old is None else None
                lf = int(end - 1) if end is not None else b - 1
                lf = max(a, lf)
                t = l.src_time(lf, fps)
                keys = [k for k in keys if k.frame < lf] + [Keyframe(lf, [t], hold=True)]
                l.out_point = max(l.out_point, self.comp.duration)
            else:
                lf = int(round(l.local(self.time)))
                t = l.src_time(lf, fps)
                if hold is None:
                    keys = [k for k in keys if k.frame < lf] + [Keyframe(lf, [t], hold=True)]
                else:
                    n = max(1, int(hold))
                    after = [k for k in keys if k.frame > lf]
                    for k in after:
                        k.frame += n
                    keys = [k for k in keys if k.frame < lf] + [Keyframe(lf, [t], hold=True),
                                                                 Keyframe(lf + n, [t])] + after
                    l.out_point += n
            prop = Property("time_remap", [t])
            prop.keyframes = sorted(keys, key=lambda k: k.frame)
            l.props["time_remap"] = prop
            l.refresh_meta()
        self.notify()
        return True

    def set_speed(self, layers, pct: float, fit: bool = True):
        """Скорость клипа (%): 200 — вдвое быстрее, 50 — вдвое медленнее. Ключи скорости убираются.
        fit — длина слоя меняется вместе со скоростью (как Time Stretch в AE)."""
        layers = [l for l in layers if "speed" in l.props]
        if not layers or pct <= 0:
            return
        self.snapshot()
        for l in layers:
            p = l.props["speed"]
            old = p.value_at(l.local(self.time))[0] if (p.animated or p.has_expr) else p.value[0]
            p.keyframes, p.expr, p.expr_on = [], "", False
            p.value = [float(pct)]
            if fit and old > 0:
                # Как Time Stretch в AE («держать начало слоя»): видимое начало клипа остаётся на месте
                # и показывает тот же кадр исходника, длина меняется в old/pct раз.
                # Раньше растягивалось от начала исходника — у обрезанного клипа он «убегал» вправо.
                r = old / pct
                pin = l.in_point
                old_start = l.start
                l.start = pin - round((pin - old_start) * r)
                l.out_point = max(pin + 1, pin + round((l.out_point - pin) * r))
                for key, prop in l.props.items():       # ключи растягиваются вместе со слоем
                    if key == "speed":
                        continue
                    seen = set()
                    for k in prop.keyframes:
                        comp_f = old_start + k.frame
                        nf = pin + round((comp_f - pin) * r) - l.start
                        while nf in seen:
                            nf += 1
                        seen.add(nf)
                        k.frame = nf
                    prop.keyframes.sort(key=lambda k: k.frame)
                end = l.local_end(self.comp.fps)
                if end is not None:
                    l.out_point = min(l.out_point, l.start + int(round(end)))
                if l.out_point > self.comp.duration:
                    if self.comp.work_out == self.comp.duration:
                        self.comp.work_out = l.out_point
                    self.comp.duration = l.out_point
        self.notify()

    def toggle_layer_flag(self, layers, attr):
        """reverse / frame_blend для выделенных слоёв."""
        layers = [l for l in layers if l.kind in ("video", "audio", "comp")]
        if not layers:
            return
        self.snapshot()
        val = not all(getattr(l, attr, False) for l in layers)
        for l in layers:
            setattr(l, attr, val)
        self.notify()

    def set_matte(self, layer: Layer, matte_layer: Layer | None, mode: str | None):
        """Трек-матт. Слой-матт выключается (как в AE), чтобы его не было видно отдельно."""
        self.snapshot()
        if matte_layer is None:
            layer.matte = None
        else:
            layer.matte = {"layer": matte_layer.id, "mode": mode or "alpha"}
            matte_layer.visible = False
        self.notify()

    def sequence_layers(self, overlap: int = 0, stagger: int | None = None):
        """Помощник ключей: «Расставить по очереди» — каждый следующий начинается, где кончился предыдущий
        (минус overlap). stagger — вместо этого сдвинуть каждый следующий на N кадров (offset)."""
        sel = [self.comp.layer_by_id(i) for i in self.sel_layers]
        sel = [l for l in sel if l is not None]
        if len(sel) < 2:
            return False
        self.snapshot()
        t = sel[0].in_point
        for i, l in enumerate(sel):
            if stagger is not None:
                new_in = sel[0].in_point + i * stagger
            else:
                new_in = t
            d = new_in - l.in_point
            l.start += d
            l.in_point += d
            l.out_point += d
            t = l.out_point - overlap
        last = max(l.out_point for l in sel)
        if last > self.comp.duration:
            if self.comp.work_out == self.comp.duration:
                self.comp.work_out = last
            self.comp.duration = last
        self.notify()
        return True

    def add_null(self):
        self.snapshot()
        n = sum(1 for l in self.comp.layers if l.kind == "null") + 1
        layer = Layer("null", tr("Нуль {}").format(n), 100, 100, self.comp)
        layer.in_point = layer.start = self.new_layer_time()
        self._insert_new(layer)
        return layer

    def set_label(self, layers, color: str | None):
        self.snapshot()
        for l in layers:
            l.label = color
        self.notify()

    def replace_source(self, layer: Layer, path: str):
        """Заменить исходник слоя (Alt+перетаскивание в AE), ключи и эффекты остаются."""
        try:
            info = self.pool.info(path)
        except Exception:
            return False
        if layer.kind not in ("video", "image", "audio") or info.kind != layer.kind and not (
                {layer.kind, info.kind} <= {"video", "image"}):
            return False
        self.snapshot()
        layer.source = path
        layer.kind = info.kind
        layer.name = info.name
        layer.has_audio = info.has_audio
        if info.width:
            layer.width, layer.height = float(info.width), float(info.height)
        if info.kind == "video":
            layer.source_frames = max(1, int(round(info.duration * self.comp.fps)))
        layer.refresh_meta()
        self.notify()
        return True

    # ------------------------------------------------------------------ время
    # ------------------------------------------------------------------ RAM-кэш и пререндер
    def _state_changed(self):
        self._state_hash = None

    def cache_sig(self, transparent: bool = False):
        if self._state_hash is None:
            js = json.dumps(self.project.comps_dict(), sort_keys=True, default=str)
            h = hash(js)
            old = getattr(self, "_last_hash", None)
            self._state_hash = self._last_hash = h
            self._state_json = js
            if old is not None and old != h:
                self.cache.frames = type(self.cache.frames)(
                    (k, v) for k, v in self.cache.frames.items() if k[0][0] == h)
                self.cache.bytes = sum(v.sizeInBytes() for v in self.cache.frames.values())
                if self.render_service is not None:
                    for k in [k for k in self.render_service.queue if k[0][0] != h]:
                        del self.render_service.queue[k]
                self.cache_changed.emit()
        return (self._state_hash, self.project.active, round(self.preview_scale, 4), bool(transparent))

    # ---- фоновые процессы-рендереры
    def start_render_service(self, n_workers: int | None = None):
        """Запустить процессы-рендереры (окно перестаёт ждать кадры). SFX_WORKERS=0 — выключить."""
        from .render_service import RenderService, default_workers
        n = default_workers() if n_workers is None else n_workers
        if self.render_service is not None or n <= 0:
            return
        self.render_service = RenderService(n, self)
        self.render_service.frame_ready.connect(self._on_worker_frame)

    def shutdown(self):
        self.cancel_prerender()
        if self.render_service is not None:
            self.render_service.stop()
            self.render_service = None
        self.proxies.stop()

    def _svc(self):
        svc = self.render_service
        return svc if svc is not None and svc.available else None

    def request_frame(self, frame: int, transparent: bool = False, urgent: bool = False) -> bool:
        """Попросить воркеры посчитать кадр. False — воркеров нет."""
        svc = self._svc()
        if svc is None:
            return False
        if not 0 <= frame < self.comp.duration:
            return True
        sig = self.cache_sig(transparent)
        if self.cache.get(sig, frame) is None:
            svc.request(sig, self._state_json, frame, self.preview_scale, transparent, urgent)
        return True

    def preview_image(self, frame: int, transparent: bool = False):
        """Кадр для вьювера. Готовый — из кэша. Лёгкий кадр считаем сразу (без задержки),
        тяжёлый (эффекты, прыжок по видео) — отдаём воркерам и возвращаем None:
        вьювер пока показывает прошлый кадр, а окно не подвисает."""
        sig = self.cache_sig(transparent)
        img = self.cache.get(sig, frame)
        if img is not None:
            return img
        svc = self._svc()
        if svc is not None and (self.est_ms > 18 or self.timer.isActive() or self._prerender is not None):
            # при проигрывании кадр уже заказан _prefetch по порядку; «срочно» вперёд очереди он бы
            # обогнал будущие кадры и успел устареть — воркеры вечно отставали бы от курсора
            svc.request(sig, self._state_json, frame, self.preview_scale, transparent,
                        urgent=not self.timer.isActive())
            slow_draft = self.draft_ms > 40 and _time.monotonic() - self._draft_t < 1.5
            if self.adaptive and not self.timer.isActive() and self.preview_scale > 0.26 and not slow_draft:
                return self._draft(sig, frame, transparent)
            return None
        img = self.frame_image(frame, transparent)
        self.est_ms = self.render_ms
        return img

    def _draft(self, sig, frame, transparent):
        """Adaptive Resolution, как в AE: пока полный кадр считается в фоне, сразу показываем
        черновик в 4 раза меньше по площади — отклик мгновенный, потом кадр «дорезкивается»."""
        from .render import render_frame
        key = (sig, frame)
        img = self._drafts.get(key)
        if img is None:
            t0 = _time.perf_counter()
            img = render_frame(self.comp, frame, self.pool, self.preview_scale / 2, transparent=transparent)
            self.draft_ms = (_time.perf_counter() - t0) * 1000   # медленный черновик (нет прокси) — пауза
            self._draft_t = _time.monotonic()
            self._drafts[key] = img
            while len(self._drafts) > 8:
                self._drafts.pop(next(iter(self._drafts)))
        return img

    def _on_worker_frame(self, sig, frame, img):
        if sig != self.cache_sig(sig[3]):
            return                                   # кадр от старой версии проекта
        self.cache.put(sig, frame, img)
        self.est_ms = self.render_service.last_ms if self.render_service else self.est_ms
        self.render_ms = self.est_ms
        self.cache_changed.emit()
        self.frame_arrived.emit(frame)
        pr = self._prerender
        if pr is not None:
            todo, total, transparent, psig = pr
            todo.discard(frame)
            self.prerender_progress.emit(total - len(todo), total)
            if not todo:
                self._finish_prerender()
        elif self._idle_running:
            self._idle_feed()

    def frame_image(self, frame: int, transparent: bool = False):
        """Кадр, посчитанный прямо сейчас в этом потоке (из кэша, если есть)."""
        from .render import render_frame
        sig = self.cache_sig(transparent)
        img = self.cache.get(sig, frame)
        if img is None:
            t0 = _time.perf_counter()
            img = render_frame(self.comp, frame, self.pool, self.preview_scale, transparent=transparent)
            self.render_ms = (_time.perf_counter() - t0) * 1000
            self.cache.put(sig, frame, img)
            self.cache_changed.emit()
        return img

    def cached_frames(self, transparent: bool = False) -> set[int]:
        return self.cache.cached_frames(self.cache_sig(transparent))

    @property
    def prerendering(self) -> bool:
        return self._prerender is not None

    def _prefetch(self, n: int | None = None):
        """При проигрывании — заранее заказать кадры впереди курсора."""
        svc = self._svc()
        if svc is None:
            return
        n = n or len(svc.workers) * svc.PER_WORKER + 6
        tp = getattr(self, "preview_transparent", False)
        f = self.time
        ahead = []
        for i in range(1, n + 1):
            g = f + i
            if g >= self.comp.work_out:
                g = self.comp.work_in + (g - self.comp.work_out)
            ahead.append(g)
        keep = set(ahead)
        keep.add(f)
        for k in [k for k in svc.queue if k[1] not in keep]:
            del svc.queue[k]          # курсор уже проехал — не тратим на них воркеры
        for g in ahead:
            self.request_frame(g, tp)

    def ram_preview(self, transparent: bool = False):
        """Num 0: просчитать рабочую область в память, затем проиграть без тормозов.
        Повторное нажатие во время просчёта — остановить."""
        if self._prerender is not None:
            self.cancel_prerender()
            return
        self.stop()
        have = self.cached_frames(transparent)
        todo = [f for f in range(self.comp.work_in, self.comp.work_out) if f not in have]
        if not todo:
            self.set_time(self.comp.work_in)
            self.toggle_play()
            return
        svc = self._svc()
        if svc is not None:     # параллельно во всех воркерах
            self._prerender = (set(todo), len(todo), transparent, self.cache_sig(transparent))
            svc.clear_queue()
            for f in todo:
                svc.request(self.cache_sig(transparent), self._state_json, f, self.preview_scale, transparent)
            self.prerender_progress.emit(0, len(todo))
            return
        self._prerender = (todo, len(todo), transparent, self.cache_sig(transparent))
        self.prerender_progress.emit(0, len(todo))
        QTimer.singleShot(0, self._prerender_step)

    def _finish_prerender(self):
        self._prerender = None
        self.prerender_progress.emit(-1, 0)
        self.time = self.comp.work_in
        self.time_changed.emit(self.time)
        self.toggle_play()

    def warm_audio(self):
        """Декодировать звук роликов заранее в фоне — первый Play не ждёт ffmpeg."""
        import threading
        from .audio import audible_layers, decode, fx_filters
        jobs = []
        for c in self.project.comps:
            for l in audible_layers(c):
                if l.kind != "comp":
                    jobs.append((l.source, fx_filters(l)))
        if jobs:
            threading.Thread(target=lambda: [decode(p, f) for p, f in jobs], daemon=True).start()

    def ensure_proxies(self):
        """Поставить в очередь прокси для всех тяжёлых видео проекта (уже готовые пропускаются)."""
        from .proxy import needs_proxy
        paths = list(self.project.media)
        for c in self.project.comps:
            paths += [l.source for l in c.layers if l.kind == "video" and l.source]
        for path in dict.fromkeys(paths):
            try:
                info = self.pool.info(path)
            except Exception:  # noqa — битый или пропавший файл
                continue
            if needs_proxy(info):
                self.proxies.request(path, info.width, info.height, info.duration)

    def _idle_restart(self, *_):
        """Любое действие пользователя — пауза фонового просчёта и новый отсчёт простоя."""
        if self._idle_running:
            self._idle_running = False
            svc = self._svc()
            if svc is not None and not self.timer.isActive() and self._prerender is None:
                svc.clear_queue()
        if self.idle_cache:
            self._idle_timer.start()

    def set_idle_cache(self, on: bool):
        self.idle_cache = on
        if on:
            self._idle_timer.start()
        else:
            self._idle_timer.stop()
            self._idle_running = False

    def _idle_todo(self, transparent=False):
        have = self.cached_frames(transparent)
        a, b = self.comp.work_in, self.comp.work_out
        t = min(max(self.time, a), b)
        order = list(range(t, b)) + list(range(a, t))   # сначала вперёд от курсора, как в AE
        return [f for f in order if f not in have]

    def _idle_ok(self) -> bool:
        return (self.idle_cache and not self.timer.isActive() and self._prerender is None
                and not self.color_pick and bool(self.comp.layers)
                and self.cache.bytes <= self.cache.limit * 0.9)   # память кончается — не вытесняем свои кадры

    def _idle_step(self):
        if not self._idle_ok():
            return
        if self._svc() is not None:
            self._idle_running = True
            self._idle_feed()
            return
        tp = getattr(self, "preview_transparent", False)
        todo = self._idle_todo(tp)
        if not todo:
            return
        t_end = _time.perf_counter() + 0.03              # 30 мс — интерфейс не подвисает
        while todo and _time.perf_counter() < t_end:
            self.frame_image(todo.pop(0), tp)
        if todo:
            QTimer.singleShot(1, self._idle_step_continue)

    def _idle_feed(self):
        """Держим воркеры занятыми кадрами для фонового кэша (немного заданий впрок)."""
        svc = self._svc()
        if svc is None or not self._idle_ok():
            self._idle_running = False
            return
        tp = getattr(self, "preview_transparent", False)
        sig = self.cache_sig(tp)
        # фон — вполсилы: половина воркеров по одному кадру, остальное ядро остаётся окну и системе
        # (на всех воркерах фоновый кэш грузил все 16 потоков и подтормаживал рабочий стол)
        want = max(1, len(svc.workers) // 2)
        have_q = len(svc.queue) + len(svc.inflight)
        for f in self._idle_todo(tp):
            if have_q >= want:
                break
            if not svc.pending(sig, f):
                svc.request(sig, self._state_json, f, self.preview_scale, tp)
                have_q += 1
        if have_q == 0:
            self._idle_running = False

    def _idle_step_continue(self):
        if not self._idle_timer.isActive():   # пока ждали — пользователь что-то сделал: ждём простоя заново
            self._idle_step()

    def cancel_prerender(self):
        if self._prerender is not None:
            self._prerender = None
            svc = self._svc()
            if svc is not None:
                svc.clear_queue()
            self.prerender_progress.emit(-1, 0)

    def _prerender_step(self):
        if self._prerender is None:
            return
        todo, total, transparent, sig = self._prerender
        if self.cache_sig(transparent) != sig:   # проект изменили во время просчёта — начинаем заново
            have = self.cached_frames(transparent)
            todo = [f for f in range(self.comp.work_in, self.comp.work_out) if f not in have]
            total, sig = len(todo), self.cache_sig(transparent)
        t_end = _time.perf_counter() + 0.04       # порция ~40 мс, чтобы окно не подвисало
        while todo and _time.perf_counter() < t_end:
            f = todo.pop(0)
            self.frame_image(f, transparent)
            self.time = f                          # курсор бежит по просчитанным кадрам, как в AE
        self.time_changed.emit(self.time)
        self._prerender = (todo, total, transparent, sig)
        self.prerender_progress.emit(total - len(todo), total)
        if todo:
            QTimer.singleShot(0, self._prerender_step)
            return
        self._finish_prerender()

    def set_time(self, f: int):
        f = max(0, min(self.comp.duration - 1, int(f)))
        if f != self.time:
            self.time = f
            self.time_changed.emit(f)

    def step(self, d: int):
        self.set_time(self.time + d)

    def set_tool(self, tool: str):
        if tool != self.tool:
            self.tool = tool
            self.tool_changed.emit(tool)

    def toggle_play(self):
        if self._prerender is not None:
            self.cancel_prerender()
            return
        if self.timer.isActive():
            self.stop()
        else:
            if self.time >= self.comp.work_out - 1 or self.time < self.comp.work_in:
                self.set_time(self.comp.work_in)
            self._start_audio(self.time)
            svc = self._svc()
            if svc is not None:
                svc.clear_queue()      # фоновые задания подождут — сначала кадры для проигрывания
                self._idle_running = False
                self._prefetch()
            self.timer.start(max(5, int(1000 / self.comp.fps / 2)))
            self.playing_changed.emit(True)

    def _start_audio(self, f0):
        self.player.stop()
        if self.audio_preview:
            try:
                self.player.play(self.comp, f0, self.comp.work_out)
            except Exception as e:  # noqa — превью без звука лучше, чем падение
                print("audio:", e)

    def stop(self):
        self.player.stop()
        if self.timer.isActive():
            self.timer.stop()
            self.playing_changed.emit(False)

    def _tick(self):
        if self.player.playing:
            nxt = int(self.player.current_frame())   # время по звуковым часам — звук и картинка не расходятся
            if nxt >= self.comp.work_out:
                self.set_time(self.comp.work_in)
                self._start_audio(self.comp.work_in)
                return
            self.set_time(nxt)
            self._prefetch()
            return
        self._acc = getattr(self, "_acc", 0) + 1
        if self._acc % 2:
            return                     # таймер тикает вдвое чаще кадра
        nxt = self.time + 1
        if nxt >= self.comp.work_out or nxt < self.comp.work_in:
            nxt = self.comp.work_in
        if self._svc() is not None:
            self._prefetch()
            tp = getattr(self, "preview_transparent", False)
            if self.cache.get(self.cache_sig(tp), nxt) is None:
                return                 # без звука ждём готовый кадр: плавно, а не рывками
        self.set_time(nxt)

    def all_key_frames(self, only_selected_layers=True) -> list[int]:
        """Кадры (в кадрах композиции) всех видимых ключей — для J/K."""
        layers = self.selected() if (only_selected_layers and self.sel_layers) else self.comp.layers
        out = set()
        for l in layers:
            for p in l.props.values():
                for k in p.keyframes:
                    out.add(l.start + k.frame)
        return sorted(out)

    # ------------------------------------------------------------------ маркеры композиции и слоя
    def add_marker(self, frame: int | None = None, name: str = "", layer=None, snapshot=True) -> dict:
        """Маркер под курсором (или на кадре frame композиции). layer — маркер слоя: хранится в локальном
        времени слоя и едет вместе с ним, как в AE; иначе маркер композиции. На том же кадре — не дублируем."""
        f = int(self.time if frame is None else frame)
        if layer is not None:
            lst, f = layer.markers, int(round(layer.local(f)))
        else:
            lst, f = self.comp.markers, max(0, min(self.comp.duration, f))
        for m in lst:
            if m["frame"] == f:
                return m
        if snapshot:
            self.snapshot()
        m = {"frame": f, "name": name}
        lst.append(m)
        lst.sort(key=lambda x: x["frame"])
        self.notify()
        return m

    def add_marker_auto(self):
        """Numpad * в AE: выделены слои — маркер на каждом, иначе маркер композиции."""
        sel = self.selected()
        if not sel:
            return [self.add_marker()]
        self.snapshot()                    # одна точка отмены на все слои
        return [self.add_marker(layer=l, snapshot=False) for l in sel]

    def _marker_list(self, m: dict):
        """Список, в котором лежит маркер (композиции или одного из слоёв), и слой-владелец."""
        if any(x is m for x in self.comp.markers):
            return self.comp.markers, None
        for l in self.comp.layers:
            if any(x is m for x in l.markers):
                return l.markers, l
        return None, None

    def marker_comp_frame(self, m: dict) -> int:
        lst, l = self._marker_list(m)
        return m["frame"] + (l.start if l is not None else 0)

    def remove_marker(self, m: dict):
        lst, _ = self._marker_list(m)
        if lst is not None:
            self.snapshot()
            lst.remove(m)
            self.notify()

    def set_marker(self, m: dict, frame: int | None = None, name: str | None = None, snapshot=True):
        """frame — кадр композиции (для маркера слоя пересчитывается в локальный)."""
        lst, l = self._marker_list(m)
        if lst is None:
            return
        if snapshot:
            self.snapshot()
        if frame is not None:
            f = max(0, min(self.comp.duration, int(frame)))
            m["frame"] = f - l.start if l is not None else f
            lst.sort(key=lambda x: x["frame"])
        if name is not None:
            m["name"] = name
        self.notify()

    # ------------------------------------------------------------------ slip
    def slip(self, layer, frames: int, snapshot=True) -> int:
        """Slip-монтаж: клип остаётся на месте (In/Out), исходник под ним сдвигается на frames.
        Сдвиг ограничен длиной исходника. Возвращает реальный сдвиг."""
        end = layer.local_end(self.comp.fps)
        if end is None or not layer.source_frames:    # картинки, солиды, Time Remap — сдвигать нечего
            return 0
        # start ≤ in_point и start + end ≥ out_point: исходник должен покрывать весь клип
        lo, hi = layer.out_point - int(end) - layer.start, layer.in_point - layer.start
        d = max(lo, min(hi, int(frames)))
        if d == 0:
            return 0
        if snapshot:
            self.snapshot()
        layer.start += d
        # ключи — в кадрах композиции на месте (как в AE: анимация клипа не уезжает вместе с исходником)
        for p in layer.props.values():
            for k in p.keyframes:
                k.frame -= d
        self.notify()
        return d

    def goto_key(self, direction: int):
        frames = sorted(set(self.all_key_frames(only_selected_layers=False)) | {m["frame"] for m in self.comp.markers}
                        | {l.start + m["frame"] for l in self.comp.layers for m in l.markers})
        if direction > 0:
            nxt = [f for f in frames if f > self.time]
            if nxt:
                self.set_time(nxt[0])
        else:
            prv = [f for f in frames if f < self.time]
            if prv:
                self.set_time(prv[-1])

    # ------------------------------------------------------------------ выделение
    def select(self, ids: list[str], add=False):
        self.sel_effect = None
        self.sel_prop = None
        locked = {l.id for l in self.comp.layers if l.locked}
        ids = [i for i in ids if i not in locked]
        if add:
            for i in ids:
                if i in self.sel_layers:
                    self.sel_layers.remove(i)
                else:
                    self.sel_layers.append(i)
        else:
            self.sel_layers = list(ids)
        self.selection_changed.emit()

    def select_range(self, lid: str):
        """Shift+клик как в AE: все слои от последнего выделенного (якоря) до lid включительно."""
        ids = [l.id for l in self.comp.layers]
        anchor = self.sel_layers[-1] if self.sel_layers else None
        if anchor not in ids or lid not in ids:
            return self.select([lid])
        a, b = sorted((ids.index(anchor), ids.index(lid)))
        rng = [i for i in ids[a:b + 1] if i != anchor]
        # якорь остаётся последним — следующий Shift+клик тянет диапазон от него же
        self.select([i for i in self.sel_layers if i not in rng and i != anchor] + rng + [anchor])

    def select_all(self):
        self.sel_layers = [l.id for l in self.comp.layers if not l.locked]
        self.selection_changed.emit()

    def deselect(self):
        self.sel_prop = None
        self.sel_layers = []
        self.sel_keys.clear()
        self.selection_changed.emit()

    def select_neighbor(self, d: int):
        layers = self.comp.layers
        if not layers:
            return
        cur = self.current()
        i = layers.index(cur) + d if cur else 0
        i = max(0, min(len(layers) - 1, i))
        self.select([layers[i].id])

    # ------------------------------------------------------------------ медиа и слои
    def import_files(self, paths: list[str]) -> list[str]:
        ok, errors = [], []
        for p in paths:
            p = os.path.abspath(p)
            try:
                self.pool.info(p)
            except Exception as e:
                errors.append(f"{os.path.basename(p)}: {e}")
                continue
            if p not in self.project.media:
                self.project.media.append(p)
            ok.append(p)
        if ok:
            self.dirty = True
            self.media_changed.emit()
        return errors

    def add_media_layer(self, path: str, at: int | None = None) -> Layer:
        info = self.pool.info(path)
        from .proxy import needs_proxy
        if needs_proxy(info):
            self.proxies.request(path, info.width, info.height, info.duration)
        QTimer.singleShot(0, self.warm_audio)
        comp = self.comp
        # Первый ролик в пустом проекте — подстраиваем композицию под него (как «новая композиция из футажа»)
        if info.kind == "video" and not comp.layers and info.width:
            self.snapshot()
            comp.width, comp.height = info.width, info.height
            comp.fps = round(info.fps, 3)
            comp.duration = max(1, int(round(info.duration * comp.fps)))
            comp.work_in, comp.work_out = 0, comp.duration
        else:
            self.snapshot()
        at = self.new_layer_time() if at is None else at
        kind = info.kind
        w, h = (info.width, info.height) if kind != "audio" else (0, 0)
        layer = Layer(kind, info.name, w, h, comp, path)
        layer.has_audio = info.has_audio
        length = max(1, int(round(info.duration * comp.fps)))
        if kind == "image":
            length = comp.duration - at
        layer.start = at
        layer.in_point = at
        layer.out_point = at + length if kind != "image" else comp.duration
        if layer.out_point > comp.duration:  # как в CapCut: композиция растёт под клип
            if comp.work_out == comp.duration:
                comp.work_out = layer.out_point
            comp.duration = layer.out_point
        layer.source_frames = length if kind != "image" else None
        if layer.out_point <= layer.in_point:
            layer.out_point = layer.in_point + 1
        if kind in ("video", "image") and (w > comp.width or h > comp.height):
            s = 100.0 * min(comp.width / w, comp.height / h)
            layer.props["scale"].value = [s, s]
        comp.layers.insert(0, layer)
        self.sel_layers = [layer.id]
        self.notify()
        self.selection_changed.emit()
        return layer

    def add_solid(self, color: str, name: str | None = None, width=None, height=None):
        self.snapshot()
        n = sum(1 for l in self.comp.layers if l.kind == "solid") + 1
        w, h = width or self.comp.width, height or self.comp.height
        layer = Layer("solid", name or tr("Солид {}").format(n), w, h, self.comp)
        layer.color = color
        layer.in_point = layer.start = self.new_layer_time()
        self._insert_new(layer)

    def add_text(self):
        self.snapshot()
        layer = Layer("text", tr("Текст"), 10, 10, self.comp)
        update_text_size(layer)
        layer.props["anchor"].value = [0.0, 0.0]
        layer.in_point = layer.start = self.new_layer_time()
        self._insert_new(layer)

    def add_adjustment(self):
        self.snapshot()
        n = sum(1 for l in self.comp.layers if l.adjustment) + 1
        layer = Layer("solid", tr("Корректирующий слой {}").format(n), self.comp.width, self.comp.height, self.comp)
        layer.adjustment = True
        layer.in_point = layer.start = self.new_layer_time()
        self._insert_new(layer)

    def add_camera(self):
        self.snapshot()
        comp = self.comp
        n = sum(1 for l in comp.layers if l.kind == "camera") + 1
        layer = Layer("camera", tr("Камера {}").format(n), 0, 0, comp)
        z = round(DEFAULT_ZOOM * comp.width / 1920, 1)
        layer.props["position"].value = [comp.width / 2, comp.height / 2, -z]
        layer.props["poi"].value = [comp.width / 2, comp.height / 2, 0.0]
        layer.in_point = layer.start = 0
        comp.layers.insert(0, layer)
        self.sel_layers = [layer.id]
        self.notify()
        self.selection_changed.emit()
        return layer

    def add_light(self, kind: str = "spot") -> Layer:
        """Слой-свет как Layer → New → Light в AE: светит на 3D-слои (Parallel / Spot / Point / Ambient).
        Ставится перед композицией, слегка сверху-слева, и смотрит в её центр."""
        if kind not in ("parallel", "spot", "point", "ambient"):
            kind = "spot"
        self.snapshot()
        comp = self.comp
        n = sum(1 for l in comp.layers if l.kind == "light") + 1
        layer = Layer("light", tr("Свет {}").format(n), 0, 0, comp)
        layer.light["type"] = kind
        layer.props["position"].value = [comp.width * 0.35, comp.height * 0.3, -comp.width * 0.35]
        layer.props["poi"].value = [comp.width / 2, comp.height / 2, 0.0]
        layer.in_point = layer.start = 0
        comp.layers.insert(0, layer)
        self.sel_layers = [layer.id]
        self.notify()
        self.selection_changed.emit()
        return layer

    def set_light_option(self, layer, key: str, value):
        """Тип света, затухание, «Отбрасывает тени» — неанимируемые параметры (Light Settings в AE)."""
        if layer.kind != "light" or layer.light.get(key) == value:
            return
        self.snapshot()
        layer.light[key] = value
        self.notify()

    def set_material_option(self, layers, key: str, value: bool):
        """Accepts Lights / Casts Shadows / Accepts Shadows у 3D-слоёв (Material Options в AE)."""
        layers = [l for l in layers if l.threed and isinstance(getattr(l, "material", None), dict)]
        if not layers:
            return
        self.snapshot()
        for l in layers:
            l.material[key] = bool(value)
        self.notify()

    def add_shape_layer(self) -> Layer:
        comp = self.comp
        n = sum(1 for l in comp.layers if l.kind == "shape") + 1
        layer = Layer("shape", tr("Шейп-слой {}").format(n), comp.width, comp.height, comp)
        layer.props["anchor"].value = [0.0, 0.0]
        layer.props["position"].value = [0.0, 0.0]
        layer.in_point = layer.start = self.new_layer_time()
        self._insert_new(layer, notify=False)
        return layer

    # ---- операторы и группа фигуры (Contents → Add в AE)
    def add_shape_op(self, layer: Layer, sid: str, op_type: str) -> dict | None:
        from .shape_ops import OPS, op_key
        sh = layer.shape_by_id(sid)
        if sh is None or op_type not in OPS:
            return None
        self.snapshot()
        oid = uuid.uuid4().hex[:5]
        op = {"id": oid, "type": op_type, "on": True}
        sh.setdefault("ops", []).append(op)
        for prm, pd in OPS[op_type][1].items():
            k = op_key(sid, oid, prm)
            layer.props[k] = Property(k, list(pd[2]))
        layer.refresh_meta()
        self.notify()
        return op

    def remove_shape_op(self, layer: Layer, sid: str, oid: str):
        from .shape_ops import OPS, op_key
        sh = layer.shape_by_id(sid)
        op = next((o for o in (sh or {}).get("ops", []) if o["id"] == oid), None)
        if op is None:
            return
        self.snapshot()
        sh["ops"].remove(op)
        for prm in OPS.get(op["type"], (None, {}))[1]:
            layer.props.pop(op_key(sid, oid, prm), None)
        self.sel_keys = {t for t in self.sel_keys if not (t[0] == layer.id and f"op_{oid}_" in t[1])}
        self.notify()

    def move_shape_op(self, layer: Layer, sid: str, oid: str, d: int):
        sh = layer.shape_by_id(sid)
        ops = (sh or {}).get("ops", [])
        i = next((k for k, o in enumerate(ops) if o["id"] == oid), None)
        if i is None:
            return
        j = max(0, min(len(ops) - 1, i + d))
        if i != j:
            self.snapshot()
            ops.insert(j, ops.pop(i))
            self.notify()

    def toggle_shape_op(self, layer: Layer, sid: str, oid: str):
        sh = layer.shape_by_id(sid)
        op = next((o for o in (sh or {}).get("ops", []) if o["id"] == oid), None)
        if op is not None:
            self.snapshot()
            op["on"] = not op.get("on", True)
            self.notify()

    def enable_shape_group(self, layer: Layer, sid: str, what: str):
        """Показать (создать) свойства группы фигуры: transform | fill_grad | stroke_grad | dash."""
        from .shape_ops import GROUP_PROPS
        sh = layer.shape_by_id(sid)
        if sh is None:
            return
        keys = {"transform": ["tr_anchor", "tr_pos", "tr_scale", "tr_rot", "tr_opacity"],
                "fill_grad": ["g_start", "g_end"], "stroke_grad": ["sg_start", "sg_end"],
                "dash": ["dash", "gap", "dash_offset"], "stroke_opacity": ["stroke_opacity"]}[what]
        self.snapshot()
        for k in keys:
            pk = f"shape/{sid}/{k}"
            if pk not in layer.props:
                default = list(GROUP_PROPS[k][2])
                if k in ("g_start", "g_end", "sg_start", "sg_end"):   # градиент поперёк фигуры
                    from .shapes import shape_path
                    br = shape_path(layer, sh, layer.local(self.time)).boundingRect()
                    default = [br.left(), br.center().y()] if k.endswith("start") else [br.right(), br.center().y()]
                if k == "dash":
                    default = [20.0]
                layer.props[pk] = Property(pk, default)
        if what == "fill_grad":
            sh["fill_type"] = "linear" if sh.get("fill_type", "solid") == "solid" else sh["fill_type"]
            sh.setdefault("fill_color2", "#000000")
            sh["fill"] = True
        if what == "stroke_grad":
            sh["stroke_type"] = "linear" if sh.get("stroke_type", "solid") == "solid" else sh["stroke_type"]
            sh.setdefault("stroke_color2", "#000000")
            sh["stroke"] = True
        layer.refresh_meta()
        self.notify()

    def set_shape_attr(self, layer: Layer, sid: str, attr: str, value):
        sh = layer.shape_by_id(sid)
        if sh is not None:
            self.snapshot()
            sh[attr] = value
            self.notify()

    def add_shape(self, layer: Layer, kind: str, rect=None, path_vals=None, closed=True, fill="#4a90d9"):
        """Фигура в шейп-слой: rect/ellipse/star/polygon по прямоугольнику, path — по вершинам пера."""
        sid = uuid.uuid4().hex[:6]
        names = {"rect": tr("Прямоугольник"), "ellipse": tr("Эллипс"), "star": tr("Звезда"),
                 "polygon": tr("Многоугольник"), "path": tr("Контур")}
        n = sum(1 for s in layer.shapes if s["type"] == kind) + 1
        sh = {"id": sid, "name": f"{names[kind]} {n}", "type": kind, "fill": closed, "fill_color": fill,
              "stroke": not closed, "stroke_color": "#ffffff", "closed": closed, "visible": True}
        P = lambda k, v: layer.props.__setitem__(f"shape/{sid}/{k}", Property(f"shape/{sid}/{k}", v))  # noqa: E731
        if kind == "path":
            P("path", list(path_vals))
        else:
            x, y, w, h = rect
            P("position", [x + w / 2, y + h / 2])
            if kind in ("rect", "ellipse"):
                P("size", [abs(w), abs(h)])
                if kind == "rect":
                    P("roundness", [0.0])
            else:
                r = max(abs(w), abs(h)) / 2
                P("points", [5.0 if kind == "star" else 6.0])
                P("outer", [r])
                if kind == "star":
                    P("inner", [r * 0.45])
                P("srot", [0.0])
        P("stroke_width", [4.0])
        P("fill_opacity", [100.0])
        layer.shapes.insert(0, sh)
        layer.refresh_meta()
        return sh

    def add_mask(self, layer: Layer, kind: str, rect=None, path_vals=None, closed=True):
        """Маска на слой (координаты — в системе слоя)."""
        mid = uuid.uuid4().hex[:6]
        n = len(layer.masks) + 1
        if path_vals is None:
            x, y, w, h = rect
            path_vals = ellipse_vals(x + w / 2, y + h / 2, abs(w) / 2, abs(h) / 2) if kind == "ellipse" \
                else rect_vals(min(x, x + w), min(y, y + h), abs(w), abs(h))
        layer.masks.append({"id": mid, "name": tr("Маска {}").format(n), "mode": "add", "inverted": False,
                            "closed": closed})
        layer.props[f"mask/{mid}/path"] = Property(f"mask/{mid}/path", list(path_vals))
        for k, v in (("feather", 0.0), ("opacity", 100.0), ("expansion", 0.0)):
            layer.props[f"mask/{mid}/{k}"] = Property(f"mask/{mid}/{k}", [v])
        layer.refresh_meta()
        return layer.masks[-1]

    def remove_mask(self, layer: Layer, mid: str):
        m = layer.mask_by_id(mid)
        if not m:
            return
        self.snapshot()
        layer.masks.remove(m)
        for k in MASK_PROPS:
            layer.props.pop(f"mask/{mid}/{k}", None)
        self.notify()

    def remove_shape(self, layer: Layer, sid: str):
        sh = layer.shape_by_id(sid)
        if not sh:
            return
        self.snapshot()
        layer.shapes.remove(sh)
        for k in [k for k in layer.props if k.startswith(f"shape/{sid}/")]:
            layer.props.pop(k)
        self.notify()

    def set_3d(self, layers, on: bool | None = None):
        layers = [l for l in layers if l.kind not in ("audio", "camera", "light")]
        if not layers:
            return
        self.snapshot()
        val = (not all(l.threed for l in layers)) if on is None else on
        for l in layers:
            l.threed = val
            l.refresh_meta()
        self.sel_keys = {t for t in self.sel_keys if self.comp.layer_by_id(t[0]) and
                         t[1] in self.comp.layer_by_id(t[0]).props}
        self.notify()

    def _insert_new(self, layer: Layer, notify: bool = True):
        cur = self.current()
        idx = self.comp.layers.index(cur) if cur else 0
        self.comp.layers.insert(idx, layer)
        self.sel_layers = [layer.id]
        if notify:
            self.notify()
            self.selection_changed.emit()

    def delete_selection(self):
        if self.sel_effect:  # выделен эффект — удаляем именно его, а не ключи и не слой
            l = self.comp.layer_by_id(self.sel_effect[0])
            fid = self.sel_effect[1]
            self.sel_effect = None
            if l is not None and l.effect_by_id(fid):
                self.remove_effect(l, fid)
                return
        if self.sel_keys:
            self.snapshot()
            for lid, key, fr in list(self.sel_keys):
                l = self.comp.layer_by_id(lid)
                if l and key in l.props:
                    p = l.props[key]
                    k = p.keyframe_at(fr)
                    if k:
                        if len(p.keyframes) == 1:
                            p.value = list(k.value)
                        p.keyframes.remove(k)
            self.sel_keys.clear()
            self.notify()
            return
        if self.sel_layers:
            self.snapshot()
            for l in self.comp.layers:
                if l.parent in self.sel_layers and l.id not in self.sel_layers:
                    self._reparent(l, None)
            self.comp.layers = [l for l in self.comp.layers if l.id not in self.sel_layers]
            self.sel_layers = []
            self.notify()
            self.selection_changed.emit()

    # ------------------------------------------------------------------ Ctrl+C / Ctrl+X / Ctrl+V (как в AE)
    def copy_selection(self) -> str | None:
        """Копирует то, что выделено: эффект → ключи → слои. Возвращает, что скопировано."""
        if self.sel_effect:
            l = self.comp.layer_by_id(self.sel_effect[0])
            fx = l.effect_by_id(self.sel_effect[1]) if l else None
            if fx:
                self.clipboard = ("effects", [(copy.deepcopy(fx),
                                               {k: l.props[k].to_dict() for k in l.effect_keys(fx) if k in l.props})])
                return "effect"
        if not self.sel_keys and self.sel_prop:
            l = self.comp.layer_by_id(self.sel_prop[0])
            key = self.sel_prop[1]
            if l and key in l.props:   # как в AE: выделил свойство (Scale) → Ctrl+C копирует его значение
                self.clipboard = ("value", (key, l.props[key].value_at(l.local(self.time))),
                                  self._fx_meta(l, [key]))
                return "value"
        if self.sel_keys:
            items, t0 = [], None
            for lid, key, fr in sorted(self.sel_keys):
                l = self.comp.layer_by_id(lid)
                k = l.props[key].keyframe_at(fr) if l and key in l.props else None
                if k is not None:
                    comp_f = l.start + k.frame
                    t0 = comp_f if t0 is None else min(t0, comp_f)
                    items.append((key, comp_f, asdict(k)))
            if items:   # время ключей — от самого раннего, вставка от текущего кадра
                meta = {}
                for lid, key, fr in self.sel_keys:
                    l = self.comp.layer_by_id(lid)
                    if l:
                        meta.update(self._fx_meta(l, [key]))
                self.clipboard = ("keys", [(key, cf - t0, kd) for key, cf, kd in items], meta)
                return "keys"
        sel = self.selected()
        if sel:
            self.clipboard = ("layers", [l.to_dict() for l in sel])
            return "layers"
        return None

    @staticmethod
    def _fx_meta(layer, keys) -> dict:
        """Для свойств эффектов запоминаем тип и имя эффекта — чтобы вставить в такой же эффект другого слоя."""
        out = {}
        for key in keys:
            if key.startswith("fx/"):
                fx = layer.effect_by_id(key.split("/")[1])
                if fx:
                    out[key] = (fx["type"], fx.get("name", ""))
        return out

    def _map_key(self, layer, key, meta, dry=False):
        """Ключ свойства на целевом слое. Для эффекта: такой же эффект по имени, потом по типу;
        если такого эффекта нет — добавляем его (как в AE при вставке параметра эффекта)."""
        if not key.startswith("fx/"):
            return key if key in layer.props else None
        if key in layer.props:
            return key
        info = meta.get(key)
        if not info:
            return None
        fx_type, name = info
        param = key.split("/", 2)[2]
        cands = [fx for fx in layer.effects if fx["type"] == fx_type]
        fx = next((f for f in cands if f.get("name") == name), cands[0] if cands else None)
        if fx is None:
            if not effect_for_layer(fx_type, layer.kind):
                return None
            if dry:
                return "new"
            fx = self._new_effect(layer, fx_type)
        k = prop_key(fx["id"], param)
        return k if k in layer.props else None

    def cut_selection(self) -> str | None:
        what = self.copy_selection()
        if what:
            self.delete_selection()
        return what

    def paste(self) -> int:
        """Вставка. Эффекты — на все выделенные слои (в конец списка эффектов), ключи — от текущего кадра
        в те же свойства выделенных слоёв, слои — копиями над текущим. Возвращает, сколько вставлено."""
        clip = getattr(self, "clipboard", None)
        if not clip:
            return 0
        kind, data = clip[0], clip[1]
        meta = clip[2] if len(clip) > 2 else {}
        if kind == "value":
            key, val = data
            targets = [l for l in self.selected() if self._map_key(l, key, meta, dry=True)]
            if not targets:
                return 0
            self.snapshot()
            for l in targets:
                k = self._map_key(l, key, meta)
                l.props[k].set_value_at(l.local(self.time), list(val))
            self.notify()
            return len(targets)
        if kind == "effects":
            targets = self.selected()
            if not targets and self.sel_effect:
                l = self.comp.layer_by_id(self.sel_effect[0])
                targets = [l] if l else []
            n = 0
            self.snapshot()
            for l in targets:
                for fx, props in data:
                    if not effect_for_layer(fx["type"], l.kind):
                        continue
                    nid = uuid.uuid4().hex[:6]
                    nfx = copy.deepcopy(fx)
                    old = fx["id"]
                    nfx["id"] = nid
                    names = {f.get("name") for f in l.effects}
                    if nfx.get("name") in names:   # имя эффекта используется в выражениях — делаем уникальным
                        base, i = nfx["name"], 2
                        while f"{base} {i}" in names:
                            i += 1
                        nfx["name"] = f"{base} {i}"
                    first = min((kd["frame"] for pd in props.values() for kd in pd.get("keyframes", [])),
                                default=None)
                    shift = 0 if first is None else int(l.local(self.time)) - first
                    for k, pd in props.items():
                        nk = k.replace(f"fx/{old}/", f"fx/{nid}/")
                        np_ = Property.from_dict(nk, copy.deepcopy(pd))
                        for kf in np_.keyframes:   # как в AE: ключи эффекта начинаются с текущего кадра
                            kf.frame += shift
                        l.props[nk] = np_
                    l.effects.append(nfx)
                    l.refresh_meta()
                    self.sel_effect = (l.id, nid)
                    n += 1
            self.notify()
            return n
        if kind == "keys":
            targets = self.selected()
            if not targets:
                return 0
            self.snapshot()
            n = 0
            new_sel = set()
            for l in targets:
                for key, rel, kd in data:
                    k2 = self._map_key(l, key, meta)
                    p = l.props.get(k2) if k2 else None
                    if p is None:
                        continue
                    key = k2
                    fr = int(self.time + rel - l.start)
                    old = p.keyframe_at(fr)
                    if old is not None:
                        p.keyframes.remove(old)
                    nk = Keyframe(**copy.deepcopy(kd))
                    nk.frame = fr
                    p.keyframes.append(nk)
                    p.sort()
                    new_sel.add((l.id, key, fr))
                    n += 1
            self.sel_keys = new_sel
            self.notify()
            return n
        if kind == "layers":
            self.snapshot()
            cur = self.current()
            idx = self.comp.layers.index(cur) if cur else 0
            new_ids = []
            for d in reversed(data):
                nl = Layer.from_dict(copy.deepcopy(d), self.comp)
                nl.id = uuid.uuid4().hex[:12]
                if nl.parent and not self.comp.layer_by_id(nl.parent):
                    nl.parent = None
                nl.refresh_meta()
                self.comp.layers.insert(idx, nl)
                new_ids.insert(0, nl.id)
            self.sel_layers, self.sel_keys = new_ids, set()
            self.notify()
            self.selection_changed.emit()
            return len(new_ids)
        return 0

    def duplicate(self):
        sel = self.selected()
        if not sel:
            return
        self.snapshot()
        new_ids = []
        for l in sel:
            nl = Layer.from_dict(copy.deepcopy(l.to_dict()), self.comp)
            nl.id = uuid.uuid4().hex[:12]
            self.comp.layers.insert(self.comp.layers.index(l), nl)
            new_ids.append(nl.id)
        self.sel_layers = new_ids
        self.notify()
        self.selection_changed.emit()

    def split(self):
        """Ctrl+Shift+D — разрезать выделенные (или все под курсором) слои по CTI."""
        t = self.time
        targets = [l for l in (self.selected() or self.comp.layers) if l.in_point < t < l.out_point]
        if not targets:
            return
        self.snapshot()
        new_ids = []
        for l in targets:
            nl = Layer.from_dict(copy.deepcopy(l.to_dict()), self.comp)
            nl.id = uuid.uuid4().hex[:12]
            l.out_point = t
            nl.in_point = t
            l.markers = [m for m in l.markers if l.start + m["frame"] < t]       # маркеры — своей половине
            nl.markers = [m for m in nl.markers if nl.start + m["frame"] >= t]
            self.comp.layers.insert(self.comp.layers.index(l), nl)
            new_ids.append(nl.id)
        self.sel_layers = new_ids
        self.notify()
        self.selection_changed.emit()

    def lift_work_area(self, extract: bool = False) -> bool:
        """Правка → Lift / Extract Work Area (как в AE): вырезать рабочую область из выделенных слоёв
        (ничего не выделено — из всех незаблокированных). Слой, пересекающий область, делится на две части.
        extract — ещё и сдвинуть всё, что после области, влево на её длину (без дыры)."""
        a, b = self.comp.work_in, self.comp.work_out
        d = b - a
        targets = [l for l in (self.selected() or self.comp.layers) if not l.locked]
        hit = [l for l in targets if l.in_point < b and l.out_point > a]
        if d <= 0 or not hit and not (extract and any(l.in_point >= b for l in targets)):
            return False
        self.snapshot()
        gone = set()
        for l in hit:
            if l.in_point >= a and l.out_point <= b:          # целиком внутри — удалить
                gone.add(l.id)
            elif l.in_point < a and l.out_point > b:          # насквозь — вторая половина копией
                nl = Layer.from_dict(copy.deepcopy(l.to_dict()), self.comp)
                nl.id = uuid.uuid4().hex[:12]
                nl.in_point = b
                l.out_point = a
                l.markers = [m for m in l.markers if l.start + m["frame"] < a]
                nl.markers = [m for m in nl.markers if nl.start + m["frame"] >= b]
                self.comp.layers.insert(self.comp.layers.index(l), nl)
                targets.append(nl)
            elif l.in_point < a:
                l.out_point = a
            else:
                l.in_point = b
        for l in self.comp.layers:
            if l.parent in gone:
                self._reparent(l, None)
        self.comp.layers = [l for l in self.comp.layers if l.id not in gone]
        if extract:
            for l in targets:
                if l.id not in gone and l.in_point >= b:
                    l.start -= d                               # ключи в локальном времени едут вместе со start
                    l.in_point -= d
                    l.out_point -= d
        self.sel_layers = [i for i in self.sel_layers if i not in gone]
        self.notify()
        self.selection_changed.emit()
        return True

    def ripple_delete(self) -> bool:
        """Shift+Delete — удалить выделенные слои и сдвинуть влево слои, начинающиеся после них
        (как Ripple Delete в Premiere). Слои, перекрывающие удалённый промежуток, не двигаются."""
        sel = [l for l in self.selected() if not l.locked]
        if not sel:
            return False
        a, b = min(l.in_point for l in sel), max(l.out_point for l in sel)
        ids = {l.id for l in sel}
        self.snapshot()
        for l in self.comp.layers:
            if l.parent in ids and l.id not in ids:
                self._reparent(l, None)
        self.comp.layers = [l for l in self.comp.layers if l.id not in ids]
        for l in self.comp.layers:
            if l.in_point >= b and not l.locked:
                l.start -= b - a
                l.in_point -= b - a
                l.out_point -= b - a
        self.sel_layers = []
        self.notify()
        self.selection_changed.emit()
        return True

    def move_layers_to_time(self, edge: str):
        """[ / ] — сдвинуть слой так, чтобы его начало/конец оказались на CTI."""
        sel = self.selected()
        if not sel:
            return
        self.snapshot()
        for l in sel:
            d = (self.time - l.in_point) if edge == "in" else (self.time + 1 - l.out_point)
            l.start += d
            l.in_point += d
            l.out_point += d
        self.notify()

    def trim(self, edge: str):
        """Alt+[ / Alt+] — обрезать начало/конец слоя по CTI."""
        sel = self.selected()
        if not sel:
            return
        self.snapshot()
        t = self.time
        for l in sel:
            if edge == "in" and t < l.out_point:
                l.in_point = t
            elif edge == "out" and t > l.in_point:
                l.out_point = t + 1
        self.notify()

    def reorder(self, d: int):
        """Ctrl+] вверх, Ctrl+[ вниз."""
        sel = self.selected()
        if not sel:
            return
        self.snapshot()
        layers = self.comp.layers
        order = sorted(sel, key=layers.index, reverse=(d > 0))
        for l in order:
            i = layers.index(l)
            j = max(0, min(len(layers) - 1, i - d))
            layers.insert(j, layers.pop(i))
        self.notify()

    def center_anchor(self):
        """Ctrl+Alt+Home: опорная точка в центр содержимого слоя (картинка не сдвигается)."""
        sel = [l for l in self.selected() if "anchor" in l.props and l.kind not in ("camera", "light", "audio", "null")]
        if not sel:
            return
        from .render import layer_transform
        self.snapshot()
        for l in sel:
            x, y, w, h = l.local_rect()
            f = l.local(self.time)
            a_new = [x + w / 2, y + h / 2]
            tr_ = layer_transform(l, self.time, self.comp)
            p_old = tr_.map(QPointF(*l.props["anchor"].value_at(f)[:2]))
            p_new = tr_.map(QPointF(*a_new))
            l.props["anchor"].set_value_at(f, a_new)
            pos = l.props["position"].value_at(f)
            pos[0] += p_new.x() - p_old.x()
            pos[1] += p_new.y() - p_old.y()
            l.props["position"].set_value_at(f, pos)
        self.notify()

    def center_layer(self):
        """Ctrl+Home: слой в центр композиции."""
        sel = [l for l in self.selected() if "position" in l.props and l.kind != "audio"]
        if not sel:
            return
        self.snapshot()
        for l in sel:
            f = l.local(self.time)
            pos = l.props["position"].value_at(f)
            pos[0], pos[1] = self.comp.width / 2, self.comp.height / 2
            l.props["position"].set_value_at(f, pos)
        self.notify()

    def work_area_to_selection(self):
        """Ctrl+Alt+B: рабочая область по выделенным слоям."""
        sel = self.selected()
        if not sel:
            return
        self.snapshot()
        self.comp.work_in = max(0, min(l.in_point for l in sel))
        self.comp.work_out = min(self.comp.duration, max(l.out_point for l in sel))
        self.notify()

    def fit_to_comp(self):
        sel = self.selected()
        if not sel:
            return
        self.snapshot()
        for l in sel:
            if l.width <= 0 or l.height <= 0:
                continue
            s = 100.0 * min(self.comp.width / l.width, self.comp.height / l.height)
            x, y, w, h = l.local_rect()
            f = l.local(self.time)
            l.props["anchor"].set_value_at(f, [x + w / 2, y + h / 2])
            l.props["position"].set_value_at(f, [self.comp.width / 2, self.comp.height / 2])
            l.props["scale"].set_value_at(f, [s, s])
        self.notify()

    def set_work(self, edge: str):
        """B / N — начало и конец рабочей области; "all" — вся композиция (двойной клик по полосе, как в AE)."""
        self.snapshot()
        if edge == "all":
            self.comp.work_in, self.comp.work_out = 0, self.comp.duration
        elif edge == "in":
            self.comp.work_in = min(self.time, self.comp.work_out - 1)
        else:
            self.comp.work_out = max(self.time + 1, self.comp.work_in + 1)
        self.notify()

    # ------------------------------------------------------------------ ключи
    def add_keyframes(self, key: str):
        """Alt+Shift+P/S/R/T/A — поставить ключ на текущем кадре."""
        sel = self.selected()
        if not sel:
            return
        self.snapshot()
        for l in sel:
            p = l.props[key]
            if not p.animated:
                p.toggle_stopwatch(l.local(self.time))
            else:
                p.add_keyframe(l.local(self.time))
        self.notify()

    def _target_keys(self):
        for lid, key, fr in self.sel_keys:
            l = self.comp.layer_by_id(lid)
            if l and key in l.props:
                k = l.props[key].keyframe_at(fr)
                if k:
                    yield k

    def set_ease(self, mode: str):
        keys = list(self._target_keys())
        if not keys:
            return
        self.snapshot()
        for k in keys:
            if mode in ("ease", "in"):
                k.in_x, k.in_y = EASE
            if mode in ("ease", "out"):
                k.out_x, k.out_y = EASE
            if mode == "linear":
                k.in_x, k.in_y = LINEAR
                k.out_x, k.out_y = LINEAR
            if mode == "hold":
                k.hold = not k.hold
            elif mode != "hold":
                k.hold = False
        self.notify()

    def curve_segments(self):
        """Сегменты для Flow: пары соседних ключей, где оба выделены.
        Если таких нет — исходящий сегмент каждого выделенного ключа."""
        by_prop: dict[tuple[str, str], set[int]] = {}
        for lid, key, fr in self.sel_keys:
            by_prop.setdefault((lid, key), set()).add(fr)
        segs = []
        for (lid, key), frames in sorted(by_prop.items()):
            l = self.comp.layer_by_id(lid)
            if not l or key not in l.props:
                continue
            ks = l.props[key].keyframes
            idx = [i for i, k in enumerate(ks) if k.frame in frames]
            pairs = [(ks[i], ks[i + 1]) for i in idx if i + 1 < len(ks) and ks[i + 1].frame in frames]
            if not pairs:
                pairs = [(ks[i], ks[i + 1]) for i in idx if i + 1 < len(ks)]
            segs += pairs
        return segs

    def apply_curve(self, c) -> int:
        """Flow: задать сегментам кривую cubic-bezier(x1, y1, x2, y2)."""
        segs = self.curve_segments()
        if not segs:
            return 0
        x1, y1, x2, y2 = (float(v) for v in c)
        self.snapshot()
        for k0, k1 in segs:
            k0.hold = False
            k0.out_x, k0.out_y = x1, y1
            k1.in_x, k1.in_y = 1.0 - x2, 1.0 - y2
        self.notify()
        return len(segs)

    def read_curve(self):
        segs = self.curve_segments()
        if not segs:
            return None
        k0, k1 = segs[0]
        return (k0.out_x, k0.out_y, 1.0 - k1.in_x, 1.0 - k1.in_y)

    def select_all_keys(self):
        self.sel_keys = {(l.id, key, k.frame)
                         for l in (self.selected() or self.comp.layers)
                         for key, p in l.props.items() for k in p.keyframes}
        self.changed.emit()

    # ------------------------------------------------------------------ эффекты
    def add_effect(self, fx_type: str, layers=None):
        layers = layers if layers is not None else self.selected()
        layers = [l for l in layers if effect_for_layer(fx_type, l.kind)]
        d = EFFECTS.get(fx_type)
        if not layers or not d:
            return False
        self.snapshot()
        for l in layers:
            self._new_effect(l, fx_type)
        self.notify()
        return True

    def _new_effect(self, l, fx_type: str) -> dict:
        """Добавить эффект на слой (без шага отмены — его делает вызывающий)."""
        d = EFFECTS[fx_type]
        fid = uuid.uuid4().hex[:6]
        names = {fx.get("name") for fx in l.effects}
        name, n = d.label, 1
        while name in names:
            n += 1
            name = f"{d.label} {n}"
        fx = {"id": fid, "type": fx_type, "on": True, "opts": dict(d.opts), "name": name}
        l.effects.append(fx)
        for prm in d.params:
            l.props[prop_key(fid, prm.key)] = Property(prop_key(fid, prm.key), self._param_default(prm))
        l.refresh_meta()
        return fx

    def start_color_pick(self, layer_id: str, key: str):
        self.color_pick = (layer_id, key)
        self.color_pick_changed.emit(True)

    def cancel_color_pick(self):
        if self.color_pick:
            self.color_pick = None
            self.color_pick_changed.emit(False)

    def pick_color_at(self, x: float, y: float) -> list[int] | None:
        """Пипетка: цвет кадра в точке композиции (x, y), усреднение 5×5.
        Эффект, которому берём цвет (например Keylight), и все эффекты после него на время замера
        выключаются — иначе фон уже вырезан и брать нечего. Так же работает пипетка в AE."""
        from .effects import to_array
        from .render import render_frame
        if not self.color_pick:
            return None
        lid, key = self.color_pick
        l = self.comp.layer_by_id(lid)
        self.cancel_color_pick()
        if l is None or key not in l.props:
            return None
        off = []   # этот эффект и все после него — берём цвет на ВХОДЕ эффекта
        if key.startswith("fx/"):
            fid = key.split("/")[1]
            ids = [f["id"] for f in l.effects]
            if fid in ids:
                off = l.effects[ids.index(fid):]
        saved = [f.get("on", True) for f in off]
        for f in off:
            f["on"] = False
        try:
            img = render_frame(self.comp, self.time, self.pool, self.preview_scale)
        finally:
            for f, on in zip(off, saved):
                f["on"] = on
        arr = to_array(img)
        h, w = arr.shape[:2]
        px = int(max(0, min(w - 1, x * self.preview_scale)))
        py = int(max(0, min(h - 1, y * self.preview_scale)))
        patch = arr[max(0, py - 2):py + 3, max(0, px - 2):px + 3].reshape(-1, 4).mean(axis=0)
        a = max(1.0, patch[3])
        rgb = [int(round(min(255, patch[i] / a * 255))) for i in (2, 1, 0)]
        self.snapshot()
        l.props[key].set_value_at(l.local(self.time), rgb)
        self.notify()
        return rgb

    def _param_default(self, prm) -> list[float]:
        """Точки: значения ≤ 1 — доли размера композиции (начало/конец градиента), иначе центр."""
        if prm.kind != "point":
            return list(prm.default)
        if all(0.0 <= v <= 1.0 for v in prm.default):
            return [self.comp.width * prm.default[0], self.comp.height * prm.default[1]]
        return [self.comp.width / 2, self.comp.height / 2]

    def reset_effect(self, layer: Layer, fid: str):
        """Reset у эффекта — все параметры по умолчанию, ключи и выражения убираются."""
        fx = layer.effect_by_id(fid)
        d = EFFECTS.get(fx["type"]) if fx else None
        if not d:
            return
        self.snapshot()
        for prm in d.params:
            p = layer.props.get(prop_key(fid, prm.key))
            if p:
                p.keyframes, p.expr, p.expr_on = [], "", False
                p.value = self._param_default(prm)
        fx["opts"] = dict(d.opts)
        self.notify()

    def prop_default(self, layer: Layer, key: str) -> list[float] | None:
        """Значение свойства «по умолчанию» (для Reset одного свойства, как правой кнопкой в AE)."""
        from .effects import find_param
        if key.startswith("fx/"):
            fp = find_param(layer, key)
            return self._param_default(fp[2]) if fp else None
        x, y, w, h = layer.local_rect()
        comp = self.comp
        vals = {"anchor": [x + w / 2, y + h / 2], "position": [comp.width / 2, comp.height / 2],
                "scale": [100.0, 100.0], "rotation": [0.0], "opacity": [100.0], "orientation": [0.0, 0.0, 0.0],
                "rot_x": [0.0], "rot_y": [0.0], "speed": [100.0], "audio_levels": [0.0], "text_reveal": [100.0]}
        if layer.kind == "shape" and key in ("anchor", "position"):
            return [0.0, 0.0]
        v = vals.get(key)
        if v is None:
            return None
        cur = layer.props[key].value
        return (v + cur[len(v):])[:len(cur)] if len(cur) > len(v) else v[:len(cur)]

    def reset_prop(self, layer: Layer, key: str):
        """Сбросить одно свойство: ключи и выражение убираются, значение — по умолчанию."""
        d = self.prop_default(layer, key)
        p = layer.props.get(key)
        if d is None or p is None:
            return
        self.snapshot()
        p.keyframes, p.expr, p.expr_on = [], "", False
        p.value = list(d)
        self.sel_keys = {t for t in self.sel_keys if not (t[0] == layer.id and t[1] == key)}
        self.notify()

    def time_reverse_keys(self):
        """Keyframe Assistant → Time-Reverse Keyframes: выделенные ключи меняются местами во времени
        (первый становится последним), как в AE."""
        groups: dict = {}
        for lid, key, fr in self.sel_keys:
            groups.setdefault((lid, key), []).append(fr)
        groups = {k: v for k, v in groups.items() if len(v) >= 2}
        if not groups:
            return 0
        self.snapshot()
        new_sel = set(self.sel_keys)
        for (lid, key), frames in groups.items():
            l = self.comp.layer_by_id(lid)
            p = l.props[key] if l else None
            if p is None:
                continue
            keys = [p.keyframe_at(f) for f in sorted(frames)]
            keys = [k for k in keys if k]
            a, b = keys[0].frame, keys[-1].frame
            for k in keys:
                k.frame = a + b - k.frame
                k.in_x, k.out_x = k.out_x, k.in_x      # ручки тоже разворачиваются
                k.in_y, k.out_y = k.out_y, k.in_y
            p.sort()
        self.notify()
        return len(groups)

    def move_layers_to(self, layers: list, index: int):
        """Перетаскивание слоёв в списке таймлайна: вставить выделенные перед строкой index."""
        all_l = self.comp.layers
        moving = [l for l in all_l if l in layers]
        if not moving:
            return
        before = all_l[index] if 0 <= index < len(all_l) else None
        while before is not None and before in moving:
            i = all_l.index(before) + 1
            before = all_l[i] if i < len(all_l) else None
        rest = [l for l in all_l if l not in moving]
        pos = rest.index(before) if before is not None else len(rest)
        new = rest[:pos] + moving + rest[pos:]
        if [l.id for l in new] == [l.id for l in all_l]:
            return
        self.snapshot()
        self.comp.layers[:] = new
        self.notify()

    def reset_transform(self, layer: Layer):
        """Reset у группы «Трансформирование»."""
        self.snapshot()
        x, y, w, h = layer.local_rect()
        comp = self.comp
        vals = {"anchor": [x + w / 2, y + h / 2], "position": [comp.width / 2, comp.height / 2],
                "scale": [100.0, 100.0], "rotation": [0.0], "opacity": [100.0],
                "orientation": [0.0, 0.0, 0.0], "rot_x": [0.0], "rot_y": [0.0]}
        if layer.kind == "shape":
            vals["anchor"] = [0.0, 0.0]
            vals["position"] = [0.0, 0.0]
        if layer.kind == "light":
            vals = {"position": [comp.width * 0.35, comp.height * 0.3, -comp.width * 0.35],
                    "poi": [comp.width / 2, comp.height / 2, 0.0]}
        if layer.kind == "camera":
            z = DEFAULT_ZOOM * comp.width / 1920
            vals = {"position": [comp.width / 2, comp.height / 2, -z], "poi": [comp.width / 2, comp.height / 2, 0.0],
                    "rotation": [0.0]}
        for key in layer.transform_keys():
            p = layer.props.get(key)
            if p and key in vals:
                p.keyframes, p.expr, p.expr_on = [], "", False
                v = list(vals[key])
                p.value = (v + [0.0] * p.dims)[:p.dims]
        self.notify()

    def remove_effect(self, layer: Layer, fid: str):
        fx = layer.effect_by_id(fid)
        if not fx:
            return
        self.snapshot()
        for key in layer.effect_keys(fx):
            layer.props.pop(key, None)
        layer.effects.remove(fx)
        self.sel_keys = {t for t in self.sel_keys if not (t[0] == layer.id and t[1].startswith(f"fx/{fid}/"))}
        self.notify()

    def clear_effects(self):
        sel = [l for l in self.selected() if l.effects]
        if not sel:
            return
        self.snapshot()
        for l in sel:
            for fx in list(l.effects):
                for key in l.effect_keys(fx):
                    l.props.pop(key, None)
            l.effects = []
        self.sel_keys = {t for t in self.sel_keys if not t[1].startswith("fx/")}
        self.notify()

    def duplicate_effect(self, layer: Layer, fid: str):
        fx = layer.effect_by_id(fid)
        if not fx:
            return
        self.snapshot()
        nid = uuid.uuid4().hex[:6]
        nfx = copy.deepcopy(fx)
        nfx["id"] = nid
        nfx["name"] = (fx.get("name") or EFFECTS[fx["type"]].label) + " 2"
        for k in layer.effect_keys(fx):
            nk = k.replace(f"fx/{fid}/", f"fx/{nid}/")
            layer.props[nk] = Property.from_dict(nk, layer.props[k].to_dict())
        layer.effects.insert(layer.effects.index(fx) + 1, nfx)
        layer.refresh_meta()
        self.notify()

    def move_effect(self, layer: Layer, fid: str, d: int):
        fx = layer.effect_by_id(fid)
        if not fx:
            return
        i = layer.effects.index(fx)
        j = max(0, min(len(layer.effects) - 1, i + d))
        if i != j:
            self.snapshot()
            layer.effects.insert(j, layer.effects.pop(i))
            self.notify()

    def set_effect_opt(self, layer: Layer, fid: str, key: str, value):
        fx = layer.effect_by_id(fid)
        if fx:
            self.snapshot(f"{layer.id}.{fid}.{key}")
            fx.setdefault("opts", {})[key] = value
            self.notify()

    # ------------------------------------------------------------------ переключатели слоёв
    def toggle_switch(self, attr: str, layers=None):
        """solo / locked / motion_blur / adjustment / fx_on — для выделенных (или переданных) слоёв."""
        layers = layers if layers is not None else self.selected()
        if not layers:
            return
        self.snapshot()
        val = not all(getattr(l, attr) for l in layers)
        for l in layers:
            setattr(l, attr, val)
            if attr == "locked" and val and l.id in self.sel_layers:
                self.sel_layers.remove(l.id)
        self.notify()
        if attr == "locked":
            self.selection_changed.emit()

    def unlock_all(self):
        if any(l.locked for l in self.comp.layers):
            self.snapshot()
            for l in self.comp.layers:
                l.locked = False
            self.notify()

    # ------------------------------------------------------------------ родители (Parent)
    def can_parent(self, child: Layer, parent: Layer | None) -> bool:
        if parent is None:
            return True
        return parent is not child and child not in [parent] + parent_chain(parent, self.comp)

    def set_parent(self, child: Layer, parent: Layer | None):
        if not self.can_parent(child, parent) or child.parent == (parent.id if parent else None):
            return
        self.snapshot()
        self._reparent(child, parent)
        self.notify()

    def _reparent(self, child: Layer, parent: Layer | None):
        """Как в AE: при смене родителя слой остаётся на месте — пересчитываем положение/масштаб/поворот.
        Если эти свойства анимированы, значения не трогаем (ключи остались бы в старой системе координат)."""
        new_id = parent.id if parent else None
        if any(child.props[k].animated for k in ("position", "scale", "rotation")):
            child.parent = new_id
            return
        t = self.time
        old_pw = parent_world(child, self.comp, t)
        child.parent = new_id
        inv, ok = parent_world(child, self.comp, t).inverted()
        if not ok:
            return
        ax, ay = child.props["anchor"].value_at(child.local(t))
        # S·R·T(pos) = T(anchor) · local; переводим в систему нового родителя
        m = QTransform.fromTranslate(ax, ay) * local_transform(child, t, with_shake=False) * old_pw * inv
        sx = math.hypot(m.m11(), m.m12())
        if sx < 1e-9:
            return
        det = m.m11() * m.m22() - m.m12() * m.m21()
        child.props["position"].value = [round(m.dx(), 3), round(m.dy(), 3)]
        child.props["scale"].value = [round(sx * 100, 3), round(det / sx * 100, 3)]
        child.props["rotation"].value = [round(math.degrees(math.atan2(m.m12(), m.m11())), 3)]

    # ------------------------------------------------------------------ файлы
    def new_project(self):
        self.stop()
        self.project = Project()
        self.path = None
        self.dirty = False
        self.time = 0
        self.sel_layers, self.sel_keys = [], set()
        self._undo.clear()
        self._redo.clear()
        self.pool.close()
        self.media_changed.emit()
        self.comp_switched.emit()
        self.changed.emit()
        self.selection_changed.emit()
        self.time_changed.emit(0)

    def save(self, path: str):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.project.to_dict(), f, ensure_ascii=False, indent=1)
        self.path = path
        self.dirty = False
        self.changed.emit()

    # ---- автосохранение (как Auto-Save в AE): копия проекта, не трогает self.path/dirty
    @staticmethod
    def autosave_dir() -> str:
        base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        return os.path.join(base, "sfx", "autosave")

    def autosave(self) -> str | None:
        """Записать копию несохранённого проекта в папку автосохранения (атомарно). Путь или None."""
        if not self.dirty or not any(c.layers for c in self.project.comps):
            return None
        d = self.autosave_dir()
        os.makedirs(d, exist_ok=True)
        name = os.path.splitext(os.path.basename(self.path))[0] if self.path else "untitled"
        out = os.path.join(d, f"{name}_autosave.skl")
        tmp = out + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(dict(self.project.to_dict(), autosave_of=self.path), f, ensure_ascii=False)
        os.replace(tmp, out)
        return out

    def load(self, path: str):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        self.new_project()
        self.project = Project.from_dict(d)
        self.path = path
        self.dirty = False
        self.time = max(0, min(self.comp.duration - 1, getattr(self.comp, "time", 0)))
        self.media_changed.emit()
        self.comp_switched.emit()
        self.changed.emit()
        self.selection_changed.emit()
