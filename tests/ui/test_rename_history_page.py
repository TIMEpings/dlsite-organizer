from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication, QHeaderView, QSplitter, QWidget

from dlsite_organizer.domain.rename_execution import RenameTransaction, TransactionStatus
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.rename_history import RenameHistoryService
from dlsite_organizer.ui.pages.rename_history_page import (
    RenameHistoryPage,
    _contrast_ratio,
    _format_datetime,
    _format_local_wall_time,
    _operation_selection_background,
    _OperationRowDelegate,
)


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


def _visible_transaction_ids(page: RenameHistoryPage) -> list[object]:
    transaction_ids = []
    for index in range(page.transaction_list.count()):
        item = page.transaction_list.item(index)
        assert item is not None
        transaction_ids.append(item.data(page._TRANSACTION_ID_ROLE))
    return transaction_ids


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
    item = page.transaction_list.item(0)
    assert item is not None
    assert transaction.transaction_id not in item.text()
    assert "已完成" in item.text()
    list_timestamp = item.text().splitlines()[1]
    assert list_timestamp == _format_local_wall_time(before.created_at)
    assert "(UTC" not in list_timestamp
    assert not item.icon().isNull()
    assert page.detail_heading.text() == "事务详情"
    assert transaction.transaction_id not in page.detail_heading.text()
    assert page.transaction_id_value.text() == transaction.transaction_id
    assert "事务状态：已完成" in page.transaction_summary.text()
    assert page.transaction_summary.text().count("事务状态：") == 1
    assert f"创建时间：{_format_datetime(before.created_at)}" in page.transaction_summary.text()
    assert str(root) in page.transaction_summary.text()
    assert page.operations_table.rowCount() == 1
    assert _cell_text(page, "operations_table", 0, 1) == "成功"
    assert _cell_text(page, "operations_table", 0, 3) == str(source)
    assert _cell_text(page, "operations_table", 0, 4) == str(target)
    assert page.source_path_value.text() == str(source)
    assert page.target_path_value.text() == str(target)
    assert page.observation_section.isHidden()
    assert journal.get_transaction(transaction.transaction_id) == before
    assert sorted(path.name for path in root.iterdir()) == before_entries
    assert source.is_dir()
    assert not target.exists()
    page.close()
    database.dispose()


def test_transaction_detail_heading_has_no_duplicate_status_or_extra_top_gap(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = tmp_path / "library"
    root.mkdir()
    transaction = journal.create_transaction(
        root,
        ((root / "source", root / "target"),),
    )
    journal.mark_transaction_completed(transaction.transaction_id, transaction.created_at)

    page = RenameHistoryPage(RenameHistoryService(journal))
    page.resize(960, 760)
    page.show()
    page.refresh_history()
    qapp.processEvents()

    assert page.detail_heading.text() == "事务详情"
    assert "已完成" not in page.detail_heading.text()
    assert page.query_status_label.isHidden()
    assert page.transaction_summary.text().count("事务状态：") == 1
    assert "事务状态：" not in page.recovery_label.text()
    metadata_gap = (
        page.transaction_summary.geometry().top() - page.detail_heading.geometry().bottom() - 1
    )
    assert metadata_gap <= page.detail_layout.spacing()

    page.close()
    database.dispose()


def test_transaction_master_width_is_fixed_from_font_and_detail_uses_remaining_space(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = tmp_path / "library"
    root.mkdir()
    _create_completed_transactions(journal, root, 3)

    page = RenameHistoryPage(RenameHistoryService(journal))
    page.resize(900, 760)
    page.show()
    page.refresh_history()
    qapp.processEvents()

    splitter = page.findChild(QSplitter, "renameHistorySplitter")
    master = page.findChild(QWidget, "renameHistoryMaster")
    assert splitter is not None
    assert master is not None
    assert splitter.handleWidth() == 0
    assert master.minimumWidth() == master.maximumWidth() == master.width()

    first_item = page.transaction_list.item(0)
    assert first_item is not None
    timestamp = first_item.text().splitlines()[1]
    timestamp_width = page.transaction_list.fontMetrics().horizontalAdvance(timestamp)
    icon_and_padding = page.transaction_list.iconSize().width() + 8
    assert page.transaction_list.viewport().width() - icon_and_padding >= timestamp_width

    for control in (page.newer_button, page.older_button, page.refresh_button):
        position = control.mapTo(master, QPoint(0, 0))
        assert position.x() >= 0
        assert position.x() + control.width() <= master.width()
        assert control.width() >= control.sizeHint().width()

    initial_master_width = master.width()
    initial_detail_width = page.detail_scroll.width()
    page.resize(1100, 760)
    qapp.processEvents()

    assert master.width() == initial_master_width
    assert page.detail_scroll.width() == initial_detail_width + 200
    assert splitter.sizes()[0] == master.width()
    assert splitter.sizes()[1] == page.detail_scroll.width()

    page.close()
    database.dispose()


def test_long_paths_and_errors_remain_inspectable_without_expanding_rows(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = tmp_path / "library"
    root.mkdir()
    long_segment = "deep-folder-segment-" * 18
    source = Path("C:/ready") / long_segment / "RJ01664608" / "final-source-folder"
    target = Path("C:/organized") / long_segment / "RJ01664608" / "final-target-folder"
    short_source = root / "short-source"
    short_target = root / "short-target"
    transaction = journal.create_transaction(
        root,
        ((source, target), (short_source, short_target)),
    )
    long_error = "Windows reported a sharing violation: " + ("details/" * 240)
    journal.record_execution_failure(
        transaction.transaction_id,
        1,
        long_error,
        TransactionStatus.FAILED,
        transaction.created_at,
    )
    before = journal.get_transaction(transaction.transaction_id)

    page = RenameHistoryPage(RenameHistoryService(journal))
    page.resize(1000, 760)
    page.refresh_history()
    qapp.processEvents()

    table = page.operations_table
    assert table.rowCount() == 2
    assert table.wordWrap() is False
    assert table.rowHeight(0) <= 30
    assert table.rowHeight(1) == table.rowHeight(0)
    assert table.horizontalScrollBarPolicy() == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    source_item = table.item(0, 3)
    target_item = table.item(0, 4)
    assert source_item is not None and source_item.text() == str(before.operations[0].source_path)
    assert target_item is not None and target_item.text() == str(before.operations[0].target_path)
    assert source_item.toolTip() == source_item.text()
    assert target_item.toolTip() == target_item.text()
    assert page.source_path_value.text() == source_item.text()
    assert page.target_path_value.text() == target_item.text()
    assert page.execution_error_value.toPlainText() == long_error

    page.copy_source_path_button.click()
    assert qapp.clipboard().text() == source_item.text()

    table.setCurrentCell(1, 0)
    assert page.operation_sequence_label.text() == "记录序号：2"
    assert page.source_path_value.text() == str(before.operations[1].source_path)
    assert page.target_path_value.text() == str(before.operations[1].target_path)
    assert page.execution_error_value.toPlainText() == before.operations[1].error
    assert journal.get_transaction(transaction.transaction_id) == before

    page.close()
    database.dispose()


def test_many_operation_rows_scroll_and_select_in_deterministic_order(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = tmp_path / "library"
    root.mkdir()
    operation_count = 80
    transaction = journal.create_transaction(
        root,
        tuple(
            (root / f"source-{index:03}", root / f"target-{index:03}")
            for index in range(operation_count)
        ),
    )
    journal.mark_transaction_completed(transaction.transaction_id, transaction.created_at)

    page = RenameHistoryPage(RenameHistoryService(journal))
    page.resize(1100, 800)
    page.show()
    page.refresh_history()
    qapp.processEvents()

    table = page.operations_table
    assert table.rowCount() == operation_count
    assert [_cell_text(page, "operations_table", row, 0) for row in range(3)] == [
        "1",
        "2",
        "3",
    ]
    assert table.verticalScrollBar().maximum() > 0
    table.verticalScrollBar().setValue(table.verticalScrollBar().maximum())
    table.setCurrentCell(operation_count - 1, 0)
    qapp.processEvents()
    assert _cell_text(page, "operations_table", operation_count - 1, 0) == "80"
    assert page.operation_sequence_label.text() == "记录序号：80"
    assert page.source_path_value.text().endswith("source-079")
    assert page.target_path_value.text().endswith("target-079")

    page.resize(820, 700)
    qapp.processEvents()
    splitter = page.findChild(QSplitter, "renameHistorySplitter")
    assert splitter is not None
    assert all(size > 0 for size in splitter.sizes())
    assert splitter.sizes()[1] > splitter.sizes()[0]

    page.close()
    database.dispose()


def test_operation_selection_readability_and_column_sizing(
    qapp: QApplication,
    tmp_path: Path,
) -> None:
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = tmp_path / "library"
    root.mkdir()
    transaction = journal.create_transaction(
        root,
        ((root / "source-one", root / "target-one"), (root / "source-two", root / "target-two")),
    )

    page = RenameHistoryPage(RenameHistoryService(journal))
    page.resize(1100, 800)
    page.show()
    page.refresh_history()
    qapp.processEvents()
    table = page.operations_table
    table.setCurrentCell(0, 1)
    qapp.processEvents()

    assert table.selectionBehavior() == table.SelectionBehavior.SelectRows
    assert table.selectionMode() == table.SelectionMode.SingleSelection
    assert [index.row() for index in table.selectionModel().selectedRows()] == [0]
    assert isinstance(table.itemDelegate(), _OperationRowDelegate)

    header = table.horizontalHeader()
    assert header.sectionResizeMode(0) == QHeaderView.ResizeMode.Fixed
    assert header.sectionResizeMode(1) == QHeaderView.ResizeMode.Fixed
    assert header.sectionResizeMode(2) == QHeaderView.ResizeMode.Fixed
    assert header.sectionResizeMode(3) == QHeaderView.ResizeMode.Stretch
    assert header.sectionResizeMode(4) == QHeaderView.ResizeMode.Stretch
    assert table.columnWidth(0) < table.columnWidth(1)
    assert table.columnWidth(1) <= table.fontMetrics().horizontalAdvance("预检失败") + 20
    assert table.columnWidth(2) <= table.fontMetrics().horizontalAdvance("未撤销") + 20

    for base_hex, highlight_hex, text_hex in (
        ("#ffffff", "#2463eb", "#18202b"),
        ("#1f2937", "#4a86e8", "#f8fafc"),
    ):
        palette = QPalette()
        base = QColor(base_hex)
        text = QColor(text_hex)
        palette.setColor(QPalette.ColorRole.Base, base)
        palette.setColor(QPalette.ColorRole.Highlight, QColor(highlight_hex))
        palette.setColor(QPalette.ColorRole.Text, text)
        selection_background = _operation_selection_background(palette)
        assert selection_background != base
        assert _contrast_ratio(selection_background, text) >= 4.5

    assert page._selected_transaction is not None
    assert page._selected_transaction.transaction_id == transaction.transaction_id
    page.close()
    database.dispose()


@pytest.mark.parametrize(
    ("local_offset", "expected_wall_time", "expected"),
    [
        (
            timedelta(hours=8),
            "2026-09-15 20:35:19",
            "2026-09-15 20:35:19 (UTC+08:00)",
        ),
        (
            timedelta(hours=-4),
            "2026-09-15 08:35:19",
            "2026-09-15 08:35:19 (UTC-04:00)",
        ),
        (
            timedelta(hours=5, minutes=30),
            "2026-09-15 18:05:19",
            "2026-09-15 18:05:19 (UTC+05:30)",
        ),
    ],
)
def test_timestamp_formatter_uses_numeric_local_offset_and_keeps_legacy_utc_wall_time(
    local_offset: timedelta,
    expected_wall_time: str,
    expected: str,
) -> None:
    legacy_sqlite_value = datetime(2026, 9, 15, 12, 35, 19)

    result = _format_datetime(legacy_sqlite_value, timezone(local_offset))
    aware_result = _format_datetime(
        legacy_sqlite_value.replace(tzinfo=UTC),
        timezone(local_offset),
    )
    list_result = _format_local_wall_time(legacy_sqlite_value, timezone(local_offset))
    aware_list_result = _format_local_wall_time(
        legacy_sqlite_value.replace(tzinfo=UTC),
        timezone(local_offset),
    )

    assert result == expected
    assert aware_result == expected
    assert list_result == expected_wall_time
    assert aware_list_result == expected_wall_time
    assert "中国标准时间" not in result
    assert "+0800" not in result


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

    assert page.detail_heading.text() == "事务详情"
    assert page.transaction_id_value.text() == transaction.transaction_id
    assert "事务状态：需要检查" in page.transaction_summary.text()
    recovery_text = page.recovery_label.text()
    assert "恢复阶段：forward" in recovery_text
    assert "恢复序号：2" in recovery_text
    assert "journal update was interrupted" in recovery_text
    assert "阻止新的重命名和普通撤销" in recovery_text

    assert page.operations_table.rowCount() == 2
    assert _cell_text(page, "operations_table", 0, 1) == "成功"
    assert _cell_text(page, "operations_table", 1, 1) == "失败"
    page.operations_table.setCurrentCell(1, 0)
    assert page.execution_error_value.toPlainText() == "recorded operation failure"

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

    assert page.transaction_id_value.text() == transaction.transaction_id
    assert "recovery record" in page.recovery_label.text()
    source_observation = _cell_text(page, "observations_table", 0, 1)
    assert source_observation.startswith("无法检查当前路径状态")
    assert "denied: source" in source_observation
    assert source_observation.endswith("…")
    assert len(source_observation) < 340
    page.close()
    database.dispose()


@pytest.mark.parametrize(
    (
        "transaction_count",
        "expected_first_page_count",
        "expected_older_page_count",
        "has_third_page",
    ),
    [
        (0, 0, 0, False),
        (1, 1, 0, False),
        (49, 49, 0, False),
        (50, 50, 0, False),
        (51, 50, 1, False),
        (100, 50, 50, False),
        (101, 50, 50, True),
    ],
)
def test_pagination_handles_exact_and_partial_last_pages(
    qapp: QApplication,
    tmp_path: Path,
    transaction_count: int,
    expected_first_page_count: int,
    expected_older_page_count: int,
    has_third_page: bool,
) -> None:
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    root = tmp_path / "library"
    root.mkdir()
    _create_completed_transactions(journal, root, transaction_count)

    page = RenameHistoryPage(RenameHistoryService(journal))
    page.refresh_history()

    assert page.transaction_list.count() == expected_first_page_count
    assert page.transaction_list.count() <= page.PAGE_SIZE
    assert page.older_button.isEnabled() is (transaction_count > page.PAGE_SIZE)
    first_page = journal.list_transactions(limit=page.PAGE_SIZE)
    assert _visible_transaction_ids(page) == [
        transaction.transaction_id for transaction in first_page
    ]
    if transaction_count:
        assert page._selected_transaction is not None
        assert page._selected_transaction.transaction_id == first_page[0].transaction_id
    else:
        assert page._selected_transaction is None
        assert not page.newer_button.isEnabled()

    if transaction_count > page.PAGE_SIZE:
        page.older_button.click()
        assert page.page_label.text().startswith("第 2 页")
        assert page.transaction_list.count() == expected_older_page_count
        assert page.transaction_list.count() <= page.PAGE_SIZE
        older_page = journal.list_transactions(limit=page.PAGE_SIZE, offset=page.PAGE_SIZE)
        assert _visible_transaction_ids(page) == [
            transaction.transaction_id for transaction in older_page
        ]
        assert page.older_button.isEnabled() is has_third_page

        if has_third_page:
            page.older_button.click()
            assert page.page_label.text().startswith("第 3 页")
            assert page.transaction_list.count() == 1
            assert not page.older_button.isEnabled()
            page.newer_button.click()
            assert page.page_label.text().startswith("第 2 页")

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
    journal.create_transaction(root, ((root / "source", root / "target"),))

    class MissingTransactionHistoryService(RenameHistoryService):
        def get_transaction(self, transaction_id: str) -> RenameTransaction:
            if transaction_id == "disappeared-transaction":
                raise RuntimeError("transaction no longer exists")
            return super().get_transaction(transaction_id)

    page = RenameHistoryPage(MissingTransactionHistoryService(journal))
    page.refresh_history()
    assert page.detail_heading.text() == "事务详情"

    page.open_transaction("disappeared-transaction")

    assert page.detail_heading.text() == "事务详情不可用"
    assert page.transaction_id_value.text() == "disappeared-transaction"
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
    assert page.detail_heading.text() == "事务详情"
    assert page._selected_transaction is not None
    assert page._selected_transaction.transaction_id == unresolved.transaction_id
    page.close()
    database.dispose()
