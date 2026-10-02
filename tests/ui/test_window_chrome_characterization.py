"""Window requests must keep the full-mode lifecycle and routing boundaries."""

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import QApplication
from tests.services.test_lookup import FakeProvider

from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.ui.main_window import MainPage, MainWindow


@pytest.fixture
def window():
    app = QApplication.instance() or QApplication([])
    window = MainWindow(LookupService(FakeProvider(), NamingService()), CoverService())
    window.show()
    app.processEvents()
    yield window

    def accept_close(_window: object, event: object) -> None:
        assert isinstance(event, QCloseEvent)
        event.accept()

    window.set_close_handler(accept_close)
    window.close()


def test_close_request_calls_installed_lifecycle_even_when_busy(window, monkeypatch):
    calls = []
    monkeypatch.setattr(window, "is_busy", lambda: True)

    def lifecycle(owner, event):
        calls.append((owner, isinstance(event, QCloseEvent)))
        event.ignore()

    window.set_close_handler(lifecycle)
    assert not window.close()
    assert calls == [(window, True)]
    assert window.isVisible()


@pytest.mark.parametrize("page_name", ["lookup_page", "organizer_page", "about_page"])
def test_close_request_guards_each_busy_page(window, monkeypatch, page_name):
    messages = []
    monkeypatch.setattr(getattr(window, page_name), "is_busy", lambda: True)
    monkeypatch.setattr(
        "PySide6.QtWidgets.QMessageBox.information", lambda *_args: messages.append(True)
    )
    assert not window.close()
    assert window.isVisible()
    assert messages == [True]


def test_mode_request_preserves_pages_and_startup_preference(window, monkeypatch):
    requests = []
    window.lightweight_requested.connect(lambda: requests.append(True))
    pages = dict(window._pages_by_identity)
    settings = window._settings_service.settings
    window.navigate_to(MainPage.LOOKUP)

    window.lightweight_button.click()
    assert requests == [True]
    assert window.current_page is MainPage.LOOKUP
    assert window._pages_by_identity == pages
    assert window._settings_service.settings == settings

    monkeypatch.setattr(window, "is_busy", lambda: True)
    monkeypatch.setattr("PySide6.QtWidgets.QMessageBox.information", lambda *_args: None)
    window.lightweight_button.click()
    assert requests == [True]
