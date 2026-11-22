# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""3D-трекер камеры: решение (от track_worker.py) → сцена SFX и скрипт для After Effects.

Оси решения, SFX и After Effects совпадают: x вправо, y вниз, z вперёд (от камеры в сцену).
Сцена = решение после выравнивания: Xs = s · Ra · (X − O) + (W/2, H/2, 0)
  O — начало координат, Ra — поворот (плоскость земли → горизонталь, «вверх» = −y), s — масштаб.
"""
from __future__ import annotations

import json
import math
import os

import numpy as np


class Solution:
    def __init__(self, folder: str):
        self.folder = folder
        with open(os.path.join(folder, "solution.json")) as f:
            self.d = json.load(f)
        try:
            with open(os.path.join(folder, "obs.json")) as f:
                self.obs = json.load(f)
        except OSError:
            self.obs = []
        self.points = {p["id"]: (np.array(p["X"], float), p["err"]) for p in self.d["points"]}
        self.O = np.zeros(3)
        self.Ra = np.eye(3)
        self.s = self.default_scale()

    # ------------------------------------------------------------------ сведения
    @property
    def n(self) -> int:
        return self.d["n"]

    def quality(self) -> tuple[str, str]:
        """(оценка ru, en) по средней ошибке репроекции."""
        r = self.d["rms_px"]
        if r < 0.7:
            return "отлично", "excellent"
        if r < 1.5:
            return "хорошо", "good"
        if r < 3.0:
            return "удовлетворительно", "fair"
        return "плохо — проверьте видео и диапазон", "poor — check the footage and range"

    def cam(self, idx: int):
        """(R мир→камера 3x3, центр C) в координатах решения или None."""
        if idx < 0 or idx >= self.n:
            return None
        c = self.d["cams"][idx]
        if c is None:
            return None
        return np.array(c["R"]).reshape(3, 3), np.array(c["C"])

    def default_scale(self) -> float:
        """Масштаб по умолчанию: типичная глубина точек ≈ 1000 px сцены (удобно для слоёв AE/SFX)."""
        deps = []
        for i in range(0, self.n, max(1, self.n // 20)):
            c = self.cam(i)
            if c is None:
                continue
            R, C = c
            for X, _ in list(self.points.values())[:400]:
                z = (R @ (X - C))[2]
                if z > 0:
                    deps.append(z)
        return 1000.0 / float(np.median(deps)) if deps else 100.0

    # ------------------------------------------------------------------ выравнивание сцены
    def set_ground(self, ids: list[int]) -> bool:
        """Плоскость земли по ≥3 точкам: начало — их центр, «вверх» — нормаль в сторону камеры."""
        P = np.array([self.points[i][0] for i in ids if i in self.points])
        if len(P) < 3:
            return False
        c = P.mean(axis=0)
        _, _, vt = np.linalg.svd(P - c)
        nrm = vt[2]
        cams = [self.cam(i)[1] for i in range(self.n) if self.cam(i) is not None]
        if np.dot(np.mean(cams, axis=0) - c, nrm) < 0:
            nrm = -nrm                                  # нормаль смотрит туда, где камера (вверх)
        y = -nrm                                        # в SFX/AE ось y направлена вниз
        R0, _ = self.cam(next(i for i in range(self.n) if self.cam(i) is not None))
        x = R0[0] - np.dot(R0[0], y) * y                # «вправо» первой камеры, уложенное в плоскость
        x /= np.linalg.norm(x)
        z = np.cross(x, y)
        self.Ra = np.vstack([x, y, z])
        self.O = c
        return True

    def set_origin(self, pid: int) -> bool:
        if pid not in self.points:
            return False
        self.O = self.points[pid][0].copy()
        return True

    def set_distance(self, a: int, b: int, dist: float) -> bool:
        """Масштаб сцены: расстояние между двумя точками = dist единиц (пикселей SFX/AE)."""
        if a not in self.points or b not in self.points or dist <= 0:
            return False
        d = np.linalg.norm(self.points[a][0] - self.points[b][0])
        if d < 1e-9:
            return False
        self.s = dist / d
        return True

    def to_scene(self, X, w, h) -> np.ndarray:
        return self.s * (self.Ra @ (np.asarray(X, float) - self.O)) + np.array([w / 2, h / 2, 0.0])

    def cam_scene(self, idx, w, h):
        """(центр камеры, матрица камера→мир 3x3: столбцы right/down/forward) в координатах сцены."""
        c = self.cam(idx)
        if c is None:
            return None
        R, C = c
        M = (R @ self.Ra.T).T
        return self.to_scene(C, w, h), M

    # ------------------------------------------------------------------ SFX: камера и слои
    def zoom_for(self, comp_w: float, layer_scale: float = 100.0) -> float:
        """Zoom камеры SFX/AE = фокус в пикселях кадра композиции."""
        return self.d["f"] * comp_w / self.d["w"] * layer_scale / 100.0 if self.d["w"] else self.d["f"]

    @staticmethod
    def sfx_look(pos, M, dist):
        """Камера SFX задаётся позицией, точкой интереса и креном (как в three.camera_params)."""
        fwd = M[:, 2]
        poi = pos + fwd * dist
        right = np.cross(np.array([0.0, 1.0, 0.0]), fwd)
        if np.linalg.norm(right) < 1e-6:
            right = np.array([1.0, 0.0, 0.0])
        right /= np.linalg.norm(right)
        up = np.cross(fwd, right)
        r = M[:, 0]
        roll = math.degrees(math.atan2(float(r @ up), float(r @ right)))
        return poi, roll

    @staticmethod
    def ae_orientation(M) -> list[float]:
        """Orientation After Effects (градусы) для матрицы камера→мир M = Ry(y)·Rx(x)·Rz(z)
        (тот же порядок, что у выражения lookAt() в AE: x = −atan2(dy, √(dx²+dz²)), y = atan2(dx, dz))."""
        x = math.asin(max(-1.0, min(1.0, -M[1, 2])))
        y = math.atan2(M[0, 2], M[2, 2])
        z = math.atan2(M[1, 0], M[1, 1])
        return [math.degrees(x), math.degrees(y), math.degrees(z)]


def build_sfx_camera(ed, layer, sol: Solution, name: str):
    """Камера SFX с ключом на каждом кадре слоя (позиция, точка интереса, крен; zoom — константа)."""
    from .model import Keyframe, Layer
    comp = ed.comp
    w, h = comp.width, comp.height
    sx = layer.props["scale"].value_at(layer.local(layer.in_point))[0] if "scale" in layer.props else 100.0
    zoom = sol.zoom_for(w, sx)
    ed.snapshot()
    cam = Layer("camera", name, 0, 0, comp)
    cam.in_point = cam.start = layer.in_point
    cam.out_point = layer.out_point
    cam.props["zoom"].value = [round(zoom, 3)]
    vfps = sol.d["fps"]
    kp, kq, kr = [], [], []
    prev_roll = None
    for t in range(int(layer.in_point), int(layer.out_point)):
        st = layer.src_time(layer.local(t), comp.fps)
        idx = int(round(st * vfps)) - sol.d["start"]
        cs = sol.cam_scene(idx, w, h)
        if cs is None:
            continue
        pos, M = cs
        poi, roll = sol.sfx_look(pos, M, zoom)
        if prev_roll is not None:                       # без скачков на ±180°
            roll += 360.0 * round((prev_roll - roll) / 360.0)
        prev_roll = roll
        f = int(t - cam.start)
        kp.append(Keyframe(f, [float(v) for v in pos]))
        kq.append(Keyframe(f, [float(v) for v in poi]))
        kr.append(Keyframe(f, [float(roll)]))
    if not kp:
        return None
    cam.props["position"].keyframes = kp
    cam.props["poi"].keyframes = kq
    cam.props["rotation"].keyframes = kr
    cam.props["position"].value = list(kp[0].value)
    cam.props["poi"].value = list(kq[0].value)
    comp.layers.insert(0, cam)
    ed.sel_layers = [cam.id]
    ed.notify()
    ed.selection_changed.emit()
    return cam


def build_sfx_nulls(ed, layer, sol: Solution, ids: list[int], name_fmt: str):
    """3D-нули в выбранных точках (к ним можно привязать текст, солиды, фигуры через Parent)."""
    from .model import Layer
    comp = ed.comp
    ed.snapshot()
    out = []
    for k, pid in enumerate(ids):
        if pid not in sol.points:
            continue
        p = sol.to_scene(sol.points[pid][0], comp.width, comp.height)
        nl = Layer("null", name_fmt.format(k + 1), 100, 100, comp)
        nl.in_point = nl.start = layer.in_point
        nl.out_point = layer.out_point
        nl.threed = True
        nl.refresh_meta()
        nl.props["position"].value = [float(v) for v in p]
        comp.layers.insert(0, nl)
        out.append(nl)
    ed.notify()
    return out


def build_sfx_text_on_plane(ed, layer, sol: Solution, ids: list[int], text: str):
    """3D-текст, лежащий на плоскости земли в центре выбранных точек."""
    from .render import update_text_size
    comp = ed.comp
    P = [sol.points[i][0] for i in ids if i in sol.points]
    if not P:
        return None
    c = sol.to_scene(np.mean(P, axis=0), comp.width, comp.height)
    before = {x.id for x in comp.layers}
    ed.add_text()
    tl = next(x for x in comp.layers if x.id not in before)     # новый слой (вставляется над выделенным)
    tl.text = text
    tl.font_size = 120
    update_text_size(tl)
    tl.in_point = tl.start = layer.in_point
    tl.out_point = layer.out_point
    ed.set_3d([tl], True)
    tl.props["position"].value = [float(c[0]), float(c[1]), float(c[2])]
    tl.props["orientation"].value = [270.0, 0.0, 0.0]    # уложить на «пол» лицом вверх: верх текста — от камеры
    ed.notify()
    return tl


# ============================================================================ After Effects
def export_jsx(path: str, sol: Solution, comp_w: int, comp_h: int, fps: float, null_ids: list[int],
               cloud: int = 0, layer_scale: float = 100.0) -> int:
    """Скрипт для AE: в активной композиции создаёт 3D-камеру с ключами на каждом кадре,
    3D-нули в выбранных точках и (по желанию) облако точек. Кадр 0 решения = время OFFSET в композиции."""
    zoom = sol.zoom_for(comp_w, layer_scale)
    times, pos, ori = [], [], []
    prev = None
    for i in range(sol.n):
        cs = sol.cam_scene(i, comp_w, comp_h)
        if cs is None:
            continue
        p, M = cs
        o = sol.ae_orientation(M)
        if prev is not None:
            o = [v + 360.0 * round((pv - v) / 360.0) for v, pv in zip(o, prev)]
        prev = o
        times.append(round(i / sol.d["fps"], 6))
        pos.append([round(float(v), 4) for v in p])
        ori.append([round(v, 5) for v in o])
    nulls = [[round(float(v), 3) for v in sol.to_scene(sol.points[i][0], comp_w, comp_h)] for i in null_ids
             if i in sol.points]
    pts = []
    if cloud:
        good = sorted(sol.points.items(), key=lambda kv: kv[1][1])[:cloud]
        pts = [[round(float(v), 2) for v in sol.to_scene(X, comp_w, comp_h)] for _, (X, _) in good]
    data = {"times": times, "pos": pos, "ori": ori, "zoom": round(zoom, 4), "nulls": nulls, "cloud": pts,
            "rms": round(sol.d["rms_px"], 3), "fov": round(sol.d["fov_h"], 3)}
    js = """// SFX (Sound & Special Effects) — 3D Camera Tracker: экспорт в Adobe After Effects
// Запуск: File > Scripts > Run Script File… (в AE открыта композиция с этим видео, тот же размер кадра).
// Кадр 0 решения ставится на время OFFSET (сек) композиции. Ошибка решения: %(rms)s px, FOV %(fov)s°.
(function () {
    var OFFSET = 0;
    var D = %(data)s;
    var comp = app.project.activeItem;
    if (!(comp instanceof CompItem)) { alert("SFX: откройте композицию с видео и запустите скрипт снова."); return; }
    app.beginUndoGroup("SFX Camera Track");
    var cam = comp.layers.addCamera("SFX Tracked Camera", [comp.width / 2, comp.height / 2]);
    cam.autoOrient = AutoOrientType.NO_AUTO_ORIENT;
    var T = cam.property("ADBE Transform Group");
    var pos = T.property("ADBE Position"), ori = T.property("ADBE Orientation");
    var times = [];
    for (var i = 0; i < D.times.length; i++) times.push(D.times[i] + OFFSET);
    pos.setValuesAtTimes(times, D.pos);
    ori.setValuesAtTimes(times, D.ori);
    cam.property("ADBE Camera Options Group").property("ADBE Camera Zoom").setValue(D.zoom);
    for (var k = 0; k < D.nulls.length; k++) {
        var n = comp.layers.addNull(); n.threeDLayer = true; n.name = "SFX Track Null " + (k + 1);
        n.property("ADBE Transform Group").property("ADBE Position").setValue(D.nulls[k]);
    }
    if (D.cloud.length) {
        var c = comp.layers.addNull(); c.threeDLayer = true; c.name = "SFX Point Cloud (" + D.cloud.length + ")";
        c.property("ADBE Transform Group").property("ADBE Position").setValue([0, 0, 0]);
        for (var j = 0; j < D.cloud.length; j++) {
            var s = comp.layers.addSolid([0.2, 1, 0.4], "pt " + j, 6, 6, 1); s.threeDLayer = true; s.parent = c;
            s.property("ADBE Transform Group").property("ADBE Position").setValue(D.cloud[j]);
        }
    }
    app.endUndoGroup();
})();
""" % {"rms": data["rms"], "fov": data["fov"], "data": json.dumps(data)}
    with open(path, "w", encoding="utf-8") as f:
        f.write(js)
    return len(times)
