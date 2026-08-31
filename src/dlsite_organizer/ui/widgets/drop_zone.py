"""Small Qt drop target that accepts only local directory URLs."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMimeData, QRectF, QSize, Qt, Signal
from PySide6.QtGui import (
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPalette,
    QPen,
)
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout

from dlsite_organizer.app.branding import load_branding_pixmap


class DirectoryDropZone(QFrame):
    """A visibly labelled, local-directory-only drag-and-drop surface."""

    WATERMARK_OPACITY = 0.18
    WATERMARK_MAX_SIZE = 124
    MINIMUM_HEIGHT = 116

    paths_dropped = Signal(object)

    def __init__(self, title: str, description: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(self.MINIMUM_HEIGHT)
        # Keep the source comfortably above the logical display size so a
        # HiDPI paint does not have to enlarge a small intermediate pixmap.
        self.branding_pixmap = load_branding_pixmap(self.WATERMARK_MAX_SIZE * 2)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(4)
        heading = QLabel(title)
        heading.setObjectName("dropZoneTitle")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        detail = QLabel(description)
        detail.setObjectName("dropZoneDescription")
        detail.setWordWrap(True)
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(heading)
        layout.addWidget(detail)
        self._set_highlight(False)

    def sizeHint(self) -> QSize:
        """Keep enough vertical room for text and the subdued watermark."""
        size = super().sizeHint()
        return QSize(size.width(), max(size.height(), self.MINIMUM_HEIGHT))

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint the complete drop surface behind its transparent labels."""
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        palette = self.palette()
        surface_rect = QRectF(self.rect()).adjusted(1.0, 1.0, -1.0, -1.0)
        surface_path = QPainterPath()
        surface_path.addRoundedRect(surface_rect, 10.0, 10.0)
        painter.save()
        painter.setClipPath(surface_path)
        painter.fillPath(surface_path, palette.color(QPalette.ColorRole.Base))
        if self.property("dragActive") is True:
            highlight = palette.color(QPalette.ColorRole.Highlight)
            highlight.setAlpha(24)
            painter.fillPath(surface_path, highlight)

        watermark_side = min(
            self.WATERMARK_MAX_SIZE,
            max(0, min(self.width() - 48, self.height() - 8)),
        )
        if self.branding_pixmap is not None and watermark_side > 0:
            watermark_rect = QRectF(
                self.width() - watermark_side - 28,
                (self.height() - watermark_side) / 2,
                watermark_side,
                watermark_side,
            )
            painter.save()
            painter.setOpacity(self.WATERMARK_OPACITY)
            painter.drawPixmap(
                watermark_rect,
                self.branding_pixmap,
                QRectF(self.branding_pixmap.rect()),
            )
        painter.restore()

        border_role = (
            QPalette.ColorRole.Highlight
            if self.property("dragActive") is True
            else QPalette.ColorRole.Mid
        )
        border_color = palette.color(border_role)
        if self.property("dragActive") is not True:
            border_color.setAlpha(190)
        pen = QPen(border_color, 2.0, Qt.PenStyle.DashLine)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(surface_path)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if local_directory_paths(event.mimeData()):
            self._set_highlight(True)
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if local_directory_paths(event.mimeData()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._set_highlight(False)
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = local_directory_paths(event.mimeData())
        self._set_highlight(False)
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self.paths_dropped.emit(paths)

    def _set_highlight(self, active: bool) -> None:
        self.setProperty("dragActive", active)
        self.update()


def local_directory_paths(mime: QMimeData) -> tuple[Path, ...]:
    if not mime.hasUrls():
        return ()
    urls = mime.urls()
    if not urls or any(not url.isLocalFile() for url in urls):
        return ()
    paths = tuple(Path(url.toLocalFile()) for url in urls)
    if any(not path.is_dir() for path in paths):
        return ()
    return paths
