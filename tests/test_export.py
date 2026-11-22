# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
import os, sys, json, subprocess
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from PySide6.QtWidgets import QApplication
app = QApplication([])
from sfx.editor import Editor
from sfx.export import ExportThread, RenderSettings
ed = Editor()
ed.add_media_layer(os.path.expanduser("~/Videos/Replay_2026-10-31_00-14-28.mp4")); v = ed.comp.layers[0]
ed.select([v.id]); ed.add_effect("blurmo"); ed.add_effect("wiggle"); ed.add_effect("glow")
ed.add_solid("#30c040"); s2 = ed.comp.layers[0]; ed.select([s2.id]); ed.add_effect("keylight")
ed.comp.work_in, ed.comp.work_out = 0, 30
out = sys.argv[0].replace("test_export.py", "out_fx.mp4")
st = RenderSettings(path=out, audio=False)
th = ExportThread(json.loads(json.dumps(ed.project.to_dict() if hasattr(ed.project, "to_dict") else ed.project.comps_dict())), out, settings=st)
res = []
th.finished_ok.connect(lambda p: res.append(("ok", p))); th.failed.connect(lambda m: res.append(("fail", m)))
th.start()
while th.isRunning() or not res: app.processEvents(); th.wait(50)
app.processEvents(); print(res)
print(subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v", "-show_entries", "stream=nb_read_frames,width,height", "-of", "csv=p=0", res[0][1]], capture_output=True, text=True).stdout)
subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "0.5", "-i", res[0][1], "-frames:v", "1", "-vf", "scale=640:-1", out.replace(".mp4", ".png")])
