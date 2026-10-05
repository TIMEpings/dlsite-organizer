"""Phase 6.4 scope and desktop interaction regression contracts (offline)."""

import pytest
from PySide6.QtCore import QItemSelectionModel, QPoint, QRect, QRectF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QStyleOptionViewItem
from tests.ui.test_organizer_page import FakeLookupService, _wait_for_scan_idle
from tests.ui.test_organizer_preview_model import synthetic_preview
from tests.ui.test_organizer_reconciliation import checked_codes

from dlsite_organizer.domain.organizer import RenamePlanStatus
from dlsite_organizer.services.drop_input import normalized_path_identity
from dlsite_organizer.services.folder_scanner import FolderScanFailure
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.ui.delegates.organizer_delegates import OrganizerCheckDelegate
from dlsite_organizer.ui.models.organizer_preview_model import OrganizerPreviewModel
from dlsite_organizer.ui.pages.organizer_page import OrganizerPage
from dlsite_organizer.ui.state.organizer_state import OrganizerState
from dlsite_organizer.ui.widgets.organizer_review import OrganizerElidedLabel

pytest_plugins = ["tests.ui.test_organizer_reconciliation"]


def test_scoped_refresh_excludes_removed_members(qapp, review):
    page, service, executor, _journal, root, _lookup = review
    for name in ("D RJ000006", "E RJ000007"):
        (root / name).mkdir()
    page.set_preview(service.preview(root))
    removed = [i for i, p in enumerate(page.state.plans)
               if p.work_code in {"RJ000006", "RJ000007"}]
    page.state.project_highlight(removed, removed[0])
    page.remove_selected()
    assert len(page.state.plans) == 4
    result = executor.execute(root, page.selected_ready_plans()[:1], confirmed=True)
    page._show_execution_result(result)
    _wait_for_scan_idle(qapp, page)
    assert len(page.state.plans) == 4
    assert not {"RJ000006", "RJ000007"} & {p.work_code for p in page.state.plans}
    assert tuple(page.state.review_scope.values()) == tuple(p.source_path for p in page.state.plans)


@pytest.mark.parametrize("entry", ["bottom", "context", "delete"])
def test_remove_entries_replan_highlight_not_checked(qapp, tmp_path, entry):
    for name in ("a RJ000001", "b RJ000001", "c RJ000002"):
        (tmp_path / name).mkdir()
    service = OrganizerService(FakeLookupService())
    page = OrganizerPage(service)
    page.set_preview(service.preview(tmp_path))
    page.resize(1000, 750)
    page.show()
    qapp.processEvents()
    try:
        page.table.setCurrentIndex(page.preview_model.index(1, 1))
        if entry == "bottom":
            page.remove_button.click()
        elif entry == "context":
            assert hasattr(page, "remove_action"), "Table context removal is required"
            page.remove_action.trigger()
        else:
            page.table.setFocus()
            QTest.keyClick(page.table, Qt.Key.Key_Delete)
        assert [p.source_path.name for p in page.state.plans] == ["a RJ000001", "c RJ000002"]
        assert not page.state.checked(0) and page.state.checked(1)
        assert all((tmp_path / name).is_dir() for name in
                   ("a RJ000001", "b RJ000001", "c RJ000002"))
    finally:
        page.close()


def test_summary_and_status_hover_keep_accessibility_without_duplicates(qapp, review):
    page, *_ = review
    assert not page.summary_label.toolTip()
    assert page.summary_label.text() in page.summary_label.accessibleDescription()
    assert page.preview_model.index(1, 1).data(Qt.ItemDataRole.ToolTipRole) is None


@pytest.mark.parametrize("size", [13, 14, 18, 20])
def test_checkmark_bounds_center_in_logical_indicator(qapp, size):
    rect = QRect(7, 11, size, size)
    bounds = OrganizerCheckDelegate.checkmark_points(rect).boundingRect()
    assert bounds.center().x() == pytest.approx(QRectF(rect).center().x())
    assert bounds.center().y() == pytest.approx(QRectF(rect).center().y())
    assert QRectF(rect).contains(bounds.adjusted(-0.85, -0.85, 0.85, 0.85))


@pytest.mark.parametrize("refresh_fails", [False, True])
def test_canonical_scope_retry_second_execution_append_and_manual_rescan(
    qapp, review, refresh_fails,
):
    page, service, executor, _journal, parent, lookup = review
    root = parent.parent / "ABCDE"
    root.mkdir()
    paths = tuple(root / f"{letter} RJ{i:06}" for i, letter in enumerate("ABCDE", 1))
    for path in paths:
        path.mkdir()
    page.set_preview(service.preview(root))
    assert tuple(page.state.review_scope.values()) == paths
    page.state.project_highlight([3, 4], 3)
    page.remove_selected()
    assert tuple(page.state.review_scope.values()) == paths[:3]
    page.hide()
    page.show()
    checked_codes(page, {"RJ000001"})
    result = executor.execute(root, page.selected_ready_plans(), confirmed=True)
    target = result.operations[0].target_path
    expected = (target, *paths[1:3])
    service.fail = refresh_fails
    page.set_root_path(parent.parent / "next-input")
    page._show_execution_result(result)
    page.hide()
    _wait_for_scan_idle(qapp, page)
    assert tuple(page.state.review_scope.values()) == expected
    if refresh_fails:
        assert page.state.stale and page.undo_button.isEnabled()
        service.fail = False
        page.retry_refresh()
        _wait_for_scan_idle(qapp, page)
    page.show()
    assert tuple(p.source_path for p in page.state.plans) == expected
    assert page.state.completed(0) and page.state.checked_ready_count == 0
    assert page.state.plans[1].status is RenamePlanStatus.READY
    assert page.state.plans[2].status is RenamePlanStatus.READY
    assert page.root_input.text().endswith("next-input")
    checked_codes(page, {"RJ000002"})
    second = executor.execute(root, page.selected_ready_plans(), confirmed=True)
    page._show_execution_result(second)
    _wait_for_scan_idle(qapp, page)
    assert tuple(page.state.review_scope.values()) == (
        target, second.operations[0].target_path, paths[2])
    assert sum(page.state.completed(i) for i in range(3)) == 2
    page._handle_drop_paths([paths[3]])
    _wait_for_scan_idle(qapp, page)
    assert tuple(page.state.review_scope.values())[-1] == paths[3]
    assert len(page.state.plans) == 4 and not page.state.checked(3)
    page.set_root_path(root)
    page.scan_button.click()
    _wait_for_scan_idle(qapp, page)
    assert len(page.state.plans) == 5 and not page.state.completed_targets
    assert set(page.state.review_scope.values()) == set(root.iterdir())
    assert len(lookup.calls) > 5  # Fresh evaluation performed, not old-plan editing.
    page.clear_preview()
    assert not page.state.review_scope and not page.state.completed_targets
    assert page.state.reconciliation_root is None and page.undo_button.isEnabled()
    assert not OrganizerState().review_scope


def test_refresh_scope_order_real_sibling_occupancy_and_missing_diagnostic(tmp_path):
    a, b = (tmp_path / name for name in ("z RJ000001", "a RJ000002"))
    a.mkdir()
    b.mkdir()
    service = OrganizerService(FakeLookupService())
    target = service.preview_paths(tmp_path, [a]).plans[0].target_path
    assert target is not None
    target.mkdir()  # Unscoped sibling still blocks A.
    missing = tmp_path / "missing RJ000003"
    preview = service.refresh_preview_scope(tmp_path, (a, b, missing))
    assert tuple(p.source_path for p in preview.plans) == (a, b)
    assert preview.plans[0].status is RenamePlanStatus.CONFLICT
    assert preview.skipped_count == 1 and preview.scan.skipped[0].source_path == missing
    with pytest.raises(FolderScanFailure):
        service.refresh_preview_scope(tmp_path, (tmp_path.parent / "outside RJ000004",))


def test_partial_scope_preserves_position_and_sparse_remove(qapp, review):
    page, _service, executor, _journal, root, _lookup = review
    original = tuple(page.state.review_scope.values())
    result = executor.execute(root, page.selected_ready_plans()[:1], confirmed=True)
    # prepare_reconciliation only maps confirmed operations; unmatched members remain.
    assert page.state.prepare_reconciliation(result)
    assert tuple(page.state.review_scope.values()) == tuple(
        result.operations[0].target_path if path == result.operations[0].source_path else path
        for path in original)
    state = OrganizerState()
    model = OrganizerPreviewModel(state)
    model.replace_preview(synthetic_preview(root, 100))
    model.remove_rows(range(0, 100, 2))
    assert tuple(state.review_scope) == state.row_keys and len(state.row_keys) == 50
    assert all(normalized_path_identity(path) == key for key, path in state.review_scope.items())


def test_context_selection_blank_area_and_delete_focus(qapp, review):
    page, *_ = review
    page.resize(1100, 800)
    page.show()
    QTest.qWait(30)
    selection = page.table.selectionModel()
    flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    for row in (1, 2):
        selection.select(page.preview_model.index(row, 1), flags)
    point = page.table.visualRect(page.preview_model.index(2, 1)).center()
    QTest.mouseClick(page.table.viewport(), Qt.MouseButton.RightButton, pos=point)
    assert {i.row() for i in selection.selectedRows()} == {1, 2}
    assert page.table.currentIndex().row() == 2
    page._show_preview_menu(point)
    assert not page.preview_menu.isHidden()
    page.preview_menu.hide()
    blank = QPoint(10, page.table.viewport().height() - 5)
    page._show_preview_menu(blank)
    assert page.preview_menu.isHidden()
    assert {i.row() for i in selection.selectedRows()} == {1, 2}
    point = page.table.visualRect(page.preview_model.index(0, 1)).center()
    QTest.mouseClick(page.table.viewport(), Qt.MouseButton.RightButton, pos=point)
    assert {i.row() for i in selection.selectedRows()} == {0}
    page.root_input.setFocus()
    page.root_input.setText("ABC")
    page.root_input.setCursorPosition(0)
    QTest.keyClick(page.root_input, Qt.Key.Key_Delete)
    assert page.root_input.text() == "BC" and len(page.state.plans) == 4
    page.detail_source_path.path_input.setFocus()
    QTest.keyClick(page.detail_source_path.path_input, Qt.Key.Key_Delete)
    assert len(page.state.plans) == 4
    page.table.setFocus()
    for row in (1, 2):
        selection.select(page.preview_model.index(row, 1), flags)
    page.remove_action.trigger()
    assert len(page.state.plans) == 1  # all three highlighted, independent of checks


@pytest.mark.parametrize("column", [2, 3, 4])
def test_cell_tooltip_only_for_actual_elision(qapp, review, column):
    page, *_ = review
    index = page.preview_model.index(1, column)
    delegate = page.table.itemDelegateForColumn(column)
    option = QStyleOptionViewItem()
    option.initFrom(page.table)
    option.widget = page.table
    option.rect = QRect(0, 0, 2000, 40)
    assert delegate.tooltip_text(option, index) is None
    option.rect = QRect(0, 0, 20, 40)
    assert delegate.tooltip_text(option, index)
    assert "社团：" not in delegate.tooltip_text(option, index)


def test_labels_and_paths_tooltip_only_when_truncated(qapp, review):
    page, *_ = review
    label = OrganizerElidedLabel("完整文本")
    label.setParent(page)
    page.show()
    label.resize(500, 30)
    label.show()
    QTest.qWait(30)
    assert not label.toolTip() and label.accessibleDescription() == label.text()
    label.resize(10, 30)
    qapp.processEvents()
    assert label.toolTip() == label.text()
    label.set_extra_tooltip("additional transaction identity")
    label.resize(600, 30)
    qapp.processEvents()
    assert label.toolTip() == "additional transaction identity"
    label.close()
    path = page.preview_root_display
    path.resize(2000, 30)
    path.show()
    page.resize(2000, 800)
    page.show()
    QTest.qWait(30)
    assert not path.path_input.toolTip()
    page.resize(720, 650)
    path.set_path("C:/" + "long-directory/" * 50)
    QTest.qWait(30)
    assert path.path_input.toolTip() == path.text()
    assert page.bulk_menu_button.toolTip() and page.bulk_menu_button.accessibleName()
