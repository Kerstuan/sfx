# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"; os.environ["XDG_CACHE_HOME"] = "/tmp/sfx-tests/xdgcache"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1]
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
app = QApplication([])
from sfx.editor import Editor
from sfx.ui.main_window import MainWindow
ed = Editor(); win = MainWindow(ed); win.resize(1600, 950); win.show(); ed.set_idle_cache(False); ed.proxies.enabled = False
app.processEvents()
SRC = os.path.expanduser("~/Videos/Replay_2026-10-31_00-14-28.mp4")
ed.add_media_layer(SRC); a = ed.comp.layers[0]; fps = ed.comp.fps
# --- 1. скорость у обрезанного клипа: как на скрине — исходник начинается далеко левее
a.start = -1200; a.in_point = 300; a.out_point = 600
p = a.props["position"]; p.toggle_stopwatch(400 - a.start); p.add_keyframe(500 - a.start)
src_at_in = a.src_time(a.local(300), fps)
ed.select([a.id]); ed.time = 300
ed.set_speed([a], 50.0, True)
print("in", a.in_point, "out", a.out_point, "ключи (комп.)", [a.start + k.frame for k in p.keyframes])
assert a.in_point == 300, a.in_point
assert a.out_point == 900, a.out_point
assert abs(a.src_time(a.local(300), fps) - src_at_in) < 1.0 / fps, (a.src_time(a.local(300), fps), src_at_in)
assert [a.start + k.frame for k in p.keyframes] == [500, 700]
ed.set_speed([a], 200.0, True); assert a.in_point == 300 and a.out_point == 450, (a.in_point, a.out_point)
# --- 2. масштаб таймлайна после Ctrl+Z
tl = win.timeline; tl.ppf, tl.view_start = 23.0, 250.0; win.viewer.vzoom = 0.8
ed.snapshot(); a.props["opacity"].value = [40.0]; ed.notify()
QTest.keyClick(win, Qt.Key_Z, Qt.ControlModifier); app.processEvents()
assert ed.comp.layers[0].props["opacity"].value == [100.0], "undo не сработал"
assert (tl.ppf, tl.view_start, win.viewer.vzoom) == (23.0, 250.0, 0.8), (tl.ppf, tl.view_start, win.viewer.vzoom)
QTest.keyClick(win, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier); app.processEvents()
assert tl.ppf == 23.0
# --- 3. Ctrl+C / Ctrl+V эффекта с ключами на другой слой
a = ed.comp.layers[0]
ed.select([a.id]); ed.add_effect("shake"); fid = a.effects[-1]["id"]
amt = a.props[f"fx/{fid}/amount"]; amt.toggle_stopwatch(0); amt.add_keyframe(30); amt.set_value_at(30, [77.0])
ed.add_solid("#ff0000"); b = ed.comp.layers[0]
ed.select([a.id]); ed.sel_effect = (a.id, fid)
QTest.keyClick(win, Qt.Key_C, Qt.ControlModifier); app.processEvents()
assert ed.clipboard and ed.clipboard[0] == "effects", ed.clipboard
ed.select([b.id]); QTest.keyClick(win, Qt.Key_V, Qt.ControlModifier); app.processEvents()
assert len(b.effects) == 1 and b.effects[0]["type"] == "shake"
nid = b.effects[0]["id"]; assert nid != fid
bk = b.props[f"fx/{nid}/amount"]; t0 = int(b.local(ed.time)); assert len(bk.keyframes) == 2 and bk.keyframe_at(t0 + 30).value == [77.0], [(k.frame, k.value) for k in bk.keyframes]
# вставка в тот же слой второй раз — уникальное имя
ed.select([b.id]); ed.paste(); assert len(b.effects) == 2 and b.effects[0]["name"] != b.effects[1]["name"]
# undo вставки
ed.undo(); b = ed.comp.layer_by_id(b.id); assert len(b.effects) == 1
# ключи: копия и вставка от текущего кадра
a = ed.comp.layer_by_id(a.id); pa = a.props["position"]
ed.select([a.id]); ed.sel_keys = {(a.id, "position", k.frame) for k in pa.keyframes}
ed.copy_selection(); ed.time = 2000; ed.select([b.id]); n = ed.paste()
pb = b.props["position"]; print("ключи вставлены:", n, [b.start + k.frame for k in pb.keyframes])
assert n == 2 and [b.start + k.frame for k in pb.keyframes][:2] == [2000, 2050]
# слои
ed.select([b.id]); ed.sel_keys = set(); ed.copy_selection(); cnt = len(ed.comp.layers); ed.paste()
assert len(ed.comp.layers) == cnt + 1 and ed.comp.layers[0].id != b.id
# вырезать эффект
ed.select([b.id]); ed.sel_effect = (b.id, b.effects[0]["id"]); ed.cut_selection(); assert not b.effects
print("OK", sys.argv[1])
