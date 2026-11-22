# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""Звук: декодирование слоёв с аудиоэффектами (ffmpeg), сведение с «Уровнями звука», волна, проигрывание.

Звук каждого слоя декодируется целиком один раз (с цепочкой его аудиоэффектов) и кэшируется.
Сведение — numpy: громкость по ключам «Уровней звука» считается покадрово и плавно интерполируется.
Одна и та же функция mix() используется и в превью, и в рендере.
"""
from __future__ import annotations

import math
import os
import subprocess
import threading
from collections import OrderedDict

import numpy as np

from .effects import EFFECTS, active_effects, values

SR = 48000
_cache: OrderedDict = OrderedDict()
_cache_lock = threading.Lock()
_CACHE_MAX = 6
_peaks: dict = {}


# ---------------------------------------------------------------- цепочка эффектов -> фильтр ffmpeg
def _atempo_chain(r: float) -> list[str]:
    out = []
    while r > 2.0:
        out.append("atempo=2.0")
        r /= 2.0
    while r < 0.5:
        out.append("atempo=0.5")
        r /= 0.5
    out.append(f"atempo={r:.6f}")
    return out


def fx_filters(layer) -> list[str]:
    """Аудиоэффекты слоя в виде фильтров ffmpeg. Параметры берутся на начале слоя."""
    out = []
    lf = layer.in_point - layer.start
    for fx in active_effects(layer):
        d = EFFECTS[fx["type"]]
        if not d.audio:
            continue
        v = values(layer, fx, lf)
        t = fx["type"]
        if t == "eq":
            if abs(v["low_g"]) > 0.01:
                out.append(f"lowshelf=f={v['low_f']:.2f}:g={v['low_g']:.2f}")
            for b in ("m1", "m2", "m3"):
                if abs(v[f"{b}_g"]) > 0.01:
                    out.append(f"equalizer=f={v[b + '_f']:.2f}:t=q:w={max(0.05, v[b + '_q']):.3f}:g={v[b + '_g']:.2f}")
            if abs(v["high_g"]) > 0.01:
                out.append(f"highshelf=f={v['high_f']:.2f}:g={v['high_g']:.2f}")
        elif t == "passf":
            out.append(f"{'lowpass' if v['mode'] == 1 else 'highpass'}=f={v['freq']:.2f}")
        elif t == "basstreble":
            if abs(v["bass"]) > 0.01:
                out.append(f"bass=g={v['bass']:.2f}")
            if abs(v["treble"]) > 0.01:
                out.append(f"treble=g={v['treble']:.2f}")
        elif t == "comp":
            thr = 10 ** (v["threshold"] / 20)
            mk = 10 ** (v["makeup"] / 20)
            out.append(f"acompressor=threshold={max(0.000976563, thr):.6f}:ratio={v['ratio']:.3f}:"
                       f"attack={v['attack']:.3f}:release={v['release']:.3f}:makeup={min(64, mk):.4f}")
        elif t == "reverb":
            # много коротких отражений; «сухой» сигнал остаётся громким, mix регулирует только хвост
            size, decay, mix = v["size"] / 100, v["decay"] / 100, v["mix"] / 100
            delays = [round(15 + 160 * size * k) for k in (1.0, 1.37, 1.73, 2.11, 2.53, 3.07)]
            decays = [round(max(0.001, mix * decay * f), 3) for f in (0.9, 0.78, 0.66, 0.55, 0.45, 0.36)]
            out.append(f"aecho=1.0:0.9:{'|'.join(map(str, delays))}:{'|'.join(map(str, decays))}")
        elif t == "delay":
            tm, fb, mix = v["time"], v["feedback"] / 100, v["mix"] / 100
            delays = [round(tm * k, 1) for k in (1, 2, 3, 4)]
            decays = [round(max(0.001, mix * fb ** (k - 1) if k > 1 else mix), 3) for k in (1, 2, 3, 4)]
            out.append(f"aecho=1.0:0.95:{'|'.join(map(str, delays))}:{'|'.join(map(str, decays))}")
        elif t == "pitch":
            if abs(v["semitones"]) > 0.001:
                out.append(f"rubberband=pitch={2 ** (v['semitones'] / 12):.6f}")
        elif t == "denoise":
            out.append(f"afftdn=nr={v['amount']:.2f}")
        elif t == "balance":
            out.append(f"stereotools=balance_out={max(-1.0, min(1.0, v['balance'] / 100)):.3f}")
    return out


# ---------------------------------------------------------------- декодирование
def decode(path: str, filters: list[str] | None = None) -> np.ndarray:
    """Весь звук файла -> float32 (N, 2) при 48 кГц, с фильтрами. Кэшируется."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return np.zeros((0, 2), np.float32)
    key = (path, mtime, tuple(filters or ()))
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-i", path, "-vn"]
    af = list(filters or []) + [f"aresample={SR}"]
    cmd += ["-af", ",".join(af), "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=600)
        data = np.frombuffer(r.stdout, np.float32)
    except Exception:
        data = np.zeros(0, np.float32)
    arr = data[: len(data) // 2 * 2].reshape(-1, 2).copy()
    with _cache_lock:
        _cache[key] = arr
        while len(_cache) > _CACHE_MAX:
            _cache.popitem(last=False)
    return arr


def peaks(path: str, per_sec: int = 100) -> np.ndarray:
    """Пики для волны на таймлайне: (N, 2) [min, max] моно, per_sec точек в секунду."""
    key = (path, per_sec)
    if key in _peaks:
        return _peaks[key]
    a = decode(path)
    if not len(a):
        res = np.zeros((0, 2), np.float32)
    else:
        mono = a.mean(axis=1)
        step = SR // per_sec
        n = len(mono) // step
        blk = mono[: n * step].reshape(n, step)
        res = np.stack([blk.min(axis=1), blk.max(axis=1)], axis=1)
    _peaks[key] = res
    return res


_beats: dict = {}


def beats(path: str) -> np.ndarray:
    """Удары в музыке (секунды): всплески энергии (spectral flux) с упором на бас и щелчки.
    Для меток на волне и прилипания клипов/ключей к битам."""
    if path in _beats:
        return _beats[path]
    a = decode(path)
    res = np.zeros(0)
    if len(a) > SR:
        mono = a.mean(axis=1)
        hop, win = SR // 100, 1024
        n = (len(mono) - win) // hop
        if n > 10:
            idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
            frames = mono[idx] * np.hanning(win)[None, :].astype(np.float32)
            spec = np.log1p(np.abs(np.fft.rfft(frames, axis=1)) * 10)
            fr = np.fft.rfftfreq(win, 1 / SR)
            wgt = np.where(fr < 200, 2.0, np.where(fr > 2000, 0.6, 1.0))   # бочка важнее, тарелки — меньше
            flux = np.maximum(0, np.diff(spec, axis=0)) @ wgt
            flux = np.concatenate([[0], flux])
            k = 25                                                      # адаптивный порог по окну ±0.25 с
            pad = np.pad(flux, k, mode="edge")
            loc = np.lib.stride_tricks.sliding_window_view(pad, 2 * k + 1)
            thr = loc.mean(axis=1) + 1.4 * loc.std(axis=1)
            is_peak = (flux > thr) & (flux >= loc.max(axis=1)) & (flux > flux.max() * 0.08)
            picks = np.nonzero(is_peak)[0]
            keep, last = [], -999
            for i in picks:          # не чаще 1 раза в 0.12 с
                if i - last >= 12:
                    keep.append(i)
                    last = i
            res = (np.array(keep) * hop + win / 2) / SR
    _beats[path] = res
    return res


# ---------------------------------------------------------------- сведение
def audible_layers(comp, _depth=0) -> list:
    solo = any(l.solo and l.visible for l in comp.layers if l.kind not in ("camera", "light"))
    out = []
    for l in comp.layers:
        if l.kind == "comp":
            nested = comp.find_comp(l.source)
            if not (l.visible and l.audio and nested is not None and _depth < 8 and audible_layers(nested, _depth + 1)):
                continue
        elif l.kind not in ("video", "audio") or not l.visible or not l.audio or not l.has_audio or not l.source:
            continue
        if solo and not l.solo:
            continue
        out.append(l)
    return out


def _gain_env(layer, f0: int, f1: int, fps: float, n: int) -> np.ndarray | float:
    """Множитель громкости по «Уровням звука» на каждый сэмпл отрезка [f0, f1)."""
    prop = layer.props.get("audio_levels")
    if prop is None:
        return 1.0
    if not prop.animated and not prop.has_expr:
        return 10 ** (prop.value_at(0)[0] / 20)
    frames = np.arange(f0, f1 + 1)
    db = np.array([prop.value_at(f - layer.start)[0] for f in frames], np.float32)
    g = np.power(10.0, db / 20.0)
    t = np.linspace(0, len(frames) - 1, n, endpoint=False, dtype=np.float32)
    return np.interp(t, np.arange(len(frames)), g).astype(np.float32)[:, None]


def mix(comp, f0: int, f1: int, _depth: int = 0) -> np.ndarray:
    """Сведённый звук кадров [f0, f1) композиции: float32 (N, 2). Прекомпозиции сводятся рекурсивно."""
    fps = comp.fps
    total = max(0, int(round((f1 - f0) / fps * SR)))
    out = np.zeros((total, 2), np.float32)
    for l in audible_layers(comp, _depth):
        s, e = max(l.in_point, f0), min(l.out_point, f1)
        if e <= s:
            continue
        sp = l.props.get("speed")
        # Time Remap (в т.ч. стоп-кадр) тоже меняет время исходника — иначе звук уходит от картинки
        if (sp is not None and (sp.animated or sp.has_expr or abs(sp.value[0] - 100) > 1e-6)) or l.reverse \
                or "time_remap" in l.props:
            _mix_retimed(out, comp, l, s, e, f0, total, _depth)
            continue
        if l.kind == "comp":
            nested = comp.find_comp(l.source)
            r = nested.fps / fps
            flt = fx_filters(l)
            if flt:   # звуковые эффекты на слое-прекомпе: сводим прекомп целиком, фильтруем, режем кусок
                full = _filtered_comp(nested, flt, _depth + 1)
                a0 = max(0, int(round((s - l.start) / fps * SR)))
                sub = full[a0:a0 + int(round((e - s) / fps * SR))]
            else:
                sub = mix(nested, int(round((s - l.start) * r)), int(round((e - l.start) * r)), _depth + 1)
            d0 = int(round((s - f0) / fps * SR))
            n = min(len(sub), total - d0)
            if n > 0:
                out[d0:d0 + n] += sub[:n] * _gain_env(l, s, e, fps, n)
            continue
        src = decode(l.source, fx_filters(l))
        if not len(src):
            continue
        a0 = int(round((s - l.start) / fps * SR))
        a1 = int(round((e - l.start) / fps * SR))
        d0 = int(round((s - f0) / fps * SR))
        a0 = max(0, a0)
        a1 = min(len(src), a1)
        n = min(a1 - a0, total - d0)
        if n <= 0:
            continue
        chunk = src[a0:a0 + n]
        g = _gain_env(l, s, e, fps, n)
        out[d0:d0 + n] += chunk * g
    return out


_comp_cache: OrderedDict = OrderedDict()


def _filtered_comp(nested, filters: list[str], depth: int) -> np.ndarray:
    """Весь звук прекомпа через фильтры ffmpeg (басы, эхо…). Кэш по содержимому прекомпа и фильтрам."""
    import hashlib
    import json
    key = (hashlib.sha1(json.dumps(nested.to_dict(), sort_keys=True, default=str).encode()).hexdigest(),
           tuple(filters))
    with _cache_lock:
        if key in _comp_cache:
            _comp_cache.move_to_end(key)
            return _comp_cache[key]
    raw = mix(nested, 0, nested.duration, depth).astype(np.float32)
    cmd = ["ffmpeg", "-v", "error", "-nostdin", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
           "-af", ",".join(list(filters) + [f"aresample={SR}"]), "-ac", "2", "-ar", str(SR), "-f", "f32le", "-"]
    try:
        r = subprocess.run(cmd, input=raw.tobytes(), capture_output=True, timeout=600)
        data = np.frombuffer(r.stdout, np.float32)
        out = data[: len(data) // 2 * 2].reshape(-1, 2).copy() if len(data) else raw
    except Exception:  # noqa — без фильтра лучше, чем тишина
        out = raw
    with _cache_lock:
        _comp_cache[key] = out
        while len(_comp_cache) > 4:
            _comp_cache.popitem(last=False)
    return out


def _mix_retimed(out, comp, l, s, e, f0, total, _depth):
    """Слой с другой скоростью/реверсом: берём исходник по карте времени (как плёнка — тон меняется)."""
    fps = comp.fps
    d0 = int(round((s - f0) / fps * SR))
    n = min(int(round((e - s) / fps * SR)), total - d0)
    if n <= 0:
        return
    frames = np.arange(s, e + 1)
    st = np.array([l.src_time(f - l.start, fps) for f in frames], np.float64)
    tt = np.interp(np.linspace(0, len(frames) - 1, n, endpoint=False), np.arange(len(frames)), st)
    if l.kind == "comp":
        nested = comp.find_comp(l.source)
        a = int(np.floor(tt.min() * nested.fps))
        b = int(np.ceil(tt.max() * nested.fps)) + 1
        src = mix(nested, max(0, a), b, _depth + 1)
        idx = (tt - max(0, a) / nested.fps) * SR
    else:
        src = decode(l.source, fx_filters(l))
        idx = tt * SR
    if not len(src):
        return
    xs = np.arange(len(src))
    chunk = np.stack([np.interp(idx, xs, src[:, c], left=0.0, right=0.0) for c in (0, 1)], axis=1).astype(np.float32)
    # время стоит (стоп-кадр, скорость 0) — тишина, как в AE, а не постоянный уровень одного сэмпла
    if len(tt) > 1:
        chunk[np.abs(np.gradient(tt)) * SR < 1e-3] = 0.0
    out[d0:d0 + n] += chunk * _gain_env(l, s, e, fps, n)


def level_db(x: float) -> float:
    return 20 * math.log10(max(1e-6, x))


# ---------------------------------------------------------------- проигрывание
class AudioPlayer:
    """Проигрывает сведение с кадра start через QAudioSink; время берётся по звуковым часам."""

    def __init__(self):
        self.sink = None
        self.buf = None
        self.data = None
        self.dev = None
        self.feeder = None
        self.hist = None
        self.start_frame = 0
        self.fps = 30.0
        self.ok = True
        self.volume = 1.0   # общая громкость превью

    def available(self) -> bool:
        if not self.ok:
            return False
        try:
            from PySide6.QtMultimedia import QMediaDevices
            return not QMediaDevices.defaultAudioOutput().isNull()
        except Exception:
            self.ok = False
            return False

    CHUNK_SEC = 0.1   # сводим кусками по 100 мс прямо во время проигрывания

    def play(self, comp, f0: int, f1: int) -> bool:
        """Старт мгновенный: звук сводится не весь сразу, а по кусочкам впереди курсора
        (раньше на длинной композиции перед стартом сводились минуты звука — секунды ожидания)."""
        self.stop()
        if not audible_layers(comp) or not self.available():
            return False
        from PySide6.QtCore import QTimer
        from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices
        fmt = QAudioFormat()
        fmt.setSampleRate(SR)
        fmt.setChannelCount(2)
        fmt.setSampleFormat(QAudioFormat.Float)
        self.comp, self.next_f, self.end_f = comp, f0, f1
        self.chunk_f = max(1, int(round(self.CHUNK_SEC * comp.fps)))
        self.pending = b""
        self.hist = np.zeros((0, 2), np.float32)   # недавно отданный звук — для индикатора громкости
        self.hist_start = 0                         # номер сэмпла начала hist от старта
        self.written = 0
        self.sink = QAudioSink(QMediaDevices.defaultAudioOutput(), fmt)
        self.sink.setBufferSize(SR // 10 * 8 * 3)   # ~300 мс в устройстве
        self.start_frame, self.fps = f0, comp.fps
        self.sink.setVolume(self.volume)
        self.dev = self.sink.start()
        self._feed()
        self.feeder = QTimer()
        self.feeder.setInterval(15)
        self.feeder.timeout.connect(self._feed)
        self.feeder.start()
        return True

    def _feed(self):
        if self.sink is None or self.dev is None:
            return
        free = self.sink.bytesFree()
        while free > 0:
            if not self.pending:
                if self.next_f >= self.end_f:
                    return
                f1 = min(self.end_f, self.next_f + self.chunk_f)
                blk = np.clip(mix(self.comp, self.next_f, f1), -1, 1).astype(np.float32)
                self.next_f = f1
                self.pending = blk.tobytes()
                self.hist = np.concatenate([self.hist, blk])
                if len(self.hist) > SR * 2:
                    cut = len(self.hist) - SR * 2
                    self.hist = self.hist[cut:]
                    self.hist_start += cut
            n = self.dev.write(self.pending[: free // 8 * 8])
            if n <= 0:
                return
            self.pending = self.pending[n:]
            self.written += n
            free -= n

    def stop(self):
        if getattr(self, "feeder", None) is not None:
            self.feeder.stop()
            self.feeder = None
        if self.sink is not None:
            self.sink.stop()
            self.sink = None
        self.dev = None
        if self.buf is not None:
            self.buf.close()
            self.buf = None

    @property
    def playing(self) -> bool:
        return self.sink is not None

    def elapsed(self) -> float:
        """Сколько секунд реально прозвучало."""
        if self.sink is None:
            return 0.0
        return self.sink.processedUSecs() / 1e6

    def current_frame(self) -> float:
        return self.start_frame + self.elapsed() * self.fps

    def levels(self) -> tuple[float, float]:
        """Пики L/R (0..1) около текущего момента — для индикатора громкости."""
        if self.sink is None or getattr(self, "hist", None) is None:
            return 0.0, 0.0
        i = int(self.elapsed() * SR) - self.hist_start
        blk = self.hist[max(0, i - 1200): max(0, i + 1200)]
        if not len(blk):
            return 0.0, 0.0
        m = np.abs(blk).max(axis=0)
        return float(m[0]), float(m[1])
