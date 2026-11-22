# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""Тёмная тема в духе After Effects."""
import os

from PySide6.QtGui import QColor

_CHECK = os.path.join(os.path.dirname(os.path.abspath(__file__)), "check.svg").replace("\\", "/")

BG = "#232323"
PANEL = "#1e1e1e"
PANEL_2 = "#262626"
ROW = "#232323"
ROW_ALT = "#262626"
ROW_SEL = "#3a3a3a"
BORDER = "#111111"
TEXT = "#b4b4b4"
TEXT_DIM = "#7a7a7a"
TEXT_HI = "#e6e6e6"
ACCENT = "#2d8ceb"     # синий AE: CTI, выделение, значения
VALUE = "#3d9bf2"      # синие «протягиваемые» значения
KEY = "#c8c8c8"
KEY_SEL = "#2d8ceb"
WORK = "#5a5a5a"
CURVE_X = "#e05757"
CURVE_Y = "#5fc35f"
CURVE_1 = "#d6c45a"


def c(hexstr: str, alpha: int | None = None) -> QColor:
    col = QColor(hexstr)
    if alpha is not None:
        col.setAlpha(alpha)
    return col


STYLESHEET = f"""
* {{ color: {TEXT}; font-size: 12px; }}
QMainWindow, QWidget {{ background: {BG}; }}
QMenuBar {{ background: {PANEL}; border-bottom: 1px solid {BORDER}; }}
QMenuBar::item {{ padding: 4px 10px; background: transparent; }}
QMenuBar::item:selected {{ background: #3a3a3a; }}
QMenu {{ background: #2b2b2b; border: 1px solid {BORDER}; }}
QMenu::item {{ padding: 4px 24px 4px 20px; }}
QMenu::item:selected {{ background: {ACCENT}; color: white; }}
QMenu::separator {{ height: 1px; background: #3a3a3a; margin: 3px 6px; }}
QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 6px; }}
QSplitter::handle:vertical {{ height: 6px; }}
QSplitter::handle:hover {{ background: {ACCENT}; }}
QLabel#PanelTitle {{ background: {PANEL}; color: {TEXT_HI}; padding: 4px 8px;
                     border-bottom: 2px solid {ACCENT}; font-weight: bold; }}
QListWidget, QTreeWidget {{ background: {PANEL}; border: none; outline: none; }}
QListWidget::item {{ padding: 3px 4px; }}
QListWidget::item:selected {{ background: #3d4c5c; color: white; }}
QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QFontComboBox {{
    background: #161616; border: 1px solid #3a3a3a; border-radius: 2px; padding: 2px 4px;
    selection-background-color: {ACCENT}; }}
QLineEdit:focus, QPlainTextEdit:focus {{ border: 1px solid {ACCENT}; }}
QPushButton, QToolButton {{ background: #333; border: 1px solid #1a1a1a; border-radius: 2px; padding: 3px 10px; }}
QPushButton:hover, QToolButton:hover {{ background: #3e3e3e; }}
QPushButton:pressed, QToolButton:pressed, QToolButton:checked {{ background: {ACCENT}; color: white; }}
QCheckBox::indicator {{ width: 12px; height: 12px; border: 1px solid #5a5a5a; border-radius: 2px;
                       background: #161616; }}
QCheckBox::indicator:hover {{ border: 1px solid {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border: 1px solid {ACCENT};
    image: url({_CHECK}); }}
QCheckBox::indicator:disabled {{ border: 1px solid #333; background: #1b1b1b; }}
QCheckBox:disabled {{ color: #555; }}
QComboBox:hover, QLineEdit:hover {{ border: 1px solid #4a4a4a; }}
QTabBar::tab:selected {{ border-bottom: 2px solid {ACCENT}; }}
QScrollBar:vertical {{ background: {PANEL}; width: 10px; }}
QScrollBar:horizontal {{ background: {PANEL}; height: 10px; }}
QScrollBar::handle {{ background: #444; border-radius: 4px; min-height: 20px; min-width: 20px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QProgressDialog {{ background: {BG}; }}
QStatusBar {{ background: {PANEL}; color: {TEXT_DIM}; }}
QToolTip {{ background: #2b2b2b; color: {TEXT_HI}; border: 1px solid #444; }}
"""
