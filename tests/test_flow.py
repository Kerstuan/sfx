# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
import os, sys, tempfile
os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
lang = sys.argv[1]
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n
i18n.LANG = lang
from PySide6.QtWidgets import QApplication
app = QApplication([])
from sfx.editor import Editor
from sfx.ui.main_window import MainWindow
from sfx.model import bezier_ease
ed = Editor(); win = MainWindow(ed); win.resize(1600, 950); win.show(); app.processEvents()
fp = win.flow_panel
assert not fp.apply_btn.isEnabled()
ed.add_solid("#ff0000")
l = ed.comp.layers[0]
p = l.props["position"]
p.toggle_stopwatch(0); p.value = [0, 0]
p.set_value_at(0, [0, 0]); p.add_keyframe(30); p.set_value_at(30, [100, 0]); p.add_keyframe(60); p.set_value_at(60, [200, 0])
ks = p.keyframes; assert len(ks) == 3, ks
ed.sel_keys = {(l.id, "position", 0), (l.id, "position", 30), (l.id, "position", 60)}; ed.changed.emit()
assert fp.apply_btn.isEnabled() and len(ed.curve_segments()) == 2
c = (0.16, 1.0, 0.3, 1.0)  # Expo Out
fp.set_curve(c); fp.apply()
v = p.raw_at(15)[0]; exp = 100 * bezier_ease(0.5, *c)
assert abs(v - exp) < 1e-6 and v > 90, v
assert fp.edit.text() == "0.16, 1.00, 0.30, 1.00"
# read back from segment
fp.set_curve((0.5, 0.5, 0.5, 0.5)); fp.read(); assert tuple(round(x, 4) for x in fp.curve.c) == c, fp.curve.c
# undo
ed.undo(); p = ed.comp.layers[0].props["position"]; assert abs(p.raw_at(15)[0] - 50) < 1e-6, p.raw_at(15)
# single selected key -> outgoing segment; Back Out overshoot
ed.sel_keys = {(l.id, "position", 30)}; ed.changed.emit()
assert len(ed.curve_segments()) == 1
fp.set_curve((0.34, 1.56, 0.64, 1.0)); fp.apply()
p = ed.comp.layers[0].props["position"]; assert max(p.raw_at(f)[0] for f in range(30, 61)) > 200
# parse
from sfx.ui.flow import parse_curve
assert parse_curve("cubic-bezier(.33,0,.67,1)") == (0.33, 0, 0.67, 1)
assert parse_curve("abc") is None
fp.edit.setText("0.4, 0, 0.2, 1"); fp._edit_done(); assert fp.curve.c == [0.4, 0, 0.2, 1]
# user presets
fp.save_preset("Мой"); from sfx.ui.flow import load_user_presets
assert load_user_presets()[0][0] == "Мой"; n = fp.list.count(); fp.delete_preset("Мой"); assert fp.list.count() == n - 1
fp.search.setText("expo"); assert fp.list.count() == 3; fp.search.setText("")
# workspace / F4
win.set_workspace("animation"); app.processEvents(); assert fp.isVisible()
win.set_workspace("default"); app.processEvents(); assert not fp.isVisible()
win.show_flow(); app.processEvents(); assert fp.isVisible()
fp.list.item(0)  # exists
# drag a handle in curve widget
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
cw = fp.curve; h = cw._to_px(cw.c[0], cw.c[1]).toPoint()
QTest.mousePress(cw, Qt.LeftButton, Qt.NoModifier, h); QTest.mouseMove(cw, h + type(h)(10, -10)); QTest.mouseRelease(cw, Qt.LeftButton, Qt.NoModifier, h + type(h)(10, -10))
assert cw.c[0] != 0.4
# hotkey labels translated
txt = [t for m, t, k in win.actions_list if "Flow" in t]
print(lang, txt)
win.grab().save(f"/tmp/sfx-tests/flow_{lang}.png")
win.timeline.show_props("position"); win.toggle_graph(); win.timeline.speed_graph=True; app.processEvents(); win.grab(); win.timeline.speed_graph=False; app.processEvents(); win.grab().save(f"/tmp/sfx-tests/flow_graph_{lang}.png")
print("OK", lang)
