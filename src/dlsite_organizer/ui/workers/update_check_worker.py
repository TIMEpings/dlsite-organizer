"""Background execution adapter for the manual release update check."""

from __future__ import annotations

import logging
from typing import Protocol

from PySide6.QtCore import QObject, Signal, Slot

from dlsite_organizer import __version__
from dlsite_organizer.services.update_checker import UpdateCheckResult, UpdateCheckStatus

logger = logging.getLogger(__name__)


class UpdateCheckServiceLike(Protocol):
    """Small injection boundary shared by the worker and About page."""

    def check(self) -> UpdateCheckResult: ...


class UpdateCheckWorker(QObject):
    """Run the release check away from the GUI thread."""

    finished = Signal(object)

    def __init__(self, service: UpdateCheckServiceLike) -> None:
        super().__init__()
        self._service = service

    @Slot()
    def run(self) -> None:
        """Call the service and always return a structured result to the UI."""
        try:
            result = self._service.check()
        except Exception:
            logger.exception("Unexpected exception escaped update check service")
            result = UpdateCheckResult(
                status=UpdateCheckStatus.CHECK_FAILED,
                current_version=__version__,
                detail="unexpected update check failure",
            )
        self.finished.emit(result)
