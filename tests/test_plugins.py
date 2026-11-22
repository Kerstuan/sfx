# Copyright (c) 2026 Kerstuan. All rights reserved.
import os, sys, time, tempfile
os.environ["QT_QPA_PLATFORM"] = "offscreen"; os.environ["XDG_CACHE_HOME"] = "/tmp/sfx-tests/xdgcache"
os.environ["SFX_PLUGINS"] = sys.argv[0].rsplit("/", 1)[0] + "/plugtest"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1]
from PySide6.QtWidgets import QApplication
app = QApplication([])
import numpy as np
from sfx import plugins as P
reg = P.load_all()
for pid, info in sorted(reg.items()): print(f"{pid:14s} {info['status']:8s} {info.get('error','')[:60]}")
assert reg["example.scan"]["status"] == "loaded" and reg["crashy"]["status"] == "loaded"
assert reg["future"]["status"] == "error" and any(i["status"] == "error" and "broken" in i["path"] for i in reg.values())
from sfx.editor import Editor
from sfx.ui.main_window import MainWindow
from sfx.render import render_frame
from sfx.effects import to_array, EFFECTS
from sfx.shape_ops import op_key
ed = Editor(); win = MainWindow(ed); ed.set_idle_cache(False)
t = "example.scan/scanlines"; assert t in EFFECTS and EFFECTS[t].label in ("Строки развёртки", "Scanlines")
assert any(a.text() == EFFECTS[t].label for m in win.menuBar().findChildren(type(win.menuBar().actions()[0].menu())) for a in m.actions())
ed.add_solid("#ffffff"); s = ed.comp.layers[0]; ed.select([s.id])
base = to_array(render_frame(ed.comp, 0, ed.pool, 0.25))
assert ed.add_effect(t); a = to_array(render_frame(ed.comp, 0, ed.pool, 0.25)); print("scanlines затемнение:", round(float(base.mean() - a.mean()), 1)); assert base.mean() - a.mean() > 10
# сломанный во время работы — кадр не падает, ошибка запомнена
assert ed.add_effect("crashy/boom"); render_frame(ed.comp, 0, ed.pool, 0.25); print("ошибка плагина:", reg["crashy"].get("runtime_error"))
assert reg["crashy"].get("runtime_error")
# оператор фигуры из плагина
L = ed.add_shape_layer(); sh = ed.add_shape(L, "rect", rect=(600, 400, 700, 200)); L.refresh_meta()
b0 = to_array(render_frame(ed.comp, 0, ed.pool, 0.25, transparent=True))
op = ed.add_shape_op(L, sh["id"], "example.scan/wave"); assert op
L.props[op_key(sh["id"], op["id"], "height")].value = [60.0]
b1 = to_array(render_frame(ed.comp, 0, ed.pool, 0.25, transparent=True)); print("волна на фигуре, отличие:", round(float(np.abs(b1 - b0).mean()), 2)); assert np.abs(b1 - b0).mean() > 0.5
# фоновый процесс тоже знает плагин
ed.start_render_service(1); ed.cache.clear(); ed.notify()
sig = ed.cache_sig(); ed.render_service.request(sig, ed._state_json, 0, ed.preview_scale, False, urgent=True)
t0 = time.time()
while ed.cache.get(sig, 0) is None and time.time() - t0 < 20: app.processEvents(); time.sleep(0.02)
assert ed.cache.get(sig, 0) is not None, "воркер не посчитал кадр с эффектом плагина"
w = to_array(ed.cache.get(sig, 0)); m = to_array(render_frame(ed.comp, 0, ed.pool, ed.preview_scale)); print("воркер vs окно:", round(float(np.abs(w - m).mean()), 3))
assert np.abs(w - m).mean() < 1.0
ed.shutdown()
# выключение
P.set_enabled("example.scan", False); assert "example.scan" in P.disabled_ids(); P.set_enabled("example.scan", True)
print("OK", sys.argv[1])
