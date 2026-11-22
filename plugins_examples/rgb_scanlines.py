# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Пример плагина SFX: эффект «Строки развёртки» и оператор фигуры «Волна».
Скопируй файл в ~/.config/sfx/plugins/ и перезапусти программу."""
import math

import numpy as np
from PySide6.QtCore import QPointF
from PySide6.QtGui import QPainterPath

PLUGIN = {"id": "example.scan", "name": "Scanlines (пример)", "version": "1.0", "api": 1}


def scanlines(arr, v, scale=1.0, frame=0, **_):
    """arr — BGRA float32 (премультиплицированный), вернуть такой же массив."""
    h = arr.shape[0]
    period = max(2, int(v["period"] * scale))
    rows = (np.arange(h) + int(frame * v["speed"])) % period < period / 2
    out = arr.copy()
    out[rows, :, :3] *= 1.0 - v["dark"] / 100.0
    return out


def wave(path, v, t_sec):
    """path — QPainterPath фигуры, вернуть новый контур."""
    out = QPainterPath()
    for poly in path.toSubpathPolygons():
        for i, p in enumerate(poly):
            q = QPointF(p.x(), p.y() + math.sin(p.x() / max(1.0, v["length"]) + t_sec * 4) * v["height"])
            out.moveTo(q) if i == 0 else out.lineTo(q)
    return out


def register(api):
    api.add_effect("scanlines", "Строки развёртки", "Scanlines", params=[
        {"key": "period", "ru": "Шаг строк", "en": "Line Period", "default": [6.0], "suffix": " px", "lo": 2, "hi": 200},
        {"key": "dark", "ru": "Затемнение", "en": "Darkness", "default": [40.0], "suffix": "%", "lo": 0, "hi": 100},
        {"key": "speed", "ru": "Скорость", "en": "Speed", "default": [0.0], "lo": -50, "hi": 50},
    ], fn=scanlines)
    api.add_shape_op("wave", "Волна", "Wave", params=[
        {"key": "height", "ru": "Высота", "en": "Height", "default": [15.0], "suffix": " px"},
        {"key": "length", "ru": "Длина", "en": "Length", "default": [20.0], "suffix": " px", "lo": 1},
    ], fn=wave)
