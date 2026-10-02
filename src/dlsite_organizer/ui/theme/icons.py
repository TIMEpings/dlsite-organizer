"""Cached, DPI-aware line icons used by application navigation."""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap

from dlsite_organizer.ui.theme.tokens import LIGHT_COLORS

_ICON_LOGICAL_SIZE = 18
_ICON_SCALE = 2
_NAVIGATION_ICONS = {"organizer", "lookup", "history", "settings", "about"}


@lru_cache(maxsize=len(_NAVIGATION_ICONS))
def navigation_icon(name: str) -> QIcon:
    """Create one small line icon and cache neutral/selected color variants."""
    if name not in _NAVIGATION_ICONS:
        raise ValueError(f"Unknown navigation icon: {name}")

    icon = QIcon()
    icon.addPixmap(_draw_icon(name, LIGHT_COLORS.text_secondary), QIcon.Mode.Normal)
    icon.addPixmap(_draw_icon(name, LIGHT_COLORS.accent), QIcon.Mode.Selected)
    return icon


def _draw_icon(name: str, color: str) -> QPixmap:
    pixmap = QPixmap(_ICON_LOGICAL_SIZE * _ICON_SCALE, _ICON_LOGICAL_SIZE * _ICON_SCALE)
    pixmap.setDevicePixelRatio(_ICON_SCALE)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    pen = QPen(QColor(color), 1.55)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    if name == "organizer":
        for x, y in ((3, 3), (10, 3), (3, 10), (10, 10)):
            painter.drawRoundedRect(QRectF(x, y, 5, 5), 1, 1)
    elif name == "lookup":
        painter.drawEllipse(QRectF(2.5, 2.5, 9.5, 9.5))
        painter.drawLine(QPointF(10, 10), QPointF(15.4, 15.4))
    elif name == "history":
        painter.drawEllipse(QRectF(2.4, 2.4, 13.2, 13.2))
        painter.drawLine(QPointF(9, 5), QPointF(9, 9))
        painter.drawLine(QPointF(9, 9), QPointF(12, 10.8))
    elif name == "settings":
        for start, end in (
            (QPointF(9, 1.8), QPointF(9, 4.1)),
            (QPointF(9, 13.9), QPointF(9, 16.2)),
            (QPointF(1.8, 9), QPointF(4.1, 9)),
            (QPointF(13.9, 9), QPointF(16.2, 9)),
            (QPointF(3.9, 3.9), QPointF(5.5, 5.5)),
            (QPointF(12.5, 12.5), QPointF(14.1, 14.1)),
            (QPointF(14.1, 3.9), QPointF(12.5, 5.5)),
            (QPointF(5.5, 12.5), QPointF(3.9, 14.1)),
        ):
            painter.drawLine(start, end)
        painter.drawEllipse(QRectF(4.1, 4.1, 9.8, 9.8))
        painter.drawEllipse(QRectF(7.1, 7.1, 3.8, 3.8))
    else:
        painter.drawEllipse(QRectF(2.2, 2.2, 13.6, 13.6))
        painter.drawEllipse(QRectF(8.15, 5, 1.7, 1.7))
        painter.drawLine(QPointF(9, 8), QPointF(9, 13))

    painter.end()
    return pixmap
