"""Phase 6.4 scope and desktop interaction regression contracts (offline)."""

import pytest
from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtTest import QTest
from tests.ui.test_organizer_page import FakeLookupService, _wait_for_scan_idle
from tests.ui.test_organizer_reconciliation import qapp, review  # noqa: F401

from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.ui.delegates.organizer_delegates import OrganizerCheckDelegate
from dlsite_organizer.ui.pages.organizer_page import OrganizerPage


def test_phase63_root_refresh_reintroduces_removed_members(qapp, review):
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
    # Characterizes the old bug before changing production code.
    assert len(page.state.plans) == 6
    assert {"RJ000006", "RJ000007"} <= {p.work_code for p in page.state.plans}


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
