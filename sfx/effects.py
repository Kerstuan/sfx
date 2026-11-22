# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Эффекты слоёв: реестр (параметры, подписи) и обработка кадра через numpy.

Кадр — массив float32 (H, W, 4), каналы B, G, R, A (так лежит QImage ARGB32 в памяти),
цвет premultiplied, диапазон 0..255. Все размеры в параметрах — в пикселях композиции,
при превью в пониженном разрешении умножаются на scale.

Анимируемые параметры живут в layer.props под ключом "fx/<id эффекта>/<параметр>",
поэтому ключи, граф-эдитор, выделение и undo работают для них так же, как для трансформаций.
Неанимируемые настройки (цвет, сид) лежат в effect["opts"].
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QTransform

from .i18n import LANG


@dataclass
class Param:
    key: str
    ru: str
    en: str
    default: list[float]
    step: float = 0.5
    suffix: str = ""
    lo: float | None = None
    hi: float | None = None
    kind: str = "float"            # float | angle | checkbox | choice | color | point
    choices: list | None = None    # для choice: [(ru, en), ...]
    group: tuple | None = None     # (ru, en) — подгруппа в Effect Controls, как «Style» у Deep Glow

    @property
    def label(self) -> str:
        return self.en if LANG == "en" else self.ru

    def choice_labels(self) -> list[str]:
        return [en if LANG == "en" else ru for ru, en in (self.choices or [])]

    @property
    def group_label(self) -> str | None:
        if not self.group:
            return None
        return self.group[1] if LANG == "en" else self.group[0]


CATEGORIES = {
    "blur": ("Размытие и резкость", "Blur & Sharpen"),
    "stylize": ("Стилизация", "Stylize"),
    "color": ("Цветокоррекция", "Color Correction"),
    "distort": ("Искажение", "Distort"),
    "generate": ("Генерация", "Generate"),
    "keying": ("Кеинг", "Keying"),
    "sapphire": ("В духе Sapphire", "Sapphire-style"),
    "control": ("Управление выражениями", "Expression Controls"),
    "audio": ("Звук", "Audio"),
}


def category_label(cat: str) -> str:
    ru, en = CATEGORIES.get(cat, (cat, cat))
    return en if LANG == "en" else ru


@dataclass
class EffectDef:
    type: str
    ru: str
    en: str
    params: list[Param]
    opts: dict = field(default_factory=dict)  # неанимируемые настройки по умолчанию
    transform: bool = False                   # меняет трансформацию слоя, а не пиксели
    category: str = "stylize"
    audio: bool = False                       # звуковой эффект (обрабатывается ffmpeg при сведении)
    control: bool = False                     # контрол для выражений: на картинку не влияет
    transition: bool = False                  # переход на стыке двух клипов (рисует render, см. transitions.py)
    time: bool = False                        # эффект времени: слой рисуется в другие моменты (fx_extra.py)

    @property
    def label(self) -> str:
        return self.en if LANG == "en" else self.ru


STYLE = ("Стиль", "Style")
# номера 0–5 не меняем (на них ссылаются пресеты и сохранённые проекты), новые стили — в конце
SHAKE_MODES = [("Органичная", "Organic"), ("С рук", "Handheld"), ("Удар", "Impact"), ("Дрожь", "Jitter"),
               ("Землетрясение", "Earthquake"), ("По направлению", "Directional"), ("Лёгкая", "Subtle"),
               ("Кинематографичная", "Cinematic"), ("Сильная", "Heavy")]
BAND = lambda n: (f"Полоса {n}", f"Band {n}")  # noqa: E731

EFFECTS: dict[str, EffectDef] = {e.type: e for e in [
    # --- размытие и резкость
    EffectDef("blur", "Размытие по Гауссу", "Gaussian Blur", [
        Param("radius", "Размытие", "Blurriness", [10.0], 0.5, " px", 0, 1000)], category="blur"),
    EffectDef("dblur", "Направленное размытие", "Directional Blur", [
        Param("angle", "Направление", "Direction", [0.0], 0.5, "°", kind="angle"),
        Param("length", "Длина", "Blur Length", [40.0], 0.5, " px", 0, 2000)], category="blur"),
    EffectDef("sharpen", "Нерезкая маска", "Unsharp Mask", [
        Param("amount", "Сила", "Amount", [100.0], 0.5, "%", 0, 500),
        Param("radius", "Радиус", "Radius", [3.0], 0.1, " px", 0, 100),
        Param("threshold", "Порог", "Threshold", [0.0], 0.5, "", 0, 255)], category="blur"),
    # --- стилизация
    EffectDef("glow", "Свечение", "Glow", [
        Param("threshold", "Порог", "Threshold", [60.0], 0.5, "%", 0, 100),
        Param("radius", "Радиус", "Radius", [30.0], 0.5, " px", 0, 1000),
        Param("intensity", "Интенсивность", "Intensity", [100.0], 0.5, "%", 0, 1000),
        Param("blend", "Режим наложения", "Blend Mode", [0.0], kind="choice",
              choices=[("Добавить", "Add"), ("Экран", "Screen")], group=STYLE),
        Param("colors", "Цвета", "Glow Colors", [0.0], kind="choice",
              choices=[("Исходные", "Original Colors"), ("Тонировка", "Tint")], group=STYLE),
        Param("tint", "Цвет тонировки", "Tint Color", [255.0, 170.0, 60.0], kind="color", group=STYLE),
        Param("chroma", "Хроматическая аберрация", "Chromatic Aberration", [0.0], 0.2, " px", 0, 200, group=STYLE)]),
    EffectDef("chroma", "Хроматическая аберрация", "Chromatic Aberration", [
        Param("amount", "Смещение", "Offset", [8.0], 0.2, " px", 0, 500),
        Param("angle", "Направление", "Direction", [0.0], 0.5, "°", kind="angle")]),
    EffectDef("mosaic", "Мозаика", "Mosaic", [
        Param("h", "Блоков по горизонтали", "Horizontal Blocks", [20.0], 0.5, "", 1, 4000),
        Param("v", "Блоков по вертикали", "Vertical Blocks", [12.0], 0.5, "", 1, 4000)]),
    EffectDef("vignette", "Виньетка", "Vignette", [
        Param("amount", "Сила", "Amount", [60.0], 0.5, "%", 0, 100),
        Param("size", "Размер", "Size", [70.0], 0.5, "%", 0, 200),
        Param("softness", "Мягкость", "Softness", [50.0], 0.5, "%", 1, 100)]),
    # --- искажение
    EffectDef("shake", "Тряска (S_Shake)", "Shake", [
        Param("mode", "Стиль", "Style", [0.0], kind="choice", choices=SHAKE_MODES),
        Param("master", "Сила тряски", "Shake Amount", [1.0], 0.01, "", 0, 20),
        Param("amount", "Амплитуда сдвига", "Amplitude", [30.0], 0.5, " px", 0, 5000),
        Param("xmul", "Сдвиг по X", "Position X", [100.0], 0.5, "%", 0, 500),
        Param("ymul", "Сдвиг по Y", "Position Y", [100.0], 0.5, "%", 0, 500),
        Param("rot", "Поворот", "Rotation", [2.0], 0.05, "°", 0, 360),
        Param("zoom", "Зум (колебание)", "Zoom", [0.0], 0.05, "%", 0, 200),
        Param("freq", "Частота движения", "Motion Frequency", [4.0], 0.05, " Hz", 0, 120),
        Param("smooth", "Плавность", "Smoothness", [0.6], 0.01, "", 0, 1),
        Param("random", "Случайность", "Randomness", [0.5], 0.01, "", 0, 1),
        Param("mblur", "Размытие в движении", "Motion Blur", [1.0], kind="checkbox"),
        Param("zcomp", "Компенсация зума (против краёв)", "Zoom Compensation", [0.0], 0.05, "%", 0, 200),
        Param("dir", "Направление (для «По направлению»)", "Direction", [0.0], 0.5, "°", kind="angle", group=("Тонкая настройка", "Advanced")),
        Param("detail", "Детализация (октавы)", "Detail", [3.0], 0.05, "", 1, 6, group=("Тонкая настройка", "Advanced"))],
        opts={"seed": 1}, transform=True, category="distort"),
    EffectDef("threshold", "Порог", "Threshold", [
        Param("level", "Уровень", "Level", [128.0], 0.5, "", 0, 255)], category="stylize"),
    EffectDef("posterize", "Постеризация", "Posterize", [
        Param("levels", "Уровни", "Level", [6.0], 0.05, "", 2, 64)], category="stylize"),
    EffectDef("findedges", "Выделение краёв", "Find Edges", [
        Param("invert", "Инвертировать", "Invert", [0.0], kind="checkbox"),
        Param("blend", "Смешать с исходным", "Blend With Original", [0.0], 0.5, "%", 0, 100)], category="stylize"),
    EffectDef("emboss", "Тиснение", "Emboss", [
        Param("dir", "Направление", "Direction", [45.0], 0.5, "°", kind="angle"),
        Param("relief", "Рельеф", "Relief", [2.0], 0.05, "", 0.5, 10),
        Param("contrast", "Контраст", "Contrast", [100.0], 0.5, "%", 0, 500),
        Param("blend", "Смешать с исходным", "Blend With Original", [0.0], 0.5, "%", 0, 100)], category="stylize"),
    EffectDef("glitch", "Цифровой сбой", "Glitch", [
        Param("amount", "Сила", "Amount", [30.0], 0.5, "%", 0, 100),
        Param("block", "Высота полос", "Block Size", [24.0], 0.5, " px", 2, 300),
        Param("rgb", "Разнос RGB", "RGB Split", [12.0], 0.5, " px", 0, 200),
        Param("speed", "Смена каждые N кадров", "Change Every N Frames", [2.0], 0.05, "", 1, 30)],
        opts={"seed": 1}, category="stylize"),
    EffectDef("mirror", "Зеркало", "Mirror", [
        Param("mode", "Отразить", "Reflect", [0.0], kind="choice",
              choices=[("Левую половину вправо", "Left to Right"), ("Правую влево", "Right to Left"),
                       ("Верх вниз", "Top to Bottom"), ("Низ вверх", "Bottom to Top")]),
        Param("center", "Центр", "Reflection Center", [0.5, 0.5], 1.0, kind="point")], category="distort"),
    EffectDef("wiggle", "Тряска (простая)", "Wiggle Transform", [
        Param("px", "Сдвиг по X", "Position X", [0.0], 0.5, " px", 0, 2000),
        Param("py", "Сдвиг по Y", "Position Y", [0.0], 0.5, " px", 0, 2000),
        Param("pz", "Сдвиг по Z", "Position Z", [0.0], 0.5, " px", 0, 900),
        Param("rot", "Поворот", "Rotation", [0.0], 0.1, "°", 0, 180),
        Param("scale", "Масштаб", "Scale", [0.0], 0.1, "%", 0, 100),
        Param("freq", "Частота", "Frequency", [2.0], 0.05, " Hz", 0, 60),
        Param("detail", "Детализация", "Detail", [1.0], 0.05, "", 1, 6)],
        opts={"seed": 1}, transform=True, category="distort"),
    # --- в духе Sapphire (S_BlurMoCurves, S_WarpPuddle, S_DistortChroma, S_RandomEdits)
    EffectDef("blurmo", "Влёт с размытием (BlurMo)", "Blur Motion Transform", [
        Param("zdist", "Приближение Z", "Z Dist", [1.0], 0.005, "", 0.05, 20),
        Param("rotate", "Поворот", "Rotate", [0.0], 0.5, "°", kind="angle"),
        Param("shiftx", "Сдвиг X", "Shift X", [0.0], 1.0, " px", -5000, 5000),
        Param("shifty", "Сдвиг Y", "Shift Y", [0.0], 1.0, " px", -5000, 5000),
        Param("center", "Центр", "Center XY", [0.5, 0.5], 1.0, kind="point"),
        Param("shutter", "Длительность затвора", "Shutter Duration", [1.0], 0.01, "", 0, 4),
        Param("samples", "Качество (сэмплы)", "Samples", [10.0], 0.1, "", 2, 32),
        Param("bright", "Яркость", "Brightness", [1.0], 0.01, "", 0, 4)], category="sapphire"),
    EffectDef("puddle", "Рябь-лужа (WarpPuddle)", "Warp Puddle", [
        Param("amp", "Амплитуда", "Amplitude", [0.13], 0.005, "", 0, 5),
        Param("freq", "Частота", "Frequency", [1.2], 0.01, "", 0, 50),
        Param("center", "Центр", "Center XY", [0.5, 0.5], 1.0, kind="point"),
        Param("phase", "Фаза", "Phase Start", [0.0], 0.01, "", -100, 100),
        Param("speed", "Скорость фазы", "Phase Speed", [1.0], 0.01, "", -50, 50),
        Param("inner", "Внутренний радиус", "Inner Radius", [0.0], 1.0, " px", 0, 10000),
        Param("outer", "Внешний радиус", "Outer Radius", [1500.0], 1.0, " px", 0, 10000),
        Param("soft", "Мягкость края", "Outer Softness", [400.0], 1.0, " px", 0, 10000),
        Param("chroma", "Разнос цвета", "Chroma", [0.0], 0.01, "", 0, 2)], category="sapphire"),
    EffectDef("distchroma", "Цветная деформация (DistortChroma)", "Distort Chroma", [
        Param("amount", "Сила", "Amount", [1.2], 0.01, "", 0, 20),
        Param("lens", "Размытие линзы", "Blur Lens", [60.0], 0.5, " px", 0, 2000),
        Param("rot", "Поворот деформации", "Rotate Warp Dir", [0.0], 0.5, "°", kind="angle"),
        Param("wr", "Деформация красного", "Warp Red", [0.5], 0.01, "", -4, 4),
        Param("wb", "Деформация синего", "Warp Blue", [1.0], 0.01, "", -4, 4),
        Param("steps", "Шаги", "Steps", [8.0], 0.1, "", 1, 32)], category="sapphire"),
    EffectDef("randedits", "Случайные склейки (RandomEdits)", "Random Edits", [
        Param("len", "Длина куска (кадры)", "Edit Frame Length", [6.0], 0.1, "", 1, 600),
        Param("jump", "Макс. прыжок (сек)", "Max Jump", [2.0], 0.05, " s", 0, 600),
        Param("seed", "Случайность", "Seed", [0.123], 0.01, "", 0, 1000)], transform=True, category="sapphire"),
    # --- Roto Brush (маску считает SAM 2.1 в отдельном процессе; здесь — только уточнение края)
    EffectDef("roto", "Roto Brush", "Roto Brush", [
        Param("show", "Показать", "View", [0.0], kind="choice",
              choices=[("Результат", "Result"), ("Маска", "Alpha"), ("Наложение", "Overlay")]),
        Param("refine", "Уточнение края", "Refine Edge Radius", [4.0], 0.1, " px", 0, 100),
        Param("detail", "Детализация края (волосы)", "Edge Detail", [0.5], 0.01, "", 0, 1),
        Param("feather", "Растушёвка", "Feather", [0.0], 0.1, " px", 0, 200),
        Param("choke", "Сжатие", "Choke", [0.0], 0.1, "%", -100, 100),
        Param("shift", "Сдвиг края", "Shift Edge", [0.0], 0.1, "%", -100, 100),
        Param("contrast", "Контраст края", "Contrast", [0.0], 0.1, "%", 0, 100),
        Param("invert", "Инвертировать", "Invert", [0.0], kind="checkbox")],
        opts={"session": ""}, transform=True, category="keying"),
    # --- генерация
    EffectDef("flash", "Вспышка", "Flash", [
        Param("amount", "Сила", "Amount", [0.0], 0.5, "%", 0, 100)],
        opts={"color": "#ffffff"}, category="generate"),
    EffectDef("fill", "Заливка", "Fill", [
        Param("amount", "Непрозрачность", "Opacity", [100.0], 0.5, "%", 0, 100)],
        opts={"color": "#ff3030"}, category="generate"),
    EffectDef("ramp", "Градиент", "Gradient Ramp", [
        Param("start", "Начало градиента", "Start of Ramp", [0.5, 0.0], 1.0, kind="point"),
        Param("c0", "Начальный цвет", "Start Color", [0.0, 0.0, 0.0], kind="color"),
        Param("end", "Конец градиента", "End of Ramp", [0.5, 1.0], 1.0, kind="point"),
        Param("c1", "Конечный цвет", "End Color", [255.0, 255.0, 255.0], kind="color"),
        Param("shape", "Форма", "Ramp Shape", [0.0], kind="choice",
              choices=[("Линейная", "Linear Ramp"), ("Радиальная", "Radial Ramp")]),
        Param("scatter", "Рассеивание", "Ramp Scatter", [0.0], 0.5, "", 0, 100),
        Param("blend", "Смешать с исходным", "Blend With Original", [0.0], 0.5, "%", 0, 100)],
        category="generate"),
    # --- кеинг
    EffectDef("keylight", "Keylight", "Keylight", [
        Param("view", "Вид", "View", [0.0], kind="choice",
              choices=[("Итог", "Final Result"), ("Маска (ч/б)", "Screen Matte"), ("Исходник", "Source")]),
        Param("screen", "Цвет фона", "Screen Colour", [40.0, 190.0, 60.0], kind="color"),
        Param("gain", "Усиление", "Screen Gain", [100.0], 0.5, "", 0, 200),
        Param("despill", "Подавление отсвета", "Despill", [100.0], 0.5, "%", 0, 100),
        Param("clip_black", "Отсечь чёрное", "Clip Black", [0.0], 0.2, "", 0, 100, group=("Маска", "Screen Matte")),
        Param("clip_white", "Отсечь белое", "Clip White", [100.0], 0.2, "", 0, 100, group=("Маска", "Screen Matte")),
        Param("shrink", "Сжать / расширить", "Screen Shrink/Grow", [0.0], 0.1, " px", -20, 20,
              group=("Маска", "Screen Matte")),
        Param("soft", "Мягкость края", "Screen Softness", [0.0], 0.1, " px", 0, 50, group=("Маска", "Screen Matte"))],
        category="keying"),
    # --- цвет
    EffectDef("bc", "Яркость и контраст", "Brightness & Contrast", [
        Param("brightness", "Яркость", "Brightness", [0.0], 0.5, "", -150, 150),
        Param("contrast", "Контраст", "Contrast", [0.0], 0.5, "", -100, 100)], category="color"),
    EffectDef("hsl", "Цветовой тон и насыщенность", "Hue/Saturation", [
        Param("hue", "Цветовой тон", "Master Hue", [0.0], 0.5, "°", kind="angle"),
        Param("sat", "Насыщенность", "Master Saturation", [0.0], 0.5, "", -100, 100),
        Param("light", "Яркость", "Master Lightness", [0.0], 0.5, "", -100, 100),
        Param("colorize", "Тонировать", "Colorize", [0.0], kind="checkbox", group=("Тонирование", "Colorize")),
        Param("col_hue", "Тон", "Colorize Hue", [0.0], 0.5, "°", kind="angle", group=("Тонирование", "Colorize")),
        Param("col_sat", "Насыщенность", "Colorize Saturation", [25.0], 0.5, "", 0, 100,
              group=("Тонирование", "Colorize"))], category="color"),
    EffectDef("tint", "Тонирование", "Tint", [
        Param("black", "Чёрный в", "Map Black To", [0.0, 0.0, 0.0], kind="color"),
        Param("white", "Белый в", "Map White To", [255.0, 255.0, 255.0], kind="color"),
        Param("amount", "Сила", "Amount to Tint", [100.0], 0.5, "%", 0, 100)], category="color"),
    EffectDef("levels", "Уровни", "Levels", [
        Param("in_black", "Вход: чёрное", "Input Black", [0.0], 0.5, "", 0, 255),
        Param("in_white", "Вход: белое", "Input White", [255.0], 0.5, "", 0, 255),
        Param("gamma", "Гамма", "Gamma", [1.0], 0.01, "", 0.1, 10),
        Param("out_black", "Выход: чёрное", "Output Black", [0.0], 0.5, "", 0, 255),
        Param("out_white", "Выход: белое", "Output White", [255.0], 0.5, "", 0, 255)], category="color"),
    EffectDef("exposure", "Экспозиция", "Exposure", [
        Param("exposure", "Экспозиция", "Exposure", [0.0], 0.01, "", -5, 5),
        Param("offset", "Сдвиг", "Offset", [0.0], 0.002, "", -0.5, 0.5),
        Param("gamma", "Гамма-коррекция", "Gamma Correction", [1.0], 0.01, "", 0.1, 10)], category="color"),
    EffectDef("bw", "Чёрно-белое", "Black & White", [
        Param("amount", "Сила", "Amount", [100.0], 0.5, "%", 0, 100),
        Param("reds", "Красные", "Reds", [40.0], 0.5, "%", -200, 300),
        Param("greens", "Зелёные", "Greens", [40.0], 0.5, "%", -200, 300),
        Param("blues", "Синие", "Blues", [20.0], 0.5, "%", -200, 300)], category="color"),
    EffectDef("vibrance", "Сочность", "Vibrance", [
        Param("vibrance", "Сочность", "Vibrance", [30.0], 0.5, "", -100, 100),
        Param("sat", "Насыщенность", "Saturation", [0.0], 0.5, "", -100, 100)], category="color"),
    EffectDef("photofilter", "Фотофильтр", "Photo Filter", [
        Param("color", "Цвет", "Color", [236.0, 138.0, 0.0], kind="color"),
        Param("density", "Плотность", "Density", [25.0], 0.5, "%", 0, 100),
        Param("keep", "Сохранять яркость", "Preserve Luminosity", [1.0], kind="checkbox")], category="color"),
    EffectDef("colorbal", "Цветовой баланс", "Color Balance", [
        Param("s_r", "Голубой – красный", "Shadow Red Balance", [0.0], 0.5, "", -100, 100, group=("Тени", "Shadows")),
        Param("s_g", "Пурпурный – зелёный", "Shadow Green Balance", [0.0], 0.5, "", -100, 100, group=("Тени", "Shadows")),
        Param("s_b", "Жёлтый – синий", "Shadow Blue Balance", [0.0], 0.5, "", -100, 100, group=("Тени", "Shadows")),
        Param("m_r", "Голубой – красный", "Midtone Red Balance", [0.0], 0.5, "", -100, 100, group=("Средние тона", "Midtones")),
        Param("m_g", "Пурпурный – зелёный", "Midtone Green Balance", [0.0], 0.5, "", -100, 100, group=("Средние тона", "Midtones")),
        Param("m_b", "Жёлтый – синий", "Midtone Blue Balance", [0.0], 0.5, "", -100, 100, group=("Средние тона", "Midtones")),
        Param("h_r", "Голубой – красный", "Highlight Red Balance", [0.0], 0.5, "", -100, 100, group=("Света", "Highlights")),
        Param("h_g", "Пурпурный – зелёный", "Highlight Green Balance", [0.0], 0.5, "", -100, 100, group=("Света", "Highlights")),
        Param("h_b", "Жёлтый – синий", "Highlight Blue Balance", [0.0], 0.5, "", -100, 100, group=("Света", "Highlights"))],
        category="color"),
    EffectDef("curves", "Кривые", "Curves", [
        Param("channel", "Канал", "Channel", [0.0], kind="choice",
              choices=[("RGB", "RGB"), ("Красный", "Red"), ("Зелёный", "Green"), ("Синий", "Blue")])],
        opts={"rgb": [[0.0, 0.0], [1.0, 1.0]], "r": [[0.0, 0.0], [1.0, 1.0]],
              "g": [[0.0, 0.0], [1.0, 1.0]], "b": [[0.0, 0.0], [1.0, 1.0]]}, category="color"),
    EffectDef("noise", "Шум (зерно)", "Noise", [
        Param("amount", "Количество шума", "Amount of Noise", [8.0], 0.1, "%", 0, 100),
        Param("color", "Цветной шум", "Use Color Noise", [0.0], kind="checkbox"),
        Param("size", "Размер зерна", "Grain Size", [1.0], 0.02, " px", 1, 8)], category="stylize"),
    EffectDef("invert", "Инверсия", "Invert", [
        Param("channel", "Канал", "Channel", [0.0], kind="choice",
              choices=[("RGB", "RGB"), ("Яркость", "Luminance")]),
        Param("blend", "Смешать с оригиналом", "Blend With Original", [0.0], 0.5, "%", 0, 100)], category="color"),
    # --- контролы для выражений (как в AE)
    EffectDef("slider", "Слайдер", "Slider Control", [
        Param("slider", "Слайдер", "Slider", [0.0], 0.5, "")], category="control", control=True),
    EffectDef("anglec", "Угол", "Angle Control", [
        Param("angle", "Угол", "Angle", [0.0], 0.5, "°", kind="angle")], category="control", control=True),
    EffectDef("checkc", "Флажок", "Checkbox Control", [
        Param("checkbox", "Флажок", "Checkbox", [0.0], kind="checkbox")], category="control", control=True),
    EffectDef("colorc", "Цвет", "Color Control", [
        Param("color", "Цвет", "Color", [255.0, 0.0, 0.0], kind="color")], category="control", control=True),
    EffectDef("pointc", "Точка", "Point Control", [
        Param("point", "Точка", "Point", [960.0, 540.0], 1.0, kind="point")], category="control", control=True),
    # --- звук (сводится через ffmpeg; параметры берутся на начале слоя)
    EffectDef("eq", "Параметрический эквалайзер", "Parametric EQ", [
        Param("low_f", "Низкие: частота", "Low Shelf Frequency", [100.0], 1.0, " Hz", 20, 20000, group=BAND(1)),
        Param("low_g", "Низкие: усиление", "Low Shelf Gain", [0.0], 0.1, " dB", -24, 24, group=BAND(1)),
        Param("m1_f", "Частота", "Frequency", [400.0], 1.0, " Hz", 20, 20000, group=BAND(2)),
        Param("m1_g", "Усиление", "Gain", [0.0], 0.1, " dB", -24, 24, group=BAND(2)),
        Param("m1_q", "Добротность", "Q", [1.0], 0.01, "", 0.1, 20, group=BAND(2)),
        Param("m2_f", "Частота", "Frequency", [1500.0], 1.0, " Hz", 20, 20000, group=BAND(3)),
        Param("m2_g", "Усиление", "Gain", [0.0], 0.1, " dB", -24, 24, group=BAND(3)),
        Param("m2_q", "Добротность", "Q", [1.0], 0.01, "", 0.1, 20, group=BAND(3)),
        Param("m3_f", "Частота", "Frequency", [5000.0], 1.0, " Hz", 20, 20000, group=BAND(4)),
        Param("m3_g", "Усиление", "Gain", [0.0], 0.1, " dB", -24, 24, group=BAND(4)),
        Param("m3_q", "Добротность", "Q", [1.0], 0.01, "", 0.1, 20, group=BAND(4)),
        Param("high_f", "Высокие: частота", "High Shelf Frequency", [10000.0], 1.0, " Hz", 20, 20000, group=BAND(5)),
        Param("high_g", "Высокие: усиление", "High Shelf Gain", [0.0], 0.1, " dB", -24, 24, group=BAND(5))],
        category="audio", audio=True),
    EffectDef("passf", "Фильтр высоких/низких частот", "High-Low Pass", [
        Param("mode", "Тип", "Filter Options", [0.0], kind="choice",
              choices=[("Срез низких (High Pass)", "High Pass"), ("Срез высоких (Low Pass)", "Low Pass")]),
        Param("freq", "Частота среза", "Cutoff Frequency", [200.0], 1.0, " Hz", 20, 20000)],
        category="audio", audio=True),
    EffectDef("basstreble", "Басы и верха", "Bass & Treble", [
        Param("bass", "Басы", "Bass", [0.0], 0.1, " dB", -24, 24),
        Param("treble", "Верха", "Treble", [0.0], 0.1, " dB", -24, 24)], category="audio", audio=True),
    EffectDef("comp", "Компрессор", "Compressor", [
        Param("threshold", "Порог", "Threshold", [-18.0], 0.1, " dB", -60, 0),
        Param("ratio", "Степень", "Ratio", [4.0], 0.05, ":1", 1, 20),
        Param("attack", "Атака", "Attack", [20.0], 0.5, " ms", 0.01, 2000),
        Param("release", "Восстановление", "Release", [250.0], 1.0, " ms", 0.01, 9000),
        Param("makeup", "Компенсация", "Makeup Gain", [0.0], 0.1, " dB", 0, 36)], category="audio", audio=True),
    EffectDef("reverb", "Реверберация", "Reverb", [
        Param("size", "Размер помещения", "Room Size", [50.0], 0.5, "%", 1, 100),
        Param("decay", "Затухание", "Decay", [50.0], 0.5, "%", 1, 95),
        Param("mix", "Сухой/обработанный", "Dry/Wet Mix", [30.0], 0.5, "%", 0, 100)], category="audio", audio=True),
    EffectDef("delay", "Задержка (эхо)", "Delay", [
        Param("time", "Время", "Delay Time", [300.0], 1.0, " ms", 1, 5000),
        Param("feedback", "Обратная связь", "Feedback", [40.0], 0.5, "%", 0, 95),
        Param("mix", "Сухой/обработанный", "Dry/Wet Mix", [40.0], 0.5, "%", 0, 100)], category="audio", audio=True),
    EffectDef("pitch", "Высота тона", "Pitch Shifter", [
        Param("semitones", "Полутоны", "Semitones", [0.0], 0.05, "", -24, 24)], category="audio", audio=True),
    EffectDef("denoise", "Шумоподавление", "Noise Reduction", [
        Param("amount", "Сила", "Amount", [12.0], 0.2, " dB", 0.01, 97)], category="audio", audio=True),
    EffectDef("balance", "Баланс", "Stereo Balance", [
        Param("balance", "Баланс", "Balance", [0.0], 0.5, "", -100, 100)], category="audio", audio=True),
]}


# «Параметры наложения» (Compositing Options в AE): сколько эффекта примешивать к исходной картинке
COMPOSITING = ("Параметры наложения", "Compositing Options")
for _d in EFFECTS.values():
    if not (_d.audio or _d.control or _d.transform) and not any(p.key == "mix" for p in _d.params):
        _d.params.append(Param("mix", "Непрозрачность эффекта", "Effect Opacity", [100.0], 0.5, "%", 0, 100,
                               group=COMPOSITING))


def effect_for_layer(fx_type: str, layer_kind: str) -> bool:
    """Звуковые эффекты — только слоям со звуком; картинку — всем, кроме звуковых."""
    d = EFFECTS.get(fx_type)
    if d is None:
        return False
    if d.audio:
        return layer_kind in ("video", "audio", "comp")
    return layer_kind not in ("audio", "camera", "light")


def prop_key(fx_id: str, param: str) -> str:
    return f"fx/{fx_id}/{param}"


def split_key(key: str):
    """'fx/<id>/<param>' -> (id, param), иначе None."""
    if not key.startswith("fx/"):
        return None
    _, fid, param = key.split("/", 2)
    return fid, param


def find_param(layer, key) -> tuple[dict, EffectDef, Param] | None:
    sp = split_key(key)
    if not sp:
        return None
    fid, pname = sp
    for fx in layer.effects:
        if fx["id"] == fid:
            d = EFFECTS.get(fx["type"])
            if d:
                for p in d.params:
                    if p.key == pname:
                        return fx, d, p
    return None


def values(layer, fx, local_frame: float) -> dict:
    """Значения параметров эффекта: число для 1D, список для цвета/точки; флажки и списки — целые."""
    d = EFFECTS[fx["type"]]
    out = {}
    for p in d.params:
        prop = layer.props.get(prop_key(fx["id"], p.key))
        v = prop.value_at(local_frame) if prop else list(p.default)
        if p.kind in ("checkbox", "choice"):
            out[p.key] = int(round(v[0]))
        elif len(p.default) == 1:
            out[p.key] = v[0]
        else:
            out[p.key] = v
    return out


def active_effects(layer):
    if not getattr(layer, "fx_on", True):
        return []
    return [fx for fx in layer.effects if fx.get("on", True) and fx["type"] in EFFECTS]


def visual_effects(layer):
    return [fx for fx in active_effects(layer) if not EFFECTS[fx["type"]].audio and not EFFECTS[fx["type"]].control]


def effect_display_name(fx) -> str:
    d = EFFECTS.get(fx["type"])
    return fx.get("name") or (d.label if d else fx["type"])


# ============================================================================ тряска
def _hash(i: int, seed: float) -> float:
    x = math.sin(i * 127.1 + seed * 311.7) * 43758.5453
    return (x - math.floor(x)) * 2 - 1


def _vnoise(x: float, seed: float, smooth: bool = True) -> float:
    """Шум значений в [-1, 1]: случайные точки на целых x, между ними — плавно (или линейно для ударов)."""
    i = math.floor(x)
    f = x - i
    u = f * f * (3 - 2 * f) if smooth else f
    return _hash(i, seed) * (1 - u) + _hash(i + 1, seed) * u


def _fbm(x: float, seed: float, octaves: float, smooth: bool = True) -> float:
    """Несколько октав шума — чем больше «детализация», тем больше мелкой дрожи поверх крупных движений."""
    total, amp, norm, fr = 0.0, 1.0, 0.0, 1.0
    n = max(1, int(math.ceil(octaves)))
    for o in range(n):
        w = amp * (1.0 if o < int(octaves) or octaves == int(octaves) else octaves - int(octaves))
        total += _vnoise(x * fr, seed + o * 7.31, smooth) * w
        norm += w
        amp *= 0.5
        fr *= 2.1
    # нормируем по энергии, а не по сумме весов: иначе пики доходят лишь до ~70% «Силы»
    norm2 = math.sqrt(sum((0.5 ** o) ** 2 for o in range(n)))
    return max(-1.0, min(1.0, total / max(norm2, 1e-6) * 0.8))


def _noise(x: float, seed: float) -> float:   # совместимость со старыми вызовами
    return _fbm(x, seed, 2)


def _perlin1(x: float, seed: float) -> float:
    """1D-шум Перлина в [-1, 1]: случайные наклоны в целых точках, квинтическое сглаживание —
    непрерывен вместе с первой и второй производной (нет рывков скорости)."""
    i = math.floor(x)
    f = x - i
    g0, g1 = _hash(i, seed), _hash(i + 1, seed)
    u = f * f * f * (f * (f * 6 - 15) + 10)
    return (g0 * f + (g1 * (f - 1) - g0 * f) * u) * 2.0


def _shake_fbm(t: float, seed: float, octaves: float, smooth: float) -> float:
    """Фрактальный шум для тряски. Smoothness убирает вес верхних октав (меньше мелкой дрожи)."""
    pers = 0.65 - 0.45 * smooth            # вклад следующей октавы
    total, amp, norm = 0.0, 1.0, 0.0
    n = max(1, int(math.ceil(octaves)))
    for o in range(n):
        w = amp * (1.0 if o < int(octaves) or octaves == int(octaves) else octaves - int(octaves))
        total += _perlin1(t * (2.03 ** o), seed + o * 7.31) * w
        norm += w * w
        amp *= pers
    return max(-1.0, min(1.0, total / max(1e-6, math.sqrt(norm)) * 0.9))


# стиль: (сдвиг ×, поворот ×, частота ×, октавы +, плавность +, Y ×)
_SHAKE_STYLE = {0: (0.8, 0.8, 0.25, 0, 0.3, 1.0),   # органичная: медленно и мягко
                1: (1.0, 1.2, 0.45, 1, 0.1, 1.2),   # с рук: покачивание + немного дрожи
                2: (1.5, 1.5, 1.6, 1, -0.4, 1.0),   # удар: резкие толчки с затуханием
                3: (0.4, 0.4, 4.0, 0, -0.6, 1.0),   # дрожь: мелко и часто
                4: (2.5, 1.5, 1.6, 2, -0.3, 1.5),   # землетрясение: крупно + хаос
                5: (1.0, 0.0, 1.0, 0, 0.0, 1.0),    # по направлению
                6: (0.35, 0.4, 0.6, 0, 0.2, 1.0),   # лёгкая
                7: (0.7, 0.6, 0.3, 0, 0.35, 0.9),   # кинематографичная: медленный «вес» камеры
                8: (2.0, 2.0, 1.0, 1, -0.2, 1.1)}   # сильная


def shake_values(v: dict, seed: float, t_sec: float) -> tuple[float, float, float, float]:
    """(dx, dy, градусы, масштаб) тряски в момент t_sec. Shake Amount — общий множитель интенсивности:
    растут сдвиг, поворот и (если задан) зум; сам по себе Amount кадр НЕ увеличивает.
    Компенсация зума — отдельный ручной параметр, по умолчанию 0."""
    mode = int(v.get("mode", 0))
    master = max(0.0, v.get("master", 1.0))
    ps, rs, fs, oc, sm, ys = _SHAKE_STYLE.get(mode, _SHAKE_STYLE[0])
    smooth = min(1.0, max(0.0, v.get("smooth", 0.6) + sm))
    rnd = min(1.0, max(0.0, v.get("random", 0.5)))
    freq = max(0.0, v["freq"]) * fs
    octaves = max(1.0, min(7.0, v.get("detail", 3.0) + oc))
    # самая быстрая октава — не выше ~12 Гц: иначе при 24–30 к/с шум «скачет» каждый кадр (алиасинг)
    if freq > 1e-6:
        octaves = max(1.0, min(octaves, 1.0 + math.log(max(1.0, 12.0 / freq)) / math.log(2.03)))
    # Randomness: «плавающий» темп (время ускоряется/замедляется) и неровная огибающая силы
    t = t_sec * freq + rnd * 0.8 * _perlin1(t_sec * 0.35, seed + 41.0)
    env = max(0.15, 1.0 + rnd * 0.55 * _perlin1(t_sec * 0.27, seed + 57.0))
    if mode == 2:              # удар: серия толчков, каждый резко начинается и экспоненциально гаснет
        period = 1.0 / max(0.05, freq * 0.25)
        k = math.floor(t_sec / period)
        dt = t_sec - k * period
        env *= math.exp(-dt * 6.0 / period) * (0.6 + 0.4 * abs(_hash(k, seed + 9.0)))
    amp = v["amount"] * master * ps * env
    nx = _shake_fbm(t, seed, octaves, smooth)
    ny = _shake_fbm(t + 31.7, seed + 3.3, octaves, smooth)
    nr = _shake_fbm(t * 0.8 + 63.1, seed + 7.7, octaves, smooth)
    nz = _shake_fbm(t * 0.6 + 91.3, seed + 11.1, max(1.0, octaves - 1), smooth)
    if mode == 5:              # всё движение вдоль одной оси
        a = math.radians(v.get("dir", 0.0) - 90)
        dx, dy = math.cos(a) * nx * amp, math.sin(a) * nx * amp
    else:
        dx = nx * amp * v.get("xmul", 100.0) / 100.0
        dy = ny * amp * v.get("ymul", 100.0) / 100.0 * ys
    dr = nr * v["rot"] * master * rs * env
    ds = max(0.05, 1.0 + nz * v.get("zoom", 0.0) * master * env / 100.0)
    ds *= 1.0 + max(0.0, v.get("zcomp", 0.0)) / 100.0
    return dx, dy, dr, ds


def shake_offset(layer, fps: float, comp_frame: float) -> tuple[float, float, float, float]:
    """Суммарно (dx, dy, градусы, масштаб) от всех эффектов «Тряска» слоя."""
    dx = dy = dr = 0.0
    ds = 1.0
    for fx in active_effects(layer):
        if fx["type"] not in ("shake", "wiggle"):
            continue
        v = values(layer, fx, layer.local(comp_frame))
        seed = float(fx.get("opts", {}).get("seed", 1))
        if fx["type"] == "wiggle":
            a, b, c, d = wiggle_values(v, seed, comp_frame / fps)
        else:
            _x, _y, w, h = layer.local_rect()
            v["_w"], v["_h"] = w or 1920.0, h or 1080.0
            a, b, c, d = shake_values(v, seed, comp_frame / fps)
        dx, dy, dr, ds = dx + a, dy + b, dr + c, ds * d
    return dx, dy, dr, ds


def wiggle_values(v: dict, seed: float, t_sec: float) -> tuple[float, float, float, float]:
    """Простая тряска (как wiggle() в AE): всё по нулям, каждую ось крутишь сам.
    Z — сдвиг «к камере / от камеры», в 2D это изменение масштаба как у перспективы."""
    t = t_sec * v.get("freq", 2.0)
    det = v.get("detail", 1.0)
    nx = _fbm(t, seed, det)
    ny = _fbm(t + 31.7, seed + 3.3, det)
    nz = _fbm(t + 57.9, seed + 5.1, det)
    nr = _fbm(t + 63.1, seed + 7.7, det)
    ns = _fbm(t + 91.3, seed + 11.1, det)
    ds = max(0.05, 1.0 + ns * v.get("scale", 0.0) / 100.0)
    z = nz * v.get("pz", 0.0)
    ds *= 1000.0 / max(50.0, 1000.0 - z)              # положительный Z — ближе (крупнее)
    return nx * v.get("px", 0.0), ny * v.get("py", 0.0), nr * v.get("rot", 0.0), ds


def shake_wants_blur(layer) -> bool:
    return any(fx["type"] == "shake" and fx.get("on", True) and
               layer.props.get(prop_key(fx["id"], "mblur")) is not None and
               layer.props[prop_key(fx["id"], "mblur")].value_at(0)[0] >= 0.5
               for fx in active_effects(layer))


# ============================================================================ numpy <-> QImage
def to_array(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format_ARGB32_Premultiplied)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    buf = np.frombuffer(img.constBits(), np.uint8, count=h * bpl).reshape(h, bpl)
    return buf[:, : w * 4].reshape(h, w, 4).astype(np.float32)


def to_image(arr: np.ndarray) -> QImage:
    a = np.ascontiguousarray(np.clip(arr, 0, 255).astype(np.uint8))
    h, w = a.shape[:2]
    img = QImage(a.data, w, h, w * 4, QImage.Format_ARGB32_Premultiplied)
    return img.copy()  # отвязываемся от буфера numpy


def _box(a: np.ndarray, r: int, axis: int) -> np.ndarray:
    if r < 1:
        return a
    n = a.shape[axis]
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r + 1, r)
    c = np.cumsum(np.pad(a, pad), axis=axis, dtype=np.float32)
    hi = [slice(None)] * a.ndim
    lo = [slice(None)] * a.ndim
    hi[axis] = slice(2 * r + 1, 2 * r + 1 + n)
    lo[axis] = slice(0, n)
    return (c[tuple(hi)] - c[tuple(lo)]) / (2 * r + 1)


def _resize(arr: np.ndarray, w: int, h: int) -> np.ndarray:
    img = to_image(arr).scaled(max(1, w), max(1, h), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    return to_array(img)


def _downsample(arr: np.ndarray, k: int) -> np.ndarray:
    """Уменьшение в k раз (k — степень двойки) последовательными усреднениями 2×2."""
    a = arr
    while k > 1:
        h, w = a.shape[0] // 2 * 2, a.shape[1] // 2 * 2
        a = (a[0:h:2, 0:w:2] + a[1:h:2, 0:w:2] + a[0:h:2, 1:w:2] + a[1:h:2, 1:w:2]) * 0.25
        k //= 2
    return a


def _blur_down(radius: float, w: int, h: int) -> int:
    down = 1
    while radius / down > 4 and min(w, h) / (down * 2) > 8:
        down *= 2
    return down


def _gauss_small(a: np.ndarray, sigma: float) -> np.ndarray:
    """Гаусс с маленьким ядром (σ ≤ ~2): сумма сдвинутых срезов, края — повтор крайнего пикселя."""
    r = max(1, int(math.ceil(sigma * 2.0)))
    k = np.exp(-0.5 * (np.arange(-r, r + 1) / max(sigma, 1e-3)) ** 2).astype(np.float32)
    k /= k.sum()
    h, w = a.shape[:2]
    # по вертикали
    p = np.concatenate([np.repeat(a[:1], r, axis=0), a, np.repeat(a[-1:], r, axis=0)], axis=0)
    out = p[0:h] * k[0]
    tmp = np.empty_like(out)
    for i in range(1, 2 * r + 1):
        np.multiply(p[i:i + h], k[i], out=tmp)
        out += tmp
    # по горизонтали
    p = np.concatenate([np.repeat(out[:, :1], r, axis=1), out, np.repeat(out[:, -1:], r, axis=1)], axis=1)
    res = p[:, 0:w] * k[0]
    for i in range(1, 2 * r + 1):
        np.multiply(p[:, i:i + w], k[i], out=tmp)
        res += tmp
    return res


def _blur_pyramid(arr: np.ndarray, sigma: float) -> np.ndarray:
    """Быстрое размытие большого радиуса: уменьшаем вдвое, пока σ не станет ~1.5 пикселя,
    размываем маленьким гауссом и плавно растягиваем обратно (как bloom в играх)."""
    h, w = arr.shape[:2]
    a, d = arr, 1
    while sigma / d > 1.5 and min(a.shape[0], a.shape[1]) > 16:
        a = _downsample(a, 2)
        d *= 2
    pyr = 0.55 * d   # сколько уже размыло само уменьшение
    s_rem = math.sqrt(max(0.0, sigma * sigma - pyr * pyr)) / d
    if s_rem > 0.3:
        a = _gauss_small(a, s_rem)
    if d > 1:
        a = _resize(a, w, h)
    return a


def blur(arr: np.ndarray, radius: float) -> np.ndarray:
    """Приближение гауссова размытия тремя проходами box-фильтра.
    Радиусы больше 4 px считаются на уменьшенной копии — на глаз то же самое, но в разы быстрее."""
    if radius < 0.5:
        return arr
    h, w = arr.shape[:2]
    sigma = radius / 2.0
    if sigma >= 0.75 and w * h >= 200_000 and sigma < 6:
        from . import gpu   # средний радиус на большом кадре — на видеокарте быстрее
        if gpu.available():
            r = gpu.blur(arr, sigma)
            if r is not None:
                return r
    if sigma < 0.4:
        return arr
    if sigma <= 1.5:
        return _gauss_small(arr, sigma)
    return _blur_pyramid(arr, sigma)


def shift(arr: np.ndarray, dx: int, dy: int) -> np.ndarray:
    """Сдвиг с прозрачными краями."""
    h, w = arr.shape[:2]
    out = np.zeros_like(arr)
    if abs(dx) >= w or abs(dy) >= h:
        return out
    ys, yd = (slice(0, h - dy), slice(dy, h)) if dy >= 0 else (slice(-dy, h), slice(0, h + dy))
    xs, xd = (slice(0, w - dx), slice(dx, w)) if dx >= 0 else (slice(-dx, w), slice(0, w + dx))
    out[yd, xd] = arr[ys, xs]
    return out


def _color(hexstr: str) -> np.ndarray:
    c = QColor(hexstr)
    return np.array([c.blue(), c.green(), c.red()], np.float32)


# ============================================================================ эффекты
def _directional(arr, angle, length):
    if length < 1:
        return arr
    n = int(max(2, min(48, length / 2)))
    rad = math.radians(angle - 90)  # 0° — вертикально, как в AE
    acc = np.zeros_like(arr)
    for i in range(n):
        t = (i / (n - 1) - 0.5) * length
        acc += shift(arr, int(round(math.cos(rad) * t)), int(round(math.sin(rad) * t)))
    return acc / n


def _glow(arr, threshold, radius, intensity):
    a = arr[..., 3:4]
    lum = arr[..., 0:1] * 0.114 + arr[..., 1:2] * 0.587 + arr[..., 2:3] * 0.299
    lum_u = np.where(a > 0, lum / np.maximum(a, 1e-3), 0)
    th = threshold / 100.0
    m = np.clip((lum_u - th) / max(1e-3, 1 - th), 0, 1)
    g = blur(arr * m, radius) * (intensity / 100.0)
    out = arr + g
    out[..., 3] = np.maximum(out[..., 3], out[..., :3].max(axis=2))
    return out


def _chroma(arr, amount, angle):
    rad = math.radians(angle)
    dx, dy = int(round(math.cos(rad) * amount)), int(round(math.sin(rad) * amount))
    if dx == 0 and dy == 0:
        return arr
    r = shift(arr, dx, dy)
    b = shift(arr, -dx, -dy)
    out = arr.copy()
    out[..., 2] = r[..., 2]
    out[..., 0] = b[..., 0]
    out[..., 3] = np.maximum(arr[..., 3], np.maximum(r[..., 3], b[..., 3]))
    out[..., :3] = np.minimum(out[..., :3], out[..., 3:4])
    return out


def _tint(arr, color_hex, amount):
    k = amount / 100.0
    if k <= 0:
        return arr
    col = _color(color_hex)
    a = arr[..., 3:4] / 255.0
    out = arr.copy()
    out[..., :3] = arr[..., :3] + (col * a - arr[..., :3]) * k
    return out


def _bc(arr, brightness, contrast):
    a = arr[..., 3:4]
    u = np.where(a > 0, arr[..., :3] / np.maximum(a, 1e-3) * 255.0, 0)
    f = 1 + contrast / 100.0 if contrast <= 0 else 1 / max(0.02, 1 - contrast / 100.0)
    u = (u - 128) * f + 128 + brightness
    out = arr.copy()
    out[..., :3] = np.clip(u, 0, 255) * a / 255.0
    return out


def _sharpen(arr, amount, radius, threshold):
    if amount <= 0 or radius < 0.3:
        return arr
    b = blur(arr, max(radius, 0.8))   # на превью 1/4 радиус < 0.8 размытие пропускает — резкость пропадала
    diff = arr[..., :3] - b[..., :3]
    if threshold > 0:
        diff = np.where(np.abs(diff) > threshold, diff, 0)
    out = arr.copy()
    out[..., :3] = np.clip(arr[..., :3] + diff * (amount / 100.0), 0, arr[..., 3:4])
    return out


def _mosaic(arr, hb, vb):
    h, w = arr.shape[:2]
    hb, vb = max(1, int(hb)), max(1, int(vb))
    small = _resize(arr, min(w, hb), min(h, vb))
    img = to_image(small).scaled(w, h, Qt.IgnoreAspectRatio, Qt.FastTransformation)
    return to_array(img)


def _vignette(arr, amount, size, softness):
    h, w = arr.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    nx, ny = (xx - w / 2) / (w / 2), (yy - h / 2) / (h / 2)
    d = np.sqrt(nx * nx + ny * ny) / max(0.01, size / 100.0 * 1.2)
    soft = max(0.01, softness / 100.0)
    m = np.clip((d - (1 - soft)) / soft, 0, 1)[..., None]
    out = arr.copy()
    out[..., :3] = arr[..., :3] * (1 - m * amount / 100.0)
    return out


def _unpremult(arr):
    a = arr[..., 3:4]
    return np.where(a > 0, arr[..., :3] / np.maximum(a, 1e-3) * 255.0, 0), a


def _hsl(arr, hue, sat, light):
    rgb, a = _unpremult(arr)
    b, g, r = rgb[..., 0] / 255, rgb[..., 1] / 255, rgb[..., 2] / 255
    # поворот тона в пространстве YIQ (быстро и без конверсии в HSL)
    y = 0.299 * r + 0.587 * g + 0.114 * b
    i = 0.596 * r - 0.274 * g - 0.322 * b
    q = 0.211 * r - 0.523 * g + 0.312 * b
    th = math.radians(hue)
    c, s_ = math.cos(th), math.sin(th)
    k = 1 + sat / 100.0
    i, q = (i * c - q * s_) * k, (i * s_ + q * c) * k
    r = y + 0.956 * i + 0.621 * q
    g = y - 0.272 * i - 0.647 * q
    b = y - 1.106 * i + 1.703 * q
    out_rgb = np.stack([b, g, r], axis=-1)
    if light > 0:
        out_rgb = out_rgb + (1 - out_rgb) * (light / 100.0)
    elif light < 0:
        out_rgb = out_rgb * (1 + light / 100.0)
    out = arr.copy()
    out[..., :3] = np.clip(out_rgb, 0, 1) * a
    return out


def curve_lut(points) -> np.ndarray:
    """Кривая по точкам [[x, y]] (0..1) -> таблица 256 значений. Монотонная кубическая (без «перехлёстов»)."""
    pts = sorted((max(0.0, min(1.0, x)), max(0.0, min(1.0, y))) for x, y in points)
    xs = np.array([p[0] for p in pts])
    ys = np.array([p[1] for p in pts])
    t = np.linspace(0, 1, 256)
    if len(xs) < 2:
        return np.clip(t, 0, 1)
    if len(xs) == 2:
        return np.clip(np.interp(t, xs, ys), 0, 1)
    h = np.diff(xs)
    h[h == 0] = 1e-6
    d = np.diff(ys) / h
    m = np.zeros(len(xs))
    m[0], m[-1] = d[0], d[-1]
    for i in range(1, len(xs) - 1):
        m[i] = 0 if d[i - 1] * d[i] <= 0 else 2 / (1 / d[i - 1] + 1 / d[i])
    out = np.empty_like(t)
    idx = np.clip(np.searchsorted(xs, t) - 1, 0, len(xs) - 2)
    x0, x1 = xs[idx], xs[idx + 1]
    hh = x1 - x0
    hh[hh == 0] = 1e-6
    u = (t - x0) / hh
    h00, h10, h01, h11 = 2 * u ** 3 - 3 * u ** 2 + 1, u ** 3 - 2 * u ** 2 + u, -2 * u ** 3 + 3 * u ** 2, u ** 3 - u ** 2
    out = h00 * ys[idx] + h10 * hh * m[idx] + h01 * ys[idx + 1] + h11 * hh * m[idx + 1]
    out = np.where(t < xs[0], ys[0], np.where(t > xs[-1], ys[-1], out))
    return np.clip(out, 0, 1)


def _curves(arr, opts):
    rgb, a = _unpremult(arr)
    master = curve_lut(opts.get("rgb", [[0, 0], [1, 1]]))
    out = arr.copy()
    for ch, key in ((2, "r"), (1, "g"), (0, "b")):   # BGRA в памяти
        lut = curve_lut(opts.get(key, [[0, 0], [1, 1]]))
        comb = lut[np.clip((master * 255).astype(np.int32), 0, 255)]
        idx = np.clip(rgb[..., ch], 0, 255).astype(np.int32)
        out[..., ch] = comb[idx] * a[..., 0]
    return out


def _noise_fx(arr, amount, color, size, seed):
    if amount <= 0:
        return arr
    h, w = arr.shape[:2]
    rng = np.random.default_rng(int(seed) & 0xFFFFFFFF)
    sh = (max(1, int(h / size)), max(1, int(w / size)), 3 if color else 1)
    n = rng.standard_normal(sh).astype(np.float32)
    if size > 1:
        n = np.repeat(np.repeat(n, int(np.ceil(size)), 0), int(np.ceil(size)), 1)[:h, :w]
    n = n[:h, :w]
    if n.shape[2] == 1:
        n = np.repeat(n, 3, axis=2)
    out = arr.copy()
    a = arr[..., 3:4] / 255.0
    out[..., :3] = np.clip(arr[..., :3] + n * amount * 1.6 * a, 0, arr[..., 3:4])
    return out


def _colorize(arr, hue, sat):
    """Тонирование как галка Colorize у Hue/Saturation: яркость остаётся, цвет — один тон."""
    rgb, a = _unpremult(arr)
    lum = (rgb[..., 0] * 0.114 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.299)[..., None] / 255.0
    c = QColor.fromHsvF((hue % 360) / 360.0, 1.0, 1.0)
    tint = np.array([c.blueF(), c.greenF(), c.redF()], np.float32)
    k = sat / 100.0
    col = lum * (1 - k) + lum * tint * k * 1.6
    out = arr.copy()
    out[..., :3] = np.clip(col, 0, 1) * a
    return out


def _tint_map(arr, black, white, amount):
    rgb, a = _unpremult(arr)
    lum = (rgb[..., 0] * 0.114 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.299)[..., None] / 255.0
    bl = np.array([black[2], black[1], black[0]], np.float32)
    wh = np.array([white[2], white[1], white[0]], np.float32)
    mapped = bl + (wh - bl) * lum
    res = rgb + (mapped - rgb) * (amount / 100.0)
    out = arr.copy()
    out[..., :3] = np.clip(res, 0, 255) * a / 255.0
    return out


def _invert(arr, channel, blend):
    rgb, a = _unpremult(arr)
    if channel == 1:
        lum = rgb[..., 0:1] * 0.114 + rgb[..., 1:2] * 0.587 + rgb[..., 2:3] * 0.299
        inv = np.clip(rgb + (255 - 2 * lum), 0, 255)
    else:
        inv = 255 - rgb
    res = inv + (rgb - inv) * (blend / 100.0)
    out = arr.copy()
    out[..., :3] = res * a / 255.0
    return out


def _glow_full(arr, v):
    """Свечение с режимом наложения, тонировкой и хроматической аберрацией свечения (группа «Стиль»).
    Маска яркости и размытие считаются на уменьшенной копии — это основная экономия времени."""
    h, w = arr.shape[:2]
    down = _blur_down(v["radius"], w, h)
    small = _downsample(arr, down) if down > 1 else arr
    a = small[..., 3:4]
    lum = small[..., 0:1] * 0.114 + small[..., 1:2] * 0.587 + small[..., 2:3] * 0.299
    lum_u = np.where(a > 0, lum / np.maximum(a, 1e-3), 0)
    th = v["threshold"] / 100.0
    m = np.clip((lum_u - th) / max(1e-3, 1 - th), 0, 1)
    src = small * m
    if v.get("colors", 0) == 1:
        t = v.get("tint", [255, 170, 60])
        col = np.array([t[2], t[1], t[0]], np.float32) / 255.0
        src[..., :3] = (src[..., 0:1] * 0.114 + src[..., 1:2] * 0.587 + src[..., 2:3] * 0.299) * col
    g = blur(src, v["radius"] / down) * (v["intensity"] / 100.0)
    if down > 1:
        g = _resize(g, w, h)
    ch = v.get("chroma", 0.0)
    if ch >= 0.5:
        g = _chroma(g, ch, 0.0)
    if v.get("blend", 0) == 1:   # экран
        out = arr.copy()
        out[..., :3] = 255 - (255 - arr[..., :3]) * (255 - np.clip(g[..., :3], 0, 255)) / 255.0
    else:
        out = arr + g
    out[..., 3] = np.maximum(out[..., 3], np.minimum(255, np.maximum(np.maximum(out[..., 0], out[..., 1]), out[..., 2])))
    return out


def _ramp(arr, v, scale):
    """Gradient Ramp: линейный или радиальный градиент по форме слоя (альфа сохраняется)."""
    h, w = arr.shape[:2]
    x0, y0 = v["start"][0] * scale, v["start"][1] * scale
    x1, y1 = v["end"][0] * scale, v["end"][1] * scale
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    dx, dy = x1 - x0, y1 - y0
    if v.get("shape", 0) == 1:
        rad = max(1e-3, math.hypot(dx, dy))
        t = np.sqrt((xs - x0) ** 2 + (ys - y0) ** 2) / rad
    else:
        ln = max(1e-3, dx * dx + dy * dy)
        t = ((xs - x0) * dx + (ys - y0) * dy) / ln
    sc = v.get("scatter", 0.0)
    if sc > 0:   # рассеивание убирает «ступеньки» градиента
        rng = np.random.default_rng(1234)
        t = t + (rng.random((h, w), dtype=np.float32) - 0.5) * (sc / 100.0) * 0.08
    t = np.clip(t, 0, 1)[..., None]
    c0 = np.array([v["c0"][2], v["c0"][1], v["c0"][0]], np.float32)
    c1 = np.array([v["c1"][2], v["c1"][1], v["c1"][0]], np.float32)
    a = arr[..., 3:4]
    out = arr.copy()
    out[..., :3] = (c0 + (c1 - c0) * t) * (a / 255.0)
    b = v.get("blend", 0.0) / 100.0
    if b > 0:
        out = out * (1 - b) + arr * b
    return out


def _keylight(arr, v, scale):
    """Keylight: цветоразностный кеер. Альфа = 1 − (главный канал − max(остальные)) / то же у цвета фона,
    затем отсечение чёрного/белого, сжатие/расширение и мягкость края, подавление отсвета (despill)."""
    view = int(v.get("view", 0))
    if view == 2:
        return arr
    rgb, a = _unpremult(arr)                         # BGR, 0..255
    scr = np.array([v["screen"][2], v["screen"][1], v["screen"][0]], np.float32)
    p = int(np.argmax(scr))                          # главный канал фона (обычно зелёный)
    o1, o2 = [i for i in range(3) if i != p]
    diff_s = max(1.0, scr[p] - max(scr[o1], scr[o2]))
    diff = rgb[..., p] - np.maximum(rgb[..., o1], rgb[..., o2])
    matte = 1.0 - np.clip(diff / diff_s * (v.get("gain", 100.0) / 100.0), 0, 1)
    cb, cw = v.get("clip_black", 0.0) / 100.0, v.get("clip_white", 100.0) / 100.0
    matte = np.clip((matte - cb) / max(1e-3, cw - cb), 0, 1)
    shrink = v.get("shrink", 0.0) * scale
    if abs(shrink) >= 0.25:   # сжатие/расширение: размыть и сдвинуть порог
        bl = blur(np.repeat(matte[..., None], 4, axis=2), abs(shrink))[..., 0]
        matte = np.clip(bl * 2 - (1.0 if shrink < 0 else 0.0), 0, 1)   # «+» расширяет, «−» сжимает
    soft = v.get("soft", 0.0) * scale
    if soft >= 0.5:
        matte = blur(np.repeat(matte[..., None], 4, axis=2), soft)[..., 0]
    if view == 1:   # чёрно-белая маска
        out = np.empty_like(arr)
        out[..., :3] = (matte * 255.0)[..., None] * (a / 255.0)
        out[..., 3] = a[..., 0]
        return out
    ds = v.get("despill", 100.0) / 100.0
    if ds > 0:   # убираем зелёный отсвет с краёв и волос
        lim = np.maximum(rgb[..., o1], rgb[..., o2])
        rgb = rgb.copy()
        rgb[..., p] = rgb[..., p] - np.maximum(0, rgb[..., p] - lim) * ds
    na = a[..., 0] * matte
    out = np.empty_like(arr)
    out[..., :3] = rgb * (na / 255.0)[..., None]
    out[..., 3] = na
    return out


# ---------------------------------------------------------------- фильтры (Threshold, Levels и т.п.)
def _lum(rgb):
    return rgb[..., 0] * 0.114 + rgb[..., 1] * 0.587 + rgb[..., 2] * 0.299   # BGR


def _per_pixel(arr, fn):
    """Обёртка: fn работает с непремультиплицированным BGR 0..255, альфа сохраняется."""
    rgb, a = _unpremult(arr)
    res = np.clip(fn(rgb), 0, 255)
    out = arr.copy()
    out[..., :3] = res * a / 255.0
    return out


def _f_threshold(arr, v, **_):
    lvl = v["level"]
    return _per_pixel(arr, lambda rgb: np.repeat((_lum(rgb) >= lvl)[..., None], 3, axis=2) * 255.0)


def _f_posterize(arr, v, **_):
    n = max(2, int(round(v["levels"])))
    return _per_pixel(arr, lambda rgb: np.round(rgb / 255.0 * (n - 1)) / (n - 1) * 255.0)


def _f_levels(arr, v, **_):
    ib, iw = v["in_black"], max(v["in_black"] + 1, v["in_white"])
    g = max(0.01, v["gamma"])
    ob, ow = v["out_black"], v["out_white"]
    lut = np.clip((np.arange(256, dtype=np.float32) - ib) / (iw - ib), 0, 1) ** (1.0 / g) * (ow - ob) + ob
    return _per_pixel(arr, lambda rgb: lut[np.clip(rgb, 0, 255).astype(np.uint8)])


def _f_exposure(arr, v, **_):
    k = 2.0 ** v["exposure"]
    g = max(0.01, v["gamma"])
    lut = np.clip((np.arange(256, dtype=np.float32) / 255.0) * k + v["offset"], 0, 1) ** (1.0 / g) * 255.0
    return _per_pixel(arr, lambda rgb: lut[np.clip(rgb, 0, 255).astype(np.uint8)])


def _f_bw(arr, v, **_):
    w = np.array([v["blues"], v["greens"], v["reds"]], np.float32) / 100.0
    am = v["amount"] / 100.0

    def fn(rgb):
        g = np.clip((rgb * w).sum(axis=2, keepdims=True), 0, 255)
        return rgb + (g - rgb) * am
    return _per_pixel(arr, fn)


def _f_vibrance(arr, v, **_):
    vib, sat = v["vibrance"] / 100.0, v["sat"] / 100.0

    def fn(rgb):
        l = _lum(rgb)[..., None]
        mx, mn = rgb.max(axis=2, keepdims=True), rgb.min(axis=2, keepdims=True)
        s_cur = (mx - mn) / 255.0
        k = 1 + sat + vib * (1 - s_cur)          # сочность сильнее тянет тусклые цвета
        return l + (rgb - l) * k
    return _per_pixel(arr, fn)


def _f_photofilter(arr, v, **_):
    col = np.array([v["color"][2], v["color"][1], v["color"][0]], np.float32)
    d = v["density"] / 100.0
    keep = v.get("keep", 1) >= 0.5

    def fn(rgb):
        res = rgb * (1 - d) + rgb * (col / 255.0) * d + col * d * 0.25
        if keep:
            l0, l1 = _lum(rgb)[..., None], _lum(res)[..., None]
            res = res * np.where(l1 > 1e-3, l0 / np.maximum(l1, 1e-3), 1)
        return res
    return _per_pixel(arr, fn)


def _f_colorbal(arr, v, **_):
    def fn(rgb):
        l = _lum(rgb)[..., None] / 255.0
        ws = np.clip(1 - l * 3, 0, 1)                    # тени
        wh = np.clip(l * 3 - 2, 0, 1)                    # света
        wm = 1 - ws - wh
        shift = np.zeros_like(rgb)
        for pre, w in (("s", ws), ("m", wm), ("h", wh)):
            vec = np.array([v[pre + "_b"], v[pre + "_g"], v[pre + "_r"]], np.float32) * 0.6
            shift = shift + w * vec
        return rgb + shift
    return _per_pixel(arr, fn)


def _sobel(gray):
    p = np.pad(gray, 1, mode="edge")
    gx = (p[:-2, 2:] + 2 * p[1:-1, 2:] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[1:-1, :-2] + p[2:, :-2])
    gy = (p[2:, :-2] + 2 * p[2:, 1:-1] + p[2:, 2:]) - (p[:-2, :-2] + 2 * p[:-2, 1:-1] + p[:-2, 2:])
    return gx, gy


def _f_findedges(arr, v, **_):
    def fn(rgb):
        gx, gy = _sobel(_lum(rgb))
        e = np.clip(np.sqrt(gx * gx + gy * gy) / 4.0, 0, 255)
        e = e if v["invert"] >= 0.5 else 255 - e           # как в AE: тёмные линии на белом
        res = np.repeat(e[..., None], 3, axis=2)
        b = v["blend"] / 100.0
        return res * (1 - b) + rgb * b
    return _per_pixel(arr, fn)


def _f_emboss(arr, v, scale=1.0, **_):
    def fn(rgb):
        gx, gy = _sobel(_lum(rgb))
        r = math.radians(v["dir"])
        e = (gx * math.cos(r) - gy * math.sin(r)) * v["relief"] * (v["contrast"] / 100.0) / 8.0 + 128
        res = np.repeat(np.clip(e, 0, 255)[..., None], 3, axis=2)
        b = v["blend"] / 100.0
        return res * (1 - b) + rgb * b
    return _per_pixel(arr, fn)


def _f_glitch(arr, v, scale=1.0, frame=0, seed=1, **_):
    am = v["amount"] / 100.0
    if am <= 0:
        return arr
    h, w = arr.shape[:2]
    step = max(1, int(round(v["speed"])))
    rng = np.random.default_rng(int(seed) * 100003 + int(frame) // step)
    out = arr.copy()
    bh = max(1, int(v["block"] * scale))
    y = 0
    while y < h:   # горизонтальные полосы со сдвигом
        hh = max(1, int(bh * rng.uniform(0.3, 1.7)))
        if rng.random() < am * 0.6:
            dx = int(rng.normal(0, w * 0.06 * am))
            out[y:y + hh] = np.roll(arr[y:y + hh], dx, axis=1)
        y += hh
    sp = int(v["rgb"] * scale * am * rng.uniform(0.4, 1.0))
    if sp:   # разнос каналов: красный влево, синий вправо
        out[..., 2] = np.roll(out[..., 2], -sp, axis=1)
        out[..., 0] = np.roll(out[..., 0], sp, axis=1)
        out[..., 3] = np.maximum(out[..., 3], np.maximum(out[..., 0], out[..., 2]))
    return out


def _f_mirror(arr, v, scale=1.0, **_):
    h, w = arr.shape[:2]
    cx = int(np.clip(v["center"][0] * scale, 1, w - 1))
    cy = int(np.clip(v["center"][1] * scale, 1, h - 1))
    out = arr.copy()
    m = int(v["mode"])
    if m == 0:
        n = min(cx, w - cx); out[:, cx:cx + n] = arr[:, cx - n:cx][:, ::-1]
    elif m == 1:
        n = min(cx, w - cx); out[:, cx - n:cx] = arr[:, cx:cx + n][:, ::-1]
    elif m == 2:
        n = min(cy, h - cy); out[cy:cy + n] = arr[cy - n:cy][::-1]
    else:
        n = min(cy, h - cy); out[cy - n:cy] = arr[cy:cy + n][::-1]
    return out


# ---------------------------------------------------------------- в духе Sapphire
_GRID: dict = {}


def _grid(h, w):
    """Сетка координат кадра (кэш — не создаём заново на каждом кадре)."""
    g = _GRID.get((h, w))
    if g is None:
        ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
        g = _GRID[(h, w)] = (xs, ys)
        while len(_GRID) > 4:
            _GRID.pop(next(iter(_GRID)))
    return g


def _remap(arr, sx, sy, nearest=False):
    """Картинка по картам координат (sx, sy), билинейно (или по ближайшему пикселю — быстрее);
    за краем — отражение (Wrap: Reflect, как в Sapphire). Выборка через плоские индексы — это быстро."""
    h, w = arr.shape[:2]
    c = arr.shape[2]
    flat = arr.reshape(h * w, c)

    def refl(v, n):
        v = np.abs(v)
        period = 2 * (n - 1)
        v = np.mod(v, period)
        return np.where(v > n - 1, period - v, v)
    sx = refl(sx, w)
    sy = refl(sy, h)
    if nearest:
        idx = (np.rint(sy).astype(np.int32) * w + np.rint(sx).astype(np.int32)).ravel()
        return np.take(flat, idx, axis=0).reshape(h, w, c)
    x0 = np.minimum(sx.astype(np.int32), w - 2)
    y0 = np.minimum(sy.astype(np.int32), h - 2)
    fx = (sx - x0).reshape(-1, 1).astype(np.float32)
    fy = (sy - y0).reshape(-1, 1).astype(np.float32)
    i00 = (y0 * w + x0).ravel()
    top = np.take(flat, i00, axis=0) * (1 - fx) + np.take(flat, i00 + 1, axis=0) * fx
    bot = np.take(flat, i00 + w, axis=0) * (1 - fx) + np.take(flat, i00 + w + 1, axis=0) * fx
    return (top * (1 - fy) + bot * fy).reshape(h, w, c)


def _f_blurmo(arr, v, scale=1.0, **_):
    """S_BlurMoCurves: зум/поворот/сдвиг кадра с размытием в движении вдоль пути трансформации.
    Сэмплы рисует QPainter (C++, билинейно) с накоплением среднего — быстро даже на 32 сэмплах."""
    z, rot = max(0.05, v["zdist"]), v["rotate"]
    sx, sy = v["shiftx"] * scale, v["shifty"] * scale
    sh = max(0.0, v["shutter"])
    n = int(max(2, min(32, v["samples"]))) if sh > 0.001 else 1
    h, w = arr.shape[:2]
    cx, cy = v["center"][0] * scale, v["center"][1] * scale
    src = to_image(arr)
    dst = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    dst.fill(0)
    p = QPainter(dst)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    for i in range(n):
        t = 1.0 - sh * (i / max(1, n - 1)) if n > 1 else 1.0
        zz = 1.0 + (z - 1.0) * t
        tr = QTransform()
        tr.translate(cx + sx * t, cy + sy * t)
        tr.rotate(rot * t)
        tr.scale(zz, zz)
        tr.translate(-cx, -cy)
        p.setTransform(tr)
        p.setOpacity(1.0 / (i + 1))     # бегущее среднее: каждый новый сэмпл весит 1/(i+1)
        p.drawImage(QPointF(0, 0), src)
    p.end()
    out = to_array(dst)
    b = v["bright"]
    if abs(b - 1.0) > 1e-3:
        out[..., :3] = np.minimum(out[..., 3:4], out[..., :3] * b) if b > 1 else out[..., :3] * b
    return out


def _f_puddle(arr, v, scale=1.0, frame=0, fps=30.0, **_):
    """S_WarpPuddle: круговые волны от центра, как от капли в луже; фаза бежит во времени."""
    h, w = arr.shape[:2]
    cx, cy = v["center"][0] * scale, v["center"][1] * scale
    xs, ys = _grid(h, w)
    dx, dy = xs - cx, ys - cy
    r = np.sqrt(dx * dx + dy * dy) + 1e-3
    ref = 100.0 * scale
    ph = v["phase"] + v["speed"] * frame / max(fps, 1.0)
    inner, outer, soft = v["inner"] * scale, v["outer"] * scale, max(1e-3, v["soft"] * scale)
    fall = np.clip((outer - r) / soft, 0, 1) * np.clip((r - inner) / soft + 1.0, 0, 1)
    wave = np.sin(r / ref * v["freq"] * 2 * math.pi - ph * 2 * math.pi) * v["amp"] * ref * 0.5 * fall
    ux, uy = dx / r, dy / r
    out = _remap(arr, xs + ux * wave, ys + uy * wave)
    ch = v.get("chroma", 0.0)
    if ch > 0.01:   # разнос каналов вдоль волны
        rr = _remap(arr, xs + ux * wave * (1 + ch), ys + uy * wave * (1 + ch))
        bb = _remap(arr, xs + ux * wave * (1 - ch), ys + uy * wave * (1 - ch))
        out[..., 2] = rr[..., 2]
        out[..., 0] = bb[..., 0]
    return out


def _f_distchroma(arr, v, scale=1.0, **_):
    """S_DistortChroma: картинка «течёт» вдоль градиента размытой яркости, каналы R/G/B смещаются
    на разные расстояния и складываются из нескольких шагов — радужные разводы."""
    h, w = arr.shape[:2]
    lum = (arr[..., 0] * 0.114 + arr[..., 1] * 0.587 + arr[..., 2] * 0.299)[..., None]
    lens = blur(np.repeat(lum, 4, axis=2), max(0.5, v["lens"] * scale))[..., 0]
    gy, gx = np.gradient(lens)
    a = math.radians(v["rot"])
    ca, sa = math.cos(a), math.sin(a)
    gx, gy = ca * gx - sa * gy, sa * gx + ca * gy
    amt = v["amount"] * 6.0 * scale
    xs, ys = _grid(h, w)
    steps = int(max(1, min(32, v["steps"])))
    out = np.zeros_like(arr)
    for ch, k in ((2, v["wr"]), (1, (v["wr"] + v["wb"]) / 2), (0, v["wb"])):
        acc = np.zeros((h, w, 1), np.float32)
        plane = np.ascontiguousarray(arr[..., ch:ch + 1])
        for i in range(steps):   # много шагов по ближайшему пикселю ≈ размытие вдоль потока, гладко и быстро
            t = (i + 1) / steps * k * amt
            acc += _remap(plane, xs + gx * t, ys + gy * t, nearest=steps > 2)
        out[..., ch] = acc[..., 0] / steps
    out[..., 3] = arr[..., 3]
    out[..., :3] = np.minimum(out[..., :3], out[..., 3:4])
    return out


def random_edit_offset(layer, local_frame: float, fps: float) -> float:
    """S_RandomEdits: сдвиг времени исходника (сек) — каждые N кадров клип прыгает в случайное место."""
    off = 0.0
    for fx in active_effects(layer):
        if fx["type"] != "randedits":
            continue
        v = values(layer, fx, local_frame)
        n = max(1, int(round(v["len"])))
        seg = int(local_frame // n)
        off += _hash(seg, v["seed"] * 7.77 + 1.0) * v["jump"]
    return off


FILTERS = {"threshold": _f_threshold, "posterize": _f_posterize, "levels": _f_levels, "exposure": _f_exposure,
           "bw": _f_bw, "vibrance": _f_vibrance, "photofilter": _f_photofilter, "colorbal": _f_colorbal,
           "findedges": _f_findedges, "emboss": _f_emboss, "glitch": _f_glitch, "mirror": _f_mirror,
           "blurmo": _f_blurmo, "puddle": _f_puddle, "distchroma": _f_distchroma}


def transform_array(arr: np.ndarray, dx: float, dy: float, deg: float, ds: float = 1.0) -> np.ndarray:
    """Сдвиг и поворот всего кадра вокруг центра (тряска на корректирующем слое)."""
    h, w = arr.shape[:2]
    src = to_image(arr)
    dst = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    dst.fill(0)
    p = QPainter(dst)
    p.setRenderHint(QPainter.SmoothPixmapTransform)
    t = QTransform()
    t.translate(w / 2 + dx, h / 2 + dy)
    t.rotate(deg)
    t.scale(ds, ds)
    t.translate(-w / 2, -h / 2)
    p.setTransform(t)
    p.drawImage(QPointF(0, 0), src)
    p.end()
    return to_array(dst)


def apply_effects(arr: np.ndarray, layer, comp_frame: float, fps: float, scale: float,
                  include_transform: bool = False) -> np.ndarray:
    lf = layer.local(comp_frame)
    for fx in visual_effects(layer):
        t = fx["type"]
        v = values(layer, fx, lf)
        opts = fx.get("opts", {})
        mix = v.get("mix", 100.0) / 100.0
        if mix <= 0.0:
            continue                       # непрозрачность эффекта 0% — эффект как выключен
        src = arr
        if t == "blur":
            arr = blur(arr, v["radius"] * scale)
        elif t == "dblur":
            arr = _directional(arr, v["angle"], v["length"] * scale)
        elif t == "glow":
            v = dict(v, radius=v["radius"] * scale, chroma=v.get("chroma", 0.0) * scale)
            arr = _glow_full(arr, v)
        elif t == "sharpen":
            arr = _sharpen(arr, v["amount"], v["radius"] * scale, v["threshold"])
        elif t == "mosaic":
            arr = _mosaic(arr, v["h"], v["v"])
        elif t == "vignette":
            arr = _vignette(arr, v["amount"], v["size"], v["softness"])
        elif t == "hsl":
            arr = _hsl(arr, v["hue"], v["sat"], v["light"])
            if v.get("colorize"):
                arr = _colorize(arr, v["col_hue"], v["col_sat"])
        elif t == "tint":
            arr = _tint_map(arr, v["black"], v["white"], v["amount"])
        elif t == "invert":
            arr = _invert(arr, v["channel"], v["blend"])
        elif t == "curves":
            arr = _curves(arr, opts)
        elif t == "noise":
            arr = _noise_fx(arr, v["amount"], v["color"], v["size"] * scale if v["size"] > 1 else 1,
                            int(comp_frame) * 7919 + __import__("zlib").crc32(fx["id"].encode()) % 10007)
        elif t == "chroma":
            arr = _chroma(arr, v["amount"] * scale, v["angle"])
        elif t in ("flash", "fill"):
            arr = _tint(arr, opts.get("color", EFFECTS[t].opts["color"]), v["amount"])
        elif t in FILTERS:
            arr = FILTERS[t](arr, v, scale=scale, frame=comp_frame, fps=fps,
                             seed=float(opts.get("seed", 1)) * 7 + __import__("zlib").crc32(fx["id"].encode()) % 997)
        elif t == "ramp":
            arr = _ramp(arr, v, scale)
        elif t == "keylight":
            arr = _keylight(arr, v, scale)
        elif t == "bc":
            arr = _bc(arr, v["brightness"], v["contrast"])
        elif t in ("shake", "wiggle") and include_transform:
            seed = float(opts.get("seed", 1))
            if t == "wiggle":
                dx, dy, dr, ds = wiggle_values(v, seed, comp_frame / fps)
            else:
                v["_w"], v["_h"] = arr.shape[1] / max(scale, 1e-6), arr.shape[0] / max(scale, 1e-6)
                dx, dy, dr, ds = shake_values(v, seed, comp_frame / fps)
            arr = transform_array(arr, dx * scale, dy * scale, dr, ds)
        if mix < 0.999 and arr is not src and arr.shape == src.shape:
            arr = src + (arr - src) * mix      # Effect Opacity: смесь с картинкой до эффекта
    return arr


def has_pixel_effects(layer) -> bool:
    return any(not (EFFECTS[fx["type"]].transform or EFFECTS[fx["type"]].transition) for fx in visual_effects(layer))


# вторая партия эффектов в духе Sapphire — регистрируется в EFFECTS/FILTERS при импорте
from . import sapphire as _sapphire  # noqa: E402,F401
from . import transitions as _transitions  # noqa: E402,F401  переходы (категория «transition»)
from . import fx_extra as _fx_extra  # noqa: E402,F401  Posterize Time, Echo, Deep Glow, Motion Tile
