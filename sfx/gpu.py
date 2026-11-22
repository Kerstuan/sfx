# Copyright (c) 2026 Kerstuan. Licensed under the MIT License (see LICENSE).
"""Эффекты на видеокарте через OpenGL (Qt, без дополнительных библиотек).

Сейчас на GPU — гауссово размытие любого радиуса (два прохода шейдера), на нём держатся
«Размытие по Гауссу» и свечение. Включается в процессах-рендерерах; если OpenGL недоступен —
всё тихо считается на процессоре, как раньше.
"""
from __future__ import annotations

import math
import os

import numpy as np

_GL = None
_TRIED = False

_VERT = """#version 330 core
out vec2 uv;
void main() {
    vec2 p = vec2((gl_VertexID << 1) & 2, gl_VertexID & 2);
    uv = p;
    gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}
"""

_BLUR = """#version 330 core
in vec2 uv;
out vec4 color;
uniform sampler2D src;
uniform vec2 dir;      // шаг в текселях: (1/w, 0) или (0, 1/h)
uniform float sigma;
uniform int radius;
void main() {
    vec4 acc = texture(src, uv);
    float wsum = 1.0;
    float k = -0.5 / (sigma * sigma);
    // пары соседних тапов через линейную фильтрацию — вдвое меньше выборок
    for (int i = 1; i <= radius; i += 2) {
        float w1 = exp(k * float(i * i));
        float w2 = exp(k * float((i + 1) * (i + 1)));
        float w = w1 + w2;
        float o = (float(i) * w1 + float(i + 1) * w2) / w;
        acc += (texture(src, uv + dir * o) + texture(src, uv - dir * o)) * w;
        wsum += 2.0 * w;
    }
    color = acc / wsum;
}
"""


class _GLContext:
    def __init__(self):
        from PySide6.QtGui import QOffscreenSurface, QOpenGLContext, QSurfaceFormat
        from PySide6.QtOpenGL import QOpenGLShader, QOpenGLShaderProgram, QOpenGLVertexArrayObject
        fmt = QSurfaceFormat()
        fmt.setVersion(3, 3)
        fmt.setProfile(QSurfaceFormat.CoreProfile)
        self.ctx = QOpenGLContext()
        self.ctx.setFormat(fmt)
        if not self.ctx.create():
            raise RuntimeError("no GL context")
        self.surf = QOffscreenSurface()
        self.surf.setFormat(self.ctx.format())
        self.surf.create()
        if not self.ctx.makeCurrent(self.surf):
            raise RuntimeError("makeCurrent failed")
        self.f = self.ctx.extraFunctions()
        self.vao = QOpenGLVertexArrayObject()
        self.vao.create()
        self.blur_prog = QOpenGLShaderProgram()
        self.blur_prog.addShaderFromSourceCode(QOpenGLShader.Vertex, _VERT)
        self.blur_prog.addShaderFromSourceCode(QOpenGLShader.Fragment, _BLUR)
        if not self.blur_prog.link():
            raise RuntimeError(self.blur_prog.log())
        self._fbos = {}
        GL_RENDERER = 0x1F01
        self.renderer = self.f.glGetString(GL_RENDERER)

    def _fbo_pair(self, w, h):
        from PySide6.QtOpenGL import QOpenGLFramebufferObject, QOpenGLFramebufferObjectFormat
        key = (w, h)
        if key not in self._fbos:
            fmt = QOpenGLFramebufferObjectFormat()
            fmt.setInternalTextureFormat(0x8058)   # GL_RGBA8
            self._fbos[key] = (QOpenGLFramebufferObject(w, h, fmt), QOpenGLFramebufferObject(w, h, fmt))
            while len(self._fbos) > 6:
                self._fbos.pop(next(iter(self._fbos)))
        return self._fbos[key]

    def _pass(self, tex_id, fbo, dx, dy, sigma, radius, w, h):
        GL_TEXTURE_2D, GL_TRIANGLES = 0x0DE1, 0x0004
        f = self.f
        fbo.bind()
        f.glViewport(0, 0, w, h)
        p = self.blur_prog
        p.bind()
        f.glActiveTexture(0x84C0)
        f.glBindTexture(GL_TEXTURE_2D, tex_id)
        for pname, val in ((0x2801, 0x2601), (0x2800, 0x2601), (0x2802, 0x812F), (0x2803, 0x812F)):
            f.glTexParameteri(GL_TEXTURE_2D, pname, val)   # LINEAR, CLAMP_TO_EDGE
        p.setUniformValue1i(p.uniformLocation("src"), 0)
        p.setUniformValue(p.uniformLocation("dir"), dx, dy)
        p.setUniformValue1f(p.uniformLocation("sigma"), sigma)
        p.setUniformValue1i(p.uniformLocation("radius"), radius)
        self.vao.bind()
        f.glDrawArrays(GL_TRIANGLES, 0, 3)
        self.vao.release()
        p.release()
        fbo.release()

    def blur(self, arr: np.ndarray, sigma: float) -> np.ndarray:
        from PySide6.QtGui import QImage
        from PySide6.QtOpenGL import QOpenGLTexture
        self.ctx.makeCurrent(self.surf)
        h, w = arr.shape[:2]
        # BGRA (премультиплицированные) -> байты RGBA как есть: шейдеру всё равно, он просто усредняет
        u8 = np.ascontiguousarray(np.clip(arr, 0, 255).astype(np.uint8)[..., [2, 1, 0, 3]])
        img = QImage(u8.data, w, h, w * 4, QImage.Format_RGBA8888)
        tex = QOpenGLTexture(QOpenGLTexture.Target2D)
        tex.setFormat(QOpenGLTexture.RGBA8_UNorm)
        tex.setSize(w, h)
        tex.allocateStorage()
        tex.setData(QOpenGLTexture.RGBA, QOpenGLTexture.UInt8, u8.data)
        a, b = self._fbo_pair(w, h)
        radius = max(1, int(math.ceil(sigma * 3)))
        self._pass(tex.textureId(), a, 1.0 / w, 0.0, sigma, radius, w, h)
        self._pass(a.texture(), b, 0.0, 1.0 / h, sigma, radius, w, h)
        out = b.toImage(False)   # без переворота: строки в том же порядке, что мы загрузили
        tex.destroy()
        del img
        out = out.convertToFormat(QImage.Format_RGBA8888_Premultiplied) \
            if out.format() != QImage.Format_RGBA8888_Premultiplied else out
        buf = np.frombuffer(out.constBits(), np.uint8, count=h * out.bytesPerLine()).reshape(h, out.bytesPerLine())
        rgba = buf[:, : w * 4].reshape(h, w, 4)
        return rgba[..., [2, 1, 0, 3]].astype(np.float32)


def enable() -> bool:
    """Попробовать включить GPU (вызывается в процессах-рендерерах)."""
    global _GL, _TRIED
    if _TRIED:
        return _GL is not None
    _TRIED = True
    if os.environ.get("SFX_GPU", "1") == "0":
        return False
    try:
        _GL = _GLContext()
    except Exception as e:  # noqa — нет драйвера/контекста: считаем на CPU
        import sys
        print("GPU off:", e, file=sys.stderr)
        _GL = None
    return _GL is not None


def available() -> bool:
    return _GL is not None


def name() -> str:
    return _GL.renderer if _GL is not None else ""


def blur(arr: np.ndarray, sigma: float):
    """Гауссово размытие на GPU; None — если GPU нет (тогда считает CPU)."""
    if _GL is None:
        return None
    try:
        return _GL.blur(arr, sigma)
    except Exception as e:  # noqa
        import sys
        print("GPU blur failed:", e, file=sys.stderr)
        return None
