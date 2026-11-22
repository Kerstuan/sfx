# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
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
ed.select([l.id]); ed.add_effect("blur"); ed.add_effect("glow")
ed.comp.work_in, ed.comp.work_out = 0, 45
ed.notify(); app.processEvents()
t = time.time(); ed.ram_preview()
while ed.prerendering:
    app.processEvents()
print("prerender 45 кадров:", round(time.time() - t, 2), "s; играет:", ed.timer.isActive())
assert ed.timer.isActive()
assert set(range(0, 45)) <= ed.cached_frames()
win.grab().save("/tmp/sfx-tests/" + __import__("os").path.basename(sys.argv[0]).replace("test_cache.py", f"cache_{sys.argv[1]}.png"))
ed.stop()
# из кэша — быстро
t = time.perf_counter()
for f in range(45): ed.set_time(f); win.viewer.repaint()
print("показ из кэша: %.1f ms/кадр" % ((time.perf_counter() - t) * 1000 / 45))
# изменение проекта сбрасывает кэш
ed.snapshot(); l.props["opacity"].set_value_at(0, [50]); ed.notify(); app.processEvents()
assert ed.cached_frames() <= {ed.time}, len(ed.cached_frames())
img = ed.frame_image(3); assert 3 in ed.cached_frames()
# отмена
ed.ram_preview(); app.processEvents(); ed.toggle_play(); assert not ed.prerendering and not ed.timer.isActive()
# undo после изменений: кадры старого состояния снова невалидны/валидны корректно
ed.undo(); app.processEvents(); img2 = ed.frame_image(3)
assert img2 is not img
print("OK", sys.argv[1])
