"""Read-only access to persisted rename history and current path observations."""

from __future__ import annotations

import logging
import stat
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from dlsite_organizer.domain.rename_execution import RenameTransaction, RenameTransactionSummary
from dlsite_organizer.persistence.rename_journal import RenameJournal

logger = logging.getLogger(__name__)


class CurrentPathState(StrEnum):
    """A no-follow observation of a path at the time it was inspected."""

    DIRECTORY = "directory"
    FILE = "file"
    OTHER = "other"
    LINK_LIKE = "link_like"
    ABSENT = "absent"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class CurrentPathObservation:
    """Current filesystem metadata, explicitly separate from journal facts."""

    path: Path
    state: CurrentPathState
    error: str | None = None


@dataclass(frozen=True, slots=True)
class OperationPathObservations:
    """Current source and target path observations for one journal operation."""

    sequence: int
    source: CurrentPathObservation
    target: CurrentPathObservation


PathObserver = Callable[[Path], CurrentPathObservation]


class RenameHistoryService:
    """Expose small read APIs without giving the UI access to SQLite."""

    def __init__(
        self,
        journal: RenameJournal,
        *,
        path_observer: PathObserver | None = None,
    ) -> None:
        self._journal = journal
        self._path_observer = path_observer or _observe_current_path

    def list_recent(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[RenameTransactionSummary, ...]:
        """Return a bounded page of persisted transactions, newest first."""
        return self._journal.list_transactions(limit=limit, offset=offset)

    def get_transaction(self, transaction_id: str) -> RenameTransaction:
        """Load the recorded transaction and its operations by ID."""
        return self._journal.get_transaction(transaction_id)

    def observe_paths(
        self, transaction: RenameTransaction
    ) -> tuple[OperationPathObservations, ...]:
        """Observe recorded source and target paths without following link-like entries."""
        observations: list[OperationPathObservations] = []
        for operation in transaction.operations:
            observations.append(
                OperationPathObservations(
                    sequence=operation.sequence,
                    source=self._safe_observe(operation.source_path),
                    target=self._safe_observe(operation.target_path),
                )
            )
        return tuple(observations)

    def _safe_observe(self, path: Path) -> CurrentPathObservation:
        try:
            return self._path_observer(path)
        except Exception as exc:  # A path observation must never hide journal facts.
            logger.info("Unable to inspect current rename path %s: %s", path, exc)
            return CurrentPathObservation(path, CurrentPathState.ERROR, str(exc))


def _observe_current_path(path: Path) -> CurrentPathObservation:
    """Classify one entry from lstat metadata so links are never followed."""
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return CurrentPathObservation(path, CurrentPathState.ABSENT)
    except OSError as exc:
        return CurrentPathObservation(path, CurrentPathState.ERROR, str(exc))

    if stat.S_ISLNK(metadata.st_mode):
        return CurrentPathObservation(path, CurrentPathState.LINK_LIKE)
    is_junction = getattr(path, "is_junction", None)
    if is_junction is not None:
        try:
            if is_junction():
                return CurrentPathObservation(path, CurrentPathState.LINK_LIKE)
        except OSError as exc:
            return CurrentPathObservation(path, CurrentPathState.ERROR, str(exc))
    if stat.S_ISDIR(metadata.st_mode):
        state = CurrentPathState.DIRECTORY
    elif stat.S_ISREG(metadata.st_mode):
        state = CurrentPathState.FILE
    else:
        state = CurrentPathState.OTHER
    return CurrentPathObservation(path, state)
