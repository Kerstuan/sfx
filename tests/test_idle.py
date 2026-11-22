# Copyright (c) 2026 Kerstuan. All rights reserved.
import os, sys, time
os.environ["QT_QPA_PLATFORM"] = "offscreen"; os.environ["XDG_CACHE_HOME"] = "/tmp/sfx-tests/xdgcache"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1]
from PySide6.QtWidgets import QApplication
app = QApplication([])
from sfx.editor import Editor
from sfx.ui.main_window import MainWindow
ed = Editor(); win = MainWindow(ed); win.resize(1600, 950); win.show(); app.processEvents()
ed.add_media_layer(os.path.expanduser("~/Videos/Replay_2026-10-31_00-14-28.mp4")); l = ed.comp.layers[0]
ed.select([l.id]); ed.add_effect("blur"); ed.add_effect("threshold")
ed.comp.work_in, ed.comp.work_out = 0, 40; ed.set_time(10); ed.notify()
def run(sec):
    t = time.time()
    while time.time() - t < sec: app.processEvents(); time.sleep(0.005)
run(0.3); n0 = len(ed.cached_frames()); assert n0 <= 2, n0          # до простоя ничего не считается
t_ = time.time()   # холодный старт (прокси ещё строится) — ~8,5 с, с готовым прокси ~2,5 с
while time.time() - t_ < 12 and not set(range(0, 40)) <= ed.cached_frames():
    run(0.1)
have = ed.cached_frames(); print("кадров в фоне:", len(have), "за %.1f с" % (time.time() - t_))
assert set(range(0, 40)) <= have
# изменение — кэш сброшен, потом снова набирается
ed.snapshot(); l.props["opacity"].set_value_at(0, [70]); ed.notify(); run(0.2)
assert len(ed.cached_frames()) <= 2
# отзывчивость: максимальная пауза цикла событий во время фонового счёта
gaps = []; last = time.perf_counter(); t = time.time()
while time.time() - t < 2.5:
    app.processEvents(); now = time.perf_counter(); gaps.append(now - last); last = now
print("макс. пауза интерфейса: %.0f мс" % (max(gaps) * 1000))
# выключение
win.preview_panel.idle.setChecked(False); assert not ed.idle_cache and not win.idle_action.isChecked()
ed.cache.clear(); ed.notify(); run(1.5); assert len(ed.cached_frames()) <= 1
print("OK", sys.argv[1])
