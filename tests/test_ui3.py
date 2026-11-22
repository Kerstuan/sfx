# Copyright (c) 2026 Kerstuan. All rights reserved.
import os, sys, time, tempfile
os.environ["QT_QPA_PLATFORM"] = "offscreen"; os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1]
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtTest import QTest
app = QApplication([]); app.setStyle("Fusion")
from sfx.ui.theme import STYLESHEET; app.setStyleSheet(STYLESHEET)
from sfx.editor import Editor
from sfx.ui.main_window import MainWindow
ed = Editor(); win = MainWindow(ed); win.resize(1600, 950); win.show(); ed.set_idle_cache(False); app.processEvents()
for i in range(14): ed.add_solid("#%02x4040" % (i * 15))
app.processEvents()
tl = win.timeline
# рабочая среда: поменять местами, таймлайн сверху, сохранить, переключить, вернуться
win.set_columns_swapped(True); win.set_timeline_on_top(True); app.processEvents()
assert win.top.indexOf(win.right) == 0 and win.main_split.indexOf(win.timeline_panel) == 0
win.main_split.setSizes([700, 250]); app.processEvents()
win.save_workspace("Моя1"); assert "user:Моя1" in win.ws_btns
win.set_workspace("default"); app.processEvents()
assert win.top.indexOf(win.left_tabs) == 0 and win.main_split.indexOf(win.timeline_panel) == 1
win.set_workspace("user:Моя1"); app.processEvents()
assert win.top.indexOf(win.right) == 0 and win.main_split.indexOf(win.timeline_panel) == 0
print("рабочая среда ок, размеры", win.main_split.sizes())
win.grab().save("/tmp/sfx-tests/" + __import__("os").path.basename(sys.argv[0]).replace("test_ui3.py", f"ws_{sys.argv[1]}.png"))
win.set_workspace("default"); app.processEvents()
# авто-прокрутка: тянем курсор времени за правый край
tl.ppf = 20; tl.view_start = 0; app.processEvents()
from sfx.ui.timeline import HEADER_H
p0 = QPoint(int(tl.f2x(10)), HEADER_H - 5)
QTest.mousePress(tl, Qt.LeftButton, Qt.NoModifier, p0)
QTest.mouseMove(tl, QPoint(tl.width() + 30, HEADER_H - 5))
t = time.time()
while time.time() - t < 0.6: app.processEvents(); time.sleep(0.01)
QTest.mouseRelease(tl, Qt.LeftButton, Qt.NoModifier, QPoint(tl.width() + 30, HEADER_H - 5))
print("view_start после тяги за край:", round(tl.view_start, 1), "время", ed.time); assert tl.view_start > 5
# T у самого нижнего слоя — прокрутка вниз
tl.view_start = 0; tl.scroll_y = 0
bottom = ed.comp.layers[-1]; ed.select([bottom.id]); tl.show_props("opacity"); app.processEvents(); tl.repaint()
rows = tl.rows(); geo = tl._layout(rows)
ys = [y for (k, l, key), (y, h) in zip(rows, geo) if l is bottom and k == "prop"]
print("строка Opacity нижнего слоя y=", ys, "высота", tl.height()); assert ys and ys[0] + 22 <= tl.height()
# рамка по ключам
s = ed.comp.layers[0]; p = s.props["position"]; p.toggle_stopwatch(20); p.add_keyframe(40); p.add_keyframe(60)
ed.select([s.id]); tl.scroll_y = 0; tl.show_props("position"); ed.deselect(); app.processEvents(); tl.repaint()
kfs = [h for h in tl._hits if h[0] == "kf"]; print("ключей на экране:", len(kfs))
a = kfs[0][1].topLeft() - QPointF(6, 3); b = kfs[-1][1].bottomRight() + QPointF(6, 3)
QTest.mousePress(tl, Qt.LeftButton, Qt.NoModifier, a.toPoint()); QTest.mouseMove(tl, b.toPoint()); QTest.mouseRelease(tl, Qt.LeftButton, Qt.NoModifier, b.toPoint())
print("рамкой выделено ключей:", len(ed.sel_keys)); assert len(ed.sel_keys) == len(kfs) >= 2
# рамка слоёв слева на пустом месте
tl.scroll_y = tl._max_scroll(); app.processEvents(); tl.repaint()
lr = tl._layer_rows(); _g = tl._layout(tl.rows()); last_y = _g[-1][0] + _g[-1][1]
start = QPoint(200, int(last_y + 15)); end = QPoint(200, int(lr[-3][1] + 3))
print("h", tl.height(), "start", start, "end", end, "hit", tl._hit_at(QPointF(start))); QTest.mousePress(tl, Qt.LeftButton, Qt.NoModifier, start); print("drag", tl._drag and tl._drag[0]); QTest.mouseMove(tl, end); print("sel", ed.sel_layers); QTest.mouseRelease(tl, Qt.LeftButton, Qt.NoModifier, end)
print("рамкой слева выделено слоёв:", len(ed.sel_layers)); assert len(ed.sel_layers) >= 3
# перетаскивание имени слоя мышью
tl.scroll_y = 0; ed.deselect(); app.processEvents(); tl.repaint()
lr = tl._layer_rows(); first = lr[0][0]
nh = next(h for h in tl._hits if h[0] == "name" and h[2] is first)
c = nh[1].center().toPoint(); dst = QPoint(c.x(), int(lr[3][1] + lr[3][2] - 2))
QTest.mousePress(tl, Qt.LeftButton, Qt.NoModifier, c); QTest.mouseMove(tl, c + QPoint(0, 8)); QTest.mouseMove(tl, dst); QTest.mouseRelease(tl, Qt.LeftButton, Qt.NoModifier, dst)
print("слой перетащен на позицию", ed.comp.layers.index(first)); assert ed.comp.layers.index(first) == 3
ed.undo(); assert ed.comp.layers.index(ed.comp.layer_by_id(first.id)) == 0
print("OK", sys.argv[1])
