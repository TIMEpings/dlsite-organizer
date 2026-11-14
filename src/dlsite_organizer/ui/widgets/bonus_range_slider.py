"""Nonlinear positioning with exact keyboard steps for the paired end offset."""

from bisect import bisect_right

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QSlider

# Small ranges occupy more of the slider track per candidate.
SLIDER_MAX_END = 20_000
_ANCHORS = (1, 20, 50, 100, 1000, 5000, SLIDER_MAX_END)
_POSITIONS = (0, 100, 220, 360, 550, 760, 1000)
_SNAP_DISTANCE = 6


def _interpolate(value: int, source: tuple[int, ...], target: tuple[int, ...]) -> int:
    right = min(bisect_right(source, value), len(source) - 1)
    left = right - 1
    fraction = (value - source[left]) / (source[right] - source[left])
    return round(target[left] + fraction * (target[right] - target[left]))


class BonusRangeSlider(QSlider):
    exact_step = Signal(int)

    def __init__(self) -> None:
        super().__init__(Qt.Orientation.Horizontal)
        self.setRange(_POSITIONS[0], _POSITIONS[-1])
        self.setPageStep(50)

    def end_offset(self) -> int:
        position = self.value()
        for anchor, stop in zip(_ANCHORS[1:], _POSITIONS[1:], strict=True):
            if abs(position - stop) <= _SNAP_DISTANCE:
                return anchor
        return _interpolate(position, _POSITIONS, _ANCHORS)

    def set_end_offset(self, end: int) -> None:
        bounded = max(_ANCHORS[0], min(end, SLIDER_MAX_END))
        self.setValue(_interpolate(bounded, _ANCHORS, _POSITIONS))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Down, Qt.Key.Key_Right, Qt.Key.Key_Up):
            self.exact_step.emit(-1 if key in (Qt.Key.Key_Left, Qt.Key.Key_Down) else 1)
            event.accept()
            return
        super().keyPressEvent(event)
