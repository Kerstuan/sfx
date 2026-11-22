# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Движок 3D-трекера камеры SFX — отдельный процесс в окружении ~/sfx/.venv-track (OpenCV + SciPy).

Свой конвейер Structure-from-Motion для видео (встроенный Camera Tracker Adobe не используется):
  1. точки: Shi-Tomasi + пирамидальный Лукас–Канаде, проверка «вперёд-назад», отсев по RANSAC (F-матрица);
  2. фокус: по F-матрицам нескольких пар кадров (критерий Мендонсы–Чиполлы: у E = KᵀFK
     два ненулевых сингулярных числа должны совпадать) — затем уточняется в bundle adjustment;
  3. старт: пара кадров с наибольшим параллаксом → E-матрица → поза → триангуляция;
  4. остальные кадры: PnP + RANSAC по уже найденным 3D-точкам, триангуляция новых точек;
  5. bundle adjustment (SciPy least_squares, разреженный якобиан, функция потерь Хьюбера) — все позы,
     все точки и фокус вместе; отсев наблюдений с ошибкой > порога; повтор.
Если параллакса нет (штатив/панорама) — решения нет: возвращается ошибка "parallax"
(режим «только поворот»/nodal ещё не реализован, поле "nodal" в ответе всегда false).

Запуск: python track_worker.py '<json>'   (video, out, start, end, track_w, corners, min_dist, err_px)
Пишет строки JSON в stdout: {"progress":k,"of":n,"stage":...} … {"ok":true,"rms":…} | {"error":…}
Результат: <out>/solution.json (+ <out>/obs.json — 2D-наблюдения решённых точек для отрисовки).
Оси: x вправо, y вниз, z вперёд (как у OpenCV, After Effects и 3D в SFX).
"""
from __future__ import annotations

import json
import math
import os
import sys
import time

import cv2
import numpy as np
from scipy.optimize import least_squares
from scipy.sparse import lil_matrix


def send(**kw):
    sys.stdout.write(json.dumps(kw) + "\n")
    sys.stdout.flush()


# ============================================================================ 1. кадры и точки
def decode(video, start, end, track_w):
    import av
    c = av.open(video)
    st = c.streams.video[0]
    st.thread_type = "AUTO"
    fps = float(st.average_rate or st.guessed_rate or 30)
    if start > 0:
        c.seek(int(start / fps / st.time_base), stream=st, backward=True)
    frames, full = [], None
    total = max(1, end - start)
    for fr in c.decode(st):
        idx = int(round(float(fr.pts * st.time_base) * fps)) if fr.pts is not None else start + len(frames)
        if idx < start:
            continue
        if idx >= end:
            break
        g = fr.to_ndarray(format="gray")
        if (fr.rotation or 0) % 360:   # вертикальное видео с телефона — как в превью (media.rotated)
            g = np.ascontiguousarray(np.rot90(g, (fr.rotation // 90) % 4))
        full = g.shape
        s = track_w / g.shape[1]
        if s < 1.0:
            g = cv2.resize(g, (track_w, int(round(g.shape[0] * s))), interpolation=cv2.INTER_AREA)
        frames.append(g)
        if len(frames) % 20 == 0:
            send(progress=len(frames), of=total, stage="decode")
    c.close()
    return frames, fps, full


def track_points(frames, corners, min_dist):
    """Треки точек: obs[f] = (ids int32[N], xy float32[N,2]) — в разрешении трекинга."""
    lk = dict(winSize=(21, 21), maxLevel=4, criteria=(cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 30, 0.01))
    h, w = frames[0].shape
    next_id = 0
    obs = []
    pts = np.zeros((0, 2), np.float32)
    ids = np.zeros(0, np.int32)

    def replenish(img, pts, ids, next_id):
        need = corners - len(pts)
        if need < corners * 0.15:
            return pts, ids, next_id
        mask = np.full((h, w), 255, np.uint8)
        for x, y in pts:
            cv2.circle(mask, (int(x), int(y)), int(min_dist), 0, -1)
        new = cv2.goodFeaturesToTrack(img, maxCorners=need, qualityLevel=0.008, minDistance=min_dist,
                                      mask=mask, blockSize=7)
        if new is not None:
            new = new.reshape(-1, 2).astype(np.float32)
            pts = np.vstack([pts, new])
            ids = np.concatenate([ids, np.arange(next_id, next_id + len(new), dtype=np.int32)])
            next_id += len(new)
        return pts, ids, next_id

    pts, ids, next_id = replenish(frames[0], pts, ids, next_id)
    obs.append((ids.copy(), pts.copy()))
    for f in range(1, len(frames)):
        a, b = frames[f - 1], frames[f]
        if len(pts):
            p1, st1, _ = cv2.calcOpticalFlowPyrLK(a, b, pts, None, **lk)
            p0, st0, _ = cv2.calcOpticalFlowPyrLK(b, a, p1, None, **lk)
            fb = np.linalg.norm(p0 - pts, axis=1)
            good = (st1.ravel() == 1) & (st0.ravel() == 1) & (fb < 0.7)
            good &= (p1[:, 0] >= 0) & (p1[:, 0] < w) & (p1[:, 1] >= 0) & (p1[:, 1] < h)
            if good.sum() >= 12:   # движение должно согласовываться с одной камерой (F-матрица, RANSAC)
                _, inl = cv2.findFundamentalMat(pts[good], p1[good], cv2.FM_RANSAC, 1.0, 0.999)
                if inl is not None:
                    gi = np.nonzero(good)[0]
                    good[gi[inl.ravel() == 0]] = False
            pts, ids = p1[good].astype(np.float32), ids[good]
        pts, ids, next_id = replenish(b, pts, ids, next_id)
        obs.append((ids.copy(), pts.copy()))
        if f % 10 == 0:
            send(progress=f, of=len(frames) - 1, stage="track")
    return obs


def drop_screen_locked(obs, min_len=20):
    """Убрать треки, которые стоят на месте в кадре, пока сцена движется: интерфейс игры (HUD),
    логотипы, титры, пятна на объективе. Они не принадлежат 3D-сцене и ломают решение."""
    tr = build_tracks(obs, min_len=2)
    scene = []
    for i, (fr, xy) in tr.items():
        if len(fr) >= 2:
            scene.append(np.median(np.linalg.norm(np.diff(xy, axis=0), axis=1)))
    if not scene:
        return obs, 0
    typical = float(np.median(scene))               # типичный сдвиг точки за кадр
    if typical < 0.3:                               # камера почти не двигается — не трогаем
        return obs, 0
    locked = set()
    for i, (fr, xy) in tr.items():
        if len(fr) >= min_len:
            span = np.linalg.norm(xy.max(axis=0) - xy.min(axis=0))
            if span < max(1.0, typical * 0.15) * 3:
                locked.add(i)
    out = []
    for ids, xy in obs:
        keep = np.array([i not in locked for i in ids.tolist()], bool) if len(ids) else np.zeros(0, bool)
        out.append((ids[keep], xy[keep]))
    return out, len(locked)


def build_tracks(obs, min_len=3):
    """id трека -> (кадры[], xy[]) — для триангуляции и BA."""
    tr = {}
    for f, (ids, xy) in enumerate(obs):
        for i, p in zip(ids.tolist(), xy):
            tr.setdefault(i, ([], []))
            tr[i][0].append(f)
            tr[i][1].append(p)
    return {k: (np.array(v[0]), np.array(v[1], np.float32)) for k, v in tr.items() if len(v[0]) >= min_len}


def pair_matches(obs, a, b):
    ia, xa = obs[a]
    ib, xb = obs[b]
    common, ka, kb = np.intersect1d(ia, ib, return_indices=True)
    return common, xa[ka], xb[kb]


# ============================================================================ 2. фокус
def estimate_focal(obs, w, h, n):
    """Фокус в пикселях трекинга (критерий Мендонсы–Чиполлы: у E = KᵀFK два ненулевых сингулярных
    числа равны). Пары кадров с разносом 15/25/40, стоимость пары нормирована и взвешена числом
    точек-«инлайеров». Проверено на синтетике с известной камерой (640/960 px) и на игровом видео
    с известным FOV: ошибка 1–3% (сумма без весов и медиана давали до 7% и 18%)."""
    pairs = []
    for gap in (15, 25, 40):
        for a in range(0, max(1, n - gap), max(4, n // 16)):
            if a + gap < n:
                pairs.append((a, a + gap))
    cands = np.geomspace(0.35 * w, 4.0 * w, 300)
    Ks = [np.array([[f, 0, w / 2], [0, f, h / 2], [0, 0, 1]]) for f in cands]
    total = np.zeros(len(cands))
    used = 0
    for a, b in pairs:
        _, xa, xb = pair_matches(obs, a, b)
        if len(xa) < 40 or np.median(np.linalg.norm(xa - xb, axis=1)) < 4.0:
            continue
        F, inl = cv2.findFundamentalMat(xa, xb, cv2.FM_RANSAC, 1.0, 0.999)
        if F is None or F.shape != (3, 3) or inl is None or inl.sum() < 30:
            continue
        for k, K in enumerate(Ks):
            sv = np.linalg.svd(K.T @ F @ K, compute_uv=False)
            total[k] += (sv[0] - sv[1]) / max(sv[0], 1e-12) * float(inl.sum())
        used += 1
    if used < 2:
        return None
    return float(cands[int(np.argmin(total))])


# ============================================================================ 3–5. реконструкция
def rodrigues_rotate(rv, X):
    """Повернуть точки X (N,3) векторами поворота rv (N,3) — векторизовано (формула Родрига)."""
    th = np.linalg.norm(rv, axis=1, keepdims=True)
    with np.errstate(invalid="ignore", divide="ignore"):
        k = np.where(th > 1e-12, rv / th, 0.0)
    c, s = np.cos(th), np.sin(th)
    dot = np.sum(k * X, axis=1, keepdims=True)
    return X * c + np.cross(k, X) * s + k * dot * (1 - c)


def project(rv, tv, X, f, cx, cy):
    Xc = rodrigues_rotate(rv, X) + tv
    z = Xc[:, 2:3]
    return np.hstack([f * Xc[:, 0:1] / z + cx, f * Xc[:, 1:2] / z + cy]), z[:, 0]


def bundle_adjust(poses, pts3d, observations, f, cx, cy, fixed, refine_f=True, iters=40):
    """poses: {кадр: (rv, tv)}, pts3d: {id: X}, observations: список (кадр, id, x, y).
    Оптимизирует всё, кроме позы fixed (задаёт систему координат); фокус — если refine_f."""
    frames = sorted(k for k in poses if k != fixed)
    pids = sorted(pts3d)
    fi = {k: i for i, k in enumerate(frames)}
    pi = {k: i for i, k in enumerate(pids)}
    obs = [(fr, pid, x, y) for fr, pid, x, y in observations if pid in pi and (fr in fi or fr == fixed)]
    nf, npnt, no = len(frames), len(pids), len(obs)
    if no < 20 or npnt < 8:
        return poses, pts3d, f
    o_f = np.array([fi.get(o[0], -1) for o in obs])
    o_p = np.array([pi[o[1]] for o in obs])
    o_xy = np.array([[o[2], o[3]] for o in obs], np.float64)
    fixed_rt = np.concatenate(poses[fixed]) if fixed in poses else np.zeros(6)
    x0 = np.concatenate([np.concatenate([np.concatenate(poses[k]) for k in frames]) if nf else np.zeros(0),
                         np.concatenate([pts3d[k] for k in pids]), [f] if refine_f else []])

    def unpack(x):
        cam = x[:nf * 6].reshape(nf, 6)
        P = x[nf * 6:nf * 6 + npnt * 3].reshape(npnt, 3)
        ff = x[-1] if refine_f else f
        return cam, P, ff

    def resid(x):
        cam, P, ff = unpack(x)
        rt = np.where((o_f >= 0)[:, None], cam[np.maximum(o_f, 0)], fixed_rt)
        uv, z = project(rt[:, :3], rt[:, 3:], P[o_p], ff, cx, cy)
        r = (uv - o_xy)
        r[z <= 1e-6] = 50.0                     # точка за камерой — большой штраф
        return r.ravel()

    A = lil_matrix((no * 2, len(x0)), dtype=np.int8)
    rows = np.arange(no)
    for k in range(6):
        sel = o_f >= 0
        A[2 * rows[sel], o_f[sel] * 6 + k] = 1
        A[2 * rows[sel] + 1, o_f[sel] * 6 + k] = 1
    for k in range(3):
        A[2 * rows, nf * 6 + o_p * 3 + k] = 1
        A[2 * rows + 1, nf * 6 + o_p * 3 + k] = 1
    if refine_f:
        A[:, -1] = 1
    res = least_squares(resid, x0, jac_sparsity=A, x_scale="jac", loss="huber", f_scale=1.5,
                        method="trf", max_nfev=iters, ftol=1e-6, xtol=1e-8)
    cam, P, ff = unpack(res.x)
    poses = dict(poses)
    for k, i in fi.items():
        poses[k] = (cam[i, :3].copy(), cam[i, 3:].copy())
    pts3d = {k: P[i].copy() for k, i in pi.items()}
    return poses, pts3d, float(ff)


def triangulate(K, pa, pb, xa, xb):
    """Триангуляция + проверки: перед обеими камерами, угол между лучами, ошибка репроекции."""
    Ra, _ = cv2.Rodrigues(pa[0])
    Rb, _ = cv2.Rodrigues(pb[0])
    Pa = K @ np.hstack([Ra, pa[1].reshape(3, 1)])
    Pb = K @ np.hstack([Rb, pb[1].reshape(3, 1)])
    Xh = cv2.triangulatePoints(Pa, Pb, xa.T.astype(np.float64), xb.T.astype(np.float64))
    X = (Xh[:3] / Xh[3]).T
    Ca, Cb = -Ra.T @ pa[1], -Rb.T @ pb[1]
    da, db = X - Ca, X - Cb
    cosang = np.sum(da * db, axis=1) / (np.linalg.norm(da, axis=1) * np.linalg.norm(db, axis=1) + 1e-12)
    za = (X @ Ra.T + pa[1])[:, 2]
    zb = (X @ Rb.T + pb[1])[:, 2]
    f, cx, cy = K[0, 0], K[0, 2], K[1, 2]
    ua, _ = project(np.repeat(pa[0][None], len(X), 0), np.repeat(pa[1][None], len(X), 0), X, f, cx, cy)
    ub, _ = project(np.repeat(pb[0][None], len(X), 0), np.repeat(pb[1][None], len(X), 0), X, f, cx, cy)
    err = np.maximum(np.linalg.norm(ua - xa, axis=1), np.linalg.norm(ub - xb, axis=1))
    ok = (za > 0) & (zb > 0) & (cosang < math.cos(math.radians(1.0))) & (err < 3.0)
    return X, ok


def solve(obs, w, h, f0, err_px):
    n = len(obs)
    tracks = build_tracks(obs)
    cx, cy = w / 2.0, h / 2.0
    K = np.array([[f0, 0, cx], [0, f0, cy], [0, 0, 1.0]])
    # --- стартовая пара: максимум «хороших» триангулированных точек с заметным параллаксом
    best = None
    for a in range(0, max(1, n - 8), max(3, n // 25)):
        for gap in (8, 15, 25, 40, 60, 90):
            b = a + gap
            if b >= n:
                break
            common, xa, xb = pair_matches(obs, a, b)
            if len(common) < 40:
                continue
            E, inl = cv2.findEssentialMat(xa, xb, K, cv2.RANSAC, 0.999, 1.0)
            if E is None or E.shape != (3, 3):
                continue
            _, R, t, inl2 = cv2.recoverPose(E, xa, xb, K, mask=inl)
            pa = (np.zeros(3), np.zeros(3))
            pb = (cv2.Rodrigues(R)[0].ravel(), t.ravel())
            m = inl2.ravel() > 0
            X, ok = triangulate(K, pa, pb, xa[m], xb[m])
            # гомография объясняет пару так же хорошо, как E → параллакса нет (поворот камеры)
            Hm, hin = cv2.findHomography(xa, xb, cv2.RANSAC, 1.0)
            hratio = (hin.sum() / max(1, len(xa))) if hin is not None else 0
            score = ok.sum() * (1.0 - max(0.0, hratio - 0.5))
            if best is None or score > best[0]:
                best = (score, a, b, pa, pb, common[m][ok], X[ok], hratio)
    if best is None or best[0] < 25:
        return None, "parallax"
    _, a, b, pa, pb, pid0, X0, hratio = best
    poses = {a: pa, b: pb}
    pts3d = {int(i): x for i, x in zip(pid0, X0)}
    send(progress=0, of=n, stage="solve", init=[int(a), int(b)], points=len(pts3d))

    def observations_for(frames):
        out = []
        for fr in frames:
            ids, xy = obs[fr]
            for i, p in zip(ids.tolist(), xy):
                out.append((fr, i, float(p[0]), float(p[1])))
        return out

    poses, pts3d, f = bundle_adjust(poses, pts3d, observations_for([a, b]), f0, cx, cy, fixed=a, refine_f=False)
    K[0, 0] = K[1, 1] = f
    order = list(range(b + 1, n)) + list(range(b - 1, a, -1)) + list(range(a - 1, -1, -1))
    done = 0
    for fr in order:
        ids, xy = obs[fr]
        known = [k for k, i in enumerate(ids.tolist()) if i in pts3d]
        if len(known) < 10:
            continue
        P3 = np.array([pts3d[int(ids[k])] for k in known])
        P2 = xy[known].astype(np.float64)
        near = min(poses, key=lambda q: abs(q - fr))
        rv0, tv0 = poses[near]
        ok, rv, tv, inl = cv2.solvePnPRansac(P3, P2, K, None, rv0.reshape(3, 1).copy(), tv0.reshape(3, 1).copy(),
                                             useExtrinsicGuess=True, iterationsCount=200, reprojectionError=2.5,
                                             confidence=0.999, flags=cv2.SOLVEPNP_ITERATIVE)
        if not ok or inl is None or len(inl) < 8:
            continue
        inl = inl.ravel()
        rv, tv = cv2.solvePnPRefineLM(P3[inl], P2[inl], K, None, rv, tv)
        poses[fr] = (rv.ravel(), tv.ravel())
        # новые точки: трек виден здесь и в решённом кадре с наибольшим разносом во времени
        new_ids = [i for i in ids.tolist() if i not in pts3d and i in tracks]
        by_partner = {}
        for i in new_ids:
            fr_list, xy_list = tracks[i]
            solved = [k for k, q in enumerate(fr_list.tolist()) if q in poses and q != fr]
            if not solved:
                continue
            k = max(solved, key=lambda k: abs(int(fr_list[k]) - fr))
            here = int(np.nonzero(fr_list == fr)[0][0])
            by_partner.setdefault(int(fr_list[k]), []).append((i, xy_list[k], xy_list[here]))
        for q, items in by_partner.items():
            xa = np.array([it[1] for it in items])
            xb = np.array([it[2] for it in items])
            X, okm = triangulate(K, poses[q], poses[fr], xa, xb)
            for it, x, g in zip(items, X, okm):
                if g:
                    pts3d[it[0]] = x
        done += 1
        if done % 30 == 0:      # промежуточный BA — сдерживает накопление ошибки
            recent = sorted(poses)
            poses, pts3d, f = bundle_adjust(poses, pts3d, observations_for(recent), f, cx, cy, fixed=a,
                                            refine_f=False, iters=12)
        if done % 5 == 0:
            send(progress=len(poses), of=n, stage="solve")
    # --- итоговый BA с уточнением фокуса, затем отсев выбросов и повтор
    for rnd in range(3):
        send(progress=rnd, of=3, stage="ba")
        allobs = observations_for(sorted(poses))
        poses, pts3d, f = bundle_adjust(poses, pts3d, allobs, f, cx, cy, fixed=a, refine_f=True, iters=60)
        # ошибки наблюдений
        errs = {}
        for fr, i, x, y in allobs:
            if i in pts3d and fr in poses:
                uv, z = project(poses[fr][0][None], poses[fr][1][None], pts3d[i][None], f, cx, cy)
                errs.setdefault(i, []).append(float(np.hypot(uv[0, 0] - x, uv[0, 1] - y)) if z[0] > 0 else 99.0)
        bad = [i for i, e in errs.items() if math.sqrt(np.mean(np.square(e))) > err_px or len(e) < 3]
        for i in bad:
            pts3d.pop(i, None)
        if not bad:
            break
    return (poses, pts3d, f, (a, b), hratio), None


def stats(obs, poses, pts3d, f, cx, cy):
    per_frame, allr = {}, []
    pt_err = {}
    for fr in sorted(poses):
        ids, xy = obs[fr]
        sel = [k for k, i in enumerate(ids.tolist()) if i in pts3d]
        if not sel:
            continue
        P = np.array([pts3d[int(ids[k])] for k in sel])
        uv, z = project(np.repeat(poses[fr][0][None], len(P), 0), np.repeat(poses[fr][1][None], len(P), 0), P, f, cx, cy)
        e = np.linalg.norm(uv - xy[sel], axis=1)
        e[z <= 0] = 99.0
        per_frame[fr] = float(np.sqrt(np.mean(e ** 2)))
        allr.extend(e.tolist())
        for k, ee in zip(sel, e):
            pt_err.setdefault(int(ids[k]), []).append(float(ee))
    rms = float(np.sqrt(np.mean(np.square(allr)))) if allr else 99.0
    return per_frame, rms, {k: float(np.sqrt(np.mean(np.square(v)))) for k, v in pt_err.items()}


def main():
    cfg = json.loads(sys.argv[1])
    t0 = time.time()
    out = cfg["out"]
    os.makedirs(out, exist_ok=True)
    frames, fps, full = decode(cfg["video"], int(cfg["start"]), int(cfg["end"]), int(cfg.get("track_w", 960)))
    if len(frames) < 10:
        send(error="too_short")
        return
    h, w = frames[0].shape
    sc = full[1] / w                                   # трекинг → полное разрешение
    obs = track_points(frames, int(cfg.get("corners", 700)), float(cfg.get("min_dist", 10)))
    obs, n_locked = drop_screen_locked(obs)
    send(progress=0, of=1, stage="focal")
    f0 = cfg.get("focal_px")
    f0 = f0 / sc if f0 else (estimate_focal(obs, w, h, len(frames)) or 1.2 * w)
    send(progress=0, of=1, stage="focal", f0=round(f0 * sc, 1))
    res, why = solve(obs, w, h, f0, float(cfg.get("err_px", 2.0)))
    nodal = False
    if res is None:
        send(error=why)
        return
    poses, pts3d, f, ref, hratio = res
    per_frame, rms, pt_err = stats(obs, poses, pts3d, f, w / 2, h / 2)
    cams = []
    for fr in range(len(frames)):
        if fr in poses:
            R, _ = cv2.Rodrigues(poses[fr][0])
            C = -R.T @ poses[fr][1]
            cams.append({"R": R.ravel().tolist(), "C": C.tolist()})   # R: мир→камера; C — центр камеры
        else:
            cams.append(None)
    pts = [{"id": int(i), "X": x.tolist(), "err": round(pt_err.get(i, 99.0) * sc, 3)} for i, x in pts3d.items()]
    sol = {"version": 1, "video": cfg["video"], "start": int(cfg["start"]), "n": len(frames), "fps": fps,
           "w": int(full[1]), "h": int(full[0]), "track_w": w, "scale": sc, "f": f * sc,
           "fov_h": math.degrees(2 * math.atan(full[1] / 2 / (f * sc))),
           "cams": cams, "points": pts, "rms_px": rms * sc,
           "frame_rms_px": {str(k): v * sc for k, v in per_frame.items()},
           "solved": sum(c is not None for c in cams), "nodal": nodal, "seconds": round(time.time() - t0, 1),
           "init": list(ref), "h_ratio": round(float(hratio), 3), "screen_locked": n_locked}
    with open(os.path.join(out, "solution.json"), "w") as fp:
        json.dump(sol, fp)
    pts_ids = set(pts3d)
    o2 = []
    for fr, (ids, xy) in enumerate(obs):
        o2.append([[int(i), round(float(p[0]) * sc, 1), round(float(p[1]) * sc, 1)]
                   for i, p in zip(ids.tolist(), xy) if i in pts_ids])
    with open(os.path.join(out, "obs.json"), "w") as fp:
        json.dump(o2, fp)
    send(ok=True, rms_px=round(sol["rms_px"], 3), solved=sol["solved"], n=sol["n"], points=len(pts),
         f=round(sol["f"], 1), fov=round(sol["fov_h"], 2), seconds=sol["seconds"], init=sol["init"],
         h_ratio=sol["h_ratio"], screen_locked=n_locked)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        send(error=f"{type(e).__name__}: {e}", trace=traceback.format_exc()[-2000:])
