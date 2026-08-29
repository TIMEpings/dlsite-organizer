import pytest
from PySide6.QtWidgets import QApplication
from tests.services.test_lookup import FakeProvider

from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.ui.main_window import MainWindow


@pytest.fixture
def qapp() -> QApplication:
    application = QApplication.instance()
    if isinstance(application, QApplication):
        return application
    return QApplication([])


def test_main_window_exposes_organizer_page_without_running_filesystem_work(
    qapp: QApplication,
) -> None:
    lookup_service = LookupService(FakeProvider(), NamingService())
    window = MainWindow(lookup_service, CoverService())

    assert window.navigation_list.item(0).text() == "整理"
    assert window.pages.widget(0) is window.organizer_page
    assert window.organizer_page.table.rowCount() == 0
    assert not window.organizer_page.is_busy()
    window.close()
