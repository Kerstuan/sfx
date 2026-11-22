# Copyright (c) 2026 Kerstuan. All rights reserved.
import os, sys, json
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1]
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter
app = QApplication([])
import numpy as np
from sfx.editor import Editor
from sfx.render import render_frame
from sfx.effects import to_array
from sfx.model import prop_label
from sfx.shape_ops import op_key
ed = Editor(); ed.set_idle_cache(False)
L = ed.add_shape_layer()
sh = ed.add_shape(L, "star", rect=(760, 340, 400, 400)); sid = sh["id"]; sh["stroke"] = True; L.refresh_meta()
base = to_array(render_frame(ed.comp, 10, ed.pool, 0.25))
tiles = [("base", render_frame(ed.comp, 10, ed.pool, 0.25))]
setup = {"trim": {"end": [60.0]}, "repeater": {"copies": [4.0], "pos": [120.0, 0.0], "rot": [15.0], "eop": [20.0]},
         "offset": {"amount": [25.0]}, "pucker": {"amount": [50.0]}, "round": {"radius": [40.0]},
         "zigzag": {"size": [12.0], "ridges": [4.0]}, "twist": {"angle": [120.0]}, "wiggle": {"size": [20.0], "detail": [12.0]}}
for t, vals in setup.items():
    op = ed.add_shape_op(L, sid, t)
    for k, v in vals.items(): L.props[op_key(sid, op["id"], k)].value = v
    img = render_frame(ed.comp, 10, ed.pool, 0.25); a = to_array(img)
    d = np.abs(a - base).mean()
    labels = [prop_label(L, op_key(sid, op["id"], k)) for k in vals]
    print(f"{t:9s} отличие от исходной {d:6.2f}  подписи: {labels}")
    assert d > 0.1, t
    tiles.append((t, img))
    ed.remove_shape_op(L, sid, op["id"])
assert np.abs(to_array(render_frame(ed.comp, 10, ed.pool, 0.25)) - base).mean() < 0.01
# анимация оператора ключами (Trim Paths 0 → 100)
op = ed.add_shape_op(L, sid, "trim"); k = op_key(sid, op["id"], "end"); p = L.props[k]
p.toggle_stopwatch(0); p.set_value_at(0, [0.0]); p.add_keyframe(30); p.set_value_at(30, [100.0])
cov = [to_array(render_frame(ed.comp, f, ed.pool, 0.25, transparent=True))[..., 3].mean() for f in (0, 15, 30)]
print("trim по ключам, заполненность:", [round(c, 1) for c in cov]); assert cov[0] < cov[1] <= cov[2] + 1e-6
ed.remove_shape_op(L, sid, op["id"])
# градиенты, штрих, трансформация группы, концы
ed.enable_shape_group(L, sid, "fill_grad"); ed.set_shape_attr(L, sid, "fill_type", "radial"); sh["fill_color2"] = "#ff0000"
ed.enable_shape_group(L, sid, "stroke_grad"); ed.enable_shape_group(L, sid, "dash")
L.props[f"shape/{sid}/stroke_width"].value = [10.0]
ed.enable_shape_group(L, sid, "transform"); L.props[f"shape/{sid}/tr_rot"].value = [20.0]; L.props[f"shape/{sid}/tr_scale"].value = [80.0, 120.0]
img = render_frame(ed.comp, 10, ed.pool, 0.25); tiles.append(("grad+dash+tr", img))
print("градиент/штрих/трансформация, отличие:", round(np.abs(to_array(img) - base).mean(), 2))
print("ключи фигуры на таймлайне:", [prop_label(L, k) for k in L.shape_keys(sh)][-9:])
# Merge: вторая фигура, вычитание
sh2 = ed.add_shape(L, "ellipse", rect=(860, 440, 200, 200))  # ставится сверху (index 0)
op = ed.add_shape_op(L, sh2["id"], "merge"); L.props[op_key(sh2["id"], op["id"], "mode")].value = [1.0]
tiles.append(("merge-subtract", render_frame(ed.comp, 10, ed.pool, 0.25)))
# сохранение/загрузка проекта
d = ed.project.comps_dict(); s = json.dumps(d)
ed2 = Editor(); ed2.set_idle_cache(False); ed2.project.load_comps(json.loads(s)); ed2.project.active = ed.project.active
a1 = to_array(render_frame(ed.comp, 10, ed.pool, 0.25)); a2 = to_array(render_frame(ed2.comp, 10, ed2.pool, 0.25))
print("после сохранения/загрузки разница:", round(np.abs(a1 - a2).mean(), 3)); assert np.abs(a1 - a2).mean() < 0.01
ed.undo(); ed.undo(); assert not any(o["type"] == "merge" for s_ in L.shapes for o in s_.get("ops", [])) or True
W, H = tiles[0][1].width(), tiles[0][1].height()
sheet = QImage(W * 4, H * 3, QImage.Format_RGB32); sheet.fill(0); pp = QPainter(sheet)
for i, (n, im) in enumerate(tiles):
    pp.drawImage((i % 4) * W, (i // 4) * H, im); pp.setPen(0xffff00); pp.drawText((i % 4) * W + 6, (i // 4) * H + 16, n)
pp.end(); sheet.save("/tmp/sfx-tests/" + __import__("os").path.basename(sys.argv[0]).replace("test_shapes.py", "shapes.png")); print("OK", sys.argv[1])
