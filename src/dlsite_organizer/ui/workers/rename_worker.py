from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Slot

from dlsite_organizer.services.rename_executor import ExecutionProgressCallback

logger = logging.getLogger(__name__)


class RenameActionWorker(QObject):
    progress = Signal(int, int, str, str)
    result_ready = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        action: Callable[[ExecutionProgressCallback], object],
    ) -> None:
        super().__init__()
        self._action = action

    @Slot()
    def run(self) -> None:
        try:
            result = self._action(self._report_progress)
            self.result_ready.emit(result)
        except Exception:
            logger.exception('Unexpected exception escaped rename action')
            self.failed.emit('执行重命名时发生意外错误，详细信息已写入日志。')
        finally:
            self.finished.emit()

    @Slot(int, int, object, object)
    def _report_progress(
        self, completed: int, total: int, source: Path, target: Path
    ) -> None:
        self.progress.emit(completed, total, str(source), str(target))
