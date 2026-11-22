# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
import os, sys, time
os.environ["QT_QPA_PLATFORM"] = "offscreen"; os.environ["XDG_CACHE_HOME"] = "/tmp/sfx-tests/xdgcache"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1]
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter
app = QApplication([])
from sfx.editor import Editor
from sfx.render import render_frame
from sfx.effects import FILTERS, EFFECTS
ed = Editor()
ed.add_media_layer(os.path.expanduser("~/Videos/Replay_2026-10-31_00-14-28.mp4")); l = ed.comp.layers[0]; ed.select([l.id])
F = 1500
base = render_frame(ed.comp, F, ed.pool, 0.25)
tiles = [("orig", base)]
for t in FILTERS:
    assert ed.add_effect(t), t
    fid = l.effects[-1]["id"]
    if t == "threshold": pass
    t0 = time.perf_counter(); img = render_frame(ed.comp, F, ed.pool, 0.5); dt = (time.perf_counter() - t0) * 1000
    print(f"{t:12s} {EFFECTS[t].label:28s} {dt:6.1f} ms (half)")
    tiles.append((t, render_frame(ed.comp, F, ed.pool, 0.25)))
    ed.remove_effect(l, fid)
W, H = base.width(), base.height()
sheet = QImage(W * 4, H * 4, QImage.Format_RGB32); sheet.fill(0)
p = QPainter(sheet)
for i, (n, im) in enumerate(tiles):
    x, y = (i % 4) * W, (i // 4) * H
    p.drawImage(x, y, im); p.setPen(0xffff00); p.drawText(x + 6, y + 16, n)
p.end(); sheet.save("/tmp/sfx-tests/" + __import__("os").path.basename(sys.argv[0]).replace("test_filters.py", "filters.png"))
print("OK", sys.argv[1])
