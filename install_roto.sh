#!/usr/bin/env bash
# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
# Установка нейросети для «Roto Brush» в SFX: PyTorch (CUDA) + SAM 2.1 от Meta + модель tiny.
# Ставится в отдельное окружение ~/sfx/.venv-roto — основная программа не тяжелеет.
# Загрузка ~3 ГБ (PyTorch с CUDA) + ~150 МБ модель. Повторный запуск ничего не ломает.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

PY=python3.14
command -v "$PY" >/dev/null || PY=python3

echo "== 1/4 окружение .venv-roto"
[ -x .venv-roto/bin/python ] || "$PY" -m venv .venv-roto
.venv-roto/bin/pip install -q --upgrade pip

echo "== 2/4 PyTorch с CUDA (самое долгое, ~3 ГБ)"
.venv-roto/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cu130

echo "== 3/4 SAM 2 (Meta) и зависимости"
.venv-roto/bin/pip install numpy pillow av hydra-core iopath tqdm
SAM2_BUILD_CUDA=0 .venv-roto/bin/pip install --no-build-isolation "git+https://github.com/facebookresearch/sam2.git"

echo "== 4/4 модель SAM 2.1 tiny"
mkdir -p models
if [ ! -s models/sam2.1_hiera_tiny.pt ]; then
  curl -L --fail -o models/sam2.1_hiera_tiny.pt.part \
    https://dl.fbaipublicfiles.com/segment_anything_2/092824/sam2.1_hiera_tiny.pt
  mv models/sam2.1_hiera_tiny.pt.part models/sam2.1_hiera_tiny.pt
fi

echo "== проверка"
.venv-roto/bin/python - <<'EOF'
import torch
print("torch", torch.__version__, "CUDA:", torch.cuda.is_available(),
      torch.cuda.get_device_name(0) if torch.cuda.is_available() else "")
from sam2.build_sam import build_sam2_video_predictor
p = build_sam2_video_predictor("configs/sam2.1/sam2.1_hiera_t.yaml", "models/sam2.1_hiera_tiny.pt",
                               device="cuda" if torch.cuda.is_available() else "cpu")
print("SAM 2 загружен — Roto Brush готов")
EOF
