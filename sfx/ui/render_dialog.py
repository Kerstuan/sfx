# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""Окно «Рендер»: файл, формат, кодек, fps, качество, размер, диапазон, звук, альфа."""
from __future__ import annotations

from ..i18n import tr

import json
import os

from PySide6.QtCore import QSettings, Qt
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QWidget)

from ..export import (CODECS, FORMATS, FPS_PRESETS, QUALITIES, QUALITY_NAMES, SCALES, SPEED_NAMES, SPEEDS,
                      RenderSettings, codec_has_lossless, output_path)
from ..model import timecode


def _settings() -> QSettings:
    return QSettings("sfx", "sfx")


def load_last() -> RenderSettings:
    try:
        return RenderSettings.from_dict(json.loads(_settings().value("render", "{}") or "{}"))
    except (TypeError, ValueError):
        return RenderSettings()


def save_last(s: RenderSettings):
    _settings().setValue("render", json.dumps(s.to_dict()))


def _fps_text(v: float) -> str:
    return f"{v:g}"


class RenderDialog(QDialog):
    def __init__(self, comp, parent=None, project_path: str | None = None, comps=None):
        super().__init__(parent)
        self.comp = comp
        self.setWindowTitle(tr("Рендер"))
        self.setMinimumWidth(520)
        self.s = load_last()
        f = QFormLayout(self)
        f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        # --- какую композицию рендерить (по умолчанию — открытую; можно любую, в т.ч. прекомп)
        self.comps = list(comps or [comp])
        self.comp_box = QComboBox()
        for c in self.comps:
            self.comp_box.addItem(f"{c.name}  ({c.width}×{c.height}, {_fps_text(c.fps)} fps)", c.id)
        self.comp_box.setCurrentIndex(next((i for i, c in enumerate(self.comps) if c is comp), 0))
        f.addRow(tr("Композиция"), self.comp_box)

        # --- файл
        last_dir = os.path.dirname(self.s.path) if self.s.path else ""
        if not last_dir or not os.path.isdir(last_dir):
            last_dir = os.path.dirname(project_path) if project_path else os.path.expanduser("~")
        self.path = QLineEdit(os.path.join(last_dir, comp.name))
        browse = QPushButton(tr("Обзор…"))
        browse.clicked.connect(self._browse)
        row = QWidget()
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.addWidget(self.path, 1)
        h.addWidget(browse)
        f.addRow(tr("Файл"), row)

        # --- формат и кодек
        self.fmt = QComboBox()
        for key, (label, ext, _, _) in FORMATS.items():
            self.fmt.addItem(f"{label}  ({ext})", key)
        self.codec = QComboBox()
        f.addRow(tr("Формат"), self.fmt)
        f.addRow(tr("Кодек"), self.codec)

        self.quality = QComboBox()
        self.speed = QComboBox()
        for k in SPEEDS:
            self.speed.addItem(SPEED_NAMES[k], k)
        f.addRow(tr("Качество"), self.quality)
        f.addRow(tr("Скорость сжатия"), self.speed)

        # --- fps: список + можно вписать своё
        self.fps = QComboBox()
        self.fps.setEditable(True)
        self.fps.addItem("", 0.0)          # текст — в _fill_comp_items
        for v in FPS_PRESETS:
            self.fps.addItem(_fps_text(v), float(v))
        f.addRow(tr("Кадров в секунду"), self.fps)

        self.size = QComboBox()
        for sc in SCALES:
            self.size.addItem("", sc)
        f.addRow(tr("Размер"), self.size)

        self.range = QComboBox()
        self.range.addItem("", True)
        self.range.addItem("", False)
        f.addRow(tr("Диапазон"), self.range)
        self._fill_comp_items()
        self.comp_box.currentIndexChanged.connect(self._on_comp)

        self.audio = QCheckBox(tr("Звук"))
        self.kbps = QComboBox()
        for k in (128, 192, 256, 320):
            self.kbps.addItem(tr("{} кбит/с").format(k), k)
        arow = QWidget()
        ah = QHBoxLayout(arow)
        ah.setContentsMargins(0, 0, 0, 0)
        ah.addWidget(self.audio)
        ah.addWidget(self.kbps)
        ah.addStretch(1)
        f.addRow(tr("Аудио"), arow)

        self.alpha = QCheckBox(tr("Прозрачный фон (альфа-канал)"))
        f.addRow("", self.alpha)

        self.summary = QLabel()
        self.summary.setStyleSheet("color:#7a7a7a;")
        self.summary.setWordWrap(True)
        f.addRow("", self.summary)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText(tr("Рендер"))
        bb.button(QDialogButtonBox.Cancel).setText(tr("Отмена"))
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)

        # --- значения из прошлого раза
        self._loading = True
        self._set_combo(self.fmt, self.s.fmt)
        self._fill_codecs()
        self._set_combo(self.codec, self.s.codec)
        self._fill_quality()
        self._set_combo(self.quality, self.s.quality)
        self._set_combo(self.speed, self.s.speed)
        if self.s.fps > 0:
            i = self.fps.findData(self.s.fps)
            if i >= 0:
                self.fps.setCurrentIndex(i)
            else:
                self.fps.setEditText(_fps_text(self.s.fps))
        self._set_combo(self.size, self.s.scale)
        self._set_combo(self.range, self.s.work_area)
        self.audio.setChecked(self.s.audio)
        self._set_combo(self.kbps, self.s.audio_kbps)
        self.alpha.setChecked(self.s.alpha)
        self._loading = False

        self.fmt.currentIndexChanged.connect(self._on_fmt)
        self.codec.currentIndexChanged.connect(self._on_codec)
        for w in (self.quality, self.speed, self.size, self.range, self.kbps):
            w.currentIndexChanged.connect(self._refresh)
        self.fps.currentTextChanged.connect(self._refresh)
        self.audio.toggled.connect(self._refresh)
        self.alpha.toggled.connect(self._refresh)
        self._refresh()

    # ------------------------------------------------------------------ помощники
    @staticmethod
    def _set_combo(cb: QComboBox, data):
        i = cb.findData(data)
        if i >= 0:
            cb.setCurrentIndex(i)

    def _fill_comp_items(self):
        """Подписи, зависящие от композиции: fps, размеры, диапазон."""
        c = self.comp
        self.fps.setItemText(0, tr("Как в композиции ({})").format(_fps_text(c.fps)))
        for i, sc in enumerate(SCALES):
            w, hh = max(2, int(c.width * sc)), max(2, int(c.height * sc))
            name = tr("Полный") if sc == 1 else f"{int(sc * 100)}%"
            self.size.setItemText(i, f"{name}  ({w}×{hh})")
        self.range.setItemText(0, tr("Рабочая область (B–N): {} – {}").format(
            timecode(c.work_in, c.fps), timecode(c.work_out, c.fps)))
        self.range.setItemText(1, tr("Вся композиция: {}").format(timecode(c.duration, c.fps)))

    def _on_comp(self, i):
        old = self.comp
        self.comp = self.comps[i]
        d, base = os.path.split(self.path.text())
        if os.path.splitext(base)[0] == old.name:          # имя файла было по композиции — меняем вслед
            self.path.setText(os.path.join(d, self.comp.name))
        self._fill_comp_items()
        self._refresh()

    def _fill_codecs(self):
        cur = self.codec.currentData()
        self.codec.blockSignals(True)
        self.codec.clear()
        from ..export import codec_available
        for k in FORMATS[self.fmt.currentData()][2]:
            if codec_available(k):
                self.codec.addItem(CODECS[k][0], k)
        self._set_combo(self.codec, cur)
        self.codec.blockSignals(False)

    def _fill_quality(self):
        cur = self.quality.currentData() or self.s.quality
        codec = self.codec.currentData()
        self.quality.blockSignals(True)
        self.quality.clear()
        for k in QUALITIES:
            if k == "lossless" and not codec_has_lossless(codec):
                continue
            self.quality.addItem(QUALITY_NAMES[k], k)
        self._set_combo(self.quality, cur if self.quality.findData(cur) >= 0 else "max")
        self.quality.blockSignals(False)

    def _on_fmt(self):
        self._fill_codecs()
        self._on_codec()

    def _on_codec(self):
        self._fill_quality()
        self._refresh()

    def _fps_value(self) -> float | None:
        i = self.fps.currentIndex()
        txt = self.fps.currentText().strip()
        if i >= 0 and self.fps.itemText(i) == txt:
            return float(self.fps.itemData(i))
        try:
            v = float(txt.replace(",", ".").split()[0])
        except (ValueError, IndexError):
            return None
        return v if 0 < v <= 240 else None

    def settings(self) -> RenderSettings:
        s = RenderSettings(
            path=self.path.text().strip(),
            fmt=self.fmt.currentData(),
            codec=self.codec.currentData(),
            fps=self._fps_value() or 0.0,
            quality=self.quality.currentData() or "high",
            speed=self.speed.currentData(),
            scale=float(self.size.currentData()),
            work_area=bool(self.range.currentData()),
            audio=self.audio.isChecked(),
            audio_kbps=int(self.kbps.currentData()),
            alpha=self.alpha.isChecked(),
        )
        s.normalize()
        return s

    def _refresh(self):
        if self._loading:
            return
        codec = self.codec.currentData()
        _, alpha_ok, quality_ok = CODECS[codec]
        self.alpha.setEnabled(alpha_ok)
        if not alpha_ok:
            self.alpha.setChecked(False)
        self.quality.setEnabled(quality_ok)
        self.speed.setEnabled(codec in ("h264", "h265", "av1", "vp9"))
        has_audio = FORMATS[self.fmt.currentData()][3] is not None
        self.audio.setEnabled(has_audio)
        self.kbps.setEnabled(has_audio and self.audio.isChecked())
        s = self.settings()
        fps = self._fps_value()
        if fps is None:
            self.summary.setText(tr("Неверное значение fps"))
            return
        n = len(s.frame_map(self.comp))
        w, h = s.size(self.comp)
        out = output_path(s.path, s) if s.path else "—"
        alpha = tr(", с альфой") if s.alpha else ""
        self.summary.setText(tr("{}×{}, {} fps, {} кадров ({:.2f} с){}\n→ {}").format(
            w, h, _fps_text(s.out_fps(self.comp)), n, n / s.out_fps(self.comp), alpha, out))

    def _browse(self):
        s = self.settings()
        label, ext = FORMATS[s.fmt][0], FORMATS[s.fmt][1]
        path, _ = QFileDialog.getSaveFileName(self, tr("Куда сохранить"), output_path(s.path, s).replace("_%05d", ""),
                                              f"{label} (*{ext})")
        if path:
            self.path.setText(path)
            self._refresh()

    def _accept(self):
        s = self.settings()
        if self._fps_value() is None:
            QMessageBox.warning(self, tr("Рендер"), tr("Неверное значение fps"))
            return
        if not s.path:
            QMessageBox.warning(self, tr("Рендер"), tr("Укажи, куда сохранить файл"))
            return
        s.path = os.path.abspath(os.path.expanduser(s.path))
        out = output_path(s.path, s)
        if s.fmt != "png" and os.path.exists(out):
            r = QMessageBox.question(self, tr("Рендер"), tr("Файл уже существует. Перезаписать?\n{}").format(out))
            if r != QMessageBox.Yes:
                return
        self.result_settings = s
        self.result_path = out
        save_last(s)
        self.accept()
