# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Выражения на JavaScript, как в After Effects.

Движок — QJSEngine (по одному на поток: превью и рендер считаются независимо).
Доступно: time, value, thisComp, thisLayer, thisProperty, index, transform.position и т. п.,
effect("Slider Control")("Slider"), wiggle, loopOut/loopIn, linear/ease/easeIn/easeOut, clamp,
random/seedRandom, add/sub/mul/div, length, normalize, degreesToRadians, radiansToDegrees,
timeToFrames/framesToTime, posterizeTime, valueAtTime, key(n), numKeys, Math.

Отличие от AE: у массивов в JS нет «+». Вместо value + [10, 0] пиши add(value, [10, 0]).
"""
from __future__ import annotations

import math
import threading
import zlib

from PySide6.QtCore import QObject, Slot

from .effects import EFFECTS, prop_key

MAX_DEPTH = 6

PRELUDE = r"""
var __B, time = 0, value = 0, thisLayer = null, index = 0, numKeys = 0, __seed = 0;
var __rs = 0, __rtimeless = false, __rready = false, __rstate = 0, __src = "";
function __arr(x){
  if (x === null || x === undefined || typeof x === 'number') return x;
  var out = []; for (var i = 0; i < x.length; i++) out.push(Number(x[i])); return out;
}
function __num(v){ v = __arr(v); return (v && v.length === 1) ? v[0] : v; }
function __prim(x){
  if (x instanceof Number || x instanceof Boolean) return x.valueOf();
  if (Array.isArray(x)) return x.map(__prim);
  return x;
}
function __wrap(v, li, key){
  var o = (typeof v === 'number') ? new Number(v) : v.slice();
  o.value = v;
  o.valueAtTime = function(t){ return __num(__B.prop(li, key, t)); };
  o.velocityAtTime = function(t){
    var a = __arr(__B.prop(li, key, t - 0.01)), b = __arr(__B.prop(li, key, t + 0.01));
    return __num(a.map(function(x, i){ return (b[i] - x) / 0.02; }));
  };
  o.speedAtTime = function(t){ var v = o.velocityAtTime(t); return (typeof v === 'number') ? Math.abs(v) : length(v); };
  o.numKeys = __B.numKeysOf(li, key);
  o.key = function(n){ var k = __B.keyOf(li, key, n); return {time: k.time, value: __num(k.value), index: n}; };
  return o;
}
function __p(li, key){ return __wrap(__num(__B.prop(li, key, time)), li, key); }
var __TR = {anchorPoint: 'anchor', position: 'position', scale: 'scale', rotation: 'rotation',
  zRotation: 'rotation', xRotation: 'rot_x', yRotation: 'rot_y', orientation: 'orientation',
  opacity: 'opacity', pointOfInterest: 'poi', zoom: 'zoom', audioLevels: 'audio_levels'};
function __layer(i){
  if (!i) throw new Error('layer not found');
  var L = {}, tr = {};
  Object.keys(__TR).forEach(function(n){
    var g = {get: function(){ return __p(i, __TR[n]); }, enumerable: true};
    Object.defineProperty(tr, n, g); Object.defineProperty(L, n, g);
  });
  var info = __B.layerInfo(i);
  L.transform = tr; L.index = i; L.name = info.name; L.inPoint = info.inPoint; L.outPoint = info.outPoint;
  L.startTime = info.startTime; L.width = info.width; L.height = info.height;
  L.effect = function(n){
    var fid = __B.fxId(i, n);
    if (!fid) throw new Error('effect not found: ' + n);
    return function(p){
      var k = __B.fxParam(i, fid, p);
      if (!k) throw new Error('effect parameter not found: ' + p);
      return __p(i, k);
    };
  };
  return L;
}
var thisComp = {layer: function(n){ return __layer(__B.layerIndex(n)); }};
var thisProperty = {
  valueAtTime: function(t){ return __num(__B.raw(t)); },
  key: function(n){ var k = __B.keyOf(-1, '', n); return {time: k.time, value: __num(k.value), index: n}; }
};
function key(n){ return thisProperty.key(n); }
function valueAtTime(t){ return thisProperty.valueAtTime(t); }
function loopOut(type, n){ return __num(__B.loop(type || 'cycle', n || 0, true, time)); }
function loopIn(type, n){ return __num(__B.loop(type || 'cycle', n || 0, false, time)); }
function loopOutDuration(type, d){ return loopOut(type, 0); }
function loopInDuration(type, d){ return loopIn(type, 0); }
function __map(a, b, f){
  if (Array.isArray(a) || Array.isArray(b)){
    var A = Array.isArray(a) ? a : null, B = Array.isArray(b) ? b : null;
    var n = Math.max(A ? A.length : 0, B ? B.length : 0), r = [];
    for (var i = 0; i < n; i++) r.push(f(A ? (A[i] || 0) : a, B ? (B[i] || 0) : b));
    return r;
  }
  return f(a, b);
}
function add(a, b){ return __map(__prim(a), __prim(b), function(x, y){ return x + y; }); }
function sub(a, b){ return __map(__prim(a), __prim(b), function(x, y){ return x - y; }); }
function mul(a, b){ return __map(__prim(a), __prim(b), function(x, y){ return x * y; }); }
function div(a, b){ return __map(__prim(a), __prim(b), function(x, y){ return x / y; }); }
function clamp(v, lo, hi){ return __map(__map(__prim(v), __prim(lo), Math.max), __prim(hi), Math.min); }
function length(a, b){
  a = __prim(a); if (b !== undefined) a = sub(a, b);
  if (!Array.isArray(a)) return Math.abs(a);
  return Math.sqrt(a.reduce(function(s, x){ return s + x * x; }, 0));
}
function normalize(a){ var l = length(a); return l ? div(a, l) : a; }
function dot(a, b){ a = __prim(a); b = __prim(b); var s = 0; for (var i = 0; i < a.length; i++) s += a[i] * b[i]; return s; }
function cross(a, b){ return [a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2], a[0]*b[1]-a[1]*b[0]]; }
function degreesToRadians(d){ return d * Math.PI / 180; }
function radiansToDegrees(r){ return r * 180 / Math.PI; }
function timeToFrames(t, fps){ if (t === undefined) t = time; return Math.round(t * (fps || 1 / thisComp.frameDuration)); }
function framesToTime(f, fps){ return f / (fps || 1 / thisComp.frameDuration); }
function __interp(t, a, b, c, d, fn){
  if (c === undefined){ c = a; d = b; a = 0; b = 1; }
  var u = (b === a) ? 1 : (t - a) / (b - a);
  u = fn(Math.max(0, Math.min(1, u)));
  c = __prim(c); d = __prim(d);
  return __map(c, d, function(x, y){ return x + (y - x) * u; });
}
function linear(t, a, b, c, d){ return __interp(t, a, b, c, d, function(u){ return u; }); }
function ease(t, a, b, c, d){ return __interp(t, a, b, c, d, function(u){ return u * u * (3 - 2 * u); }); }
function easeIn(t, a, b, c, d){ return __interp(t, a, b, c, d, function(u){ return u * u; }); }
function easeOut(t, a, b, c, d){ return __interp(t, a, b, c, d, function(u){ return 1 - (1 - u) * (1 - u); }); }
function __hash(i, s){ var x = Math.sin(i * 127.1 + s * 311.7) * 43758.5453; return (x - Math.floor(x)) * 2 - 1; }
function __vnoise(x, s){ var i = Math.floor(x), f = x - i, u = f * f * (3 - 2 * f); return __hash(i, s) * (1 - u) + __hash(i + 1, s) * u; }
function noise(x){ return __vnoise(Array.isArray(x) ? x[0] : x, 0.5); }
function wiggle(freq, amp, octaves, ampMult, t){
  octaves = octaves || 1; ampMult = (ampMult === undefined) ? 0.5 : ampMult; t = (t === undefined) ? time : t;
  var v = __prim(value), isNum = !Array.isArray(v), arr = isNum ? [v] : v.slice();
  for (var d = 0; d < arr.length; d++){
    var s = 0, a = 1, fr = freq;
    for (var o = 0; o < octaves; o++){ s += __vnoise(t * fr, __seed + d * 17.13 + o * 3.7) * a; a *= ampMult; fr *= 2; }
    arr[d] += amp * s;
  }
  return isNum ? arr[0] : arr;
}
function __rinit(){
  __rstate = (__seed * 7919 + __rs * 104729 + (__rtimeless ? 0 : Math.round(time * 1000)) * 15485863) | 0;
  __rstate ^= 0x9E3779B9; __rready = true;
}
function __rnd(){
  if (!__rready) __rinit();
  __rstate = (__rstate + 0x6D2B79F5) | 0;
  var t = Math.imul(__rstate ^ (__rstate >>> 15), 1 | __rstate);
  t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
  return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
}
function seedRandom(s, timeless){ __rs = s; __rtimeless = !!timeless; __rready = false; }
function random(a, b){
  if (a === undefined) return __rnd();
  if (b === undefined){ b = a; a = Array.isArray(b) ? b.map(function(){ return 0; }) : 0; }
  return __map(__prim(a), __prim(b), function(x, y){ return x + (y - x) * __rnd(); });
}
function gaussRandom(a, b){
  var u = Math.max(1e-9, __rnd()), v = __rnd();
  var g = Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v) / 6 + 0.5;
  if (a === undefined) return g;
  if (b === undefined){ b = a; a = 0; }
  return __map(__prim(a), __prim(b), function(x, y){ return x + (y - x) * g; });
}
function posterizeTime(fps){ time = Math.floor(time * fps + 1e-6) / fps; value = __num(__B.raw(time)); }
function __run(){
  var c = __B.ctx();
  time = c.time; value = __num(c.value); index = c.index; numKeys = c.numKeys; __seed = c.seed;
  __rs = 0; __rtimeless = false; __rready = false;
  thisComp.width = c.width; thisComp.height = c.height; thisComp.duration = c.duration;
  thisComp.frameDuration = c.frameDuration; thisComp.numLayers = c.numLayers; thisComp.name = c.compName;
  thisLayer = __layer(c.index);
  thisProperty.value = value; thisProperty.numKeys = c.numKeys;
  return __prim(eval("with (thisLayer) {\n" + c.src + "\n}"));
}
"""


def _find_layer(comp, ref):
    if isinstance(ref, (int, float)) and not isinstance(ref, bool):
        i = int(ref)
        return i if 1 <= i <= len(comp.layers) else 0
    if isinstance(ref, str):
        for i, l in enumerate(comp.layers):
            if l.name == ref:
                return i + 1
    return 0


class _Bridge(QObject):
    def __init__(self):
        super().__init__()
        self.stack: list[tuple] = []   # (comp, layer, prop, local_frame)

    # --- контекст текущего вычисления
    def _top(self):
        return self.stack[-1]

    @Slot(result="QVariant")
    def ctx(self):
        comp, layer, prop, lf = self._top()
        return {
            "time": (lf + layer.start) / comp.fps,
            "value": prop.raw_at(lf),
            "index": comp.layers.index(layer) + 1,
            "numKeys": len(prop.keyframes),
            "seed": (comp.layers.index(layer) + 1) * 131 + zlib.crc32(prop.key.encode()) % 997,
            "src": prop.expr,
            "width": comp.width, "height": comp.height,
            "duration": comp.duration / comp.fps, "frameDuration": 1.0 / comp.fps,
            "numLayers": len(comp.layers), "compName": comp.name,
        }

    @Slot("QVariant", result=int)
    def layerIndex(self, ref):
        return _find_layer(self._top()[0], ref)

    @Slot(int, result="QVariant")
    def layerInfo(self, i):
        comp = self._top()[0]
        l = comp.layers[i - 1]
        fps = comp.fps
        x, y, w, h = l.local_rect()
        return {"name": l.name, "inPoint": l.in_point / fps, "outPoint": l.out_point / fps,
                "startTime": l.start / fps, "width": w, "height": h}

    @Slot(int, str, float, result="QVariantList")
    def prop(self, i, key, t):
        comp = self._top()[0]
        if not 1 <= i <= len(comp.layers):
            return [0.0]
        l = comp.layers[i - 1]
        p = l.props.get(key)
        if p is None:
            return [0.0]
        return [float(v) for v in p.value_at(t * comp.fps - l.start)]

    @Slot(float, result="QVariantList")
    def raw(self, t):
        comp, layer, prop, _ = self._top()
        return prop.raw_at(t * comp.fps - layer.start)

    def _prop_of(self, i, key):
        comp, layer, prop, _ = self._top()
        if i == -1:
            return layer, prop
        if not 1 <= i <= len(comp.layers):
            return None, None
        l = comp.layers[i - 1]
        return l, l.props.get(key)

    @Slot(int, str, result=int)
    def numKeysOf(self, i, key):
        _, p = self._prop_of(i, key)
        return len(p.keyframes) if p else 0

    @Slot(int, str, int, result="QVariant")
    def keyOf(self, i, key, n):
        comp = self._top()[0]
        l, p = self._prop_of(i, key)
        if p is None or not 1 <= n <= len(p.keyframes):
            raise_js = {"time": 0.0, "value": [0.0]}
            return raise_js
        k = p.keyframes[n - 1]
        return {"time": (k.frame + l.start) / comp.fps, "value": list(k.value)}

    @Slot(int, "QVariant", result=str)
    def fxId(self, i, ref):
        comp = self._top()[0]
        if not 1 <= i <= len(comp.layers):
            return ""
        l = comp.layers[i - 1]
        if isinstance(ref, (int, float)) and not isinstance(ref, bool):
            n = int(ref)
            return l.effects[n - 1]["id"] if 1 <= n <= len(l.effects) else ""
        for fx in l.effects:
            d = EFFECTS.get(fx["type"])
            names = {fx.get("name", "")}
            if d:
                names |= {d.ru, d.en}
            if ref in names:
                return fx["id"]
        return ""

    @Slot(int, str, "QVariant", result=str)
    def fxParam(self, i, fid, ref):
        comp = self._top()[0]
        l = comp.layers[i - 1]
        fx = l.effect_by_id(fid)
        d = EFFECTS.get(fx["type"]) if fx else None
        if not d:
            return ""
        if isinstance(ref, (int, float)) and not isinstance(ref, bool):
            n = int(ref)
            return prop_key(fid, d.params[n - 1].key) if 1 <= n <= len(d.params) else ""
        for prm in d.params:
            if ref in (prm.ru, prm.en, prm.key):
                return prop_key(fid, prm.key)
        return ""

    @Slot(str, int, bool, float, result="QVariantList")
    def loop(self, kind, n, out, t):
        comp, layer, prop, _ = self._top()
        f = t * comp.fps - layer.start
        return loop_value(prop, f, kind, n, out)


def loop_value(prop, f: float, kind: str, n: int, out: bool) -> list[float]:
    ks = prop.keyframes
    if len(ks) < 2:
        return prop.raw_at(f)
    if out:
        a = ks[max(0, len(ks) - 1 - n)].frame if n > 0 else ks[0].frame
        b = ks[-1].frame
        if f <= b:
            return prop.raw_at(f)
        over = f - b
    else:
        a = ks[0].frame
        b = ks[min(len(ks) - 1, n)].frame if n > 0 else ks[-1].frame
        if f >= a:
            return prop.raw_at(f)
        over = a - f
    dur = b - a
    if dur <= 0:
        return prop.raw_at(f)
    cyc, ph = divmod(over, dur)
    va, vb = prop.raw_at(a), prop.raw_at(b)
    kind = (kind or "cycle").lower()
    if kind == "pingpong":
        if out:
            g = b - ph if int(cyc) % 2 == 0 else a + ph
        else:
            g = a + ph if int(cyc) % 2 == 0 else b - ph
        return prop.raw_at(g)
    if kind == "continue":
        e = 0.5
        if out:
            vel = [(x - y) / e for x, y in zip(vb, prop.raw_at(b - e))]
            return [v + s * over for v, s in zip(vb, vel)]
        vel = [(y - x) / e for x, y in zip(va, prop.raw_at(a + e))]
        return [v - s * over for v, s in zip(va, vel)]
    g = (a + ph) if out else (b - ph)
    base = prop.raw_at(g)
    if kind == "offset":
        k = cyc + 1
        delta = [y - x for x, y in zip(va, vb)]
        return [v + d * k for v, d in zip(base, delta)] if out else [v - d * k for v, d in zip(base, delta)]
    return base  # cycle


_local = threading.local()


def _state():
    st = getattr(_local, "st", None)
    if st is None:
        from PySide6.QtQml import QJSEngine
        eng = QJSEngine()
        br = _Bridge()
        eng.globalObject().setProperty("__B", eng.newQObject(br))
        res = eng.evaluate(PRELUDE)
        if res.isError():
            raise RuntimeError(res.toString())
        run = eng.globalObject().property("__run")
        st = _local.st = (eng, br, run)
    return st


def _to_values(v, raw: list[float]) -> list[float]:
    n = len(raw)
    if isinstance(v, bool):
        v = 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        if not math.isfinite(v):
            raise ValueError("NaN")
        return [float(v)] * n
    if isinstance(v, (list, tuple)):
        out = []
        for x in v:
            if isinstance(x, bool):
                x = 1.0 if x else 0.0
            if not isinstance(x, (int, float)) or not math.isfinite(x):
                raise ValueError("bad array item")
            out.append(float(x))
        return (out + raw[len(out):])[:n] if out else list(raw)
    if isinstance(v, str):
        raise ValueError("string result — arrays can't be added with '+', use add(a, b)")
    raise ValueError(f"unsupported result: {type(v).__name__}")


def evaluate(prop, local_frame: float, raw: list[float]) -> list[float]:
    layer = prop._layer
    comp = getattr(layer, "_comp", None) if layer is not None else None
    if comp is None or layer not in comp.layers:
        return raw
    try:
        eng, br, run = _state()
    except Exception as e:  # noqa
        prop.expr_error = str(e)
        return raw
    if len(br.stack) >= MAX_DEPTH:
        prop.expr_error = "expression recursion"
        return raw
    br.stack.append((comp, layer, prop, local_frame))
    try:
        res = run.call()
        if res.isError():
            prop.expr_error = res.toString()
            return raw
        out = _to_values(res.toVariant(), raw)
        prop.expr_error = ""
        return out
    except Exception as e:  # noqa
        prop.expr_error = str(e)
        return raw
    finally:
        br.stack.pop()


def check(prop, local_frame: float) -> str:
    """Проверить выражение сейчас; вернуть текст ошибки или пустую строку."""
    prop.value_at(local_frame)
    return prop.expr_error


def reference(from_layer, to_layer, key: str) -> str:
    """Текст ссылки для «пики» (pickwhip): на свойство своего или чужого слоя."""
    names = {"anchor": "transform.anchorPoint", "position": "transform.position", "scale": "transform.scale",
             "rotation": "transform.rotation", "opacity": "transform.opacity", "orientation": "transform.orientation",
             "rot_x": "transform.xRotation", "rot_y": "transform.yRotation", "poi": "transform.pointOfInterest",
             "zoom": "transform.zoom", "audio_levels": "audioLevels"}
    if key in names:
        tail = names[key]
    elif key.startswith("fx/"):
        _, fid, pname = key.split("/", 2)
        fx = to_layer.effect_by_id(fid)
        d = EFFECTS.get(fx["type"]) if fx else None
        if not d:
            return ""
        prm = next((p for p in d.params if p.key == pname), None)
        fx_name = fx.get("name") or d.en
        tail = f'effect("{fx_name}")("{prm.en if prm else pname}")'
    else:
        return ""
    if to_layer is from_layer:
        return tail
    return f'thisComp.layer("{to_layer.name}").{tail}'
