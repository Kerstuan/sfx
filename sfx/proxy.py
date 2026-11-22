# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Прокси для превью, как Proxy в AE и Premiere.

Тяжёлое видео (CS2-записи 1080p60 на 40 Мбит/с, ключевой кадр раз в 2 с) медленно мотать:
каждый прыжок — декодировать до 120 кадров от ключевого. Прокси — копия в половинном размере,
где КАЖДЫЙ кадр ключевой (all-intra): прыжок и шаг назад стоят один кадр (~15 мс вместо ~250 мс).

Делается в фоне через ffmpeg на видеокарте (NVENC + масштабирование на CUDA), если она есть;
иначе на процессоре (libx264 ultrafast). Превью в разрешении «Половина» и ниже берёт кадры из прокси,
рендер в файл — всегда из оригинала.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess

from PySide6.QtCore import QObject, QProcess, Signal

_ENCODER = None   # "nvenc" | "cpu" — определяется один раз


def proxy_dir() -> str:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "sfx", "proxies")


def proxy_path(src: str) -> str:
    try:
        st = os.stat(src)
        sig = f"{os.path.abspath(src)}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        sig = os.path.abspath(src)
    return os.path.join(proxy_dir(), hashlib.sha1(sig.encode()).hexdigest()[:20] + ".mp4")


def ready(src: str) -> str | None:
    """Путь к готовому прокси или None."""
    p = proxy_path(src)
    return p if os.path.exists(p) else None


def proxy_size(w: int, h: int) -> tuple[int, int]:
    return max(2, w // 2 // 2 * 2), max(2, h // 2 // 2 * 2)


def needs_proxy(info) -> bool:
    """Картинкам и маленьким роликам прокси не нужен."""
    return info.kind == "video" and info.width * info.height >= 1280 * 720


def encoder() -> str:
    global _ENCODER
    if _ENCODER is None:
        _ENCODER = "cpu"
        if shutil.which("ffmpeg"):
            try:   # пробный кадр через NVENC: есть ли видеокарта NVIDIA и драйвер
                r = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
                                    "-c:v", "h264_nvenc", "-f", "null", "-"], capture_output=True, timeout=20)
                if r.returncode == 0:
                    _ENCODER = "nvenc"
            except (OSError, subprocess.SubprocessError):
                pass
    return _ENCODER


def build_cmd(src: str, dst: str, w: int, h: int, enc: str) -> list[str]:
    pw, ph = proxy_size(w, h)
    if enc == "nvenc":
        from .media import probe
        if probe(src).rotation % 360:
            # на кадрах CUDA ffmpeg молча пропускает автоповорот — прокси выходил лёжа и сплюснутым.
            # Декод на GPU, поворот и масштаб на CPU, кодирование NVENC.
            return ["ffmpeg", "-v", "error", "-y", "-hwaccel", "cuda", "-i", src, "-vf", f"scale={pw}:{ph}",
                    "-c:v", "h264_nvenc", "-preset", "p1", "-g", "1", "-bf", "0", "-rc", "constqp", "-qp", "30",
                    "-an", "-movflags", "+faststart", "-progress", "pipe:1", dst]
        return ["ffmpeg", "-v", "error", "-y", "-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-i", src,
                "-vf", f"scale_cuda={pw}:{ph}", "-c:v", "h264_nvenc", "-preset", "p1", "-g", "1", "-bf", "0",
                "-rc", "constqp", "-qp", "30", "-an", "-movflags", "+faststart", "-progress", "pipe:1", dst]
    return ["ffmpeg", "-v", "error", "-y", "-i", src, "-vf", f"scale={pw}:{ph}", "-c:v", "libx264",
            "-preset", "ultrafast", "-g", "1", "-crf", "24", "-an", "-movflags", "+faststart",
            "-progress", "pipe:1", dst]


class ProxyManager(QObject):
    """Очередь фоновых прокси. По одному файлу за раз, чтобы не душить систему."""

    progress = Signal(str, float)     # исходник, 0..1
    finished = Signal(str, bool)      # исходник, успех

    def __init__(self, parent=None):
        super().__init__(parent)
        self.queue: list[tuple[str, int, int, float]] = []
        self.proc: QProcess | None = None
        self.current = None
        self._tmp = None
        self.enabled = True
        import threading   # проверка NVENC (~0,2 с) — заранее в фоне, а не в момент импорта первого клипа
        threading.Thread(target=encoder, daemon=True).start()
        from PySide6.QtCore import QCoreApplication
        app = QCoreApplication.instance()
        if app is not None:   # программа закрывается — останавливаем ffmpeg и убираем недоделанный файл
            app.aboutToQuit.connect(self.stop)

    def request(self, src: str, w: int, h: int, duration: float):
        if not self.enabled or ready(src) or any(q[0] == src for q in self.queue) or \
                (self.current and self.current[0] == src):
            return
        self.queue.append((src, w, h, duration))
        self._next()

    def busy(self) -> bool:
        return self.proc is not None

    def _next(self, enc: str | None = None):
        if self.proc is not None or not self.queue:
            return
        src, w, h, dur = self.current = self.queue.pop(0)
        os.makedirs(proxy_dir(), exist_ok=True)
        dst = proxy_path(src)
        self._tmp = dst[:-4] + ".part.mp4"
        self._enc = enc or encoder()
        self.proc = QProcess(self)
        cmd = build_cmd(src, self._tmp, w, h, self._enc)
        if shutil.which("setpriv"):
            # программа упала / убита — ядро убьёт и ffmpeg (иначе он минутами кодирует сиротой)
            cmd = ["setpriv", "--pdeathsig", "KILL", "--"] + cmd
        self.proc.setProgram(cmd[0])
        self.proc.setArguments(cmd[1:])
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._done)
        self.proc.errorOccurred.connect(self._on_error)   # метод, а не lambda: PySide отключит его вместе с объектом
        self.proc.start()

    def _read(self):
        if not self.proc or not self.current:
            return
        out = bytes(self.proc.readAllStandardOutput()).decode(errors="ignore")
        dur = max(0.01, self.current[3])
        for line in out.splitlines():
            if line.startswith("out_time_us=") and line[12:].strip().isdigit():
                self.progress.emit(self.current[0], min(1.0, int(line[12:]) / 1e6 / dur))

    def _on_error(self, _err):
        self._done(-1, None)

    def _done(self, code, _status=None):
        from shiboken6 import isValid
        if not isValid(self) or self.proc is None or self.current is None:
            return                        # менеджер уже удалён (закрыли окно/редактор во время кодирования)
        src = self.current[0]
        proc, self.proc = self.proc, None
        try:
            proc.deleteLater()
        except RuntimeError:
            pass
        ok = code == 0 and os.path.exists(self._tmp) and os.path.getsize(self._tmp) > 0
        if ok:
            os.replace(self._tmp, proxy_path(src))
        else:
            try:
                os.remove(self._tmp)
            except OSError:
                pass
            if self._enc == "nvenc":   # видеокарта не справилась (формат, драйвер) — пробуем процессором
                self.queue.insert(0, self.current)
                self.current = None
                self._next("cpu")
                return
        self.current = None
        self.finished.emit(src, ok)
        self._next()

    def stop(self):
        self.queue.clear()
        proc, self.proc = self.proc, None
        if proc is not None:
            try:
                proc.finished.disconnect()
                proc.errorOccurred.disconnect()
                proc.kill()
                proc.waitForFinished(2000)
            except RuntimeError:
                pass
            if self._tmp:
                try:
                    os.remove(self._tmp)
                except OSError:
                    pass
        self.current = None


def clear_all() -> int:
    """Удалить все прокси; вернуть освобождённые байты."""
    d = proxy_dir()
    freed = 0
    if os.path.isdir(d):
        for n in os.listdir(d):
            p = os.path.join(d, n)
            try:
                freed += os.path.getsize(p)
                os.remove(p)
            except OSError:
                pass
    return freed
