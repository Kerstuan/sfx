# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
import os, sys, time
os.environ["QT_QPA_PLATFORM"] = "offscreen"; os.environ["XDG_CACHE_HOME"] = "/tmp/sfx-tests/xdgcache"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1]
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter
app = QApplication([])
import numpy as np
from sfx.editor import Editor
from sfx.render import render_frame
from sfx.effects import EFFECTS
ed = Editor(); ed.set_idle_cache(False); ed.proxies.enabled = False
ed.add_media_layer(os.path.expanduser("~/Videos/Replay_2026-10-31_00-14-28.mp4")); l = ed.comp.layers[0]; ed.select([l.id])
F = 900
tiles = [("orig", render_frame(ed.comp, F, ed.pool, 0.25))]
setup = {"blurmo": {"zdist": [1.6], "rotate": [12.0], "shutter": [1.0]}, "puddle": {"amp": [0.6], "freq": [2.0], "chroma": [0.3]},
         "distchroma": {"amount": [2.0]}, "randedits": {}}
for t, vals in setup.items():
    ed.select([l.id]); assert ed.add_effect(t), t
    fid = l.effects[-1]["id"]
    for k, vv in vals.items(): l.props[f"fx/{fid}/{k}"].value = vv
    t0 = time.perf_counter(); render_frame(ed.comp, F, ed.pool, 0.5); dt = (time.perf_counter() - t0) * 1000
    print(f"{t:11s} {EFFECTS[t].label:40s} {dt:6.0f} мс (половина)")
    tiles.append((t, render_frame(ed.comp, F, ed.pool, 0.25)))
    if t == "randedits":
        a = render_frame(ed.comp, F, ed.pool, 0.1); b = render_frame(ed.comp, F + 7, ed.pool, 0.1)
        from sfx.effects import random_edit_offset
        print("  сдвиги:", [round(random_edit_offset(l, f, 60), 2) for f in (0, 6, 12, 18)])
    ed.remove_effect(l, fid)
W, H = tiles[0][1].width(), tiles[0][1].height()
sheet = QImage(W * 3, H * 2, QImage.Format_RGB32); sheet.fill(0); p = QPainter(sheet)
for i, (n, im) in enumerate(tiles):
    p.drawImage((i % 3) * W, (i // 3) * H, im); p.setPen(0xffff00); p.drawText((i % 3) * W + 6, (i // 3) * H + 16, n)
p.end(); sheet.save("/tmp/sfx-tests/" + __import__("os").path.basename(sys.argv[0]).replace("test_sapph.py", "sapph.png")); print("OK", sys.argv[1])
