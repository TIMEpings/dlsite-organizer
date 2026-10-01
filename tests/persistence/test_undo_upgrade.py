"""Fresh and pre-intent v1.3.1 journals share the same undo contract."""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dlsite_organizer.domain.rename_execution import (
    ExecutionStatus,
    TransactionStatus,
    UndoStatus,
    UndoTransactionState,
)
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import JournalError, TransactionJournal
from dlsite_organizer.services.undo_service import UndoService

_V131_SCHEMA = """
CREATE TABLE rename_transactions (
    id VARCHAR(64) NOT NULL PRIMARY KEY,
    root VARCHAR NOT NULL,
    created_at DATETIME NOT NULL,
    completed_at DATETIME,
    status VARCHAR(32) NOT NULL,
    recovery_stage VARCHAR(64),
    recovery_error VARCHAR,
    recovery_sequence INTEGER
);
CREATE TABLE rename_operations (
    id INTEGER NOT NULL PRIMARY KEY,
    transaction_id VARCHAR(64) NOT NULL REFERENCES rename_transactions(id),
    sequence INTEGER NOT NULL,
    source_path VARCHAR NOT NULL,
    target_path VARCHAR NOT NULL,
    status VARCHAR(32) NOT NULL,
    error VARCHAR,
    executed_at DATETIME,
    undo_status VARCHAR(32) NOT NULL,
    undo_error VARCHAR,
    undone_at DATETIME,
    CONSTRAINT uq_rename_operation_transaction_sequence UNIQUE (transaction_id, sequence)
);
CREATE INDEX ix_rename_transactions_status ON rename_transactions(status);
CREATE INDEX ix_rename_operations_transaction_id ON rename_operations(transaction_id);
CREATE INDEX ix_rename_operations_status ON rename_operations(status);
CREATE INDEX ix_rename_operations_undo_status ON rename_operations(undo_status);
"""


class Filesystem:
    def rename(self, source: Path, target: Path) -> None:
        source.rename(target)


@pytest.mark.parametrize("legacy", [False, True], ids=["fresh", "v1.3.1"])
@pytest.mark.parametrize("interrupt", [False, True], ids=["terminal", "unfinished"])
def test_fresh_and_legacy_profiles_preserve_history_and_support_durable_undo(
    tmp_path: Path, legacy: bool, interrupt: bool,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    (root / "renamed").mkdir()
    path = tmp_path / "journal.sqlite3"
    now = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)
    if legacy:
        with sqlite3.connect(path) as connection:
            connection.executescript(_V131_SCHEMA)
            connection.execute(
                "INSERT INTO rename_transactions VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL)",
                ("legacy", str(root), now.isoformat(), now.isoformat(), "completed"),
            )
            connection.execute(
                "INSERT INTO rename_operations VALUES "
                "(1, ?, 1, ?, ?, 'success', NULL, ?, 'pending', NULL, NULL)",
                ("legacy", str(root / "original"), str(root / "renamed"), now.isoformat()),
            )
    database = Database(path)
    database.initialize()
    database.initialize()  # Normal schema initialization is idempotent.
    journal = TransactionJournal(database)
    if legacy:
        transaction_id = "legacy"
    else:
        transaction = journal.create_transaction(root, ((root / "original", root / "renamed"),))
        transaction_id = transaction.transaction_id
        journal.mark_operation_success(transaction_id, 1, now)
        journal.mark_transaction_completed(transaction_id, now)
    try:
        before = journal.get_transaction(transaction_id)
        assert before.status is TransactionStatus.COMPLETED
        assert before.completed_at == now
        assert before.undo_state is None
        assert before.undo_started_at is None
        assert before.undo_completed_at is None
        assert before.recovery_stage is before.recovery_error is before.recovery_sequence is None
        assert before.operations[0].error is before.operations[0].undo_error is None
        assert before.operations[0].status is ExecutionStatus.SUCCESS
        assert before.operations[0].undo_status is UndoStatus.PENDING
        assert journal.latest_undoable() == before
        assert journal.list_transactions(limit=10)[0].display_status is TransactionStatus.COMPLETED
        if interrupt:
            journal.begin_undo(transaction_id, datetime.now(UTC))
            database.dispose()
            database.initialize()
            unresolved = journal.find_unresolved_transaction()
            assert unresolved is not None
            assert unresolved.status is TransactionStatus.COMPLETED
            assert unresolved.completed_at == now
            assert unresolved.undo_state is UndoTransactionState.IN_PROGRESS
            assert unresolved.operations == before.operations
            assert (root / "renamed").is_dir()
            with pytest.raises(JournalError):
                journal.create_transaction(root, ((root / "X", root / "Y"),))
        else:
            result = UndoService(journal, filesystem=Filesystem()).undo(
                transaction_id, confirmed=True,
            )
            assert result.status is TransactionStatus.UNDONE
            after = journal.get_transaction(transaction_id)
            assert after.completed_at == now
            assert after.undo_state is UndoTransactionState.SUCCESS
            assert after.undo_started_at is not None
            assert after.undo_completed_at is not None
            assert journal.find_unresolved_transaction() is None
            assert (root / "original").is_dir()
            assert not (root / "renamed").exists()
            journal.create_transaction(root, ((root / "X", root / "Y"),))
    finally:
        database.dispose()


@pytest.mark.parametrize("status", [TransactionStatus.PENDING, TransactionStatus.RECOVERY_REQUIRED])
def test_existing_forward_recovery_still_blocks_undo_and_rename(
    tmp_path: Path, status: TransactionStatus,
) -> None:
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = tmp_path / "library"
    root.mkdir()
    transaction = journal.create_transaction(root, ((root / "X", root / "Y"),))
    if status is TransactionStatus.RECOVERY_REQUIRED:
        journal.mark_recovery_required(
            transaction.transaction_id, 1, "uncertain", datetime.now(UTC),
        )
    database.dispose()
    database.initialize()
    try:
        unresolved = journal.find_unresolved_transaction()
        assert unresolved is not None
        assert unresolved.status is status
        assert unresolved.undo_state is None
        assert unresolved.operations[0].status is ExecutionStatus.PENDING
        assert unresolved.recovery_stage == (
            "forward" if status is TransactionStatus.RECOVERY_REQUIRED else None
        )
        with pytest.raises(JournalError):
            journal.begin_undo(transaction.transaction_id, datetime.now(UTC))
        with pytest.raises(JournalError):
            journal.create_transaction(root, ((root / "A", root / "B"),))
    finally:
        database.dispose()
