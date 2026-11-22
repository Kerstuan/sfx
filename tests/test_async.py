# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
import os, sys, time
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1] if len(sys.argv) > 1 else "ru"
from PySide6.QtWidgets import QApplication
app = QApplication([])
from sfx.editor import Editor
from sfx.ui.main_window import MainWindow
ed = Editor(); ed.start_render_service(); win = MainWindow(ed); win.resize(1600, 950); win.show(); app.processEvents()
ed.set_idle_cache(False)
print("воркеров:", len(ed.render_service.workers))
SRC = os.path.expanduser("~/Videos/Replay_2026-10-31_00-14-28.mp4")
ed.add_media_layer(SRC); l = ed.comp.layers[0]; ed.select([l.id]); ed.add_effect("blur"); ed.add_effect("glow")
ed.proxies.enabled = False
app.processEvents()
def run(sec, until=None):
    t = time.time(); gaps = []; last = time.perf_counter()
    while time.time() - t < sec:
        app.processEvents(); now = time.perf_counter(); gaps.append(now - last); last = now
        if until and until(): break
        time.sleep(0.001)
    return max(gaps) * 1000
# прогрев воркеров
ed.set_time(100); win.viewer.repaint(); run(8, lambda: ed.cache.get(ed.cache_sig(), 100) is not None)
assert ed.cache.get(ed.cache_sig(), 100) is not None, "воркер не прислал кадр"
print("est_ms", round(ed.est_ms, 1))
# скраб: 30 прыжков — сколько держит окно
import random; random.seed(2)
t0 = time.perf_counter(); worst = 0
for i in range(30):
    t1 = time.perf_counter(); ed.set_time(random.randint(0, 3000)); win.viewer.repaint(); worst = max(worst, (time.perf_counter() - t1) * 1000)
    app.processEvents()
print("скраб: окно занято максимум %.0f мс на прыжок" % worst)
assert worst < 60, worst
mx = run(3)
print("пауза интерфейса пока воркеры считают: %.0f мс" % mx)
# пререндер 120 кадров
ed.comp.work_in, ed.comp.work_out = 600, 720
t = time.time(); ed.ram_preview()
mx = run(60, lambda: not ed.prerendering)
print("пререндер 120 кадров: %.1f с (%.0f кадр/с), макс. пауза окна %.0f мс" % (time.time() - t, 120 / (time.time() - t), mx))
assert set(range(600, 720)) <= ed.cached_frames() and ed.timer.isActive()
ed.stop()
# фоновый кэш с воркерами
ed.cache.clear(); ed.notify(); ed.set_idle_cache(True)
ed.comp.work_in, ed.comp.work_out = 0, 60; ed.set_time(0)
t = time.time(); run(30, lambda: set(range(0, 60)) <= ed.cached_frames())
print("фоновый кэш 60 кадров: %.1f с" % (time.time() - t))
assert set(range(0, 60)) <= ed.cached_frames()
# изменение во время работы — без старых кадров
ed.snapshot(); l.props["opacity"].set_value_at(0, [50]); ed.notify(); run(0.1)
assert len(ed.cached_frames()) <= 3
ed.shutdown()
import glob
assert not glob.glob("/dev/shm/sfx-%d-*" % os.getpid()), "кадры остались в /dev/shm"
print("OK")
