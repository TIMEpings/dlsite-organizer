"""Background execution adapter for the lookup use case."""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal, Slot

from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupFailure, LookupService

logger = logging.getLogger(__name__)


class LookupWorker(QObject):
    """Run services off the main thread and emit UI-neutral values."""

    result_ready = Signal(object)
    cover_ready = Signal(bytes)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        lookup_service: LookupService,
        cover_service: CoverService,
        raw_workno: str,
    ) -> None:
        super().__init__()
        self._lookup_service = lookup_service
        self._cover_service = cover_service
        self._raw_workno = raw_workno

    @Slot()
    def run(self) -> None:
        """Execute lookup and optional cover retrieval in the worker thread."""
        try:
            result = self._lookup_service.lookup(self._raw_workno)
            self.result_ready.emit(result)
            if result.work.cover_url:
                cover = self._cover_service.fetch(result.work.cover_url)
                if cover is not None:
                    self.cover_ready.emit(cover)
        except LookupFailure as exc:
            self.failed.emit(exc.user_message)
        except Exception:
            logger.exception("Unexpected exception escaped lookup services")
            self.failed.emit("查询时发生意外错误，详细信息已写入日志。")
        finally:
            self.finished.emit()
