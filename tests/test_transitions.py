# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""Переходы: окно у стыка, A → B, все типы меняют кадр, вне окна кадр как без перехода, сохранение и отмена."""
import os, sys, time, json
os.environ["QT_QPA_PLATFORM"] = "offscreen"; os.environ["XDG_CACHE_HOME"] = "/tmp/sfx-tests/xdgcache"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1]
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter
app = QApplication([])
import numpy as np
from sfx.editor import Editor
from sfx.render import render_frame
from sfx.effects import EFFECTS, to_array, category_label
from sfx.transitions import TRANSITIONS, window, partner

VID = os.path.expanduser("~/Videos/Replay_2026-10-31_00-14-28.mp4")
ed = Editor()
ed.add_media_layer(VID)
ed.add_media_layer(VID)
b, a = ed.comp.layers[0], ed.comp.layers[1]          # B сверху (входящий), A под ним
C = 60
a.out_point = C
b.start, b.in_point = C - 900, C                       # B показывает другой момент записи
assert partner(ed.comp, b) is a
assert category_label("transition") == ("Transitions" if sys.argv[1] == "en" else "Переходы")
S = 0.25
arr = lambda f: to_array(render_frame(ed.comp, f, ed.pool, S))
plain = {f: arr(f) for f in (40, C - 3, C, C + 2, 80)}  # без перехода: до стыка A, после — B
diff = lambda x, y: float(np.abs(x - y).mean())
assert diff(plain[C - 3], plain[C + 2]) > 5, "A и B должны отличаться"

rows = []
for t in TRANSITIONS:
    ed.select([b.id])
    assert ed.add_effect(t), t
    fx = b.effects[-1]
    w0, w1, v = window(b, fx)
    d = w1 - w0
    assert w0 < C < w1, (t, w0, w1)
    assert diff(arr(40), plain[40]) < 0.5, f"{t}: до окна кадр изменился"
    assert diff(arr(80), plain[80]) < 0.5, f"{t}: после окна кадр изменился"
    t0 = time.perf_counter(); mid = arr(C); dt = (time.perf_counter() - t0) * 1000
    assert diff(mid, plain[C]) > 2, f"{t}: в середине кадр не меняется"
    first, last = arr(w0), arr(w1 - 1)
    if t not in ("t_flash", "t_glitch", "t_flare", "t_glow"):   # у этих края зависят от вспышки/шума
        assert diff(first, plain[C - 3]) < diff(first, plain[C + 2]), f"{t}: начало не похоже на A"
        assert diff(last, plain[C + 2]) < diff(last, plain[C - 3]), f"{t}: конец не похож на B"
    print(f"{t:11s} {EFFECTS[t].label:28s} {d} fr, mid {dt:6.1f} ms (1/4)")
    rows.append((EFFECTS[t].label, [render_frame(ed.comp, w0 + int(d * k), ed.pool, S) for k in (0.1, 0.3, 0.5, 0.7, 0.9)]))
    ed.remove_effect(b, fx["id"])

# Длительность и выравнивание
ed.select([b.id]); ed.add_effect("t_dissolve"); fx = b.effects[-1]
key = f"fx/{fx['id']}/"
b.props[key + "dur"].value = [20.0]; b.props[key + "align"].value = [1.0]
assert window(b, fx)[:2] == (C, C + 20)
b.props[key + "align"].value = [2.0]
assert window(b, fx)[:2] == (C - 20, C)
# Сохранение в проект и отмена добавления
assert '"t_dissolve"' in json.dumps(ed.project.to_dict())
ed.undo()
assert not any(f["type"] == "t_dissolve" for f in ed.comp.layers[0].effects), "отмена не убрала переход"
# Без слоя A — переход из прозрачного (не падает)
nb = ed.comp.layers[0]
ed.comp.layers.pop(1)
ed.select([nb.id]); ed.add_effect("t_dissolve")
render_frame(ed.comp, C, ed.pool, S)

# Переход на выходе (Edge = Out): верхний клип уходит в нижний у своего Out
e2 = Editor(); e2.add_media_layer(VID); e2.add_media_layer(VID)
top, bot = e2.comp.layers[0], e2.comp.layers[1]
bot.start = -900                                      # нижний показывает другой момент
top.out_point = 60
ar2 = lambda f: to_array(render_frame(e2.comp, f, e2.pool, S))
p_top, p_bot = ar2(50), ar2(70)
e2.select([top.id]); e2.add_effect("t_dissolve"); ofx = top.effects[-1]
top.props[f"fx/{ofx['id']}/edge"].value = [1.0]
ow0, ow1, _ = window(top, ofx)
assert ow0 < 60 < ow1, (ow0, ow1)
assert diff(ar2(50), p_top) < 0.5 and diff(ar2(70), p_bot) < 0.5, "вне окна выходного перехода кадр изменился"
mid2 = ar2(60)
assert diff(mid2, p_top) > 1 and diff(mid2, ar2(60 + 20)) > 0.5, "выходной переход не смешивает"

# Таймлайн: окно перехода рисуется и его край тянется (Duration)
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from sfx.ui.theme import STYLESHEET; app.setStyleSheet(STYLESHEET)
from sfx.ui.main_window import MainWindow
from sfx.effects import prop_key
win = MainWindow(ed); win.resize(1600, 950); win.show(); ed.set_idle_cache(False); app.processEvents()
tl = win.timeline
fx = next(f for f in nb.effects if f["type"] == "t_dissolve")
tl.update(); app.processEvents(); tl.repaint()
hit = [h for h in tl._hits if h[0] == "trans"]
assert hit, "окно перехода не нарисовано на таймлайне"
r = hit[0][1]
y = int(r.center().y())
QTest.mousePress(tl, Qt.LeftButton, Qt.NoModifier, QPoint(int(r.right()) - 1, y))
QTest.mouseMove(tl, QPoint(int(tl.f2x(nb.in_point + 15)), y))
QTest.mouseRelease(tl, Qt.LeftButton, Qt.NoModifier, QPoint(int(tl.f2x(nb.in_point + 15)), y))
dur = nb.props[prop_key(fx["id"], "dur")].value[0]; fpp = tl.x2f(tl.f2x(0) + 1) - tl.x2f(tl.f2x(0)); print("frames/px", fpp, "dur", dur)
assert abs(dur - 30) <= 2 + 4 * fpp, f"Duration после перетаскивания {dur}, ждали ~30"
assert ed.sel_effect == (nb.id, fx["id"])
tl.grab().save(f"/tmp/sfx-tests/transitions_tl_{sys.argv[1]}.png")

W, H = rows[0][1][0].width(), rows[0][1][0].height()
sheet = QImage(W * 5, H * len(rows), QImage.Format_RGB32); sheet.fill(0)
p = QPainter(sheet)
for r, (name, ims) in enumerate(rows):
    for i, im in enumerate(ims):
        p.drawImage(i * W, r * H, im)
    p.setPen(0xffff00); p.drawText(6, r * H + 16, name)
p.end(); sheet.save(f"/tmp/sfx-tests/transitions_{sys.argv[1]}.png")
print("OK", sys.argv[1])
