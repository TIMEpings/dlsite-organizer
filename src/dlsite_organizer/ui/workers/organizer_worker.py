"""Qt adapter for the read-only organizer preview use case."""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, QThread, Signal, Slot

from dlsite_organizer.services.folder_scanner import FolderScanFailure
from dlsite_organizer.services.organizer import OrganizerPreview, OrganizerService
from dlsite_organizer.ui.state.organizer_state import merge_previews

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class OrganizerWorkerResult:
    request_id: int
    preview_revision: int
    preview: OrganizerPreview
    append_reconciled: bool = False


class OrganizerWorker(QObject):
    """Run local scan and sequential HTTP lookups outside the GUI thread."""

    progress = Signal(int, int, str)
    result_ready = Signal(object)
    failed = Signal(str)
    request_failed = Signal(int, int, str)
    finished = Signal()

    def __init__(
        self,
        organizer_service: OrganizerService,
        root_path: Path | str,
        selected_paths: Sequence[Path | str] | None = None,
        *,
        request_id: int = 0,
        preview_revision: int = 0,
        reconcile: bool = False,
        append_to: OrganizerPreview | None = None,
    ) -> None:
        super().__init__()
        self._organizer_service = organizer_service.snapshot()
        self._request_id = request_id
        self._preview_revision = preview_revision
        self._root_path = root_path
        self._selected_paths = tuple(selected_paths) if selected_paths is not None else None
        self._reconcile = reconcile
        self._append_to = append_to
        self._cancel_event = Event()

    @Slot()
    def run(self) -> None:
        """Build a preview and retain completed rows when cancellation is requested."""
        thread = QThread.currentThread()
        logger.debug(
            "organizer worker started thread=%s ident=%s root=%s",
            thread.objectName() or "<unnamed>",
            threading.get_ident(),
            self._root_path,
        )
        try:
            if self._reconcile:
                assert self._selected_paths is not None
                preview = self._organizer_service.refresh_preview_scope(
                    self._root_path, tuple(Path(path) for path in self._selected_paths),
                    progress_callback=self._report_progress,
                    cancel_check=self._cancel_event.is_set,
                )
            elif self._selected_paths is None:
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
            append_reconciled = self._append_to is not None and not preview.cancelled
            if append_reconciled:
                assert self._append_to is not None
                preview = self._organizer_service.reevaluate_preview(
                    merge_previews(self._append_to, preview), recheck_ready=True)
            self.result_ready.emit(OrganizerWorkerResult(
                self._request_id, self._preview_revision, preview, append_reconciled))
            logger.debug(
                "organizer worker success emitted thread=%s plans=%d cancelled=%s",
                thread.objectName() or "<unnamed>",
                len(preview.plans),
                preview.cancelled,
            )
        except FolderScanFailure as exc:
            self._report_failure(exc.user_message)
            logger.debug(
                "organizer worker error emitted thread=%s kind=%s",
                thread.objectName() or "<unnamed>",
                exc.kind,
            )
        except Exception:
            logger.exception("Unexpected exception escaped organizer services")
            self._report_failure("扫描时发生意外错误，详细信息已写入日志。")
        finally:
            self.finished.emit()
            logger.debug(
                "organizer worker finished emitted thread=%s",
                thread.objectName() or "<unnamed>",
            )

    def cancel(self) -> None:
        """Request cooperative cancellation; an in-flight HTTP call may finish first."""
        logger.debug("organizer worker cancellation requested")
        self._cancel_event.set()

    def _report_failure(self, message: str) -> None:
        self.request_failed.emit(self._request_id, self._preview_revision, message)
        self.failed.emit(message)

    @Slot(int, int, str)
    def _report_progress(self, completed: int, total: int, work_code: str) -> None:
        self.progress.emit(completed, total, work_code)
