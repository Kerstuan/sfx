# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Гизмо 3D-перемещения во вьювере (оси X/Y/Z, как в AE): мышью тянем стрелку — меняется только эта
координата Position; работает у 3D-слоя, света и камеры; нет у 2D-слоя; отмена; ключ при включённом секундомере."""
import os, sys, tempfile, math
os.environ["QT_QPA_PLATFORM"] = "offscreen"; os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1] if len(sys.argv) > 1 else "ru"
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtTest import QTest
app = QApplication([])
import numpy as np
from sfx.editor import Editor
from sfx.render import render_frame
from sfx.effects import to_array
from sfx.ui.main_window import MainWindow

ed = Editor(); w = MainWindow(ed); w.resize(1600, 950); w.show(); app.processEvents()
vw = w.viewer
ed.add_solid("#e0e0e0", width=400, height=300)
s = ed.comp.layers[0]
pos = lambda l: [round(v, 1) for v in l.props["position"].value_at(l.local(ed.time))]

ed.select([s.id]); ed.set_tool("select"); app.processEvents()
assert vw._gizmo() is None, "у 2D-слоя гизмо быть не должно"
ed.set_3d([s]); ed.select([s.id]); app.processEvents(); vw.repaint(); app.processEvents()
g = vw._gizmo()
assert g is not None, "у 3D-слоя нет гизмо"
_, o, axes = g
print("гизмо: начало", o.toTuple(), "оси", [(i, round(d.x(), 2), round(d.y(), 2), round(ppu, 3)) for i, d, ppu in axes])
assert axes[0][1].x() > 0.9, "X должен смотреть вправо"
assert axes[1][1].y() > 0.9, "Y должен смотреть вниз (как в AE)"


def drag_axis(i, px):
    _, o, axes = vw._gizmo()
    d = axes[i][1]
    a = o + d * (vw.GIZMO_LEN * 0.6)
    b = a + d * px
    QTest.mousePress(vw, Qt.LeftButton, Qt.NoModifier, a.toPoint())
    for k in range(1, 6):
        QTest.mouseMove(vw, (a + (b - a) * (k / 5)).toPoint())
    QTest.mouseRelease(vw, Qt.LeftButton, Qt.NoModifier, b.toPoint()); app.processEvents()


# --- X: меняется только x, картинка сдвигается вправо
p0 = pos(s)
before = to_array(render_frame(ed.comp, ed.time, ed.pool, 0.25))[..., :3].mean(2)
drag_axis(0, 60)
p1 = pos(s)
print("X:", p0, "->", p1)
assert p1[0] > p0[0] + 20 and p1[1] == p0[1] and p1[2] == p0[2], (p0, p1)
after = to_array(render_frame(ed.comp, ed.time, ed.pool, 0.25))[..., :3].mean(2)
cx = lambda a: (a.sum(0) * np.arange(a.shape[1])).sum() / a.sum()
assert cx(after) > cx(before) + 2, "картинка не сдвинулась вправо"
# 60 px вида ≈ 60 / масштаб вида пикселей композиции (без перспективы при z=0)
assert abs((p1[0] - p0[0]) - 60 / vw.view_scale()) < 0.25 * 60 / vw.view_scale(), (p1[0] - p0[0], vw.view_scale())

# --- Y: только y
drag_axis(1, 40); p2 = pos(s)
assert p2[1] > p1[1] + 10 and p2[0] == p1[0] and p2[2] == p1[2], (p1, p2)

# --- Z: только z; слой удаляется от камеры → становится меньше
area0 = (to_array(render_frame(ed.comp, ed.time, ed.pool, 0.25))[..., :3].mean(2) > 128).sum()
drag_axis(2, 80); p3 = pos(s)
print("Z:", p2, "->", p3)
assert p3[2] != p2[2] and p3[0] == p2[0] and p3[1] == p2[1], (p2, p3)
area1 = (to_array(render_frame(ed.comp, ed.time, ed.pool, 0.25))[..., :3].mean(2) > 128).sum()
assert (area1 < area0) == (p3[2] > p2[2]), (area0, area1, p2, p3)

# --- отмена: каждое перетаскивание — один шаг
ed.undo(); s = ed.comp.layers[0]; assert pos(s) == p2, (pos(s), p2)
ed.undo(); s = ed.comp.layers[0]; assert pos(s) == p1
ed.undo(); s = ed.comp.layers[0]; assert pos(s) == p0

# --- секундомер включён → перетаскивание ставит ключ на текущем кадре
ed.select([s.id]); s.props["position"].set_value_at(0, s.props["position"].value)
s.props["position"].keyframes and None
ed.add_keyframes("position") if hasattr(ed, "add_keyframes") else None
ed.set_time(30); app.processEvents()
n0 = len(s.props["position"].keyframes)
drag_axis(0, 30)
assert len(s.props["position"].keyframes) == n0 + 1, "ключ не поставился"
assert s.props["position"].value_at(0)[0] == p0[0], "ключ на кадре 0 не должен меняться"

# --- свет и камера
ed.set_time(0)
lt = ed.add_light("point"); ed.select([lt.id]); app.processEvents()
assert vw._gizmo() is not None and vw._gizmo()[0] is lt
l0 = pos(lt); drag_axis(0, 50); l1 = pos(lt)
assert l1[0] > l0[0] and l1[1:] == l0[1:], (l0, l1)
ed.add_camera(); cam = next(l for l in ed.comp.layers if l.kind == "camera"); ed.select([cam.id]); app.processEvents()
c0 = pos(cam)
g = vw._gizmo()
if g is not None:            # камера сама в своём поле зрения не видна: начало оси за ближней плоскостью
    drag_axis(1, 30); assert pos(cam)[1] != c0[1]
else:
    print("камера: гизмо скрыт (камера смотрит из своей точки) — ожидаемо")

# --- кольца поворота (инструмент W): тянем кольцо — меняется только своё свойство; знак = правило правой руки
ed.select([s.id]); ed.set_time(0); ed.set_tool("rotate"); app.processEvents(); vw.repaint(); app.processEvents()
for k in ("rot_x", "rot_y", "rotation", "position"):
    s.props[k].keyframes = []
s.props["rot_x"].value = [0.0]; s.props["rot_y"].value = [0.0]; s.props["rotation"].value = [0.0]
s.props["position"].value = [960.0, 540.0, 0.0]
g = vw._rings(); assert g is not None and len(g[2]) >= 2, "нет колец у 3D-слоя"
_, c, rings = g
rad = {i: max(math.hypot(*(q - c).toTuple()) for q in pts) for i, pts, _, _ in rings}
print("кольца: радиусы на экране", {i: round(r_) for i, r_ in rad.items()})
assert all(abs(r_ - vw.RING_PX) < 0.25 * vw.RING_PX for r_ in rad.values()), rad
import math as _m
def drag_ring(i, px):
    _, c_, rings_ = vw._rings()
    pts = next(p_ for j, p_, _, _ in rings_ if j == i)
    # точка кольца, наиболее удалённая от центра на экране (у ребром стоящих колец — край эллипса)
    a = max(pts[:-1], key=lambda q: math.hypot(*(q - c_).toTuple()))
    ra = vw._ring_at(a); assert ra is not None and ra[1] == i, (i, ra)
    b = a + ra[2] * px
    QTest.mousePress(vw, Qt.LeftButton, Qt.NoModifier, a.toPoint())
    for k in range(1, 6):
        QTest.mouseMove(vw, (a + (b - a) * (k / 5)).toPoint())
    QTest.mouseRelease(vw, Qt.LeftButton, Qt.NoModifier, b.toPoint()); app.processEvents()
import math
before = {k: s.props[k].value_at(0)[0] for k in ("rot_x", "rot_y", "rotation")}
drag_ring(2, 60)                                         # Z-кольцо (видно кругом при виде спереди)
after = {k: s.props[k].value_at(0)[0] for k in ("rot_x", "rot_y", "rotation")}
print("Z-кольцо:", before, "->", after)
assert after["rotation"] > 20 and after["rot_x"] == 0 and after["rot_y"] == 0, after
assert abs(after["rotation"] - _m.degrees(60 / vw.RING_PX)) < 15, (after["rotation"], _m.degrees(60 / vw.RING_PX))
ed.undo(); s = ed.comp.layers[0] if ed.comp.layers[0].kind == "solid" else next(l for l in ed.comp.layers if l.kind == "solid")
assert s.props["rotation"].value_at(0)[0] == 0, "поворот кольцом не отменяется"
# в своём виде видны все три кольца; X-кольцо меняет только X Rotation
w.set_view3d("custom"); ed.select([s.id]); app.processEvents()
assert len(vw._rings()[2]) == 3
drag_ring(0, 40)
assert s.props["rot_x"].value_at(0)[0] != 0 and s.props["rot_y"].value_at(0)[0] == 0 and s.props["rotation"].value_at(0)[0] == 0
drag_ring(1, 40)
assert s.props["rot_y"].value_at(0)[0] != 0 and s.props["rotation"].value_at(0)[0] == 0
w.set_view3d("active"); ed.set_tool("select")
ed.set_3d([s], False); ed.set_tool("rotate"); assert vw._rings() is None, "у 2D-слоя колец быть не должно"
ed.set_tool("select")

# --- без выделения и при другом инструменте гизмо нет / не рисуется
ed.deselect(); assert vw._gizmo() is None
vw.grab().save("/tmp/sfx-tests/gizmo.png") if os.path.isdir("/tmp/sfx-tests") else None
print("OK", i18n.LANG)
sys.stdout.flush(); os._exit(0)
