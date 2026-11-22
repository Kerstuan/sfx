# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""Маркеры композиции (*, перетаскивание, Ctrl+клик — удалить, двойной клик — имя, J/K, прилипание, сохранение,
отмена, смена fps) и slip-монтаж (тянуть тень исходника: клип на месте, видео внутри сдвигается)."""
import os, sys, json, tempfile
os.environ["QT_QPA_PLATFORM"] = "offscreen"; os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1] if len(sys.argv) > 1 else "ru"
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
app = QApplication([])
import numpy as np
from sfx.editor import Editor
from sfx.model import Project
from sfx.render import render_frame
from sfx.effects import to_array
from sfx.ui.main_window import MainWindow
from sfx.ui import timeline as T

VID = os.path.expanduser("~/Videos/Replay_2026-10-31_00-14-28.mp4")
diff = lambda x, y: float(np.abs(x - y).mean())
ed = Editor(); w = MainWindow(ed); w.resize(1600, 950); w.show(); app.processEvents()
ed.add_media_layer(VID); v = ed.comp.layers[0]
tl = w.timeline
ed.comp.duration = 600; tl.fit(); app.processEvents()

# --- маркер по хоткею *
ed.set_time(100); ed.deselect(); app.processEvents()      # без выделения * ставит маркер композиции (как в AE)
act = [a for a in w.findChildren(type(w.menuBar().actions()[0])) if a.text().startswith(i18n.tr("Добавить маркер"))]
assert act, "нет действия «Добавить маркер»"
assert any(s.toString() == "*" for s in act[0].shortcuts()), act[0].shortcuts()
act[0].trigger(); app.processEvents()
assert [m["frame"] for m in ed.comp.markers] == [100]
ed.add_marker(100); assert len(ed.comp.markers) == 1, "на одном кадре — один маркер"
ed.add_marker(300)

# --- J/K ходят по маркерам
ed.set_time(0); ed.goto_key(1); assert ed.time == 100
ed.goto_key(1); assert ed.time == 300
ed.goto_key(-1); assert ed.time == 100

# --- перетаскивание маркера мышью
tl.repaint(); app.processEvents()
hit = lambda f: next(h for h in tl._hits if h[0] == "marker" and h[2]["frame"] == f)
r = hit(100)[1]
p0 = r.center().toPoint()
QTest.mousePress(tl, Qt.LeftButton, Qt.NoModifier, p0)
QTest.mouseMove(tl, QPoint(int(tl.f2x(150)), p0.y()))
QTest.mouseRelease(tl, Qt.LeftButton, Qt.NoModifier, QPoint(int(tl.f2x(150)), p0.y()))
app.processEvents()
assert sorted(m["frame"] for m in ed.comp.markers) == [150, 300], ed.comp.markers
ed.undo(); assert sorted(m["frame"] for m in ed.comp.markers) == [100, 300], "перетаскивание маркера не отменяется"

# --- прилипание слоя к маркеру
assert 300 in tl._snap_targets()

# --- двойной клик — имя маркера
tl.repaint(); app.processEvents()
QTest.mouseDClick(tl, Qt.LeftButton, Qt.NoModifier, hit(300)[1].center().toPoint()); app.processEvents()
assert tl._edit is not None, "поле имени маркера не открылось"
tl._edit[0].setText("дроп"); QTest.keyClick(tl._edit[0], Qt.Key_Return); app.processEvents()
assert [m["name"] for m in ed.comp.markers if m["frame"] == 300] == ["дроп"]

# --- сохранение / загрузка, смена fps
p = Project.from_dict(json.loads(json.dumps(ed.project.to_dict())))
assert [(m["frame"], m["name"]) for m in p.comp.markers] == [(100, ""), (300, "дроп")]
old = Project.from_dict({k: v for k, v in json.loads(json.dumps(ed.project.to_dict())).items()})
for c in old.comps:
    c.__dict__.pop("markers", None)
assert Project.from_dict(json.loads(json.dumps(old.to_dict()))).comp.markers == [], "старый проект без маркеров должен открываться"

# --- Ctrl+клик — удалить
tl.repaint(); app.processEvents()
QTest.mouseClick(tl, Qt.LeftButton, Qt.ControlModifier, hit(100)[1].center().toPoint()); app.processEvents()
assert [m["frame"] for m in ed.comp.markers] == [300]

# --- slip: клип 50..150 из середины исходника, тянем тень влево на 20 кадров
v = ed.comp.layers[0]           # после undo слои — новые объекты
v.in_point, v.out_point = 50, 150
v.start = 0
ed.select([v.id]); tl.repaint(); app.processEvents()
f60_before = to_array(render_frame(ed.comp, 60, ed.pool, 0.25))
f80_before = to_array(render_frame(ed.comp, 80, ed.pool, 0.25))
ghost = next(h for h in tl._hits if h[0] == "ghost" and h[2] is v)[1]
y = int(ghost.center().y())
x0 = int(tl.f2x(20))                    # тень левее клипа
QTest.mousePress(tl, Qt.LeftButton, Qt.NoModifier, QPoint(x0, y))
QTest.mouseMove(tl, QPoint(x0 - int(20 * tl.ppf), y))
QTest.mouseRelease(tl, Qt.LeftButton, Qt.NoModifier, QPoint(x0 - int(20 * tl.ppf), y)); app.processEvents()
assert (v.in_point, v.out_point) == (50, 150), "slip не должен двигать сам клип"
assert v.start == -20 or v.start == -20 + 0, v.start
assert diff(to_array(render_frame(ed.comp, 60, ed.pool, 0.25)), f80_before) < 1.0, "на кадре 60 должен быть исходный кадр 80"
# упор: исходник не может уйти так, чтобы клип вышел за него
d = ed.slip(v, 10 ** 6)
assert v.start <= v.in_point and v.start + v.local_end(ed.comp.fps) >= v.out_point
ed.undo(); ed.undo()

# --- маркеры слоя: * при выделенном слое, едут вместе со слоем, J/K, перетаскивание, разрезание, сохранение
v = ed.comp.layers[0]
v.start, v.in_point, v.out_point = 0, 0, 300
ed.select([v.id]); ed.set_time(120)
n_comp = len(ed.comp.markers)
act[0].trigger(); app.processEvents()
assert len(ed.comp.markers) == n_comp, "при выделенном слое * не должен ставить маркер композиции"
assert [m["frame"] for m in v.markers] == [120], v.markers
ed.undo(); v = ed.comp.layers[0]; assert v.markers == [], "маркер слоя не отменяется"
ed.select([v.id]); act[0].trigger(); app.processEvents()
v.start += 40; v.in_point += 40; v.out_point += 40           # слой сдвинули — маркер едет вместе
assert ed.marker_comp_frame(v.markers[0]) == 160
assert 160 in tl._snap_targets()
ed.set_time(150); ed.goto_key(1); assert ed.time == 160, ed.time
tl.repaint(); app.processEvents()
lh = next(h for h in tl._hits if h[0] == "marker" and h[2] is v.markers[0])
p0 = lh[1].center().toPoint()
QTest.mousePress(tl, Qt.LeftButton, Qt.NoModifier, p0)
QTest.mouseMove(tl, QPoint(int(tl.f2x(200)), p0.y()))
QTest.mouseRelease(tl, Qt.LeftButton, Qt.NoModifier, QPoint(int(tl.f2x(200)), p0.y())); app.processEvents()
assert ed.marker_comp_frame(v.markers[0]) in (199, 200, 201), ed.marker_comp_frame(v.markers[0])
assert (v.in_point, v.out_point) == (40, 340), "перетаскивание маркера сдвинуло слой"
mk = v.markers[0]["frame"] + v.start
ed.select([v.id]); ed.set_time(100); ed.split()
parts = sorted((l for l in ed.comp.layers if l.kind == "video"), key=lambda l: l.in_point)
assert parts[0].markers == [] and [m["frame"] + parts[1].start for m in parts[1].markers] == [mk], "маркер — не в той половине"
pr = Project.from_dict(json.loads(json.dumps(ed.project.to_dict())))
assert [m["frame"] for l in pr.comp.layers for m in l.markers] == [m["frame"] for l in ed.comp.layers for m in l.markers]
old = json.loads(json.dumps(ed.project.to_dict()))
for c in old["comps"]:
    for ld in c["layers"]:
        ld.pop("markers", None)
assert all(l.markers == [] for l in Project.from_dict(old).comp.layers), "старый проект без маркеров слоя"
tl.repaint(); app.processEvents()
QTest.mouseClick(tl, Qt.LeftButton, Qt.ControlModifier,
                 next(h for h in tl._hits if h[0] == "marker" and h[2] is parts[1].markers[0])[1].center().toPoint())
assert parts[1].markers == [], "Ctrl+клик не удалил маркер слоя"
print("OK", i18n.LANG)
sys.stdout.flush(); os._exit(0)   # без flush вывод в pipe (run_all.sh) теряется
