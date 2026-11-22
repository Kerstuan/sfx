# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""Окно 3D-трекера камеры (Инструменты → 3D-трекер камеры, Ctrl+Alt+Shift+K).

Анализ — отдельный процесс (.venv-track, track_worker.py: OpenCV + SciPy). Здесь: кадр с точками
(цвет — ошибка точки), выбор точек мышью, 3D-вид облака и пути камеры, плоскость земли / начало /
масштаб, создание камеры, нулей и текста в SFX, экспорт скрипта для After Effects.
"""
from __future__ import annotations

import hashlib
import json
import math
import os

import numpy as np
from PySide6.QtCore import QPointF, QProcess, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QProgressBar,
                               QPushButton, QSlider, QSplitter, QVBoxLayout, QWidget)

from ..i18n import LANG, tr

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = os.path.join(ROOT, ".venv-track", "bin", "python")
WORKER = os.path.join(ROOT, "sfx", "track_worker.py")
QUALITY = [("Быстро", "Fast", 640, 700), ("Норма", "Normal", 960, 900), ("Точно", "Precise", 1280, 1200)]


def tracker_available() -> bool:
    return os.path.exists(PY)


def track_dir(layer, f0, f1) -> str:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    h = hashlib.sha1(f"{layer.source}|{f0}|{f1}".encode()).hexdigest()[:12]
    return os.path.join(base, "sfx", "camtrack", h)


def err_color(e: float) -> QColor:
    if e < 1.0:
        return QColor(60, 230, 110)
    if e < 2.5:
        return QColor(240, 210, 60)
    return QColor(240, 70, 60)


class TrackCanvas(QWidget):
    """Кадр + точки решения. Клик — выбрать точку, Shift — добавить, рамка — выбрать несколько."""
    selection = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(480, 270)
        self.img: QImage | None = None
        self.pts: list = []          # [(id, x, y, err)]
        self.sel: list[int] = []
        self._drag = None

    def _rect(self):
        if self.img is None:
            return QRectF(0, 0, 1, 1)
        k = min(self.width() / self.img.width(), self.height() / self.img.height())
        return QRectF((self.width() - self.img.width() * k) / 2, (self.height() - self.img.height() * k) / 2,
                      self.img.width() * k, self.img.height() * k)

    def _scr(self, x, y):
        r = self._rect()
        k = r.width() / self.img.width()
        return QPointF(r.x() + x * k, r.y() + y * k)

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#141414"))
        if self.img is None:
            p.setPen(QColor("#888"))
            p.drawText(self.rect(), Qt.AlignCenter, tr("Выберите видеослой и нажмите «Анализ»"))
            return
        p.drawImage(self._rect(), self.img)
        p.setRenderHint(QPainter.Antialiasing)
        sel = set(self.sel)
        for pid, x, y, e in self.pts:
            q = self._scr(x, y)
            if pid in sel:
                p.setPen(QPen(QColor(80, 220, 255), 2))
                p.setBrush(Qt.NoBrush)
                p.drawEllipse(q, 7, 7)
            p.setPen(Qt.NoPen)
            p.setBrush(err_color(e))
            p.drawEllipse(q, 2.6, 2.6)
        if self._drag and self._drag[1] is not None:
            p.setPen(QPen(QColor(80, 220, 255), 1, Qt.DashLine))
            p.setBrush(QColor(80, 220, 255, 30))
            p.drawRect(QRectF(self._drag[0], self._drag[1]).normalized())

    def mousePressEvent(self, e):
        self._drag = [e.position(), None, bool(e.modifiers() & Qt.ShiftModifier)]

    def mouseMoveEvent(self, e):
        if self._drag:
            self._drag[1] = e.position()
            self.update()

    def mouseReleaseEvent(self, e):
        if not self._drag or self.img is None:
            return
        a, b, add = self._drag
        self._drag = None
        if b is None or (b - a).manhattanLength() < 4:          # клик — ближайшая точка
            best, bd = None, 14.0
            for pid, x, y, _e in self.pts:
                d = (self._scr(x, y) - a).manhattanLength()
                if d < bd:
                    best, bd = pid, d
            new = [best] if best is not None else []
        else:
            r = QRectF(a, b).normalized()
            new = [pid for pid, x, y, _e in self.pts if r.contains(self._scr(x, y))]
        self.sel = list(dict.fromkeys((self.sel if add else []) + new))
        self.selection.emit()
        self.update()


class Scene3D(QWidget):
    """Облако точек и путь камеры; мышь — вращать, колесо — приблизить."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(260, 200)
        self.P = np.zeros((0, 3))
        self.E = np.zeros(0)
        self.C = np.zeros((0, 3))
        self.cur = None
        self.sel = np.zeros((0, 3))
        self.yaw, self.pitch, self.zoom = 0.6, 0.35, 1.0
        self._last = None

    def set_data(self, P, E, C, cur, sel):
        self.P, self.E, self.C, self.cur, self.sel = P, E, C, cur, sel
        self.update()

    def mousePressEvent(self, e):
        self._last = e.position()

    def mouseMoveEvent(self, e):
        if self._last is not None:
            d = e.position() - self._last
            self.yaw += d.x() * 0.01
            self.pitch = max(-1.5, min(1.5, self.pitch + d.y() * 0.01))
            self._last = e.position()
            self.update()

    def mouseReleaseEvent(self, e):
        self._last = None

    def wheelEvent(self, e):
        self.zoom *= 1.15 if e.angleDelta().y() > 0 else 1 / 1.15
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#101010"))
        if len(self.P) == 0:
            return
        allp = np.vstack([self.P, self.C]) if len(self.C) else self.P
        c = np.median(allp, axis=0)
        sc = np.percentile(np.linalg.norm(allp - c, axis=1), 90) or 1.0
        cy, sy, cp, sp = math.cos(self.yaw), math.sin(self.yaw), math.cos(self.pitch), math.sin(self.pitch)
        R = np.array([[cy, 0, -sy], [0, 1, 0], [sy, 0, cy]]) @ np.eye(3)
        R = np.array([[1, 0, 0], [0, cp, -sp], [0, sp, cp]]) @ R
        k = min(self.width(), self.height()) * 0.42 * self.zoom / sc

        def scr(X):
            q = (np.asarray(X) - c) @ R.T
            return q[..., 0] * k + self.width() / 2, q[..., 1] * k + self.height() / 2
        p.setRenderHint(QPainter.Antialiasing)
        xs, ys = scr(self.P)
        for x, y, e in zip(xs, ys, self.E):
            p.setPen(err_color(e))
            p.drawPoint(QPointF(x, y))
        if len(self.C):
            xs, ys = scr(self.C)
            p.setPen(QPen(QColor(255, 160, 60), 1.5))
            for i in range(len(xs) - 1):
                p.drawLine(QPointF(xs[i], ys[i]), QPointF(xs[i + 1], ys[i + 1]))
        if self.cur is not None:
            x, y = scr(self.cur)
            p.setPen(QPen(QColor(255, 255, 255), 2))
            p.drawEllipse(QPointF(float(x), float(y)), 5, 5)
        if len(self.sel):
            xs, ys = scr(self.sel)
            p.setPen(QPen(QColor(80, 220, 255), 2))
            for x, y in zip(xs, ys):
                p.drawEllipse(QPointF(x, y), 4, 4)
        p.setPen(QColor("#777"))
        p.drawText(8, 16, tr("3D: тяните мышью — вращать, колесо — масштаб"))


class TrackerPanel(QWidget):
    def __init__(self, editor, notify=None, parent=None):
        super().__init__(parent)
        self.ed = editor
        self.notify = notify or (lambda m: None)
        self.setWindowTitle(tr("3D-трекер камеры — SFX"))
        self.layer = None
        self.sol = None
        self.proc = None
        self._buf = b""
        self.idx = 0

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(QLabel(tr("Качество")))
        self.cb_q = QComboBox()
        for ru, en, *_ in QUALITY:
            self.cb_q.addItem(en if LANG == "en" else ru)
        self.cb_q.setCurrentIndex(1)
        top.addWidget(self.cb_q)
        top.addWidget(QLabel(tr("Угол обзора, если известен")))
        self.sp_fov = QDoubleSpinBox()
        self.sp_fov.setRange(0, 170)
        self.sp_fov.setSpecialValueText(tr("авто"))
        self.sp_fov.setSuffix("°")
        top.addWidget(self.sp_fov)
        self.btn_run = QPushButton(tr("Анализ выбранного слоя"))
        self.btn_run.clicked.connect(self.run)
        top.addWidget(self.btn_run)
        top.addStretch(1)
        lay.addLayout(top)

        split = QSplitter(Qt.Horizontal)
        self.canvas = TrackCanvas()
        self.canvas.selection.connect(self._sel_changed)
        self.view3d = Scene3D()
        split.addWidget(self.canvas)
        split.addWidget(self.view3d)
        split.setSizes([700, 300])
        lay.addWidget(split, 1)

        bot = QHBoxLayout()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setEnabled(False)
        self.slider.valueChanged.connect(self._show)
        bot.addWidget(self.slider, 1)
        self.lbl_frame = QLabel("—")
        bot.addWidget(self.lbl_frame)
        lay.addLayout(bot)

        self.info = QLabel(tr("Подсказка: камера должна смещаться (не только поворачиваться) — нужен параллакс."))
        self.info.setWordWrap(True)
        lay.addWidget(self.info)
        self.progress = QProgressBar()
        self.progress.hide()
        lay.addWidget(self.progress)

        row = QHBoxLayout()
        self.btn_ground = QPushButton(tr("Плоскость земли по выбранным (≥3)"))
        self.btn_ground.clicked.connect(self.set_ground)
        self.btn_origin = QPushButton(tr("Начало координат в точке"))
        self.btn_origin.clicked.connect(self.set_origin)
        row.addWidget(self.btn_ground)
        row.addWidget(self.btn_origin)
        row.addWidget(QLabel(tr("Между 2 точками =")))
        self.sp_dist = QDoubleSpinBox()
        self.sp_dist.setRange(1, 1e6)
        self.sp_dist.setValue(500)
        self.sp_dist.setSuffix(" px")
        row.addWidget(self.sp_dist)
        self.btn_scale = QPushButton(tr("Задать масштаб"))
        self.btn_scale.clicked.connect(self.set_scale)
        row.addWidget(self.btn_scale)
        row.addStretch(1)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        self.btn_cam = QPushButton(tr("Создать камеру"))
        self.btn_cam.clicked.connect(self.make_camera)
        self.btn_null = QPushButton(tr("Нули в выбранных точках"))
        self.btn_null.clicked.connect(self.make_nulls)
        self.ed_text = QLineEdit("SFX")
        self.ed_text.setFixedWidth(120)
        self.btn_text = QPushButton(tr("Текст на плоскости"))
        self.btn_text.clicked.connect(self.make_text)
        self.btn_jsx = QPushButton(tr("Экспорт в After Effects (.jsx)…"))
        self.btn_jsx.clicked.connect(self.export_jsx)
        for w in (self.btn_cam, self.btn_null, self.ed_text, self.btn_text, self.btn_jsx):
            row2.addWidget(w)
        row2.addStretch(1)
        lay.addLayout(row2)
        self.resize(1200, 760)
        self._enable()
        editor.time_changed.connect(self._follow_time)
        if not tracker_available():
            self.btn_run.setEnabled(False)
            self.info.setText(tr("Движок трекера не установлен: запустите ~/sfx/install_tracker.sh"))

    def _enable(self):
        has = self.sol is not None
        for b in (self.btn_ground, self.btn_origin, self.btn_scale, self.btn_cam, self.btn_null, self.btn_text,
                  self.btn_jsx):
            b.setEnabled(has)

    # ------------------------------------------------------------------ анализ
    def run(self):
        layer = self.ed.current()
        if layer is None or layer.kind != "video":
            self.info.setText(tr("Выберите видеослой на таймлайне."))
            return
        comp = self.ed.comp
        reader = self.ed.pool.video_for(layer.source, 1.0)[0]
        vfps = reader.fps
        a = layer.src_time(layer.local(layer.in_point), comp.fps)
        b = layer.src_time(layer.local(max(layer.in_point, layer.out_point - 1)), comp.fps)
        f0, f1 = int(round(min(a, b) * vfps)), int(round(max(a, b) * vfps)) + 1
        self.layer = layer
        self.out = track_dir(layer, f0, f1)
        _ru, _en, tw, corners = QUALITY[self.cb_q.currentIndex()]
        cfg = {"video": layer.source, "out": self.out, "start": f0, "end": f1, "track_w": tw, "corners": corners}
        if self.sp_fov.value() > 0:
            vw = reader.width if hasattr(reader, "width") else 1920
            cfg["focal_px"] = vw / 2 / math.tan(math.radians(self.sp_fov.value()) / 2)
        self.proc = QProcess(self)
        self.proc.setProgram(PY)
        self.proc.setArguments([WORKER, json.dumps(cfg)])
        self.proc.setWorkingDirectory(ROOT)
        self.proc.readyReadStandardOutput.connect(self._read)
        self._buf = b""
        self.btn_run.setEnabled(False)
        self.progress.show()
        self.info.setText(tr("Анализ кадров {}–{}…").format(f0, f1 - 1))
        self.proc.start()

    def _read(self):
        self._buf += bytes(self.proc.readAllStandardOutput())
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if "progress" in r:
                names = {"decode": tr("Чтение кадров"), "track": tr("Слежение за точками"), "focal": tr("Фокус"),
                         "solve": tr("Решение камеры"), "ba": tr("Bundle adjustment")}
                self.progress.setMaximum(max(1, int(r["of"])))
                self.progress.setValue(int(r["progress"]))
                self.progress.setFormat(names.get(r.get("stage"), "") + ": %v / %m")
                continue
            self.btn_run.setEnabled(True)
            self.progress.hide()
            if "error" in r:
                why = {"parallax": tr("Мало параллакса: камера почти не смещается (штатив, панорама) — глубину не восстановить."),
                       "too_short": tr("Слишком короткий фрагмент (нужно ≥ 10 кадров).")}.get(r["error"], r["error"])
                self.info.setText(tr("Не удалось решить камеру: ") + why)
                return
            self.load(self.out)

    def load(self, folder):
        from ..camtrack import Solution
        self.sol = Solution(folder)
        d = self.sol.d
        q = self.sol.quality()
        self.info.setText(tr("Решено кадров {} из {} · точек {} · ошибка репроекции {:.2f} px ({}) · угол обзора {:.1f}° "
                             "(фокус {:.0f} px) · {:.0f} с").format(
            d["solved"], d["n"], len(d["points"]), d["rms_px"], q[1] if LANG == "en" else q[0], d["fov_h"], d["f"],
            d.get("seconds", 0)))
        self.slider.setRange(0, d["n"] - 1)
        self.slider.setEnabled(True)
        self._enable()
        self._follow_time(self.ed.time)

    # ------------------------------------------------------------------ просмотр
    def _idx_of_time(self, t):
        lay, comp = self.layer, self.ed.comp
        return int(round(lay.src_time(lay.local(t), comp.fps) * self.sol.d["fps"])) - self.sol.d["start"]

    def _follow_time(self, t):
        if self.sol is None or self.layer is None:
            return
        i = max(0, min(self.sol.n - 1, self._idx_of_time(t)))
        self.slider.blockSignals(True)
        self.slider.setValue(i)
        self.slider.blockSignals(False)
        self._show(i)

    def _show(self, i):
        self.idx = i
        sol = self.sol
        try:
            reader = self.ed.pool.video_for(self.layer.source, 1.0)[0]
            img = reader.frame_at((sol.d["start"] + i) / sol.d["fps"], 1.0)
        except Exception:
            img = None
        if img is not None and img.width() != sol.d["w"]:
            img = img.scaled(sol.d["w"], sol.d["h"])
        self.canvas.img = img
        self.canvas.pts = [(pid, x, y, sol.points[pid][1]) for pid, x, y in (sol.obs[i] if i < len(sol.obs) else [])
                           if pid in sol.points]
        rms = sol.d["frame_rms_px"].get(str(i))
        self.lbl_frame.setText(f"{i + 1} / {sol.n}" + (f" · {rms:.2f} px" if rms is not None else " · " + tr("не решён")))
        self.canvas.update()
        self._update3d()

    def _update3d(self):
        sol = self.sol
        w, h = self.ed.comp.width, self.ed.comp.height
        items = list(sol.points.items())
        P = np.array([sol.to_scene(X, w, h) for _, (X, _e) in items]) if items else np.zeros((0, 3))
        E = np.array([e for _, (_X, e) in items]) if items else np.zeros(0)
        C = np.array([cs[0] for cs in (sol.cam_scene(i, w, h) for i in range(sol.n)) if cs is not None])
        cur = sol.cam_scene(self.idx, w, h)
        S = np.array([sol.to_scene(sol.points[i][0], w, h) for i in self.canvas.sel if i in sol.points])
        self.view3d.set_data(P, E, C, cur[0] if cur else None, S if len(S) else np.zeros((0, 3)))

    def _sel_changed(self):
        n = len(self.canvas.sel)
        self.notify(tr("Выбрано точек: {}").format(n))
        self._update3d()

    # ------------------------------------------------------------------ сцена
    def set_ground(self):
        if len(self.canvas.sel) < 3 or not self.sol.set_ground(self.canvas.sel):
            self.info.setText(tr("Выберите на кадре не меньше 3 точек, лежащих на полу/столе (рамкой или Shift+клик)."))
            return
        self.info.setText(tr("Плоскость земли задана, начало координат — в центре выбранных точек."))
        self._update3d()

    def set_origin(self):
        if not self.canvas.sel or not self.sol.set_origin(self.canvas.sel[0]):
            self.info.setText(tr("Выберите одну точку."))
            return
        self.info.setText(tr("Начало координат перенесено в выбранную точку."))
        self._update3d()

    def set_scale(self):
        s = self.canvas.sel
        if len(s) != 2 or not self.sol.set_distance(s[0], s[1], self.sp_dist.value()):
            self.info.setText(tr("Выберите ровно 2 точки с известным расстоянием между ними."))
            return
        self.info.setText(tr("Масштаб сцены задан."))
        self._update3d()

    def make_camera(self):
        from ..camtrack import build_sfx_camera
        cam = build_sfx_camera(self.ed, self.layer, self.sol, tr("Камера трекера"))
        self.info.setText(tr("Камера создана: ключи на каждом кадре слоя. 3D-слои теперь «прилипают» к сцене.")
                          if cam else tr("Нет решённых кадров в диапазоне слоя."))

    def make_nulls(self):
        from ..camtrack import build_sfx_nulls
        if not self.canvas.sel:
            self.info.setText(tr("Выберите точки на кадре."))
            return
        out = build_sfx_nulls(self.ed, self.layer, self.sol, self.canvas.sel, tr("Нуль трекера {}"))
        self.info.setText(tr("Создано 3D-нулей: {}. Привяжите к ним слои через Parent.").format(len(out)))

    def make_text(self):
        from ..camtrack import build_sfx_text_on_plane
        if not self.canvas.sel:
            self.info.setText(tr("Выберите точки, где должен лежать текст."))
            return
        build_sfx_text_on_plane(self.ed, self.layer, self.sol, self.canvas.sel, self.ed_text.text() or "SFX")
        self.info.setText(tr("Текст положен на плоскость. Для точного положения сначала задайте плоскость земли."))

    def export_jsx(self):
        from ..camtrack import export_jsx
        path, _ = QFileDialog.getSaveFileName(self, tr("Экспорт в After Effects"),
                                              os.path.expanduser("~/sfx_camera_track.jsx"), "ExtendScript (*.jsx)")
        if not path:
            return
        comp = self.ed.comp
        sx = self.layer.props["scale"].value_at(0)[0] if "scale" in self.layer.props else 100.0
        n = export_jsx(path, self.sol, comp.width, comp.height, comp.fps, self.canvas.sel, cloud=300, layer_scale=sx)
        self.info.setText(tr("Сохранено: {} (ключей камеры: {}). В AE: File → Scripts → Run Script File…").format(path, n))
