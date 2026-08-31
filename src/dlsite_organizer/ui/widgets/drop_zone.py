"""Small Qt drop target that accepts only local directory URLs."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QMimeData, Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout


class DirectoryDropZone(QFrame):
    """A visibly labelled, local-directory-only drag-and-drop surface."""

    paths_dropped = Signal(object)

    def __init__(self, title: str, description: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(92)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(4)
        heading = QLabel(title)
        heading.setObjectName("dropZoneTitle")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail = QLabel(description)
        detail.setObjectName("dropZoneDescription")
        detail.setWordWrap(True)
        detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading)
        layout.addWidget(detail)
        self._set_highlight(False)

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
        self.style().unpolish(self)
        self.style().polish(self)


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
