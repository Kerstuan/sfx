# Copyright (c) 2026 Kerstuan. All rights reserved.
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1]
from PySide6.QtWidgets import QApplication
app = QApplication([]); app.setStyle("Fusion")
from sfx.ui.theme import STYLESHEET; app.setStyleSheet(STYLESHEET)
import numpy as np
from sfx import effects as E
# синтетика: зелёный фон, «человек» — красно-бежевый квадрат, полупрозрачный край с отсветом
arr = np.zeros((100, 100, 4), np.float32); arr[..., 3] = 255
arr[..., :3] = [60, 200, 40]           # BGR зелёный
arr[30:70, 30:70, :3] = [120, 150, 220]  # кожа (BGR)
arr[30:70, 29, :3] = [90, 190, 120]    # край с зелёным отсветом
v = {p.key: (p.default if len(p.default) > 1 else p.default[0]) for p in E.EFFECTS["keylight"].params}
v["screen"] = [40, 200, 60]
out = E._keylight(arr, v, 1.0)
print("фон alpha", out[5, 5, 3], "объект alpha", out[50, 50, 3], "край", out[50, 29, 3])
assert out[5, 5, 3] < 5 and out[50, 50, 3] > 250
# despill: на краю зелёный не больше остальных
e = out[50, 29]; assert e[3] == 0 or e[1] <= max(e[0], e[2]) + 1, e
v["view"] = 1.0; m = E._keylight(arr, v, 1.0); assert m[5, 5, 0] < 5 and m[50, 50, 0] > 250
v["view"] = 0.0; v["soft"] = 3.0; v["shrink"] = -2.0; E._keylight(arr, v, 1.0)
# ramp
r = {p.key: (p.default if len(p.default) > 1 else p.default[0]) for p in E.EFFECTS["ramp"].params}
r["start"], r["end"] = [0, 0], [0, 100]
o = E._ramp(arr, r, 1.0); assert o[0, 50, 0] < 3 and o[99, 50, 0] > 250, (o[0, 50], o[99, 50])
r["shape"] = 1.0; r["scatter"] = 50.0; r["blend"] = 50.0; E._ramp(arr, r, 1.0)
# через программу: сплошной слой + оба эффекта, рендер
from sfx.editor import Editor
from sfx.ui.main_window import MainWindow
from sfx.render import render_frame
ed = Editor(); win = MainWindow(ed); win.resize(1600, 950); win.show(); app.processEvents()
ed.add_solid("#30c040"); l = ed.comp.layers[0]; ed.select([l.id])
ed.add_effect("keylight"); app.processEvents()
img = render_frame(ed.comp, 0, ed.pool, 0.25, transparent=True)
print("solid keyed alpha", img.pixelColor(100, 100).alpha())
assert img.pixelColor(100, 100).alpha() < 10
ed.add_effect("ramp"); app.processEvents()
win.show_effect_controls(); app.processEvents()
names = [a.text() for a in win.menuBar().actions()]
win.grab().save("/tmp/sfx-tests/" + __import__("os").path.basename(sys.argv[0]).replace("test_key.py", f"key_{sys.argv[1]}.png"))
print("OK", sys.argv[1])
# ramp defaults relative
fid = l.effects[-1]["id"]
assert l.props[f"fx/{fid}/start"].value == [960.0, 0.0] and l.props[f"fx/{fid}/end"].value == [960.0, 1080.0]
# eyedropper: красный солид поверх, пипеткой берём цвет у keylight
kid = l.effects[0]["id"]; key = f"fx/{kid}/screen"
ed.start_color_pick(l.id, key)
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt
vw = win.viewer; pt = vw._to_view(__import__("PySide6.QtCore", fromlist=["QPointF"]).QPointF(500, 500)).toPoint()
QTest.mouseClick(vw, Qt.LeftButton, Qt.NoModifier, pt); app.processEvents()
val = l.props[key].value_at(0); print("pick", val)
assert ed.color_pick is None and l.effects[0]["on"]
assert abs(val[0] - 0x30) < 6 and abs(val[1] - 0xc0) < 6 and abs(val[2] - 0x40) < 6, val
print("OK2", sys.argv[1])
