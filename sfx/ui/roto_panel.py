# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""Панель Roto Brush (Alt+W): выделение объекта кистью и слежение по видео движком SAM 2.1.

Модель работает в отдельном процессе (.venv-roto, roto_worker.py) — окно не подвисает,
а падение модели не роняет программу. Результат — эффект «Roto Brush» на слое: альфа из масок
сессии с уточнением края (Refine Edge, Feather, Choke…), его видно во вьювере и при экспорте.
"""
from __future__ import annotations

import hashlib
import json
import math
import os

import numpy as np
from PySide6.QtCore import QPointF, QProcess, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QProgressBar, QPushButton, QSlider, QSpinBox,
                               QVBoxLayout, QWidget)

from ..i18n import tr
from .installer import offer_install

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PY = os.path.join(ROOT, ".venv-roto", "bin", "python")
WORKER = os.path.join(ROOT, "sfx", "roto_worker.py")


def roto_available() -> bool:
    return os.path.exists(PY) and os.path.exists(os.path.join(ROOT, "models", "sam2.1_hiera_tiny.pt"))


def session_dir(layer) -> str:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    h = hashlib.sha1(f"{layer.id}|{layer.source}".encode()).hexdigest()[:12]
    return os.path.join(base, "sfx", "roto", h)


class RotoCanvas(QWidget):
    """Кадр сессии + маска. ЛКМ — добавить (+), Alt+ЛКМ или ПКМ — убрать (−). Колесо+Ctrl — размер кисти."""
    stroke = Signal(list, bool)             # точки в пикселях кадра, добавить?

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(480, 270)
        self.setMouseTracking(True)
        self.frame: QImage | None = None
        self.mask: QImage | None = None
        self.brush = 30.0                   # радиус в пикселях кадра
        self._pts: list = []
        self._add = True
        self._hover = None

    def set_images(self, frame: QImage | None, mask: QImage | None):
        self.frame, self.mask = frame, mask
        self.update()

    def _rect(self) -> QRectF:
        if self.frame is None:
            return QRectF(0, 0, 1, 1)
        fw, fh = self.frame.width(), self.frame.height()
        k = min(self.width() / fw, self.height() / fh)
        return QRectF((self.width() - fw * k) / 2, (self.height() - fh * k) / 2, fw * k, fh * k)

    def _to_frame(self, pos) -> QPointF:
        r = self._rect()
        return QPointF((pos.x() - r.x()) / r.width() * self.frame.width(),
                       (pos.y() - r.y()) / r.height() * self.frame.height())

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#141414"))
        if self.frame is None:
            p.setPen(QColor("#888"))
            p.drawText(self.rect(), Qt.AlignCenter, tr("Выберите видеослой и нажмите «Начать»"))
            return
        r = self._rect()
        p.drawImage(r, self.frame)
        if self.mask is not None:
            p.setOpacity(0.45)
            p.drawImage(r, self.mask)
            p.setOpacity(1.0)
        k = r.width() / self.frame.width()
        if len(self._pts) > 1:
            p.setPen(QPen(QColor(80, 220, 120) if self._add else QColor(240, 70, 70), max(2.0, self.brush * k * 2),
                          Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            p.setOpacity(0.5)
            for a, b in zip(self._pts, self._pts[1:]):
                p.drawLine(QPointF(r.x() + a.x() * k, r.y() + a.y() * k), QPointF(r.x() + b.x() * k, r.y() + b.y() * k))
            p.setOpacity(1.0)
        if self._hover is not None:
            p.setPen(QPen(QColor(255, 255, 255, 180), 1))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(self._hover, self.brush * k, self.brush * k)

    def mousePressEvent(self, e):
        if self.frame is None:
            return
        self._add = not (e.button() == Qt.RightButton or e.modifiers() & Qt.AltModifier)
        self._pts = [self._to_frame(e.position())]
        self.update()

    def mouseMoveEvent(self, e):
        self._hover = e.position()
        if self._pts:
            self._pts.append(self._to_frame(e.position()))
        self.update()

    def mouseReleaseEvent(self, e):
        if self._pts:
            pts, self._pts = self._pts, []
            self.stroke.emit([(q.x(), q.y()) for q in pts], self._add)
            self.update()

    def wheelEvent(self, e):
        if e.modifiers() & Qt.ControlModifier:
            self.brush = max(3.0, min(400.0, self.brush * (1.15 if e.angleDelta().y() > 0 else 1 / 1.15)))
            self.update()


class RotoPanel(QWidget):
    def __init__(self, editor, notify=None, parent=None):
        super().__init__(parent)
        self.ed = editor
        self.notify = notify or (lambda m: None)
        self.setWindowTitle("Roto Brush — SFX")
        self.layer = None
        self.session = None
        self.meta = None
        self.idx = 0
        self.proc: QProcess | None = None
        self._buf = b""
        self._queue: list = []
        self._busy = False
        self._ready = False

        lay = QVBoxLayout(self)
        top = QHBoxLayout()
        self.btn_start = QPushButton(tr("Начать для выбранного слоя"))
        self.btn_start.clicked.connect(self.start)
        top.addWidget(self.btn_start)
        self.btn_back = QPushButton("◀ " + tr("Отслеживать назад"))
        self.btn_back.clicked.connect(lambda: self.track(False))
        self.btn_fwd = QPushButton(tr("Отслеживать вперёд") + " ▶")
        self.btn_fwd.clicked.connect(lambda: self.track(True))
        self.btn_all = QPushButton("◀ " + tr("Отследить все кадры") + " ▶")
        self.btn_all.setToolTip(tr("Нейросеть сама проходит по всем кадрам слоя: вперёд до конца, затем назад до начала"))
        self.btn_all.clicked.connect(self.track_all)
        self.btn_clear = QPushButton(tr("Очистить кадр"))
        self.btn_clear.clicked.connect(self.clear_frame)
        for b in (self.btn_back, self.btn_all, self.btn_fwd, self.btn_clear):
            top.addWidget(b)
        self.chk_auto = QCheckBox(tr("Авто: после выделения отследить все кадры"))
        self.chk_auto.setChecked(True)
        top.addWidget(self.chk_auto)
        lay.addLayout(top)

        opt = QHBoxLayout()
        self.chk_mask = QCheckBox(tr("Кисть по маске (точная правка)"))
        self.chk_mask.setToolTip(tr("Выключено: штрих превращается в точки для нейросети (быстро выделить объект).\n"
                                    "Включено: штрих рисует прямо по маске — для точной правки края."))
        opt.addWidget(self.chk_mask)
        opt.addWidget(QLabel(tr("Кисть")))
        self.sp_brush = QSpinBox()
        self.sp_brush.setRange(3, 400)
        self.sp_brush.setValue(30)
        self.sp_brush.setSuffix(" px")
        opt.addWidget(self.sp_brush)
        opt.addStretch(1)
        opt.addWidget(QLabel(tr("Сглаживание во времени")))
        self.sl_stab = QSlider(Qt.Horizontal)
        self.sl_stab.setRange(0, 100)
        self.sl_stab.setValue(50)
        self.sl_stab.setFixedWidth(110)
        opt.addWidget(self.sl_stab)
        self.btn_stab = QPushButton(tr("Сгладить"))
        self.btn_stab.clicked.connect(self.stabilize)
        opt.addWidget(self.btn_stab)
        self.btn_export = QPushButton(tr("Экспорт для After Effects…"))
        self.btn_export.clicked.connect(self.export)
        opt.addWidget(self.btn_export)
        lay.addLayout(opt)

        self.canvas = RotoCanvas()
        self.canvas.stroke.connect(self.on_stroke)
        self.sp_brush.valueChanged.connect(lambda v: setattr(self.canvas, "brush", float(v)))
        lay.addWidget(self.canvas, 1)

        bot = QHBoxLayout()
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setEnabled(False)
        self.slider.valueChanged.connect(self.on_slider)
        bot.addWidget(self.slider, 1)
        self.lbl_frame = QLabel("—")
        bot.addWidget(self.lbl_frame)
        lay.addLayout(bot)
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.hide()
        lay.addWidget(self.progress)
        self.status = QLabel(tr("Подсказка: ЛКМ — добавить к объекту, Alt+ЛКМ или ПКМ — убрать, Ctrl+колесо — размер кисти."))
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.resize(980, 720)
        self._set_enabled(False)
        editor.time_changed.connect(self._follow_time)
        if not roto_available():
            self.status.setText(tr("Нейросеть не установлена: нажмите «Начать», чтобы скачать и установить её."))

    # ------------------------------------------------------------------ процесс модели
    def _ensure_proc(self):
        if self.proc is not None and self.proc.state() != QProcess.NotRunning:
            return
        self.proc = QProcess(self)
        self.proc.setProgram(PY)
        self.proc.setArguments([WORKER])
        self.proc.setWorkingDirectory(ROOT)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._died)
        self._ready = False
        self._buf = b""
        self.proc.start()

    def _send(self, msg: dict, on_done=None):
        self._queue.append((msg, on_done))
        self._pump()

    def _pump(self):
        if self._busy or not self._queue or not self._ready:
            if not self._ready:
                self._ensure_proc()
            return
        msg, cb = self._queue[0]
        self._busy = True
        self._set_enabled(False)
        self.proc.write((json.dumps(msg) + "\n").encode())

    def _read(self):
        self._buf += bytes(self.proc.readAllStandardOutput())
        while b"\n" in self._buf:
            line, self._buf = self._buf.split(b"\n", 1)
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("ready"):
                self._ready = True
                self._pump()
                continue
            if "progress" in r:
                self.progress.show()
                self.progress.setMaximum(int(r["of"]))
                self.progress.setValue(int(r["progress"]))
                stage = {"frames": tr("Кадры"), "track": tr("Слежение"), "stabilize": tr("Сглаживание")}.get(r.get("stage"), "")
                extra = f" · {r['fps']} " + tr("к/с") if r.get("fps") else ""
                self.progress.setFormat(f"{stage}: %v / %m{extra}")
                if r.get("stage") == "track" and self.meta:
                    self._show(self.idx)
                continue
            msg, cb = self._queue.pop(0) if self._queue else (None, None)
            self._busy = False
            self.progress.hide()
            self._set_enabled(True)
            if "error" in r:
                err = r["error"]
                if err == "no_prompt":
                    err = tr("На кадре нет выделения — сначала проведите кистью по объекту.")
                self.status.setText(tr("Ошибка: ") + err)
            elif cb:
                cb(r)
            self._pump()

    def _died(self, *_):
        self._ready = False
        if self._busy:
            self._busy = False
            self._queue.clear()
            self.progress.hide()
            self.status.setText(tr("Процесс нейросети завершился. Нажмите «Начать» ещё раз."))
            self._set_enabled(True)

    def closeEvent(self, e):
        if self.proc is not None and self.proc.state() != QProcess.NotRunning:
            self.proc.write(b'{"cmd":"quit"}\n')
            self.proc.waitForFinished(2000)
        super().closeEvent(e)

    def _set_enabled(self, on: bool):
        has = self.meta is not None
        for b in (self.btn_back, self.btn_fwd, self.btn_all, self.btn_clear, self.btn_stab, self.btn_export):
            b.setEnabled(on and has)
        self.btn_start.setEnabled(on or not self._busy)
        self.canvas.setEnabled(on and has)

    # ------------------------------------------------------------------ сессия
    def start(self):
        if not roto_available():
            offer_install(self, "install_roto.sh", "~3 GB", self.btn_start, self.status, roto_available, self.start)
            return
        layer = self.ed.current()
        if layer is None or layer.kind != "video":
            self.status.setText(tr("Выберите видеослой на таймлайне."))
            return
        comp = self.ed.comp
        try:
            vfps = self.ed.pool.video_for(layer.source, 1.0)[0].fps
        except Exception:
            vfps = comp.fps
        a = layer.src_time(layer.local(layer.in_point), comp.fps)
        b = layer.src_time(layer.local(max(layer.in_point, layer.out_point - 1)), comp.fps)
        f0, f1 = int(round(min(a, b) * vfps)), int(round(max(a, b) * vfps)) + 1
        self.layer, self.session = layer, session_dir(layer)
        self.status.setText(tr("Готовлю кадры {}–{} ({} шт.)…").format(f0, f1 - 1, f1 - f0))
        self._send({"cmd": "open", "session": self.session, "video": layer.source, "start": f0, "end": f1},
                   self._opened)

    def _opened(self, r):
        from ..matte import session_meta
        self.meta = session_meta(self.session)
        fx = next((f for f in self.layer.effects if f.get("type") == "roto"), None)
        if fx is None:
            self.ed.select([self.layer.id])
            self.ed.add_effect("roto", [self.layer])
            fx = self.layer.effects[-1]
        fx.setdefault("opts", {})["session"] = self.session
        self.ed.notify()
        self.slider.blockSignals(True)
        self.slider.setRange(0, max(0, r["n"] - 1))
        self.slider.setEnabled(True)
        self.slider.blockSignals(False)
        self._set_enabled(True)
        self._follow_time(self.ed.time)
        self.status.setText(tr("Проведите кистью по объекту на этом кадре, затем «Отслеживать вперёд».")
                            + (" " + tr("(продолжена прошлая сессия)") if r.get("resumed") else ""))

    def _frame_of_time(self, t: int) -> int:
        lay, comp = self.layer, self.ed.comp
        st = lay.src_time(lay.local(t), comp.fps)
        return int(round(st * self.meta["fps"])) - int(self.meta["start"])

    def _follow_time(self, t):
        if not self.meta or self.layer is None:
            return
        i = max(0, min(self.meta["n"] - 1, self._frame_of_time(t)))
        self.slider.blockSignals(True)
        self.slider.setValue(i)
        self.slider.blockSignals(False)
        self._show(i)

    def on_slider(self, i):
        self._show(i)
        # время композиции → ближайший кадр (линейно; при анимированной скорости — приблизительно)
        lay, comp = self.layer, self.ed.comp
        t0 = lay.in_point
        best, bd = t0, 1e9
        for t in range(int(lay.in_point), int(lay.out_point)):
            d = abs(self._frame_of_time(t) - i)
            if d < bd:
                best, bd = t, d
                if d == 0:
                    break
        self.ed.time_changed.disconnect(self._follow_time)
        self.ed.set_time(best)
        self.ed.time_changed.connect(self._follow_time)

    def _show(self, i):
        self.idx = i
        sd = self.session
        fr = QImage(os.path.join(sd, "frames", f"{i:05d}.jpg"))
        mk = None
        mp = os.path.join(sd, "masks", f"{i:05d}.png")
        if os.path.exists(mp):
            g = QImage(mp).convertToFormat(QImage.Format_Grayscale8)
            mk = self._alpha_from_gray(g, QColor(70, 140, 255))
        prompted = str(i) in (self._prompts() or {})
        self.lbl_frame.setText(f"{i + 1} / {self.meta['n']}" + ("  ●" if prompted else ""))
        self.canvas.set_images(fr if not fr.isNull() else None, mk)

    @staticmethod
    def _alpha_from_gray(g: QImage, col: QColor) -> QImage:
        a = np.frombuffer(g.constBits(), np.uint8).reshape(g.height(), g.bytesPerLine())[:, :g.width()]
        out = np.empty((g.height(), g.width(), 4), np.uint8)
        al = a.astype(np.float32) / 255.0
        out[..., 0] = (col.blue() * al).astype(np.uint8)
        out[..., 1] = (col.green() * al).astype(np.uint8)
        out[..., 2] = (col.red() * al).astype(np.uint8)
        out[..., 3] = a
        return QImage(out.tobytes(), g.width(), g.height(), g.width() * 4, QImage.Format_ARGB32_Premultiplied).copy()

    def _prompts(self):
        try:
            with open(os.path.join(self.session, "session.json")) as f:
                return json.load(f).get("prompts", {})
        except (OSError, ValueError):
            return {}

    # ------------------------------------------------------------------ правка
    def on_stroke(self, pts, add):
        if not self.meta:
            return
        i = self.idx
        if self.chk_mask.isChecked():
            self._paint_mask(i, pts, add)
            return
        # штрих → до 12 точек по его длине (нейросети нужны точки, не пиксели)
        step = max(self.canvas.brush, 20.0)
        sel, acc = [pts[0]], 0.0
        for a, b in zip(pts, pts[1:]):
            acc += math.hypot(b[0] - a[0], b[1] - a[1])
            if acc >= step:
                sel.append(b)
                acc = 0.0
        if len(sel) > 12:
            sel = [sel[int(k * (len(sel) - 1) / 11)] for k in range(12)]
        lab = 1 if add else 0
        self.status.setText(tr("Нейросеть уточняет выделение…"))
        self._send({"cmd": "points", "frame": i, "points": [[x, y, lab] for x, y in sel]}, self._after_points)

    def _after_points(self, r):
        i = self.idx
        self._show(i)
        if self.chk_auto.isChecked():
            self.track_all()
        else:
            self.status.setText(tr("Готово. Поправьте ещё или «Отслеживать вперёд»."))

    def _paint_mask(self, i, pts, add):
        mp = os.path.join(self.session, "masks", f"{i:05d}.png")
        w, h = self.meta["w"], self.meta["h"]
        img = QImage(mp).convertToFormat(QImage.Format_Grayscale8) if os.path.exists(mp) else None
        if img is None or img.isNull():
            img = QImage(w, h, QImage.Format_Grayscale8)
            img.fill(0)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor(255, 255, 255) if add else QColor(0, 0, 0), self.canvas.brush * 2, Qt.SolidLine,
                      Qt.RoundCap, Qt.RoundJoin))
        if len(pts) == 1:
            pts = pts + [(pts[0][0] + 0.1, pts[0][1])]
        for a, b in zip(pts, pts[1:]):
            p.drawLine(QPointF(*a), QPointF(*b))
        p.end()
        os.makedirs(os.path.join(self.session, "edits"), exist_ok=True)
        ep = os.path.join(self.session, "edits", f"{i:05d}.png")
        img.save(ep)
        self._send({"cmd": "mask", "frame": i, "png": ep}, lambda r: self._show(i))

    def clear_frame(self):
        i = self.idx
        self._send({"cmd": "clear", "frame": i}, lambda r: self._show(i))

    def track(self, forward: bool):
        n = self.meta["n"]
        self.status.setText(tr("Слежение…"))
        self._send({"cmd": "propagate", "from": self.idx, "to": n - 1 if forward else 0},
                   lambda r: (self._after_change(), self.status.setText(
                       tr("Готово: {} кадров за {} с. Проверьте маску, поправьте кистью и отследите снова.")
                       .format(r.get("done"), r.get("seconds")))))

    def track_all(self):
        """Слежение по всем кадрам от текущего: сначала вперёд до конца слоя, потом назад до начала."""
        n, i = self.meta["n"], self.idx
        self.status.setText(tr("Слежение по всем кадрам…"))

        def back(r1):
            done1 = r1.get("done") or 0
            if i <= 0:
                return finish(r1, done1, r1.get("seconds") or 0)
            self._send({"cmd": "propagate", "from": i, "to": 0},
                       lambda r2: finish(r2, done1 + (r2.get("done") or 0),
                                         round((r1.get("seconds") or 0) + (r2.get("seconds") or 0), 1)))

        def finish(_r, done, secs):
            self._after_change()
            self.status.setText(tr("Готово: {} кадров за {} с. Проверьте маску, поправьте кистью и отследите снова.")
                                .format(done, secs))
        if i >= n - 1:
            back({"done": 0, "seconds": 0})
        else:
            self._send({"cmd": "propagate", "from": i, "to": n - 1}, back)

    def stabilize(self):
        self._send({"cmd": "stabilize", "strength": self.sl_stab.value() / 100.0},
                   lambda r: (self._after_change(), self.status.setText(tr("Маска сглажена во времени."))))

    def _after_change(self):
        self._show(self.idx)
        cache = getattr(self.ed, "cache", None)
        if cache is not None and hasattr(cache, "clear"):
            cache.clear()
        self.ed.notify()

    def export(self):
        """Прозрачный результат для AE: окно рендера с PNG-секвенцией и альфой (или ProRes 4444)."""
        mw = self.parent()
        if mw is not None and hasattr(mw, "export_dialog"):
            mw.export_dialog()
        self.status.setText(tr("Для After Effects: формат «PNG-секвенция» или MOV + ProRes 4444, включите «Альфа-канал». "
                               "Слой с Roto Brush будет прозрачным вокруг объекта."))
