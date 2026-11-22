# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""Чтение медиа: пробинг файлов, декодирование кадров видео с кэшем."""
from __future__ import annotations

from .i18n import tr

import os
from collections import OrderedDict
from dataclasses import dataclass

import av
import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QImageReader

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".flv", ".wmv", ".mpg", ".mpeg", ".ts", ".gif"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
AUDIO_EXT = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus"}


@dataclass
class MediaInfo:
    path: str
    kind: str  # video | image | audio
    width: int = 0
    height: int = 0
    fps: float = 30.0
    duration: float = 0.0  # секунды
    has_audio: bool = False
    rotation: int = 0      # метка поворота телефона (градусы против часовой); width/height — уже после поворота

    @property
    def name(self):
        return os.path.basename(self.path)


def rotated(arr, rot: int):
    """Поворот кадра по метке телефона (frame.rotation: градусы против часовой, кратны 90)."""
    k = (rot // 90) % 4
    return np.ascontiguousarray(np.rot90(arr, k)) if k else arr


def first_rotation(c, vs) -> int:
    """Метка поворота у потока: в PyAV она есть только на кадре, поэтому декодируем первый."""
    try:
        for fr in c.decode(vs):
            return int(fr.rotation or 0)
    except av.error.FFmpegError:
        pass
    return 0


def probe(path: str) -> MediaInfo:
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXT:
        size = QImageReader(path).size()
        if not size.isValid():
            raise ValueError(tr("Не могу прочитать картинку: {}").format(path))
        return MediaInfo(path, "image", size.width(), size.height(), duration=5.0)
    with av.open(path) as c:
        has_audio = bool(c.streams.audio)
        if not c.streams.video:
            if not has_audio:
                raise ValueError(tr("В файле нет ни видео, ни звука: {}").format(path))
            dur = float(c.duration / av.time_base) if c.duration else 0.0
            return MediaInfo(path, "audio", duration=dur, has_audio=True)
        vs = c.streams.video[0]
        fps = float(vs.average_rate or vs.guessed_rate or 30)
        if vs.duration and vs.time_base:
            dur = float(vs.duration * vs.time_base)
        elif c.duration:
            dur = float(c.duration / av.time_base)
        else:
            dur = 0.0
        w, h = vs.codec_context.width, vs.codec_context.height
        rot = first_rotation(c, vs)
        if rot % 180:   # вертикальное видео с телефона: 3840×2160 + «повернуть на 90°»
            w, h = h, w
        return MediaInfo(path, "video", w, h, fps, dur, has_audio, rot)


_THUMBS: dict[str, QImage] = {}


def thumbnail(path: str, box: int = 64) -> QImage | None:
    """Первый кадр, вписанный в квадрат box (для списка «Проект»). Кэшируется на всё время работы."""
    if path in _THUMBS:
        return _THUMBS[path]
    img = None
    try:
        if os.path.splitext(path)[1].lower() in IMAGE_EXT:
            r = QImageReader(path)
            r.setAutoTransform(True)
            img = r.read()
        else:
            with av.open(path) as c:
                if c.streams.video:
                    vs = c.streams.video[0]
                    fr = next(c.decode(vs))
                    k = box / max(fr.width, fr.height)
                    arr = fr.to_ndarray(format="bgra", width=max(2, int(fr.width * k)) // 2 * 2,
                                        height=max(2, int(fr.height * k)) // 2 * 2)
                    arr = rotated(np.ascontiguousarray(arr[..., :4]), fr.rotation or 0)
                    img = QImage(arr.data, arr.shape[1], arr.shape[0], arr.shape[1] * 4, QImage.Format_ARGB32).copy()
    except (OSError, ValueError, StopIteration, av.error.FFmpegError):
        img = None
    if img is not None and not img.isNull():
        img = img.scaled(box, box, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    else:
        img = None
    _THUMBS[path] = img
    return img


class VideoReader:
    """Отдаёт QImage по времени исходника. Последовательное чтение — быстро, прыжки — через seek."""

    CACHE = 48
    CACHE_BYTES = 96 << 20   # на декодер: 48 кадров 1080p — это 400 МБ, а клипов в эдите бывает 10+

    def __init__(self, path: str):
        self.path = path
        self.container = av.open(path)
        self.stream = self.container.streams.video[0]
        self.stream.thread_type = "AUTO"
        n = os.environ.get("SFX_DECODE_THREADS", "")
        if n.isdigit() and int(n) > 0:     # в воркерах: несколько декодеров не должны драться за все ядра
            self.stream.codec_context.thread_count = int(n)
        self.fps = float(self.stream.average_rate or self.stream.guessed_rate or 30)
        self.tb = float(self.stream.time_base)
        self.cache: OrderedDict[tuple, QImage] = OrderedDict()
        self._bytes = 0
        self._iter = None
        self._last_idx: int | None = None
        self._last_frame = None
        self._scale = None

    def close(self):
        try:
            self.container.close()
        except Exception:
            pass

    def _frame_time(self, fr) -> float:
        if fr.time is not None:
            return float(fr.time)
        return float(fr.pts or 0) * self.tb

    def _convert(self, fr, scale: float) -> QImage:
        w, h = fr.width, fr.height
        if scale < 0.99:
            w, h = max(2, int(w * scale)) // 2 * 2, max(2, int(h * scale)) // 2 * 2
            arr = fr.to_ndarray(format="bgra", width=w, height=h)
        else:
            arr = fr.to_ndarray(format="bgra")
        # PyAV выравнивает строки: при уменьшении массив бывает несплошным или с хвостом в строке.
        # Берём ровно w×h пикселей и делаем сплошной буфер, иначе кадр «съезжает» или не рисуется.
        h_, w_ = arr.shape[0], arr.shape[1]
        arr = rotated(np.ascontiguousarray(arr[:h_, :w_, :4]), fr.rotation or 0)
        h_, w_ = arr.shape[0], arr.shape[1]
        img = QImage(arr.data, w_, h_, w_ * 4, QImage.Format_ARGB32)
        return img.copy()

    def _seek(self, t: float):
        self.container.seek(max(0, int(t / self.tb)), stream=self.stream, backward=True, any_frame=False)
        self._iter = self.container.decode(self.stream)
        self._last_idx = None
        self._last_frame = None

    def frame_at(self, t: float, scale: float = 1.0) -> QImage | None:
        idx = int(round(t * self.fps))
        key = (idx, round(scale, 3))
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        target = idx / self.fps
        half = 0.5 / self.fps

        sequential = (self._iter is not None and self._last_idx is not None
                      and 0 <= idx - self._last_idx <= 45)
        if self._last_idx == idx and self._last_frame is not None:
            fr = self._last_frame
        else:
            if not sequential:
                self._seek(target)
            fr = self._last_frame
            try:
                while True:
                    f = next(self._iter)
                    fr = f
                    if self._frame_time(f) >= target - half:
                        break
            except (StopIteration, av.error.EOFError):
                self._iter = None
            except av.error.FFmpegError:
                self._iter = None
            self._last_idx = idx
            self._last_frame = fr
        if fr is None:
            return None
        img = self._convert(fr, scale)
        self.cache[key] = img
        self._bytes += img.sizeInBytes()
        while len(self.cache) > 1 and (len(self.cache) > self.CACHE or self._bytes > self.CACHE_BYTES):
            self._bytes -= self.cache.popitem(last=False)[1].sizeInBytes()
        return img


class MediaPool:
    """Кэш открытых декодеров и картинок. Для экспорта создаётся отдельный пул (свой поток)."""

    def __init__(self, use_proxies: bool = False):
        self.videos: OrderedDict[str, VideoReader] = OrderedDict()   # LRU: см. MAX_OPEN
        self.images: dict[str, QImage] = {}
        self.infos: dict[str, MediaInfo] = {}
        self.use_proxies = use_proxies   # превью — да, рендер в файл — никогда
        self._proxy: dict[str, str] = {}

    def video_for(self, path: str, q: float) -> tuple["VideoReader", float]:
        """Декодер и масштаб для нужного качества. Если превью не крупнее половины —
        берём лёгкий прокси (каждый кадр ключевой, в 2 раза меньше), масштаб относительно него ×2."""
        if self.use_proxies and q <= 0.55:
            px = self._proxy.get(path)
            if px is None:
                from .proxy import ready
                px = ready(path)
                if px:
                    self._proxy[path] = px
            if px:
                return self.video(px), min(1.0, q * 2)
        return self.video(path), q

    def forget_proxy(self, path: str):
        self._proxy.pop(path, None)

    def info(self, path: str) -> MediaInfo:
        if path not in self.infos:
            self.infos[path] = probe(path)
        return self.infos[path]

    MAX_OPEN = 6   # декодер HEVC 4K держит ~300 МБ; в эдите 10+ клипов — закрываем давно не нужные

    def video(self, path: str) -> VideoReader:
        r = self.videos.get(path)
        if r is None:
            r = self.videos[path] = VideoReader(path)
            while len(self.videos) > self.MAX_OPEN:
                self.videos.popitem(last=False)[1].close()
        else:
            self.videos.move_to_end(path)
        return r

    def image(self, path: str) -> QImage:
        img = self.images.get(path)
        if img is None:
            img = QImage(path).convertToFormat(QImage.Format_ARGB32_Premultiplied)
            self.images[path] = img
        return img

    def close(self):
        for r in self.videos.values():
            r.close()
        self.videos.clear()
        self._proxy.clear()
