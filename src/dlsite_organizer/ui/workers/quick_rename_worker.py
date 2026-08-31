"""Qt adapter for the background Quick Rename operation."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from dlsite_organizer.services.quick_rename import QuickRenameService

logger = logging.getLogger(__name__)


class QuickRenameWorker(QObject):
    """Run lookup, planning, journaling, and execution away from Qt widgets."""

    progress = Signal(str)
    result_ready = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, service: QuickRenameService, paths: Sequence[Path | str]) -> None:
        super().__init__()
        self._service = service
        self._paths = tuple(paths)

    @Slot()
    def run(self) -> None:
        try:
            result = self._service.rename(
                self._paths,
                progress_callback=self.progress.emit,
            )
            self.result_ready.emit(result)
        except Exception:
            logger.exception("Unexpected exception escaped Quick Rename service")
            self.failed.emit("轻量模式执行失败，详细信息已写入日志。")
        finally:
            self.finished.emit()
