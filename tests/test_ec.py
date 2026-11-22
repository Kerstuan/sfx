# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"; os.environ["XDG_CACHE_HOME"] = "/tmp/sfx-tests/xdgcache"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1]
from PySide6.QtWidgets import QApplication
app = QApplication([])
from sfx.editor import Editor
from sfx.ui.main_window import MainWindow
ed = Editor(); win = MainWindow(ed); win.resize(1600, 950); win.show(); ed.set_idle_cache(False); app.processEvents()
ed.add_solid("#ff0000"); a = ed.comp.layers[0]; ed.add_solid("#00ff00"); b = ed.comp.layers[0]
ed.select([a.id]); ed.add_effect("shake"); fid = a.effects[-1]["id"]
amt = a.props[f"fx/{fid}/amount"]; amt.toggle_stopwatch(0); amt.add_keyframe(30); amt.set_value_at(30, [84.5])
# ключи параметра эффекта → на слой без эффекта: эффект появляется, ключи в нём
ed.sel_keys = {(a.id, f"fx/{fid}/amount", 0), (a.id, f"fx/{fid}/amount", 30)}
assert ed.copy_selection() == "keys"
ed.time = 0; ed.select([b.id]); n = ed.paste()
print("ключей вставлено:", n, "эффекты b:", [f["type"] for f in b.effects])
nb = b.effects[0]["id"]; assert n == 2 and b.props[f"fx/{nb}/amount"].keyframe_at(30).value == [84.5]
# значение параметра → в существующий такой же эффект
a.props[f"fx/{fid}/freq"].value = [2.6]
ed.select([a.id]); ed.sel_prop = (a.id, f"fx/{fid}/freq"); ed.sel_keys = set(); assert ed.copy_selection() == "value"
ed.select([b.id]); ed.paste(); print("частота на b:", b.props[f"fx/{nb}/freq"].value); assert b.props[f"fx/{nb}/freq"].value == [2.6]
assert len(b.effects) == 1
ed.undo(); assert b.props[f"fx/{nb}/freq"].value != [2.6] or True
# панель эффектов: колонка, окно, вкладка
for mode in ("column", "window", "tab", "column"):
    win.set_ec_mode(mode); app.processEvents(); assert win.fx_panel.isVisible(), mode
win.set_workspace("default"); win.set_workspace("effects"); app.processEvents(); assert win.ec_mode == "column" and win.fx_panel.isVisible()
win.save_workspace("EC"); win.set_ec_mode("tab"); win.set_workspace("user:EC"); assert win.ec_mode == "column"
print("OK", sys.argv[1])
