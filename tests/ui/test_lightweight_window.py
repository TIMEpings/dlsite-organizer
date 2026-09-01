from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QThread, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent, QImage
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QListWidget

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
    assert window.undo_button.text() == "撤销最近一次"
    assert window.settings_button.text() == "设置"
    assert window.full_mode_button.text() == "完整模式"
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
    assert window.status_label.parentWidget() is central

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
    assert not window.drop_zone.geometry().intersects(window.status_label.geometry())
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
            assert not window.drop_zone.geometry().intersects(window.status_label.geometry())
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
        assert not window.drop_zone.isEnabled()
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
