# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""Кэш просчитанных кадров (RAM Preview, как в AE).

Кадр хранится, пока не изменился проект: подпись = хэш состояния всех композиций
+ масштаб превью + прозрачность. Любое изменение — подпись другая, старые кадры не используются.
Зелёная полоска на линейке таймлайна показывает, какие кадры уже в памяти.
"""
from __future__ import annotations

from collections import OrderedDict


def _ram_limit_mb() -> int:
    """Четверть оперативной памяти, но не больше 6 ГБ и не меньше 512 МБ."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total_mb = int(line.split()[1]) // 1024
                    return max(512, min(6144, total_mb // 4))
    except (OSError, ValueError):
        pass
    return 2048


def _avail_mb() -> int | None:
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return None


RESERVE_MB = 1536   # столько памяти оставляем системе: иначе OOM-киллер убивает всю сессию Hyprland


class FrameCache:
    def __init__(self, limit_mb: int | None = None):
        self.limit = (limit_mb or _ram_limit_mb()) * 1024 * 1024
        self.frames: OrderedDict[tuple, object] = OrderedDict()   # (sig, frame) -> QImage
        self.bytes = 0

    @staticmethod
    def _size(img) -> int:
        return img.sizeInBytes()

    def get(self, sig, frame):
        img = self.frames.get((sig, frame))
        if img is not None:
            self.frames.move_to_end((sig, frame))
        return img

    def put(self, sig, frame, img):
        key = (sig, frame)
        if key in self.frames:
            return
        self.frames[key] = img
        self.bytes += self._size(img)
        limit = self.limit
        if len(self.frames) % 8 == 0:   # meminfo читаем не на каждый кадр
            av = _avail_mb()
            if av is not None and av < RESERVE_MB:
                limit = min(limit, max(64 << 20, self.bytes - ((RESERVE_MB - av) << 20)))
        while self.bytes > limit and len(self.frames) > 1:
            _k, old = self.frames.popitem(last=False)
            self.bytes -= self._size(old)

    def drop_other(self, sig):
        """Выбросить кадры старых версий проекта (освободить память)."""
        for k in [k for k in self.frames if k[0] != sig]:
            self.bytes -= self._size(self.frames.pop(k))

    def cached_frames(self, sig) -> set[int]:
        return {f for (s, f) in self.frames if s == sig}

    def clear(self):
        self.frames.clear()
        self.bytes = 0
