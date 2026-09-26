from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from dlsite_organizer.domain.rename_execution import RenameTransaction, TransactionStatus
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.rename_history import RenameHistoryService
from dlsite_organizer.ui.pages.rename_history_page import RenameHistoryPage


@pytest.fixture
def qapp() -> QApplication:
    application = QApplication.instance()
    if isinstance(application, QApplication):
        return application
    return QApplication([])


def _cell_text(page: RenameHistoryPage, table_name: str, row: int, column: int) -> str:
    table = getattr(page, table_name)
    item = table.item(row, column)
    assert item is not None
    return item.text()


def _create_completed_transactions(journal: TransactionJournal, root: Path, count: int) -> None:
    for index in range(count):
        transaction = journal.create_transaction(
            root,
            ((root / f"source-{index}", root / f"target-{index}"),),
        )
        journal.mark_operation_success(transaction.transaction_id, 1, transaction.created_at)
        journal.mark_transaction_completed(transaction.transaction_id, transaction.created_at)


def test_successful_history_can_be_opened_without_mutating_files_or_journal(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = tmp_path / "library"
    root.mkdir()
    source = root / "source"
    source.mkdir()
    target = root / "target"
    transaction = journal.create_transaction(root, ((source, target),))
    journal.mark_operation_success(transaction.transaction_id, 1, transaction.created_at)
    journal.mark_transaction_completed(transaction.transaction_id, transaction.created_at)
    before = journal.get_transaction(transaction.transaction_id)
    before_entries = sorted(path.name for path in root.iterdir())

    page = RenameHistoryPage(RenameHistoryService(journal))
    page.refresh_history()

    assert page.transaction_list.count() == 1
    assert page.detail_heading.text() == f"事务详情 · {transaction.transaction_id}"
    assert "记录的事务状态：已完成" in page.transaction_summary.text()
    assert str(root) in page.transaction_summary.text()
    assert page.operations_table.rowCount() == 1
    assert _cell_text(page, "operations_table", 0, 1) == str(source)
    assert _cell_text(page, "operations_table", 0, 2) == str(target)
    assert _cell_text(page, "operations_table", 0, 3) == "Journal 记录成功"
    assert page.observation_section.isHidden()
    assert journal.get_transaction(transaction.transaction_id) == before
    assert sorted(path.name for path in root.iterdir()) == before_entries
    assert source.is_dir()
    assert not target.exists()
    page.close()
    database.dispose()


def test_unresolved_detail_shows_recovery_facts_and_separate_observations(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = tmp_path / "library"
    root.mkdir()
    source_one = root / "source-one"
    target_one = root / "target-one"
    source_two = root / "source-two"
    target_two = root / "target-two"
    source_one.mkdir()
    # A target can exist now without proving it is the originally renamed entry.
    target_one.mkdir()
    target_two.write_text("replacement", encoding="utf-8")
    transaction = journal.create_transaction(
        root,
        ((source_one, target_one), (source_two, target_two)),
    )
    journal.mark_operation_success(transaction.transaction_id, 1, transaction.created_at)
    journal.record_execution_failure(
        transaction.transaction_id,
        2,
        "recorded operation failure",
        TransactionStatus.RECOVERY_REQUIRED,
        transaction.created_at,
    )
    journal.mark_recovery_required(
        transaction.transaction_id,
        2,
        "journal update was interrupted",
        transaction.created_at,
    )
    before = journal.get_transaction(transaction.transaction_id)
    before_entries = sorted(
        (path.name, path.is_dir(), path.is_file()) for path in root.iterdir()
    )

    page = RenameHistoryPage(RenameHistoryService(journal))
    page.refresh_history()

    assert page.detail_heading.text() == f"事务详情 · {transaction.transaction_id}"
    assert "记录的事务状态：需要检查" in page.transaction_summary.text()
    recovery_text = page.recovery_label.text()
    assert "恢复阶段：forward" in recovery_text
    assert "恢复序号：2" in recovery_text
    assert "journal update was interrupted" in recovery_text
    assert "阻止新的重命名和普通撤销" in recovery_text

    assert page.operations_table.rowCount() == 2
    assert _cell_text(page, "operations_table", 0, 3) == "Journal 记录成功"
    assert _cell_text(page, "operations_table", 1, 3) == "记录为失败"
    assert _cell_text(page, "operations_table", 1, 5) == "recorded operation failure"

    assert not page.observation_section.isHidden()
    assert page.observations_table.rowCount() == 2
    assert _cell_text(page, "observations_table", 0, 1) == "存在，目录"
    assert _cell_text(page, "observations_table", 0, 2) == "存在，目录"
    assert _cell_text(page, "observations_table", 1, 1) == "不存在"
    assert _cell_text(page, "observations_table", 1, 2) == "存在，文件"
    assert "路径存在不证明它仍指向原目录" in page.observation_caveat.text()
    assert "原目录" not in _cell_text(page, "observations_table", 0, 2)

    assert journal.get_transaction(transaction.transaction_id) == before
    after_entries = sorted(
        (path.name, path.is_dir(), path.is_file()) for path in root.iterdir()
    )
    assert after_entries == before_entries
    page.close()
    database.dispose()


def test_filesystem_observation_failure_does_not_hide_transaction_facts(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = tmp_path / "library"
    root.mkdir()
    transaction = journal.create_transaction(root, ((root / "source", root / "target"),))
    journal.mark_recovery_required(
        transaction.transaction_id,
        1,
        "recovery record",
        transaction.created_at,
    )

    def failed_observer(path: Path):
        raise PermissionError(f"denied: {path.name} {'x' * 1000}")

    page = RenameHistoryPage(
        RenameHistoryService(journal, path_observer=failed_observer),
    )
    page.refresh_history()

    assert transaction.transaction_id in page.detail_heading.text()
    assert "recovery record" in page.recovery_label.text()
    source_observation = _cell_text(page, "observations_table", 0, 1)
    assert source_observation.startswith("无法检查当前路径状态")
    assert "denied: source" in source_observation
    assert source_observation.endswith("…")
    assert len(source_observation) < 340
    page.close()
    database.dispose()


@pytest.mark.parametrize(
    ("transaction_count", "has_older_page"),
    [(50, False), (51, True)],
)
def test_pagination_handles_exact_and_partial_last_pages(
    qapp: QApplication,
    tmp_path: Path,
    transaction_count: int,
    has_older_page: bool,
) -> None:
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = tmp_path / "library"
    root.mkdir()
    _create_completed_transactions(journal, root, transaction_count)

    page = RenameHistoryPage(RenameHistoryService(journal))
    page.refresh_history()

    assert page.transaction_list.count() == 50
    assert page.older_button.isEnabled() is has_older_page
    if has_older_page:
        page.older_button.click()
        assert page.page_label.text().startswith("第 2 页")
        assert page.transaction_list.count() == 1
        assert not page.older_button.isEnabled()
        page.newer_button.click()
        assert page.page_label.text().startswith("第 1 页")

        page.older_button.click()
        assert page.page_label.text().startswith("第 2 页")
        _create_completed_transactions(journal, root, 1)
        page.refresh_after_mutation()
        assert page.page_label.text().startswith("第 1 页")
        assert page.transaction_list.count() == 50

    page.close()
    database.dispose()


def test_failed_direct_open_clears_the_previously_selected_transaction(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = tmp_path / "library"
    root.mkdir()
    visible = journal.create_transaction(root, ((root / "source", root / "target"),))

    class MissingTransactionHistoryService(RenameHistoryService):
        def get_transaction(self, transaction_id: str) -> RenameTransaction:
            if transaction_id == "disappeared-transaction":
                raise RuntimeError("transaction no longer exists")
            return super().get_transaction(transaction_id)

    page = RenameHistoryPage(MissingTransactionHistoryService(journal))
    page.refresh_history()
    assert page.detail_heading.text() == f"事务详情 · {visible.transaction_id}"

    page.open_transaction("disappeared-transaction")

    assert page.detail_heading.text() == "事务详情不可用 · disappeared-transaction"
    assert "transaction no longer exists" in page.query_status_label.text()
    assert page.transaction_summary.text() == ""
    assert page.operations_table.rowCount() == 0
    assert page._selected_transaction is None
    page.close()
    database.dispose()


def test_direct_open_outside_first_page_does_not_add_an_extra_page_row(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = tmp_path / "library"
    root.mkdir()
    unresolved = journal.create_transaction(
        root,
        ((root / "unresolved-source", root / "unresolved-target"),),
    )
    journal.mark_operation_success(unresolved.transaction_id, 1, unresolved.created_at)
    journal.mark_transaction_completed(unresolved.transaction_id, unresolved.created_at)
    _create_completed_transactions(journal, root, 50)
    journal.mark_recovery_required(
        unresolved.transaction_id,
        1,
        "needs inspection",
        unresolved.created_at,
    )

    page = RenameHistoryPage(RenameHistoryService(journal))
    page.refresh_history()
    assert page.transaction_list.count() == 50
    assert page._selected_transaction is not None
    assert page._selected_transaction.transaction_id != unresolved.transaction_id

    page.open_transaction(unresolved.transaction_id)

    assert page.transaction_list.count() == 50
    assert page.transaction_list.currentRow() == -1
    assert page.detail_heading.text() == f"事务详情 · {unresolved.transaction_id}"
    assert page._selected_transaction is not None
    assert page._selected_transaction.transaction_id == unresolved.transaction_id
    page.close()
    database.dispose()
