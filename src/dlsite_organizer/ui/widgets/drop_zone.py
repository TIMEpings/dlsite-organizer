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
    QResizeEvent,
)
from PySide6.QtWidgets import QFrame, QLabel, QListWidget, QListWidgetItem, QVBoxLayout, QWidget

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


class RecentOperationList(QListWidget):
    """Compact, tooltip-backed recent-operation rows for the lightweight surface."""

    def __init__(self, drop_surface: DirectoryDropZone, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._drop_surface = drop_surface
        self.setObjectName("quickOperationList")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.setWordWrap(False)
        self.setUniformItemSizes(True)
        self.setMinimumHeight(26)
        self.setMaximumHeight(64)

    def add_full_text(self, text: str) -> QListWidgetItem:
        """Add one compact row while keeping its complete text available."""
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, text)
        item.setToolTip(text)
        item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.addItem(item)
        self._elide_item(item)
        return item

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        for index in range(self.count()):
            item = self.item(index)
            if item is not None:
                self._elide_item(item)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        self._drop_surface.dragEnterEvent(event)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        self._drop_surface.dragMoveEvent(event)

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self._drop_surface.dragLeaveEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:
        self._drop_surface.dropEvent(event)

    def _elide_item(self, item: QListWidgetItem) -> None:
        full_text = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(full_text, str):
            return
        display_text = full_text.replace("\n", "  ·  ")
        available_width = max(0, self.viewport().width() - 8)
        item.setText(self.fontMetrics().elidedText(
            display_text,
            Qt.TextElideMode.ElideMiddle,
            available_width,
        ))


class UnifiedDropZone(DirectoryDropZone):
    """Lightweight-only drop surface with recent operations inside its border."""

    MINIMUM_HEIGHT = 154

    def __init__(self, title: str, description: str, parent=None) -> None:
        super().__init__(title, description, parent)
        self.setObjectName("unifiedDropZone")

        drop_layout = self.layout()
        if not isinstance(drop_layout, QVBoxLayout):
            raise TypeError("DirectoryDropZone must use a QVBoxLayout")
        heading_item = drop_layout.itemAt(0)
        detail_item = drop_layout.itemAt(1)
        heading = heading_item.widget() if heading_item is not None else None
        detail = detail_item.widget() if detail_item is not None else None
        if not isinstance(heading, QLabel) or not isinstance(detail, QLabel):
            raise TypeError("DirectoryDropZone labels are required by UnifiedDropZone")

        drop_layout.removeWidget(heading)
        drop_layout.removeWidget(detail)
        drop_layout.setContentsMargins(14, 10, 14, 10)
        drop_layout.setSpacing(5)

        self.main_content = QWidget(self)
        self.main_content.setObjectName("dropMainArea")
        self.main_content.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        main_layout = QVBoxLayout(self.main_content)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(2)
        heading.setParent(self.main_content)
        detail.setParent(self.main_content)
        main_layout.addWidget(heading)
        main_layout.addWidget(detail)
        self.main_content.setMinimumHeight(52)
        drop_layout.addWidget(self.main_content, 1)

        self.recent_separator = QFrame(self)
        self.recent_separator.setObjectName("recentOperationSeparator")
        self.recent_separator.setFrameShape(QFrame.Shape.HLine)
        self.recent_separator.setFrameShadow(QFrame.Shadow.Plain)
        self.recent_separator.setFixedHeight(1)
        self.recent_separator.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        drop_layout.addWidget(self.recent_separator)

        self.recent_area = QWidget(self)
        self.recent_area.setObjectName("recentOperationArea")
        self.recent_area.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        recent_layout = QVBoxLayout(self.recent_area)
        recent_layout.setContentsMargins(0, 0, 0, 0)
        recent_layout.setSpacing(3)
        self.recent_operation_label = QLabel("最近操作")
        self.recent_operation_label.setObjectName("recentOperationLabel")
        self.recent_operation_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.operation_list = RecentOperationList(self, self.recent_area)
        recent_layout.addWidget(self.recent_operation_label)
        recent_layout.addWidget(self.operation_list)
        self.recent_area.setMinimumHeight(50)
        drop_layout.addWidget(self.recent_area)
        self.operation_list.add_full_text("暂无最近操作")

    def sizeHint(self) -> QSize:
        size = super().sizeHint()
        return QSize(size.width(), max(size.height(), self.MINIMUM_HEIGHT))

    def _watermark_rect(self) -> QRectF:
        main_rect = QRectF(self.main_content.geometry())
        watermark_side = min(
            self.WATERMARK_MAX_SIZE,
            max(0, min(int(main_rect.width()) - 48, int(main_rect.height()) - 8)),
        )
        if watermark_side <= 0:
            return QRectF()
        return QRectF(
            main_rect.right() - watermark_side - 12,
            main_rect.center().y() - watermark_side / 2,
            watermark_side,
            watermark_side,
        )

    def paintEvent(self, event: QPaintEvent) -> None:
        """Paint one complete outer surface and keep the watermark in main content."""
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

        watermark_rect = self._watermark_rect()
        if self.branding_pixmap is not None and not watermark_rect.isNull():
            painter.save()
            painter.setOpacity(self.WATERMARK_OPACITY)
            painter.drawPixmap(
                watermark_rect,
                self.branding_pixmap,
                QRectF(self.branding_pixmap.rect()),
            )
            painter.restore()
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
