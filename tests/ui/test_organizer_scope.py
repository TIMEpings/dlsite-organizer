"""Phase 6.4 scope and desktop interaction regression contracts (offline)."""

import pytest
from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtTest import QTest
from tests.ui.test_organizer_page import FakeLookupService, _wait_for_scan_idle
from tests.ui.test_organizer_preview_model import synthetic_preview
from tests.ui.test_organizer_reconciliation import checked_codes

pytest_plugins = ["tests.ui.test_organizer_reconciliation"]

from dlsite_organizer.domain.organizer import RenamePlanStatus
from dlsite_organizer.services.drop_input import normalized_path_identity
from dlsite_organizer.services.folder_scanner import FolderScanFailure
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.ui.delegates.organizer_delegates import OrganizerCheckDelegate
from dlsite_organizer.ui.models.organizer_preview_model import OrganizerPreviewModel
from dlsite_organizer.ui.pages.organizer_page import OrganizerPage
from dlsite_organizer.ui.state.organizer_state import OrganizerState


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


@pytest.mark.parametrize("entry", [
    "bottom",
    pytest.param("context", marks=pytest.mark.xfail(strict=True, reason="Phase 6.4 contract")),
    pytest.param("delete", marks=pytest.mark.xfail(strict=True, reason="Phase 6.4 contract")),
])
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


def test_phase63_summary_and_work_hover_are_redundant(qapp, review):
    page, *_ = review
    assert page.summary_label.text() in page.summary_label.toolTip()
    assert "社团" in page.preview_model.index(1, 2).data(Qt.ItemDataRole.ToolTipRole)
    assert page.preview_model.index(1, 1).data(Qt.ItemDataRole.ToolTipRole) is None


def test_phase63_checkmark_uses_integer_center(qapp):
    rect = QRect(0, 0, 14, 14).adjusted(2, 2, -2, -2)
    assert QRectF(rect).center().x() - rect.center().x() == 1
    assert QRectF(rect).center().y() - rect.center().y() == 1
    assert OrganizerCheckDelegate.indicator_rect


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
