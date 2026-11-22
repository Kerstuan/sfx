# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Маски Roto Brush в SFX: чтение масок сессии, уточнение края и наложение на кадр видеослоя.

Маски считает отдельный процесс (roto_worker.py, SAM 2.1) и пишет PNG в папку сессии.
Здесь только numpy: работает и в основном окне, и в процессах-рендерерах.
"""
from __future__ import annotations

import json
import os
from collections import OrderedDict

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

_MASKS: OrderedDict = OrderedDict()     # (путь, mtime) -> float32 маска
_META: dict = {}


def session_meta(session: str) -> dict | None:
    p = os.path.join(session, "session.json")
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return None
    got = _META.get(session)
    if got and got[0] == mt:
        return got[1]
    with open(p) as f:
        meta = json.load(f)
    _META[session] = (mt, meta)
    return meta


def mask_path(session: str, idx: int) -> str:
    return os.path.join(session, "masks", f"{idx:05d}.png")


def load_mask(session: str, idx: int) -> np.ndarray | None:
    p = mask_path(session, idx)
    try:
        mt = os.path.getmtime(p)
    except OSError:
        return None
    key = (p, mt)
    m = _MASKS.get(key)
    if m is None:
        img = QImage(p)
        if img.isNull():
            return None
        img = img.convertToFormat(QImage.Format_Grayscale8)
        m = np.frombuffer(img.constBits(), np.uint8).reshape(img.height(), img.bytesPerLine())[:, :img.width()]
        m = m.astype(np.float32) / 255.0
        _MASKS[key] = m
        while len(_MASKS) > 48:
            _MASKS.popitem(last=False)
    else:
        _MASKS.move_to_end(key)
    return m


def resize_plane(m: np.ndarray, w: int, h: int) -> np.ndarray:
    if m.shape == (h, w):
        return m
    img = QImage((np.clip(m, 0, 1) * 255).astype(np.uint8).tobytes(), m.shape[1], m.shape[0], m.shape[1],
                 QImage.Format_Grayscale8).scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    a = np.frombuffer(img.constBits(), np.uint8).reshape(h, img.bytesPerLine())[:, :w]
    return a.astype(np.float32) / 255.0


# ============================================================================ уточнение края
def _box2(a: np.ndarray, r: int) -> np.ndarray:
    from .effects import _box
    return _box(_box(a, r, 0), r, 1)


def guided_filter(guide: np.ndarray, p: np.ndarray, r: int, eps: float) -> np.ndarray:
    """Guided filter (He, Sun, Tang 2010): край маски «прилипает» к краям картинки —
    волосы и мелкие детали становятся полупрозрачными, а не ступенькой."""
    if r < 1:
        return p
    mI, mp = _box2(guide, r), _box2(p, r)
    cov = _box2(guide * p, r) - mI * mp
    var = _box2(guide * guide, r) - mI * mI
    a = cov / (var + eps)
    b = mp - a * mI
    return _box2(a, r) * guide + _box2(b, r)


def _blur_plane(m: np.ndarray, radius: float) -> np.ndarray:
    r = int(round(radius / 1.7))
    if r < 1:
        return m
    for _ in range(3):                  # три прохода box ≈ гаусс
        m = _box2(m, r)
    return m


def refine(mask: np.ndarray, rgb: np.ndarray | None, v: dict, scale: float = 1.0) -> np.ndarray:
    """Маска 0..1 → альфа 0..1: Refine Edge (guided filter), Shift Edge, Contrast, Choke, Feather, Invert."""
    m = mask
    r = int(round(v.get("refine", 4.0) * scale))
    if r >= 1 and rgb is not None:
        g = (rgb[..., 0] * 0.114 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.299) / 255.0
        eps = 10.0 ** (-1.0 - 3.0 * float(v.get("detail", 0.5)))     # «Детализация края» 0..1
        m = guided_filter(g.astype(np.float32), m.astype(np.float32), r, eps)
    choke = v.get("choke", 0.0) / 100.0                                # >0 — сжать, <0 — расширить
    shift = v.get("shift", 0.0) / 100.0
    contrast = v.get("contrast", 0.0) / 100.0
    center = 0.5 + choke * 0.5 - shift * 0.5
    gain = 1.0 + contrast * 9.0
    m = np.clip((m - center) * gain + 0.5, 0, 1)
    fe = v.get("feather", 0.0) * scale
    if fe > 0.5:
        m = _blur_plane(m, fe)
    if v.get("invert", 0):
        m = 1.0 - m
    return np.clip(m, 0, 1).astype(np.float32)


# ============================================================================ наложение на кадр слоя
def roto_effect(layer):
    for fx in layer.effects:
        if fx.get("type") == "roto" and fx.get("on", True) and fx.get("opts", {}).get("session"):
            return fx
    return None


def roto_apply(layer, img: QImage, src_t: float, video_fps: float, local_frame: float) -> QImage:
    """Кадр видеослоя → тот же кадр с альфой из маски Roto (вне отслеженного диапазона — без изменений)."""
    if not getattr(layer, "fx_on", True):
        return img
    fx = roto_effect(layer)
    if fx is None:
        return img
    from .effects import to_array, to_image, values
    session = fx["opts"]["session"]
    meta = session_meta(session)
    if not meta:
        return img
    idx = int(round(src_t * meta.get("fps", video_fps))) - int(meta.get("start", 0))
    if idx < 0 or idx >= int(meta.get("n", 0)):
        return img
    m = load_mask(session, idx)
    if m is None:
        return img
    v = values(layer, fx, local_frame)
    arr = to_array(img.convertToFormat(QImage.Format_ARGB32_Premultiplied))
    h, w = arr.shape[:2]
    scale = w / max(1, m.shape[1])
    m = resize_plane(m, w, h)
    a = refine(m, arr, v, scale)[..., None]
    show = int(v.get("show", 0))
    if show == 1:                      # маска: белое на чёрном
        out = np.empty_like(arr)
        out[..., :3] = a * 255.0
        out[..., 3] = 255.0
    elif show == 2:                    # наложение: фон затемнён и подкрашен, как в AE
        out = arr.copy()
        tint = np.array([60.0, 20.0, 160.0], np.float32)
        out[..., :3] = arr[..., :3] * (0.35 + 0.65 * a) + tint * (1 - a) * 0.35
    else:
        out = arr * a
    return to_image(out.astype(np.float32))
