"""Phase 6.1 presentation regressions; model/state semantics stay authoritative."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem
from tests.ui.test_organizer_page import FakeLookupService, _wait_for_scan_idle
from tests.ui.test_organizer_preview_model import synthetic_preview

from dlsite_organizer.domain.organizer import RenamePlanStatus
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.ui.delegates.organizer_delegates import (
    OrganizerCheckDelegate,
    OrganizerStatusDelegate,
)
from dlsite_organizer.ui.pages.organizer_page import OrganizerPage
from dlsite_organizer.ui.state.organizer_state import BusyOperation


@pytest.fixture
def qapp():
    app = QApplication.instance()
    return app if isinstance(app, QApplication) else QApplication([])


def settle(app):
    # Drain the layout and the one-shot callback posted by viewport resize.
    for _ in range(3):
        app.processEvents()


def assert_fills_viewport(page):
    assert abs(sum(page.table.columnWidth(i) for i in range(5))
               - page.table.viewport().width()) <= 2


@pytest.mark.parametrize("count", [7, 1000, 5000])
def test_first_hidden_preview_fills_without_page_resize(qapp, tmp_path, count):
    page = OrganizerPage(OrganizerService(FakeLookupService()))
    page.resize(1160, 800)
    page.show()
    try:
        settle(qapp)
        assert page.table.isHidden()
        original_size = page.size()
        preview = synthetic_preview(tmp_path, count)
        page.set_preview(preview)
        settle(qapp)
        assert page.size() == original_size
        assert_fills_viewport(page)
        for size in [(980, 650), (1400, 900), (1160, 800)]:
            page.resize(*size)
            settle(qapp)
            assert_fills_viewport(page)
        page.detail_toggle.setChecked(False)
        settle(qapp)
        assert_fills_viewport(page)
        page.preview_model.append_preview(synthetic_preview(tmp_path, count + 2))
        settle(qapp)
        assert_fills_viewport(page)
        page.set_preview(preview)
        settle(qapp)
        assert_fills_viewport(page)
    finally:
        page.close()


def test_fresh_first_worker_scan_without_resize_and_second_scan(qapp, tmp_path):
    for code in ("RJ000001", "RJ000002", "RJ000003"):
        (tmp_path / f"old {code}").mkdir()
    page = OrganizerPage(OrganizerService(FakeLookupService()))
    page.resize(1160, 800)
    page.show()
    try:
        settle(qapp)
        original_size = page.size()
        page.set_root_path(tmp_path)
        page.scan_button.click()
        _wait_for_scan_idle(qapp, page)
        settle(qapp)
        assert page.size() == original_size
        assert_fills_viewport(page)
        first_widths = [page.table.columnWidth(i) for i in range(5)]
        page.scan_button.click()
        _wait_for_scan_idle(qapp, page)
        settle(qapp)
        assert first_widths == [page.table.columnWidth(i) for i in range(5)]
    finally:
        _wait_for_scan_idle(qapp, page)
        page.close()


@pytest.mark.parametrize("checked", [False, True])
@pytest.mark.parametrize("blocked", [None, "stale", "busy", "non-ready"])
def test_centered_check_geometry_and_disabled_projection(qapp, tmp_path, checked, blocked):
    page = OrganizerPage(OrganizerService(FakeLookupService()))
    page.set_preview(synthetic_preview(tmp_path, 1))
    index = page.preview_model.index(0, 0)
    page.preview_model.setData(index, Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked,
                               Qt.ItemDataRole.CheckStateRole)
    if blocked == "stale":
        page.invalidate_preview()
    elif blocked == "busy":
        page.state.busy = BusyOperation.SCAN
        page._set_loading(True)
    elif blocked == "non-ready":
        assert page.preview is not None
        page.set_preview(replace(page.preview, plans=(replace(
            page.preview.plans[0], status=RenamePlanStatus.CONFLICT,
        ),)))
    page.resize(1160, 800)
    page.show()
    try:
        settle(qapp)
        option = QStyleOptionViewItem()
        option.initFrom(page.table)
        option.rect = page.table.visualRect(index)
        delegate = page.table.itemDelegateForColumn(0)
        assert isinstance(delegate, OrganizerCheckDelegate)
        rect = delegate.indicator_rect(option)
        assert rect.center() == option.rect.center()
        assert option.rect.contains(rect.adjusted(-3, -3, 3, 3))
        actual, editable = delegate.check_projection(index, option)
        assert actual == (checked and blocked != "non-ready")
        assert editable == (blocked is None)
        page.table.viewport().grab()  # Exercise all four drawing combinations.
        assert actual == page.state.checked(0)
    finally:
        page.state.finish_operation()
        page.close()


def test_check_click_space_enter_and_highlight_are_independent(qapp, tmp_path):
    page = OrganizerPage(OrganizerService(FakeLookupService()))
    page.set_preview(synthetic_preview(tmp_path, 2))
    page.resize(1160, 800)
    page.show()
    try:
        settle(qapp)
        index = page.preview_model.index(0, 0)
        page.table.setCurrentIndex(index)
        page.table.setFocus()
        QTest.keyClick(page.table, Qt.Key.Key_Space)
        assert not page.state.checked(0) and page.state.checked(1)
        assert page.state.highlighted_keys == frozenset({page.state.row_keys[0]})
        assert page.remove_button.text() == "从预览移除（1）"
        assert page.execute_button.text() == "执行重命名（1 项）"
        revision = page.state.preview_revision
        QTest.keyClick(page.table, Qt.Key.Key_Return)
        assert page.state.preview_revision == revision and not page.is_busy()
        # Selection changes never turn a check on; current can be unselected.
        page.table.selectionModel().clearSelection()
        page.table.selectionModel().setCurrentIndex(
            index, QItemSelectionModel.SelectionFlag.NoUpdate
        )
        assert not page.state.highlighted_keys and page.state.checked(1)
        option = QStyleOptionViewItem()
        option.initFrom(page.table)
        option.rect = page.table.visualRect(index)
        option.state |= QStyle.StateFlag.State_HasFocus
        QTest.mouseClick(page.table.viewport(), Qt.MouseButton.LeftButton,
                         pos=OrganizerCheckDelegate.indicator_rect(option).center())
        assert page.state.checked(0)
        assert not page._column_layout_timer.isActive()
    finally:
        page.close()


@pytest.mark.parametrize("status", [
    RenamePlanStatus.READY, RenamePlanStatus.UNCHANGED, RenamePlanStatus.CONFLICT,
    RenamePlanStatus.AMBIGUOUS_CODE, RenamePlanStatus.LOOKUP_FAILED,
    RenamePlanStatus.INVALID_TARGET,
])
@pytest.mark.parametrize("warnings", [(), ("独立警告",)])
def test_status_warning_vocabulary_and_concise_tooltips(qapp, tmp_path, status, warnings):
    page = OrganizerPage(OrganizerService(FakeLookupService()))
    preview = synthetic_preview(tmp_path, 1)
    normal = status in (RenamePlanStatus.READY, RenamePlanStatus.UNCHANGED)
    plan = replace(preview.plans[0], status=status, warnings=warnings,
                   error=None if normal else "真实阻止原因")
    page.set_preview(replace(preview, plans=(plan,)))
    try:
        mark, _ = OrganizerStatusDelegate._mark(plan)
        assert mark == (status.value if normal else "blocked")
        assert OrganizerStatusDelegate.additional_warning_marker(plan) == bool(normal and warnings)
        tip = page.preview_model.index(0, 1).data(Qt.ItemDataRole.ToolTipRole)
        if not warnings and normal:
            assert tip is None
        else:
            assert tip and ("独立警告" if normal else "真实阻止原因") in tip
            assert all(filler not in tip for filler in ("悬停查看", "点击查看"))
        page.table.setCurrentIndex(page.preview_model.index(0, 1))
        assert ("独立警告" in page.detail_diagnostics.toPlainText()) == bool(warnings)
        assert page.state.plans[0] is plan
    finally:
        page.close()
