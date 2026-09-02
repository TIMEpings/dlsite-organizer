from __future__ import annotations

from pathlib import Path
from threading import Barrier, Event, Lock, Thread

import pytest

from dlsite_organizer.domain.organizer import RenamePlan, RenamePlanStatus
from dlsite_organizer.domain.rename_execution import (
    RenameExecutionResult,
    TransactionStatus,
    UndoResult,
)
from dlsite_organizer.domain.work import Work
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.mutation_gate import MutationGate
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.undo_service import UndoService


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


class BlockingFilesystem:
    def __init__(self) -> None:
        self.first_entered = Event()
        self.release_first = Event()
        self._lock = Lock()
        self.call_count = 0
        self.active = 0
        self.max_active = 0

    def rename(self, source: Path, target: Path) -> None:
        with self._lock:
            self.call_count += 1
            call_number = self.call_count
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if call_number == 1:
                self.first_entered.set()
        try:
            if call_number == 1:
                assert self.release_first.wait(timeout=5)
            source.rename(target)
        finally:
            with self._lock:
                self.active -= 1


class PosixRenameFilesystem:
    def rename(self, source: Path, target: Path) -> None:
        if target.exists():
            raise FileExistsError(str(target))
        source.rename(target)


def _journal(tmp_path: Path, name: str) -> TransactionJournal:
    database = Database(tmp_path / f"{name}.sqlite3")
    database.initialize()
    return TransactionJournal(database)


def test_gate_is_exclusive_and_releases_after_context() -> None:
    gate = MutationGate()
    entered = Event()
    ready = Barrier(2)

    with gate.acquire():
        def enter_after_ready() -> None:
            ready.wait()
            _enter_gate(gate, entered)

        thread = Thread(target=enter_after_ready)
        thread.start()
        ready.wait()
        assert not entered.is_set()
    thread.join(timeout=5)

    assert entered.is_set()


def test_gate_releases_after_exception() -> None:
    gate = MutationGate()

    with pytest.raises(RuntimeError, match="boom"), gate.acquire():
        raise RuntimeError("boom")

    entered = Event()
    with gate.acquire():
        entered.set()
    assert entered.is_set()


def test_rename_and_undo_can_be_injected_with_the_same_gate(tmp_path: Path) -> None:
    gate = MutationGate()
    journal = _journal(tmp_path, "journal")

    executor = RenameExecutor(journal, mutation_gate=gate)
    undo = UndoService(journal, mutation_gate=gate)

    assert executor.mutation_gate is gate
    assert undo.mutation_gate is gate


def test_full_and_quick_rename_executor_transactions_do_not_overlap(tmp_path: Path) -> None:
    gate = MutationGate()
    filesystem = BlockingFilesystem()
    full_root = tmp_path / "full"
    quick_root = tmp_path / "quick"
    full_root.mkdir()
    quick_root.mkdir()
    (full_root / "A").mkdir()
    (quick_root / "A").mkdir()
    full = RenameExecutor(
        _journal(tmp_path, "full-journal"),
        filesystem=filesystem,
        mutation_gate=gate,
    )
    quick = RenameExecutor(
        _journal(tmp_path, "quick-journal"),
        filesystem=filesystem,
        mutation_gate=gate,
    )
    outcomes: list[RenameExecutionResult] = []

    first = Thread(
        target=lambda: outcomes.append(
            full.execute(full_root, [_plan(full_root, "A", "B")], confirmed=True)
        )
    )
    first.start()
    assert filesystem.first_entered.wait(timeout=5)
    second_attempted = Event()

    def run_quick() -> None:
        second_attempted.set()
        outcomes.append(
            quick.execute(quick_root, [_plan(quick_root, "A", "B")], confirmed=True)
        )

    second = Thread(
        target=run_quick
    )
    second.start()
    assert second_attempted.wait(timeout=5)
    assert filesystem.call_count == 1
    filesystem.release_first.set()
    first.join(timeout=5)
    second.join(timeout=5)

    assert len(outcomes) == 2
    assert all(outcome.status is TransactionStatus.COMPLETED for outcome in outcomes)
    assert filesystem.max_active == 1


def test_quick_and_undo_transactions_do_not_overlap(tmp_path: Path) -> None:
    gate = MutationGate()
    undo_root = tmp_path / "undo"
    quick_root = tmp_path / "quick"
    undo_root.mkdir()
    quick_root.mkdir()
    (undo_root / "A").mkdir()
    (quick_root / "A").mkdir()
    undo_journal = _journal(tmp_path, "undo-journal")
    prepare = RenameExecutor(
        undo_journal,
        filesystem=PosixRenameFilesystem(),
        mutation_gate=gate,
    )
    prepared = prepare.execute(undo_root, [_plan(undo_root, "A", "B")], confirmed=True)
    assert prepared.status is TransactionStatus.COMPLETED

    filesystem = BlockingFilesystem()
    quick = RenameExecutor(
        _journal(tmp_path, "quick-journal"),
        filesystem=filesystem,
        mutation_gate=gate,
    )
    undo = UndoService(undo_journal, filesystem=filesystem, mutation_gate=gate)
    outcomes: list[RenameExecutionResult | UndoResult] = []

    quick_thread = Thread(
        target=lambda: outcomes.append(
            quick.execute(quick_root, [_plan(quick_root, "A", "B")], confirmed=True)
        )
    )
    quick_thread.start()
    assert filesystem.first_entered.wait(timeout=5)
    undo_attempted = Event()

    def run_undo() -> None:
        undo_attempted.set()
        outcomes.append(undo.undo(prepared.transaction_id, confirmed=True))

    undo_thread = Thread(
        target=run_undo
    )
    undo_thread.start()
    assert undo_attempted.wait(timeout=5)
    assert filesystem.call_count == 1
    filesystem.release_first.set()
    quick_thread.join(timeout=5)
    undo_thread.join(timeout=5)

    assert len(outcomes) == 2
    assert {outcome.status for outcome in outcomes} == {
        TransactionStatus.COMPLETED,
        TransactionStatus.UNDONE,
    }
    assert filesystem.max_active == 1


def test_undo_and_quick_transactions_do_not_overlap_in_reverse_order(tmp_path: Path) -> None:
    gate = MutationGate()
    undo_root = tmp_path / "undo"
    quick_root = tmp_path / "quick"
    undo_root.mkdir()
    quick_root.mkdir()
    (undo_root / "A").mkdir()
    (quick_root / "A").mkdir()
    undo_journal = _journal(tmp_path, "undo-journal")
    prepare = RenameExecutor(
        undo_journal,
        filesystem=PosixRenameFilesystem(),
        mutation_gate=gate,
    )
    prepared = prepare.execute(undo_root, [_plan(undo_root, "A", "B")], confirmed=True)
    assert prepared.status is TransactionStatus.COMPLETED

    filesystem = BlockingFilesystem()
    quick = RenameExecutor(
        _journal(tmp_path, "quick-journal"),
        filesystem=filesystem,
        mutation_gate=gate,
    )
    undo = UndoService(undo_journal, filesystem=filesystem, mutation_gate=gate)
    outcomes: list[RenameExecutionResult | UndoResult] = []
    undo_attempted = Event()

    def run_undo() -> None:
        undo_attempted.set()
        outcomes.append(undo.undo(prepared.transaction_id, confirmed=True))

    undo_thread = Thread(target=run_undo)
    undo_thread.start()
    assert undo_attempted.wait(timeout=5)
    assert filesystem.first_entered.wait(timeout=5)
    quick_attempted = Event()

    def run_quick() -> None:
        quick_attempted.set()
        outcomes.append(
            quick.execute(quick_root, [_plan(quick_root, "A", "B")], confirmed=True)
        )

    quick_thread = Thread(target=run_quick)
    quick_thread.start()
    assert quick_attempted.wait(timeout=5)
    assert filesystem.call_count == 1
    filesystem.release_first.set()
    undo_thread.join(timeout=5)
    quick_thread.join(timeout=5)

    assert len(outcomes) == 2
    assert {outcome.status for outcome in outcomes} == {
        TransactionStatus.COMPLETED,
        TransactionStatus.UNDONE,
    }
    assert filesystem.max_active == 1


def _enter_gate(gate: MutationGate, entered: Event) -> None:
    with gate.acquire():
        entered.set()
