# Copyright (c) 2026 Kerstuan. All rights reserved.
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QKeyEvent
from PySide6.QtCore import QEvent, Qt
app = QApplication([])
from sfx.editor import Editor
from sfx.ui.main_window import MainWindow
ed = Editor(); win = MainWindow(ed); win.show(); app.processEvents()
ed.add_solid("#ff0000"); l = ed.comp.layers[0]; ed.select([l.id]); ed.time = 50
fired = []
for a in win.actions():
    a.triggered.connect(lambda _=False, a=a: fired.append(a.text()))
def press(ch, mods=Qt.NoModifier, scan=0):
    for t in (QEvent.ShortcutOverride, QEvent.KeyPress):
        ev = QKeyEvent(t, ord(ch.upper()) if ch.isalpha() else ord(ch), mods, scan, 0, 0, ch)
        app.sendEvent(win.timeline, ev)
    app.processEvents()
# русская «з» = P, без скан-кода
press("з"); print("з →", fired[-1:] , win.timeline.shown.get(l.id))
assert win.timeline.shown.get(l.id) == ["position"]
press("е"); assert win.timeline.shown.get(l.id) == ["opacity"], win.timeline.shown.get(l.id)
fired.clear(); l.in_point = 0
press("х", Qt.AltModifier); print("Alt+х →", fired, "in", l.in_point); assert l.in_point == 50
press("г"); print("г (U) →", win.timeline.shown.get(l.id))
print("OK")
