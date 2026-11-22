# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Сквозной тест Roto Brush: панель → процесс SAM 2 → маски → альфа слоя в рендере. Нужен .venv-roto."""
import os, sys, time, shutil
os.environ["QT_QPA_PLATFORM"] = "offscreen"; os.environ["XDG_CACHE_HOME"] = "/tmp/sfx-tests/xdgcache-roto"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1] if len(sys.argv) > 1 else "ru"
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage
app = QApplication([])
import numpy as np
from sfx.editor import Editor
from sfx.render import render_frame
from sfx.ui.roto_panel import RotoPanel, roto_available, session_dir
from sfx.effects import to_array
assert roto_available(), "нет .venv-roto"

def wait(cond, sec=600):
    t = time.time()
    while not cond():
        app.processEvents(); time.sleep(0.01)
        if time.time() - t > sec: raise TimeoutError
ed = Editor(); ed.set_idle_cache(False); ed.proxies.enabled = False
ed.add_media_layer(os.path.expanduser("~/Videos/Replay_2026-10-31_00-14-28.mp4")); l = ed.comp.layers[0]; ed.select([l.id])
l.in_point = l.start + 840 * ed.comp.fps / 60; l.out_point = l.in_point + 60   # 1 с исходника @60
shutil.rmtree(session_dir(l), ignore_errors=True)
ed.set_time(int(l.in_point))            # штрих подобран под первый кадр; авто-слежение проходит все кадры
panel = RotoPanel(ed); panel.show()
t0 = time.time(); panel.start(); wait(lambda: panel.meta is not None and not panel._busy)
print("сессия:", panel.meta["n"], "кадров", round(time.time() - t0, 1), "с")
# штрих по красному бойцу сверху вниз (торс → ноги) и «минус» по полу ринга
panel.on_stroke([(1115, 470), (1115, 560), (1110, 680), (1100, 800)], True); wait(lambda: not panel._busy and not panel._queue)
panel.on_stroke([(1500, 850), (1600, 900)], False); wait(lambda: not panel._busy and not panel._queue)
assert panel.chk_auto.isChecked()          # «Авто»: после штриха нейросеть сама прошла все кадры
print("авто-слежение, статус:", panel.status.text()[:70])
masks = [f for f in os.listdir(os.path.join(panel.session, "masks"))]
assert len(masks) >= panel.meta["n"] - 1, len(masks)
assert os.path.exists(os.path.join(panel.session, "masks", "00000.png")), "назад до первого кадра не отследило"
fx = next(f for f in l.effects if f["type"] == "roto")
assert fx["opts"]["session"] == panel.session
# рендер: вокруг бойца прозрачно → над чёрным фоном композиции почти чёрный кадр, сам боец виден
mid = int(l.in_point) + 30
img = render_frame(ed.comp, mid, ed.pool, 0.5)
a = to_array(img.convertToFormat(QImage.Format_ARGB32_Premultiplied))
lum = a[..., :3].mean(axis=2)
dark = (lum < 8).mean()
print("доля почти чёрного (вырезанный фон): %.2f" % dark)
assert 0.6 < dark < 0.99, dark
l.props[f"fx/{fx['id']}/show"].value = [2.0]
render_frame(ed.comp, mid, ed.pool, 0.5).save("/tmp/sfx-tests/roto_overlay.png")
l.props[f"fx/{fx['id']}/show"].value = [1.0]
render_frame(ed.comp, mid, ed.pool, 0.5).save("/tmp/sfx-tests/roto_matte.png")
l.props[f"fx/{fx['id']}/show"].value = [0.0]
# баг 2: при «Смешивании кадров» и замедлении маска тоже должна применяться
from sfx.model import Property
l.frame_blend = True; ed.cache.clear()
fps_v = panel.meta["fps"]
l.props["time_remap"] = Property("time_remap", [(panel.meta["start"] + 20.5) / fps_v])   # между кадрами 20 и 21 сессии
a = to_array(render_frame(ed.comp, mid, ed.pool, 0.5).convertToFormat(QImage.Format_ARGB32_Premultiplied))
dark = (a[..., :3].mean(axis=2) < 8).mean()
print("frame_blend, доля почти чёрного: %.2f" % dark)
assert 0.6 < dark < 0.99, dark
panel.close()
print("OK", i18n.LANG)
