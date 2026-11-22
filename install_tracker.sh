#!/usr/bin/env bash
# Copyright (c) 2026 Kerstuan. All rights reserved.
# Установка движка «3D Camera Tracker» для SFX: OpenCV (точки и их слежение, RANSAC, PnP)
# + SciPy (bundle adjustment — совместная оптимизация камеры и 3D-точек).
# Отдельное окружение ~/sfx/.venv-track — основная программа не тяжелеет. Загрузка ~120 МБ.
# Повторный запуск ничего не ломает.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

PY=python3.14
command -v "$PY" >/dev/null || PY=python3

echo "== 1/3 окружение .venv-track"
[ -x .venv-track/bin/python ] || "$PY" -m venv .venv-track
.venv-track/bin/pip install -q --upgrade pip

echo "== 2/3 OpenCV, SciPy, NumPy, PyAV"
.venv-track/bin/pip install opencv-python-headless scipy numpy av

echo "== 3/3 проверка"
.venv-track/bin/python - <<'EOF'
import cv2, scipy, numpy
print("OpenCV", cv2.__version__, "| SciPy", scipy.__version__, "| NumPy", numpy.__version__)
print("Движок трекера готов")
EOF
