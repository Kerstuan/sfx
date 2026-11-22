# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Регресс 2026-11-22: вертикальное видео с телефона (кадр 3840×2160 + метка «повернуть на −90°»).
1) probe/превью/композиция — 2160×3840, кадр стоит прямо; 2) миниатюры в списке «Проект»;
3) кэш кадров ужимается, когда в системе мало свободной памяти (было: OOM убил всю сессию Hyprland)."""
import os, sys, tempfile
os.environ["QT_QPA_PLATFORM"] = "offscreen"; os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sfx.i18n as i18n; i18n.LANG = sys.argv[1] if len(sys.argv) > 1 else "ru"
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage
app = QApplication([])
from sfx.editor import Editor
from sfx.media import probe, VideoReader
from sfx.ui.main_window import MainWindow
import sfx.cache as cache

PORTRAIT = os.path.expanduser("~/Downloads/AyuGram Desktop/IMG_6545.MOV")
LAND = os.path.expanduser("~/Videos/Replay_2026-10-31_00-14-28.mp4")

# 1. размеры и кадр
i = probe(PORTRAIT); assert (i.width, i.height) == (2160, 3840), (i.width, i.height)
assert (probe(LAND).width, probe(LAND).height) == (1920, 1080)
img = VideoReader(PORTRAIT).frame_at(1.0, 0.25); assert (img.width(), img.height()) == (540, 960)
ed = Editor(); w = MainWindow(ed); w.resize(1600, 950); w.show(); app.processEvents()
ed.import_files([PORTRAIT, LAND]); ed.add_media_layer(PORTRAIT); app.processEvents()
assert (ed.comp.width, ed.comp.height) == (2160, 3840), (ed.comp.width, ed.comp.height)
L = ed.comp.layers[0]; assert (L.width, L.height) == (2160, 3840)
print("1. вертикальное видео: OK")

# 2. миниатюры
for _ in range(50):
    app.processEvents()
lst = w.project_panel.list
icons = {lst.topLevelItem(k).data(0, 0x0100): lst.topLevelItem(k).icon(0) for k in range(lst.topLevelItemCount())}
for p in (PORTRAIT, LAND):
    ic = icons["media:" + p]; assert not ic.isNull(), p
    sz = ic.availableSizes()[0]
    assert (sz.height() > sz.width()) == (p == PORTRAIT), (p, sz)
print("2. миниатюры: OK")

# 3. кэш при нехватке памяти
fc = cache.FrameCache(4096); fr = QImage(1920, 1080, QImage.Format_ARGB32)
cache._avail_mb = lambda: 8000
for k in range(64): fc.put("s", k, fr)
full = fc.bytes
cache._avail_mb = lambda: cache.RESERVE_MB - 300
for k in range(64, 72): fc.put("s", k, fr)
assert fc.bytes <= full + 8 * fr.sizeInBytes() - (300 << 20), (full >> 20, fc.bytes >> 20)
print("3. кэш и память: OK")

# 4. прокси вертикального клипа (NVENC пропускал автоповорот — прокси лёжа и сплюснутый)
import subprocess, numpy as np
from sfx.proxy import build_cmd, encoder
from sfx.effects import to_array
TMPD = tempfile.mkdtemp()
src = VideoReader(PORTRAIT).frame_at(0.0, 0.25)
for enc in {encoder(), "cpu"}:
    dst = os.path.join(TMPD, enc + ".mp4")
    cmd = build_cmd(PORTRAIT, dst, i.width, i.height, enc)
    cmd[cmd.index("-i"):cmd.index("-i")] = ["-t", "0.3"]
    assert subprocess.run(cmd, capture_output=True).returncode == 0, enc
    px = VideoReader(dst).frame_at(0.0, 0.5)
    assert (px.width(), px.height()) == (src.width(), src.height()), (enc, px.size())
    diff = np.abs(to_array(px)[..., :3].astype(int) - to_array(src)[..., :3].astype(int)).mean()
    assert diff < 12, (enc, diff)
print("4. прокси вертикального видео: OK")
print("ALL OK")
