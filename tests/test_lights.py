# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""3D: свет (Spot / Point / Parallel / Ambient, затухание), материал (Ambient/Diffuse/Specular/Shininess/Metal),
тени, глубина резкости камеры; сохранение, отмена, интерфейс (меню, панель, группы таймлайна), экспорт."""
import os, sys, json, tempfile
os.environ["QT_QPA_PLATFORM"] = "offscreen"; os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1] if len(sys.argv) > 1 else "ru"
from PySide6.QtWidgets import QApplication, QComboBox, QCheckBox
app = QApplication([])
import numpy as np
from sfx.editor import Editor
from sfx.model import Project
from sfx.render import render_frame
from sfx.effects import to_array

W, H = 400, 300


def scene():
    ed = Editor()
    ed.comp.width, ed.comp.height, ed.comp.duration = W, H, 30
    ed.comp.bg = "#000000"
    ed.add_solid("#ffffff"); s = ed.comp.layers[0]
    s.width, s.height = W, H
    s.props["anchor"].value = [W / 2, H / 2]
    ed.set_3d([s], True)
    return ed, s


def img(ed, f=0, sc=0.5):
    return to_array(render_frame(ed.comp, f, ed.pool, sc))[..., :3].mean(axis=2)   # яркость 0..255


lum = lambda a, x, y: float(a[int(y * a.shape[0]), int(x * a.shape[1])])

# --- без света 3D-слой не освещается (как в AE)
ed, s = scene()
a = img(ed); assert lum(a, .5, .5) > 250 and lum(a, .05, .05) > 250, "без света слой должен быть как есть"

# --- прожектор: центр светлый, вне конуса темно
L = ed.add_light("spot")
L.props["position"].value = [W / 2, H / 2, -400.0]; L.props["poi"].value = [W / 2, H / 2, 0.0]
L.props["cone_angle"].value = [30.0]; L.props["cone_feather"].value = [0.0]
a = img(ed)
assert lum(a, .5, .5) > 200, ("центр под прожектором", lum(a, .5, .5))
assert lum(a, .05, .05) < 5, ("угол вне конуса", lum(a, .05, .05))
# --- принимает свет: выкл — слой как без света
ed.set_material_option([s], "accepts_lights", False); assert lum(img(ed), .05, .05) > 250
ed.set_material_option([s], "accepts_lights", True)

# --- окружающий 50% — равномерно серый
ed.set_light_option(L, "type", "ambient"); L.props["light_intensity"].value = [50.0]
a = img(ed); assert 115 < lum(a, .5, .5) < 140 and abs(lum(a, .5, .5) - lum(a, .05, .05)) < 3, lum(a, .5, .5)

# --- параллельный спереди: ярко; слой повёрнут на 60° по Y — яркость ≈ cos 60° = 0.5
ed.set_light_option(L, "type", "parallel"); L.props["light_intensity"].value = [100.0]
s.props["mat_spec"].value = [0.0]
a = img(ed); b0 = lum(a, .5, .5); assert b0 > 245, b0
s.props["rot_y"].value = [60.0]
b1 = lum(img(ed), .5, .5); assert 110 < b1 < 145, ("ламберт cos60", b1)
s.props["rot_y"].value = [0.0]

# --- точечный с затуханием обратно квадрату: дальше от света — темнее
ed.set_light_option(L, "type", "point"); ed.set_light_option(L, "falloff", "inverse_square")
L.props["position"].value = [W * 0.1, H / 2, -60.0]; L.props["light_radius"].value = [80.0]
a = img(ed); near, far = lum(a, .12, .5), lum(a, .9, .5)
assert near > far * 3, ("затухание", near, far)

# --- блик: Specular 100 и Shininess 60 дают пятно ярче рассеянного
ed.set_light_option(L, "falloff", "none"); L.props["position"].value = [W / 2, H / 2, -300.0]
L.props["light_intensity"].value = [60.0]
s.props["mat_spec"].value = [0.0]; nospec = lum(img(ed), .5, .5)
s.props["mat_spec"].value = [100.0]; s.props["mat_shine"].value = [60.0]; s.props["mat_metal"].value = [0.0]
withspec = lum(img(ed), .5, .5)
assert withspec > nospec + 30, ("блик", nospec, withspec)
s.props["mat_spec"].value = [0.0]

# --- тени: маленький солид между светом и фоном
ed.set_light_option(L, "type", "spot"); L.props["cone_angle"].value = [120.0]; L.props["light_intensity"].value = [100.0]
L.props["position"].value = [W / 2, H / 2, -500.0]
ed.add_solid("#ff0000"); c = ed.comp.layers[0]
c.width, c.height = 80, 80; c.props["anchor"].value = [40, 40]
ed.set_3d([c], True); c.props["position"].value = [W / 2, H / 2, -250.0]
c.props["opacity"].value = [100.0]
c.visible = True
ed.set_light_option(L, "shadows", True)
# фон без тени (caster.casts_shadows выкл.)
a0 = img(ed)
ed.set_material_option([c], "casts_shadows", True)
a1 = img(ed)
L.props["position"].value = [W / 2 - 150, H / 2, -500.0]   # свет сбоку — тень сдвинута вправо от квадрата
c.props["position"].value = [W / 2, H / 2, -120.0]
a0 = img(ed, sc=0.5); ed.set_material_option([c], "casts_shadows", False); a0 = img(ed, sc=0.5)
ed.set_material_option([c], "casts_shadows", True); a1 = img(ed, sc=0.5)
bg = a0 > 150                                  # светлый белый фон (красный квадрат темнее)
shadow = bg & (a1 < a0 * 0.3)
assert shadow.sum() > 100, ("тени нет", int(shadow.sum()))
assert shadow[:, : a0.shape[1] // 2].sum() < shadow[:, a0.shape[1] // 2:].sum(), "тень должна уйти от света (вправо)"
L.props["shadow_darkness"].value = [50.0]
mid = img(ed, sc=0.5)[shadow].mean()
assert a1[shadow].mean() < mid < a0[shadow].mean(), ("темнота тени 50%", a1[shadow].mean(), mid, a0[shadow].mean())
ed.set_material_option([s], "accepts_shadows", False)
assert img(ed, sc=0.5)[shadow].mean() > a0[shadow].mean() * 0.9, "Принимает тени выкл — тени нет"

# --- глубина резкости
ed2, s2 = scene()
ed2.add_solid("#000000"); k = ed2.comp.layers[0]; k.width, k.height = 40, 300; k.props["anchor"].value = [20, 150]
ed2.set_3d([k], True)
cam = ed2.add_camera(); cam.dof = True
cam.props["aperture"].value = [60.0]
edge = lambda a: float(np.abs(np.diff(a[a.shape[0] // 2])).max())
sharp = edge(img(ed2, sc=1.0))
cam.props["focus_dist"].value = [cam.props["focus_dist"].value[0] * 3]
soft = edge(img(ed2, sc=1.0))
assert soft < sharp * 0.5, ("DOF не размывает", sharp, soft)

# --- сохранение / загрузка / старый проект
p = Project.from_dict(json.loads(json.dumps(ed.project.to_dict())))
lt = [l for l in p.comp.layers if l.kind == "light"][0]
assert lt.light == {"type": "spot", "falloff": "none", "shadows": True}, lt.light
assert [l for l in p.comp.layers if l.id == s.id][0].material["accepts_shadows"] is False
p2 = Project.from_dict(json.loads(json.dumps(ed2.project.to_dict())))
assert [l for l in p2.comp.layers if l.kind == "camera"][0].dof is True
d = json.loads(json.dumps(ed.project.to_dict()))
for ld in d["comps"][0]["layers"]:
    ld.pop("material", None); [ld["props"].pop(k, None) for k in list(ld["props"]) if k.startswith("mat_")]
old = Project.from_dict(d)
assert all(l.material["accepts_lights"] for l in old.comp.layers if l.threed), "старый 3D-слой получает материал по умолчанию"
render_frame(old.comp, 0, ed.pool, 0.25)

# --- отмена
n = len(ed.comp.layers); ed.add_light("point"); assert len(ed.comp.layers) == n + 1
ed.undo(); assert len(ed.comp.layers) == n

# --- интерфейс: меню, панель свойств, группа на таймлайне
from sfx.ui.main_window import MainWindow
ed3 = Editor(); w = MainWindow(ed3); w.resize(1600, 950); w.show(); app.processEvents()
act = [a for a in w.findChildren(type(w.menuBar().actions()[0])) if a.text().startswith(i18n.tr("Новый свет"))]
assert act and any(sc.toString() == "Ctrl+Alt+Shift+L" for sc in act[0].shortcuts())
act[0].trigger(); app.processEvents()
lt = ed3.comp.layers[0]; assert lt.kind == "light"
combos = [c for c in w.findChildren(QComboBox) if c.isVisible() and c.findData("ambient") >= 0]
assert combos, "в панели нет выбора типа света"
combos[0].setCurrentIndex(combos[0].findData("point")); app.processEvents()
assert ed3.comp.layers[0].light["type"] == "point"
tl = w.timeline; tl.toggle_twirl(ed3.comp.layers[0]); tl.repaint(); app.processEvents()
assert ("group", ed3.comp.layers[0], "light") in [(k, l, g) for k, l, g in tl.rows()], "нет группы «Параметры света»"
ed3.add_solid("#808080"); sl = ed3.comp.layers[0]; ed3.select([sl.id]); ed3.set_3d([sl], True); app.processEvents()
assert [c for c in w.findChildren(QCheckBox) if c.isVisible() and c.text() == i18n.tr("Принимает свет")], "нет флажков материала"
tl.toggle_twirl(sl); assert ("group", sl, "material") in tl.rows()
# выделенный свет рисуется во вьювере (жёлтый значок)
ed3.select([lt.id]); w.viewer.repaint(); app.processEvents()
g = w.viewer.grab().toImage()
yellow = sum(1 for yy in range(0, g.height(), 2) for xx in range(0, g.width(), 2)
             if (lambda c: c.red() > 180 and c.green() > 160 and c.blue() < 140)(g.pixelColor(xx, yy)))
assert yellow > 10, ("значок света не нарисован", yellow)

# --- экспорт со светом
from sfx.export import ExportThread, RenderSettings
ed.comp.work_in, ed.comp.work_out = 0, 5
out = os.path.join(tempfile.mkdtemp(), "lights.mp4")
th = ExportThread(json.loads(json.dumps(ed.project.to_dict())), out, settings=RenderSettings(path=out, audio=False, codec="h264"))
res = []
th.finished_ok.connect(lambda p_: res.append(("ok", p_))); th.failed.connect(lambda m: res.append(("fail", m)))
th.start()
while th.isRunning() or not res:
    app.processEvents(); th.wait(50)
assert res[0][0] == "ok" and os.path.getsize(res[0][1]) > 1000, res
print("OK", i18n.LANG)
sys.stdout.flush(); os._exit(0)   # без flush вывод в pipe (run_all.sh) теряется
