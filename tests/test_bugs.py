# Copyright (c) 2026 Kerstuan. All rights reserved.
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1]
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtTest import QTest
app = QApplication([])
from sfx.editor import Editor
from sfx.ui.main_window import MainWindow
ed = Editor(); win = MainWindow(ed); win.resize(1600, 950); win.show(); app.processEvents()
ed.add_solid("#ff0000"); l = ed.comp.layers[0]
p = l.props["position"]; p.toggle_stopwatch(0); p.add_keyframe(30); p.set_value_at(30, [500, 300])
tl = win.timeline
# --- баг 1: граф, клик по пустому месту
tl.show_props("position"); app.processEvents()
ed.sel_keys = {(l.id, "position", 0), (l.id, "position", 30)}
win.toggle_graph(); app.processEvents()
assert tl.graph_targets()
from sfx.ui import timeline as T
R = tl.track_rect
pt = QPoint(int(R.right() - 20), int(R.bottom() - 10))
QTest.mouseClick(tl, Qt.LeftButton, Qt.NoModifier, pt); app.processEvents()
assert not ed.sel_keys, ed.sel_keys
assert tl.graph_targets(), "кривая пропала после клика по пустому месту"
win.toggle_graph()
# --- баг 2: выделенный эффект + ключи
ed.select([l.id]); ed.add_effect("blur") if hasattr(ed, "add_effect") else None
fid = l.effects[-1]["id"]
ed.sel_keys = {(l.id, "position", 0)}
ed.sel_effect = (l.id, fid); ed.delete_selection()
assert not l.effects, "эффект не удалён"; assert len(p.keyframes) == 2, "ключ удалён вместо эффекта"
# ключи выделены позже эффекта -> удаляются ключи
ed.select([l.id]); ed.add_effect("blur"); fid = l.effects[-1]["id"]
ed.sel_effect = (l.id, fid); ed.sel_keys = {(l.id, "position", 0)}
assert ed.sel_effect is None; ed.delete_selection(); assert l.effects and len(p.keyframes) == 1
# щелчок по значению в Effect Controls выделяет эффект
win.show_effect_controls(); app.processEvents()
ec = win.findChild(type(win), None)
from sfx.ui.effect_controls import EffectControlsPanel
ecp = win.findChildren(EffectControlsPanel)[0]
ecp.refresh(); app.processEvents()
from sfx.ui.widgets import ScrubValue
ed.sel_effect = None; ed.select([l.id]); app.processEvents()
sv = [w for w in ecp.inner.findChildren(ScrubValue)][0]
QTest.mousePress(sv, Qt.LeftButton); QTest.mouseRelease(sv, Qt.LeftButton); app.processEvents()
assert ed.sel_effect == (l.id, fid), ed.sel_effect
n = len(ed.comp.layers); ed.delete_selection()
assert len(ed.comp.layers) == n and not l.effects, "удалился слой вместо эффекта"
print("OK", sys.argv[1])
