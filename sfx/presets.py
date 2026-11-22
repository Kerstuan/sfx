# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Шаблоны анимаций (как Animation Presets в AE): ставят ключи/эффекты от текущего кадра."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .effects import prop_key
from .i18n import LANG
from .model import EASE, Keyframe


@dataclass
class Preset:
    ru: str
    en: str
    fn: Callable
    text_only: bool = False

    @property
    def label(self) -> str:
        return self.en if LANG == "en" else self.ru


def _keys(prop, keys, ease=True):
    """keys: [(локальный кадр, значение)] — заменяют ключи в этом диапазоне."""
    lo, hi = min(k[0] for k in keys), max(k[0] for k in keys)
    if not prop.animated:
        prop.keyframes = []
    prop.keyframes = [k for k in prop.keyframes if not lo <= k.frame <= hi]
    for f, v in keys:
        v = list(v) if isinstance(v, (list, tuple)) else [float(v)]
        v = (v + prop.raw_at(f)[len(v):])[:prop.dims]
        k = Keyframe(int(f), [float(x) for x in v])
        if ease:
            k.in_x, k.in_y = EASE
            k.out_x, k.out_y = EASE
        prop.keyframes.append(k)
    prop.sort()


def _sec(ed, s):
    return max(1, int(round(s * ed.comp.fps)))


def fade_in(l, ed, f):
    _keys(l.props["opacity"], [(f, 0), (f + _sec(ed, .5), l.props["opacity"].raw_at(f)[0] or 100)])


def fade_out(l, ed, f):
    _keys(l.props["opacity"], [(f, l.props["opacity"].raw_at(f)[0] or 100), (f + _sec(ed, .5), 0)])


def _slide(l, ed, f, dx, dy):
    pos = l.props["position"].raw_at(f)
    _keys(l.props["position"], [(f, [pos[0] + dx, pos[1] + dy]), (f + _sec(ed, .6), pos[:2])])
    k = l.props["position"].keyframe_at(f)
    if k:  # выезд без разгона: быстро в начале, плавно в конце
        k.out_x, k.out_y = 0.1, 0.6


def slide_left(l, ed, f):
    _slide(l, ed, f, -ed.comp.width * .6, 0)


def slide_right(l, ed, f):
    _slide(l, ed, f, ed.comp.width * .6, 0)


def slide_up(l, ed, f):
    _slide(l, ed, f, 0, ed.comp.height * .5)


def pop(l, ed, f):
    s = l.props["scale"].raw_at(f)
    _keys(l.props["scale"], [(f, [0, 0]), (f + _sec(ed, .25), [s[0] * 1.12, s[1] * 1.12]), (f + _sec(ed, .4), s[:2])])


def spin_in(l, ed, f):
    r = l.props["rotation"].raw_at(f)[0]
    _keys(l.props["rotation"], [(f, r - 180), (f + _sec(ed, .6), r)])
    fade_in(l, ed, f)


def bounce(l, ed, f):
    pos = l.props["position"].raw_at(f)
    x, y = pos[0], pos[1]
    h = ed.comp.height * .35
    seq = [(0, y - h), (.35, y), (.5, y - h * .3), (.65, y), (.75, y - h * .08), (.85, y)]
    _keys(l.props["position"], [(f + _sec(ed, t) if t else f, [x, v]) for t, v in seq])


def zoom_punch(l, ed, f):
    """Удар зумом под бит — частый приём в эдитах."""
    s = l.props["scale"].raw_at(f)
    _keys(l.props["scale"], [(f, s[:2]), (f + _sec(ed, .07), [s[0] * 1.15, s[1] * 1.15]), (f + _sec(ed, .35), s[:2])])


def wiggle_expr(l, ed, f):
    p = l.props["position"]
    p.expr, p.expr_on = "wiggle(4, 25)", True


def _effect_keys(l, ed, fx_type, param, keys):
    ed.add_effect(fx_type, [l])
    fx = l.effects[-1]
    _keys(l.props[prop_key(fx["id"], param)], keys, ease=False)
    return fx


def _shake(l, ed, mode, amount, rot, freq, zoom=0.0, detail=2.0, keys=None):
    ed.add_effect("shake", [l])
    fx = l.effects[-1]
    P = lambda k, v: setattr(l.props[prop_key(fx["id"], k)], "value", [float(v)])  # noqa: E731
    P("mode", mode)
    P("amount", amount)
    P("rot", rot)
    P("freq", freq)
    P("zoom", zoom)
    P("detail", detail)
    if keys:
        _keys(l.props[prop_key(fx["id"], "amount")], keys, ease=False)
        for k in l.props[prop_key(fx["id"], "amount")].keyframes:   # быстрый спад, как у удара
            k.out_x, k.out_y = 0.15, 0.55
    return fx


def shake_hit(l, ed, f):
    """Удар: резкий толчок, быстро затухает."""
    _shake(l, ed, 2, 60, 4, 12, zoom=6, keys=[(f, 60), (f + _sec(ed, .45), 0)])


def shake_handheld(l, ed, f):
    _shake(l, ed, 1, 14, 1.5, 3, detail=3)


def shake_earthquake(l, ed, f):
    _shake(l, ed, 4, 40, 2.5, 9, zoom=3, detail=4)


def shake_jitter(l, ed, f):
    _shake(l, ed, 3, 7, 0.6, 24)


def shake_explosion(l, ed, f):
    """Взрыв: сильный удар + зум + вспышка."""
    _shake(l, ed, 2, 140, 9, 14, zoom=16, keys=[(f, 140), (f + _sec(ed, .9), 0)])
    _effect_keys(l, ed, "flash", "amount", [(f, 70), (f + _sec(ed, .3), 0)])


def shake_beat(l, ed, f):
    """Тряска под бит: 4 толчка через полсекунды (передвинь ключи под свою музыку)."""
    keys = []
    for i in range(4):
        t0 = f + _sec(ed, .5) * i
        keys += [(t0, 45), (t0 + _sec(ed, .25), 0)]
    _shake(l, ed, 2, 45, 3, 12, zoom=5, keys=keys)


def _speed(l, ed, f, seq):
    if "speed" not in l.props:
        return
    _keys(l.props["speed"], [(f + _sec(ed, t) if t else f, v) for t, v in seq])


def speed_ramp(l, ed, f):
    """Спид-рамп как в эдитах: разгон → резкое замедление → обратно."""
    _speed(l, ed, f, [(0, 100), (.25, 350), (.45, 25), (1.2, 25), (1.5, 100)])


def slowmo(l, ed, f):
    _speed(l, ed, f, [(0, 100), (.4, 30)])
    if l.kind == "video":
        l.frame_blend = True


def speedup(l, ed, f):
    _speed(l, ed, f, [(0, 100), (.4, 300)])


def flash_hit(l, ed, f):
    _effect_keys(l, ed, "flash", "amount", [(f, 85), (f + _sec(ed, .25), 0)])


def blur_in(l, ed, f):
    _effect_keys(l, ed, "blur", "radius", [(f, 40), (f + _sec(ed, .5), 0)])


def chroma_hit(l, ed, f):
    _effect_keys(l, ed, "chroma", "amount", [(f, 25), (f + _sec(ed, .3), 0)])


def typewriter(l, ed, f):
    n = max(1, len(l.text.replace("\n", "")))
    _keys(l.props["text_reveal"], [(f, 0), (f + max(1, int(round(n * .06 * ed.comp.fps))), 100)], ease=False)


PRESETS: dict[str, Preset] = {
    "fade_in": Preset("Плавное появление", "Fade In", fade_in),
    "fade_out": Preset("Плавное исчезновение", "Fade Out", fade_out),
    "slide_left": Preset("Выезд слева", "Slide In Left", slide_left),
    "slide_right": Preset("Выезд справа", "Slide In Right", slide_right),
    "slide_up": Preset("Выезд снизу", "Slide In Up", slide_up),
    "pop": Preset("Появление с отскоком (pop)", "Pop In", pop),
    "spin_in": Preset("Влёт с вращением", "Spin In", spin_in),
    "bounce": Preset("Падение с отскоками", "Bounce Drop", bounce),
    "zoom_punch": Preset("Удар зумом", "Zoom Punch", zoom_punch),
    "shake_hit": Preset("Тряска: удар", "Shake: Hit", shake_hit),
    "shake_explosion": Preset("Тряска: взрыв (удар + зум + вспышка)", "Shake: Explosion", shake_explosion),
    "shake_beat": Preset("Тряска: под бит (4 толчка)", "Shake: Beat", shake_beat),
    "shake_handheld": Preset("Тряска: ручная камера", "Shake: Handheld", shake_handheld),
    "shake_earthquake": Preset("Тряска: землетрясение", "Shake: Earthquake", shake_earthquake),
    "shake_jitter": Preset("Тряска: мелкая дрожь", "Shake: Jitter", shake_jitter),
    "speed_ramp": Preset("Спид-рамп (разгон → слоу-мо → норм)", "Speed Ramp", speed_ramp),
    "slowmo": Preset("Плавное замедление (слоу-мо)", "Slow Motion", slowmo),
    "speedup": Preset("Плавное ускорение", "Speed Up", speedup),
    "flash_hit": Preset("Вспышка", "Flash Hit", flash_hit),
    "chroma_hit": Preset("Рывок RGB", "RGB Split Hit", chroma_hit),
    "blur_in": Preset("Появление из размытия", "Blur In", blur_in),
    "wiggle": Preset("Дрожание (wiggle)", "Wiggle", wiggle_expr),
    "typewriter": Preset("Печатная машинка (текст)", "Typewriter (text)", typewriter, text_only=True),
}


def apply_preset(pid: str, layer, ed) -> bool:
    pr = PRESETS.get(pid)
    if pr is None or layer.kind in ("audio", "camera", "light"):
        return False
    if pr.text_only and layer.kind != "text":
        return False
    if pr.fn in (speed_ramp, slowmo, speedup) and "speed" not in layer.props:
        return False
    pr.fn(layer, ed, int(layer.local(ed.time)))
    return True
