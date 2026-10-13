"""History rows project durable operation facts beside matching live observations."""

from pathlib import Path

import pytest

from dlsite_organizer.domain.rename_execution import TransactionStatus
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.rename_history import (
    CurrentPathObservation,
    CurrentPathState,
    RenameHistoryService,
)
from dlsite_organizer.ui.pages.rename_history_page import RenameHistoryPage


@pytest.fixture
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _completed(journal: TransactionJournal, root: Path, names: tuple[str, ...]):
    pairs = tuple((root / f"{name}-source", root / f"{name}-target") for name in names)
    transaction = journal.create_transaction(root, pairs)
    for sequence in range(1, len(pairs) + 1):
        journal.mark_operation_success(transaction.transaction_id, sequence, transaction.created_at)
    journal.mark_transaction_completed(transaction.transaction_id, transaction.created_at)
    return transaction


def _projected_text(page: RenameHistoryPage, row: int, column: int) -> str:
    model = page.operations_table.model()
    assert model is not None
    return str(model.index(row, column).data())


def test_operation_rows_show_observations_without_standalone_section(qapp, tmp_path: Path):
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = tmp_path / "library"
    root.mkdir()
    (root / "one-source").mkdir()
    transaction = _completed(journal, root, ("one",))
    page = RenameHistoryPage(RenameHistoryService(journal))
    try:
        page.open_transaction(transaction.transaction_id)

        source = _projected_text(page, 0, 3)
        target = _projected_text(page, 0, 4)
        assert "one-source" in source
        assert "当前：存在 · 目录" in source
        assert "one-target" in target
        assert "当前：不存在" in target
        assert not hasattr(page, "observations_table")
        assert not hasattr(page, "observation_section")
        assert page.source_observation_value.text() == "当前：存在 · 目录"
        assert page.target_observation_value.text() == "当前：不存在"
        assert not page.observation_caveat.isHidden()
    finally:
        page.close()
        database.dispose()


def test_observations_follow_operation_sequence_and_selection(qapp, tmp_path: Path):
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = tmp_path / "library"
    root.mkdir()
    first = _completed(journal, root, ("first", "second"))
    second = _completed(journal, root, ("other",))

    def observe(path: Path) -> CurrentPathObservation:
        state = {
            "first-source": CurrentPathState.FILE,
            "first-target": CurrentPathState.ABSENT,
            "second-source": CurrentPathState.ERROR,
            "second-target": CurrentPathState.DIRECTORY,
            "other-source": CurrentPathState.ABSENT,
            "other-target": CurrentPathState.FILE,
        }[path.name]
        error = "fixture access error" if state is CurrentPathState.ERROR else None
        return CurrentPathObservation(path, state, error)

    page = RenameHistoryPage(RenameHistoryService(journal, path_observer=observe))
    try:
        page.open_transaction(first.transaction_id)
        assert "当前：存在 · 文件" in _projected_text(page, 0, 3)
        assert "当前：不存在" in _projected_text(page, 0, 4)
        assert "当前：无法观察" in _projected_text(page, 1, 3)
        assert "当前：存在 · 目录" in _projected_text(page, 1, 4)

        page.operations_table.selectRow(1)
        qapp.processEvents()
        assert page.source_path_value.full_path.endswith("second-source")
        assert page.source_observation_value.text() == "当前：无法观察"
        assert "fixture access error" in page.source_observation_diagnostic.text()
        assert page.target_observation_value.text() == "当前：存在 · 目录"

        page.open_transaction(second.transaction_id)
        qapp.processEvents()
        assert page.source_path_value.full_path.endswith("other-source")
        assert page.source_observation_value.text() == "当前：不存在"
        assert page.target_observation_value.text() == "当前：存在 · 文件"
        assert page.operation_sequence_label.text() == "操作 1"
    finally:
        page.close()
        database.dispose()


def test_reobserve_updates_row_and_selected_detail_without_selection_loss(qapp, tmp_path: Path):
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = tmp_path / "library"
    root.mkdir()
    transaction = _completed(journal, root, ("first", "second"))
    states = {"first-source": CurrentPathState.DIRECTORY, "second-source": CurrentPathState.FILE}

    def observe(path: Path) -> CurrentPathObservation:
        state = states.get(path.name, CurrentPathState.ABSENT)
        return CurrentPathObservation(path, state)

    page = RenameHistoryPage(RenameHistoryService(journal, path_observer=observe))
    try:
        page.open_transaction(transaction.transaction_id)
        page.operations_table.selectRow(1)
        qapp.processEvents()
        selected_transaction = page._selected_id
        selected_sequence = page._operations[1].sequence
        before = journal.get_transaction(transaction.transaction_id)
        before_time = page.observation_updated_label.accessibleDescription()

        states["second-source"] = CurrentPathState.ERROR
        page.refresh_observations()
        qapp.processEvents()

        assert page._selected_id == selected_transaction
        current_row = page.operations_table.currentIndex().row()
        assert page._operations[current_row].sequence == selected_sequence
        assert "当前：无法观察" in _projected_text(page, 1, 3)
        assert page.source_observation_value.text() == "当前：无法观察"
        assert page.observation_updated_label.accessibleDescription() != before_time
        assert journal.get_transaction(transaction.transaction_id) == before
    finally:
        page.close()
        database.dispose()


def test_missing_observation_is_unknown_and_operation_selection_survives_history_refresh(
    qapp,
    tmp_path: Path,
    monkeypatch,
):
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = tmp_path / "library"
    root.mkdir()
    transaction = _completed(journal, root, ("first", "second"))
    service = RenameHistoryService(journal)
    monkeypatch.setattr(service, "observe_paths", lambda _transaction: ())
    page = RenameHistoryPage(service)
    try:
        page.open_transaction(transaction.transaction_id)
        assert "当前：无法观察" in _projected_text(page, 0, 3)
        assert "当前：无法观察" in _projected_text(page, 0, 4)

        page.operations_table.setCurrentIndex(page.operations_model.index(1, 0))
        qapp.processEvents()
        page.refresh_after_mutation()
        qapp.processEvents()

        assert page._selected_id == transaction.transaction_id
        assert page.operation_sequence_label.text() == "操作 2"
        assert page.source_path_value.full_path.endswith("second-source")
        assert page.source_observation_value.text() == "当前：无法观察"
    finally:
        page.close()
        database.dispose()


def test_missing_target_does_not_rewrite_historical_success(qapp, tmp_path: Path):
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = tmp_path / "library"
    root.mkdir()
    transaction = _completed(journal, root, ("missing",))
    page = RenameHistoryPage(RenameHistoryService(journal))
    try:
        page.open_transaction(transaction.transaction_id)

        assert page._selected_transaction is not None
        assert page._selected_transaction.status is TransactionStatus.COMPLETED
        assert _projected_text(page, 0, 1) == "成功"
        assert "当前：不存在" in _projected_text(page, 0, 4)
    finally:
        page.close()
        database.dispose()
