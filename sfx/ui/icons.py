# Copyright (c) 2026 Kerstuan. All rights reserved.
"""Векторные значки в стиле AE (рисуются QPainter'ом, без файлов картинок)."""
from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

_cache: dict = {}


def _draw(p: QPainter, name: str, s: float, col: QColor):
    pen = QPen(col, s * 0.085)
    pen.setJoinStyle(Qt.RoundJoin)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    P = lambda x, y: QPointF(x * s, y * s)  # noqa: E731 — координаты в долях иконки
    if name == "select":
        p.setBrush(col)
        p.drawPolygon(QPolygonF([P(.28, .12), P(.28, .82), P(.45, .64), P(.57, .9), P(.66, .86), P(.54, .6), P(.76, .6)]))
    elif name == "hand":
        path = QPainterPath(P(.3, .55))
        for x in (.36, .48, .6, .72):
            path.moveTo(P(x, .55))
            path.lineTo(P(x, .2 if x in (.48, .6) else .28))
        p.drawPath(path)
        p.drawRoundedRect(QRectF(P(.3, .45), P(.78, .88)), s * .1, s * .1)
        p.drawLine(P(.3, .6), P(.2, .48))
    elif name == "zoom":
        p.drawEllipse(QRectF(P(.18, .14), P(.62, .58)))
        p.drawLine(P(.56, .52), P(.84, .82))
    elif name == "rotate":
        p.drawArc(QRectF(P(.2, .2), P(.8, .8)), 40 * 16, 280 * 16)
        p.setBrush(col)
        p.drawPolygon(QPolygonF([P(.74, .14), P(.82, .38), P(.6, .32)]))
    elif name == "orbit":
        p.drawEllipse(QRectF(P(.12, .34), P(.88, .66)))
        p.drawRect(QRectF(P(.36, .22), P(.64, .5)))
        p.setBrush(col)
        p.drawPolygon(QPolygonF([P(.64, .3), P(.78, .22), P(.78, .5), P(.64, .42)]))
    elif name == "anchor":
        p.drawEllipse(P(.5, .5), s * .14, s * .14)
        for a in range(4):
            ang = a * math.pi / 2
            c, d = math.cos(ang), math.sin(ang)
            p.drawLine(P(.5 + c * .2, .5 + d * .2), P(.5 + c * .42, .5 + d * .42))
            p.drawLine(P(.5 + c * .42, .5 + d * .42), P(.5 + c * .32 - d * .08, .5 + d * .32 + c * .08))
            p.drawLine(P(.5 + c * .42, .5 + d * .42), P(.5 + c * .32 + d * .08, .5 + d * .32 - c * .08))
    elif name == "rect":
        p.drawRect(QRectF(P(.16, .26), P(.84, .74)))
    elif name == "ellipse":
        p.drawEllipse(QRectF(P(.14, .24), P(.86, .76)))
    elif name == "star":
        pts = []
        for i in range(10):
            r = .38 if i % 2 == 0 else .16
            a = -math.pi / 2 + i * math.pi / 5
            pts.append(P(.5 + r * math.cos(a), .54 + r * math.sin(a)))
        p.drawPolygon(QPolygonF(pts))
    elif name == "pen":
        p.setBrush(col)
        p.drawPolygon(QPolygonF([P(.5, .1), P(.72, .5), P(.6, .72), P(.4, .72), P(.28, .5)]))
        p.setBrush(Qt.NoBrush)
        p.drawLine(P(.42, .8), P(.58, .8))
        p.setPen(QPen(QColor("#1e1e1e"), s * .07))
        p.drawLine(P(.5, .3), P(.5, .55))
    elif name == "text":
        p.drawLine(P(.2, .2), P(.8, .2))
        p.drawLine(P(.5, .2), P(.5, .84))
        p.drawLine(P(.38, .84), P(.62, .84))
    elif name in ("play", "first", "last", "prev", "next"):
        p.setBrush(col)
        if name == "play":
            p.drawPolygon(QPolygonF([P(.3, .18), P(.82, .5), P(.3, .82)]))
        elif name in ("prev", "first"):
            p.drawPolygon(QPolygonF([P(.74, .22), P(.36, .5), P(.74, .78)]))
            p.drawRect(QRectF(P(.22, .22), P(.3, .78)))
            if name == "first":
                p.drawPolygon(QPolygonF([P(.5, .22), P(.2, .5), P(.5, .78)]))
        else:
            p.drawPolygon(QPolygonF([P(.26, .22), P(.64, .5), P(.26, .78)]))
            p.drawRect(QRectF(P(.7, .22), P(.78, .78)))
            if name == "last":
                p.drawPolygon(QPolygonF([P(.5, .22), P(.8, .5), P(.5, .78)]))
    elif name == "pause":
        p.setBrush(col)
        p.drawRect(QRectF(P(.26, .2), P(.42, .8)))
        p.drawRect(QRectF(P(.58, .2), P(.74, .8)))
    elif name == "render":
        p.setBrush(col)
        p.drawEllipse(P(.5, .5), s * .22, s * .22)
    elif name == "comp":
        p.drawRect(QRectF(P(.14, .22), P(.86, .78)))
        p.drawEllipse(P(.6, .52), s * .12, s * .12)
        p.drawPolygon(QPolygonF([P(.24, .7), P(.4, .4), P(.54, .7)]))
    elif name == "fx":
        f = p.font()
        f.setPixelSize(int(s * .62))
        f.setItalic(True)
        f.setBold(True)
        p.setFont(f)
        p.drawText(QRectF(0, 0, s, s), Qt.AlignCenter, "fx")
    elif name == "graph":
        path = QPainterPath(P(.14, .8))
        path.cubicTo(P(.5, .8), P(.5, .2), P(.86, .2))
        p.drawPath(path)
    elif name == "eyedropper":
        p.drawLine(P(.22, .78), P(.58, .42))
        p.setBrush(col)
        p.drawRoundedRect(QRectF(P(.56, .14), P(.86, .44)), s * .08, s * .08)
        p.setBrush(Qt.NoBrush)
        p.drawLine(P(.5, .34), P(.66, .5))
        p.drawPoint(P(.18, .82))
    elif name == "snap":
        p.drawArc(QRectF(P(.2, .16), P(.8, .76)), 0, -180 * 16)
        p.drawLine(P(.2, .46), P(.2, .16))
        p.drawLine(P(.8, .46), P(.8, .16))


def icon(name: str, color: str = "#c8c8c8", size: int = 20) -> QIcon:
    """Значок; во включённом состоянии (checked) — белый, как в AE."""
    key = (name, color, size)
    if key in _cache:
        return _cache[key]
    ic = QIcon()
    for state, col in ((QIcon.Off, color), (QIcon.On, "#ffffff")):
        for scale in (1, 2):
            pm = QPixmap(size * scale, size * scale)
            pm.fill(Qt.transparent)
            pm.setDevicePixelRatio(scale)
            p = QPainter(pm)
            p.setRenderHint(QPainter.Antialiasing)
            _draw(p, name, size, QColor(col))
            p.end()
            ic.addPixmap(pm, QIcon.Normal, state)
    _cache[key] = ic
    return ic
