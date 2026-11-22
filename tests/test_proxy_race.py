# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""Два редактора в одном процессе кодируют прокси одного клипа — готовый прокси должен читаться."""
import os, sys, time, tempfile
os.environ["QT_QPA_PLATFORM"] = "offscreen"; os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp(prefix="sfx-px-")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication
app = QApplication([])
import av
from sfx.editor import Editor
from sfx.proxy import ready
VID = os.path.expanduser("~/Videos/Replay_2026-10-31_00-14-28.mp4")
def pump(sec):
    t = time.time()
    while time.time() - t < sec:
        app.processEvents(); time.sleep(0.05)
eds = []
for _ in range(2):      # второй стартует позже первого — как fresh() в test_time
    e = Editor(); e.add_media_layer(VID); eds.append(e); pump(3)
t = time.time()
while (ready(VID) is None or any(e.proxies.busy() for e in eds)) and time.time() - t < 300:
    app.processEvents(); time.sleep(0.05)
p = ready(VID); assert p, "прокси не собрался"
with av.open(p) as c:
    assert c.streams.video and c.duration, "прокси битый"
print("OK")
