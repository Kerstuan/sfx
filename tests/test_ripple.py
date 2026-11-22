# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""Lift / Extract Work Area (как в AE) и Ripple Delete: деление слоёв, сдвиг, ключи едут со слоем,
картинка после стыка = картинка до правки, блокированные слои не трогаются, отмена, меню, сохранение."""
import os, sys, tempfile
os.environ["QT_QPA_PLATFORM"] = "offscreen"; os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1] if len(sys.argv) > 1 else "ru"
from PySide6.QtWidgets import QApplication
app = QApplication([])
import numpy as np
from sfx.editor import Editor
from sfx.render import render_frame
from sfx.effects import to_array
from sfx.ui.main_window import MainWindow

VID = os.path.expanduser("~/Videos/Replay_2026-10-31_00-14-28.mp4")
pic = lambda f: to_array(render_frame(ed.comp, f, ed.pool, 0.25)).astype(np.float32)
diff = lambda x, y: float(np.abs(x - y).mean())
vids = lambda: sorted(((l.in_point, l.out_point) for l in ed.comp.layers if l.kind == "video"))
ed = Editor(); w = MainWindow(ed); w.show(); app.processEvents()
ed.comp.duration = 600
ed.add_media_layer(VID); v = ed.comp.layers[0]
v.in_point, v.out_point = 0, 300
ed.add_solid("#3070c0")
sol = next(l for l in ed.comp.layers if l.kind == "solid")
sol.start, sol.in_point, sol.out_point = 350, 350, 500
sol.props["opacity"].value = [50.0]
# ключ прозрачности на видео — должен уехать вместе со своей частью слоя
v.props["opacity"].set_value_at(0, [100.0]); v.props["opacity"].set_value_at(250, [40.0])
before_260, before_400 = pic(260), pic(400)
assert diff(pic(150), before_260) > 1, "кадры 150 и 260 должны различаться (иначе тест ничего не проверяет)"

# --- Lift: дыра 100..200, видео делится на 2 части, ничего не сдвигается
ed.sel_layers = []
ed.comp.work_in, ed.comp.work_out = 100, 200
assert ed.lift_work_area(False)
assert vids() == [(0, 100), (200, 300)], vids()
assert len({l.id for l in ed.comp.layers}) == len(ed.comp.layers), "одинаковые id после деления"
assert diff(pic(260), before_260) < 0.5, "Lift сдвинул картинку"
assert to_array(render_frame(ed.comp, 150, ed.pool, 0.25))[..., :3].max() < 40, "в дыре должен быть фон"
ed.undo(); assert vids() == [(0, 300)]
v = next(l for l in ed.comp.layers if l.kind == "video"); sol = next(l for l in ed.comp.layers if l.kind == "solid")

# --- Extract: дыры нет, всё после 200 сдвигается на 100 влево; ключи и картинка едут вместе
assert ed.lift_work_area(True)
assert vids() == [(0, 100), (100, 200)], vids()
assert diff(pic(160), before_260) < 0.5, "Extract: бывший кадр 260 должен стать кадром 160"
assert (sol.in_point, sol.out_point) == (250, 400)
assert diff(pic(300), before_400) < 0.5
ed.undo(); assert vids() == [(0, 300)]
v = next(l for l in ed.comp.layers if l.kind == "video"); sol = next(l for l in ed.comp.layers if l.kind == "solid")

# --- Extract только по выделенным: невыделенный сплошной остаётся на месте
ed.sel_layers = [v.id]
assert ed.lift_work_area(True)
assert (sol.in_point, sol.out_point) == (350, 500), "невыделенный слой сдвинулся"
ed.undo()
v = next(l for l in ed.comp.layers if l.kind == "video"); sol = next(l for l in ed.comp.layers if l.kind == "solid")

# --- блокированный слой не трогается
ed.sel_layers = []; v.locked = True
ed.lift_work_area(True)
assert vids() == [(0, 300)], "заблокированный слой изменён"
assert (sol.in_point, sol.out_point) == (250, 400)       # незаблокированный сдвинулся
ed.undo()
v = next(l for l in ed.comp.layers if l.kind == "video"); sol = next(l for l in ed.comp.layers if l.kind == "solid")
v.locked = False

# --- слой целиком внутри области удаляется, его ребёнок отвязывается; отмена возвращает связь
v.parent = sol.id
ed.comp.work_in, ed.comp.work_out = 340, 520
ed.sel_layers = []
assert ed.lift_work_area(False)
assert all(l.kind != "solid" for l in ed.comp.layers)
assert next(l for l in ed.comp.layers if l.kind == "video").parent is None
ed.undo()
v = next(l for l in ed.comp.layers if l.kind == "video"); sol = next(l for l in ed.comp.layers if l.kind == "solid")
assert v.parent == sol.id
v.parent = None

# --- Ripple Delete: удалить видео → сплошной сдвигается на 300 влево (ключи — тоже через start)
ed.sel_layers = [v.id]
assert ed.ripple_delete()
assert [l.kind for l in ed.comp.layers] == ["solid"]
assert (sol.start, sol.in_point, sol.out_point) == (50, 50, 200)
ed.undo(); assert vids() == [(0, 300)]
assert not (ed.sel_layers.clear() or ed.ripple_delete()), "без выделения Ripple Delete ничего не делает"

# --- пункты меню (переводы, хоткей) вызывают то же
acts = {a.text(): a for a in w.findChildren(type(w.menuBar().actions()[0]))}
for t in ("Удалить со сдвигом (Ripple Delete)", "Поднять рабочую область (Lift Work Area)",
          "Извлечь рабочую область (Extract Work Area)"):
    assert i18n.tr(t) in acts, t
    if i18n.LANG == "en":
        assert i18n.tr(t) != t, "нет перевода: " + t
assert any(s.toString() == "Shift+Del" for s in acts[i18n.tr("Удалить со сдвигом (Ripple Delete)")].shortcuts())
ed.comp.work_in, ed.comp.work_out = 100, 200; ed.sel_layers = []
acts[i18n.tr("Извлечь рабочую область (Extract Work Area)")].trigger(); app.processEvents()
assert vids() == [(0, 100), (100, 200)]

# --- сохранение после Extract
p = os.path.join(tempfile.mkdtemp(), "r.skl"); ed.save(p)
ed2 = Editor(); ed2.load(p)
assert sorted((l.in_point, l.out_point) for l in ed2.comp.layers if l.kind == "video") == [(0, 100), (100, 200)]
print("OK", i18n.LANG)
sys.stdout.flush()
os._exit(0)
