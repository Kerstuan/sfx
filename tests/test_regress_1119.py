# Copyright (c) 2026 Kerstuan. All rights reserved.
# Регрессии 2026-11-19: падение при вводе числа в «Эффектах слоя», Motion Tile по границам слоя,
# Shift+клик — диапазон слоёв, рабочая область (двойной клик = вся композиция, B/N с отменой).
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1] if len(sys.argv) > 1 else "ru"
import numpy as np
from PySide6.QtWidgets import QApplication, QLineEdit
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
app = QApplication([])
from sfx.editor import Editor
from sfx.ui.main_window import MainWindow
from sfx.ui.widgets import ScrubValue
from sfx.model import prop_key
from sfx.render import render_frame
from sfx.effects import to_array
ed = Editor(); w = MainWindow(ed); w.resize(1600, 950); w.show(); app.processEvents()

# --- 1. ввод числа в эффекте + клик в другое место: панель пересобиралась внутри focusOut — SIGSEGV
ed.add_solid("#3080c0"); l = ed.comp.layers[0]; ed.select([l.id])
ed.add_effect("tint"); ed.add_effect("motiontile")
ed.sel_effect = None; w.fx_panel.refresh(); w.set_ec_mode("column"); app.processEvents()
sv = w.fx_panel.inner.findChildren(ScrubValue)[-3]
QTest.mouseClick(sv, Qt.LeftButton, Qt.NoModifier, QPoint(8, sv.height() // 2)); app.processEvents()
le = sv.findChild(QLineEdit); assert le is not None and le.hasFocus(), "поле ввода не открылось"
le.setText("150")
QTest.mouseClick(w.timeline, Qt.LeftButton, Qt.NoModifier, QPoint(300, 200)); app.processEvents(); app.processEvents()
fx = l.effects[-1]; key = prop_key(fx["id"], "")
assert any(l.props[key + k].value[0] == 150.0 for k in ("tw", "th", "ow", "oh")), "значение не применилось"

# --- 2. Motion Tile: плитка = слой; 100% вывода — только слой, 300% — заполняет кадр вокруг
ed.select([l.id]); ed.delete_selected() if hasattr(ed, "delete_selected") else None
ed.add_solid("#ff0000"); s = ed.comp.layers[0]; ed.select([s.id])
s.props["scale"].value = [40.0, 40.0]
ed.add_effect("motiontile"); fx = s.effects[-1]; key = prop_key(fx["id"], "")
for other in ed.comp.layers[1:]:
    other.visible = False
ed.comp.bg = "#000000"
a = to_array(render_frame(ed.comp, 0, ed.pool, 0.25))
lit = a[..., 2] > 128   # ARGB32 в памяти — BGRA, красный = канал 2
h, wd = lit.shape
assert not lit[0, 0] and lit[h // 2, wd // 2], "при 100% вывода плитка должна быть только в границах слоя"
s.props[key + "ow"].value = [300.0]; s.props[key + "oh"].value = [300.0]
b = to_array(render_frame(ed.comp, 0, ed.pool, 0.25))
assert (b[..., 2] > 128).mean() > 0.95, "при 300% плитки должны заполнить кадр"

# --- 3. Shift+клик по имени слоя — все слои между якорем и щелчком
ed2 = Editor(); w2 = MainWindow(ed2); w2.resize(1600, 950); w2.show(); app.processEvents()
for c in ("#111111", "#222222", "#333333", "#444444", "#555555"):
    ed2.add_solid(c)
ids = [x.id for x in ed2.comp.layers]
tl = w2.timeline; tl.update(); app.processEvents(); tl.repaint()
names = {h[2].id: h[1] for h in tl._hits if h[0] == "name"}
assert len(names) == 5, names
c = lambda lid: names[lid].center().toPoint()
QTest.mouseClick(tl, Qt.LeftButton, Qt.NoModifier, c(ids[0])); app.processEvents()
QTest.mouseClick(tl, Qt.LeftButton, Qt.ShiftModifier, c(ids[3])); app.processEvents()
assert set(ed2.sel_layers) == set(ids[0:4]), ("Shift+клик не взял слои между", ed2.sel_layers)
QTest.mouseClick(tl, Qt.LeftButton, Qt.ShiftModifier, c(ids[1])); app.processEvents()   # якорь — первый слой
assert set(ed2.sel_layers) == set(ids[0:2]) or set(ids[0:2]) <= set(ed2.sel_layers), ed2.sel_layers
ed2.select([ids[4]]); ed2.select_range(ids[2])
assert set(ed2.sel_layers) == set(ids[2:5]), ed2.sel_layers

# --- 4. рабочая область: B — с отменой, двойной клик по полосе — вся композиция
comp = ed2.comp; ed2.set_time(30); ed2.set_work("in")
assert comp.work_in == 30
ed2.undo(); assert ed2.comp.work_in == 0, "B не отменяется"
ed2.set_time(30); ed2.set_work("in"); comp = ed2.comp
QTest.mouseDClick(tl, Qt.LeftButton, Qt.NoModifier, QPoint(tl.width() - 40, 5)); app.processEvents()
assert (ed2.comp.work_in, ed2.comp.work_out) == (0, ed2.comp.duration), (ed2.comp.work_in, ed2.comp.work_out)
# --- 5. утечки: меню ПКМ удаляется после закрытия; старые панели «Эффектов слоя» не копятся
from PySide6.QtWidgets import QMenu, QWidget
from PySide6.QtCore import QTimer, QEvent
lay = ed2.comp.layers[0]; ed2.select([lay.id])
n0 = len(w2.findChildren(QMenu))
for _ in range(5):
    QTimer.singleShot(30, lambda: app.activePopupWidget() and app.activePopupWidget().close())
    w2.layer_menu(QPoint(100, 100), lay)
app.processEvents(); app.sendPostedEvents(None, QEvent.DeferredDelete)
assert len(w2.findChildren(QMenu)) == n0, ("меню ПКМ копятся", n0, len(w2.findChildren(QMenu)))
for _ in range(10):
    w2.fx_panel.refresh(); app.processEvents()
alive = sum(1 for x in app.allWidgets() if x is w2.fx_panel._retired)
assert alive <= 1, "старая панель должна быть одна"

print("OK", i18n.LANG)
sys.stdout.flush(); os._exit(0)   # без flush вывод в pipe (run_all.sh) теряется
