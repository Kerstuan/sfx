# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Время: Freeze Frame (с удержанием / до конца / последний кадр), Time Remap, Posterize Time, Echo;
Deep Glow и Motion Tile; сохранение/загрузка, отмена, экспорт через ffmpeg."""
import os, sys, json, subprocess
os.environ["QT_QPA_PLATFORM"] = "offscreen"; os.environ["XDG_CACHE_HOME"] = "/tmp/sfx-tests/xdgcache"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1]
from PySide6.QtWidgets import QApplication
app = QApplication([])
import numpy as np
from sfx.editor import Editor
from sfx.render import render_frame
from sfx.effects import to_array, prop_key, category_label
from sfx.model import Project

VID = os.path.expanduser("~/Videos/Replay_2026-10-31_00-14-28.mp4")
S = 0.25
diff = lambda x, y: float(np.abs(x - y).mean())


def fresh():
    e = Editor()
    e.add_media_layer(VID)
    ly = e.comp.layers[0]
    e.select([ly.id])
    return e, ly


ed, l = fresh()
arr = lambda f: to_array(render_frame(ed.comp, f, ed.pool, S))
assert category_label("time") == ("Time" if sys.argv[1] == "en" else "Время")
orig = {f: arr(f) for f in (30, 31, 40, 50)}
assert diff(orig[30], orig[50]) > 1, "кадры видео должны отличаться"

# --- Freeze Frame с удержанием 20 кадров на кадре 30: 30..49 — один кадр, потом видео идёт дальше с 31
out0 = l.out_point
ed.time = 30
assert ed.freeze_frame(hold=20)
assert l.out_point == out0 + 20, (l.out_point, out0)
a30, a40, a49 = arr(30), arr(40), arr(49)
assert diff(a30, orig[30]) < 0.5 and diff(a40, a30) < 0.5 and diff(a49, a30) < 0.5, "удержание не держит кадр"
assert diff(arr(51), orig[31]) < 0.5, "после удержания видео не продолжилось"
assert diff(arr(70), orig[50]) < 0.5
k = [k for k in l.props["time_remap"].keyframes if k.frame == 50 - l.start][0]
k.frame += 10                                   # длину паузы меняют, двигая ключ
assert diff(arr(55), a30) < 0.5, "после удлинения паузы кадр 55 должен быть стоп-кадром"
ed.undo()
assert "time_remap" not in ed.comp.layers[0].props and ed.comp.layers[0].out_point == out0, "отмена стоп-кадра"
ed.redo()
assert "time_remap" in ed.comp.layers[0].props, "повтор стоп-кадра"
l = ed.comp.layers[0]

# --- сохранение / загрузка проекта со стоп-кадром
p2 = Project.from_dict(json.loads(json.dumps(ed.project.to_dict())))
l2 = p2.comp.layers[0]
for f in (10, 35, 60):
    assert abs(l2.src_time(l2.local(f), 60) - l.src_time(l.local(f), 60)) < 1e-6, "Time Remap не сохранился"

# --- Freeze Frame до конца слоя и на последнем кадре
ed, l = fresh(); ed.time = 40
assert ed.freeze_frame()
assert diff(arr(100), orig[40]) < 0.5 and diff(arr(41), orig[40]) < 0.5
ed, l = fresh()
assert ed.freeze_frame(last=True)
assert l.out_point >= ed.comp.duration

# --- Time Remap: включение не меняет картинку, повтор — выключает
ed, l = fresh()
assert ed.toggle_time_remap()
ks = l.props["time_remap"].keyframes
assert len(ks) == 2 and ks[0].frame == l.in_point - l.start
assert diff(arr(40), orig[40]) < 0.5, "Time Remap сам по себе изменил кадр"
ks[1].value = [ks[1].value[0] / 2]              # вдвое медленнее
assert diff(arr(50), orig[50]) > 0.5
ed.toggle_time_remap()
assert "time_remap" not in l.props

# --- Posterize Time 12 к/с: внутри шага кадры одинаковые
ed, l = fresh()
assert ed.add_effect("posterizetime")
step = int(round(ed.comp.fps / 12))
base = arr(30)
for f in range(30, 30 + step):
    assert diff(arr(f), base) < 0.5, f"Posterize Time: кадр {f} отличается"
assert diff(arr(30 + step), base) > 0.3

# --- Echo: 0 эхо = как без эффекта, 6 эхо со сложением — ярче
ed, l = fresh()
ed.add_effect("echo"); fx = l.effects[-1]; key = prop_key(fx["id"], "")
l.props[key + "num"].value = [0.0]
assert diff(arr(40), orig[40]) < 0.5
l.props[key + "num"].value = [6.0]; l.props[key + "decay"].value = [0.6]; l.props[key + "etime"].value = [-0.05]
e = arr(40)
assert diff(e, orig[40]) > 1 and e[..., :3].mean() >= orig[40][..., :3].mean(), "Echo не добавил шлейф"
l.props[key + "op"].value = [6.0]
assert diff(arr(40), orig[40]) > 0.5

# --- Deep Glow и Motion Tile
ed, l = fresh()
ed.add_effect("deepglow")
g = arr(40)
assert diff(g, orig[40]) > 0.5 and g[..., :3].mean() > orig[40][..., :3].mean(), "Deep Glow не светит"
ed, l = fresh()
ed.add_effect("motiontile"); fx = l.effects[-1]; key = prop_key(fx["id"], "")
assert diff(arr(40), orig[40]) < 0.5, "Motion Tile 100% должен быть как исходник"
l.props[key + "tw"].value = [50.0]; l.props[key + "th"].value = [50.0]
m = arr(40)
w = m.shape[1]
assert diff(m[:, : w // 2 - 2], m[:, w // 2: w - 2]) < 3, "плитка не повторяется"
l.props[key + "mirror"].value = [1.0]
assert diff(arr(40), m) > 0.5

# --- экспорт со стоп-кадром через ffmpeg
from sfx.export import ExportThread, RenderSettings
ed, l = fresh(); ed.time = 10; ed.freeze_frame(hold=15)
ed.comp.work_in, ed.comp.work_out = 0, 40
out = "/tmp/sfx-tests/freeze_export.mp4"
th = ExportThread(json.loads(json.dumps(ed.project.to_dict())), out, settings=RenderSettings(path=out, audio=False))
res = []
th.finished_ok.connect(lambda p: res.append(("ok", p))); th.failed.connect(lambda m: res.append(("fail", m)))
th.start()
while th.isRunning() or not res:
    app.processEvents(); th.wait(50)
assert res[0][0] == "ok", res
raw = subprocess.run(["ffmpeg", "-v", "error", "-i", res[0][1], "-vf", "scale=160:90", "-f", "rawvideo",
                      "-pix_fmt", "gray", "-"], capture_output=True).stdout
fr = np.frombuffer(raw, np.uint8).reshape(-1, 90, 160).astype(np.float32)
assert len(fr) == 40, len(fr)
assert np.abs(fr[12] - fr[22]).mean() < 2.0, "в файле удержание не держит кадр"
assert np.abs(fr[2] - fr[35]).mean() > 1.0
# --- каждый визуальный эффект рендерится со значениями по умолчанию без ошибок (так нашёлся баг Directional Blur)
from sfx.effects import EFFECTS
ed, l = fresh()
for t, d in EFFECTS.items():
    if d.audio or d.control or d.transition:
        continue
    ed.add_effect(t)
    fid = l.effects[-1]["id"]
    render_frame(ed.comp, 40, ed.pool, S)
    ed.remove_effect(l, fid)

# --- автосохранение и восстановление после сбоя
import tempfile
os.environ["XDG_DATA_HOME"] = tempfile.mkdtemp()
ed, l = fresh(); ed.time = 5; ed.freeze_frame(hold=3)
assert ed.dirty
ap = ed.autosave()
assert ap and os.path.exists(ap) and ed.dirty, "автосохранение не записалось или сбросило «не сохранено»"
pa = Project.from_dict(json.load(open(ap)))
assert "time_remap" in pa.comp.layers[0].props
open(os.path.join(ed.autosave_dir(), "running"), "w").close()     # «прошлый запуск упал»
from PySide6.QtWidgets import QMessageBox
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
from sfx.ui.main_window import MainWindow
e3 = Editor(); w3 = MainWindow(e3)
w3.start_autosave(10 ** 7)
assert e3.comp.layers and "time_remap" in e3.comp.layers[0].props, "восстановление не открыло автосохранение"
assert e3.path is None and e3.dirty
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Discard)
w3.close(); app.processEvents()
assert not os.path.exists(os.path.join(e3.autosave_dir(), "running")), "метка не снята при выходе"
print("OK", sys.argv[1])
