from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtWidgets import QApplication, QLabel

from dlsite_organizer.app.settings import AppSettings, SettingsService
from dlsite_organizer.domain.work import Work
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.lookup import LookupResult
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.services.quick_rename import QuickRenameService
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.undo_service import UndoService
from dlsite_organizer.ui.lightweight_window import LightweightWindow
from dlsite_organizer.ui.widgets.drop_zone import local_directory_paths


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


def test_lightweight_window_has_explicit_drop_zone_and_controls(
    qapp: QApplication, tmp_path: Path
) -> None:
    window = _window(tmp_path)

    assert window.windowTitle() == "DLsite Organizer — 轻量模式"
    assert window.drop_zone.acceptDrops()
    assert window.undo_button.text() == "撤销最近一次"
    assert window.settings_button.text() == "设置"
    assert window.full_mode_button.text() == "完整模式"
    drop_layout = window.drop_zone.layout()
    assert drop_layout is not None
    detail_item = drop_layout.itemAt(1)
    assert detail_item is not None
    detail = detail_item.widget()
    assert isinstance(detail, QLabel)
    assert "立即按当前设置重命名" in detail.text()
    window.close()


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
