# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Процесс-рендерер: считает кадры превью отдельно от окна (запуск: python -m sfx.render_worker).

Протокол по stdin/stdout, сообщения с 4-байтной длиной впереди:
  запрос:  pickle((job_id, state_hash, state_json | None, frame, scale, transparent[, {"mb": n, "proxies": bool}]))
  ответ:   pickle((job_id, w, h, bpl, ms, error, shm, shm_bpl)) + сырые байты картинки (h * bpl).
           Если есть /dev/shm, пиксели лежат в файле shm (/dev/shm/sfx-<окно>-<воркер>-<job>.raw),
           bpl = 0 и по каналу идёт только заголовок; окно читает файл и удаляет его.
state_json присылается только при смене состояния проекта — воркер держит у себя копию.
"""
from __future__ import annotations

import os
import pickle
import struct
import sys
import time


def main():
    out = os.fdopen(os.dup(1), "wb", buffering=0)
    os.dup2(2, 1)          # случайные print() и предупреждения — в stderr, а не в канал с картинками
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        import fcntl
        fcntl.fcntl(out.fileno(), 1031, 1 << 20)   # F_SETPIPE_SZ: кадр 1080p (8 МБ) за 8 записей, а не за 128
    except (OSError, ImportError):
        pass
    try:
        os.nice(5)   # фоновый просчёт уступает окну и композитору — интерфейс не дёргается под нагрузкой
    except OSError:
        pass
    from PySide6.QtGui import QGuiApplication
    app = QGuiApplication([])  # noqa: F841 — нужен для шрифтов и QPainter
    from . import gpu, plugins
    gpu.enable()
    plugins.load_all()         # те же плагины, что и в окне — иначе их эффекты не посчитаются               # размытие на видеокарте, если есть OpenGL
    import json
    from .media import MediaPool
    from .model import Project
    from .render import render_frame

    inp = sys.stdin.buffer
    shm_dir = "/dev/shm" if os.access("/dev/shm", os.W_OK) else None
    pools = {True: MediaPool(use_proxies=True), False: MediaPool(use_proxies=False)}
    project, cur_hash = None, None

    def write_all(b):
        mv = memoryview(b)
        while mv:                  # небуферизованный канал может принять не всё за раз
            mv = mv[out.write(mv):]

    def read_exact(n):
        buf = b""
        while len(buf) < n:
            chunk = inp.read(n - len(buf))
            if not chunk:
                return None
            buf += chunk
        return buf

    while True:
        head = read_exact(4)
        if head is None:
            break
        msg = read_exact(struct.unpack("<I", head)[0])
        if msg is None:
            break
        job = pickle.loads(msg)
        job_id, state_hash, state_json, frame, scale, transparent = job[:6]
        opts = job[6] if len(job) > 6 else {}
        pool = pools[bool(opts.get("proxies", True))]   # рендер в файл — только оригиналы
        err, data, w, h, bpl, ms, shm = "", b"", 0, 0, 0, 0.0, ""
        try:
            if state_hash != cur_hash:
                if state_json is None:
                    raise RuntimeError("no state")
                project = Project()
                project.load_comps(json.loads(state_json))
                cur_hash = state_hash
            t0 = time.perf_counter()
            img = render_frame(project.comp, frame, pool, scale, transparent=transparent,
                               mb_samples=int(opts.get("mb", 8)))
            ms = (time.perf_counter() - t0) * 1000
            w, h, bpl = img.width(), img.height(), img.bytesPerLine()
            bits = memoryview(img.constBits())[: h * bpl]
            if shm_dir:
                # пиксели — в общую память, по каналу только заголовок: окно не перекачивает 8 МБ через pipe
                shm = os.path.join(shm_dir, f"sfx-{os.getppid()}-{os.getpid()}-{job_id}.raw")
                with open(shm, "wb") as f:
                    f.write(bits)
            else:
                data = bits.tobytes()
        except Exception as e:  # noqa — ошибку отдаём окну, воркер живёт дальше
            err = f"{type(e).__name__}: {e}"
            if str(e) == "no state":
                err = "no state"
        hdr = pickle.dumps((job_id, w, h, 0 if shm else bpl, ms, err, shm, bpl))
        write_all(struct.pack("<I", len(hdr)) + hdr)
        write_all(data)            # без склейки: лишняя копия 8 МБ на кадр


if __name__ == "__main__":
    main()
