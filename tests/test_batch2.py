# Copyright (c) 2026 Kerstuan. All rights reserved.
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"; os.environ["XDG_CACHE_HOME"] = "/tmp/sfx-tests/xdgcache"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1]
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtTest import QTest
app = QApplication([])
import numpy as np
from sfx.editor import Editor
from sfx.ui.main_window import MainWindow
from sfx.render import render_frame
from sfx.effects import to_array
ed = Editor(); win = MainWindow(ed); win.resize(1600, 950); win.show(); ed.set_idle_cache(False); ed.proxies.enabled = False
app.processEvents()
SRC = os.path.expanduser("~/Videos/Replay_2026-10-31_00-14-28.mp4")
ed.add_media_layer(SRC); v = ed.comp.layers[0]
# --- прекомп с подгонкой длины
v.start, v.in_point, v.out_point = -600, 300, 500
img_before = to_array(render_frame(ed.comp, 400, ed.pool, 0.25))
ed.select([v.id]); pre = ed.precompose("P1", fit_duration=True)
nc = ed.project.comp_by_id(pre.source)
print("прекомп: длина", nc.duration, "слой", pre.in_point, pre.out_point, "внутри", nc.layers[0].in_point, nc.layers[0].out_point)
assert nc.duration == 200 and (pre.in_point, pre.out_point) == (300, 500) and nc.layers[0].in_point == 0
img_after = to_array(render_frame(ed.comp, 400, ed.pool, 0.25))
d = np.abs(img_before - img_after).mean(); print("разница картинки до/после прекомпа:", round(d, 2)); assert d < 1.0
ed.undo(); v = ed.comp.layers[0]
# --- «оставить атрибуты»
ed.select([v.id]); ed.add_effect("threshold"); v.props["scale"].value = [50.0, 50.0]
img_before = to_array(render_frame(ed.comp, 400, ed.pool, 0.25))
ed.select([v.id]); pre = ed.precompose("P2", leave_attrs=True)
nc = ed.project.comp_by_id(pre.source); inner = nc.layers[0]
print("оставить атрибуты: снаружи эффекты", [f["type"] for f in pre.effects], "scale", pre.props["scale"].value, "внутри эффекты", inner.effects, inner.props["scale"].value)
assert pre.effects and not inner.effects and pre.props["scale"].value == [50.0, 50.0] and inner.props["scale"].value == [100.0, 100.0]
img_after = to_array(render_frame(ed.comp, 400, ed.pool, 0.25))
d = np.abs(img_before - img_after).mean(); print("разница:", round(d, 2)); assert d < 2.0
ed.undo(); ed.undo()
v = ed.comp.layers[0]
# --- непрозрачность эффекта
ed.select([v.id]); ed.add_effect("invert"); fid = v.effects[-1]["id"]
a100 = to_array(render_frame(ed.comp, 400, ed.pool, 0.25))
v.props[f"fx/{fid}/mix"].value = [0.0]; a0 = to_array(render_frame(ed.comp, 400, ed.pool, 0.25))
v.props[f"fx/{fid}/mix"].value = [50.0]; a50 = to_array(render_frame(ed.comp, 400, ed.pool, 0.25))
print("mix 50% ≈ среднее:", round(np.abs(a50 - (a0 + a100) / 2).mean(), 2)); assert np.abs(a50 - (a0 + a100) / 2).mean() < 1.5
ed.remove_effect(v, fid)
# --- простая тряска
ed.select([v.id]); ed.add_effect("wiggle"); fid = v.effects[-1]["id"]
from sfx.render import local_transform
t0 = local_transform(v, 400, ed.comp.fps); assert abs(t0.dx() - local_transform(v, 410, ed.comp.fps).dx()) < 1e-6
v.props[f"fx/{fid}/px"].value = [80.0]
xs = [local_transform(v, f, ed.comp.fps).dx() for f in range(300, 400)]; print("wiggle X размах", round(max(xs) - min(xs), 1)); assert max(xs) - min(xs) > 40
# --- значение сразу у нескольких слоёв (как в AE)
ed.add_solid("#ff0000"); s1 = ed.comp.layers[0]; ed.add_solid("#00ff00"); s2 = ed.comp.layers[0]
tl = win.timeline; ed.select([s1.id, s2.id]); tl.show_props("scale"); app.processEvents(); tl.repaint()
hit = next(h for h in tl._hits if h[0] == "value" and h[2][0] is s2 and h[2][1] == "scale" and h[2][2] == 0)
c = hit[1].center().toPoint()
QTest.mousePress(tl, Qt.LeftButton, Qt.NoModifier, c); QTest.mouseMove(tl, c + QPoint(30, 0)); QTest.mouseRelease(tl, Qt.LeftButton, Qt.NoModifier, c + QPoint(30, 0)); app.processEvents()
print("scale у двух слоёв:", s1.props["scale"].value, s2.props["scale"].value)
assert s1.props["scale"].value == s2.props["scale"].value and s1.props["scale"].value[0] > 100
# --- копия значения Scale: Ctrl+C на свойстве → Ctrl+V на другом слое
ed.select([s2.id]); ed.sel_prop = (s2.id, "scale"); ed.sel_keys = set(); what = ed.copy_selection(); assert what == "value"
ed.select([v.id]); n = ed.paste(); print("вставили scale:", v.props["scale"].value); assert v.props["scale"].value == s2.props["scale"].value
# --- сброс одного свойства
v.props["opacity"].value = [30.0]; ed.reset_prop(v, "opacity"); assert v.props["opacity"].value == [100.0]
# --- разворот ключей
p = s1.props["position"]; p.toggle_stopwatch(0); p.set_value_at(0, [100, 100]); p.add_keyframe(30); p.set_value_at(30, [500, 100])
ed.sel_keys = {(s1.id, "position", 0), (s1.id, "position", 30)}; ed.time_reverse_keys()
print("разворот:", [(k.frame, k.value) for k in p.keyframes]); assert p.keyframe_at(0).value == [500, 100]
# --- U дважды сворачивает всё
ed.deselect(); tl.show_props("animated"); assert tl.shown.get(s1.id); tl.show_props("animated"); assert not any(tl.shown.values()), tl.shown
# --- перетаскивание слоя в списке
order = [l.id for l in ed.comp.layers]; ed.select([order[0]])
ed.move_layers_to(ed.selected(), 3); print("порядок:", [order.index(l.id) for l in ed.comp.layers])
assert ed.comp.layers[2].id == order[0]
# --- поле ввода закрывается при прокрутке
tl._open_edit(hit[1], "123", lambda t: None); assert tl._edit
from PySide6.QtGui import QWheelEvent
ev = QWheelEvent(QPointF(400, 300), QPointF(400, 300), QPoint(0, 0), QPoint(0, -120), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
app.sendEvent(tl, ev); assert not tl._edit
print("OK", sys.argv[1])
