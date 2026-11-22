# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""3D-виды вьювера (как 3D View в AE): Active Camera / Front / Left / Top / Back / Right / Bottom / Custom.
Ортогональность (размер не зависит от z), ребро слоя в Top/Left, экспорт всегда через активную камеру,
вложенный прекомп не наследует вид, облёт Custom, значок и гизмо камеры, перемещение мышью по осям вида,
меню F10/F11/F12, выпадающий список, сохранение, старые проекты."""
import os, sys, json, tempfile
os.environ["QT_QPA_PLATFORM"] = "offscreen"; os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1] if len(sys.argv) > 1 else "ru"
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPointF
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
app = QApplication([])
import numpy as np
from sfx.editor import Editor
from sfx.model import Project
from sfx.render import render_frame
from sfx.effects import to_array
from sfx import three
from sfx.ui.main_window import MainWindow

ed = Editor(); w = MainWindow(ed); w.resize(1600, 950); w.show(); app.processEvents()
vw = w.viewer
lum = lambda: to_array(render_frame(ed.comp, ed.time, ed.pool, 0.25))[..., :3].mean(2)
area = lambda: int((lum() > 60).sum())
diff = lambda a, b: float(np.abs(a - b).mean())
solid = lambda: next(l for l in ed.comp.layers if l.kind == "solid")
camera = lambda: next(l for l in ed.comp.layers if l.kind == "camera")

ed.add_solid("#e0e0e0", width=600, height=400)
ed.set_3d([solid()])
ed.add_camera()
active0 = lum(); a_active = area()

# --- Front при z=0 совпадает с видом камеры по умолчанию; при z=+800 в перспективе слой меньше, в Front — нет
w.set_view3d("front"); app.processEvents()
assert ed.comp.view3d == "front" and w.view3d.currentIndex() == three.VIEWS.index("front")
assert diff(lum(), active0) < 2.0, diff(lum(), active0)
solid().props["position"].value = [960.0, 540.0, 800.0]
a_front = area()
w.set_view3d("active"); a_persp = area()
print("площадь: камера z=0 %d, камера z=800 %d, Front z=800 %d" % (a_active, a_persp, a_front))
assert a_persp < 0.75 * a_active, "в перспективе дальний слой должен быть меньше"
assert abs(a_front - a_active) < 0.03 * a_active, "Front ортогональный: размер не зависит от z"

# --- Top / Left: плоский слой смотрит ребром — почти не виден; повёрнутый на 90° по X — виден сверху
solid().props["position"].value = [960.0, 540.0, 0.0]
for v in ("top", "left", "bottom", "right"):
    w.set_view3d(v)
    assert area() < 0.03 * a_active, (v, area())
solid().props["rot_x"].value = [90.0]
w.set_view3d("top"); a_top = area()
sc = ed.comp.view_ortho["top"]["scale"]
print("Top: масштаб после вписывания %.3f" % sc)
assert sc < 0.5, "вписывание должно включить камеру (z≈-1870) — масштаб меньше 1:1"
assert a_top > 0.5 * a_active * sc * sc, ("сверху повёрнутый слой должен быть виден", a_top)
# камера после вписывания — внутри кадра
vp_ = three.view_projection(ed.comp, ed.time)
q_ = vp_ @ np.array(list(three._xyz(camera().props["position"].value)) + [1.0])
assert 0 <= q_[0] / q_[2] <= ed.comp.width and 0 <= q_[1] / q_[2] <= ed.comp.height, "камера вне кадра вида сверху"
# колесо меняет масштаб вида, а не зум картинки вьювера
from PySide6.QtGui import QWheelEvent
from PySide6.QtCore import QPoint
ctr = vw._view_rect().center()
ev = QWheelEvent(ctr, vw.mapToGlobal(ctr), QPoint(0, 0), QPoint(0, 120), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
vz0 = vw.view_scale(); vw.wheelEvent(ev); app.processEvents()
assert ed.comp.view_ortho["top"]["scale"] > sc * 1.05 and abs(vw.view_scale() - vz0) < 1e-9
a_zoom = area(); assert a_zoom > a_top, (a_zoom, a_top)
three.zoom_view(ed.comp, "top", 1 / 1.1); ed.notify()
# C в ортогональном виде сдвигает вид (цель), камера на месте
t0 = list(ed.comp.view_ortho["top"]["target"]); cpos = list(camera().props["position"].value)
ed.set_tool("orbit")
QTest.mousePress(vw, Qt.LeftButton, Qt.NoModifier, ctr.toPoint()); QTest.mouseMove(vw, (ctr + QPointF(40, 0)).toPoint())
QTest.mouseRelease(vw, Qt.LeftButton, Qt.NoModifier, (ctr + QPointF(40, 0)).toPoint()); app.processEvents()
assert ed.comp.view_ortho["top"]["target"][0] < t0[0] - 10 and list(camera().props["position"].value) == cpos
ed.comp.view_ortho["top"]["target"] = t0; ed.set_tool("select"); ed.notify()
w.set_view3d("left"); assert area() < 0.03 * a_active
solid().props["rot_x"].value = [0.0]
w.set_view3d("back"); assert area() > 0.9 * a_active, "сзади слой виден (двусторонний)"

# --- Custom: виден под углом; облёт меняет картинку; наезд
w.set_view3d("custom"); c0 = lum()
assert area() > 0 and diff(c0, active0) > 1.0, "свой вид должен отличаться от вида камеры"
three.orbit_view(ed.comp, 25, 0); ed.notify(); c1 = lum()
assert diff(c1, c0) > 1.0, "облёт не изменил вид"
d0 = three.custom_view(ed.comp)["dist"]; three.dolly_view(ed.comp, 0.5); ed.notify()
assert three.custom_view(ed.comp)["dist"] < d0 and area() > 0

# --- инструмент C в Custom облетает вид, а не камеру
cp0 = list(camera().props["position"].value)
ed.set_tool("orbit"); app.processEvents()
a0 = vw._view_rect().center()
yaw0 = three.custom_view(ed.comp)["yaw"]
QTest.mousePress(vw, Qt.LeftButton, Qt.NoModifier, a0.toPoint())
QTest.mouseMove(vw, (a0 + QPointF(80, 0)).toPoint())
QTest.mouseRelease(vw, Qt.LeftButton, Qt.NoModifier, (a0 + QPointF(80, 0)).toPoint()); app.processEvents()
assert three.custom_view(ed.comp)["yaw"] != yaw0, "C в своём виде должен облетать вид"
assert list(camera().props["position"].value) == cp0, "камера не должна меняться"
ed.set_tool("select")

# --- в Top видна камера: значок, клик выделяет, гизмо двигает её по X
w.set_view3d("top"); ed.deselect(); app.processEvents(); vw.repaint(); app.processEvents()
vp = three.view_projection(ed.comp, ed.time)
q = vp @ np.array(list(three._xyz(camera().props["position"].value)) + [1.0])
ic = vw._to_view(QPointF(q[0] / q[2], q[1] / q[2]))
assert vw._camera_icon_at(ic) is camera()
QTest.mouseClick(vw, Qt.LeftButton, Qt.NoModifier, ic.toPoint()); app.processEvents()
assert ed.sel_layers == [camera().id], ed.sel_layers
g = vw._gizmo()
assert g is not None and g[0] is camera(), "в виде сверху у камеры должен быть гизмо"
_, o, axes = g
dz = axes[2][1]
print("Top: ось Z камеры на экране", round(dz.x(), 2), round(dz.y(), 2))
assert dz.y() < -0.9, "в виде сверху +Z должен смотреть вверх экрана"
x0 = camera().props["position"].value[0]
a = o + axes[0][1] * 40; b = a + axes[0][1] * 50
QTest.mousePress(vw, Qt.LeftButton, Qt.NoModifier, a.toPoint()); QTest.mouseMove(vw, b.toPoint())
QTest.mouseRelease(vw, Qt.LeftButton, Qt.NoModifier, b.toPoint()); app.processEvents()
assert camera().props["position"].value[0] > x0 + 10, "гизмо не сдвинул камеру"
ed.undo()

# --- перемещение 3D-слоя мышью в Top двигает X и Z, а не Y
solid().props["rot_x"].value = [90.0]; ed.select([solid().id]); app.processEvents()
p0 = list(solid().props["position"].value)
q = vp @ np.array(p0 + [1.0]); c = vw._to_view(QPointF(q[0] / q[2], q[1] / q[2]))
c = c + QPointF(-20, 10)                          # мимо стрелок гизмо, внутри слоя
QTest.mousePress(vw, Qt.LeftButton, Qt.NoModifier, c.toPoint()); QTest.mouseMove(vw, (c + QPointF(0, -50)).toPoint())
QTest.mouseRelease(vw, Qt.LeftButton, Qt.NoModifier, (c + QPointF(0, -50)).toPoint()); app.processEvents()
p1 = list(solid().props["position"].value)
print("Top, тянем вверх:", p0, "->", p1)
assert abs(p1[1] - p0[1]) < 0.5 and p1[2] > p0[2] + 10, (p0, p1)

# --- экспорт идёт через активную камеру, даже если во вьювере Top
from sfx.export import ExportThread, RenderSettings, output_path
w.set_view3d("active"); ref = to_array(render_frame(ed.comp, 0, ed.pool, 1.0))[..., :3].astype(float)
w.set_view3d("top")
ed.comp.work_in, ed.comp.work_out = 0, 1
rs = RenderSettings(fmt="png", codec="png", audio=False)
out = output_path(os.path.join(tempfile.mkdtemp(), "v"), rs); rs.path = out
th = ExportThread(json.loads(json.dumps(ed.project.to_dict())), out, settings=rs)
res = []
th.finished_ok.connect(lambda p_: res.append(("ok", p_))); th.failed.connect(lambda m: res.append(("fail", m)))
th.start()
while th.isRunning() or not res:
    app.processEvents(); th.wait(50)
assert res[0][0] == "ok", res
png = os.path.join(os.path.dirname(out), "v_00000.png")
got = to_array(QImage(png).convertToFormat(QImage.Format_ARGB32_Premultiplied))[..., :3].astype(float)
print("экспорт против активной камеры: %.2f" % diff(got, ref))
assert got.shape == ref.shape and diff(got, ref) < 2.0, "экспорт должен идти через активную камеру"
assert ed.comp.view3d == "top", "экспорт не должен менять вид во вьювере"

# --- вид только у открытой композиции: прекомп с видом Top внутри не меняется
w.set_view3d("active")
ed.select([solid().id]); ed.precompose("pc")
outer = ed.comp; pre = next(c_ for c_ in ed.project.comps if c_ is not outer)
before = lum()
pre.view3d = "top"; ed.notify()
assert diff(lum(), before) < 0.5, "вид вложенного прекомпа повлиял на внешнюю композицию"
assert three.view_override(pre) is None

# --- переключение композиции обновляет список; F10/F11/F12
ed.open_comp(pre.id); app.processEvents(); w._update_time_label(ed.time)
assert w.view3d.currentIndex() == three.VIEWS.index("top")
ed.open_comp(outer.id); app.processEvents(); w._update_time_label(ed.time)
assert w.view3d.currentIndex() == 0
acts = {a.text(): a for a in w.findChildren(type(w.menuBar().actions()[0]))}
for key, txt, v in (("F10", "3D-вид: спереди", "front"), ("F11", "3D-вид: свой вид", "custom"),
                    ("F12", "3D-вид: активная камера", "active")):
    a = acts[i18n.tr(txt)]
    assert any(sq.toString() == key for sq in a.shortcuts()), (txt, a.shortcuts())
    a.trigger(); app.processEvents(); assert ed.comp.view3d == v
    if i18n.LANG == "en":
        assert i18n.tr(txt) != txt
assert w.view3d.itemText(3) == i18n.tr("Сверху") and (i18n.LANG == "ru" or w.view3d.itemText(3) == "Top")
assert i18n.LANG == "ru" or not any(ch in w.view3d.toolTip() for ch in "абвгдежз"), "подсказка не переведена"

# --- раскладки 2/4 вида: боковые виды показывают свой вид, проект и главный вид не меняются
w.set_view3d("active"); ed.open_comp(outer.id); app.processEvents()
ed.add_solid("#40c040", width=500, height=300); g3 = ed.comp.layers[0]; ed.set_3d([g3]); g3.props["rot_x"].value = [90.0]
ed.add_camera(); app.processEvents()
state0 = json.dumps(ed.project.to_dict(), sort_keys=True)
sig0 = ed.cache_sig()
w.set_view_layout(4); app.processEvents()
assert w.view_layout == 4 and len(w.side_views) == 3 and all(sv.isVisible() for sv in w.side_views)
assert [sv.view for sv in w.side_views] == ["top", "front", "right"]
for sv in w.side_views:
    sv.refresh()
app.processEvents()
assert all(sv.image() is not None for sv in w.side_views)
top_sv = w.side_views[0]
li = to_array(top_sv.image())[..., :3].mean(2)
print("вид сверху в боковой панели: светлых пикселей", int((li > 60).sum()))
assert (li > 60).sum() > 50, "в виде сверху повёрнутый на 90° слой должен быть виден"
n_cam = sum(1 for l in ed.comp.layers if l.kind == "camera")
assert len(top_sv.camera_marks()) == n_cam >= 1, (len(top_sv.camera_marks()), n_cam)
assert json.dumps(ed.project.to_dict(), sort_keys=True) == state0, "боковой вид изменил проект"
assert ed.cache_sig() == sig0 and ed.comp.view3d == "active"
# масштаб и сдвиг бокового вида — свои, проект не меняется
sc0 = top_sv.ortho["top"]["scale"]; top_sv.zoom(1.5); assert top_sv.ortho["top"]["scale"] > sc0
top_sv.pan(30, 0); assert json.dumps(ed.project.to_dict(), sort_keys=True) == state0
assert ed.comp.view_ortho in (None, {}) or "top" not in (ed.comp.view_ortho or {}) or ed.comp.view_ortho == json.loads(state0)["comps"][0].get("view_ortho")
# смена вида в списке панели
top_sv.combo.setCurrentIndex(three.VIEWS.index("custom")); app.processEvents()
assert top_sv.view == "custom" and top_sv.image() is not None
top_sv.orbit(20, 0); assert top_sv.custom and json.dumps(ed.project.to_dict(), sort_keys=True) == state0
# во время проигрывания боковые виды не пересчитываются
img_before = top_sv.image()
ed.audio_preview = False; ed.toggle_play(); app.processEvents()
top_sv.schedule(); assert not top_sv._timer.isActive()
ed.stop(); app.processEvents(); assert top_sv._timer.isActive(), "после остановки боковой вид должен догнать"
# 2 вида и обратно 1; меню и запоминание
w.set_view_layout(2); assert w.side_views[0].isVisible() and not w.side_views[1].isVisible()
w.set_view_layout(1); assert not any(sv.isVisible() for sv in w.side_views)
acts = {a.text(): a for a in w.findChildren(type(w.menuBar().actions()[0]))}
acts[i18n.tr("Раскладка: 4 вида")].trigger(); app.processEvents(); assert w.view_layout == 4 and w.layout_box.currentIndex() == 2
from PySide6.QtCore import QSettings
assert int(QSettings("sfx", "sfx").value("view_layout")) == 4
w.set_view_layout(1)

# --- сохранение и старые проекты
w.set_view3d("custom"); three.orbit_view(ed.comp, 10, 5)
pr = Project.from_dict(json.loads(json.dumps(ed.project.to_dict())))
assert pr.comp.view3d == "custom" and pr.comp.view_custom == ed.comp.view_custom
old = json.loads(json.dumps(ed.project.to_dict()))
for c_ in old["comps"]:
    c_.pop("view3d"); c_.pop("view_custom")
po = Project.from_dict(old)
assert po.comp.view3d == "active" and three.view_override(po.comp) is None
print("OK", i18n.LANG)
sys.stdout.flush(); os._exit(0)
