# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""Рендер композиции через ffmpeg: формат, кодек, fps, качество, размер, альфа, звук слоёв."""
from __future__ import annotations

from .i18n import tr

import math
import os
import subprocess
import tempfile
from dataclasses import asdict, dataclass

from PySide6.QtCore import QThread, Signal
from PySide6.QtGui import QImage

from .audio import SR, audible_layers, mix
from .media import MediaPool
from .model import Composition
from .render import render_frame

# ---------------------------------------------------------------- форматы и кодеки
# формат: (подпись, расширение, [кодеки], аудиокодек или None)
FORMATS = {
    "mp4": ("MP4", ".mp4", ["h264_nvenc", "h264", "hevc_nvenc", "h265", "av1"], "aac"),
    "mov": ("QuickTime MOV", ".mov", ["prores422", "prores4444", "qtrle", "h264", "h264_nvenc"], "aac"),
    "mkv": ("Matroska MKV", ".mkv", ["h264_nvenc", "h264", "hevc_nvenc", "h265", "av1"], "aac"),
    "webm": ("WebM", ".webm", ["vp9", "av1"], "libopus"),
    "gif": ("GIF", ".gif", ["gif"], None),
    "png": (tr("PNG-секвенция"), ".png", ["png"], None),
}

# кодек: (подпись, поддерживает альфу, есть ли выбор качества)
CODECS = {
    "h264_nvenc": (tr("H.264 — видеокарта NVIDIA (быстро)"), False, True),
    "hevc_nvenc": (tr("H.265 — видеокарта NVIDIA (быстро)"), False, True),
    "h264": ("H.264 (libx264)", False, True),
    "h265": ("H.265 / HEVC (libx265)", False, True),
    "av1": ("AV1 (SVT-AV1)", False, True),
    "vp9": ("VP9 (libvpx)", True, True),
    "prores422": ("Apple ProRes 422", False, True),
    "prores4444": ("Apple ProRes 4444", True, False),
    "qtrle": ("QuickTime Animation", True, False),
    "gif": ("GIF", False, True),
    "png": ("PNG", True, False),
}

QUALITIES = ["draft", "medium", "high", "max", "lossless"]
QUALITY_NAMES = {"draft": tr("Черновик"), "medium": tr("Среднее"), "high": tr("Высокое"),
                 "max": tr("Максимальное"), "lossless": tr("Без потерь")}
SPEEDS = ["fast", "normal", "slow"]
SPEED_NAMES = {"fast": tr("Быстро"), "normal": tr("Обычно"), "slow": tr("Медленно (меньше файл)")}
SCALES = [1.0, 0.75, 0.5, 0.25]
FPS_PRESETS = [12, 15, 23.976, 24, 25, 29.97, 30, 50, 59.94, 60, 120]

CRF = {  # draft, medium, high, max
    "h264": (28, 23, 18, 14),
    "h265": (32, 28, 23, 18),
    "h264_nvenc": (30, 25, 20, 16),     # -cq для NVENC
    "hevc_nvenc": (32, 28, 23, 19),
    "av1": (42, 35, 28, 22),
    "vp9": (42, 34, 28, 20),
}
PRORES_422 = (0, 1, 2, 3)            # proxy, LT, 422, HQ
X264_SPEED = {"fast": "veryfast", "normal": "medium", "slow": "slow"}
X265_SPEED = {"fast": "fast", "normal": "medium", "slow": "slow"}
AV1_SPEED = {"fast": "10", "normal": "8", "slow": "5"}
VP9_SPEED = {"fast": "5", "normal": "3", "slow": "1"}
NVENC_SPEED = {"fast": "p2", "normal": "p5", "slow": "p7"}
GPU_CODECS = ("h264_nvenc", "hevc_nvenc")


def codec_available(codec: str) -> bool:
    """Кодеки видеокарты — только если NVENC реально работает на этой машине."""
    if codec in GPU_CODECS:
        from .proxy import encoder
        return encoder() == "nvenc"
    return True


def codec_has_lossless(codec: str) -> bool:
    return codec in ("h264", "h265", "vp9", "h264_nvenc", "hevc_nvenc")


@dataclass
class RenderSettings:
    path: str = ""
    fmt: str = "mp4"
    codec: str = "h264_nvenc"     # нет NVIDIA — normalize() сам возьмёт libx264
    fps: float = 0.0              # 0 — как у композиции
    quality: str = "high"
    speed: str = "normal"
    scale: float = 1.0
    work_area: bool = True        # False — вся композиция
    audio: bool = True
    audio_kbps: int = 192
    alpha: bool = False
    mb_samples: int = 12

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RenderSettings":
        s = cls()
        for k, v in (d or {}).items():
            if hasattr(s, k):
                try:
                    setattr(s, k, type(getattr(s, k))(v))
                except (TypeError, ValueError):
                    pass
        s.normalize()
        return s

    def normalize(self):
        if self.fmt not in FORMATS:
            self.fmt = "mp4"
        if self.codec not in FORMATS[self.fmt][2] or not codec_available(self.codec):
            self.codec = next(c for c in FORMATS[self.fmt][2] if codec_available(c))
        if self.quality not in QUALITIES:
            self.quality = "high"
        if self.quality == "lossless" and not codec_has_lossless(self.codec):
            self.quality = "max"
        if self.speed not in SPEEDS:
            self.speed = "normal"
        if not CODECS[self.codec][1]:
            self.alpha = False
        if self.scale <= 0:
            self.scale = 1.0

    # ---- производные
    def out_fps(self, comp: Composition) -> float:
        return self.fps if self.fps > 0 else comp.fps

    def range(self, comp: Composition) -> tuple[int, int]:
        if self.work_area:
            return comp.work_in, comp.work_out
        return 0, comp.duration

    def size(self, comp: Composition) -> tuple[int, int]:
        return max(2, int(comp.width * self.scale)), max(2, int(comp.height * self.scale))

    def has_audio(self) -> bool:
        return self.audio and FORMATS[self.fmt][3] is not None

    def frame_map(self, comp: Composition) -> list[int]:
        """Кадры композиции для каждого кадра на выходе (при другом fps кадры повторяются или пропускаются)."""
        a, b = self.range(comp)
        ofps = self.out_fps(comp)
        n = max(1, math.ceil((b - a) / comp.fps * ofps - 1e-6))
        return [min(b - 1, a + int(math.floor(i * comp.fps / ofps + 1e-6))) for i in range(n)]


def output_path(path: str, s: RenderSettings) -> str:
    """Путь с правильным расширением; для PNG-секвенции — шаблон имя_00000.png."""
    ext = FORMATS[s.fmt][1]
    root, cur = os.path.splitext(path)
    if cur.lower() not in {f[1] for f in FORMATS.values()}:
        root = path
    if root.endswith("_%05d"):
        root = root[:-5]
    if s.fmt == "png":
        return root + "_%05d.png"
    return root + ext


def _video_args(s: RenderSettings) -> tuple[str, list[str]]:
    """Фильтр формата пикселей и аргументы кодека."""
    q = QUALITIES.index(s.quality)
    c = s.codec
    even = "scale=trunc(iw/2)*2:trunc(ih/2)*2,"
    if c == "h264":
        if s.quality == "lossless":
            return even + "format=yuv444p", ["-c:v", "libx264", "-preset", X264_SPEED[s.speed], "-qp", "0"]
        return even + "format=yuv420p", ["-c:v", "libx264", "-preset", X264_SPEED[s.speed],
                                         "-crf", str(CRF["h264"][q])]
    if c in GPU_CODECS:
        # Кодирование на видеокарте. Кадры BGRA отдаём NVENC как есть — перевод цвета в YUV он делает
        # сам на GPU; иначе ffmpeg переводит каждый кадр 1080p на процессоре в один поток (узкое место).
        if s.quality == "lossless":
            return even + "format=yuv444p", ["-c:v", c, "-preset", "p7", "-tune", "lossless"]
        args = ["-c:v", c, "-preset", NVENC_SPEED[s.speed], "-rc", "vbr", "-cq", str(CRF[c][q]), "-b:v", "0",
                "-profile:v", "high" if c == "h264_nvenc" else "main"]
        if c == "hevc_nvenc":
            args += ["-tag:v", "hvc1"]
        return even + "format=bgra", args
    if c == "h265":
        args = ["-c:v", "libx265", "-preset", X265_SPEED[s.speed], "-tag:v", "hvc1", "-x265-params", "log-level=error"]
        if s.quality == "lossless":
            return even + "format=yuv444p", args[:-1] + ["log-level=error:lossless=1"]
        return even + "format=yuv420p", args + ["-crf", str(CRF["h265"][q])]
    if c == "av1":
        return even + "format=yuv420p", ["-c:v", "libsvtav1", "-preset", AV1_SPEED[s.speed],
                                         "-crf", str(CRF["av1"][min(q, 3)])]
    if c == "vp9":
        fmt = "yuva420p" if s.alpha else "yuv420p"
        args = ["-c:v", "libvpx-vp9", "-b:v", "0", "-row-mt", "1", "-deadline", "good",
                "-cpu-used", VP9_SPEED[s.speed]]
        if s.quality == "lossless":
            return even + f"format={fmt}", args + ["-lossless", "1"]
        return even + f"format={fmt}", args + ["-crf", str(CRF["vp9"][q])]
    if c == "prores422":
        return even + "format=yuv422p10le", ["-c:v", "prores_ks", "-profile:v", str(PRORES_422[min(q, 3)]),
                                             "-vendor", "apl0"]
    if c == "prores4444":
        fmt = "yuva444p10le" if s.alpha else "yuv444p10le"
        return even + f"format={fmt}", ["-c:v", "prores_ks", "-profile:v", "4", "-vendor", "apl0"]
    if c == "qtrle":
        return "format=" + ("argb" if s.alpha else "rgb24"), ["-c:v", "qtrle"]
    if c == "png":
        return "format=" + ("rgba" if s.alpha else "rgb24"), ["-c:v", "png"]
    raise ValueError(c)


def _gif_filter(s: RenderSettings) -> str:
    colors = (64, 128, 256, 256, 256)[QUALITIES.index(s.quality)]
    dither = "bayer:bayer_scale=3" if s.quality in ("draft", "medium") else "sierra2_4a"
    return (f"[0:v]split[g0][g1];[g0]palettegen=max_colors={colors}:stats_mode=diff[pal];"
            f"[g1][pal]paletteuse=dither={dither}:diff_mode=rectangle[vout]")


def build_ffmpeg_cmd(comp: Composition, out_path: str, pool: MediaPool,
                     s: RenderSettings | None = None, audio_file: str | None = None) -> list[str]:
    """audio_file — сведённый звук (float32, 48 кГц, стерео), см. audio.mix()."""
    s = s or RenderSettings()
    s.normalize()
    ofps = s.out_fps(comp)
    w, h = s.size(comp)
    dur = len(s.frame_map(comp)) / ofps
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "bgra", "-s", f"{w}x{h}",
           "-r", f"{ofps}", "-i", "-"]
    if s.codec == "gif":
        filters, vargs = [_gif_filter(s)], ["-loop", "0"]
    else:
        vf, vargs = _video_args(s)
        filters = [f"[0:v]{vf}[vout]"]
    has_audio = bool(audio_file)
    if has_audio:
        cmd += ["-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", audio_file]
    cmd += ["-filter_complex", ";".join(filters), "-map", "[vout]"]
    cmd += vargs
    if has_audio:
        acodec = FORMATS[s.fmt][3]
        cmd += ["-map", "1:a", "-c:a", acodec, "-b:a", f"{s.audio_kbps}k"]
    cmd += ["-t", f"{dur:.4f}"]
    if s.fmt in ("mp4", "mov"):
        cmd += ["-movflags", "+faststart"]
    if s.fmt == "png":
        cmd += ["-start_number", "0"]
    cmd.append(out_path)
    return cmd


def frame_bytes(img: QImage, alpha: bool) -> bytes:
    """Байты BGRA. Для альфы снимаем премультипликацию, иначе края полупрозрачного будут тёмными."""
    if alpha:
        img = img.convertToFormat(QImage.Format_ARGB32)
    return bytes(img.constBits())


class ExportThread(QThread):
    progress = Signal(int, int)       # готово, всего
    finished_ok = Signal(str)
    failed = Signal(str)

    def __init__(self, comp_dict: dict, out_path: str, parent=None, settings: RenderSettings | None = None):
        super().__init__(parent)
        self.comp_dict = comp_dict
        self.settings = settings or RenderSettings()
        self.out_path = out_path
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        if "comps" in self.comp_dict:   # весь проект: нужен для прекомпозиций
            from .model import Project
            proj = Project.from_dict(self.comp_dict)
            comp = proj.comp
            for c in proj.comps:
                c.view3d = "active"     # 3D-вид вьювера (Top/Custom…) — только для просмотра, рендер через камеру
        else:
            comp = Composition.from_dict(self.comp_dict)
            comp.view3d = "active"
        s = self.settings
        s.normalize()
        pool = MediaPool()
        log = tempfile.TemporaryFile()
        audio_file = None
        try:
            out_dir = os.path.dirname(self.out_path)
            if out_dir:
                os.makedirs(out_dir, exist_ok=True)
            audio_file = None
            if s.has_audio() and audible_layers(comp):
                a, b = s.range(comp)
                audio_tmp = tempfile.NamedTemporaryFile(suffix=".f32", delete=False)
                audio_tmp.write(mix(comp, a, b).astype("float32").tobytes())
                audio_tmp.close()
                audio_file = audio_tmp.name
            cmd = build_ffmpeg_cmd(comp, self.out_path, pool, s, audio_file)
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
            frames = s.frame_map(comp)
            total = len(frames)
            last_f, last_bytes = None, None
            try:
                for i, f in enumerate(frames):
                    if self._cancel:
                        proc.kill()
                        proc.wait()
                        self.failed.emit(tr("Экспорт отменён"))
                        return
                    if f != last_f:  # при fps выше композиции кадр повторяется — не считаем заново
                        img = render_frame(comp, f, pool, s.scale, mb_samples=s.mb_samples, transparent=s.alpha)
                        last_f, last_bytes = f, frame_bytes(img, s.alpha)
                    proc.stdin.write(last_bytes)
                    self.progress.emit(i + 1, total)
                proc.stdin.close()
            except BrokenPipeError:
                pass
            code = proc.wait()
            if code != 0:
                log.seek(0)
                self.failed.emit(log.read().decode(errors="replace")[-2000:] or tr("ffmpeg код {}").format(code))
            else:
                self.finished_ok.emit(self.out_path)
        except Exception as e:  # noqa
            self.failed.emit(str(e))
        finally:
            pool.close()
            log.close()
            if audio_file:
                try:
                    os.remove(audio_file)
                except OSError:
                    pass
