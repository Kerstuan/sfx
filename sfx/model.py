# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Модель проекта: композиция, слои, свойства, ключевые кадры.

Все времена хранятся в кадрах (int) композиции.
  * layer.start      — кадр композиции, где начинается исходник слоя (время 0 исходника)
  * layer.in_point   — первый видимый кадр слоя (в кадрах композиции)
  * layer.out_point  — кадр, на котором слой уже не виден (исключительно)
  * keyframe.frame   — время ключа в кадрах ОТНОСИТЕЛЬНО layer.start,
                       поэтому при сдвиге слоя ключи едут вместе с ним (как в AE)
"""
from __future__ import annotations

from .i18n import tr

import copy

import uuid
from dataclasses import dataclass, field, asdict

from .effects import EFFECTS, find_param, prop_key

# Ручки кривой (influence, speed) в нормализованных координатах сегмента.
LINEAR = (1 / 3, 1 / 3)
EASE = (1 / 3, 0.0)


def bezier_ease(x: float, x1: float, y1: float, x2: float, y2: float) -> float:
    """cubic-bezier(x1, y1, x2, y2) от (0,0) до (1,1): по x находим y."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0

    def bx(s):
        return 3 * (1 - s) ** 2 * s * x1 + 3 * (1 - s) * s * s * x2 + s ** 3

    def by(s):
        return 3 * (1 - s) ** 2 * s * y1 + 3 * (1 - s) * s * s * y2 + s ** 3

    def dbx(s):
        return 3 * (1 - s) ** 2 * x1 + 6 * (1 - s) * s * (x2 - x1) + 3 * s * s * (1 - x2)

    s = x
    for _ in range(8):  # Ньютон
        err = bx(s) - x
        if abs(err) < 1e-7:
            return by(s)
        d = dbx(s)
        if abs(d) < 1e-6:
            break
        s -= err / d
        if not 0.0 <= s <= 1.0:
            break
    lo, hi = 0.0, 1.0  # бисекция как запасной вариант
    s = x
    for _ in range(40):
        v = bx(s)
        if abs(v - x) < 1e-7:
            break
        if v < x:
            lo = s
        else:
            hi = s
        s = (lo + hi) / 2
    return by(s)


@dataclass
class Keyframe:
    frame: int
    value: list[float]
    out_x: float = LINEAR[0]
    out_y: float = LINEAR[1]
    in_x: float = LINEAR[0]
    in_y: float = LINEAR[1]
    hold: bool = False

    def is_eased_in(self) -> bool:
        return abs(self.in_y) < 1e-6

    def is_eased_out(self) -> bool:
        return abs(self.out_y) < 1e-6


PROP_ORDER = ["anchor", "position", "scale", "rotation", "opacity"]
PROP_ORDER_3D = ["anchor", "position", "scale", "orientation", "rot_x", "rot_y", "rotation", "opacity"]
CAMERA_ORDER = ["poi", "position", "rotation"]
LIGHT_ORDER = ["poi", "position"]
# 3D: камера (глубина резкости), свет и материал слоя — как группы «Параметры …» в AE
CAMERA_OPTS = ["zoom", "focus_dist", "aperture", "blur_level"]
LIGHT_OPTS = ["light_intensity", "light_color", "cone_angle", "cone_feather", "light_radius", "falloff_dist",
              "shadow_darkness", "shadow_diffusion"]
MATERIAL_OPTS = ["mat_ambient", "mat_diffuse", "mat_spec", "mat_shine", "mat_metal"]
LIGHT_TYPES = [("parallel", "Параллельный", "Parallel"), ("spot", "Прожектор", "Spot"),
               ("point", "Точечный", "Point"), ("ambient", "Окружающий", "Ambient")]
FALLOFF_TYPES = [("none", "Нет", "None"), ("smooth", "Плавное", "Smooth"),
                 ("inverse_square", "Обратно квадрату", "Inverse Square Clamped")]
PROP_NAMES = {
    "anchor": tr("Опорная точка"),
    "position": tr("Положение"),
    "scale": tr("Масштаб"),
    "rotation": tr("Поворот"),
    "opacity": tr("Непрозрачность"),
    "orientation": tr("Ориентация"),
    "rot_x": tr("Поворот по X"),
    "rot_y": tr("Поворот по Y"),
    "poi": tr("Точка интереса"),
    "zoom": tr("Зум"),
    "audio_levels": tr("Уровни звука"),
    "text_reveal": tr("Показ символов"),
    "speed": tr("Скорость"),
    "time_remap": tr("Переназначение времени"),
    "focus_dist": tr("Фокусное расстояние"),
    "aperture": tr("Диафрагма"),
    "blur_level": tr("Уровень размытия"),
    "light_intensity": tr("Интенсивность"),
    "light_color": tr("Цвет"),
    "cone_angle": tr("Угол конуса"),
    "cone_feather": tr("Растушёвка конуса"),
    "light_radius": tr("Радиус"),
    "falloff_dist": tr("Расстояние затухания"),
    "shadow_darkness": tr("Темнота тени"),
    "shadow_diffusion": tr("Рассеивание тени"),
    "mat_ambient": tr("Окружающее"),
    "mat_diffuse": tr("Рассеянное"),
    "mat_spec": tr("Интенсивность блика"),
    "mat_shine": tr("Глянец"),
    "mat_metal": tr("Металл"),
}
# Шаг изменения значения при протягивании мышью (на 1 пиксель).
PROP_STEP = {"anchor": 1.0, "position": 1.0, "scale": 0.5, "rotation": 0.5, "opacity": 0.5, "orientation": 0.5,
             "rot_x": 0.5, "rot_y": 0.5, "poi": 1.0, "zoom": 2.0, "audio_levels": 0.1, "text_reveal": 0.5, "speed": 1.0,
             "time_remap": 0.01, "focus_dist": 2.0, "aperture": 0.5, "blur_level": 0.5, "light_intensity": 0.5,
             "light_color": 1.0, "cone_angle": 0.5, "cone_feather": 0.5, "light_radius": 2.0, "falloff_dist": 2.0,
             "shadow_darkness": 0.5, "shadow_diffusion": 0.5, "mat_ambient": 0.5, "mat_diffuse": 0.5, "mat_spec": 0.5,
             "mat_shine": 0.5, "mat_metal": 0.5}
PROP_SUFFIX = {"anchor": "", "position": "", "scale": "%", "rotation": "°", "opacity": "%", "orientation": "°",
               "rot_x": "°", "rot_y": "°", "poi": "", "zoom": " px", "audio_levels": " dB",
               "text_reveal": "%", "speed": "%", "time_remap": " s", "focus_dist": " px", "aperture": " px",
               "blur_level": "%", "light_intensity": "%", "light_color": "", "cone_angle": "°", "cone_feather": "%",
               "light_radius": " px", "falloff_dist": " px", "shadow_darkness": "%", "shadow_diffusion": " px",
               "mat_ambient": "%", "mat_diffuse": "%", "mat_spec": "%", "mat_shine": "%", "mat_metal": "%"}
ANGLE_KEYS = {"rotation", "rot_x", "rot_y"}

# свойства масок и фигур: (подпись, шаг, суффикс, мин, макс)
MASK_PROPS = {
    "path": (tr("Контур маски"), 1.0, "", None, None),
    "feather": (tr("Растушёвка маски"), 0.5, " px", 0.0, 1000.0),
    "opacity": (tr("Непрозрачность маски"), 0.5, "%", 0.0, 100.0),
    "expansion": (tr("Расширение маски"), 0.5, " px", -1000.0, 1000.0),
}
SHAPE_PROPS = {
    "path": (tr("Контур"), 1.0, "", None, None),
    "size": (tr("Размер"), 1.0, "", 0.0, None),
    "position": (tr("Положение"), 1.0, "", None, None),
    "roundness": (tr("Скругление"), 0.5, "", 0.0, 5000.0),
    "points": (tr("Лучей"), 0.05, "", 3.0, 100.0),
    "outer": (tr("Внешний радиус"), 1.0, "", 0.0, None),
    "inner": (tr("Внутренний радиус"), 1.0, "", 0.0, None),
    "srot": (tr("Поворот"), 0.5, "°", None, None),
    "stroke_width": (tr("Ширина обводки"), 0.2, "", 0.0, 1000.0),
    "fill_opacity": (tr("Непрозрачность заливки"), 0.5, "%", 0.0, 100.0),
}


def _sub(key):
    """'mask/<id>/feather' -> ('mask', id, 'feather')."""
    parts = key.split("/", 2)
    return parts if len(parts) == 3 else None


def prop_label(layer, key) -> str:
    if key == "rotation" and getattr(layer, "threed", False) and layer.kind != "camera":
        return tr("Поворот по Z")
    if key in PROP_NAMES:
        return PROP_NAMES[key]
    sp = _sub(key)
    if sp and sp[0] == "mask" and sp[2] in MASK_PROPS:
        return MASK_PROPS[sp[2]][0]
    if sp and sp[0] == "shape" and sp[2] in SHAPE_PROPS:
        return SHAPE_PROPS[sp[2]][0]
    if sp and sp[0] == "shape":
        from .shape_ops import param_info
        pi = param_info(layer, key)
        if pi:
            return pi[0]
    fp = find_param(layer, key)
    return fp[2].label if fp else key


def prop_step(layer, key) -> float:
    if key in PROP_STEP:
        return PROP_STEP[key]
    sp = _sub(key)
    if sp and sp[0] == "mask" and sp[2] in MASK_PROPS:
        return MASK_PROPS[sp[2]][1]
    if sp and sp[0] == "shape" and sp[2] in SHAPE_PROPS:
        return SHAPE_PROPS[sp[2]][1]
    if sp and sp[0] == "shape":
        from .shape_ops import param_info
        pi = param_info(layer, key)
        if pi:
            return pi[1]
    fp = find_param(layer, key)
    return fp[2].step if fp else 1.0


def prop_suffix(layer, key) -> str:
    if key in PROP_SUFFIX:
        return PROP_SUFFIX[key]
    sp = _sub(key)
    if sp and sp[0] == "mask" and sp[2] in MASK_PROPS:
        return MASK_PROPS[sp[2]][2]
    if sp and sp[0] == "shape" and sp[2] in SHAPE_PROPS:
        return SHAPE_PROPS[sp[2]][2]
    if sp and sp[0] == "shape":
        from .shape_ops import param_info
        pi = param_info(layer, key)
        if pi:
            return pi[2]
    fp = find_param(layer, key)
    return fp[2].suffix if fp else ""


def prop_kind(layer, key) -> str:
    """Как показывать значение: float | angle | checkbox | choice | color | point | path."""
    if key in ANGLE_KEYS:
        return "angle"
    if key == "light_color":
        return "color"
    sp = _sub(key)
    if sp and sp[0] in ("mask", "shape") and sp[2] == "path":
        return "path"
    fp = find_param(layer, key)
    return fp[2].kind if fp else "float"


class Property:
    def __init__(self, key: str, value: list[float]):
        self.key = key
        self.value = list(value)
        self.keyframes: list[Keyframe] = []
        self.lo: float | None = None
        self.hi: float | None = None
        self.expr = ""            # выражение (JavaScript, как в AE)
        self.expr_on = False
        self.expr_error = ""      # последняя ошибка выражения (не сохраняется)
        self._layer = None        # слой-владелец (для выражений)

    # --- анимация ----------------------------------------------------------
    @property
    def animated(self) -> bool:
        return bool(self.keyframes)

    @property
    def dims(self) -> int:
        return len(self.value)

    def sort(self):
        self.keyframes.sort(key=lambda k: k.frame)

    def keyframe_at(self, frame: int) -> Keyframe | None:
        for k in self.keyframes:
            if k.frame == frame:
                return k
        return None

    @property
    def has_expr(self) -> bool:
        return self.expr_on and bool(self.expr.strip())

    def value_at(self, frame: float) -> list[float]:
        """Значение с учётом выражения (как видит его рендер)."""
        raw = self.raw_at(frame)
        if self.has_expr and self._layer is not None:
            from .expressions import evaluate
            return evaluate(self, frame, raw)
        return raw

    def raw_at(self, frame: float) -> list[float]:
        """Значение по ключам, без выражения."""
        ks = self.keyframes
        if not ks:
            return list(self.value)
        if frame <= ks[0].frame:
            return list(ks[0].value)
        if frame >= ks[-1].frame:
            return list(ks[-1].value)
        for i in range(len(ks) - 1):
            k0, k1 = ks[i], ks[i + 1]
            if k0.frame <= frame < k1.frame:
                if k0.hold:
                    return list(k0.value)
                u = (frame - k0.frame) / (k1.frame - k0.frame)
                p = bezier_ease(u, k0.out_x, k0.out_y, 1 - k1.in_x, 1 - k1.in_y)
                return [a + (b - a) * p for a, b in zip(k0.value, k1.value)]
        return list(ks[-1].value)

    def set_value_at(self, frame: int, value: list[float]):
        """Как в AE: если секундомер включён — ставим/правим ключ, иначе статичное значение."""
        value = [float(v) for v in value]
        if len(value) < self.dims:  # например, 2 числа для 3D-положения — z сохраняем
            value += self.raw_at(frame)[len(value):]
        if self.lo is not None:
            value = [max(self.lo, v) for v in value]
        if self.hi is not None:
            value = [min(self.hi, v) for v in value]
        if not self.animated:
            self.value = value
            return
        k = self.keyframe_at(frame)
        if k:
            k.value = value
        else:
            self.keyframes.append(Keyframe(frame, value))
            self.sort()

    def add_keyframe(self, frame: int):
        if not self.keyframe_at(frame):
            self.keyframes.append(Keyframe(frame, self.raw_at(frame)))
            self.sort()

    def toggle_stopwatch(self, frame: int):
        if self.animated:
            self.value = self.raw_at(frame)
            self.keyframes = []
        else:
            self.keyframes = [Keyframe(frame, list(self.value))]

    # --- сериализация --------------------------------------------------------
    def set_dims(self, n: int, fill: float = 0.0):
        """Поменять размерность (2D <-> 3D): лишнее отрезаем, недостающее заполняем."""
        def fix(v):
            return (list(v) + [fill] * n)[:n]
        self.value = fix(self.value)
        for k in self.keyframes:
            k.value = fix(k.value)

    def to_dict(self):
        d = {"value": self.value, "keyframes": [asdict(k) for k in self.keyframes]}
        if self.expr:
            d["expr"] = self.expr
            d["expr_on"] = self.expr_on
        return d

    @classmethod
    def from_dict(cls, key, d):
        p = cls(key, d["value"])
        p.keyframes = [Keyframe(**k) for k in d.get("keyframes", [])]
        p.expr = d.get("expr", "")
        p.expr_on = d.get("expr_on", False)
        p.sort()
        return p


LAYER_COLORS = {  # цвета полосок как в AE (label colors)
    "adjust": "#9c9c9c",
    "video": "#8a9fc4",
    "image": "#a99ac2",
    "solid": "#c4676a",
    "text": "#d4848f",
    "shape": "#5fa8a0",
    "camera": "#c9a95c",
    "light": "#e0c860",
    "audio": "#7cb07a",
    "comp": "#d8b878",
    "null": "#c27a5c",
}

BLEND_MODES = [("normal", "Обычный", "Normal"), ("add", "Добавить", "Add"), ("screen", "Экран", "Screen"),
               ("lighten", "Замена светлым", "Lighten"), ("dodge", "Осветление основы", "Color Dodge"),
               ("multiply", "Умножение", "Multiply"), ("darken", "Замена тёмным", "Darken"),
               ("burn", "Затемнение основы", "Color Burn"), ("overlay", "Перекрытие", "Overlay"),
               ("soft", "Мягкий свет", "Soft Light"), ("hard", "Жёсткий свет", "Hard Light"),
               ("difference", "Разница", "Difference"), ("exclusion", "Исключение", "Exclusion")]
MATTE_MODES = [("alpha", "Альфа", "Alpha Matte"), ("alpha_inv", "Альфа инверт.", "Alpha Inverted Matte"),
               ("luma", "Яркость", "Luma Matte"), ("luma_inv", "Яркость инверт.", "Luma Inverted Matte")]

# Цвета меток как в AE (Label): название -> цвет
LABELS = [("Красный", "Red", "#b53838"), ("Жёлтый", "Yellow", "#e4d84c"), ("Аква", "Aqua", "#a9cbc7"),
          ("Розовый", "Pink", "#e5bcc9"), ("Лаванда", "Lavender", "#a9a9ca"), ("Персик", "Peach", "#e7c19e"),
          ("Морская пена", "Sea Foam", "#b3c7b3"), ("Синий", "Blue", "#677de0"), ("Зелёный", "Green", "#4aa44c"),
          ("Фиолетовый", "Purple", "#8e2c9a"), ("Оранжевый", "Orange", "#e8920d"), ("Коричневый", "Brown", "#7f452a"),
          ("Фуксия", "Fuchsia", "#f46dd6"), ("Голубой", "Cyan", "#3da2a5"), ("Песочный", "Sandstone", "#a89677"),
          ("Тёмно-зелёный", "Dark Green", "#1e401e")]

DEFAULT_ZOOM = 2666.7  # «50 мм» для ширины 1920, как камера по умолчанию в AE


class Layer:
    def __init__(self, kind: str, name: str, width: float, height: float, comp: "Composition",
                 source: str | None = None):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.name = name
        self.source = source
        self.width = float(width)
        self.height = float(height)
        self.start = 0
        self.in_point = 0
        self.out_point = comp.duration
        self.visible = True
        self.audio = True        # для видео: включён ли звук
        self.has_audio = False
        self.source_frames: int | None = None  # длина исходника в кадрах композиции
        self.color = "#ffffff"   # солид / текст
        self.text = tr("Текст")
        self.font_family = "Sans Serif"
        self.font_size = 96.0
        self.bold = True
        # переключатели как в AE
        self.parent: str | None = None   # id родительского слоя
        self.solo = False
        self.locked = False
        self.motion_blur = False
        self.adjustment = False          # корректирующий слой: эффекты применяются ко всему, что под ним
        self.fx_on = True
        self.effects: list[dict] = []    # [{"id", "type", "on", "opts", "name"}]
        self.threed = False              # 3D-слой (кубик в колонке переключателей)
        self.scale_link = True           # цепочка у масштаба: менять X и Y вместе
        self.masks: list[dict] = []      # [{"id", "name", "mode", "inverted", "closed"}]
        self.shapes: list[dict] = []     # для шейп-слоя: [{"id", "name", "type", "fill", "fill_color", ...}]
        self._comp = comp
        self.label = None                # цвет метки слоя (как Label в AE); None — по типу слоя
        self.blend = "normal"            # режим наложения (Mode): normal, add, screen, multiply, overlay…
        self.matte = None                # трек-матт: {"layer": id, "mode": alpha|alpha_inv|luma|luma_inv}
        self.reverse = False             # обратное воспроизведение
        self.frame_blend = False         # смешивание кадров (плавное замедление)
        self.markers: list[dict] = []    # маркеры слоя: {"frame": локальный кадр, "name"} — едут вместе со слоем
        r = self.local_rect()
        cx, cy = r[0] + r[2] / 2, r[1] + r[3] / 2
        self.props: dict[str, Property] = {
            "anchor": Property("anchor", [cx, cy]),
            "position": Property("position", [comp.width / 2, comp.height / 2]),
            "scale": Property("scale", [100.0, 100.0]),
            "rotation": Property("rotation", [0.0]),
            "opacity": Property("opacity", [100.0]),
        }
        self.refresh_meta()

    def refresh_meta(self):
        """Недостающие свойства, ограничения значений и ссылки на слой (не сохраняются, выставляются заново)."""
        self.ensure_props()
        op = self.props["opacity"]
        op.lo, op.hi = 0.0, 100.0
        al = self.props.get("audio_levels")
        if al:
            al.lo, al.hi = -96.0, 24.0
        sp = self.props.get("speed")
        if sp:
            sp.lo, sp.hi = 1.0, 10000.0
        trm = self.props.get("time_remap")
        if trm:
            trm.lo = 0.0
        tr_ = self.props.get("text_reveal")
        if tr_:
            tr_.lo, tr_.hi = 0.0, 100.0
        if "zoom" in self.props:
            self.props["zoom"].lo = 1.0
        for key, prop in self.props.items():
            prop._layer = self
            fp = find_param(self, key)
            if fp:
                prop.lo, prop.hi = fp[2].lo, fp[2].hi
            sp = _sub(key)
            if sp and sp[0] == "mask" and sp[2] in MASK_PROPS:
                prop.lo, prop.hi = MASK_PROPS[sp[2]][3:5]
            elif sp and sp[0] == "shape" and sp[2] in SHAPE_PROPS:
                prop.lo, prop.hi = SHAPE_PROPS[sp[2]][3:5]
            elif sp and sp[0] == "shape":
                from .shape_ops import param_info
                pi = param_info(self, key)
                if pi:
                    prop.lo, prop.hi = pi[3], pi[4]

    def ensure_props(self):
        comp = self._comp
        cw, ch = (comp.width, comp.height) if comp else (1920, 1080)
        p = self.props
        if self.kind in ("video", "audio", "comp") and "audio_levels" not in p:
            p["audio_levels"] = Property("audio_levels", [0.0])
        if self.kind in ("video", "audio", "comp") and "speed" not in p:
            p["speed"] = Property("speed", [100.0])
        if self.kind == "text" and "text_reveal" not in p:
            p["text_reveal"] = Property("text_reveal", [100.0])
        for fx in self.effects:   # параметры, добавленные в эффекты позже (старые проекты)
            d = EFFECTS.get(fx["type"])
            if d:
                for prm in d.params:
                    k = prop_key(fx["id"], prm.key)
                    if k not in p:
                        p[k] = Property(k, list(prm.default))
        is3d = self.threed or self.kind in ("camera", "light")
        dims = 3 if is3d else 2
        for key in ("anchor", "position"):
            if p[key].dims != dims:
                p[key].set_dims(dims)
        # материал 3D-слоя (Material Options в AE); значения по умолчанию как в AE
        mat = (("mat_ambient", [100.0]), ("mat_diffuse", [50.0]), ("mat_spec", [50.0]), ("mat_shine", [5.0]),
               ("mat_metal", [100.0]))
        if self.threed and self.kind not in ("camera", "light"):
            for key, val in (("orientation", [0.0, 0.0, 0.0]), ("rot_x", [0.0]), ("rot_y", [0.0])) + mat:
                p.setdefault(key, Property(key, val))
            if not isinstance(getattr(self, "material", None), dict):
                self.material = {"accepts_lights": True, "casts_shadows": False, "accepts_shadows": True}
        elif self.kind not in ("camera", "light"):
            for key in ("orientation", "rot_x", "rot_y") + tuple(k for k, _ in mat):
                p.pop(key, None)
        if self.kind == "camera":
            zoom = DEFAULT_ZOOM * cw / 1920.0
            p.setdefault("zoom", Property("zoom", [round(zoom, 1)]))
            p.setdefault("poi", Property("poi", [cw / 2, ch / 2, 0.0]))
            # глубина резкости: по умолчанию фокус = зум (резкость на плоскости z=0), как у камеры AE
            p.setdefault("focus_dist", Property("focus_dist", [round(zoom, 1)]))
            p.setdefault("aperture", Property("aperture", [25.0]))
            p.setdefault("blur_level", Property("blur_level", [100.0]))
            if not hasattr(self, "dof"):
                self.dof = False
        if self.kind == "light":
            p.setdefault("poi", Property("poi", [cw / 2, ch / 2, 0.0]))
            for key, val in (("light_intensity", [100.0]), ("light_color", [255.0, 255.0, 255.0]),
                             ("cone_angle", [90.0]), ("cone_feather", [50.0]), ("light_radius", [500.0]),
                             ("falloff_dist", [500.0]), ("shadow_darkness", [100.0]), ("shadow_diffusion", [0.0])):
                p.setdefault(key, Property(key, val))
            if not isinstance(getattr(self, "light", None), dict):
                self.light = {"type": "spot", "falloff": "none", "shadows": False}

    def transform_keys(self) -> list[str]:
        if self.kind == "audio":
            return []
        if self.kind == "camera":
            return list(CAMERA_ORDER)
        if self.kind == "light":
            return list(LIGHT_ORDER)
        return list(PROP_ORDER_3D if self.threed else PROP_ORDER)

    def option_keys(self) -> list[str]:
        """Свойства групп «Параметры камеры / света / материала» (кроме трансформации)."""
        if self.kind == "camera":
            return [k for k in CAMERA_OPTS if k in self.props]
        if self.kind == "light":
            t = self.light["type"]
            keys = ["light_intensity", "light_color"]
            if t == "spot":
                keys += ["cone_angle", "cone_feather"]
            if t in ("spot", "point"):
                if self.light["falloff"] != "none":
                    keys += ["light_radius", "falloff_dist"]
            if t != "ambient" and self.light.get("shadows"):
                keys += ["shadow_darkness", "shadow_diffusion"]
            return keys
        if self.threed:
            return [k for k in MATERIAL_OPTS if k in self.props]
        return []

    def mask_keys(self, m) -> list[str]:
        return [f"mask/{m['id']}/{k}" for k in MASK_PROPS]

    def shape_keys(self, sh) -> list[str]:
        """Свойства фигуры в порядке AE: контур/размер, заливка и обводка, операторы, трансформация группы."""
        from .shape_ops import GROUP_ORDER, OPS
        sid = sh["id"]
        out = [f"shape/{sid}/{k}" for k in SHAPE_PROPS]
        for o in sh.get("ops", []):
            out += [f"shape/{sid}/op_{o['id']}_{prm}" for prm in OPS.get(o["type"], (None, {}))[1]]
        out += [f"shape/{sid}/{k}" for k in GROUP_ORDER]
        return [k for k in out if k in self.props]

    def prop_keys(self) -> list[str]:
        """Все свойства слоя по порядку: маски, фигуры, эффекты, трансформации, звук, камера."""
        out = []
        for m in self.masks:
            out += self.mask_keys(m)
        for sh in self.shapes:
            out += self.shape_keys(sh)
        for fx in self.effects:
            d = EFFECTS.get(fx["type"])
            if d:
                out += [prop_key(fx["id"], p.key) for p in d.params]
        if "text_reveal" in self.props:
            out.append("text_reveal")
        if "time_remap" in self.props:
            out.append("time_remap")
        if "speed" in self.props:
            out.append("speed")
        out += self.transform_keys()
        if "audio_levels" in self.props:
            out.append("audio_levels")
        if "zoom" in self.props:
            out.append("zoom")
        return [k for k in out if k in self.props]

    def mask_by_id(self, mid):
        return next((m for m in self.masks if m["id"] == mid), None)

    def shape_by_id(self, sid):
        return next((s for s in self.shapes if s["id"] == sid), None)

    def effect_keys(self, fx) -> list[str]:
        d = EFFECTS.get(fx["type"])
        return [prop_key(fx["id"], p.key) for p in d.params] if d else []

    def effect_by_id(self, fid):
        return next((fx for fx in self.effects if fx["id"] == fid), None)

    def local_rect(self) -> tuple[float, float, float, float]:
        """Прямоугольник содержимого в координатах слоя (x, y, w, h)."""
        if self.kind == "text":
            return (-self.width / 2, -self.height / 2, self.width, self.height)
        if self.kind == "shape":
            from .shapes import shapes_bounds
            return shapes_bounds(self)
        if self.kind == "null":
            return (0.0, 0.0, 100.0, 100.0)
        return (0.0, 0.0, self.width, self.height)

    # ------------------------------------------------------------------ время исходника (скорость)
    def _speed_table(self, n: int, fps: float):
        """Накопленное время исходника (сек) на кадрах 0..n при анимированной скорости — с кэшем."""
        p = self.props["speed"]
        sig = (tuple((k.frame, tuple(k.value), k.in_x, k.in_y, k.out_x, k.out_y, k.hold) for k in p.keyframes),
               p.expr if p.has_expr else "", fps, tuple(p.value))
        cache = getattr(self, "_spd", None)
        if cache is None or cache[0] != sig or len(cache[1]) < n + 2:
            m = max(n + 2, int(self.out_point - self.start) + 2, 2)
            v = [p.value_at(f)[0] / 100.0 for f in range(m)]
            acc = [0.0]
            for i in range(1, m):
                acc.append(acc[-1] + (v[i - 1] + v[i]) / 2 / fps)
            cache = (sig, acc)
            self._spd = cache
        return cache[1]

    def src_time(self, local_frame: float, fps: float) -> float:
        """Сколько секунд исходника показывает слой на этом (локальном) кадре — с учётом скорости и реверса.
        Включён Time Remap — время берётся прямо из него (секунды исходника), скорость не действует."""
        trm = self.props.get("time_remap")
        if trm is not None:
            t = max(0.0, trm.value_at(local_frame)[0])
            if self.source_frames:
                t = min(t, (self.source_frames - 1) / fps)
            return t
        p = self.props.get("speed")
        if p is None or (not p.animated and not p.has_expr):
            k = (p.value[0] if p else 100.0) / 100.0
            t = max(0.0, local_frame) / fps * k
        else:
            lf = max(0.0, local_frame)
            tab = self._speed_table(int(lf) + 1, fps)
            i = int(lf)
            fr = lf - i
            t = tab[i] + (tab[i + 1] - tab[i]) * fr
        if getattr(self, "reverse", False) and self.source_frames:
            # исходная длина (сек) = source_frames при 100% / fps
            t = max(0.0, self.source_frames / fps - t - 1.0 / fps)
        return t

    def local_end(self, fps: float) -> float | None:
        """Локальный кадр, на котором кончается исходник при текущей скорости (None — бесконечный)."""
        if not self.source_frames or "time_remap" in self.props:   # с Time Remap слой тянется как угодно
            return None
        total = self.source_frames / fps
        p = self.props.get("speed")
        if p is None or (not p.animated and not p.has_expr):
            return self.source_frames * 100.0 / max(1.0, p.value[0] if p else 100.0)
        tab = self._speed_table(int(self.source_frames * 100) + 2, fps)
        for i, t in enumerate(tab):
            if t >= total:
                return float(i)
        return float(len(tab))

    def color_label(self) -> str:
        """Цвет метки/полоски слоя: заданный пользователем или по типу."""
        if self.label:
            return self.label
        return LAYER_COLORS.get("adjust" if self.adjustment else self.kind, "#888888")

    def local(self, comp_frame: float) -> float:
        return comp_frame - self.start

    def active_at(self, comp_frame: int) -> bool:
        return self.visible and self.in_point <= comp_frame < self.out_point

    def to_dict(self):
        d = {k: copy.deepcopy(v) for k, v in self.__dict__.items() if k != "props" and not k.startswith("_")}
        d["props"] = {k: p.to_dict() for k, p in self.props.items()}
        return d

    @classmethod
    def from_dict(cls, d, comp):
        layer = cls.__new__(cls)
        Layer.__init__(layer, d["kind"], d["name"], d["width"], d["height"], comp, d.get("source"))
        for k, v in d.items():
            if k != "props":
                setattr(layer, k, v)
        layer.props = {k: Property.from_dict(k, pd) for k, pd in d["props"].items()}
        layer.effects = copy.deepcopy(layer.effects)
        layer.masks = copy.deepcopy(layer.masks)
        layer.shapes = copy.deepcopy(layer.shapes)
        layer._comp = comp
        layer.refresh_meta()
        return layer


class Composition:
    def __init__(self, name=None, width=1920, height=1080, fps=30.0, duration=300):
        self.id = uuid.uuid4().hex[:10]
        self.name = name or tr("Композиция 1")
        self.width = int(width)
        self.height = int(height)
        self.fps = float(fps)
        self.duration = int(duration)  # в кадрах
        self.bg = "#000000"
        self.motion_blur = True   # общий переключатель «Размытие в движении» (как кнопка в AE)
        self.layers: list[Layer] = []  # [0] — верхний слой, как в AE
        self.work_in = 0
        self.work_out = self.duration
        self.time = 0             # где стоит курсор — у каждой композиции свой, как в AE
        self.markers: list[dict] = []   # маркеры композиции: {"frame": int, "name": str}, по возрастанию кадра
        self.view3d = "active"          # 3D-вид во вьювере (three.VIEWS); экспорт всегда через активную камеру
        self.view_custom = None         # облёт вида Custom: {"yaw", "pitch", "dist", "target"}
        self.view_ortho = None          # ортогональные виды: {"top": {"scale", "target"}, …}
        self._project = None

    def find_comp(self, cid):
        """Вложенная композиция по id (для слоёв-прекомпозиций)."""
        return self._project.comp_by_id(cid) if self._project is not None else None

    def layer_by_id(self, lid) -> Layer | None:
        for l in self.layers:
            if l.id == lid:
                return l
        return None

    def to_dict(self):
        d = {k: v for k, v in self.__dict__.items() if k != "layers" and not k.startswith("_")}
        d["layers"] = [l.to_dict() for l in self.layers]
        return d

    def active_camera(self, frame: float) -> Layer | None:
        """Верхняя включённая камера на этом кадре (как в AE)."""
        for l in self.layers:
            if l.kind == "camera" and l.visible and l.in_point <= frame < l.out_point:
                return l
        return None

    @classmethod
    def from_dict(cls, d):
        c = cls()
        for k, v in d.items():
            if k != "layers":
                setattr(c, k, v)
        c.layers = [Layer.from_dict(ld, c) for ld in d["layers"]]
        return c


class Project:
    """Проект: медиа и несколько композиций (прекомпозиции — слои, ссылающиеся на другую композицию)."""

    def __init__(self, comps=None, media=None, active=None):
        self.comps: list[Composition] = comps or [Composition()]
        self.media: list[str] = list(media or [])
        self.active = active if active and any(c.id == active for c in self.comps) else self.comps[0].id
        for c in self.comps:
            c._project = self

    @property
    def comp(self) -> Composition:
        return self.comp_by_id(self.active) or self.comps[0]

    @comp.setter
    def comp(self, c: Composition):
        self.comps = [c if x.id == c.id else x for x in self.comps]
        if c not in self.comps:
            self.comps.append(c)
        c._project = self
        self.active = c.id

    def comp_by_id(self, cid) -> Composition | None:
        return next((c for c in self.comps if c.id == cid), None)

    def add_comp(self, c: Composition):
        c._project = self
        self.comps.append(c)
        return c

    def uses(self, outer: Composition, inner_id: str) -> bool:
        """Есть ли (в том числе глубже) внутри outer ссылка на композицию inner_id — защита от циклов."""
        seen = set()

        def walk(c):
            if c is None or c.id in seen:
                return False
            seen.add(c.id)
            for l in c.layers:
                if l.kind == "comp":
                    if l.source == inner_id or walk(self.comp_by_id(l.source)):
                        return True
            return False
        return outer.id == inner_id or walk(outer)

    def comps_dict(self) -> dict:
        return {"comps": [c.to_dict() for c in self.comps], "active": self.active}

    def load_comps(self, d: dict):
        self.comps = [Composition.from_dict(cd) for cd in d["comps"]]
        for c in self.comps:
            c._project = self
        self.active = d.get("active") if any(c.id == d.get("active") for c in self.comps) else self.comps[0].id

    def to_dict(self):
        return {"version": 2, "media": self.media, **self.comps_dict()}

    @classmethod
    def from_dict(cls, d):
        if "comps" in d:
            comps = [Composition.from_dict(cd) for cd in d["comps"]]
            return cls(comps, d.get("media", []), d.get("active"))
        return cls([Composition.from_dict(d["comp"])], d.get("media", []))  # проекты версии 1


def timecode(frame: int, fps: float) -> str:
    """0:00:00:00 как в AE."""
    fps_i = max(1, round(fps))
    sign = "-" if frame < 0 else ""
    frame = abs(int(frame))
    ff = frame % fps_i
    total = frame // fps_i
    return f"{sign}{total // 3600}:{(total // 60) % 60:02d}:{total % 60:02d}:{ff:02d}"
