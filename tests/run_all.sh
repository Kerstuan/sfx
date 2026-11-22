#!/usr/bin/env bash
# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
# Все offscreen-тесты в обоих языках. Нужны записи ~/Videos/Replay_2026-10-31_00-14-28.mp4 и ~/Downloads/ssstik*.mp3.
cd "$(dirname "$(readlink -f "$0")")/.."
mkdir -p /tmp/sfx-tests
fail=0
for t in test_flow test_bugs test_cache test_key test_filters test_idle test_three test_batch2 test_ui3 test_ec test_shapes test_plugins test_sapph2 test_transitions test_time test_menus test_layout test_regress_1119 test_freeze test_markers_slip test_lights test_ripple test_gizmo test_views test_regress_1120 test_regress_1122 test_fx_audit test_params test_proxy_race test_installer; do
  for L in ru en; do
    r=$(XDG_CONFIG_HOME=$(mktemp -d) .venv/bin/python tests/$t.py $L 2>&1 | grep -E "^(ALL )?OK" | tail -1)
    echo "$t $L: ${r:-FAIL}"; [ -z "$r" ] && fail=1
  done
done
# нейросетевые инструменты — только если установлены их окружения (install_roto.sh / install_tracker.sh)
[ -x .venv-roto/bin/python ] && for L in ru en; do r=$(.venv/bin/python tests/test_roto.py $L 2>&1 | grep -E "^OK" | tail -1); echo "test_roto $L: ${r:-FAIL}"; [ -z "$r" ] && fail=1; done
[ -x .venv-track/bin/python ] && { r=$(.venv/bin/python tests/test_tracker.py ru 2>&1 | grep -E "^OK" | tail -1); echo "test_tracker ru: ${r:-FAIL}"; [ -z "$r" ] && fail=1; }
.venv/bin/python tests/test_async.py 2>&1 | grep -q "^OK" && echo "async: OK" || { echo "async: FAIL"; fail=1; }
exit $fail
