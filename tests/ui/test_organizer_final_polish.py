"""Phase 6.2 interaction and semantic painting regressions, entirely offline."""

from dataclasses import replace

import pytest
from PySide6.QtCore import (
    QCoreApplication,
    QItemSelectionModel,
    QMimeData,
    QPoint,
    QPointF,
    Qt,
    QUrl,
)
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent, QPainter, QPixmap
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem, QWidget
from tests.ui.test_organizer_page import FakeLookupService, _wait_for_scan_idle
from tests.ui.test_organizer_preview_model import synthetic_preview

from dlsite_organizer.domain.organizer import RenamePlanStatus
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.undo_service import UndoService
from dlsite_organizer.ui.delegates import organizer_delegates
from dlsite_organizer.ui.pages.organizer_page import OrganizerPage
from dlsite_organizer.ui.state.organizer_state import BusyOperation


@pytest.fixture
def qapp():
    app = QApplication.instance()
    return app if isinstance(app, QApplication) else QApplication([])


def test_empty_preview_hides_populated_hierarchy_and_retains_real_undo(qapp, tmp_path):
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    executor = RenameExecutor(journal)
    service = OrganizerService(FakeLookupService())
    page = OrganizerPage(service, execution_service=executor, undo_service=UndoService(journal))
    page.show()
    try:
        qapp.processEvents()
        assert page.empty_state.isVisible()
        for widget in (page.preview_context, page.table, page.detail_panel,
                       page.preview_actions, page.bulk_toolbar, page.undo_region):
            assert not widget.isVisible()
        assert page.empty_title_label.text() == "尚未生成预览"
        source = tmp_path / "old RJ000001"
        source.mkdir()
        preview = service.preview_paths(tmp_path, (source,))
        result = executor.execute(tmp_path, preview.plans, confirmed=True)
        assert result.success_count == 1
        page.refresh_mutation_state()
        assert page.undo_region.isVisible() and page.undo_button.isEnabled()
        page.set_preview(preview)
        assert page.preview_context.isVisible() and page.preview_actions.isVisible()
        page.clear_preview()
        assert page.empty_state.isVisible() and page.undo_region.isVisible()
    finally:
        page.close()
        database.dispose()


@pytest.mark.parametrize("action,expected", [
    ("select", (True, True)), ("invert", (False, True)), ("clear", (False, False)),
])
def test_bulk_preserves_nonready_highlight_current_and_coalesces(qapp, tmp_path, action, expected):
    page = OrganizerPage(OrganizerService(FakeLookupService()))
    preview = synthetic_preview(tmp_path, 4)
    page.set_preview(replace(preview, plans=(*preview.plans[:2],
        replace(preview.plans[2], status=RenamePlanStatus.CONFLICT),
        replace(preview.plans[3], status=RenamePlanStatus.UNCHANGED),
    )))
    page.preview_model.setData(page.preview_model.index(1, 0), Qt.CheckState.Unchecked,
                               Qt.ItemDataRole.CheckStateRole)
    selection = page.table.selectionModel()
    flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    selection.select(page.preview_model.index(0, 0), flags)
    selection.select(page.preview_model.index(2, 0), flags)
    selection.setCurrentIndex(page.preview_model.index(2, 1),
                              QItemSelectionModel.SelectionFlag.NoUpdate)
    highlight, current = page.state.highlighted_keys, page.state.current_key
    revision = page.state.preview_revision
    detail = page.detail_summary_label.text()
    changed = QSignalSpy(page.preview_model.dataChanged)
    checks = QSignalSpy(page.preview_model.checked_changed)
    resets = QSignalSpy(page.preview_model.modelReset)
    try:
        page.bulk_buttons[action].click()
        assert tuple(page.state.checked(i) for i in range(2)) == expected
        assert not page.state.checked(2) and not page.state.checked(3)
        assert page.state.highlighted_keys == highlight and page.state.current_key == current
        assert page.detail_summary_label.text() == detail
        assert page.state.preview_revision == revision
        assert page.execute_button.text() == f"执行重命名（{sum(expected)} 项）"
        assert changed.count() == checks.count() == 1 and resets.count() == 0
    finally:
        page.close()


@pytest.mark.parametrize("blocked", ["stale", "busy", "generation", "no-ready", "empty"])
def test_bulk_gates_and_accessibility(qapp, tmp_path, blocked):
    page = OrganizerPage(OrganizerService(FakeLookupService()))
    if blocked != "empty":
        preview = synthetic_preview(tmp_path, 2)
        if blocked == "no-ready":
            preview = replace(preview, plans=tuple(replace(
                plan, status=RenamePlanStatus.UNCHANGED) for plan in preview.plans))
        page.set_preview(preview)
    if blocked == "stale":
        page.invalidate_preview()
    elif blocked == "busy":
        page.state.busy = BusyOperation.SCAN
    elif blocked == "generation":
        page.state.settings_generation += 1
    page._update_bulk_controls()
    before = tuple(page.state.checked(i) for i in range(len(page.state.plans)))
    spy = QSignalSpy(page.preview_model.checked_changed)
    try:
        for action, button in page.bulk_buttons.items():
            assert not button.isEnabled()
            assert button.toolTip() == button.accessibleName()
            assert "执行" in button.accessibleName()
            assert button.focusPolicy() == Qt.FocusPolicy.StrongFocus
            assert button.width() == button.height() == 28
            page.preview_model.bulk_check(action)
        assert before == tuple(page.state.checked(i) for i in range(len(page.state.plans)))
        assert spy.count() == 0
    finally:
        page.state.finish_operation()
        page.close()


def test_scan_cancel_roles_and_keyboard_bulk_activation(qapp, tmp_path):
    page = OrganizerPage(OrganizerService(FakeLookupService()))
    page.set_preview(synthetic_preview(tmp_path, 2))
    page.show()
    try:
        qapp.processEvents()
        assert page.scan_button.property("visualRole") == "secondary"
        assert page.cancel_button.property("visualRole") == "secondary"
        assert page.scan_button.styleSheet() == page.cancel_button.styleSheet()
        page._set_loading(True)
        assert page.cancel_button.isVisible() and not page.scan_button.isVisible()
        page._set_loading(False)
        button = page.bulk_buttons["clear"]
        button.setFocus()
        QTest.keyClick(button, Qt.Key.Key_Space)
        assert page.state.checked_ready_count == 0
        QTest.keyClick(button, Qt.Key.Key_Tab)
        assert qapp.focusWidget() is not button
    finally:
        page.close()


@pytest.mark.parametrize("region", ["page", "input", "viewport", "empty", "detail", "blank"])
def test_workspace_drop_routes_to_existing_append(qapp, tmp_path, region):
    first, second = tmp_path / "old RJ000001", tmp_path / "old RJ000002"
    first.mkdir()
    second.mkdir()
    service = OrganizerService(FakeLookupService())
    page = OrganizerPage(service)
    if region != "empty":
        page.set_preview(service.preview_paths(tmp_path, (first,)))
    page.show()
    try:
        qapp.processEvents()
        page.detail_toggle.setChecked(True)
        targets = {"page": page, "input": page.root_input, "viewport": page.table.viewport(),
                   "empty": page.empty_state, "detail": page.detail_source_path.path_input,
                   "blank": page.footer}
        target = targets[region]
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(second))])
        enter = QDragEnterEvent(QPoint(20, 20), Qt.DropAction.CopyAction, mime,
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QCoreApplication.sendEvent(target, enter)
        assert enter.isAccepted() and page.drop_overlay.isVisible()
        leave = QDragLeaveEvent()
        QCoreApplication.sendEvent(target, leave)
        assert not page.drop_overlay.isVisible()
        QCoreApplication.sendEvent(target, QDragEnterEvent(
            QPoint(20, 20), Qt.DropAction.CopyAction, mime,
            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
        drop = QDropEvent(QPointF(20, 20), Qt.DropAction.CopyAction, mime,
                          Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QCoreApplication.sendEvent(target, drop)
        assert drop.isAccepted() and not page.drop_overlay.isVisible()
        _wait_for_scan_idle(qapp, page)
        assert page.preview is not None
        expected = [second] if region == "empty" else [first, second]
        assert [plan.source_path for plan in page.preview.plans] == expected
        if region != "empty":
            assert page.state.checked(0) and not page.state.checked(1)
    finally:
        _wait_for_scan_idle(qapp, page)
        page.close()


def test_invalid_drag_rejected_and_unrelated_widget_unchanged(qapp, tmp_path):
    page = OrganizerPage(OrganizerService(FakeLookupService()))
    unrelated = QWidget()
    page.show()
    try:
        file = tmp_path / "file.txt"
        file.write_text("fixture")
        for url in (QUrl.fromLocalFile(str(file)), QUrl("https://example.invalid/folder")):
            mime = QMimeData()
            mime.setUrls([url])
            event = QDragEnterEvent(QPoint(20, 20), Qt.DropAction.CopyAction, mime,
                                    Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            QCoreApplication.sendEvent(page.empty_state, event)
            assert not event.isAccepted() and not page.drop_overlay.isVisible()
        assert not unrelated.acceptDrops()
    finally:
        page.close()


@pytest.mark.parametrize("status", [RenamePlanStatus.READY, RenamePlanStatus.CONFLICT,
                                    RenamePlanStatus.UNCHANGED])
@pytest.mark.parametrize("selected", [False, True])
def test_semantic_paint_color_survives_selected_current_focus(
    qapp, tmp_path, monkeypatch, status, selected,
):
    page = OrganizerPage(OrganizerService(FakeLookupService()))
    preview = synthetic_preview(tmp_path, 1)
    plan = replace(preview.plans[0], status=status, warnings=("独立警告",))
    page.set_preview(replace(preview, plans=(plan,)))
    colors = []
    monkeypatch.setattr(organizer_delegates, "paint_status_mark",
                        lambda painter, rect, kind, color: colors.append((kind, color.name())))
    option = QStyleOptionViewItem()
    option.initFrom(page.table)
    option.rect = page.table.rect()
    if selected:
        option.state |= QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus
    pixmap = QPixmap(200, 40)
    painter = QPainter(pixmap)
    try:
        delegate = organizer_delegates.OrganizerStatusDelegate(page.table)
        delegate.paint(painter, option, page.preview_model.index(0, 1))
        kind, color = delegate._mark(plan)
        assert colors[0] == (kind, color.name())
        if status != RenamePlanStatus.CONFLICT:
            assert colors[1][0] == "warning"
    finally:
        painter.end()
        page.close()


def test_5k_bulk_notifications_are_bounded(qapp, tmp_path):
    page = OrganizerPage(OrganizerService(FakeLookupService()))
    page.set_preview(synthetic_preview(tmp_path, 5000))
    changed = QSignalSpy(page.preview_model.dataChanged)
    checks = QSignalSpy(page.preview_model.checked_changed)
    resets = QSignalSpy(page.preview_model.modelReset)
    try:
        for action, count in (("clear", 0), ("select", 5000), ("invert", 0), ("clear", 0)):
            page.preview_model.bulk_check(action)
            assert page.state.checked_ready_count == count
        assert changed.count() == checks.count() == 3
        assert resets.count() == 0
    finally:
        page.close()
