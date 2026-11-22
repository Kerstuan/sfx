# Copyright (c) 2026 Kerstuan. All rights reserved.
import os, sys, time
os.environ["QT_QPA_PLATFORM"] = "offscreen"; os.environ["XDG_CACHE_HOME"] = "/tmp/sfx-tests/xdgcache"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1]
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter
app = QApplication([])
from sfx.editor import Editor
from sfx.render import render_frame
from sfx.effects import EFFECTS
import sfx.ui.effect_controls  # noqa: F401
ed = Editor(); ed.set_idle_cache(False); ed.proxies.enabled = False
ed.add_media_layer(os.path.expanduser("~/Videos/Replay_2026-10-31_00-14-28.mp4")); l = ed.comp.layers[0]; ed.select([l.id])
F = 900
tiles = [("orig", render_frame(ed.comp, F, ed.pool, 0.25))]
# (тип, подпись, значения параметров)
CASES = [
    ("grunge", "grunge default", {}),
    ("grunge", "grunge clouds blue", {"s1": [1.0], "s1_c1": [120.0, 170.0, 190.0], "s1_c2": [235.0, 245.0, 250.0],
                                      "s2": [7.0], "s2_c1": [150.0, 190.0, 210.0], "s2_c2": [255.0, 255.0, 255.0],
                                      "density": [120.0], "size": [3.0], "frame_amt": [0.6]}),
    ("grunge", "grunge scratch+grit", {"s1": [5.0], "s2": [4.0], "s1_c2": [255.0, 255.0, 255.0],
                                       "s2_c2": [200.0, 200.0, 190.0], "blink": [0.8]}),
    ("grunge", "grunge splatter", {"s1": [6.0], "s1_c1": [90.0, 0.0, 0.0], "s1_c2": [160.0, 10.0, 10.0], "s2": [0.0],
                                   "density": [60.0]}),
    ("warpwaves", "warpwaves default", {}),
    ("warpwaves", "warpwaves video 3:30", {"amp": [-0.178], "angle": [0.0]}),
    ("warpvortex", "vortex 1.5", {"vstart": [1.5]}),
    ("warpvortex", "vortex -4 inner 200", {"vstart": [-4.0], "inner": [200.0]}),
    ("hotspots", "hotspots 0.71", {"thresh": [0.5]}),
    ("halftone", "halftone 11:10", {}),
    ("halftonecolor", "halftonecolor CMY", {"freq": [110.0]}),
    ("halftonecolor", "halftonecolor RGB", {"mode": [1.0], "freq": [90.0]}),
    ("digitaldamage", "digitaldamage", {"intensity": [2.0]}),
    ("filmdamage", "filmdamage 12:30", {"grain": [1.99], "shake": [0.24], "flicker": [1.83]}),
    ("jpegdamage", "jpegdamage 0.056", {}),
    ("jpegdamage", "jpeg errors res4", {"errrate": [1.5], "res": [4.0], "quality": [0.03]}),
    ("tvdamage", "tvdamage 13:30", {}),
    ("tvdamage", "tvdamage bw ffwd", {"mode": [1.0], "ffwd": [1.0], "drop": [1.0], "master": [0.8]}),
    ("sglow", "S_Glow default", {}),
    ("sglow", "S_Glow wide atmo", {"width": [1.2], "atmo": [0.8], "thresh": [0.3]}),
    ("sblur", "S_Blur default", {}),
    ("sblur", "S_Blur box horiz", {"filter": [0.0], "rely": [0.0], "amount": [1.0]}),
    ("sdistort", "S_Distort texture", {}),
    ("sdistort", "S_Distort self luma", {"lens": [0.0], "amount": [3.0]}),
    ("edgerays", "S_EdgeRays", {"length": [0.4]}),
    ("lensflare", "S_LensFlare classic", {}),
    ("lensflare", "S_LensFlare anamorphic", {"lens": [1.0], "hotspot": [1400.0, 300.0]}),
    ("lensflare", "S_LensFlare sun", {"lens": [2.0]}),
    ("warpwaves", "warpwaves black+zoom", {"wrapx": [0.0], "wrapy": [0.0], "zdist": [0.8], "freq": [3.0]}),
]
for t, name, vals in CASES:
    ed.select([l.id]); assert ed.add_effect(t), t
    fid = l.effects[-1]["id"]
    for k, vv in vals.items(): l.props[f"fx/{fid}/{k}"].value = vv
    t0 = time.perf_counter(); render_frame(ed.comp, F, ed.pool, 0.5); dt = (time.perf_counter() - t0) * 1000
    t0 = time.perf_counter(); render_frame(ed.comp, F, ed.pool, 1.0); dt1 = (time.perf_counter() - t0) * 1000
    print(f"{name:22s} {EFFECTS[t].label:32s} {dt:6.0f} мс (1/2)  {dt1:6.0f} мс (полное)")
    tiles.append((name, render_frame(ed.comp, F, ed.pool, 0.25)))
    assert tiles[-1][1] != tiles[0][1], f"{name}: эффект не изменил кадр"
    ed.remove_effect(l, fid)
# генераторы — на чёрном солиде, как в видео (6:15)
ed.add_solid("#000000", "Black Solid 1"); sol = next(x for x in ed.comp.layers if x.name == "Black Solid 1"); ed.select([sol.id])
for name, vals in [("bokeh default (solid)", {}),
                   ("bokeh red streaks 6:55", {"bright": [4.7], "color": [255.0, 0.0, 0.0], "size": [195.0], "relh": [0.03],
                                               "shiftsx": [2280.0]})]:
    ed.select([sol.id]); assert ed.add_effect("bokeh")
    fid = sol.effects[-1]["id"]
    for k, vv in vals.items(): sol.props[f"fx/{fid}/{k}"].value = vv
    t0 = time.perf_counter(); render_frame(ed.comp, F, ed.pool, 1.0); dt1 = (time.perf_counter() - t0) * 1000
    print(f"{name:22s} {EFFECTS['bokeh'].label:32s} {dt1:6.0f} мс (полное)")
    tiles.append((name, render_frame(ed.comp, F, ed.pool, 0.25)))
    a = render_frame(ed.comp, F + 10, ed.pool, 0.25)
    assert a != tiles[-1][1], "огни должны двигаться"
    ed.remove_effect(sol, fid)
# лучи на текстовом слое поверх чёрного солида (8:30)
from sfx.render import update_text_size
ed.add_text(); tl = ed.comp.layers[0]; tl.text = "TEXT LAYER"; tl.font_size = 150; tl.bold = True; update_text_size(tl)
tl.props["position"].value = [ed.comp.width / 2, ed.comp.height / 2]
for name, vals in [("rays white 8:40", {}), ("rays red 8:55", {"color": [255.0, 0.0, 0.0]}),
                   ("glint 9:10", {"_t": "glint"}),
                   ("rays rgb+shimmer", {"lr": [1.4], "lb": [0.7], "shimmer": [0.7], "length": [0.6]})]:
    ft = vals.pop("_t", "rays")
    ed.select([tl.id]); assert ed.add_effect(ft)
    fid = tl.effects[-1]["id"]
    for k, vv in vals.items(): tl.props[f"fx/{fid}/{k}"].value = vv
    t0 = time.perf_counter(); render_frame(ed.comp, F, ed.pool, 1.0); dt1 = (time.perf_counter() - t0) * 1000
    print(f"{name:22s} {EFFECTS[ft].label:32s} {dt1:6.0f} мс (полное)")
    tiles.append((name, render_frame(ed.comp, F, ed.pool, 0.25)))
    ed.remove_effect(tl, fid)
W, H = tiles[0][1].width(), tiles[0][1].height()
cols = 3; rows = (len(tiles) + cols - 1) // cols
sheet = QImage(W * cols, H * rows, QImage.Format_RGB32); sheet.fill(0); p = QPainter(sheet)
for i, (n, im) in enumerate(tiles):
    p.drawImage((i % cols) * W, (i // cols) * H, im); p.setPen(0xffff00); p.drawText((i % cols) * W + 6, (i // cols) * H + 16, n)
p.end(); sheet.save(f"/tmp/sfx-tests/sapph2_{sys.argv[1]}.png"); print("OK", sys.argv[1])
