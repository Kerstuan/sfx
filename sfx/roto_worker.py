# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Движок Roto Brush для SFX — отдельный процесс в окружении ~/sfx/.venv-roto (PyTorch + SAM 2.1).

Не импортирует Qt и остальной SFX: общается с программой строками JSON через stdin/stdout.
Модель — SAM 2.1 (Meta, Apache 2.0): выделение по точкам/маске на любом кадре и слежение по видео
с памятью о прошлых кадрах. Встроенный Roto Brush Adobe не используется никак.

Сессия — папка: frames/00000.jpg… (кадры исходника), masks/00000.png… (мягкая маска 0..255),
session.json (видео, диапазон кадров, fps, подсказки по кадрам).

Команды (по одной JSON-строке):
  {"cmd":"open","session":dir,"video":path,"start":f0,"end":f1}       кадры исходника [f0, f1)
  {"cmd":"points","frame":i,"points":[[x,y,1|0],...],"replace":false} точки (+/−) на кадре i
  {"cmd":"mask","frame":i,"png":path}                                 готовая маска кадра (ручная кисть)
  {"cmd":"clear","frame":i}                                           убрать подсказки и маску кадра
  {"cmd":"propagate","from":i,"to":j}                                 следить от i до j (вперёд/назад)
  {"cmd":"stabilize","strength":0..1}                                 сгладить маску во времени
  {"cmd":"quit"}
Ответы: {"ok":true,...} | {"error":"..."} | {"progress":k,"of":n}
"""
from __future__ import annotations

import json
import os
import sys
import time

import numpy as np

CHUNK = 240                     # кадров в одном состоянии SAM 2 (память GPU/ОЗУ ограничена)
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CKPT = os.path.join(ROOT, "models", "sam2.1_hiera_tiny.pt")
CFG = "configs/sam2.1/sam2.1_hiera_t.yaml"


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


class Session:
    def __init__(self):
        self.dir = None
        self.meta = {}
        self.predictor = None
        self.state = None
        self.chunk = None          # (a, b) — кадры текущего состояния SAM 2
        self.torch = None

    # ---------------------------------------------------------------- служебное
    def _model(self):
        if self.predictor is None:
            import torch
            from sam2.build_sam import build_sam2_video_predictor
            self.torch = torch
            dev = "cuda" if torch.cuda.is_available() else "cpu"
            self.predictor = build_sam2_video_predictor(CFG, CKPT, device=dev)
        return self.predictor

    def _save_meta(self):
        with open(os.path.join(self.dir, "session.json"), "w") as f:
            json.dump(self.meta, f)

    def frame_path(self, i):
        return os.path.join(self.dir, "frames", f"{i:05d}.jpg")

    def mask_path(self, i):
        return os.path.join(self.dir, "masks", f"{i:05d}.png")

    def _write_mask(self, i, logits):
        """Логиты SAM 2 → мягкая маска 0..255 (сигмоида; край ~2–3 px мягкий, дальше уточняет SFX)."""
        from PIL import Image
        prob = 1.0 / (1.0 + np.exp(-np.clip(logits, -30, 30)))
        Image.fromarray((prob * 255.0 + 0.5).astype(np.uint8)).save(self.mask_path(i), compress_level=1)

    def _read_mask(self, i):
        from PIL import Image
        p = self.mask_path(i)
        if not os.path.exists(p):
            return None
        return np.asarray(Image.open(p).convert("L"), dtype=np.float32) / 255.0

    # ---------------------------------------------------------------- состояние SAM 2 на куске
    def _ensure_chunk(self, i):
        """Состояние SAM 2 для куска, содержащего кадр i (кадры куска — симлинки в отдельной папке)."""
        n = self.meta["n"]
        a = (i // CHUNK) * CHUNK
        b = min(n, a + CHUNK)
        if self.chunk == (a, b) and self.state is not None:
            return a
        pred = self._model()
        cdir = os.path.join(self.dir, "chunk")
        os.makedirs(cdir, exist_ok=True)
        for f in os.listdir(cdir):
            os.unlink(os.path.join(cdir, f))
        for k in range(a, b):
            os.symlink(self.frame_path(k), os.path.join(cdir, f"{k - a:05d}.jpg"))
        if self.state is not None:
            del self.state
            self.torch.cuda.empty_cache()
        self.state = pred.init_state(cdir, offload_video_to_cpu=True, offload_state_to_cpu=False,
                                     async_loading_frames=True)
        self.chunk = (a, b)
        # повторить подсказки пользователя, попавшие в этот кусок
        for key, pr in sorted(self.meta.get("prompts", {}).items(), key=lambda kv: int(kv[0])):
            k = int(key)
            if a <= k < b:
                self._apply_prompt(k, pr)
        return a

    def _apply_prompt(self, k, pr):
        a = self.chunk[0]
        pred = self.predictor
        with self.torch.inference_mode(), self.torch.autocast("cuda", dtype=self.torch.bfloat16,
                                                               enabled=self.torch.cuda.is_available()):
            if pr.get("mask"):
                from PIL import Image
                m = np.asarray(Image.open(pr["mask"]).convert("L"), dtype=np.float32) / 255.0 > 0.5
                return pred.add_new_mask(self.state, frame_idx=k - a, obj_id=1, mask=m)
            pts = np.array([[p[0], p[1]] for p in pr["points"]], np.float32)
            lab = np.array([int(p[2]) for p in pr["points"]], np.int32)
            return pred.add_new_points_or_box(self.state, frame_idx=k - a, obj_id=1, points=pts, labels=lab,
                                              clear_old_points=True)

    # ---------------------------------------------------------------- команды
    def open(self, session, video, start, end):
        import av
        from PIL import Image
        self.dir = session
        os.makedirs(os.path.join(session, "frames"), exist_ok=True)
        os.makedirs(os.path.join(session, "masks"), exist_ok=True)
        mp = os.path.join(session, "session.json")
        if os.path.exists(mp):
            with open(mp) as f:
                self.meta = json.load(f)
            if self.meta.get("video") == video and self.meta.get("start") == start and self.meta.get("end") == end \
                    and os.path.exists(self.frame_path(self.meta["n"] - 1)):
                send({"ok": True, "n": self.meta["n"], "w": self.meta["w"], "h": self.meta["h"],
                      "fps": self.meta["fps"], "resumed": True})
                return
        c = av.open(video)
        st = c.streams.video[0]
        st.thread_type = "AUTO"
        fps = float(st.average_rate or st.guessed_rate or 30)
        t0 = start / fps
        if t0 > 0:
            c.seek(int(t0 / st.time_base), stream=st, backward=True)
        n = 0
        total = max(1, end - start)
        w = h = 0
        for fr in c.decode(st):
            idx = int(round(float(fr.pts * st.time_base) * fps)) if fr.pts is not None else start + n
            if idx < start:
                continue
            if idx >= end:
                break
            img = fr.to_image()
            if (fr.rotation or 0) % 360:   # вертикальное видео с телефона (против часовой, как np.rot90)
                img = img.rotate(fr.rotation, expand=True)
            w, h = img.size
            img.save(self.frame_path(n), quality=92)
            n += 1
            if n % 10 == 0:
                send({"progress": n, "of": total, "stage": "frames"})
        c.close()
        self.meta = {"video": video, "start": start, "end": end, "n": n, "w": w, "h": h, "fps": fps, "prompts": {}}
        self._save_meta()
        self.state, self.chunk = None, None
        send({"ok": True, "n": n, "w": w, "h": h, "fps": fps})

    def points(self, frame, points, replace=False):
        key = str(frame)
        pr = self.meta["prompts"].get(key)
        if replace or pr is None or pr.get("mask"):
            pr = {"points": []}
        pr["points"] = pr["points"] + [list(map(float, p)) for p in points]
        self.meta["prompts"][key] = pr
        self._save_meta()
        self._ensure_chunk(frame)
        _, _, logits = self._apply_prompt(frame, pr)
        self._write_mask(frame, logits[0, 0].float().cpu().numpy())
        send({"ok": True, "frame": frame})

    def mask(self, frame, png):
        self.meta["prompts"][str(frame)] = {"mask": png}
        self._save_meta()
        self._ensure_chunk(frame)
        _, _, logits = self._apply_prompt(frame, self.meta["prompts"][str(frame)])
        self._write_mask(frame, logits[0, 0].float().cpu().numpy())
        send({"ok": True, "frame": frame})

    def clear(self, frame):
        self.meta["prompts"].pop(str(frame), None)
        self._save_meta()
        if os.path.exists(self.mask_path(frame)):
            os.unlink(self.mask_path(frame))
        self.state, self.chunk = None, None          # пересобрать состояние без этих подсказок
        send({"ok": True, "frame": frame})

    def propagate(self, start, stop):
        """Слежение от start до stop включительно. Через границу кусков передаём последнюю маску
        как подсказку-маску следующего куска — так длинный клип не упирается в память."""
        n = self.meta["n"]
        stop = max(0, min(n - 1, stop))
        rev = stop < start
        total = abs(stop - start) + 1
        done = 0
        cur = start
        t0 = time.time()
        while True:
            a = self._ensure_chunk(cur)
            b = self.chunk[1]
            if str(cur) not in self.meta["prompts"]:
                # нет подсказки на стартовом кадре куска: его маска, а если её нет — маска соседа
                m = self._read_mask(cur)
                if m is None:
                    m = self._read_mask(cur + 1 if rev else cur - 1)
                if m is None:
                    send({"error": "no_prompt", "frame": cur})
                    return
                with self.torch.inference_mode():
                    self.predictor.add_new_mask(self.state, frame_idx=cur - a, obj_id=1, mask=m > 0.5)
            seg_end = max(stop, a) if rev else min(stop, b - 1)
            with self.torch.inference_mode(), self.torch.autocast("cuda", dtype=self.torch.bfloat16,
                                                                   enabled=self.torch.cuda.is_available()):
                for fidx, _ids, logits in self.predictor.propagate_in_video(
                        self.state, start_frame_idx=cur - a, max_frame_num_to_track=abs(seg_end - cur), reverse=rev):
                    k = fidx + a
                    if (k > seg_end and not rev) or (k < seg_end and rev):
                        continue
                    self._write_mask(k, logits[0, 0].float().cpu().numpy())
                    done += 1
                    if done % 2 == 0 or k == seg_end:
                        send({"progress": done, "of": total, "stage": "track",
                              "fps": round(done / max(1e-3, time.time() - t0), 1)})
            if seg_end == stop:
                break
            cur = seg_end - 1 if rev else seg_end + 1
            self.state, self.chunk = None, None      # следующий кусок; засеется маской seg_end
        send({"ok": True, "done": done, "seconds": round(time.time() - t0, 2)})

    def stabilize(self, strength):
        """Сглаживание маски во времени с учётом движения: где кадр почти не меняется,
        маска усредняется с соседями (нет «дрожания» края); где движение — остаётся как есть."""
        from PIL import Image
        n = self.meta["n"]
        if strength <= 0 or n < 3:
            send({"ok": True})
            return
        def gray(i):
            return np.asarray(Image.open(self.frame_path(i)).convert("L").reduce(4), np.float32) / 255.0
        masks = [self._read_mask(i) for i in range(n)]
        if any(m is None for m in masks):
            send({"error": "not_tracked"})
            return
        out = []
        prev_g, cur_g = None, gray(0)
        for i in range(n):
            nxt_g = gray(i + 1) if i + 1 < n else None
            acc, wsum = masks[i].copy(), np.ones_like(masks[i])
            for j, g in ((i - 1, prev_g), (i + 1, nxt_g)):
                if g is None:
                    continue
                d = np.abs(cur_g - g)                                   # движение (в 1/4 разрешения)
                wt = np.exp(-d / 0.03) * strength
                wt = np.asarray(Image.fromarray((wt * 255).astype(np.uint8)).resize(masks[i].shape[::-1]),
                                np.float32) / 255.0
                acc += masks[j] * wt
                wsum += wt
            out.append(acc / wsum)
            prev_g, cur_g = cur_g, nxt_g
            if i % 10 == 0:
                send({"progress": i + 1, "of": n, "stage": "stabilize"})
        for i, m in enumerate(out):
            Image.fromarray((np.clip(m, 0, 1) * 255 + 0.5).astype(np.uint8)).save(self.mask_path(i), compress_level=1)
        send({"ok": True})


def main():
    s = Session()
    send({"ready": True})
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            cmd = msg.pop("cmd")
            if cmd == "quit":
                break
            elif cmd == "open":
                s.open(msg["session"], msg["video"], int(msg["start"]), int(msg["end"]))
            elif cmd == "points":
                s.points(int(msg["frame"]), msg["points"], bool(msg.get("replace")))
            elif cmd == "mask":
                s.mask(int(msg["frame"]), msg["png"])
            elif cmd == "clear":
                s.clear(int(msg["frame"]))
            elif cmd == "propagate":
                s.propagate(int(msg["from"]), int(msg["to"]))
            elif cmd == "stabilize":
                s.stabilize(float(msg.get("strength", 0.5)))
            else:
                send({"error": f"unknown command {cmd}"})
        except Exception as e:      # ошибка одной команды не роняет процесс
            import traceback
            send({"error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-1500:]})


if __name__ == "__main__":
    main()
