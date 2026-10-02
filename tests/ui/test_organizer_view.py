"""Offscreen integration: standard Qt interactions and bounded widget ownership."""

from dataclasses import replace

import pytest
from PySide6.QtCore import QCoreApplication, QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QStyleOptionViewItem,
    QTableView,
    QTableWidget,
    QWidget,
)
from tests.services.test_lookup import FakeProvider
from tests.ui.test_organizer_page import FakeLookupService, _wait_for_scan_idle
from tests.ui.test_organizer_preview_model import synthetic_preview

from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.ui.delegates.organizer_delegates import OrganizerCheckDelegate
from dlsite_organizer.ui.main_window import MainPage, MainWindow
from dlsite_organizer.ui.models.organizer_preview_model import OrganizerPreviewModel
from dlsite_organizer.ui.pages.organizer_page import OrganizerPage
from dlsite_organizer.ui.state.organizer_state import PreviewStaleReason
from dlsite_organizer.ui.workers.organizer_worker import OrganizerWorkerResult


@pytest.fixture
def qapp() -> QApplication:
    app = QApplication.instance()
    return app if isinstance(app, QApplication) else QApplication([])


@pytest.mark.parametrize("count", [1000, 5000])
def test_production_view_has_no_cell_widgets_and_survives_navigation(qapp, tmp_path, count):
    window = MainWindow(
        LookupService(FakeProvider(), NamingService()), CoverService(),
        organizer_service=OrganizerService(FakeLookupService()),
    )
    page = window.organizer_page
    widget_count = len(page.table.findChildren(QWidget))
    preview = synthetic_preview(tmp_path, count)
    try:
        page.set_preview(preview)
        window.show()
        qapp.processEvents()
        assert isinstance(page.table, QTableView) and not isinstance(page.table, QTableWidget)
        model = page.table.model()
        assert isinstance(model, OrganizerPreviewModel)
        assert model.state is page.state
        assert not page.table.isSortingEnabled()
        assert page.table.acceptDrops() and page.table.viewport().acceptDrops()
        assert len(page.table.findChildren(QWidget)) == widget_count
        index = page.preview_model.index(count - 1, 0)
        page.table.setCurrentIndex(index)
        page.table.scrollTo(index)
        assert page.preview_model.setData(index, 0, Qt.ItemDataRole.CheckStateRole)
        qapp.processEvents()
        assert page.table.verticalScrollBar().value() > 0
        key = page.state.row_keys[-1]
        assert page.state.current_key == key
        assert key in page.state.highlighted_keys
        selection = page.table.selectionModel()
        window.navigate_to(MainPage.LOOKUP)
        window.navigate_to(MainPage.ORGANIZER)
        qapp.processEvents()
        assert page.preview is preview and page.table.selectionModel() is selection
        assert page.state.current_key == key and not page.state.checked(count - 1)
        page.set_preview(preview)
        assert page.table.selectionModel() is selection
        assert page.state.current_key is None and not page.state.highlighted_keys
        assert not page.table.currentIndex().isValid()
        page.table.selectRow(2)
        assert page.state.highlighted_keys == frozenset({page.state.row_keys[2]})
        assert page.remove_button.isEnabled()
        page.detail_toggle.setChecked(False)
        window.navigate_to(MainPage.LOOKUP)
        window.navigate_to(MainPage.ORGANIZER)
        assert page.detail_content.isHidden()
    finally:
        window.close()


def test_mouse_checkbox_space_and_selection_click_are_independent(qapp, tmp_path):
    page = OrganizerPage(OrganizerService(FakeLookupService()))
    page.resize(1160, 800)
    page.set_preview(synthetic_preview(tmp_path, 2))
    page.show()
    try:
        qapp.processEvents()
        view = page.table
        index = page.preview_model.index(0, 0)
        # Use the installed execution delegate: painted and clickable geometry coincide.
        option = QStyleOptionViewItem()
        option.initFrom(view)
        option.rect = view.visualRect(index)
        delegate = view.itemDelegateForColumn(0)
        assert isinstance(delegate, OrganizerCheckDelegate)
        delegate.initStyleOption(option, index)
        indicator = delegate.indicator_rect(option)
        assert indicator.isValid()
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=indicator.center())
        assert not page.state.checked(0)
        view.setCurrentIndex(index)
        view.setFocus()
        QTest.keyClick(view, Qt.Key.Key_Space)
        assert page.state.checked(0)
        text_cell = view.visualRect(page.preview_model.index(1, 3))
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=text_cell.center())
        assert page.state.checked(0) and page.state.checked(1)
        assert page.state.current_key == page.state.row_keys[1]
        page.invalidate_preview()
        assert not page.preview_model.setData(index, 0, Qt.ItemDataRole.CheckStateRole)
        assert page.state.checked(0)
    finally:
        page.close()


def test_drop_zone_event_appends_without_table_consuming_drop(qapp, tmp_path):
    first = tmp_path / "old RJ000001"
    second = tmp_path / "old RJ000002"
    first.mkdir()
    second.mkdir()
    service = OrganizerService(FakeLookupService())
    page = OrganizerPage(service)
    page.set_preview(service.preview_paths(tmp_path, (first,)))
    page.show()
    try:
        qapp.processEvents()
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(second))])
        enter = QDragEnterEvent(QPoint(20, 20), Qt.DropAction.CopyAction, mime,
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QCoreApplication.sendEvent(page.drop_zone, enter)
        assert enter.isAccepted()
        assert page.drop_overlay.isVisible()
        drop = QDropEvent(QPointF(20, 20), Qt.DropAction.CopyAction, mime,
                          Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QCoreApplication.sendEvent(page.drop_zone, drop)
        assert drop.isAccepted()
        assert not page.drop_overlay.isVisible()
        _wait_for_scan_idle(qapp, page)
        assert page.preview is not None
        assert [plan.source_path for plan in page.preview.plans] == [first, second]
        assert page.state.checked(0) and not page.state.checked(1)
    finally:
        _wait_for_scan_idle(qapp, page)
        page.close()


def test_removal_rejects_obsolete_result_and_current_is_separate_from_highlight(qapp, tmp_path):
    page = OrganizerPage(OrganizerService(FakeLookupService()))
    preview = synthetic_preview(tmp_path, 4)
    page.set_preview(preview)
    old = OrganizerWorkerResult(page.state.request_id, page.state.preview_revision, preview)
    page.table.selectRow(1)
    page.table.selectionModel().setCurrentIndex(
        page.preview_model.index(3, 0), page.table.selectionModel().SelectionFlag.NoUpdate
    )
    assert page.state.current_key == page.state.row_keys[3]
    assert page.state.highlighted_keys == frozenset({page.state.row_keys[1]})
    page.remove_selected()
    remaining = page.preview
    page._show_preview(old)
    assert page.preview is remaining
    assert page.preview_model.rowCount() == 3
    assert page.state.current_key is None and not page.state.highlighted_keys
    page.close()


@pytest.mark.parametrize("kind", ["generation", "cancelled"])
def test_direct_stale_preview_displays_previous_checks_but_blocks_edit(qapp, tmp_path, kind):
    page = OrganizerPage(OrganizerService(FakeLookupService()))
    preview = synthetic_preview(tmp_path, 2)
    preview = replace(preview, settings_generation=1) if kind == "generation" else replace(
        preview, cancelled=True
    )
    page.set_preview(preview)
    assert page.preview is preview
    assert page.preview_stale_reason is (
        PreviewStaleReason.SETTINGS_CHANGED
        if kind == "generation" else PreviewStaleReason.CANCELLED
    )
    assert page.table.isEnabled() and page.state.checked(0)
    assert not page.status_banner.isHidden()
    assert not page.preview_model.setData(page.preview_model.index(0, 0), 0,
                                          Qt.ItemDataRole.CheckStateRole)
    page.close()
