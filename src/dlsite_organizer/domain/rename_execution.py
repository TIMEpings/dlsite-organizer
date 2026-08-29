"""Domain records for filesystem rename execution and undo.

These types deliberately do not know about Qt, SQLite, or the filesystem.  A
rename plan is a proposed business decision; the records in this module
describe what an execution service actually attempted and what the journal
knows about it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class TransactionStatus(StrEnum):
    """Lifecycle state of one filesystem operation journal."""

    PENDING = "pending"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    UNDONE = "undone"
    UNDO_PARTIAL = "undo_partial"
    RECOVERY_REQUIRED = "recovery_required"


class ExecutionStatus(StrEnum):
    """Fact about the forward execution of one planned rename."""

    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    NOT_EXECUTED = "not_executed"
    PRECONDITION_FAILED = "precondition_failed"
    REJECTED = "rejected"
    RECOVERY_REQUIRED = "recovery_required"


class UndoStatus(StrEnum):
    """Fact about undoing one successful forward rename."""

    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class RenameOperation:
    """A journaled source-to-target operation and its undo state."""

    transaction_id: str
    sequence: int
    source_path: Path
    target_path: Path
    status: ExecutionStatus = ExecutionStatus.PENDING
    error: str | None = None
    executed_at: datetime | None = None
    undo_status: UndoStatus = UndoStatus.PENDING
    undo_error: str | None = None
    undone_at: datetime | None = None
    operation_id: int | None = None


@dataclass(frozen=True, slots=True)
class RenameTransaction:
    """A durable journal snapshot for one confirmed execution batch."""

    transaction_id: str
    root: Path
    created_at: datetime
    completed_at: datetime | None
    status: TransactionStatus
    operations: tuple[RenameOperation, ...]


@dataclass(frozen=True, slots=True)
class RenameExecutionResult:
    """Result returned by the forward execution service."""

    status: TransactionStatus
    transaction: RenameTransaction | None
    operations: tuple[RenameOperation, ...] = ()
    error: str | None = None

    @property
    def success_count(self) -> int:
        return sum(operation.status is ExecutionStatus.SUCCESS for operation in self.operations)

    @property
    def failed_count(self) -> int:
        return sum(operation.status is ExecutionStatus.FAILED for operation in self.operations)

    @property
    def not_executed_count(self) -> int:
        return sum(
            operation.status
            in {
                ExecutionStatus.NOT_EXECUTED,
                ExecutionStatus.PRECONDITION_FAILED,
                ExecutionStatus.REJECTED,
            }
            for operation in self.operations
        )

    @property
    def transaction_id(self) -> str | None:
        return self.transaction.transaction_id if self.transaction is not None else None


@dataclass(frozen=True, slots=True)
class UndoResult:
    """Result returned by the journal-driven undo service."""

    status: TransactionStatus
    transaction: RenameTransaction | None
    operations: tuple[RenameOperation, ...] = ()
    error: str | None = None

    @property
    def success_count(self) -> int:
        return sum(operation.undo_status is UndoStatus.SUCCESS for operation in self.operations)

    @property
    def failed_count(self) -> int:
        return sum(
            operation.undo_status in {UndoStatus.FAILED, UndoStatus.CONFLICT}
            for operation in self.operations
        )

    @property
    def pending_count(self) -> int:
        return sum(
            operation.status is ExecutionStatus.SUCCESS
            and operation.undo_status is not UndoStatus.SUCCESS
            for operation in self.operations
        )

    @property
    def transaction_id(self) -> str | None:
        return self.transaction.transaction_id if self.transaction is not None else None


ExecutionResult = RenameExecutionResult
