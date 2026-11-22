# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""Установка дополнительных движков (Roto Brush, 3D-трекер) прямо из программы — запускает install_*.sh."""
import os

from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QMessageBox

from ..i18n import tr

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def offer_install(parent, script: str, size: str, button, status, check, on_ready) -> None:
    """Спросить и поставить движок. Вывод скрипта идёт в status, по готовности (check() == True) — on_ready()."""
    if QMessageBox.question(parent, tr("Установка"),
                            tr("Движок не установлен. Скачать и установить сейчас? Загрузка: {}.").format(size)
                            ) != QMessageBox.StandardButton.Yes:
        return
    path = os.path.join(ROOT, script)
    proc = QProcess(parent)     # shortcut: закрытие панели обрывает установку; скрипты идемпотентны — повтор доставит
    proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
    proc.setWorkingDirectory(ROOT)
    button.setEnabled(False)
    status.setText(tr("Установка… это может занять несколько минут."))

    def out():
        lines = [ln for ln in bytes(proc.readAll()).decode(errors="replace").replace("\r", "\n").splitlines()
                 if ln.strip()]
        if lines:
            status.setText(tr("Установка: ") + lines[-1].strip()[-160:])

    def done(code, _status):
        button.setEnabled(True)
        if code == 0 and check():
            status.setText(tr("Установка завершена."))
            on_ready()
        else:
            status.setText(tr("Установка не удалась. Запустите в терминале, чтобы увидеть ошибку: ") + path)

    proc.readyRead.connect(out)
    proc.finished.connect(done)
    proc.start("bash", [path])
