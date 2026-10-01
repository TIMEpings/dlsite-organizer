"""Real filesystem replacements at deterministic Undo operation boundaries."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path

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
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.rename_executor import LocalRenameFilesystem, RenameExecutor
from dlsite_organizer.services.undo_service import UndoService


class RecordingFilesystem:
    def __init__(self) -> None:
        self.calls: list[tuple[Path, Path]] = []

    def rename(self, source: Path, target: Path) -> None:
        self.calls.append((source, target))
        LocalRenameFilesystem().rename(source, target)


class IntentHookJournal(TransactionJournal):
    after_intent: Callable[[], None] | None = None

    def begin_undo(self, transaction_id: str, when: datetime) -> None:
        super().begin_undo(transaction_id, when)
        if self.after_intent is not None:
            self.after_intent()


@pytest.fixture
def prepared(tmp_path: Path) -> Iterator[tuple[Database, IntentHookJournal, RenameTransaction]]:
    root = tmp_path / 'container' / 'library'
    root.mkdir(parents=True)
    plans: list[RenamePlan] = []
    for name in 'ABC':
        (root / name).mkdir()
        (root / name / 'payload').write_text(name, encoding='utf-8')
        plans.append(RenamePlan(
            source_path=root / name,
            current_name=name,
            work_code='RJ00000001',
            work=Work(workno='RJ00000001', title='Test'),
            proposed_name=name + '2',
            target_path=root / (name + '2'),
            status=RenamePlanStatus.READY,
        ))
    database = Database(tmp_path / 'journal.sqlite3')
    database.initialize()
    journal = IntentHookJournal(database)
    result = RenameExecutor(journal, filesystem=RecordingFilesystem()).execute(
        root, plans, confirmed=True,
    )
    assert result.status is TransactionStatus.COMPLETED
    assert result.transaction is not None
    try:
        yield database, journal, result.transaction
    finally:
        database.dispose()


def _directory_link(path: Path, target: Path) -> None:
    if os.name == 'nt':
        # Junctions need no symlink privilege. Failure here is a real test failure,
        # not a skip or a weakened mock of the Windows policy.
        result = subprocess.run(
            ['cmd', '/c', 'mklink', '/J', str(path), str(target)],
            capture_output=True, text=True, timeout=10,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert path.is_junction()
    else:
        path.symlink_to(target, target_is_directory=True)
        assert path.is_symlink()


def _assert_partial(
    journal: TransactionJournal,
    before: RenameTransaction,
    completed: int,
    diagnostic: str,
) -> None:
    after = journal.get_transaction(before.transaction_id)
    assert after.status is TransactionStatus.UNDO_PARTIAL
    assert after.undo_state is UndoTransactionState.PARTIAL
    assert after.undo_started_at is not None
    assert after.undo_completed_at is not None
    assert after.completed_at == before.completed_at
    assert not after.unresolved
    assert journal.find_unresolved_transaction() is None
    assert journal.list_transactions(limit=10)[0].display_status is TransactionStatus.UNDO_PARTIAL
    latest = journal.latest_undoable()
    assert latest is not None and latest.transaction_id == before.transaction_id
    for index, operation in enumerate(reversed(after.operations)):
        forward = before.operations[operation.sequence - 1]
        assert operation.status is forward.status is ExecutionStatus.SUCCESS
        assert operation.executed_at == forward.executed_at
        assert operation.source_path == forward.source_path
        assert operation.target_path == forward.target_path
        if index < completed:
            assert operation.undo_status is UndoStatus.SUCCESS
            assert operation.undone_at is not None
        elif index == completed:
            assert operation.undo_status is UndoStatus.FAILED
            assert operation.undo_error is not None and diagnostic in operation.undo_error
            assert operation.undone_at is None
        else:
            assert operation.undo_status is UndoStatus.PENDING
            assert operation.undone_at is None


@pytest.mark.parametrize('boundary', ['intent', 'op1', 'op2', 'op3'])
@pytest.mark.parametrize(
    ('change', 'diagnostic'),
    [
        ('file', '不再是目录'),
        ('directory', '目录身份已改变'),
        ('link', 'symlink/junction'),
        ('missing', '已不存在'),
        ('destination_file', '禁止覆盖'),
        ('destination_directory', '禁止覆盖'),
        ('destination_case', '禁止覆盖'),
        ('destination_link', '禁止覆盖'),
        ('destination_dangling_link', '禁止覆盖'),
    ],
)
def test_late_change_never_moves_unsafe_object_and_stops_remaining_operations(
    tmp_path: Path,
    prepared: tuple[Database, IntentHookJournal, RenameTransaction],
    boundary: str,
    change: str,
    diagnostic: str,
) -> None:
    database, journal, before = prepared
    count = 1 if boundary == 'intent' else int(boundary[-1])
    operation = tuple(reversed(before.operations))[count - 1]
    current, original = operation.target_path, operation.source_path
    displaced = tmp_path / 'displaced'
    external = tmp_path / 'external'
    external.mkdir()
    (external / 'sentinel').write_text('untouched', encoding='utf-8')
    conflict = (
        original.with_name(original.name.lower()) if change == 'destination_case' else original
    )
    filesystem = RecordingFilesystem()

    def inject() -> None:
        active = journal.get_transaction(before.transaction_id)
        assert active.undo_state is UndoTransactionState.IN_PROGRESS
        if change.startswith('destination'):
            if change in {'destination_file', 'destination_case'}:
                conflict.write_text('replacement', encoding='utf-8')
            elif change == 'destination_directory':
                conflict.mkdir()
                (conflict / 'sentinel').write_text('replacement', encoding='utf-8')
            else:
                target = external if change == 'destination_link' else tmp_path / 'absent'
                _directory_link(conflict, target)
        else:
            current.rename(displaced)
            if change == 'file':
                current.write_text('replacement', encoding='utf-8')
            elif change == 'directory':
                current.mkdir()
                (current / 'sentinel').write_text('replacement', encoding='utf-8')
            elif change == 'link':
                _directory_link(current, external)

    if boundary == 'intent':
        journal.after_intent = inject

    def progress(index: int, total: int, source: Path, target: Path) -> None:
        assert total == 3
        if boundary != 'intent' and index == count:
            assert (source, target) == (current, original)
            inject()

    result = UndoService(journal, filesystem=filesystem).undo(
        before.transaction_id, confirmed=True, progress_callback=progress,
    )
    assert result.status is TransactionStatus.UNDO_PARTIAL
    assert filesystem.calls == [
        (op.target_path, op.source_path) for op in reversed(before.operations)
    ][:count - 1]
    for index, op in enumerate(reversed(before.operations)):
        if index == count - 1:
            continue
        present = op.source_path if index < count - 1 else op.target_path
        absent = op.target_path if index < count - 1 else op.source_path
        assert not os.path.lexists(absent)
        assert (present / 'payload').read_text(encoding='utf-8') == op.source_path.name
    if change.startswith('destination'):
        assert (current / 'payload').read_text(encoding='utf-8') == original.name
        if change in {'destination_file', 'destination_case'}:
            assert conflict.read_text(encoding='utf-8') == 'replacement'
        elif change == 'destination_directory':
            assert (conflict / 'sentinel').read_text(encoding='utf-8') == 'replacement'
        else:
            assert os.path.lexists(conflict)
            assert conflict.is_junction() if os.name == 'nt' else conflict.is_symlink()
    else:
        assert not os.path.lexists(original)
        assert (displaced / 'payload').read_text(encoding='utf-8') == original.name
        if change == 'file':
            assert current.read_text(encoding='utf-8') == 'replacement'
        elif change == 'directory':
            assert (current / 'sentinel').read_text(encoding='utf-8') == 'replacement'
        elif change == 'link':
            assert current.is_junction() if os.name == 'nt' else current.is_symlink()
        else:
            assert not os.path.lexists(current)
    assert (external / 'sentinel').read_text(encoding='utf-8') == 'untouched'
    _assert_partial(journal, before, count - 1, diagnostic)

    # Restart preserves known partial history, not an unresolved/crash guess.
    database.dispose()
    restarted = Database(database.path)
    restarted.initialize()
    try:
        _assert_partial(TransactionJournal(restarted), before, count - 1, diagnostic)
    finally:
        restarted.dispose()


@pytest.mark.parametrize(
    ('change', 'diagnostic'),
    [('directory', '目录身份已改变'), ('file', '不再是目录'),
     ('missing', '已不存在'), ('link', 'symlink/junction')],
)
def test_root_replacement_after_preflight_is_rejected_without_traversal(
    tmp_path: Path,
    prepared: tuple[Database, IntentHookJournal, RenameTransaction],
    change: str,
    diagnostic: str,
) -> None:
    _, journal, before = prepared
    displaced = tmp_path / 'displaced_root'
    external = tmp_path / 'external'
    external.mkdir()
    for name in 'ABC':
        (external / (name + '2')).mkdir()
        (external / (name + '2') / 'payload').write_text('external', encoding='utf-8')

    def inject() -> None:
        before.root.rename(displaced)
        if change == 'directory':
            before.root.mkdir()
            (before.root / 'C2').mkdir()
        elif change == 'file':
            before.root.write_text('replacement', encoding='utf-8')
        elif change == 'link':
            _directory_link(before.root, external)

    journal.after_intent = inject
    filesystem = RecordingFilesystem()
    result = UndoService(journal, filesystem=filesystem).undo(before.transaction_id, confirmed=True)
    assert result.status is TransactionStatus.UNDO_PARTIAL
    assert filesystem.calls == []
    _assert_partial(journal, before, 0, diagnostic)
    for name in 'ABC':
        assert (displaced / (name + '2') / 'payload').read_text(encoding='utf-8') == name
        assert (external / (name + '2') / 'payload').read_text(encoding='utf-8') == 'external'
        assert not (external / name).exists()
    if change == 'directory':
        assert (before.root / 'C2').is_dir()
        assert not (before.root / 'C').exists()
    elif change == 'file':
        assert before.root.read_text(encoding='utf-8') == 'replacement'
    elif change == 'missing':
        assert not os.path.lexists(before.root)
    else:
        assert before.root.is_junction() if os.name == 'nt' else before.root.is_symlink()


def test_normal_undo_accepts_directory_content_changes(
    prepared: tuple[Database, IntentHookJournal, RenameTransaction],
) -> None:
    _, journal, before = prepared

    def progress(index: int, total: int, source: Path, target: Path) -> None:
        (source / 'new_content').write_text('allowed', encoding='utf-8')

    filesystem = RecordingFilesystem()
    result = UndoService(journal, filesystem=filesystem).undo(
        before.transaction_id, confirmed=True, progress_callback=progress,
    )
    assert result.status is TransactionStatus.UNDONE
    assert result.transaction is not None
    assert result.transaction.undo_state is UndoTransactionState.SUCCESS
    assert len(filesystem.calls) == 3
    for op in before.operations:
        assert (op.source_path / 'payload').read_text(encoding='utf-8') == op.source_path.name
        assert (op.source_path / 'new_content').read_text(encoding='utf-8') == 'allowed'
        assert not op.target_path.exists()


def test_redirected_root_ancestor_cannot_bypass_identity_check(
    tmp_path: Path,
    prepared: tuple[Database, IntentHookJournal, RenameTransaction],
) -> None:
    _, journal, before = prepared
    displaced = tmp_path / 'displaced_container'
    external = tmp_path / 'external_container'
    (external / 'library' / 'C2').mkdir(parents=True)
    sentinel = external / 'library' / 'C2' / 'payload'
    sentinel.write_text('external', encoding='utf-8')

    def inject() -> None:
        before.root.parent.rename(displaced)
        _directory_link(before.root.parent, external)

    journal.after_intent = inject
    filesystem = RecordingFilesystem()
    result = UndoService(journal, filesystem=filesystem).undo(before.transaction_id, confirmed=True)
    assert result.status is TransactionStatus.UNDO_PARTIAL
    assert filesystem.calls == []
    assert sentinel.read_text(encoding='utf-8') == 'external'
    assert not (external / 'library' / 'C').exists()
    for name in 'ABC':
        assert (displaced / 'library' / (name + '2') / 'payload').read_text(
            encoding='utf-8',
        ) == name
    _assert_partial(journal, before, 0, 'transaction root 目录身份已改变')


def test_known_revalidation_failure_can_be_retried_after_explicit_repair(
    tmp_path: Path,
    prepared: tuple[Database, IntentHookJournal, RenameTransaction],
) -> None:
    _, journal, before = prepared
    displaced = tmp_path / 'displaced'
    current = before.root / 'B2'

    def progress(index: int, total: int, source: Path, target: Path) -> None:
        if index == 2:
            source.rename(displaced)
            source.write_text('replacement', encoding='utf-8')

    filesystem = RecordingFilesystem()
    first = UndoService(journal, filesystem=filesystem).undo(
        before.transaction_id, confirmed=True, progress_callback=progress,
    )
    assert first.status is TransactionStatus.UNDO_PARTIAL
    assert filesystem.calls == [(before.root / 'C2', before.root / 'C')]
    assert current.read_text(encoding='utf-8') == 'replacement'
    _assert_partial(journal, before, 1, '不再是目录')
    # The caller repairs the filesystem explicitly; Undo never guesses a repair.
    current.unlink()
    displaced.rename(current)
    second = UndoService(journal, filesystem=filesystem).undo(before.transaction_id, confirmed=True)
    assert second.status is TransactionStatus.UNDONE
    assert filesystem.calls == [
        (op.target_path, op.source_path) for op in reversed(before.operations)
    ]
    for name in 'ABC':
        assert (before.root / name / 'payload').read_text(encoding='utf-8') == name


@pytest.mark.parametrize('failure', ['unavailable_identity', 'inspection_error', 'reparse'])
def test_unverifiable_metadata_fails_closed_at_operation_boundary(
    prepared: tuple[Database, IntentHookJournal, RenameTransaction],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    _, journal, before = prepared
    current = before.root / 'B2'
    original_lstat = Path.lstat

    def lstat(path: Path) -> os.stat_result:
        if path != current:
            return original_lstat(path)
        if failure == 'inspection_error':
            raise PermissionError('injected inspection error')
        metadata = original_lstat(path)
        if failure == 'unavailable_identity':
            fields = list(metadata)
            fields[1] = 0
            return os.stat_result(fields, {
                'st_file_attributes': getattr(metadata, 'st_file_attributes', 0),
            })
        # Other reparse tags need not identify as symlink/junction. Reproduce
        # their non-following metadata while retaining a real source directory.
        class ReparseMetadata:
            st_mode = metadata.st_mode
            st_file_attributes = 0x400

        return ReparseMetadata()  # type: ignore[return-value]

    def progress(index: int, total: int, source: Path, target: Path) -> None:
        if index == 2:
            monkeypatch.setattr(Path, 'lstat', lstat)

    filesystem = RecordingFilesystem()
    result = UndoService(journal, filesystem=filesystem).undo(
        before.transaction_id, confirmed=True, progress_callback=progress,
    )
    assert result.status is TransactionStatus.UNDO_PARTIAL
    assert filesystem.calls == [(before.root / 'C2', before.root / 'C')]
    assert (current / 'payload').read_text(encoding='utf-8') == 'B'
    assert (before.root / 'A2' / 'payload').read_text(encoding='utf-8') == 'A'
    assert not (before.root / 'B').exists()
    diagnostic = {
        'unavailable_identity': '无法取得目录身份',
        'inspection_error': '无法安全检查',
        'reparse': 'link-like/reparse',
    }[failure]
    _assert_partial(journal, before, 1, diagnostic)


def test_destination_appearing_inside_rename_seam_is_not_overwritten(
    prepared: tuple[Database, IntentHookJournal, RenameTransaction],
) -> None:
    _, journal, before = prepared

    class LateDestinationFilesystem(RecordingFilesystem):
        def rename(self, source: Path, target: Path) -> None:
            target.write_text('late destination', encoding='utf-8')
            super().rename(source, target)

    filesystem = LateDestinationFilesystem()
    result = UndoService(journal, filesystem=filesystem).undo(before.transaction_id, confirmed=True)
    assert result.status is TransactionStatus.UNDO_PARTIAL
    assert len(filesystem.calls) == 1
    assert (before.root / 'C').read_text(encoding='utf-8') == 'late destination'
    assert (before.root / 'C2' / 'payload').read_text(encoding='utf-8') == 'C'
    _assert_partial(journal, before, 0, '未覆盖')


@pytest.mark.skipif(os.name != 'nt', reason='Production no-overwrite syscall is Windows-only')
def test_windows_syscall_rejects_destination_after_filesystem_check(
    prepared: tuple[Database, IntentHookJournal, RenameTransaction],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, journal, before = prepared
    current, original = before.root / 'C2', before.root / 'C'
    rename = Path.rename

    def syscall_boundary(source: Path, target: Path | str) -> Path:
        if source == current:
            original.write_text('at syscall', encoding='utf-8')
        return rename(source, target)

    monkeypatch.setattr(Path, 'rename', syscall_boundary)
    filesystem = RecordingFilesystem()
    result = UndoService(journal, filesystem=filesystem).undo(before.transaction_id, confirmed=True)
    assert result.status is TransactionStatus.UNDO_PARTIAL
    assert filesystem.calls == [(current, original)]
    assert original.read_text(encoding='utf-8') == 'at syscall'
    assert (current / 'payload').read_text(encoding='utf-8') == 'C'
    assert (before.root / 'B2' / 'payload').read_text(encoding='utf-8') == 'B'
    assert (before.root / 'A2' / 'payload').read_text(encoding='utf-8') == 'A'
    _assert_partial(journal, before, 0, '未覆盖')


def test_pathname_rename_has_a_residual_source_replacement_window(
    tmp_path: Path,
    prepared: tuple[Database, IntentHookJournal, RenameTransaction],
) -> None:
    """Document the limit: identity checks do not bind the rename to a handle."""
    _, journal, before = prepared
    displaced = tmp_path / 'after_check'

    class AfterCheckFilesystem(RecordingFilesystem):
        def rename(self, source: Path, target: Path) -> None:
            if not self.calls:
                source.rename(displaced)
                source.mkdir()
                (source / 'replacement').write_text('after final check', encoding='utf-8')
            super().rename(source, target)

    result = UndoService(journal, filesystem=AfterCheckFilesystem()).undo(
        before.transaction_id, confirmed=True,
    )
    # This mutation deliberately happens AFTER the final userspace check. The
    # existing pathname syscall cannot provide atomic object-identity protection.
    assert result.status is TransactionStatus.UNDONE
    assert (displaced / 'payload').read_text(encoding='utf-8') == 'C'
    assert (before.root / 'C' / 'replacement').read_text(encoding='utf-8') == 'after final check'
