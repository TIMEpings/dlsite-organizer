"""UI-neutral admission and serialization for Quick Action batches."""

from __future__ import annotations

import logging
import os
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from threading import Lock
from typing import Protocol

from PySide6.QtCore import QObject, Signal

from dlsite_organizer.services.drop_input import normalized_path_identity

logger = logging.getLogger(__name__)


class QuickActionAdmissionStatus(StrEnum):
    """Stable outcomes for submitting a Quick Action to the controller."""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    QUEUE_FULL = "queue_full"
    SHUTTING_DOWN = "shutting_down"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class QuickActionRequest:
    """The normalized paths and identities belonging to one admitted request.

    ``path`` and ``identity`` remain the first-item compatibility view used by
    the original one-directory controller contract.  ``paths`` and
    ``identities`` let one queued item preserve the existing multi-directory
    Quick Rename batch semantics.
    """

    request_id: int
    path: Path
    identity: str
    paths: tuple[Path, ...] = ()
    identities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.paths:
            object.__setattr__(self, "paths", (self.path,))
        if not self.identities:
            object.__setattr__(self, "identities", (self.identity,))


@dataclass(frozen=True, slots=True)
class QuickActionAdmissionResult:
    """Typed result returned synchronously by :meth:`QuickActionController.submit`."""

    status: QuickActionAdmissionStatus
    request: QuickActionRequest | None = None
    queue_depth: int = 0

    @property
    def accepted(self) -> bool:
        return self.status is QuickActionAdmissionStatus.ACCEPTED

    @property
    def path(self) -> Path | None:
        """Return the admitted path, when this result represents an admission."""
        return self.request.path if self.request is not None else None

    @property
    def paths(self) -> tuple[Path, ...]:
        """Return all paths in the admitted user action."""
        return self.request.paths if self.request is not None else ()


@dataclass(frozen=True, slots=True)
class QuickActionProgress:
    """UI-neutral progress emitted for the currently running request."""

    request: QuickActionRequest
    message: str


@dataclass(frozen=True, slots=True)
class QuickActionFinished:
    """UI-neutral completion payload emitted for every started request."""

    request: QuickActionRequest
    result: object | None = None
    error: str | None = None


QuickActionCompletion = Callable[[object | None, str | None], None]


class QuickActionRunner(Protocol):
    """Minimal runner seam owned by the caller that constructs the worker."""

    def start(self) -> None:
        """Start the already-created worker or equivalent operation."""
        ...


class _LifecycleSignal(Protocol):
    def emit(self, *args: object) -> None:
        ...


QuickActionRunnerFactory = Callable[
    [QuickActionRequest, QuickActionCompletion], QuickActionRunner
]


class QuickActionController(QObject):
    """Admit and serialize Quick Actions in FIFO order.

    The controller owns only admission, identity, queue, and lifecycle state.
    A queued item is one user action and may contain one or more paths.  A
    runner factory owns worker/thread construction and reports completion via
    the callback passed to it.  Consequently this class has no knowledge of
    QuickRenameService, IPC, or the filesystem.
    """

    MAX_PENDING = 8

    request_started = Signal(object)
    request_progress = Signal(object)
    request_finished = Signal(object)
    queue_depth_changed = Signal(int)
    active_changed = Signal(object)

    def __init__(
        self,
        runner_factory: QuickActionRunnerFactory,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._runner_factory = runner_factory
        self._lock = Lock()
        self._next_request_id = 1
        self._active: QuickActionRequest | None = None
        self._active_runner: QuickActionRunner | None = None
        self._pending: deque[QuickActionRequest] = deque()
        self._identities: set[str] = set()
        self._shutting_down = False

    @property
    def active_request(self) -> QuickActionRequest | None:
        with self._lock:
            return self._active

    @property
    def pending_requests(self) -> tuple[QuickActionRequest, ...]:
        with self._lock:
            return tuple(self._pending)

    @property
    def queue_depth(self) -> int:
        """Return the number of admitted requests waiting behind the active one."""
        with self._lock:
            return len(self._pending)

    @property
    def is_shutting_down(self) -> bool:
        with self._lock:
            return self._shutting_down

    def submit(
        self,
        path: Path | str | os.PathLike[str] | Sequence[Path | str | os.PathLike[str]],
    ) -> QuickActionAdmissionResult:
        """Admit one user action, start it immediately, or enqueue it FIFO.

        A batch is normalized and checked as a whole.  Duplicate identities
        inside one batch are rejected before admission; any identity already
        active or pending returns ``DUPLICATE`` for the whole action.
        """
        try:
            normalized_paths = _normalize_request_paths(path)
            identities = tuple(normalized_path_identity(item) for item in normalized_paths)
        except (OSError, TypeError, ValueError):
            with self._lock:
                queue_depth = len(self._pending)
            return QuickActionAdmissionResult(
                QuickActionAdmissionStatus.REJECTED,
                queue_depth=queue_depth,
            )
        start_request: QuickActionRequest | None = None
        queue_depth_changed = False
        previous_depth = 0

        with self._lock:
            previous_depth = len(self._pending)
            if self._shutting_down:
                return QuickActionAdmissionResult(
                    QuickActionAdmissionStatus.SHUTTING_DOWN,
                    queue_depth=previous_depth,
                )
            if len(set(identities)) != len(identities):
                return QuickActionAdmissionResult(
                    QuickActionAdmissionStatus.REJECTED,
                    queue_depth=previous_depth,
                )
            if any(identity in self._identities for identity in identities):
                return QuickActionAdmissionResult(
                    QuickActionAdmissionStatus.DUPLICATE,
                    queue_depth=previous_depth,
                )
            if self._active is not None and len(self._pending) >= self.MAX_PENDING:
                return QuickActionAdmissionResult(
                    QuickActionAdmissionStatus.QUEUE_FULL,
                    queue_depth=previous_depth,
                )

            request = QuickActionRequest(
                self._next_request_id,
                normalized_paths[0],
                identities[0],
                normalized_paths,
                identities,
            )
            self._next_request_id += 1
            self._identities.update(identities)
            if self._active is None:
                self._active = request
                start_request = request
            else:
                self._pending.append(request)
            queue_depth_changed = len(self._pending) != previous_depth
            result = QuickActionAdmissionResult(
                QuickActionAdmissionStatus.ACCEPTED,
                request=request,
                queue_depth=len(self._pending),
            )

        if queue_depth_changed:
            self._emit(self.queue_depth_changed, result.queue_depth)
        if start_request is not None:
            self._emit(self.active_changed, start_request)
            self._emit(self.request_started, start_request)
            self._start(start_request)
        return result

    def begin_shutdown(self) -> None:
        """Reject new work and discard pending work without interrupting active work."""
        with self._lock:
            if self._shutting_down:
                return
            self._shutting_down = True
            previous_depth = len(self._pending)
            for request in self._pending:
                self._discard_identities(request)
            self._pending.clear()

        if previous_depth:
            self._emit(self.queue_depth_changed, 0)

    def complete(
        self,
        request: QuickActionRequest | int,
        result: object | None = None,
        error: object | None = None,
    ) -> None:
        """Complete a request; stale or duplicate completion callbacks are ignored."""
        request_id = request.request_id if isinstance(request, QuickActionRequest) else request
        self._complete(request_id, result, _error_text(error))

    def report_progress(self, request: QuickActionRequest | int, message: str) -> None:
        """Publish progress only while the supplied request is active."""
        request_id = request.request_id if isinstance(request, QuickActionRequest) else request
        if not isinstance(message, str):
            return
        with self._lock:
            active = self._active
            if active is None or active.request_id != request_id:
                return
        self._emit(self.request_progress, QuickActionProgress(active, message))

    def _start(self, request: QuickActionRequest) -> None:
        def completion(result: object | None = None, error: object | None = None) -> None:
            self._complete(request.request_id, result, _error_text(error))

        try:
            runner = self._runner_factory(request, completion)
        except Exception as exc:
            self._complete(request.request_id, None, f"runner creation failed: {exc}")
            return

        with self._lock:
            if self._active is not request:
                # A factory should only construct a runner.  If a broken
                # factory completed synchronously, do not start a stale runner.
                return
            if self._shutting_down:
                self._active = None
                self._active_runner = None
                self._discard_identities(request)
                discard_active = True
            else:
                self._active_runner = runner
                discard_active = False
        if discard_active:
            self._emit(self.active_changed, None)
            return

        try:
            runner.start()
        except Exception as exc:
            self._complete(request.request_id, None, f"runner start failed: {exc}")

    def _complete(self, request_id: int, result: object | None, error: str | None) -> None:
        next_request: QuickActionRequest | None = None
        with self._lock:
            active = self._active
            if active is None or active.request_id != request_id:
                return
            self._active = None
            self._active_runner = None
            self._discard_identities(active)
            previous_depth = len(self._pending)
            if not self._shutting_down and self._pending:
                next_request = self._pending.popleft()
                self._active = next_request
            queue_depth = len(self._pending)

        self._emit(self.request_finished, QuickActionFinished(active, result, error))
        if queue_depth != previous_depth:
            self._emit(self.queue_depth_changed, queue_depth)
        self._emit(self.active_changed, next_request)
        if next_request is not None:
            self._emit(self.request_started, next_request)
            self._start(next_request)

    @staticmethod
    def _emit(signal: _LifecycleSignal, *args: object) -> None:
        """Keep observer failures from blocking queue progression."""
        try:
            signal.emit(*args)
        except Exception:
            logger.exception("Quick Action lifecycle observer failed")

    def _discard_identities(self, request: QuickActionRequest) -> None:
        """Release every path identity belonging to one queued user action."""
        self._identities.difference_update(request.identities)


def _error_text(error: object | None) -> str | None:
    if error is None:
        return None
    return str(error)


def _normalize_request_paths(
    value: Path | str | os.PathLike[str] | Sequence[Path | str | os.PathLike[str]],
) -> tuple[Path, ...]:
    """Normalize one scalar path or one explicit path sequence as a batch."""
    if isinstance(value, (str, os.PathLike)):
        values: tuple[Path | str | os.PathLike[str], ...] = (value,)
    elif isinstance(value, Sequence):
        values = tuple(value)
    else:
        raise TypeError("quick action paths must be a path or sequence")
    if not values:
        raise ValueError("quick action paths must not be empty")
    return tuple(Path(item).expanduser().absolute() for item in values)


# Short aliases keep the module convenient for callers without changing the
# explicit, protocol-facing names above.
AdmissionStatus = QuickActionAdmissionStatus
AdmissionResult = QuickActionAdmissionResult
