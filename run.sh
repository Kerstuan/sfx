#!/usr/bin/env bash
# Copyright (c) 2026 Kerstuan. All rights reserved.
# Запуск SFX. При первом запуске сам создаёт окружение и ставит зависимости.
cd "$(dirname "$(readlink -f "$0")")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt || exit 1
fi
exec .venv/bin/python main.py "$@"
