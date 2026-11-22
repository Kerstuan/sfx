# Copyright (c) 2026 Kerstuan. All rights reserved.
# Раскладка окон сохраняется при закрытии и восстанавливается при запуске (main.py → restore_last_layout).
import os, sys
import json, tempfile
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()  # не трогать настройки пользователя
_scr = os.path.join(os.environ["XDG_CONFIG_HOME"], "scr.json")  # экран 1920x1080: дефолтный offscreen 800x600 ужимает окно
json.dump({"screens": [{"name": "s", "x": 0, "y": 0, "width": 1920, "height": 1080, "logicalDpi": 96, "logicalBaseDpi": 96, "dpr": 1}]}, open(_scr, "w"))
os.environ["QT_QPA_PLATFORM"] = "offscreen:configfile=" + _scr
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from sfx.editor import Editor
from sfx.ui.main_window import MainWindow
w = MainWindow(Editor()); w.show(); w.restore_last_layout(); app.processEvents()
w.set_columns_swapped(True); w.set_ec_mode("column"); w.right.hide(); w.main_split.setSizes([300, 650])
w.resize(1500, 900); app.processEvents()
before = w._layout_state(); size = w.size()
ORDER = [{id(w.left_tabs):"L",id(w.right):"R",id(w.viewer_panel):"V"}.get(id(w.top.widget(i)),"EC") for i in range(w.top.count())]
w.save_last_layout(); w.ed._dirty = False
w.close(); app.processEvents()
w2 = MainWindow(Editor()); w2.show(); w2.restore_last_layout(); app.processEvents(); app.processEvents()
after = w2._layout_state()
nm=lambda w:[{id(w.left_tabs):"L",id(w.right):"R",id(w.viewer_panel):"V"}.get(id(w.top.widget(i)),"EC") for i in range(w.top.count())]
print(before); print(after); print(ORDER, nm(w2)); print(size, w2.size())
for k in ("swap", "ec", "right", "tl_top", "left", "tab"):
    assert before[k] == after[k], k
assert abs(after["main"][0] - before["main"][0]) <= 2, (before["main"], after["main"])
assert w2.size() == size; assert all(abs(a-b)<=3 for a,b in zip(before["top"], after["top"])), "top"
print("OK layout")
sys.stdout.flush(); os._exit(0)   # без flush вывод в pipe (run_all.sh) теряется
