import pytest
from PySide6.QtWidgets import QApplication
from tests.services.test_lookup import FakeProvider

from dlsite_organizer import __version__
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

    assert [
        window.navigation_list.item(index).text()
        for index in range(window.navigation_list.count())
    ] == ["整理", "查询", "设置"]
    assert window.about_button.text() == f"关于 · v{__version__}"
    assert window.pages.widget(0) is window.organizer_page
    assert window.organizer_page.table.rowCount() == 0
    assert not window.organizer_page.is_busy()
    assert window.navigation_list.currentRow() == window.page_indices["organizer"]
    assert window.about_page.version_value.text() == f"v{__version__}"
    window.close()


def test_about_footer_opens_about_destination_without_main_nav_entry(
    qapp: QApplication,
) -> None:
    window = MainWindow(LookupService(FakeProvider(), NamingService()), CoverService())

    window.about_button.click()

    assert window.pages.currentWidget() is window.about_page
    assert window.navigation_list.currentRow() == -1
    assert [
        window.navigation_list.item(index).text()
        for index in range(window.navigation_list.count())
    ] == ["整理", "查询", "设置"]
    window.close()


def test_main_window_lightweight_settings_selects_settings_page(
    qapp: QApplication,
) -> None:
    window = MainWindow(LookupService(FakeProvider(), NamingService()), CoverService())

    window.show_settings_page()

    assert window.navigation_list.currentRow() == window.page_indices["settings"]
    assert window.pages.currentWidget() is window.settings_page
    window.close()
