# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""Сквозной тест 3D-трекера: окно → процесс OpenCV/SciPy → камера/нули/текст в SFX → JSX для AE.
Видео с известной камерой делает tests/make_synth_track.py. Нужен .venv-track."""
import os, sys, time, subprocess, json, math
os.environ["QT_QPA_PLATFORM"] = "offscreen"; os.environ["XDG_CACHE_HOME"] = "/tmp/sfx-tests/xdgcache-track"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1] if len(sys.argv) > 1 else "ru"
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter
app = QApplication([])
import numpy as np
from sfx.editor import Editor
from sfx.render import render_frame
from sfx.ui.tracker_panel import TrackerPanel, tracker_available
assert tracker_available()
T = "/tmp/sfx-tests"; os.makedirs(T, exist_ok=True)
vid, gt = f"{T}/synth_track.mp4", f"{T}/synth_track_gt.json"
if not os.path.exists(vid):
    subprocess.run([os.path.expanduser("~/sfx/.venv-track/bin/python"), os.path.expanduser("~/sfx/tests/make_synth_track.py"), vid, gt], check=True)
def wait(cond, sec=900):
    t = time.time()
    while not cond():
        app.processEvents(); time.sleep(0.01)
        if time.time() - t > sec: raise TimeoutError
ed = Editor(); ed.set_idle_cache(False); ed.proxies.enabled = False
ed.comp.width, ed.comp.height, ed.comp.fps = 1280, 720, 30.0
ed.add_media_layer(vid); l = ed.comp.layers[0]; ed.select([l.id])
panel = TrackerPanel(ed); panel.show()
panel.cb_q.setCurrentIndex(0)          # «Быстро»
t0 = time.time(); panel.run(); wait(lambda: panel.sol is not None or panel.btn_run.isEnabled() and panel.proc.state() == 0)
assert panel.sol is not None, panel.info.text()
d = panel.sol.d
print("решение:", d["solved"], "/", d["n"], "кадров, RMS %.3f px, f %.1f (истина 900), %.0f с" % (d["rms_px"], d["f"], time.time() - t0))
print({k: d[k] for k in ("solved", "n", "rms_px", "f", "fov_h", "init", "screen_locked")}); assert d["solved"] >= d["n"] * 0.95 and d["rms_px"] < 1.5 and abs(d["f"] - 900) / 900 < 0.05
# выбираем 25 точек с наименьшей ошибкой на кадре 0 → плоскость, камера, нули, текст
panel._show(0)
best = sorted(panel.canvas.pts, key=lambda p: p[3])[:25]
panel.canvas.sel = [p[0] for p in best]
panel.set_ground(); panel.make_camera(); panel.make_nulls()
panel.ed_text.setText("SFX"); panel.make_text()
txt = next(x for x in ed.comp.layers if x.kind == "text")
assert txt.text == "SFX" and txt.threed, (txt.text, txt.threed)
from sfx.three import layer_homography
c0 = layer_homography(txt, ed.comp, 0).map(0.0, 0.0); c1 = layer_homography(txt, ed.comp, 149).map(0.0, 0.0)
print("текст на экране: кадр 0 → %s, кадр 149 → %s" % (tuple(round(v) for v in c0), tuple(round(v) for v in c1)))
assert abs(c0[0] - c1[0]) + abs(c0[1] - c1[1]) > 30, "текст не движется вместе со сценой"
cams = [x for x in ed.comp.layers if x.kind == "camera"]
assert cams and len(cams[0].props["position"].keyframes) >= d["solved"] - 2, "камера без ключей"
assert sum(1 for x in ed.comp.layers if x.kind == "null") == 25
jsx = f"{T}/camtrack_test.jsx"
from sfx.camtrack import export_jsx
n = export_jsx(jsx, panel.sol, 1280, 720, 30.0, panel.canvas.sel[:3], cloud=50)
js = open(jsx).read(); assert "addCamera" in js and "setValuesAtTimes" in js and n == d["solved"]
data = json.loads(js[js.index("var D = ") + 8: js.index(";\n", js.index("var D = "))])
assert len(data["ori"]) == n and len(data["nulls"]) == 3
# кадры с текстом на найденной плоскости: должен «стоять» на месте в сцене
sheet = QImage(3 * 426, 240, QImage.Format_RGB32); p = QPainter(sheet)
for k, f in enumerate((0, 75, 149)):
    p.drawImage(k * 426, 0, render_frame(ed.comp, f, ed.pool, 0.333).scaled(426, 240))
p.end(); sheet.save(f"{T}/tracker_text.png")
panel.close(); print("OK", i18n.LANG)
