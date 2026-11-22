# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Сборка кадра композиции через QPainter."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QPointF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QTransform

import numpy as np

from .effects import apply_effects, has_pixel_effects, shake_offset, to_array, to_image, visual_effects
from .shapes import active_masks, draw_shapes, mask_alpha
from .media import MediaPool
from .model import Composition, Layer


def local_transform(layer: Layer, comp_frame: float, fps: float = 30.0, with_shake: bool = True) -> QTransform:
    """Трансформация слоя относительно родителя (или композиции)."""
    f = layer.local(comp_frame)
    ax, ay = layer.props["anchor"].value_at(f)[:2]
    px, py = layer.props["position"].value_at(f)[:2]
    sx, sy = layer.props["scale"].value_at(f)[:2]
    (r,) = layer.props["rotation"].value_at(f)
    if with_shake and layer.effects:
        dx, dy, dr, ds = shake_offset(layer, fps, comp_frame)
        px, py, r = px + dx, py + dy, r + dr
        sx, sy = sx * ds, sy * ds
    t = QTransform()
    t.translate(px, py)
    t.rotate(r)
    t.scale(sx / 100.0, sy / 100.0)
    t.translate(-ax, -ay)
    return t


def parent_chain(layer: Layer, comp: Composition | None) -> list[Layer]:
    """Родители слоя снизу вверх (без циклов)."""
    out, seen = [], {layer.id}
    cur = layer
    while comp is not None and cur.parent:
        par = comp.layer_by_id(cur.parent)
        if par is None or par.id in seen:
            break
        out.append(par)
        seen.add(par.id)
        cur = par
    return out


def parent_world(layer: Layer, comp: Composition | None, comp_frame: float) -> QTransform:
    fps = comp.fps if comp else 30.0
    t = QTransform()
    for par in parent_chain(layer, comp):
        t = t * local_transform(par, comp_frame, fps)
    return t


def layer_transform(layer: Layer, comp_frame: float, comp: Composition | None = None) -> QTransform:
    """Полная трансформация слоя в координаты композиции (с учётом родителей и 3D-камеры)."""
    if comp is not None and layer.threed and layer.kind not in ("camera", "light"):
        from .three import layer_homography
        h = layer_homography(layer, comp, comp_frame)
        return h if h is not None else QTransform.fromScale(0, 0)
    fps = comp.fps if comp else 30.0
    return local_transform(layer, comp_frame, fps) * parent_world(layer, comp, comp_frame)


def text_font(layer: Layer) -> QFont:
    font = QFont(layer.font_family)
    font.setPixelSize(max(1, int(layer.font_size)))
    font.setBold(layer.bold)
    return font


def update_text_size(layer: Layer):
    """Пересчитать размер текстового слоя по тексту и шрифту."""
    fm = QFontMetricsF(text_font(layer))
    lines = layer.text.split("\n") or [""]
    w = max(fm.horizontalAdvance(l) for l in lines)
    h = fm.lineSpacing() * len(lines)
    layer.width, layer.height = max(1.0, w), max(1.0, h)


def draw_layer(p: QPainter, layer: Layer, comp: Composition, frame: int, pool: MediaPool, scale: float):
    x, y, w, h = layer.local_rect()
    rect = QRectF(x, y, w, h)
    if layer.kind == "video":
        src_t = layer.src_time(layer.local(frame), comp.fps)
        if layer.effects:   # «Случайные склейки» (как S_RandomEdits) — прыжки по клипу
            from .effects import random_edit_offset
            src_t = max(0.0, src_t + random_edit_offset(layer, layer.local(frame), comp.fps))
        try:
            sx = layer.props["scale"].value_at(layer.local(frame))[0]
            q = min(1.0, scale * abs(sx) / 100.0 * 1.05)
            reader, q = pool.video_for(layer.source, q)
            if layer.frame_blend:
                # смешивание кадров: два соседних кадра исходника в пропорции — плавное замедление
                pos = src_t * reader.fps
                f0 = int(pos)
                frac = pos - f0
                img = reader.frame_at(f0 / reader.fps, q)
                if img is not None and frac > 0.02:
                    nxt = reader.frame_at((f0 + 1) / reader.fps, q)
                    if nxt is not None:
                        if layer.effects:      # Roto Brush: маска на оба смешиваемых кадра
                            from .matte import roto_apply
                            img = roto_apply(layer, img, f0 / reader.fps, reader.fps, layer.local(frame))
                            nxt = roto_apply(layer, nxt, (f0 + 1) / reader.fps, reader.fps, layer.local(frame))
                        p.drawImage(rect, img)
                        op = p.opacity()
                        p.setOpacity(op * frac)
                        p.drawImage(rect, nxt)
                        p.setOpacity(op)
                        return
            else:
                img = reader.frame_at(src_t, q)
            if img is not None and layer.effects:      # Roto Brush: альфа из маски сессии
                from .matte import roto_apply
                img = roto_apply(layer, img, src_t, reader.fps, layer.local(frame))
        except Exception:
            img = None
        if img is not None:
            p.drawImage(rect, img)
        else:
            p.fillRect(rect, QColor("#402020"))
    elif layer.kind == "image":
        try:
            p.drawImage(rect, pool.image(layer.source))
        except Exception:
            p.fillRect(rect, QColor("#402020"))
    elif layer.kind == "solid":
        p.fillRect(rect, QColor(layer.color))
    elif layer.kind == "text":
        _draw_text(p, layer, rect, frame)
    elif layer.kind == "comp":
        nested = comp.find_comp(layer.source)
        if nested is not None and _DEPTH[0] < 8:
            nf = int(round(layer.src_time(layer.local(frame), comp.fps) * nested.fps))
            if 0 <= nf < nested.duration:
                _DEPTH[0] += 1
                try:
                    img = render_frame(nested, nf, pool, scale, transparent=True)
                finally:
                    _DEPTH[0] -= 1
                p.drawImage(QRectF(0, 0, nested.width, nested.height), img)
    elif layer.kind == "shape":
        draw_shapes(p, layer, layer.local(frame))


_DEPTH = [0]

_BLEND = {"normal": QPainter.CompositionMode_SourceOver, "add": QPainter.CompositionMode_Plus,
          "screen": QPainter.CompositionMode_Screen, "lighten": QPainter.CompositionMode_Lighten,
          "dodge": QPainter.CompositionMode_ColorDodge, "multiply": QPainter.CompositionMode_Multiply,
          "darken": QPainter.CompositionMode_Darken, "burn": QPainter.CompositionMode_ColorBurn,
          "overlay": QPainter.CompositionMode_Overlay, "soft": QPainter.CompositionMode_SoftLight,
          "hard": QPainter.CompositionMode_HardLight, "difference": QPainter.CompositionMode_Difference,
          "exclusion": QPainter.CompositionMode_Exclusion}


def _matte_alpha(layer, comp, frame, pool, scale, w, h, base, mb_samples) -> np.ndarray:
    """Маска (H, W, 1) 0..1 от слоя-матта: его альфа или яркость (с эффектами и непрозрачностью)."""
    m = layer.matte
    ml = comp.layer_by_id(m.get("layer")) if m else None
    mode = m.get("mode", "alpha") if m else "alpha"
    if ml is None or not (ml.in_point <= frame < ml.out_point):
        a = np.zeros((h, w, 1), np.float32)
    else:
        times = _mb_times(ml, comp, frame, mb_samples)
        arr = _layer_buffer(ml, comp, frame, pool, scale, w, h, base, times)
        arr = apply_effects(arr, ml, frame, comp.fps, scale)
        op = ml.props["opacity"].value_at(ml.local(frame))[0] / 100.0
        if mode.startswith("luma"):
            al = arr[..., 3:4]
            rgb = np.where(al > 0, arr[..., :3] / np.maximum(al, 1e-3), 0)
            a = (rgb[..., 0:1] * 0.114 + rgb[..., 1:2] * 0.587 + rgb[..., 2:3] * 0.299) / 255.0 * (al / 255.0)
        else:
            a = arr[..., 3:4] / 255.0
        a = np.clip(a * op, 0, 1)
    return 1 - a if mode.endswith("_inv") else a  # глубина вложенных прекомпозиций (защита от бесконечной рекурсии)


def _draw_text(p, layer, rect, frame):
    """Текст по центру; «Показ символов» (%) открывает буквы слева направо, не сдвигая строку."""
    font = text_font(layer)
    p.setFont(font)
    p.setPen(QColor(layer.color))
    rv = layer.props.get("text_reveal")
    reveal = rv.value_at(layer.local(frame))[0] if rv else 100.0
    if reveal >= 100:
        p.drawText(rect, Qt.AlignCenter | Qt.TextDontClip, layer.text)
        return
    total = len(layer.text.replace("\n", ""))
    left = int(round(total * max(0.0, reveal) / 100.0))
    fm = QFontMetricsF(font)
    lines = layer.text.split("\n")
    lh = fm.lineSpacing()
    y = rect.center().y() - lh * len(lines) / 2 + fm.ascent()
    for line in lines:
        vis = line[:max(0, left)]
        left -= len(line)
        x = rect.center().x() - fm.horizontalAdvance(line) / 2
        if vis:
            p.drawText(QPointF(x, y), vis)
        y += lh


def _new_canvas(w, h) -> QImage:
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    img.fill(0)
    return img


def _painter(img: QImage) -> QPainter:
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    p.setRenderHint(QPainter.TextAntialiasing)
    return p


def _mb_times(layer, comp, frame, samples) -> list[float]:
    """Подкадры для размытия в движении (угол затвора 180°). Если слой не двигается — один кадр."""
    from .effects import shake_wants_blur
    if samples < 2 or not comp.motion_blur or not (layer.motion_blur or (layer.effects and shake_wants_blur(layer))):
        return [frame]
    a, b = layer_transform(layer, frame - 0.25, comp), layer_transform(layer, frame + 0.25, comp)
    if all(abs(x - y) < 1e-4 for x, y in zip(
            (a.m11(), a.m12(), a.m13(), a.m21(), a.m22(), a.m23(), a.dx(), a.dy(), a.m33()),
            (b.m11(), b.m12(), b.m13(), b.m21(), b.m22(), b.m23(), b.dx(), b.dy(), b.m33()))):
        return [frame]
    return [frame - 0.25 + 0.5 * i / (samples - 1) for i in range(samples)]


def _layer_buffer(layer, comp, frame, pool, scale, w, h, base, times) -> np.ndarray:
    """Слой отдельно на прозрачном холсте размером с композицию (с размытием в движении)."""
    acc = None
    masked = bool(active_masks(layer))
    for t in times:
        img = _new_canvas(w, h)
        p = _painter(img)
        tr = layer_transform(layer, t, comp) * base
        p.setTransform(tr)
        draw_layer(p, layer, comp, frame, pool, scale)
        p.end()
        arr = to_array(img)
        if masked:
            m = mask_alpha(layer, layer.local(t), w, h, tr, scale)
            if m is not None:
                arr = arr * m
        acc = arr if acc is None else acc + arr
    return acc / len(times)


def _needs_3d_pass(layer, comp, frame, scale) -> bool:
    if not layer.threed:
        return False
    from .lighting import dof_radius, lit
    return lit(layer, comp, frame) or dof_radius(layer, comp, frame, scale) >= 0.5


def _composited(layer, comp, frame, pool, scale, w, h, base, mb_samples, times=None) -> np.ndarray:
    """Слой на холсте композиции со своими эффектами и трек-маттом (без непрозрачности слоя)."""
    if times is None:
        times = _mb_times(layer, comp, frame, mb_samples)
    from .fx_extra import echo_combine, time_sources
    ts = time_sources(layer, frame, comp.fps) if layer.effects else None
    if ts is None:
        arr = _layer_buffer(layer, comp, frame, pool, scale, w, h, base, times)
        arr = apply_effects(arr, layer, frame, comp.fps, scale)
    else:   # эффекты времени: содержимое и эффекты — в другие моменты, трансформация — на текущем кадре
        srcs, op = ts
        parts = []
        for sf, wgt in srcs:
            a = _layer_buffer(layer, comp, sf, pool, scale, w, h, base, times)
            parts.append((apply_effects(a, layer, sf, comp.fps, scale), wgt))
        arr = echo_combine(parts, op)
    if layer.threed:           # свет, тени и глубина резкости 3D — после эффектов слоя, как в AE
        from .lighting import dof_radius, lit, shade
        if lit(layer, comp, frame):
            arr = shade(arr, layer, comp, frame, scale, pool)
        r = dof_radius(layer, comp, frame, scale)
        if r >= 0.5:
            from .effects import blur
            arr = blur(arr, r)
    if getattr(layer, "matte", None):
        arr = arr * _matte_alpha(layer, comp, frame, pool, scale, w, h, base, mb_samples)
    return arr


def _layer_mask(layer, comp, frame, w, h, base) -> np.ndarray:
    """Альфа-маска прямоугольника слоя (для корректирующего слоя)."""
    img = _new_canvas(w, h)
    p = _painter(img)
    p.setTransform(layer_transform(layer, frame, comp) * base)
    x, y, lw, lh = layer.local_rect()
    p.fillRect(QRectF(x, y, lw, lh), QColor("#ffffff"))
    p.end()
    return to_array(img)[..., 3:4] / 255.0


def visible_layers(comp: Composition, frame: int, extra=()) -> list[Layer]:
    """Слои, которые рисуются на этом кадре (снизу вверх), с учётом Solo.
    extra — id слоёв, видимых вне своих In/Out (слой B во время перехода).
    Подряд идущие 3D-слои сортируются по глубине от камеры, как в AE."""
    solo = any(l.solo and l.visible and l.kind not in ("audio", "camera", "light") for l in comp.layers)
    out = [l for l in reversed(comp.layers)
           if (l.active_at(frame) or l.id in extra) and l.kind not in ("audio", "camera", "light") and (l.solo or not solo)]
    if not any(l.threed for l in out):
        return out
    from .three import layer_depth
    res, run = [], []
    for l in out + [None]:
        if l is not None and l.threed:
            run.append(l)
            continue
        if run:
            res += sorted(run, key=lambda x: -layer_depth(x, comp, frame))
            run = []
        if l is not None:
            res.append(l)
    return res


def render_frame(comp: Composition, frame: int, pool: MediaPool, scale: float = 1.0,
                 mb_samples: int = 8, transparent: bool = False) -> QImage:
    """transparent=True — без фона композиции (для рендера с альфа-каналом)."""
    w = max(2, int(comp.width * scale))
    h = max(2, int(comp.height * scale))
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    if transparent:
        img.fill(0)
    else:
        img.fill(QColor(comp.bg))
    p = _painter(img)
    base = QTransform.fromScale(w / comp.width, h / comp.height)
    from .transitions import apply_transition, transitions_at
    trans = transitions_at(comp, frame)
    skip = {t[0].id for t in trans.values() if t[0] is not None}   # слой A рисуется внутри перехода
    for layer in visible_layers(comp, frame, trans):
        if layer.kind == "null" or layer.id in skip:
            continue
        (op,) = layer.props["opacity"].value_at(layer.local(frame))
        if layer.id in trans:
            a, prog, fx, v = trans[layer.id]
            arr_b = _composited(layer, comp, frame, pool, scale, w, h, base, mb_samples) * (op / 100.0)
            if v.get("edge", 0) == 1:            # переход на выходе: слой уходит в то, что под ним
                arr_a, arr_b = arr_b, np.zeros_like(arr_b)
            elif a is not None:
                op_a = a.props["opacity"].value_at(a.local(frame))[0] / 100.0
                arr_a = _composited(a, comp, frame, pool, scale, w, h, base, mb_samples) * op_a
            else:
                arr_a = np.zeros_like(arr_b)
            p.setCompositionMode(_BLEND.get(getattr(layer, "blend", "normal"), QPainter.CompositionMode_SourceOver))
            p.setOpacity(1.0)
            p.setTransform(QTransform())
            p.drawImage(QPointF(0, 0), to_image(apply_transition(fx, arr_a, arr_b, prog, v, scale, frame, comp.fps)))
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            continue
        if op <= 0:
            continue
        if layer.adjustment:
            if not visual_effects(layer):
                continue
            # корректирующий слой: обрабатываем всё, что уже нарисовано под ним
            p.end()
            under = to_array(img)
            done = apply_effects(under.copy(), layer, frame, comp.fps, scale, include_transform=True)
            m = _layer_mask(layer, comp, frame, w, h, base) * (op / 100.0)
            img = to_image(under * (1 - m) + done * m)
            p = _painter(img)
            continue
        times = _mb_times(layer, comp, frame, mb_samples)
        p.setCompositionMode(_BLEND.get(getattr(layer, "blend", "normal"), QPainter.CompositionMode_SourceOver))
        has_matte = bool(getattr(layer, "matte", None))
        if len(times) == 1 and not has_pixel_effects(layer) and not active_masks(layer) and not has_matte \
                and not _needs_3d_pass(layer, comp, frame, scale):
            p.setOpacity(op / 100.0)
            p.setTransform(layer_transform(layer, frame, comp) * base)
            draw_layer(p, layer, comp, frame, pool, scale)
            p.setCompositionMode(QPainter.CompositionMode_SourceOver)
            continue
        arr = _composited(layer, comp, frame, pool, scale, w, h, base, mb_samples, times)
        p.setOpacity(op / 100.0)
        p.setTransform(QTransform())
        p.drawImage(QPointF(0, 0), to_image(arr))
        p.setCompositionMode(QPainter.CompositionMode_SourceOver)
    p.end()
    return img


def layer_hit(layer: Layer, frame: int, point: QPointF, comp: Composition | None = None) -> bool:
    if layer.kind in ("audio", "camera", "light"):
        return False
    inv, ok = layer_transform(layer, frame, comp).inverted()
    if not ok:
        return False
    x, y, w, h = layer.local_rect()
    q = inv.map(point)
    if not QRectF(x, y, w, h).contains(q):
        return False
    if layer.kind == "shape":   # у шейпа попадание — по самим фигурам, а не по рамке
        from .shapes import shape_path
        lf = layer.local(frame)
        return any(shape_path(layer, sh, lf).contains(q) for sh in layer.shapes) or \
            any(shape_path(layer, sh, lf).boundingRect().adjusted(-6, -6, 6, 6).contains(q)
                for sh in layer.shapes if sh.get("stroke") and not sh.get("fill", True))
    return True


def layer_corners(layer: Layer, frame: int, comp: Composition | None = None) -> list[QPointF]:
    t = layer_transform(layer, frame, comp)
    x, y, w, h = layer.local_rect()
    return [t.map(QPointF(px, py)) for px, py in ((x, y), (x + w, y), (x + w, y + h), (x, y + h))]
