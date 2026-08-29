from dataclasses import replace
from pathlib import Path

import pytest

from dlsite_organizer.domain.organizer import RenamePlan, RenamePlanStatus
from dlsite_organizer.domain.rename_execution import (
    ExecutionStatus,
    TransactionStatus,
    UndoStatus,
)
from dlsite_organizer.domain.work import Work
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.rename_executor import LocalRenameFilesystem, RenameExecutor
from dlsite_organizer.services.undo_service import UndoService


def _plan(root: Path, source: str, target: str, status: RenamePlanStatus = RenamePlanStatus.READY):
    return RenamePlan(
        source_path=root / source,
        current_name=source,
        work_code='RJ00000001',
        work=Work(workno='RJ00000001', title='Test'),
        proposed_name=target,
        target_path=root / target,
        status=status,
    )


def _journal(tmp_path: Path) -> TransactionJournal:
    database = Database(tmp_path / 'journal.sqlite3')
    database.initialize()
    return TransactionJournal(database)


def test_ready_plan_executes_and_journals_then_undoes(tmp_path: Path) -> None:
    root = tmp_path / 'root'
    root.mkdir()
    source = root / 'A'
    source.mkdir()
    (source / 'payload.txt').write_text('payload', encoding='utf-8')
    journal = _journal(tmp_path)

    result = RenameExecutor(journal).execute(root, [_plan(root, 'A', 'B')], confirmed=True)

    assert result.status is TransactionStatus.COMPLETED
    assert result.success_count == 1
    assert not source.exists()
    assert (root / 'B' / 'payload.txt').read_text(encoding='utf-8') == 'payload'
    assert result.transaction is not None
    assert result.transaction.operations[0].status is ExecutionStatus.SUCCESS
    assert result.transaction.operations[0].executed_at is not None
    assert result.transaction.completed_at is not None

    undo = UndoService(journal).undo(result.transaction_id, confirmed=True)

    assert undo.status is TransactionStatus.UNDONE
    assert (root / 'A' / 'payload.txt').read_text(encoding='utf-8') == 'payload'
    assert not (root / 'B').exists()
    assert undo.transaction is not None
    assert undo.transaction.operations[0].undo_status is UndoStatus.SUCCESS
    assert undo.transaction.operations[0].undone_at is not None


def test_no_confirmation_does_not_mutate_or_create_journal(tmp_path: Path) -> None:
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'A').mkdir()
    journal = _journal(tmp_path)

    result = RenameExecutor(journal).execute(root, [_plan(root, 'A', 'B')])

    assert result.error is not None
    assert result.transaction is None
    assert result.operations[0].status is ExecutionStatus.REJECTED
    assert (root / 'A').exists()
    assert not (root / 'B').exists()
    assert journal.latest_undoable() is None


def test_non_ready_plan_is_rejected_by_executor(tmp_path: Path) -> None:
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'A').mkdir()
    journal = _journal(tmp_path)

    result = RenameExecutor(journal).execute(
        root,
        [_plan(root, 'A', 'B', RenamePlanStatus.CONFLICT)],
        confirmed=True,
    )

    assert result.operations[0].status is ExecutionStatus.REJECTED
    assert (root / 'A').exists()
    assert journal.latest_undoable() is None


def test_missing_source_and_appearing_target_reject_whole_batch(tmp_path: Path) -> None:
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'A').mkdir()
    (root / 'C').mkdir()
    journal = _journal(tmp_path)
    missing = _plan(root, 'A', 'B')
    (root / 'A').rename(root / 'gone')

    result = RenameExecutor(journal).execute(root, [missing], confirmed=True)

    assert result.status is TransactionStatus.FAILED
    assert result.operations[0].status is ExecutionStatus.PRECONDITION_FAILED
    assert (root / 'gone').exists()
    assert not (root / 'B').exists()

    (root / 'A').mkdir()
    (root / 'B').mkdir()
    result = RenameExecutor(journal).execute(
        root,
        [_plan(root, 'A', 'B'), _plan(root, 'C', 'D')],
        confirmed=True,
    )

    assert result.operations[0].status is ExecutionStatus.PRECONDITION_FAILED
    assert result.operations[1].status is ExecutionStatus.PRECONDITION_FAILED
    assert (root / 'A').exists()
    assert (root / 'C').exists()
    assert not (root / 'D').exists()


def test_duplicate_and_dependency_batches_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / 'root'
    root.mkdir()
    for name in ('A', 'B', 'C'):
        (root / name).mkdir()
    journal = _journal(tmp_path)

    duplicate = RenameExecutor(journal).execute(
        root,
        [_plan(root, 'A', 'X'), _plan(root, 'A', 'Y')],
        confirmed=True,
    )
    chain = RenameExecutor(journal).execute(
        root,
        [_plan(root, 'A', 'B'), _plan(root, 'B', 'C')],
        confirmed=True,
    )

    assert all(
        operation.status is ExecutionStatus.PRECONDITION_FAILED
        for operation in duplicate.operations
    )
    assert all(
        operation.status is ExecutionStatus.PRECONDITION_FAILED
        for operation in chain.operations
    )
    assert sorted(path.name for path in root.iterdir()) == ['A', 'B', 'C']


def test_duplicate_target_and_cycle_are_rejected_without_mutation(tmp_path: Path) -> None:
    root = tmp_path / 'root'
    root.mkdir()
    for name in ('A', 'B'):
        (root / name).mkdir()
    journal = _journal(tmp_path)

    duplicate_target = RenameExecutor(journal).execute(
        root,
        [_plan(root, 'A', 'X'), _plan(root, 'B', 'X')],
        confirmed=True,
    )
    cycle = RenameExecutor(journal).execute(
        root,
        [_plan(root, 'A', 'B'), _plan(root, 'B', 'A')],
        confirmed=True,
    )

    assert all(
        operation.status is ExecutionStatus.PRECONDITION_FAILED
        for operation in duplicate_target.operations
    )
    assert all(
        operation.status is ExecutionStatus.PRECONDITION_FAILED
        for operation in cycle.operations
    )
    assert sorted(path.name for path in root.iterdir()) == ['A', 'B']


def test_source_and_target_outside_root_are_rejected(tmp_path: Path) -> None:
    root = tmp_path / 'root'
    outside = tmp_path / 'outside'
    root.mkdir()
    outside.mkdir()
    (root / 'A').mkdir()
    (outside / 'outside-source').mkdir()
    journal = _journal(tmp_path)

    source_outside = replace(
        _plan(root, 'A', 'B'),
        source_path=outside / 'outside-source',
    )
    target_outside = replace(
        _plan(root, 'A', 'B'),
        target_path=outside / 'outside-target',
    )

    source_result = RenameExecutor(journal).execute(
        root, [source_outside], confirmed=True
    )
    target_result = RenameExecutor(journal).execute(
        root, [target_outside], confirmed=True
    )

    assert source_result.operations[0].status is ExecutionStatus.PRECONDITION_FAILED
    assert target_result.operations[0].status is ExecutionStatus.PRECONDITION_FAILED
    assert (root / 'A').exists()
    assert (outside / 'outside-source').exists()
    assert not (outside / 'outside-target').exists()


def test_symlink_source_is_rejected_when_platform_supports_directory_links(
    tmp_path: Path,
) -> None:
    root = tmp_path / 'root'
    root.mkdir()
    real_source = root / 'real-source'
    real_source.mkdir()
    link = root / 'linked-source'
    try:
        link.symlink_to(real_source, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f'directory symlinks are unavailable in this environment: {exc}')
    journal = _journal(tmp_path)

    result = RenameExecutor(journal).execute(
        root,
        [_plan(root, 'linked-source', 'renamed')],
        confirmed=True,
    )

    assert result.operations[0].status is ExecutionStatus.PRECONDITION_FAILED
    assert link.is_symlink()
    assert not (root / 'renamed').exists()


def test_existing_target_is_never_overwritten(tmp_path: Path) -> None:
    root = tmp_path / 'root'
    root.mkdir()
    source = root / 'A'
    target = root / 'B'
    source.mkdir()
    target.mkdir()
    (source / 'source.txt').write_text('source', encoding='utf-8')
    (target / 'target.txt').write_text('target', encoding='utf-8')
    journal = _journal(tmp_path)

    result = RenameExecutor(journal).execute(
        root, [_plan(root, 'A', 'B')], confirmed=True
    )

    assert result.operations[0].status is ExecutionStatus.PRECONDITION_FAILED
    assert (source / 'source.txt').read_text(encoding='utf-8') == 'source'
    assert (target / 'target.txt').read_text(encoding='utf-8') == 'target'


class RecordingFilesystem(LocalRenameFilesystem):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def rename(self, source: Path, target: Path) -> None:
        self.calls.append((source.name, target.name))
        super().rename(source, target)


def test_execution_order_is_deterministic_by_source_path(tmp_path: Path) -> None:
    root = tmp_path / 'root'
    root.mkdir()
    for name in ('A', 'B'):
        (root / name).mkdir()
    journal = _journal(tmp_path)
    filesystem = RecordingFilesystem()

    result = RenameExecutor(
        journal,
        filesystem=filesystem,
    ).execute(
        root,
        [_plan(root, 'B', 'B2'), _plan(root, 'A', 'A2')],
        confirmed=True,
    )

    assert result.status is TransactionStatus.COMPLETED
    assert filesystem.calls == [('A', 'A2'), ('B', 'B2')]
    assert result.transaction is not None
    assert [operation.sequence for operation in result.transaction.operations] == [1, 2]
    assert [operation.source_path.name for operation in result.transaction.operations] == ['A', 'B']


def test_case_only_rename_is_rejected_without_mutation(tmp_path: Path) -> None:
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'abc').mkdir()
    journal = _journal(tmp_path)

    result = RenameExecutor(journal).execute(root, [_plan(root, 'abc', 'ABC')], confirmed=True)

    assert result.operations[0].status is ExecutionStatus.PRECONDITION_FAILED
    assert (root / 'abc').exists()
    assert sorted(path.name for path in root.iterdir()) == ['abc']


def test_no_journal_means_no_mutation(tmp_path: Path) -> None:
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'A').mkdir()

    result = RenameExecutor(None).execute(root, [_plan(root, 'A', 'B')], confirmed=True)

    assert result.operations[0].status is ExecutionStatus.REJECTED
    assert (root / 'A').exists()
    assert not (root / 'B').exists()


class SuccessUpdateRaisesJournal(TransactionJournal):
    def mark_operation_success(self, transaction_id: str, sequence: int, when) -> None:
        super().mark_operation_success(transaction_id, sequence, when)
        raise RuntimeError('synthetic journal update failure')


def test_journal_success_update_failure_stops_and_requires_recovery(tmp_path: Path) -> None:
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'A').mkdir()
    journal = SuccessUpdateRaisesJournal(_database_for_test(tmp_path))

    result = RenameExecutor(journal).execute(root, [_plan(root, 'A', 'B')], confirmed=True)

    assert result.status is TransactionStatus.RECOVERY_REQUIRED
    assert result.transaction is not None
    assert result.transaction.status is TransactionStatus.RECOVERY_REQUIRED
    assert (root / 'B').exists()


class CreateRaisesJournal(TransactionJournal):
    def create_transaction(self, root: Path, operations):
        del root, operations
        raise RuntimeError('synthetic journal create failure')


def test_journal_create_failure_happens_before_any_mutation(tmp_path: Path) -> None:
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'A').mkdir()
    journal = CreateRaisesJournal(_database_for_test(tmp_path))

    result = RenameExecutor(journal).execute(
        root, [_plan(root, 'A', 'B')], confirmed=True
    )

    assert result.status is TransactionStatus.FAILED
    assert result.operations[0].status is ExecutionStatus.REJECTED
    assert (root / 'A').exists()
    assert not (root / 'B').exists()


def _database_for_test(tmp_path: Path) -> Database:
    database = Database(tmp_path / 'recovery.sqlite3')
    database.initialize()
    return database


class FailingFilesystem(LocalRenameFilesystem):
    def __init__(self, failing_source: str) -> None:
        self.failing_source = failing_source

    def rename(self, source: Path, target: Path) -> None:
        if source.name == self.failing_source:
            raise OSError('synthetic failure')
        super().rename(source, target)


def test_partial_failure_stops_following_operations_and_is_undoable(tmp_path: Path) -> None:
    root = tmp_path / 'root'
    root.mkdir()
    for name in ('A', 'B', 'C'):
        (root / name).mkdir()
    journal = _journal(tmp_path)
    plans = [_plan(root, name, name + '2') for name in ('A', 'B', 'C')]

    result = RenameExecutor(
        journal,
        filesystem=FailingFilesystem('B'),
    ).execute(root, plans, confirmed=True)

    assert result.status is TransactionStatus.PARTIAL
    assert [operation.status for operation in result.operations] == [
        ExecutionStatus.SUCCESS,
        ExecutionStatus.FAILED,
        ExecutionStatus.NOT_EXECUTED,
    ]
    assert (root / 'A2').exists()
    assert (root / 'B').exists()
    assert (root / 'C').exists()
    assert not (root / 'C2').exists()

    undo = UndoService(journal).undo(result.transaction_id, confirmed=True)

    assert undo.status is TransactionStatus.UNDONE
    assert sorted(path.name for path in root.iterdir()) == ['A', 'B', 'C']
