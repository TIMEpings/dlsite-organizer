"""SQLite-backed durable journal for filesystem rename transactions."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from dlsite_organizer.domain.rename_execution import (
    ExecutionStatus,
    RenameOperation,
    RenameTransaction,
    RenameTransactionSummary,
    TransactionStatus,
    UndoStatus,
)
from dlsite_organizer.persistence.database import (
    Database,
    RenameOperationRecord,
    RenameTransactionRecord,
)

logger = logging.getLogger(__name__)


class JournalError(RuntimeError):
    """A journal read or write could not be completed safely."""


class JournalUnavailableError(JournalError):
    """The application has no usable initialized journal."""


class RenameJournal(Protocol):
    """Narrow journal seam used by execution and undo services."""

    @property
    def available(self) -> bool:
        ...

    def create_transaction(
        self, root: Path, operations: Sequence[tuple[Path, Path]]
    ) -> RenameTransaction:
        ...

    def get_transaction(self, transaction_id: str) -> RenameTransaction:
        ...

    def list_transactions(
        self, *, limit: int, offset: int = 0
    ) -> tuple[RenameTransactionSummary, ...]:
        """Return a bounded page ordered newest first, with a stable tie-breaker."""
        ...

    def latest_undoable(self) -> RenameTransaction | None:
        ...

    def find_unresolved_transaction(self) -> RenameTransaction | None:
        ...

    def mark_operation_success(self, transaction_id: str, sequence: int, when: datetime) -> None:
        ...

    def record_execution_failure(
        self,
        transaction_id: str,
        sequence: int,
        error: str,
        status: TransactionStatus,
        when: datetime,
    ) -> None:
        ...

    def mark_transaction_completed(self, transaction_id: str, when: datetime) -> None:
        ...

    def mark_recovery_required(
        self, transaction_id: str, sequence: int | None, error: str, when: datetime
    ) -> None:
        ...

    def mark_undo_recovery_required(
        self, transaction_id: str, sequence: int | None, error: str, when: datetime
    ) -> None:
        ...

    def mark_undo_conflict(self, transaction_id: str, sequence: int, error: str) -> None:
        ...

    def mark_undo_success(self, transaction_id: str, sequence: int, when: datetime) -> None:
        ...

    def record_undo_failure(
        self, transaction_id: str, sequence: int, error: str, when: datetime
    ) -> None:
        ...

    def mark_transaction_undone(self, transaction_id: str, when: datetime) -> None:
        ...


class UnavailableRenameJournal:
    """No-op journal used when SQLite initialization failed."""

    available = False

    def _raise(self) -> None:
        raise JournalUnavailableError('事务日志不可用，已禁止文件系统重命名。')

    def create_transaction(
        self, root: Path, operations: Sequence[tuple[Path, Path]]
    ) -> RenameTransaction:
        del root, operations
        self._raise()
        raise AssertionError('unreachable')

    def get_transaction(self, transaction_id: str) -> RenameTransaction:
        del transaction_id
        self._raise()
        raise AssertionError('unreachable')

    def list_transactions(
        self, *, limit: int, offset: int = 0
    ) -> tuple[RenameTransactionSummary, ...]:
        del limit, offset
        self._raise()
        raise AssertionError('unreachable')

    def latest_undoable(self) -> RenameTransaction | None:
        self._raise()
        raise AssertionError('unreachable')

    def find_unresolved_transaction(self) -> RenameTransaction | None:
        self._raise()
        raise AssertionError('unreachable')

    def mark_operation_success(self, transaction_id: str, sequence: int, when: datetime) -> None:
        del transaction_id, sequence, when
        self._raise()

    def record_execution_failure(
        self,
        transaction_id: str,
        sequence: int,
        error: str,
        status: TransactionStatus,
        when: datetime,
    ) -> None:
        del transaction_id, sequence, error, status, when
        self._raise()

    def mark_transaction_completed(self, transaction_id: str, when: datetime) -> None:
        del transaction_id, when
        self._raise()

    def mark_recovery_required(
        self, transaction_id: str, sequence: int | None, error: str, when: datetime
    ) -> None:
        del transaction_id, sequence, error, when
        self._raise()

    def mark_undo_recovery_required(
        self, transaction_id: str, sequence: int | None, error: str, when: datetime
    ) -> None:
        del transaction_id, sequence, error, when
        self._raise()

    def mark_undo_conflict(self, transaction_id: str, sequence: int, error: str) -> None:
        del transaction_id, sequence, error
        self._raise()

    def mark_undo_success(self, transaction_id: str, sequence: int, when: datetime) -> None:
        del transaction_id, sequence, when
        self._raise()

    def record_undo_failure(
        self, transaction_id: str, sequence: int, error: str, when: datetime
    ) -> None:
        del transaction_id, sequence, error, when
        self._raise()

    def mark_transaction_undone(self, transaction_id: str, when: datetime) -> None:
        del transaction_id, when
        self._raise()


class TransactionJournal:
    """Repository that commits journal intent and each operation update."""

    def __init__(self, database: Database) -> None:
        self._database = database

    def find_unresolved_transaction(self) -> RenameTransaction | None:
        self._ensure_available()
        try:
            with self._database.session() as session:
                record = session.scalar(
                    select(RenameTransactionRecord)
                    .where(
                        RenameTransactionRecord.status.in_(
                            [
                                TransactionStatus.PENDING.value,
                                TransactionStatus.RECOVERY_REQUIRED.value,
                            ]
                        )
                    )
                    .order_by(RenameTransactionRecord.created_at.asc())
                )
                if record is None:
                    return None
                operations = session.scalars(
                    select(RenameOperationRecord)
                    .where(RenameOperationRecord.transaction_id == record.id)
                    .order_by(RenameOperationRecord.sequence)
                ).all()
                return _to_transaction(record, operations)
        except Exception as exc:
            raise JournalError('journal health check failed') from exc

    @property
    def available(self) -> bool:
        return self._database.initialized

    def create_transaction(
        self, root: Path, operations: Sequence[tuple[Path, Path]]
    ) -> RenameTransaction:
        self._ensure_available()
        transaction_id = uuid.uuid4().hex
        now = datetime.now(UTC)
        normalized_root = _absolute_path(root)
        try:
            with self._database.session() as session:
                # Serialize the health check and transaction intent.  Two
                # independent Explorer launches must not both observe an
                # empty journal and then commit overlapping PENDING rows.
                session.execute(text('BEGIN IMMEDIATE'))
                unresolved_id = session.scalar(
                    select(RenameTransactionRecord.id)
                    .where(
                        RenameTransactionRecord.status.in_(
                            [
                                TransactionStatus.PENDING.value,
                                TransactionStatus.RECOVERY_REQUIRED.value,
                            ]
                        )
                    )
                    .limit(1)
                )
                if unresolved_id is not None:
                    raise JournalError(
                        '已有未解决的重命名事务，已阻止新的文件系统修改。'
                    )
                session.add(
                    RenameTransactionRecord(
                        id=transaction_id,
                        root=str(normalized_root),
                        created_at=now,
                        completed_at=None,
                        status=TransactionStatus.PENDING.value,
                    )
                )
                for sequence, (source, target) in enumerate(operations, start=1):
                    session.add(
                        RenameOperationRecord(
                            transaction_id=transaction_id,
                            sequence=sequence,
                            source_path=str(_absolute_path(source)),
                            target_path=str(_absolute_path(target)),
                            status=ExecutionStatus.PENDING.value,
                            error=None,
                            executed_at=None,
                            undo_status=UndoStatus.PENDING.value,
                            undo_error=None,
                            undone_at=None,
                        )
                    )
                session.commit()
        except JournalError:
            raise
        except Exception as exc:
            logger.exception('Could not create rename transaction journal %s', transaction_id)
            raise JournalError('无法建立重命名事务日志，未执行任何文件操作。') from exc
        return self.get_transaction(transaction_id)

    def get_transaction(self, transaction_id: str) -> RenameTransaction:
        self._ensure_available()
        try:
            with self._database.session() as session:
                record = session.get(RenameTransactionRecord, transaction_id)
                if record is None:
                    raise JournalError(f'找不到重命名事务日志：{transaction_id}')
                operations = session.scalars(
                    select(RenameOperationRecord)
                    .where(RenameOperationRecord.transaction_id == transaction_id)
                    .order_by(RenameOperationRecord.sequence)
                ).all()
                return _to_transaction(record, operations)
        except JournalError:
            raise
        except Exception as exc:
            logger.exception('Could not read rename transaction journal %s', transaction_id)
            raise JournalError('无法读取重命名事务日志。') from exc

    def list_transactions(
        self, *, limit: int, offset: int = 0
    ) -> tuple[RenameTransactionSummary, ...]:
        """Read a bounded page of transaction headers in deterministic order."""
        self._ensure_available()
        if not 1 <= limit <= 100:
            raise ValueError('transaction page size must be between 1 and 100')
        if offset < 0:
            raise ValueError('transaction page offset must not be negative')
        try:
            with self._database.session() as session:
                records = session.scalars(
                    select(RenameTransactionRecord)
                    .order_by(
                        RenameTransactionRecord.created_at.desc(),
                        RenameTransactionRecord.id.desc(),
                    )
                    .limit(limit)
                    .offset(offset)
                ).all()
                return tuple(_to_transaction_summary(record) for record in records)
        except ValueError:
            raise
        except Exception as exc:
            logger.exception('Could not list rename transaction journal history')
            raise JournalError('无法读取重命名历史。') from exc

    def latest_undoable(self) -> RenameTransaction | None:
        self._ensure_available()
        eligible = {
            TransactionStatus.COMPLETED.value,
            TransactionStatus.PARTIAL.value,
            TransactionStatus.UNDO_PARTIAL.value,
        }
        try:
            with self._database.session() as session:
                records = session.scalars(
                    select(RenameTransactionRecord)
                    .where(RenameTransactionRecord.status.in_(eligible))
                    .order_by(RenameTransactionRecord.created_at.desc())
                ).all()
                for record in records:
                    operations = session.scalars(
                        select(RenameOperationRecord)
                        .where(RenameOperationRecord.transaction_id == record.id)
                        .order_by(RenameOperationRecord.sequence)
                    ).all()
                    transaction = _to_transaction(record, operations)
                    if any(
                        operation.status is ExecutionStatus.SUCCESS
                        and operation.undo_status is not UndoStatus.SUCCESS
                        for operation in transaction.operations
                    ):
                        return transaction
                return None
        except Exception as exc:
            logger.exception('Could not find latest undoable rename transaction')
            raise JournalError('无法读取最近一次可撤销的重命名事务。') from exc

    def mark_operation_success(self, transaction_id: str, sequence: int, when: datetime) -> None:
        self._write_operation(
            transaction_id,
            sequence,
            {'status': ExecutionStatus.SUCCESS.value, 'executed_at': when},
        )

    def record_execution_failure(
        self,
        transaction_id: str,
        sequence: int,
        error: str,
        status: TransactionStatus,
        when: datetime,
    ) -> None:
        self._ensure_available()
        try:
            with self._database.session() as session:
                operation = _operation_or_raise(session, transaction_id, sequence)
                operation.status = ExecutionStatus.FAILED.value
                operation.error = error
                operation.executed_at = when
                later = session.scalars(
                    select(RenameOperationRecord).where(
                        RenameOperationRecord.transaction_id == transaction_id,
                        RenameOperationRecord.sequence > sequence,
                    )
                ).all()
                for pending in later:
                    pending.status = ExecutionStatus.NOT_EXECUTED.value
                    pending.error = '前一项重命名失败，后续操作未执行。'
                transaction = session.get(RenameTransactionRecord, transaction_id)
                if transaction is None:
                    raise JournalError(f'找不到重命名事务日志：{transaction_id}')
                transaction.status = status.value
                transaction.completed_at = when
                session.commit()
        except JournalError:
            raise
        except Exception as exc:
            logger.exception(
                'Could not persist failed rename operation %s/%s',
                transaction_id,
                sequence,
            )
            raise JournalError('重命名失败状态无法写入事务日志。') from exc

    def mark_transaction_completed(self, transaction_id: str, when: datetime) -> None:
        self._write_transaction(
            transaction_id,
            {'status': TransactionStatus.COMPLETED.value, 'completed_at': when},
        )

    def mark_recovery_required(
        self, transaction_id: str, sequence: int | None, error: str, when: datetime
    ) -> None:
        self._ensure_available()
        try:
            with self._database.session() as session:
                transaction = session.get(RenameTransactionRecord, transaction_id)
                if transaction is None:
                    raise JournalError(f'找不到重命名事务日志：{transaction_id}')
                transaction.status = TransactionStatus.RECOVERY_REQUIRED.value
                transaction.recovery_stage = 'forward'
                transaction.recovery_error = error
                transaction.recovery_sequence = sequence
                if sequence is not None:
                    _operation_or_raise(session, transaction_id, sequence)
                session.commit()
        except JournalError:
            raise
        except Exception as exc:
            logger.exception(
                'Could not mark transaction %s as recovery-required',
                transaction_id,
            )
            raise JournalError('事务日志无法标记为需要恢复。') from exc

    def mark_undo_conflict(self, transaction_id: str, sequence: int, error: str) -> None:
        self._write_operation(
            transaction_id,
            sequence,
            {'undo_status': UndoStatus.CONFLICT.value, 'undo_error': error},
        )

    def mark_undo_recovery_required(
        self, transaction_id: str, sequence: int | None, error: str, when: datetime
    ) -> None:
        self._ensure_available()
        try:
            with self._database.session() as session:
                transaction = session.get(RenameTransactionRecord, transaction_id)
                if transaction is None:
                    raise JournalError(f'找不到重命名事务日志：{transaction_id}')
                transaction.status = TransactionStatus.RECOVERY_REQUIRED.value
                transaction.recovery_stage = 'undo'
                transaction.recovery_error = error
                transaction.recovery_sequence = sequence
                if sequence is not None:
                    _operation_or_raise(session, transaction_id, sequence)
                session.commit()
        except JournalError:
            raise
        except Exception as exc:
            logger.exception(
                'Could not mark undo recovery for transaction %s',
                transaction_id,
            )
            raise JournalError('事务日志无法标记为需要恢复。') from exc

    def mark_undo_success(self, transaction_id: str, sequence: int, when: datetime) -> None:
        self._write_operation(
            transaction_id,
            sequence,
            {
                'undo_status': UndoStatus.SUCCESS.value,
                'undone_at': when,
                'undo_error': None,
            },
        )

    def record_undo_failure(
        self, transaction_id: str, sequence: int, error: str, when: datetime
    ) -> None:
        self._ensure_available()
        try:
            with self._database.session() as session:
                operation = _operation_or_raise(session, transaction_id, sequence)
                operation.undo_status = UndoStatus.FAILED.value
                operation.undo_error = error
                transaction = session.get(RenameTransactionRecord, transaction_id)
                if transaction is None:
                    raise JournalError(f'找不到重命名事务日志：{transaction_id}')
                transaction.status = TransactionStatus.UNDO_PARTIAL.value
                transaction.completed_at = when
                session.commit()
        except JournalError:
            raise
        except Exception as exc:
            logger.exception(
                'Could not persist failed undo operation %s/%s',
                transaction_id,
                sequence,
            )
            raise JournalError('撤销失败状态无法写入事务日志。') from exc

    def mark_transaction_undone(self, transaction_id: str, when: datetime) -> None:
        self._write_transaction(
            transaction_id,
            {'status': TransactionStatus.UNDONE.value, 'completed_at': when},
        )

    def _write_operation(
        self, transaction_id: str, sequence: int, values: dict[str, object]
    ) -> None:
        self._ensure_available()
        try:
            with self._database.session() as session:
                operation = _operation_or_raise(session, transaction_id, sequence)
                for key, value in values.items():
                    setattr(operation, key, value)
                session.commit()
        except JournalError:
            raise
        except Exception as exc:
            logger.exception(
                'Could not update rename operation %s/%s',
                transaction_id,
                sequence,
            )
            raise JournalError('重命名事务日志更新失败。') from exc

    def _write_transaction(self, transaction_id: str, values: dict[str, object]) -> None:
        self._ensure_available()
        try:
            with self._database.session() as session:
                transaction = session.get(RenameTransactionRecord, transaction_id)
                if transaction is None:
                    raise JournalError(f'找不到重命名事务日志：{transaction_id}')
                for key, value in values.items():
                    setattr(transaction, key, value)
                session.commit()
        except JournalError:
            raise
        except Exception as exc:
            logger.exception('Could not update rename transaction %s', transaction_id)
            raise JournalError('重命名事务日志更新失败。') from exc

    def _ensure_available(self) -> None:
        if not self.available:
            raise JournalUnavailableError('事务日志不可用，已禁止文件系统重命名。')


def _operation_or_raise(
    session: Session, transaction_id: str, sequence: int
) -> RenameOperationRecord:
    operation = session.scalar(
        select(RenameOperationRecord).where(
            RenameOperationRecord.transaction_id == transaction_id,
            RenameOperationRecord.sequence == sequence,
        )
    )
    if operation is None:
        raise JournalError(f'找不到重命名操作：{transaction_id}/{sequence}')
    return operation


def _to_transaction(
    record: RenameTransactionRecord, operations: Sequence[RenameOperationRecord]
) -> RenameTransaction:
    return RenameTransaction(
        transaction_id=record.id,
        root=Path(record.root),
        created_at=_as_utc(record.created_at),
        completed_at=_as_utc(record.completed_at) if record.completed_at else None,
        status=TransactionStatus(record.status),
        recovery_stage=record.recovery_stage,
        recovery_error=record.recovery_error,
        recovery_sequence=record.recovery_sequence,
        operations=tuple(
            RenameOperation(
                operation_id=operation.id,
                transaction_id=record.id,
                sequence=operation.sequence,
                source_path=Path(operation.source_path),
                target_path=Path(operation.target_path),
                status=ExecutionStatus(operation.status),
                error=operation.error,
                executed_at=_as_utc(operation.executed_at) if operation.executed_at else None,
                undo_status=UndoStatus(operation.undo_status),
                undo_error=operation.undo_error,
                undone_at=_as_utc(operation.undone_at) if operation.undone_at else None,
            )
            for operation in operations
        ),
    )


def _to_transaction_summary(record: RenameTransactionRecord) -> RenameTransactionSummary:
    return RenameTransactionSummary(
        transaction_id=record.id,
        root=Path(record.root),
        created_at=_as_utc(record.created_at),
        status=TransactionStatus(record.status),
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _absolute_path(path: Path) -> Path:
    """Normalize lexically without following a possibly changed child link."""
    return Path(path).expanduser().absolute()
