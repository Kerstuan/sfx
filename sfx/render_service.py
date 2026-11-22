# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Фоновый рендер превью в нескольких процессах (как многопроцессорный рендер в AE).

Окно никогда не ждёт кадр: вьювер просит кадр, показывает последний готовый, а когда воркер
досчитает — кадр ложится в RAM-кэш и вьювер перерисовывается. Воркеры — отдельные процессы
(python -m sfx.render_worker), поэтому эффекты на numpy не тормозят интерфейс и идут параллельно.
Текущий кадр всегда в начале очереди; кадры для проигрывания и фонового кэша — за ним.
"""
from __future__ import annotations

import glob
import os
import pickle
import struct
import sys
from collections import OrderedDict

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal
from PySide6.QtGui import QImage

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def default_workers() -> int:
    env = os.environ.get("SFX_WORKERS")
    if env is not None and env.strip().isdigit():
        return int(env)
    # эффекты упираются в память, а не в ядра: больше 4 процессов почти не ускоряют
    return max(1, min(4, (os.cpu_count() or 4) // 4))


def _take_shm(path: str, w: int, h: int, bpl: int):
    """Забрать кадр из /dev/shm (файл кладёт воркер) прямо в новый QImage и удалить файл.
    readinto — одна копия ядром, без mmap и без второй копии QImage.copy()."""
    try:
        img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        if img.bytesPerLine() != bpl:
            return None
        with open(path, "rb", buffering=0) as f:
            if f.readinto(memoryview(img.bits())[: h * bpl]) != h * bpl:
                return None
        return img
    except OSError:
        return None
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _clean_shm(owner: int | None = None):
    """Удалить кадры, оставшиеся в /dev/shm: свои (owner) или от уже не живущих окон."""
    for name in glob.glob("/dev/shm/sfx-*.raw"):
        try:
            pid = int(os.path.basename(name).split("-")[1])
        except (IndexError, ValueError):
            continue
        if pid == owner or (owner is None and not os.path.exists(f"/proc/{pid}")):
            try:
                os.unlink(name)
            except OSError:
                pass


def service_n(service) -> int:
    return getattr(service, "_n", 1)


class _Worker:
    def __init__(self, service, idx):
        self.service = service
        self.idx = idx
        self._need = None              # длина заголовка ответа, который ещё не дочитан
        self._hdr = None               # заголовок ответа, пиксели которого ещё не дочитаны
        self.busy: list[int] = []      # id заданий в работе (по порядку)
        self.sent_hash = None
        self.proc = QProcess(service)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONPATH", _ROOT + os.pathsep + env.value("PYTHONPATH", ""))
        env.insert("QT_QPA_PLATFORM", "offscreen")
        env.insert("SFX_DECODE_THREADS", str(max(2, (os.cpu_count() or 8) // max(1, service_n(service)))))
        self.proc.setProcessEnvironment(env)
        self.proc.setProcessChannelMode(QProcess.SeparateChannels)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.readyReadStandardError.connect(lambda: self.proc.readAllStandardError())
        self.proc.finished.connect(self._died)
        self.proc.start(sys.executable, ["-m", "sfx.render_worker"])
        self.alive = True

    def send(self, job_id, job):
        sig, state_json, frame, scale, transparent = job
        st = state_json if self.sent_hash != sig[0] else None
        msg = pickle.dumps((job_id, sig[0], st, frame, scale, transparent, dict(self.service.opts)))
        self.proc.write(struct.pack("<I", len(msg)) + msg)
        self.sent_hash = sig[0]
        self.busy.append(job_id)

    def _read(self):
        # Копим в буфере QProcess и забираем ответ целиком: раньше каждый кусок канала (64 КБ)
        # дописывался в bytearray и копировался ещё раз — главный поток тратил ~4 мс на кадр.
        p = self.proc
        while True:
            if self._hdr is None:
                if self._need is None:
                    if p.bytesAvailable() < 4:
                        return
                    self._need = struct.unpack("<I", p.read(4).data())[0]
                if p.bytesAvailable() < self._need:
                    return
                self._hdr = pickle.loads(p.read(self._need).data())
                self._need = None
            job_id, w, h, bpl, ms, err, shm, shm_bpl = self._hdr
            n = h * bpl                    # 0, если пиксели лежат в общей памяти (shm)
            if p.bytesAvailable() < n:
                return
            data = memoryview(p.read(n)) if n else b""   # без .data(): та копия стоила ~5 мс на кадр 1080p
            self._hdr = None
            if job_id in self.busy:
                self.busy.remove(job_id)
            if err == "no state":
                self.sent_hash = None
            if shm:
                data, bpl = _take_shm(shm, w, h, shm_bpl), shm_bpl
                if data is None and not err:
                    err = "shm: frame lost"
            self.service._result(self, job_id, w, h, bpl, ms, err, data)

    def _died(self, *_):
        self.alive = False
        self.service._worker_died(self)

    def stop(self):
        self.alive = False
        try:
            self.proc.finished.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.proc.closeWriteChannel()
        if not self.proc.waitForFinished(800):
            self.proc.kill()
            self.proc.waitForFinished(500)


class RenderService(QObject):
    frame_ready = Signal(object, int, object)    # sig, кадр, QImage
    idle = Signal()                              # очередь пуста

    PER_WORKER = 2   # заданий в полёте на воркер: один считается, один ждёт — без простоя

    def __init__(self, n_workers: int | None = None, parent=None):
        super().__init__(parent)
        n = default_workers() if n_workers is None else n_workers
        self._n = n
        _clean_shm()                       # хвосты после прошлого падения
        self.opts = {"proxies": True}   # общие настройки для воркеров (прокси вкл/выкл)
        self.workers = [_Worker(self, i) for i in range(n)]
        self.queue: OrderedDict[tuple, tuple] = OrderedDict()   # (sig, frame) -> job, по приоритету
        self.inflight: dict[int, tuple] = {}                     # job_id -> (sig, frame, worker)
        self._next_id = 0
        self.last_ms = 0.0
        self.failed = 0

    @property
    def available(self) -> bool:
        return any(w.alive for w in self.workers)

    def pending(self, sig, frame) -> bool:
        key = (sig, frame)
        return key in self.queue or any((v[0], v[1]) == key for v in self.inflight.values())

    def request(self, sig, state_json: str, frame: int, scale: float, transparent: bool, urgent=False):
        key = (sig, frame)
        if any((v[0], v[1]) == key for v in self.inflight.values()):
            return
        job = (sig, state_json, frame, scale, transparent)
        if key in self.queue:
            if urgent:
                self.queue.move_to_end(key, last=False)
        else:
            self.queue[key] = job
            if urgent:
                self.queue.move_to_end(key, last=False)
        self._dispatch()

    def drop_stale(self, sig):
        """Проект изменился — задания для старой версии больше не нужны."""
        for k in [k for k in self.queue if k[0] != sig]:
            del self.queue[k]

    def clear_queue(self):
        self.queue.clear()

    def _dispatch(self):
        for w in sorted((w for w in self.workers if w.alive), key=lambda w: len(w.busy)):
            while self.queue and len(w.busy) < self.PER_WORKER:
                key, job = self.queue.popitem(last=False)
                jid = self._next_id = self._next_id + 1
                self.inflight[jid] = (key[0], key[1], w, job)
                w.send(jid, job)

    def _result(self, w, job_id, width, height, bpl, ms, err, data):
        info = self.inflight.pop(job_id, None)
        if info is not None:
            sig, frame, _w, job = info
            if err:
                self.failed += 1
                if err == "no state" and self.failed < 200:   # воркер потерял проект — повторим с ним
                    self.queue[(sig, frame)] = job
                    self.queue.move_to_end((sig, frame), last=False)
                else:
                    print("render worker:", err, file=sys.stderr)
            else:
                self.last_ms = ms
                img = data if isinstance(data, QImage) else \
                    QImage(data, width, height, bpl, QImage.Format_ARGB32_Premultiplied).copy()
                self.frame_ready.emit(sig, frame, img)
        self._dispatch()
        if not self.queue and not self.inflight:
            self.idle.emit()

    def _worker_died(self, w):
        for jid in list(w.busy):
            info = self.inflight.pop(jid, None)
            if info is not None:   # задания упавшего воркера отдаём остальным
                self.queue[(info[0], info[1])] = info[3]
        w.busy.clear()
        self._dispatch()

    def stop(self):
        self.queue.clear()
        for w in self.workers:
            w.stop()
        self.workers = []
        _clean_shm(os.getpid())
