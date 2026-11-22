# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Аудит интерфейса: каждый пункт главного меню и каждое действие окна вызывается на проекте с разными
слоями (видео, текст, солид, шейп, камера, нуль). Блокирующие диалоги отвечают «Отмена».
Ошибка = исключение в обработчике или зависание. Печатает число проверенных пунктов."""
import os, sys, time, traceback, faulthandler
PROG = open("/tmp/sfx-tests/menus_progress.log", "w", buffering=1)
faulthandler.dump_traceback_later(120, repeat=True, file=PROG)   # зависание — видно, где стоит
os.environ["QT_QPA_PLATFORM"] = "offscreen"; os.environ["XDG_CACHE_HOME"] = "/tmp/sfx-tests/xdgcache"
os.environ["SFX_WORKERS"] = "0"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1]
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QInputDialog, QMessageBox, QColorDialog, QMenu,
                               QFontDialog)
from PySide6.QtGui import QColor, QAction
app = QApplication([])

# --- диалоги не блокируют: «Отмена»
QDialog.exec = lambda self, *a: 0
captured = []


class CapMenu(QMenu):
    """Меню, которое не открывается (exec у самого QMenu в PySide6 не подменяется), а запоминается для обхода."""
    def exec(self, *a, **k):
        captured.append(self)
        return None
QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: ("", ""))
QFileDialog.getOpenFileNames = staticmethod(lambda *a, **k: ([], ""))
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))
QFileDialog.getExistingDirectory = staticmethod(lambda *a, **k: "")
QInputDialog.getText = staticmethod(lambda *a, **k: ("", False))
QInputDialog.getDouble = staticmethod(lambda *a, **k: (0.0, False))
QInputDialog.getInt = staticmethod(lambda *a, **k: (0, False))
QInputDialog.getItem = staticmethod(lambda *a, **k: ("", False))
QColorDialog.getColor = staticmethod(lambda *a, **k: QColor())
QFontDialog.getFont = staticmethod(lambda *a, **k: (None, False))
for name in ("information", "warning", "critical"):
    setattr(QMessageBox, name, staticmethod(lambda *a, **k: QMessageBox.Ok))
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Discard)

errors = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))

from sfx.ui.theme import STYLESHEET; app.setStyleSheet(STYLESHEET)
from sfx.editor import Editor
from sfx.ui.main_window import MainWindow
import sfx.ui.main_window as _mw, sfx.ui.effect_controls as _ec, sfx.ui.timeline as _tl
import sfx.ui.panels as _pn, sfx.ui.flow as _fl
for _m in (_mw, _ec, _tl, _pn, _fl):
    if hasattr(_m, "QMenu"):
        _m.QMenu = CapMenu

VID = os.path.expanduser("~/Videos/Replay_2026-10-31_00-14-28.mp4")


def build(ed):
    ed.add_media_layer(VID)
    ed.comp.duration = ed.comp.work_out = 90
    ed.add_solid("#3060c0")
    ed.add_text()
    ed.add_null()
    ed.add_camera()
    v = next(l for l in ed.comp.layers if l.kind == "video")
    v.out_point = min(v.out_point, 90)
    ed.select([v.id])
    ed.add_effect("glow")
    ed.time = 20


ed = Editor(); ed.set_idle_cache(False)
win = MainWindow(ed); win.resize(1600, 950); win.show(); app.processEvents()
build(ed)
app.processEvents()

SKIP = {"Выход", "Exit", "Quit"}           # закрыло бы окно — проверяется отдельно (closeEvent ниже)
actions, seen = [], set()


def walk(menu, path):
    for a in menu.actions():
        if a.menu():
            walk(a.menu(), path + [a.menu().title()])
        elif not a.isSeparator() and a.text() and id(a) not in seen:
            seen.add(id(a))
            actions.append((" › ".join(path + [a.text()]), a))


for top in win.menuBar().actions():
    if top.menu():
        top.menu().aboutToShow.emit()       # меню, которые собираются при открытии
        walk(top.menu(), [top.text()])

ok, failed, slow = 0, [], []
for name, a in actions:
    if a.text().replace("&", "") in SKIP:
        continue
    if not any(l.kind == "video" for l in ed.comp.layers):   # «Новый проект»/удаление — вернуть слои
        build(ed)
    vids = [l for l in ed.comp.layers if l.kind == "video"]
    ed.select([vids[0].id])
    n0 = len(errors)
    t0 = time.time()
    PROG.write(f"main {name}\n")
    try:
        a.trigger()
        for _ in range(3):
            app.processEvents()
    except Exception:
        errors.append(traceback.format_exc())
    dt = time.time() - t0
    for w in app.topLevelWidgets():           # закрыть открытые окна-инструменты (кроме главного)
        if w is not win and w.isVisible() and w.isWindow():
            w.close()
    if len(errors) > n0:
        failed.append((name, errors[-1].strip().splitlines()[-1]))
    else:
        ok += 1
    if dt > 20:
        slow.append((name, round(dt, 1)))

# --- контекстные меню: правый клик по каждому типу слоя и по эффекту в Effect Controls
from PySide6.QtCore import QPoint
ctx_total = 0


def run_menu_actions(make_menu, label):
    global ok, ctx_total
    captured.clear()
    make_menu()
    if not captured:
        return
    items = []
    seen_m = set()

    def walk_m(m, path):
        for a in m.actions():
            if a.menu():
                a.menu().aboutToShow.emit()
                walk_m(a.menu(), path + [a.menu().title()])
            elif not a.isSeparator() and a.text() and id(a) not in seen_m:
                seen_m.add(id(a))
                items.append((" › ".join(path + [a.text()]), a.text()))
    walk_m(captured[-1], [label])
    for name, text in items:          # каждое действие — в свежем проекте и свежем меню
        ctx_total += 1
        PROG.write(f"ctx {name}\n")
        ed.new_project()
        build(ed)
        captured.clear()
        make_menu()
        acts = []

        def find(m):
            for a in m.actions():
                if a.menu():
                    a.menu().aboutToShow.emit()
                    find(a.menu())
                elif a.text() == text:
                    acts.append(a)
        if captured:
            find(captured[-1])
        if not acts:
            continue
        n0 = len(errors)
        try:
            acts[0].trigger()
            for _ in range(3):
                app.processEvents()
        except Exception:
            errors.append(traceback.format_exc())
        for w in app.topLevelWidgets():
            if w is not win and w.isVisible() and w.isWindow():
                w.close()
        if len(errors) > n0:
            failed.append((name, errors[-1].strip().splitlines()[-1]))
        else:
            ok += 1
        if not any(l.kind == "video" for l in ed.comp.layers):
            build(ed)


for kind in ("video", "solid", "text", "null", "camera"):
    def mk(kind=kind):
        if not any(l.kind == kind for l in ed.comp.layers):
            build(ed)
        lay = next(l for l in ed.comp.layers if l.kind == kind)
        win.layer_menu(QPoint(0, 0), lay)
    run_menu_actions(mk, "ПКМ " + kind)
run_menu_actions(lambda: win.layer_menu(QPoint(0, 0), None), "ПКМ пусто")


def mk_fx():
    if not any(l.kind == "video" for l in ed.comp.layers):
        build(ed)
    v = next(l for l in ed.comp.layers if l.kind == "video")
    if not v.effects:
        ed.select([v.id]); ed.add_effect("glow")
    win.fx_panel._fx_menu(v, v.effects[0]["id"], QPoint(0, 0))
run_menu_actions(mk_fx, "ПКМ эффект")
print("пунктов контекстных меню:", ctx_total)

# undo/redo после всего — состояние восстанавливается без ошибок
for _ in range(5):
    ed.undo()
for _ in range(5):
    ed.redo()
app.processEvents()
win.close(); app.processEvents()   # closeEvent с вопросом «Сохранить?» → «Не сохранять»

print(f"пунктов меню: {len(actions)}, без ошибок: {ok}, с ошибками: {len(failed)}")
for n, e in failed:
    print("  FAIL", n, "→", e)
for n, s in slow:
    print("  SLOW", n, s, "с")
if errors and not failed:
    print(errors[-1])
assert not failed and len(errors) == 0, f"{len(failed)} пунктов с ошибками"
print("OK", sys.argv[1])
