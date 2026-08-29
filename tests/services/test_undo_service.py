from pathlib import Path

from dlsite_organizer.domain.organizer import RenamePlan, RenamePlanStatus
from dlsite_organizer.domain.rename_execution import (
    TransactionStatus,
    UndoStatus,
)
from dlsite_organizer.domain.work import Work
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.rename_executor import LocalRenameFilesystem, RenameExecutor
from dlsite_organizer.services.undo_service import UndoService


def plan(root: Path, source: str, target: str) -> RenamePlan:
    return RenamePlan(
        source_path=root / source,
        current_name=source,
        work_code='RJ00000001',
        work=Work(workno='RJ00000001', title='Test'),
        proposed_name=target,
        target_path=root / target,
        status=RenamePlanStatus.READY,
    )


def journal(tmp_path: Path) -> TransactionJournal:
    database = Database(tmp_path / 'journal.sqlite3')
    database.initialize()
    return TransactionJournal(database)


def test_undo_target_conflict_is_preflighted_without_mutation(tmp_path: Path) -> None:
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'A').mkdir()
    j = journal(tmp_path)
    execution = RenameExecutor(j).execute(root, [plan(root, 'A', 'B')], confirmed=True)
    (root / 'A').mkdir()

    result = UndoService(j).undo(execution.transaction_id, confirmed=True)

    assert result.status is TransactionStatus.FAILED
    assert result.operations[0].undo_status is UndoStatus.CONFLICT
    assert (root / 'A').exists()
    assert (root / 'B').exists()


def test_undo_missing_current_path_is_preflighted_without_mutation(tmp_path: Path) -> None:
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'A').mkdir()
    j = journal(tmp_path)
    execution = RenameExecutor(j).execute(root, [plan(root, 'A', 'B')], confirmed=True)
    (root / 'B').rmdir()

    result = UndoService(j).undo(execution.transaction_id, confirmed=True)

    assert result.status is TransactionStatus.FAILED
    assert result.operations[0].undo_status is UndoStatus.CONFLICT


def test_undo_requires_confirmation_without_mutation(tmp_path: Path) -> None:
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'A').mkdir()
    j = journal(tmp_path)
    execution = RenameExecutor(j).execute(root, [plan(root, 'A', 'B')], confirmed=True)

    result = UndoService(j).undo(execution.transaction_id)

    assert result.status is TransactionStatus.FAILED
    assert result.operations[0].undo_status is UndoStatus.PENDING
    assert (root / 'B').exists()
    assert not (root / 'A').exists()


class RecordingFilesystem(LocalRenameFilesystem):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def rename(self, source: Path, target: Path) -> None:
        self.calls.append((source.name, target.name))
        super().rename(source, target)


def test_undo_uses_reverse_execution_order(tmp_path: Path) -> None:
    root = tmp_path / 'root'
    root.mkdir()
    for name in ('A', 'B'):
        (root / name).mkdir()
    j = journal(tmp_path)
    execution = RenameExecutor(j).execute(
        root,
        [plan(root, 'A', 'A2'), plan(root, 'B', 'B2')],
        confirmed=True,
    )
    filesystem = RecordingFilesystem()

    result = UndoService(j, filesystem=filesystem).undo(
        execution.transaction_id,
        confirmed=True,
    )

    assert result.status is TransactionStatus.UNDONE
    assert filesystem.calls == [('B2', 'B'), ('A2', 'A')]


class FailOnceFilesystem(LocalRenameFilesystem):
    def __init__(self, source_name: str) -> None:
        self.source_name = source_name
        self.failed = False

    def rename(self, source: Path, target: Path) -> None:
        if not self.failed and source.name == self.source_name:
            self.failed = True
            raise OSError('synthetic undo failure')
        super().rename(source, target)


def test_undo_partial_failure_stops_and_can_resume(tmp_path: Path) -> None:
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'A').mkdir()
    (root / 'B').mkdir()
    j = journal(tmp_path)
    execution = RenameExecutor(j).execute(
        root,
        [plan(root, 'A', 'A2'), plan(root, 'B', 'B2')],
        confirmed=True,
    )
    failing = FailOnceFilesystem('A2')
    service = UndoService(j, filesystem=failing)

    first = service.undo(execution.transaction_id, confirmed=True)

    assert first.status is TransactionStatus.UNDO_PARTIAL
    assert first.operations[0].undo_status is UndoStatus.FAILED
    assert first.operations[1].undo_status is UndoStatus.SUCCESS
    assert (root / 'A2').exists()
    assert (root / 'B').exists()

    second = service.undo(execution.transaction_id, confirmed=True)

    assert second.status is TransactionStatus.UNDONE
    assert sorted(path.name for path in root.iterdir()) == ['A', 'B']


def test_double_undo_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / 'root'
    root.mkdir()
    (root / 'A').mkdir()
    j = journal(tmp_path)
    execution = RenameExecutor(j).execute(root, [plan(root, 'A', 'B')], confirmed=True)
    service = UndoService(j)
    assert service.undo(execution.transaction_id, confirmed=True).status is TransactionStatus.UNDONE

    result = service.undo(execution.transaction_id, confirmed=True)

    assert result.status is TransactionStatus.FAILED
    assert result.error is not None
    assert (root / 'A').exists()
