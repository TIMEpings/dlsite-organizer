"""Qt adapter for the read-only organizer preview use case."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, Signal, Slot

from dlsite_organizer.services.folder_scanner import FolderScanFailure
from dlsite_organizer.services.organizer import OrganizerService

logger = logging.getLogger(__name__)


class OrganizerWorker(QObject):
    """Run local scan and sequential HTTP lookups outside the GUI thread."""

    progress = Signal(int, int, str)
    result_ready = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        organizer_service: OrganizerService,
        root_path: Path | str,
        selected_paths: Sequence[Path | str] | None = None,
    ) -> None:
        super().__init__()
        self._organizer_service = organizer_service
        self._root_path = root_path
        self._selected_paths = tuple(selected_paths) if selected_paths is not None else None
        self._cancel_event = Event()

    @Slot()
    def run(self) -> None:
        """Build a preview and retain completed rows when cancellation is requested."""
        try:
            if self._selected_paths is None:
                preview = self._organizer_service.preview(
                    self._root_path,
                    progress_callback=self._report_progress,
                    cancel_check=self._cancel_event.is_set,
                )
            else:
                preview = self._organizer_service.preview_paths(
                    self._root_path,
                    self._selected_paths,
                    progress_callback=self._report_progress,
                    cancel_check=self._cancel_event.is_set,
                )
            self.result_ready.emit(preview)
        except FolderScanFailure as exc:
            self.failed.emit(exc.user_message)
        except Exception:
            logger.exception("Unexpected exception escaped organizer services")
            self.failed.emit("扫描时发生意外错误，详细信息已写入日志。")
        finally:
            self.finished.emit()

    def cancel(self) -> None:
        """Request cooperative cancellation; an in-flight HTTP call may finish first."""
        self._cancel_event.set()

    @Slot(int, int, str)
    def _report_progress(self, completed: int, total: int, work_code: str) -> None:
        self.progress.emit(completed, total, work_code)
