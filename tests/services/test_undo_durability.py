"""Undo durability across abrupt process exits and actual SQLite write failures."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Barrier

import pytest

from dlsite_organizer.domain.organizer import RenamePlan, RenamePlanStatus
from dlsite_organizer.domain.rename_execution import (
    ExecutionStatus,
    RenameTransaction,
    TransactionStatus,
    UndoStatus,
    UndoTransactionState,
)
from dlsite_organizer.domain.work import Work
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import JournalError, TransactionJournal
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.undo_service import UndoService


class Filesystem:
    """Use real temporary directories on either test platform."""

    def rename(self, source: Path, target: Path) -> None:
        if target.exists():
            raise FileExistsError(str(target))
        source.rename(target)


def _plan(root: Path, source: str, target: str) -> RenamePlan:
    return RenamePlan(
        source_path=root / source,
        current_name=source,
        work_code="RJ00000001",
        work=Work(workno="RJ00000001", title="Test"),
        proposed_name=target,
        target_path=root / target,
        status=RenamePlanStatus.READY,
    )


def _prepare(tmp_path: Path) -> tuple[Database, TransactionJournal, RenameTransaction]:
    root = tmp_path / "library"
    root.mkdir()
    for name in "ABC":
        (root / name).mkdir()
        (root / name / "payload").write_text(name, encoding="utf-8")
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    result = RenameExecutor(journal, filesystem=Filesystem()).execute(
        root, [_plan(root, name, name + "2") for name in "ABC"], confirmed=True,
    )
    assert result.status is TransactionStatus.COMPLETED
    assert result.transaction is not None
    return database, journal, result.transaction


# os._exit skips finally blocks, service exception handlers, and connection cleanup.
# Hooks surround real SQLite commits and real filesystem renames, without changing
# the production state machine or adding production fault-injection switches.
_CRASH_WORKER = """
import os
import sys
from pathlib import Path
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.undo_service import UndoService

db_path, transaction_id, boundary, count = sys.argv[1:]
count = int(count)
def crash(point, n=0):
    if boundary == point and n == count:
        os._exit(73)

class CrashJournal(TransactionJournal):
    progress = 0
    def begin_undo(self, transaction_id, when):
        super().begin_undo(transaction_id, when)
        crash('intent')
    def mark_undo_success(self, transaction_id, sequence, when):
        self.progress += 1
        crash('before_progress', self.progress)
        super().mark_undo_success(transaction_id, sequence, when)
        crash('after_progress', self.progress)
    def mark_transaction_undone(self, transaction_id, when):
        crash('before_terminal')
        super().mark_transaction_undone(transaction_id, when)
        crash('after_terminal')
    def record_undo_failure(self, transaction_id, sequence, error, when):
        crash('before_failure')
        super().record_undo_failure(transaction_id, sequence, error, when)
        crash('after_failure')

class Filesystem:
    def rename(self, source, target):
        if boundary in {'before_failure', 'after_failure'}:
            raise PermissionError('injected known filesystem failure')
        if target.exists():
            raise FileExistsError(str(target))
        source.rename(target)

database = Database(Path(db_path))
database.initialize()
UndoService(CrashJournal(database), filesystem=Filesystem()).undo(
    transaction_id, confirmed=True,
)
raise AssertionError('crash boundary was not reached')
"""


def _crash(path: Path, transaction_id: str, boundary: str, count: int = 0) -> None:
    result = subprocess.run(
        [sys.executable, "-c", _CRASH_WORKER, str(path), transaction_id, boundary, str(count)],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 73, result.stdout + result.stderr


def _assert_blocked(journal: TransactionJournal, transaction: RenameTransaction) -> None:
    unresolved = journal.find_unresolved_transaction()
    assert unresolved is not None
    assert unresolved.transaction_id == transaction.transaction_id
    assert unresolved.unresolved
    assert unresolved.display_status is TransactionStatus.RECOVERY_REQUIRED
    assert journal.latest_undoable() is None
    summary = journal.list_transactions(limit=10)[0]
    assert summary.display_status is TransactionStatus.RECOVERY_REQUIRED
    root = transaction.root
    (root / "unrelated").mkdir(exist_ok=True)
    result = RenameExecutor(journal, filesystem=Filesystem()).execute(
        root, [_plan(root, "unrelated", "changed")], confirmed=True,
    )
    assert result.operations[0].status is ExecutionStatus.REJECTED
    assert (root / "unrelated").is_dir()
    assert not (root / "changed").exists()
    assert UndoService(journal, filesystem=Filesystem()).undo(
        transaction.transaction_id, confirmed=True,
    ).status is TransactionStatus.RECOVERY_REQUIRED
    with pytest.raises(JournalError):
        journal.create_transaction(root, ((root / "unrelated", root / "changed"),))
    with pytest.raises(JournalError):
        journal.begin_undo(transaction.transaction_id, datetime.now(UTC))


@pytest.mark.parametrize(
    ("boundary", "count", "mutated", "persisted"),
    [("intent", 0, 0, 0)]
    + [("before_progress", n, n, n - 1) for n in (1, 2, 3)]
    + [("after_progress", n, n, n) for n in (1, 2, 3)]
    + [("before_terminal", 0, 3, 3)],
)
def test_crash_at_every_undo_boundary_remains_unresolved(
    tmp_path: Path, boundary: str, count: int, mutated: int, persisted: int,
) -> None:
    database, _, before = _prepare(tmp_path)
    database.dispose()
    _crash(database.path, before.transaction_id, boundary, count)
    restarted = Database(database.path)
    restarted.initialize()
    try:
        journal = TransactionJournal(restarted)
        after = journal.get_transaction(before.transaction_id)
        assert after.status is TransactionStatus.COMPLETED
        assert after.completed_at == before.completed_at
        assert after.undo_state is UndoTransactionState.IN_PROGRESS
        assert after.undo_started_at is not None
        assert after.undo_completed_at is None
        assert after.recovery_stage == "undo"
        for index, operation in enumerate(reversed(after.operations)):
            original = before.operations[operation.sequence - 1]
            assert operation.status is original.status is ExecutionStatus.SUCCESS
            assert operation.executed_at == original.executed_at
            assert operation.undo_status is (
                UndoStatus.SUCCESS if index < persisted else UndoStatus.PENDING
            )
            path = operation.source_path if index < mutated else operation.target_path
            absent = operation.target_path if index < mutated else operation.source_path
            assert path.is_dir()
            assert not absent.exists()
            assert (path / "payload").read_text(encoding="utf-8") == original.source_path.name
        _assert_blocked(journal, after)
    finally:
        restarted.dispose()


@pytest.mark.parametrize("boundary", ["after_terminal", "before_failure", "after_failure"])
def test_terminal_outcome_is_atomic_across_restart(tmp_path: Path, boundary: str) -> None:
    database, _, before = _prepare(tmp_path)
    database.dispose()
    _crash(database.path, before.transaction_id, boundary)
    restarted = Database(database.path)
    restarted.initialize()
    try:
        journal = TransactionJournal(restarted)
        after = journal.get_transaction(before.transaction_id)
        assert after.completed_at == before.completed_at
        if boundary == "before_failure":
            assert after.undo_state is UndoTransactionState.IN_PROGRESS
            assert after.operations[-1].undo_status is UndoStatus.PENDING
            _assert_blocked(journal, after)
            return
        assert journal.find_unresolved_transaction() is None
        assert after.undo_completed_at is not None
        if boundary == "after_terminal":
            assert after.status is TransactionStatus.UNDONE
            assert after.undo_state is UndoTransactionState.SUCCESS
            assert all(op.undo_status is UndoStatus.SUCCESS for op in after.operations)
        else:
            assert after.status is TransactionStatus.UNDO_PARTIAL
            assert after.undo_state is UndoTransactionState.PARTIAL
            assert after.operations[-1].undo_status is UndoStatus.FAILED
            assert UndoService(journal, filesystem=Filesystem()).undo(
                before.transaction_id, confirmed=True,
            ).status is TransactionStatus.UNDONE
        result = RenameExecutor(journal, filesystem=Filesystem()).execute(
            before.root, [_plan(before.root, "A", "new")], confirmed=True,
        )
        assert result.status is TransactionStatus.COMPLETED
    finally:
        restarted.dispose()


@pytest.mark.parametrize("failure", ["intent", "progress", "terminal", "failure_outcome"])
@pytest.mark.parametrize("recovery_write_fails", [False, True])
def test_sqlite_write_failures_never_lose_durable_intent(
    tmp_path: Path, failure: str, recovery_write_fails: bool,
) -> None:
    database, journal, before = _prepare(tmp_path)
    if failure == "progress":
        trigger = """CREATE TRIGGER reject_progress BEFORE UPDATE OF undo_status
            ON rename_operations BEGIN SELECT RAISE(ABORT, 'injected progress failure'); END"""
    else:
        state = {"intent": "in_progress", "terminal": "success", "failure_outcome": "partial"}[
            failure
        ]
        trigger = f"""CREATE TRIGGER reject_state BEFORE UPDATE OF undo_state
            ON rename_transactions WHEN NEW.undo_state = '{state}'
            BEGIN SELECT RAISE(ABORT, 'injected state failure'); END"""
    with sqlite3.connect(database.path) as connection:
        connection.execute(trigger)
        if recovery_write_fails:
            connection.execute("""CREATE TRIGGER reject_recovery BEFORE UPDATE OF undo_state
                ON rename_transactions WHEN NEW.undo_state = 'recovery_required'
                BEGIN SELECT RAISE(ABORT, 'injected recovery failure'); END""")

    class FailingFilesystem(Filesystem):
        def rename(self, source: Path, target: Path) -> None:
            raise PermissionError("injected filesystem failure")

    result = UndoService(
        journal, filesystem=FailingFilesystem() if failure == "failure_outcome" else Filesystem(),
    ).undo(before.transaction_id, confirmed=True)
    database.dispose()
    restarted = Database(database.path)
    restarted.initialize()
    try:
        journal = TransactionJournal(restarted)
        after = journal.get_transaction(before.transaction_id)
        assert after.status is TransactionStatus.COMPLETED
        assert after.completed_at == before.completed_at
        assert all(op.status is ExecutionStatus.SUCCESS for op in after.operations)
        if failure == "intent":
            assert result.status is TransactionStatus.FAILED
            assert after.undo_state is None
            assert journal.find_unresolved_transaction() is None
            assert sorted(p.name for p in before.root.iterdir()) == ["A2", "B2", "C2"]
        else:
            assert result.status is TransactionStatus.RECOVERY_REQUIRED
            assert after.undo_state is (
                UndoTransactionState.IN_PROGRESS if recovery_write_fails
                else UndoTransactionState.RECOVERY_REQUIRED
            )
            expected = {
                "progress": ["A2", "B2", "C"],
                "terminal": ["A", "B", "C"],
                "failure_outcome": ["A2", "B2", "C2"],
            }[failure]
            assert sorted(p.name for p in before.root.iterdir()) == expected
            _assert_blocked(journal, after)
    finally:
        restarted.dispose()


def test_independent_database_guards_serialize_undo_and_new_forward_intents(tmp_path: Path) -> None:
    database, _, before = _prepare(tmp_path)
    database.dispose()
    barrier = Barrier(3)

    def attempt(kind: str) -> str:
        independent = Database(database.path)
        independent.initialize()
        journal = TransactionJournal(independent)
        try:
            barrier.wait(timeout=10)
            if kind == "forward":
                journal.create_transaction(before.root, ((before.root / "X", before.root / "Y"),))
            else:
                journal.begin_undo(before.transaction_id, datetime.now(UTC))
            return "reserved"
        except JournalError:
            return "blocked"
        finally:
            independent.dispose()

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(attempt, ["undo", "undo", "forward"]))
    assert sorted(results) == ["blocked", "blocked", "reserved"]
    assert sorted(p.name for p in before.root.iterdir()) == ["A2", "B2", "C2"]
