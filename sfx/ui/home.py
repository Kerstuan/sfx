# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""Окно «Проекты» (Home), как стартовый экран After Effects: новый проект, открыть, список сохранённых проектов.

Список = история открытий/сохранений (QSettings "recent_projects") + все .skl в SCAN_DIRS + автосохранения.
Файл, которого больше нет, показывается серым; при попытке открыть — сообщение и предложение убрать из списка."""
from __future__ import annotations

import glob
import os
import time

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from ..i18n import tr

SCAN_DIRS = [os.path.expanduser("~/Eddits")]     # папки, где лежат проекты пользователя
MAX_RECENT = 30


def _settings():
    return QSettings("sfx", "sfx")


def recent_list() -> list[str]:
    v = _settings().value("recent_projects", [])
    if isinstance(v, str):                 # QSettings отдаёт одиночное значение строкой
        v = [v]
    return [p for p in (v or []) if isinstance(p, str) and p]


def add_recent(path: str):
    """Вызывается после успешного открытия и сохранения: проект поднимается в начало списка."""
    path = os.path.abspath(path)
    lst = [path] + [p for p in recent_list() if os.path.abspath(p) != path]
    _settings().setValue("recent_projects", lst[:MAX_RECENT])


def remove_recent(path: str):
    path = os.path.abspath(path)
    _settings().setValue("recent_projects", [p for p in recent_list() if os.path.abspath(p) != path])


def ago(ts: float, now: float | None = None) -> str:
    """«только что», «5 мин назад», «3 ч назад», «вчера, 21:39», иначе дата."""
    now = time.time() if now is None else now
    d = max(0.0, now - ts)
    if d < 60:
        return tr("только что")
    if d < 3600:
        return tr("{} мин назад").format(int(d // 60))
    lt, ln = time.localtime(ts), time.localtime(now)
    same_day = lt.tm_yday == ln.tm_yday and lt.tm_year == ln.tm_year
    if same_day:
        return tr("{} ч назад").format(int(d // 3600))
    yesterday = time.localtime(now - 86400)
    if lt.tm_yday == yesterday.tm_yday and lt.tm_year == yesterday.tm_year:
        return tr("вчера, {}").format(time.strftime("%H:%M", lt))
    return time.strftime("%d.%m.%Y %H:%M", lt)


def _size(n: int) -> str:
    return tr("{} КБ").format(max(1, round(n / 1024))) if n < 1024 * 1024 else tr("{:.1f} МБ").format(n / 1024 / 1024)


def collect(autosave_dir: str | None = None) -> list[dict]:
    """Все известные проекты: {"path", "name", "mtime", "size", "exists", "kind": recent|folder|autosave}.
    Сначала существующие по времени сохранения (новые сверху), в конце — пропавшие."""
    seen, out = set(), []

    def add(p, kind):
        p = os.path.abspath(p)
        if p in seen:
            return
        seen.add(p)
        ok = os.path.isfile(p)
        st = os.stat(p) if ok else None
        out.append({"path": p, "name": os.path.splitext(os.path.basename(p))[0], "exists": ok, "kind": kind,
                    "mtime": st.st_mtime if st else 0.0, "size": st.st_size if st else 0})
    for p in recent_list():
        add(p, "recent")
    for d in SCAN_DIRS:
        for p in glob.glob(os.path.join(d, "*.skl")):
            add(p, "folder")
    if autosave_dir:
        for p in glob.glob(os.path.join(autosave_dir, "*_autosave.skl")):
            add(p, "autosave")
    out.sort(key=lambda r: (not r["exists"], -r["mtime"]))
    return out


class HomeDialog(QDialog):
    """Результат: self.choice = ("new", None) | ("open_dialog", None) | ("open", путь) | None (закрыли)."""

    def __init__(self, parent=None, autosave_dir: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("SFX — проекты"))
        self.resize(860, 520)
        self.choice = None
        self._autosave_dir = autosave_dir
        v = QVBoxLayout(self)
        top = QHBoxLayout()
        title = QLabel(tr("Проекты"))
        title.setStyleSheet("font-size:20px; font-weight:bold; color:#e6e6e6;")
        top.addWidget(title)
        top.addStretch(1)
        self.btn_new = QPushButton(tr("Новый проект"))
        self.btn_open = QPushButton(tr("Открыть проект…"))
        self.btn_new.setStyleSheet("QPushButton { background:#2d8ceb; color:white; font-weight:bold; padding:5px 16px; }")
        self.btn_new.clicked.connect(lambda: self._done(("new", None)))
        self.btn_open.clicked.connect(lambda: self._done(("open_dialog", None)))
        top.addWidget(self.btn_new)
        top.addWidget(self.btn_open)
        v.addLayout(top)
        self.list = QTreeWidget()
        self.list.setHeaderLabels([tr("Имя"), tr("Сохранён"), tr("Размер"), tr("Путь")])
        self.list.setRootIsDecorated(False)
        self.list.setAlternatingRowColors(False)   # в тёмной теме Fusion чередование давало почти белые строки
        self.list.header().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.list.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.list.header().setSectionResizeMode(3, QHeaderView.Stretch)
        self.list.itemActivated.connect(self._activate)        # двойной клик / Enter
        v.addWidget(self.list, 1)
        bottom = QHBoxLayout()
        self.hint = QLabel(tr("Двойной клик — открыть. В списке: недавние проекты, все .skl из ~/Eddits и автосохранения."))
        self.hint.setStyleSheet("color:#7a7a7a;")
        bottom.addWidget(self.hint, 1)
        self.show_on_start = QCheckBox(tr("Показывать при запуске"))
        self.show_on_start.setChecked(_settings().value("home_on_start", True, type=bool))
        self.show_on_start.toggled.connect(lambda on: _settings().setValue("home_on_start", bool(on)))
        bottom.addWidget(self.show_on_start)
        v.addLayout(bottom)
        self.fill()

    def fill(self):
        self.list.clear()
        self.rows = collect(self._autosave_dir)
        for r in self.rows:
            name = r["name"] + (tr("  (автосохранение)") if r["kind"] == "autosave" else "")
            it = QTreeWidgetItem([name, ago(r["mtime"]) if r["exists"] else tr("файл не найден"),
                                  _size(r["size"]) if r["exists"] else "—", r["path"]])
            it.setData(0, Qt.UserRole, r["path"])
            it.setToolTip(3, r["path"])
            if not r["exists"]:
                for col in range(4):
                    it.setForeground(col, QColor("#777777"))
            self.list.addTopLevelItem(it)
        if self.list.topLevelItemCount():
            self.list.setCurrentItem(self.list.topLevelItem(0))

    def _activate(self, it, _col=0):
        path = it.data(0, Qt.UserRole)
        if not os.path.isfile(path):          # перемещён или удалён
            r = QMessageBox.question(self, tr("Нет файла"),
                                     tr("Файл проекта не найден (перемещён или удалён):\n{}\n\nУбрать его из списка?").format(path),
                                     QMessageBox.Yes | QMessageBox.No)
            if r == QMessageBox.Yes:
                remove_recent(path)
                self.fill()
            return
        self._done(("open", path))

    def _done(self, choice):
        self.choice = choice
        self.accept()
