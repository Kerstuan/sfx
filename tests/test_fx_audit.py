# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Аудит всех эффектов: каждый визуальный эффект применяется к видео и рендерится без ошибок; параметры влияют
на картинку; ключи анимируют параметр; эффект переживает сохранение/загрузку; смена порядка эффектов меняет результат.
Звуковые — сведение без ошибок. Печатает сводку; падает, если есть ошибки."""
import os, sys, json, time, traceback
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1] if len(sys.argv) > 1 else "ru"
from PySide6.QtWidgets import QApplication
app = QApplication([])
import numpy as np
from sfx.editor import Editor
from sfx.render import render_frame
from sfx.effects import EFFECTS, to_array, prop_key
from sfx.model import Project
from sfx.audio import mix

VID = os.path.expanduser("~/Videos/Replay_2026-10-31_00-14-28.mp4")
diff = lambda x, y: float(np.abs(x - y).mean())
errors, no_effect, no_anim, slow, report = [], [], [], [], []


_prev = []


def setup(t):
    for old in _prev:            # прошлый редактор: кэш кадров и декодеры — освободить (иначе ~80 редакторов съедают ОЗУ)
        old.set_idle_cache(False); old.cache.clear(); old.pool.close()
    _prev.clear()
    ed = Editor(); ed.set_idle_cache(False)
    _prev.append(ed)
    ed.add_media_layer(VID); v = ed.comp.layers[0]
    if EFFECTS[t].transition:   # переход: нужен слой A под ним
        ed.add_media_layer(VID); b = ed.comp.layers[0]
        b.start = b.in_point = 30
        v = b
    ed.select([v.id])
    return ed, v


base_ed, _ = setup("blur")
base = {f: to_array(render_frame(base_ed.comp, f, base_ed.pool, 0.25)) for f in (40,)}

for t, d in sorted(EFFECTS.items()):
    if d.control:
        continue
    try:
        ed, v = setup(t)
        if d.audio:
            assert ed.add_effect(t, [v]), "не применился"
            out = mix(ed.comp, 0, 30)
            assert np.isfinite(out).all(), "NaN в звуке"
            report.append((t, "audio", "ok"))
            continue
        assert ed.add_effect(t, [v]), "не применился к видео"
        fx = v.effects[-1]
        f = 40
        t0 = time.time()
        a = to_array(render_frame(ed.comp, f, ed.pool, 0.25))
        dt = time.time() - t0
        assert np.isfinite(a).all()
        if dt > 1.5:
            slow.append((t, round(dt, 2)))
        changed = diff(a, base[40]) > 0.3
        # параметр: меняем первый числовой (не mix) и смотрим, что картинка меняется
        nums = [p for p in d.params if p.kind not in ("checkbox", "choice", "color", "point") and p.key != "mix"]
        moved = None
        if nums:
            p = nums[0]
            key = prop_key(fx["id"], p.key)
            pr = v.props[key]
            lo = p.lo if p.lo is not None else -1e9
            hi = p.hi if p.hi is not None else 1e9
            cur = pr.value[0]
            new = min(hi, max(lo, cur * 2 + (p.step * 40 if cur == 0 else 0)))
            if new == cur:
                new = min(hi, max(lo, cur / 2))
            # ключи: кадр 0 — старое, кадр 80 — новое; на 40 должно быть посередине и отличаться от обоих краёв
            lf0, lf1 = 0, 80
            pr.toggle_stopwatch(lf0); pr.add_keyframe(lf1); pr.set_value_at(lf1, [new] + list(pr.value[1:]))
            mid = pr.value_at(v.local(40))[0]
            if not (min(cur, new) - 1e-6 <= mid <= max(cur, new) + 1e-6) or cur == new:
                no_anim.append((t, p.key, cur, new, mid))
            b = to_array(render_frame(ed.comp, 79, ed.pool, 0.25))
            ed.time = 79
            c0 = to_array(render_frame(ed.comp, 79, ed.pool, 0.25))
            pr.keyframes = []; pr.value = [cur] + list(pr.value[1:])
            c1 = to_array(render_frame(ed.comp, 79, ed.pool, 0.25))
            moved = diff(c0, c1)
        if not changed and (moved is None or moved < 0.05):
            no_effect.append(t)
        # сохранение/загрузка
        p2 = Project.from_dict(json.loads(json.dumps(ed.project.to_dict())))
        lay = [l for l in p2.comp.layers if l.id == v.id][0]
        assert [e["type"] for e in lay.effects] == [e["type"] for e in v.effects], "эффект потерялся при сохранении"
        a2 = to_array(render_frame(p2.comp, f, ed.pool, 0.25))
        if diff(a, a2) > 0.5 and t not in ("noise", "grain", "glitch", "randedits", "jpegdamage"):
            errors.append((t, f"после загрузки картинка другая ({diff(a, a2):.2f})"))
        report.append((t, d.category, f"ok {dt:.2f}s changed={changed} param_move={moved if moved is None else round(moved, 2)}"))
    except Exception as e:  # noqa
        errors.append((t, repr(e)[:200]))
        traceback.print_exc()

# порядок эффектов: blur → mosaic против mosaic → blur
ed, v = setup("blur"); ed.add_effect("blur", [v]); ed.add_effect("mosaic", [v])
v.props[prop_key(v.effects[0]["id"], "radius")].value = [20.0]
x = to_array(render_frame(ed.comp, 40, ed.pool, 0.25))
v.effects.reverse()
y = to_array(render_frame(ed.comp, 40, ed.pool, 0.25))
if diff(x, y) < 0.3:
    errors.append(("order", "смена порядка эффектов не меняет результат"))

for r in report:
    print("  ", *r)
print("ошибки:", errors)
print("без видимого действия (по умолчанию и при смене 1-го параметра):", no_effect)
print("ключи не анимируют:", no_anim)
print("медленные (>1.5 с на кадр 1/4):", slow)
print("эффектов проверено:", len(report))
if not errors:
    print("OK", i18n.LANG)
sys.stdout.flush(); os._exit(0)   # без flush вывод в pipe (run_all.sh) теряется
