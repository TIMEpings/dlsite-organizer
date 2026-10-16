from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QThread, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent, QImage, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QListWidget

from dlsite_organizer.app.quick_action_controller import (
    QuickActionController,
    QuickActionRequest,
)
from dlsite_organizer.app.settings import AppSettings, SettingsService
from dlsite_organizer.domain.rename_execution import (
    ExecutionStatus,
    RenameOperation,
    TransactionStatus,
    UndoResult,
    UndoStatus,
)
from dlsite_organizer.domain.work import Work
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.lookup import LookupResult
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.services.quick_rename import QuickRenameService
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.undo_service import UndoService
from dlsite_organizer.ui.lightweight_window import LightweightWindow
from dlsite_organizer.ui.widgets import drop_zone as drop_zone_module
from dlsite_organizer.ui.widgets.drop_zone import (
    DirectoryDropZone,
    UnifiedDropZone,
    local_directory_paths,
)


class FakeLookupService:
    def lookup(self, raw_workno: str) -> LookupResult:
        work = Work(workno=raw_workno, title="Target", maker_name="Circle")
        return LookupResult(work=work, formatted_name=f"[{raw_workno}] Target")


class FakeQuickActionRunner:
    def __init__(self, completion) -> None:
        self._completion = completion

    def start(self) -> None:
        return

    def finish(self) -> None:
        self._completion(None, None)


@pytest.fixture
def qapp() -> QApplication:
    application = QApplication.instance()
    if isinstance(application, QApplication):
        return application
    return QApplication([])


def _window(tmp_path: Path) -> LightweightWindow:
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    settings = SettingsService(AppSettings(database_path=tmp_path / "metadata.sqlite3"))
    service = QuickRenameService(
        OrganizerService(FakeLookupService()),
        RenameExecutor(journal),
    )
    return LightweightWindow(service, UndoService(journal), settings)


def test_lightweight_window_has_unified_surface_and_controls(
    qapp: QApplication, tmp_path: Path
) -> None:
    window = _window(tmp_path)

    assert window.windowTitle() == "DLsite Organizer — 轻量模式"
    central = window.centralWidget()
    assert central is not None
    content_labels = central.findChildren(QLabel)
    content_text = [label.text() for label in content_labels]
    assert "DLsite Organizer · 轻量模式" not in content_text
    assert (
        "拖入一个或多个同一父目录下的 DLsite 作品文件夹，即按当前设置安全重命名。"
        not in content_text
    )
    assert window.findChild(QLabel, "lightweightBrandingImage") is None
    pixmap = window.drop_zone.branding_pixmap
    assert pixmap is not None and not pixmap.isNull()
    assert pytest.approx(0.18) == DirectoryDropZone.WATERMARK_OPACITY
    assert window.drop_zone.acceptDrops()
    assert window.undo_button.accessibleName() == "撤销最近一次"
    assert window.settings_button.accessibleName() == "设置"
    assert window.full_mode_button.accessibleName() == "切换到完整模式"
    assert type(window.drop_zone) is UnifiedDropZone
    assert window.drop_zone.recent_area.parentWidget() is window.drop_zone
    assert window.operation_list is window.drop_zone.operation_list
    assert window.operation_list.parentWidget() is window.drop_zone.recent_area
    assert window.findChildren(QListWidget) == [window.operation_list]
    assert window.operation_list.frameShape() == QFrame.Shape.NoFrame
    assert (
        window.operation_list.horizontalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert window.drop_zone.recent_operation_label.text() == "最近操作"
    assert window.operation_list.item(0) is not None
    assert window.operation_list.item(0).text() == "暂无最近操作"
    assert window.status_label.parentWidget() is window.drop_zone

    drop_layout = window.drop_zone.main_content.layout()
    assert drop_layout is not None
    detail_item = drop_layout.itemAt(1)
    assert detail_item is not None
    detail = detail_item.widget()
    assert isinstance(detail, QLabel)
    assert detail.text() == "拖入后立即按当前设置重命名"
    assert window.height() < 338
    assert window.minimumHeight() < 312
    assert window.minimumHeight() >= window.minimumSizeHint().height()
    window.show()
    qapp.processEvents()
    surface = window.drop_zone
    inner_surface = surface.rect().adjusted(2, 2, -2, -2)
    assert inner_surface.contains(surface.main_content.geometry())
    assert inner_surface.contains(surface.recent_separator.geometry())
    assert inner_surface.contains(surface.recent_area.geometry())
    assert not surface.main_content.geometry().intersects(surface.recent_area.geometry())
    assert window.drop_zone.rect().contains(window.status_label.geometry())
    window.close()


def test_unified_surface_paints_closed_border_at_default_and_minimum_sizes(
    qapp: QApplication, tmp_path: Path
) -> None:
    window = _window(tmp_path)
    window.show()
    qapp.processEvents()

    try:
        for height in (window.height(), window.minimumHeight()):
            window.resize(540, height)
            qapp.processEvents()
            image = QImage(window.drop_zone.size(), QImage.Format.Format_ARGB32)
            window.drop_zone.render(image)
            assert not image.isNull()
            watermark = window.drop_zone._watermark_rect()
            assert not watermark.isNull()
            assert window.drop_zone.main_content.geometry().contains(watermark.toRect())
            assert window.drop_zone.recent_area.geometry().bottom() < window.height()
            assert window.drop_zone.rect().contains(window.status_label.geometry())
    finally:
        window.close()


def test_unified_surface_busy_state_blocks_duplicate_drop_and_restores_controls(
    qapp: QApplication, tmp_path: Path
) -> None:
    window = _window(tmp_path)
    thread = QThread()
    window._thread = thread

    try:
        window._set_busy(True)
        assert not window.drop_zone.acceptDrops()
        assert not window.undo_button.isEnabled()
        assert not window.settings_button.isEnabled()
        assert not window.full_mode_button.isEnabled()

        window.start_quick_rename(tmp_path / "RJ01609020")
        assert window._worker is None
    finally:
        window._thread = None
        window._set_busy(False)
        thread.deleteLater()
        qapp.processEvents()
        window.close()


def test_unified_surface_keeps_directory_drop_zone_as_full_mode_contract(
    qapp: QApplication,
) -> None:
    zone = DirectoryDropZone("title", "detail")
    unified = UnifiedDropZone("title", "detail")

    try:
        assert type(zone) is DirectoryDropZone
        assert type(unified) is UnifiedDropZone
        assert zone.acceptDrops()
        assert unified.acceptDrops()
        assert zone.layout() is not None
        assert unified.recent_area.parentWidget() is unified
    finally:
        zone.close()
        unified.close()


def test_drop_zone_accepts_only_local_directories(tmp_path: Path) -> None:
    folder = tmp_path / "RJ01609020"
    folder.mkdir()
    file_path = tmp_path / "RJ01609020.txt"
    file_path.write_text("x", encoding="utf-8")

    directory_mime = QMimeData()
    directory_mime.setUrls([QUrl.fromLocalFile(str(folder))])
    mixed_mime = QMimeData()
    mixed_mime.setUrls([QUrl.fromLocalFile(str(folder)), QUrl.fromLocalFile(str(file_path))])
    remote_mime = QMimeData()
    remote_mime.setUrls([QUrl("https://example.invalid/RJ01609020")])

    assert local_directory_paths(directory_mime) == (folder,)
    assert local_directory_paths(mixed_mime) == ()
    assert local_directory_paths(remote_mime) == ()


def test_drop_zone_accepts_directory_drag_and_emits_paths(
    qapp: QApplication, tmp_path: Path
) -> None:
    folder = tmp_path / "RJ01609020"
    folder.mkdir()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(folder))])
    zone = DirectoryDropZone("title", "detail")
    captured: list[tuple[Path, ...]] = []
    zone.paths_dropped.connect(captured.append)

    enter = QDragEnterEvent(
        QPoint(20, 20),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    zone.dragEnterEvent(enter)
    assert enter.isAccepted()
    assert zone.property("dragActive") is True

    drop = QDropEvent(
        QPointF(20, 20),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    zone.dropEvent(drop)

    assert drop.isAccepted()
    assert captured == [(folder,)]
    assert zone.property("dragActive") is False
    zone.close()


def test_drop_zone_custom_paint_handles_normal_and_hover_states(qapp: QApplication) -> None:
    zone = DirectoryDropZone("title", "detail")
    zone.resize(420, zone.minimumHeight())
    zone.show()
    qapp.processEvents()
    image = QImage(zone.size(), QImage.Format.Format_ARGB32)
    zone.render(image)

    zone._set_highlight(True)
    qapp.processEvents()
    zone.render(image)

    assert zone.WATERMARK_OPACITY >= 0.15
    assert zone.WATERMARK_OPACITY <= 0.25
    zone.close()


def test_recent_operation_list_elides_long_text_and_delegates_drag_events(
    qapp: QApplication, tmp_path: Path
) -> None:
    folder = tmp_path / "RJ01609020"
    folder.mkdir()
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(folder))])
    zone = UnifiedDropZone("title", "detail")
    captured: list[tuple[Path, ...]] = []
    zone.paths_dropped.connect(captured.append)
    long_text = "RJ01609020  ✓ 已重命名\n" + ("很长的目录名称-" * 30)
    item = zone.operation_list.add_full_text(long_text)
    zone.resize(460, zone.minimumHeight())
    zone.show()
    qapp.processEvents()

    try:
        assert item.toolTip() == long_text
        assert item.text() != long_text
        assert (
            zone.operation_list.horizontalScrollBarPolicy()
            == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        enter = QDragEnterEvent(
            QPoint(20, 20),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        zone.operation_list.dragEnterEvent(enter)
        assert enter.isAccepted()
        assert zone.property("dragActive") is True

        leave = QDragLeaveEvent()
        zone.operation_list.dragLeaveEvent(leave)
        assert zone.property("dragActive") is False

        drop = QDropEvent(
            QPointF(20, 20),
            Qt.DropAction.CopyAction,
            mime,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
        zone.operation_list.dropEvent(drop)
        assert drop.isAccepted()
        assert captured == [(folder,)]
    finally:
        zone.close()


def test_lightweight_recent_list_keeps_a_styled_scrollbar_for_many_cjk_entries(
    qapp: QApplication, tmp_path: Path
) -> None:
    window = _window(tmp_path)
    window.show()
    window.operation_list.clear()
    for index in range(12):
        window.operation_list.add_full_text(
            f"RJ01609020  ✓ 已重命名  {index}  " + ("很长的目录名称-" * 12)
        )
    qapp.processEvents()

    try:
        scrollbar = window.operation_list.verticalScrollBar()
        assert scrollbar.maximum() > 0
        assert scrollbar.isVisible()
        assert "QScrollBar::handle:vertical:hover" in window.styleSheet()
        assert "width: 9px" in window.styleSheet()

        before = scrollbar.value()
        window.operation_list.setFocus()
        window.operation_list.setCurrentRow(0)
        for _ in range(window.operation_list.count() - 1):
            QTest.keyClick(window.operation_list, Qt.Key.Key_Down)
        qapp.processEvents()
        assert scrollbar.value() > before

        scrollbar.setValue(scrollbar.minimum())
        viewport = window.operation_list.viewport()
        point = viewport.rect().center()
        wheel = QWheelEvent(
            QPointF(point),
            QPointF(viewport.mapToGlobal(point)),
            QPoint(0, 0),
            QPoint(0, -120),
            Qt.MouseButton.NoButton,
            Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollUpdate,
            False,
        )
        qapp.sendEvent(viewport, wheel)
        qapp.processEvents()
        assert scrollbar.value() > scrollbar.minimum()
        assert window.operation_list.item(0).toolTip().startswith("RJ01609020")
    finally:
        window.close()


def test_drop_zone_without_branding_still_paints(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(drop_zone_module, "load_branding_pixmap", lambda _size: None)
    zone = DirectoryDropZone("title", "detail")
    zone.resize(420, zone.minimumHeight())
    zone.show()
    qapp.processEvents()
    image = QImage(zone.size(), QImage.Format.Format_ARGB32)
    zone.render(image)

    assert zone.branding_pixmap is None
    zone.close()


def test_explorer_entry_point_forwards_one_path_batch_to_drop_handler(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    window = _window(tmp_path)
    paths = (tmp_path / "RJ00000001", tmp_path / "RJ00000002")
    captured: list[tuple[Path, ...]] = []
    monkeypatch.setattr(window, "_handle_drop", lambda value: captured.append(tuple(value)))

    window.start_quick_rename(paths)

    assert captured == [paths]
    window.close()


def test_controller_wiring_keeps_local_and_follow_up_actions_on_one_queue(
    qapp: QApplication, tmp_path: Path
) -> None:
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    settings = SettingsService(AppSettings(database_path=tmp_path / "metadata.sqlite3"))
    service = QuickRenameService(
        OrganizerService(FakeLookupService()),
        RenameExecutor(journal),
    )
    runners: list[FakeQuickActionRunner] = []

    def factory(_request: QuickActionRequest, completion) -> FakeQuickActionRunner:
        runner = FakeQuickActionRunner(completion)
        runners.append(runner)
        return runner

    controller = QuickActionController(factory)
    window = LightweightWindow(
        service,
        UndoService(journal),
        settings,
        quick_action_controller=controller,
    )
    first = tmp_path / "RJ00000001"
    second = tmp_path / "RJ00000002"

    try:
        window.start_quick_rename(first)
        queued = controller.submit(second)

        assert queued.accepted
        assert len(runners) == 1
        assert controller.queue_depth == 1

        runners[0].finish()
        assert len(runners) == 2
        runners[1].finish()
        assert controller.active_request is None
    finally:
        window.close()
        database.dispose()


def test_undo_replaces_previous_recent_operation_without_scrollbar(
    qapp: QApplication, tmp_path: Path
) -> None:
    window = _window(tmp_path)
    operation = RenameOperation(
        transaction_id="tx",
        sequence=1,
        source_path=tmp_path / "RJ01609020",
        target_path=tmp_path / "[RJ01609020] Target",
        status=ExecutionStatus.SUCCESS,
        undo_status=UndoStatus.SUCCESS,
    )
    result = UndoResult(
        status=TransactionStatus.UNDONE,
        transaction=None,
        operations=(operation,),
    )

    try:
        window.show()
        qapp.processEvents()
        window.operation_list.add_full_text("RJ01609020  ✓ 已重命名")
        window._show_undo_result(result)
        qapp.processEvents()

        assert window.operation_list.count() == 1
        assert window.operation_list.item(0) is not None
        assert window.operation_list.item(0).data(Qt.ItemDataRole.UserRole) == (
            "撤销  ✓ 已恢复 1 个目录"
        )
        scrollbar = window.operation_list.verticalScrollBar()
        assert scrollbar.minimum() == 0
        assert scrollbar.maximum() == 0
        assert scrollbar.value() == 0
        assert not scrollbar.isVisible()
    finally:
        window.close()


@pytest.mark.parametrize("region", ["body", "title", "detail", "recent", "list",
                                   "scroll", "status", "undo", "settings", "full"])
def test_native_qt_drop_owner_covers_descendants(qapp, tmp_path, region):
    window = _window(tmp_path)
    window.show()
    qapp.processEvents()
    surface = window.drop_zone
    labels = surface.main_content.findChildren(QLabel)
    target = {"body": surface, "title": labels[0], "detail": labels[1],
              "recent": surface.recent_operation_label,
              "list": window.operation_list.viewport(),
              "scroll": window.operation_list.verticalScrollBar(),
              "status": window.status_label, "undo": window.undo_button,
              "settings": window.settings_button, "full": window.full_mode_button}[region]
    try:
        # Native Qt climbs to the closest acceptDrops ancestor. All children,
        # including the item-view viewport and scrollbar, defer to the surface.
        owner = target
        while not owner.acceptDrops():
            owner = owner.parentWidget()
            assert owner is not None
        assert owner is surface
        point = target.mapTo(surface, target.rect().center())
        assert surface.rect().contains(point)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(tmp_path))])
        captured = []
        clicked = []
        surface.paths_dropped.disconnect(window._handle_drop)
        surface.paths_dropped.connect(captured.append)
        for button in (window.undo_button, window.settings_button, window.full_mode_button):
            button.clicked.connect(lambda: clicked.append(True))
        enter = QDragEnterEvent(point, Qt.DropAction.CopyAction, mime,
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        qapp.sendEvent(owner, enter)
        assert enter.isAccepted()
        drop = QDropEvent(QPointF(point), Qt.DropAction.CopyAction, mime,
                          Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        qapp.sendEvent(owner, drop)
        assert drop.isAccepted()
        assert captured == [(tmp_path,)]
        assert clicked == []
        assert surface.property("dragActive") is False
    finally:
        window.close()


def test_icon_actions_focus_routing_and_blocked_surface(qapp, tmp_path):
    from PySide6.QtWidgets import QPushButton

    window = _window(tmp_path)
    window.show()
    qapp.processEvents()
    buttons = [window.undo_button, window.settings_button, window.full_mode_button]
    try:
        assert window.findChildren(QPushButton) == buttons
        for button in buttons:
            assert button.text() == ""
            assert button.toolTip() == button.accessibleName()
            assert not button.icon().isNull()
            assert button.parentWidget() is window.drop_zone
            assert button.focusPolicy() == Qt.FocusPolicy.StrongFocus
        settings, full = [], []
        window.settings_requested.connect(lambda: settings.append(True))
        window.full_mode_requested.connect(lambda: full.append(True))
        window.settings_button.setFocus()
        QTest.keyClick(window.settings_button, Qt.Key.Key_Space)
        QTest.keyClick(window.settings_button, Qt.Key.Key_Tab)
        assert window.full_mode_button.hasFocus()
        QTest.keyClick(window.full_mode_button, Qt.Key.Key_Space)
        assert settings == [True]
        assert full == [True]
        window.drop_zone.set_drop_enabled(False)
        assert window.status_label.isEnabled()
        assert window.settings_button.isEnabled()
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(tmp_path))])
        event = QDragEnterEvent(QPoint(20, 20), Qt.DropAction.CopyAction, mime,
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        window.drop_zone.dragEnterEvent(event)
        assert not event.isAccepted()
        assert window.drop_zone.property("dragActive") is False
    finally:
        window.close()


def test_unified_rejected_drag_resets_highlight(qapp):
    zone = UnifiedDropZone("title", "detail")
    try:
        zone._set_highlight(True)
        mime = QMimeData()
        event = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime,
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        zone.dragEnterEvent(event)
        assert not event.isAccepted()
        assert zone.property("dragActive") is False
    finally:
        zone.close()


def test_undo_icon_keeps_confirmation_and_recovery_navigation(qapp, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from PySide6.QtWidgets import QMessageBox

    window = _window(tmp_path)
    transaction = SimpleNamespace(operations=[SimpleNamespace(
        status=ExecutionStatus.SUCCESS, undo_status=UndoStatus.PENDING)])
    confirmations = []
    try:
        monkeypatch.setattr(window._undo_service, "latest_transaction", lambda: transaction)
        window.refresh_mutation_state()
        assert window.undo_button.isEnabled()

        def cancel(*args):
            confirmations.append(args)
            return QMessageBox.StandardButton.Cancel

        monkeypatch.setattr(QMessageBox, "question", cancel)
        QTest.mouseClick(window.undo_button, Qt.MouseButton.LeftButton)
        assert len(confirmations) == 1
        assert window._thread is None
        monkeypatch.setattr(window._undo_service, "unresolved_transaction", lambda: transaction)
        window.refresh_mutation_state()
        assert not window.drop_zone.acceptDrops()
        assert not window.undo_button.isEnabled()
        assert window.settings_button.isEnabled()
        assert window.full_mode_button.isEnabled()
        assert "恢复" in window.status_label.text()
    finally:
        window.close()


def test_unified_move_leave_and_invalid_drop_reset_drag_state(qapp, tmp_path):
    from PySide6.QtGui import QDragMoveEvent

    zone = UnifiedDropZone("title", "detail")
    valid = QMimeData()
    valid.setUrls([QUrl.fromLocalFile(str(tmp_path))])
    invalid = QMimeData()
    captured = []
    zone.paths_dropped.connect(captured.append)
    try:
        for mime, accepted in ((valid, True), (invalid, False)):
            move = QDragMoveEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime,
                                  Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            zone.dragMoveEvent(move)
            assert move.isAccepted() is accepted
            assert zone.property("dragActive") is accepted
        zone._set_highlight(True)
        zone.dragLeaveEvent(QDragLeaveEvent())
        assert zone.property("dragActive") is False
        zone._set_highlight(True)
        drop = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, invalid,
                          Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        zone.dropEvent(drop)
        assert not drop.isAccepted()
        assert zone.property("dragActive") is False
        assert captured == []
    finally:
        zone.close()


def test_undo_disabled_artwork_is_readable_and_distinct(qapp, tmp_path):
    from PySide6.QtGui import QIcon

    from dlsite_organizer.ui.theme.icons import lightweight_action_icon
    from dlsite_organizer.ui.theme.tokens import LIGHT_COLORS

    window = _window(tmp_path)
    try:
        assert not window.undo_button.isEnabled()
        assert window.undo_button.accessibleName() == "撤销最近一次"
        assert window.undo_button.toolTip() == "撤销最近一次"
        icon = lightweight_action_icon("undo")
        images = [icon.pixmap(18, 18, mode).toImage() for mode in
                  (QIcon.Mode.Normal, QIcon.Mode.Disabled)]
        for image, color in zip(images, (LIGHT_COLORS.text_secondary,
                                        LIGHT_COLORS.text_muted), strict=True):
            pixels = [image.pixelColor(x, y) for y in range(image.height())
                      for x in range(image.width()) if image.pixelColor(x, y).alpha() > 200]
            assert len(pixels) >= 12
            assert any(pixel.name() == color.lower() for pixel in pixels)
            assert all(image.pixelColor(x, 0).alpha() == 0 for x in range(image.width()))
        assert images[0] != images[1]
    finally:
        window.close()


def test_lightweight_shared_native_caption_and_body_geometry(qapp, tmp_path, monkeypatch):
    from dlsite_organizer.ui.widgets.title_bar import TitleBar
    from dlsite_organizer.ui.windows.native_chrome import Hit

    window = _window(tmp_path)
    window.resize(540, 280)
    window.show()
    qapp.processEvents()
    try:
        assert len(window.findChildren(TitleBar)) == 1
        assert window.title_bar.title_label.text() == window.windowTitle()
        assert not window.windowFlags() & Qt.WindowType.FramelessWindowHint
        surface = window.drop_zone
        body = window.drop_body
        assert surface.width() == body.width() - 12
        assert surface.height() == body.height() - 12
        assert surface.width() * surface.height() / (body.width() * body.height()) > 0.92
        if window._native_chrome is not None:
            assert window.title_bar.isVisible()
            assert body.y() == 40
            for widget, hit in ((window.title_bar.title_label, Hit.CAPTION),
                                (window.title_bar.icon_button, Hit.SYSTEM_MENU),
                                (window.title_bar.maximize_button, Hit.MAXIMIZE),
                                (window.title_bar.minimize_button, Hit.CLIENT),
                                (window.title_bar.close_button, Hit.CLIENT),
                                (surface, Hit.CLIENT)):
                point = widget.mapToGlobal(widget.rect().center())
                ratio = window.devicePixelRatioF()
                assert window._native_chrome.hit_test(
                    int(window.winId()), round(point.x() * ratio), round(point.y() * ratio)
                ) == hit
        window.title_bar.minimize_button.click()
        assert window.isMinimized()
        window.showNormal()
        window.title_bar.maximize_button.click()
        qapp.processEvents()
        assert window.isMaximized()
        assert window.title_bar.maximize_button.accessibleName() == "还原"
        window.title_bar.maximize_button.click()
        qapp.processEvents()
        assert not window.isMaximized()
        assert window.title_bar.maximize_button.accessibleName() == "最大化"
        assert window.size().width() == 540
        monkeypatch.setattr(window, "is_busy", lambda: True)
        monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information", lambda *_: None)
        window.title_bar.close_button.click()
        assert window.isVisible()
        monkeypatch.setattr(window, "is_busy", lambda: False)
        window.title_bar.close_button.click()
        assert not window.isVisible()
    finally:
        window.close()


def test_lightweight_native_chrome_fallback(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr("dlsite_organizer.ui.windows.native_chrome.sys.platform", "linux")
    window = _window(tmp_path)
    try:
        assert window._native_chrome is None
        assert window.title_bar.isHidden()
        assert window.nativeEvent(b"windows_generic_MSG", 0) == (False, 0)
        assert not window.windowFlags() & Qt.WindowType.FramelessWindowHint
    finally:
        window.close()


def test_unified_borderless_tint_during_valid_drag(qapp, tmp_path):
    window = _window(tmp_path)
    window.resize(540, 280)
    window.show()
    zone = window.drop_zone
    qapp.processEvents()
    try:
        zone.paths_dropped.disconnect(window._handle_drop)
        idle = zone.grab().toImage()
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(tmp_path))])
        enter = QDragEnterEvent(QPoint(20, 20), Qt.DropAction.CopyAction, mime,
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        zone.dragEnterEvent(enter)
        assert enter.isAccepted()
        qapp.processEvents()
        active = zone.grab().toImage()
        # Fixed, unobstructed interior points sample the actual QWidget rendering.
        samples = ((15, 20), (512, 20), (15, 40), (512, 40), (15, 60), (512, 60))
        diffs = []
        for x, y in samples:
            before = idle.pixelColor(x, y)
            after = active.pixelColor(x, y)
            diffs.append(sum(abs(a - b) for a, b in zip(
                (before.red(), before.green(), before.blue()),
                (after.red(), after.green(), after.blue()), strict=True)))
        assert sum(diff >= 12 for diff in diffs) >= 4
        assert all(active.pixelColor(x, y).name() == "#e8f0ff" for x, y in samples)
        # Surface edges remain borderless: there is no continuous accent stroke.
        assert active.pixelColor(15, 1).name() != "#315fdb"
        zone.dragLeaveEvent(QDragLeaveEvent())
        qapp.processEvents()
        assert zone.grab().toImage() == idle

        invalid = QMimeData()
        invalid.setText("unsupported input")
        rejected = QDragEnterEvent(QPoint(20, 20), Qt.DropAction.CopyAction, invalid,
                                   Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        zone.dragEnterEvent(rejected)
        qapp.processEvents()
        rejected_frame = zone.grab().toImage()
        assert not rejected.isAccepted()
        assert all(rejected_frame.pixelColor(x, y) == idle.pixelColor(x, y)
                   for x, y in samples)
        zone.dragLeaveEvent(QDragLeaveEvent())

        for _ in range(2):
            repeated = QDragEnterEvent(QPoint(20, 20), Qt.DropAction.CopyAction, mime,
                                       Qt.MouseButton.LeftButton,
                                       Qt.KeyboardModifier.NoModifier)
            zone.dragEnterEvent(repeated)
            qapp.processEvents()
            assert zone.grab().toImage() == active
            zone.dragLeaveEvent(QDragLeaveEvent())
            qapp.processEvents()
            assert zone.grab().toImage() == idle

        zone.dragEnterEvent(enter)
        qapp.processEvents()
        assert zone.grab().toImage() == active
        drop = QDropEvent(QPointF(20, 20), Qt.DropAction.CopyAction, mime,
                          Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        zone.dropEvent(drop)
        qapp.processEvents()
        assert drop.isAccepted()
        assert zone.grab().toImage() == idle
    finally:
        window.close()


@pytest.mark.parametrize("status", ["部分成功", "需要恢复，禁止修改", "排队等待"])
def test_drag_instruction_is_temporary_and_preserves_domain_status(qapp, tmp_path, status):
    window = _window(tmp_path)
    zone = window.drop_zone
    heading = zone.findChild(QLabel, "dropZoneTitle")
    detail = zone.findChild(QLabel, "dropZoneDescription")
    assert heading is not None and detail is not None
    original = (heading.text(), detail.text())
    window.status_label.setText(status)
    valid = QMimeData()
    valid.setUrls([QUrl.fromLocalFile(str(tmp_path))])
    invalid = QMimeData()
    captured = []
    # Avoid real submission while checking the surface's one-emission contract.
    zone.paths_dropped.disconnect(window._handle_drop)
    zone.paths_dropped.connect(captured.append)
    enter = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, valid,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    try:
        for _ in range(3):
            zone.dragEnterEvent(enter)
            assert enter.isAccepted()
            assert heading.text() == "松开以开始重命名"
            assert "立即" in detail.text()
            assert window.status_label.text() == status
            zone.dragLeaveEvent(QDragLeaveEvent())
            assert (heading.text(), detail.text()) == original
        rejected = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, invalid,
                                   Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        zone.dragEnterEvent(rejected)
        assert not rejected.isAccepted()
        assert heading.text() == "请拖入本地作品文件夹"
        assert zone.property("dragActive") is False
        zone.dragLeaveEvent(QDragLeaveEvent())
        assert (heading.text(), detail.text()) == original
        zone.dragEnterEvent(enter)
        drop = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, valid,
                          Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        zone.dropEvent(drop)
        assert drop.isAccepted()
        assert len(captured) == 1
        assert (heading.text(), detail.text()) == original
        assert zone.property("dragActive") is False
        zone.set_drop_enabled(False)
        zone.dragEnterEvent(enter)
        assert not enter.isAccepted()
        assert (heading.text(), detail.text()) == original
        assert window.status_label.text() == status
    finally:
        window.close()
