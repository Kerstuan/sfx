# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Плагины SFX.

Плагин — файл <имя>.py (или папка с __init__.py) в ~/.config/sfx/plugins/. В нём:

    PLUGIN = {"id": "my.glowy", "name": "Glowy", "version": "1.0", "api": 1}

    def register(api):
        api.add_effect("glowy", "Свечение-2", "Glowy", params=[
            {"key": "amount", "ru": "Сила", "en": "Amount", "default": [50.0], "suffix": "%", "lo": 0, "hi": 100},
        ], fn=my_filter)               # my_filter(arr, v, scale=..., frame=..., fps=..., **_) -> arr (BGRA float32, premultiplied)
        api.add_shape_op("bumps", "Шишки", "Bumps", params=[...], fn=my_op)   # my_op(path, v, t_sec) -> QPainterPath

Тип эффекта/оператора получает префикс id плагина ("my.glowy/glowy"), так что имена не пересекаются.
Ошибка внутри плагина не роняет программу: эффект пропускается, ошибка видна в «Эффект → Плагины…».
Выключенные плагины (настройка plugins_disabled) не загружаются. Применяется после перезапуска.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import traceback

API_VERSION = 1

# id плагина -> {"name", "version", "path", "status": loaded|disabled|error, "error", "effects", "ops"}
REGISTRY: dict[str, dict] = {}
_LOADED = False


def plugins_dir() -> str:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.environ.get("SFX_PLUGINS") or os.path.join(base, "sfx", "plugins")


def disabled_ids() -> set[str]:
    try:
        from PySide6.QtCore import QSettings
        v = QSettings("sfx", "sfx").value("plugins_disabled", "") or ""
        return {x for x in str(v).split(",") if x}
    except Exception:  # noqa
        return set()


def set_enabled(pid: str, on: bool):
    from PySide6.QtCore import QSettings
    d = disabled_ids()
    (d.discard if on else d.add)(pid)
    QSettings("sfx", "sfx").setValue("plugins_disabled", ",".join(sorted(d)))


class PluginAPI:
    """То, что видит плагин. Всё регистрируется в общих реестрах программы."""

    version = API_VERSION

    def __init__(self, pid: str):
        self.pid = pid

    def _type(self, name: str) -> str:
        return f"{self.pid}/{name}"

    def add_effect(self, name: str, ru: str, en: str, params: list[dict], fn, category: str = "plugins",
                   transform: bool = False) -> str:
        from . import effects as E
        t = self._type(name)
        if t in E.EFFECTS:
            raise ValueError(f"effect {t} already registered")
        ps = [E.Param(p["key"], p.get("ru", p["key"]), p.get("en", p.get("ru", p["key"])),
                      [float(x) for x in p.get("default", [0.0])], float(p.get("step", 0.5)), p.get("suffix", ""),
                      p.get("lo"), p.get("hi"), p.get("kind", "float"), p.get("choices"), p.get("group"))
              for p in params]
        if not transform:
            ps.append(E.Param("mix", "Непрозрачность эффекта", "Effect Opacity", [100.0], 0.5, "%", 0, 100,
                              group=E.COMPOSITING))
        E.EFFECTS[t] = E.EffectDef(t, ru, en, ps, category=category, transform=transform)
        E.FILTERS[t] = _safe(self.pid, fn)
        REGISTRY[self.pid]["effects"].append(t)
        return t

    def add_shape_op(self, name: str, ru: str, en: str, params: list[dict], fn) -> str:
        from . import shape_ops as so
        t = self._type(name)
        if t in so.OPS:
            raise ValueError(f"shape op {t} already registered")
        so.OPS[t] = ((ru, en), {p["key"]: (p.get("ru", p["key"]), p.get("en", p["key"]),
                                          [float(x) for x in p.get("default", [0.0])], float(p.get("step", 0.5)),
                                          p.get("suffix", ""), p.get("lo"), p.get("hi")) for p in params})
        so.CUSTOM[t] = _safe(self.pid, fn, passthrough_arg=0)
        REGISTRY[self.pid]["ops"].append(t)
        return t


def _safe(pid, fn, passthrough_arg=0):
    """Обёртка: исключение в плагине → вернуть вход без изменений и запомнить ошибку (один раз в лог)."""
    def wrapped(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:  # noqa
            info = REGISTRY.get(pid)
            if info is not None and not info.get("runtime_error"):
                info["runtime_error"] = f"{type(e).__name__}: {e}"
                print(f"plugin {pid}: {info['runtime_error']}", file=sys.stderr)
            return a[passthrough_arg]
    return wrapped


def _candidates(d: str) -> list[str]:
    if not os.path.isdir(d):
        return []
    out = []
    for n in sorted(os.listdir(d)):
        p = os.path.join(d, n)
        if n.endswith(".py") and not n.startswith("_"):
            out.append(p)
        elif os.path.isfile(os.path.join(p, "__init__.py")):
            out.append(os.path.join(p, "__init__.py"))
    return out


def load_all(force: bool = False) -> dict[str, dict]:
    """Загрузить все плагины (один раз за запуск). Возвращает реестр со статусами."""
    global _LOADED
    if _LOADED and not force:
        return REGISTRY
    _LOADED = True
    off = disabled_ids()
    from . import effects as E
    E.CATEGORIES.setdefault("plugins", ("Плагины", "Plugins"))
    for path in _candidates(plugins_dir()):
        modname = "sfx_plugin_" + os.path.basename(os.path.dirname(path) if path.endswith("__init__.py")
                                                       else path[:-3]).replace("-", "_").replace(".", "_")
        info = {"name": os.path.basename(path), "version": "?", "path": path, "status": "error", "error": "",
                "effects": [], "ops": []}
        try:
            spec = importlib.util.spec_from_file_location(modname, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            meta = getattr(mod, "PLUGIN", None)
            if not isinstance(meta, dict) or not meta.get("id"):
                raise ValueError("нет описания PLUGIN = {'id': ...}")
            pid = str(meta["id"])
            info.update(name=meta.get("name", pid), version=str(meta.get("version", "?")))
            if pid in REGISTRY:
                raise ValueError(f"id {pid} уже занят ({REGISTRY[pid]['path']})")
            REGISTRY[pid] = info
            if int(meta.get("api", 1)) > API_VERSION:
                raise ValueError(f"нужен API {meta.get('api')}, в программе {API_VERSION}")
            if pid in off:
                info["status"] = "disabled"
                continue
            mod.register(PluginAPI(pid))
            info["status"] = "loaded"
        except Exception as e:  # noqa — плохой плагин не мешает остальным
            info["error"] = f"{type(e).__name__}: {e}"
            info["trace"] = traceback.format_exc(limit=3)
            REGISTRY.setdefault(info.get("name", path), info)
            print(f"plugin {path}: {info['error']}", file=sys.stderr)
    return REGISTRY
