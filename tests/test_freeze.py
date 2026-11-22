# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Freeze Frame как в AE (Layer → Time): удержание N кадров и продолжение с того же кадра, сдвиг следующих клипов
(ripple), звук молчит на паузе и после неё идёт синхронно с картинкой, диалог Shift+Alt+F, отмена, сохранение."""
import os, sys, json, tempfile
os.environ["QT_QPA_PLATFORM"] = "offscreen"; os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1] if len(sys.argv) > 1 else "ru"
from PySide6.QtWidgets import QApplication, QDialog, QDoubleSpinBox, QCheckBox
from PySide6.QtCore import QTimer
app = QApplication([])
import numpy as np
from sfx.editor import Editor
from sfx.render import render_frame
from sfx.effects import to_array
from sfx.model import Project
from sfx.audio import mix, SR
from sfx.ui.main_window import MainWindow

VID = os.path.expanduser("~/Videos/Replay_2026-10-31_00-14-28.mp4")
diff = lambda x, y: float(np.abs(x - y).mean())
ed = Editor()
ed.add_media_layer(VID); A = ed.comp.layers[0]
A.out_point = A.in_point + 60                          # клип A: 0..60
ed.add_media_layer(VID); B = ed.comp.layers[0]
B.start, B.in_point, B.out_point = 60, 60, 120         # клип B выше, 60..120 — как монтаж «лесенкой»
B.audio = False
fps = ed.comp.fps
arr = lambda f: to_array(render_frame(ed.comp, f, ed.pool, 0.25))
orig = {f: arr(f) for f in (20, 21, 40)}
snd0 = mix(ed.comp, 0, 60)
ed.comp.duration = 120

# --- курсор вне слоя: ничего не делаем
ed.select([A.id]); ed.time = 100
assert not ed.freeze_frame(hold=30), "курсор вне слоя — стоп-кадра быть не должно"

# --- стоп-кадр на кадре 20, держать 30, сдвинуть следующие
ed.time = 20
assert ed.freeze_frame(hold=30, ripple=True)
assert A.out_point == 90 and (B.in_point, B.out_point) == (90, 150), (A.out_point, B.in_point, B.out_point)
assert ed.comp.duration >= 150
for f in (20, 35, 49):
    assert diff(arr(f), orig[20]) < 1.0, f"кадр {f} должен быть стоп-кадром"
assert diff(arr(51), orig[21]) < 1.0, "после паузы видео продолжается с кадра 21"
assert diff(arr(70), orig[40]) < 1.0, "кадр 70 = исходный 40"
assert diff(arr(100), arr(100)) == 0 and diff(arr(95), orig[20]) > 1, "после 90 — уже клип B"

# --- звук: на паузе тишина, после — тот же звук, сдвинутый на 30 кадров
snd = mix(ed.comp, 0, 90)
k = lambda f: int(round(f / fps * SR))
assert np.abs(snd[k(22):k(48)]).max() < 1e-6, "на стоп-кадре звук должен молчать"
seg_new, seg_old = snd[k(55):k(85)], snd0[k(25):k(55)]
n = min(len(seg_new), len(seg_old))
assert np.abs(seg_old[:n]).max() > 1e-3, "в исходнике должен быть звук"
assert np.abs(seg_new[:n] - seg_old[:n]).mean() < 0.02 * np.abs(seg_old[:n]).mean() + 1e-4, "звук после паузы рассинхронизирован"

# --- отмена возвращает всё, сохранение/загрузка сохраняет стоп-кадр
ed.undo()
A2, B2 = ed.comp.layers[1], ed.comp.layers[0]
assert A2.out_point == 60 and B2.in_point == 60 and "time_remap" not in A2.props
ed.redo() if hasattr(ed, "redo") else None
p = Project.from_dict(json.loads(json.dumps(ed.project.to_dict())))
assert "time_remap" in p.comp.layers[1].props and p.comp.layers[0].in_point == 90

# --- диалог Shift+Alt+F: секунды + галка ripple
ed2 = Editor(); w = MainWindow(ed2); w.show()
ed2.add_media_layer(VID); C = ed2.comp.layers[0]; C.out_point = 60
ed2.add_media_layer(VID); D = ed2.comp.layers[0]; D.start, D.in_point, D.out_point = 60, 60, 120
ed2.select([C.id]); ed2.time = 10


def answer():
    dlg = app.activeModalWidget()
    assert isinstance(dlg, QDialog), dlg
    dlg.findChild(QDoubleSpinBox).setValue(0.5)
    dlg.findChild(QCheckBox).setChecked(True)
    dlg.accept()
QTimer.singleShot(200, answer)
w.freeze_dialog()
h = round(0.5 * ed2.comp.fps)
assert C.out_point == 60 + h and D.in_point == 60 + h, (C.out_point, D.in_point, h)
print("OK", i18n.LANG)
sys.stdout.flush(); os._exit(0)   # без flush вывод в pipe (run_all.sh) теряется
