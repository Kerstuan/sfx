# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Аудит параметров: у КАЖДОГО визуального эффекта КАЖДЫЙ параметр (и opts seed/color) по очереди ставится
в крайние/другие значения; кадр должен измениться хотя бы при одном значении, рендер — без исключений и NaN.
Печатает «мёртвые» параметры. Аргументы: язык [фильтр типов через запятую] [масштаб]."""
import os, sys, time, traceback
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1] if len(sys.argv) > 1 else "ru"
from PySide6.QtWidgets import QApplication
app = QApplication([])
import numpy as np
from sfx.editor import Editor
from sfx.render import render_frame
from sfx.effects import EFFECTS, to_array, prop_key

VID = os.path.expanduser("~/Videos/Replay_2026-10-31_00-14-28.mp4")
ONLY = set(sys.argv[2].split(",")) if len(sys.argv) > 2 and sys.argv[2] else None
SCALE = float(sys.argv[3]) if len(sys.argv) > 3 else 0.5
F = 40
_prev = []
# параметр действует только при другом значении «хозяина» (проверено вручную; тест ставит этот контекст)
CONTEXT = {
    ("blurmo", "center"): {"rotate": [30.0]},
    ("edgerays", "dark"): {"color": [255.0, 80.0, 0.0]},          # тёмные лучи = (1 − цвет лучей)
    ("lensflare", "efall"): {"eshift": [0.1]},
    ("motiontile", "mirror"): {"tw": [40.0], "th": [40.0]},       # швы видны, только когда плиток больше одной
    ("motiontile", "phase"): {"tw": [40.0], "th": [40.0]},
    ("motiontile", "hshift"): {"tw": [40.0], "th": [40.0], "phase": [90.0]},
    ("jpegdamage", "opts.seed"): {"errrate": [10.0]},
    ("lensflare", "opts.seed"): {"flicker": [4.0]},
    ("rays", "opts.seed"): {"atmo": [1.0]},
    ("wiggle", "opts.seed"): {"px": [200.0]},
    ("flash", "opts.color"): {"amount": [100.0]},
}
# «Влияет на альфу» видно только на прозрачном слое — проверяется на текстовом слое
ALPHA = {"affalpha"}
# не про рендер: выбор кривой для редактирования; Roto — без сессии маски нет (проверяет test_roto)
SKIP = {("curves", "channel")} | {("roto", k) for k in
                                  ("show", "refine", "detail", "feather", "choke", "shift", "contrast", "invert")}


def setup(t, text=False):
    for old in _prev:
        old.set_idle_cache(False); old.cache.clear(); old.pool.close()
    _prev.clear()
    ed = Editor(); ed.set_idle_cache(False); _prev.append(ed)
    if text:
        ed.add_text(); v = [l for l in ed.comp.layers if l.kind == "text"][0]
    else:
        ed.add_media_layer(VID); v = ed.comp.layers[0]
    if EFFECTS[t].transition:
        ed.add_media_layer(VID); v = ed.comp.layers[0]
        v.start = v.in_point = 30
    ed.add_effect(t, [v])
    return ed, v, v.effects[-1]


def alts(p, cur, ed):
    """Другие допустимые значения параметра."""
    W, H = ed.comp.width, ed.comp.height
    if p.kind == "checkbox":
        return [[1.0 - cur[0]]]
    if p.kind == "choice":
        return [[float(i)] for i in range(len(p.choices or [])) if i != int(round(cur[0]))]
    if p.kind == "color":
        return [[255.0 - c for c in cur[:3]] + cur[3:], [255.0, 0.0, 255.0] + cur[3:]]
    if p.kind == "point":
        return [[W * 0.2, H * 0.8], [W * 0.85, H * 0.15]]
    if p.kind == "angle":
        return [[c + 90.0 for c in cur], [c + 37.0 for c in cur]]
    out = [[float(x)] * len(cur) for x in (p.lo, p.hi) if x is not None]
    for cand in ([c * 3 + p.step * 40 for c in cur], [c / 3 - p.step * 10 for c in cur]):
        cand = [min(p.hi, x) if p.hi is not None else x for x in cand]
        out.append([max(p.lo, x) if p.lo is not None else x for x in cand])
    return [o for o in out if o != list(cur)]


def render(ed):
    fxl = [l for l in ed.comp.layers if l.effects][0]
    f = 34 if EFFECTS[fxl.effects[-1]["type"]].transition else F   # окно перехода у In=30
    a = to_array(render_frame(ed.comp, f, ed.pool, SCALE))
    assert np.isfinite(a).all(), "NaN/inf в кадре"
    return a


def d(x, y):
    e = np.abs(x - y)
    return float(e.mean()), float(e.max())


def ctx_values(q, cur, ed):
    """Значения «хозяина», при которых зависимый параметр может включиться."""
    if q.kind in ("checkbox", "choice"):
        return alts(q, cur, ed)
    if q.kind == "float" and all(c == 0 for c in cur):
        return [[float(q.hi) if q.hi is not None and q.hi < 1e4 else q.step * 40] * len(cur)]
    return []


def context_pass(ed, v, fx, ed_def, p):
    """Параметр не действует сам по себе: ищем другой параметр, при котором он действует."""
    pr = v.props[prop_key(fx["id"], p.key)]
    cur = list(pr.value)
    for q in ed_def.params:
        if q is p:
            continue
        qr = v.props[prop_key(fx["id"], q.key)]
        qcur = list(qr.value)
        for qv in ctx_values(q, qcur, ed):
            qr.value = qv; ed.cache.clear()
            try:
                b = render(ed); best = (0.0, 0.0)
                for val in alts(p, cur, ed):
                    pr.value = val; ed.cache.clear()
                    best = max(best, d(render(ed), b))
                pr.value = cur
                if best[1] > 1.0 or best[0] >= 0.02:
                    return best, f"{q.key}={qv[0]:g}"
            finally:
                pr.value = cur; qr.value = qcur
    ed.cache.clear()
    return (0.0, 0.0), None


dead, errors, nondet, slow, total = [], [], [], [], 0
for t, ed_def in sorted(EFFECTS.items()):
    if ed_def.control or ed_def.audio or (ONLY and t not in ONLY):
        continue
    try:
        ed, v, fx = setup(t)
        base = render(ed)
        noise = d(base, render(ed))[1]
        if noise > 0:
            nondet.append((t, round(noise, 2)))
        checks = [("param", p) for p in ed_def.params] + [("opt", k) for k in ("seed", "color") if k in fx["opts"]]
        for kind, p in checks:
            total += 1
            name = p.key if kind == "param" else "opts." + p
            best, t0 = (0.0, 0.0), time.time()
            if (t, name) in SKIP:
                continue
            ctx_saved = {}
            if kind == "param" and p.key in ALPHA:
                ed, v, fx = setup(t, text=True)
            for k, val in CONTEXT.get((t, name), {}).items():
                ctx_saved[k] = list(v.props[prop_key(fx["id"], k)].value)
                v.props[prop_key(fx["id"], k)].value = val
            if ctx_saved or (kind == "param" and p.key in ALPHA):
                ed.cache.clear(); base = render(ed)
            try:
                if kind == "param":
                    pr = v.props[prop_key(fx["id"], p.key)]
                    cur = list(pr.value)
                    try:
                        for val in alts(p, cur, ed):
                            pr.value = val
                            ed.cache.clear()
                            best = max(best, d(render(ed), base))
                    finally:
                        pr.value = cur
                else:
                    old = fx["opts"][p]
                    try:
                        for val in ([old + 1, old + 17] if p == "seed" else ["#00ff40", "#2040ff"]):
                            fx["opts"][p] = val
                            ed.cache.clear()
                            best = max(best, d(render(ed), base))
                    finally:
                        fx["opts"][p] = old
            except Exception as e:  # noqa
                errors.append((t, name, repr(e)[:200]))
                traceback.print_exc()
                continue
            finally:
                for k, val in ctx_saved.items():
                    v.props[prop_key(fx["id"], k)].value = val
                if ctx_saved or (kind == "param" and p.key in ALPHA):
                    if kind == "param" and p.key in ALPHA:
                        ed, v, fx = setup(t)
                    ed.cache.clear(); base = render(ed)
            dt = time.time() - t0
            if dt > 6:
                slow.append((t, name, round(dt, 1)))
            if kind == "param" and best[1] <= max(noise, 1.0) and best[0] < 0.02:
                best, ctx = context_pass(ed, v, fx, ed_def, p)
                name += f" [при {ctx}]" if ctx else ""
            if best[1] <= max(noise, 1.0) and best[0] < 0.02:   # не выше шума повторного рендера
                dead.append((t, name, round(best[0], 3), round(best[1], 2)))
            print(f"  {t:16s} {name:18s} mean={best[0]:7.3f} max={best[1]:6.1f}", flush=True)
    except Exception as e:  # noqa
        errors.append((t, "setup", repr(e)[:200]))
        traceback.print_exc()

print("ошибки:", errors)
print("мёртвые параметры (кадр не меняется):", dead)
print("недетерминированный рендер (тот же кадр дважды):", nondet)
print("медленные проверки:", slow)
print("параметров проверено:", total)
if not errors and not dead:
    print("OK", i18n.LANG)
sys.stdout.flush(); os._exit(0)
