#!/usr/bin/env python3
# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Запуск SFX:  ./run.sh  [проект.skl | видео ...]"""
import os
import sys

from PySide6.QtWidgets import QApplication

from sfx.editor import Editor
from sfx.i18n import tr
from sfx.ui.main_window import MainWindow
from sfx.ui.theme import STYLESHEET


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(tr("SFX"))
    app.setDesktopFileName("sfx")   # окно связано с ярлыком sfx.desktop: значок в панели/Hyprland
    from PySide6.QtGui import QIcon
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sfx.png")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    app.setStyle("Fusion")
    app.setStyleSheet(STYLESHEET)
    from sfx import plugins
    plugins.load_all()          # плагины до создания меню — их эффекты попадут в списки
    ed = Editor()
    ed.start_render_service()   # кадры превью считаются в фоновых процессах — окно не подвисает
    win = MainWindow(ed)
    args = [a for a in sys.argv[1:] if os.path.exists(a)]
    if args and args[0].lower().endswith(".skl"):
        win.open_path(args[0])
    elif args:
        win._import(args, add=True)
    win.show()
    win.restore_last_layout()   # раскладка окон с прошлого запуска
    win.start_autosave()        # раз в 2 мин копия проекта; после сбоя — предложение восстановить
    if not args and ed.path is None and not any(c.layers for c in ed.project.comps):
        win.show_home(startup=True)   # окно «Проекты», как стартовый экран AE (отключается галкой в нём)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
