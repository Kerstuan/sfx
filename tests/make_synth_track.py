# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
import cv2, numpy as np, json, math, sys
W, H, F, N = 1280, 720, 900.0, 150
K = np.array([[F, 0, W / 2], [0, F, H / 2], [0, 0, 1]])
rng = np.random.default_rng(3)
def tex(seed):
    r = np.random.default_rng(seed); t = np.zeros((1024, 1024), np.float32)
    for s in (4, 16, 64, 256):
        n = r.random((s, s)).astype(np.float32); t += cv2.resize(n, (1024, 1024), interpolation=cv2.INTER_CUBIC) * (64 / s) ** 0.5
    t = (t - t.min()) / (t.max() - t.min()); return cv2.cvtColor((t * 255).astype(np.uint8), cv2.COLOR_GRAY2BGR)
# плоскости: (начало O, ось U (на всю текстуру), ось V, текстура)
planes = [(np.array([-12, 2, 1.0]), np.array([24, 0, 0.]), np.array([0, 0, 30.]), tex(1)),     # пол y=2
          (np.array([-12, -8, 31.]), np.array([24, 0, 0.]), np.array([0, 10, 0.]), tex(2)),    # задняя стена
          (np.array([-3.5, 0, 9.]), np.array([2.5, 0, 0.]), np.array([0, 2, 0.]), tex(3)),     # коробка, перед
          (np.array([-1.0, 0, 9.]), np.array([0, 0, 3.]), np.array([0, 2, 0.]), tex(4)),       # коробка, бок
          (np.array([4, -3, 14.]), np.array([3, 0, 0.]), np.array([0, 5, 0.]), tex(5))]        # колонна
def look(C, target):
    fwd = target - C; fwd /= np.linalg.norm(fwd)
    right = np.cross(np.array([0, 1., 0]), fwd); right /= np.linalg.norm(right); down = np.cross(fwd, right)
    return np.vstack([right, down, fwd])          # мир→камера
out = cv2.VideoWriter(sys.argv[1], cv2.VideoWriter_fourcc(*"mp4v"), 30, (W, H))
gt = []
for i in range(N):
    t = i / (N - 1)
    C = np.array([3.0 * math.sin(t * 2.2) - 1.5, -0.4 + 0.25 * math.sin(t * 5), -1.0 + 4.0 * t])
    R = look(C, np.array([0.5 * math.sin(t * 3), 0.3, 14.0]))
    tv = -R @ C
    img = np.zeros((H, W, 3), np.uint8)
    for O, U, V, T in sorted(planes, key=lambda p: -np.linalg.norm(p[0] + p[1] / 2 + p[2] / 2 - C)):
        S = np.array([[1 / 1024, 0, 0], [0, 1 / 1024, 0], [0, 0, 1.]])
        Hm = K @ np.column_stack([R @ U, R @ V, R @ O + tv]) @ S
        # отсечь плоскости за камерой: проверим углы
        cs = [O, O + U, O + V, O + U + V]
        if min((R @ c + tv)[2] for c in cs) < 0.3: continue
        warped = cv2.warpPerspective(T, Hm, (W, H), flags=cv2.INTER_LINEAR)
        m = cv2.warpPerspective(np.full((1024, 1024), 255, np.uint8), Hm, (W, H))
        img[m > 127] = warped[m > 127]
    out.write(img); gt.append({"R": R.ravel().tolist(), "C": C.tolist()})
out.release(); json.dump({"f": F, "cams": gt}, open(sys.argv[2], "w")); print("ok", N)
