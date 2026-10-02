"""Main window and lightweight navigation shell."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from PySide6.QtCore import QEvent, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QCloseEvent, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from dlsite_organizer import __version__
from dlsite_organizer.app.branding import load_application_icon
from dlsite_organizer.app.runtime import RuntimeSignals
from dlsite_organizer.app.settings import AppSettings, SettingsService
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.drop_input import DropInputService
from dlsite_organizer.services.explorer_integration import ExplorerIntegrationService
from dlsite_organizer.services.folder_scanner import FolderScanner
from dlsite_organizer.services.lookup import LookupService, operation_settings_key
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.rename_history import RenameHistoryService
from dlsite_organizer.services.rename_planner import RenamePlanner
from dlsite_organizer.services.undo_service import UndoService
from dlsite_organizer.ui.pages.about_page import AboutPage
from dlsite_organizer.ui.pages.lookup_page import LookupPage
from dlsite_organizer.ui.pages.organizer_page import OrganizerPage
from dlsite_organizer.ui.pages.rename_history_page import RenameHistoryPage
from dlsite_organizer.ui.pages.settings_page import SettingsPage
from dlsite_organizer.ui.theme import apply_light_theme, light_stylesheet
from dlsite_organizer.ui.widgets.navigation import NavigationItemDelegate
from dlsite_organizer.ui.widgets.title_bar import TitleBar
from dlsite_organizer.ui.windows.native_chrome import WindowsChrome
from dlsite_organizer.ui.workers.update_check_worker import UpdateCheckServiceLike


class MainPage(StrEnum):
    """Stable identities for the public pages and the About destination."""

    ORGANIZER = "organizer"
    LOOKUP = "lookup"
    SETTINGS = "settings"
    HISTORY = "history"
    ABOUT = "about"


class MainWindow(QMainWindow):
    """Host the public pages of the full application."""

    lightweight_requested = Signal()

    def __init__(
        self,
        lookup_service: LookupService,
        cover_service: CoverService,
        organizer_service: OrganizerService | None = None,
        rename_executor: RenameExecutor | None = None,
        undo_service: UndoService | None = None,
        candidate_review_queue_service=None,
        settings_service: SettingsService | None = None,
        quick_rename_service=None,
        explorer_integration_service: ExplorerIntegrationService | None = None,
        runtime_signals: RuntimeSignals | None = None,
        update_check_service: UpdateCheckServiceLike | None = None,
        rename_history_service: RenameHistoryService | None = None,
    ) -> None:
        super().__init__()
        self._native_chrome: WindowsChrome | None = None
        application = QApplication.instance()
        if isinstance(application, QApplication):
            apply_light_theme(application)
        self._lookup_service = lookup_service
        self._settings_service = settings_service or SettingsService(AppSettings())
        self._settings_snapshot = self._settings_service.settings
        self._lookup_service.apply_settings(self._settings_snapshot)
        self._quick_rename_service = quick_rename_service
        self._runtime_signals = runtime_signals
        self._close_handler: Callable[[object, object], None] | None = None
        # Kept in the constructor for callers from the research-era shell.
        # Candidate services remain backend capabilities but are intentionally
        # not mounted in the public application navigation.
        self._candidate_review_queue_service = candidate_review_queue_service
        self.setWindowTitle("DLsite Organizer")
        self.setWindowIcon(load_application_icon())
        self.resize(1160, 800)

        central = QWidget()
        central.setObjectName("applicationSurface")
        frame = QVBoxLayout(central)
        frame.setContentsMargins(0, 0, 0, 0)
        frame.setSpacing(0)
        self.title_bar = TitleBar(central)
        self.title_bar.minimize_requested.connect(self.showMinimized)
        self.title_bar.maximize_requested.connect(self._toggle_maximized)
        self.title_bar.close_requested.connect(self.close)
        self._native_chrome = WindowsChrome.create(self)
        # Keep native styles and Qt's ordinary frame on unsupported platforms.
        self.title_bar.setVisible(self._native_chrome is not None)
        frame.addWidget(self.title_bar)
        shell = QHBoxLayout()
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        frame.addLayout(shell, 1)

        self.navigation = QWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setFixedWidth(184)
        nav_layout = QVBoxLayout(self.navigation)
        nav_layout.setContentsMargins(16, 12, 16, 16)
        nav_layout.setSpacing(12)

        self.navigation_list = QListWidget()
        self.navigation_list.setObjectName("navigationList")
        self.navigation_list.setSpacing(4)
        self.navigation_list.setItemDelegate(NavigationItemDelegate(self.navigation_list))
        nav_layout.addWidget(self.navigation_list, 1)

        self.settings_button = QPushButton("设置")
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.setCheckable(True)
        self.settings_button.setToolTip("打开设置页面")
        self.settings_button.clicked.connect(self._navigate_from_sidebar_to_settings)
        nav_layout.addWidget(self.settings_button)

        self.about_button = QPushButton(f"关于 · v{__version__}")
        self.about_button.setObjectName("aboutButton")
        self.about_button.setCheckable(True)
        self.about_button.setToolTip("打开关于页面")
        self.about_button.clicked.connect(self.show_about_page)
        nav_layout.addWidget(self.about_button)
        self.lightweight_button = QPushButton("切换到轻量模式")
        self.lightweight_button.setObjectName("modeButton")
        self.lightweight_button.clicked.connect(self.request_lightweight_mode)
        nav_layout.addWidget(self.lightweight_button)

        self.pages = QStackedWidget()
        self.lookup_page = LookupPage(lookup_service, cover_service)
        self.organizer_page = OrganizerPage(
            organizer_service
            or OrganizerService(
                lookup_service,
                scanner=FolderScanner(),
                planner=RenamePlanner(),
            ),
            execution_service=rename_executor,
            undo_service=undo_service,
            drop_input_service=DropInputService(),
        )
        self.settings_page = SettingsPage(
            self._settings_service,
            explorer_integration_service=explorer_integration_service,
        )
        self.rename_history_page = RenameHistoryPage(rename_history_service)
        self.about_page = AboutPage(update_check_service=update_check_service)
        self._settings_service.subscribe(self._settings_saved)
        # Route identities point directly to the one long-lived page instance.
        # Stack order and visible navigation order are intentionally independent.
        self._pages_by_identity: dict[MainPage, QWidget] = {
            MainPage.ORGANIZER: self.organizer_page,
            MainPage.LOOKUP: self.lookup_page,
            MainPage.HISTORY: self.rename_history_page,
            MainPage.SETTINGS: self.settings_page,
            MainPage.ABOUT: self.about_page,
        }
        for page in self._pages_by_identity.values():
            self.pages.addWidget(page)

        for page_identity, label in (
            (MainPage.ORGANIZER, "整理"),
            (MainPage.LOOKUP, "查询"),
            (MainPage.HISTORY, "重命名历史"),
        ):
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, page_identity.value)
            self.navigation_list.addItem(item)

        self._current_page: MainPage | None = None
        self.navigation_list.currentItemChanged.connect(self._navigation_item_changed)
        self.organizer_page.view_transaction_requested.connect(self.show_transaction_history)
        self.navigate_to(MainPage.ORGANIZER)
        shell.addWidget(self.navigation)
        shell.addWidget(self.pages, 1)
        self.setCentralWidget(central)
        self.setStyleSheet(light_stylesheet())
        # Polish child controls before measuring the legacy page's minimum.
        # Create/recalculate the HWND before setting the requested client size,
        # so Qt does not add the old native title/frame margins on first show.
        self.ensurePolished()
        if self._native_chrome is not None:
            self._native_chrome.attach()
        self._update_minimum_size_from_layout()
        self.resize(
            max(1160, self.minimumWidth()),
            max(800, self.minimumHeight()),
        )
        if self._runtime_signals is not None:
            self._runtime_signals.mutation_history_changed.connect(
                self.organizer_page.mark_filesystem_changed
            )
            self._runtime_signals.mutation_history_changed.connect(
                self.rename_history_page.refresh_after_mutation
            )

    @property
    def current_page(self) -> MainPage:
        """Return the current stable page identity."""
        assert self._current_page is not None
        return self._current_page

    @Slot(object, object)
    def _navigation_item_changed(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        if current is not None:
            self.navigate_to(MainPage(current.data(Qt.ItemDataRole.UserRole)))

    def navigate_to(self, page: MainPage) -> None:
        """Select a public destination by stable identity, preserving its instance."""
        previous_page = self._current_page
        page_widget = self._pages_by_identity[page]
        self._current_page = page
        self.pages.setCurrentWidget(page_widget)

        previous_block_state = self.navigation_list.blockSignals(True)
        try:
            matching_item = next(
                (
                    self.navigation_list.item(index)
                    for index in range(self.navigation_list.count())
                    if self.navigation_list.item(index).data(Qt.ItemDataRole.UserRole)
                    == page.value
                ),
                None,
            )
            if matching_item is None:
                self.navigation_list.setCurrentRow(-1)
                self.navigation_list.clearSelection()
            else:
                self.navigation_list.setCurrentItem(matching_item)
        finally:
            self.navigation_list.blockSignals(previous_block_state)

        self.settings_button.setChecked(page is MainPage.SETTINGS)
        self.about_button.setChecked(page is MainPage.ABOUT)
        if previous_page is not page:
            self._navigation_changed(page)

    def _navigation_changed(self, page: MainPage) -> None:
        if page is MainPage.HISTORY:
            self.rename_history_page.refresh_history()

    @Slot(str)
    def show_transaction_history(self, transaction_id: str) -> None:
        """Open the history page on a journal transaction selected by Organizer."""
        already_on_history = self.current_page is MainPage.HISTORY
        self.navigate_to(MainPage.HISTORY)
        self.rename_history_page.open_transaction(
            transaction_id,
            refresh=already_on_history,
        )

    @Slot()
    def refresh_mutation_state(self) -> None:
        """Refresh journal-backed mutation controls from the shared service."""
        self.organizer_page.refresh_mutation_state()

    def showEvent(self, event: QShowEvent) -> None:
        """Re-read mutation state whenever a hidden full-mode window is activated."""
        super().showEvent(event)
        self.refresh_mutation_state()
        self._update_minimum_size_from_layout()
        if self._native_chrome is not None:
            self._native_chrome.attach()
            # Child show/layout events can finalize legacy page hints after
            # this parent's showEvent. Re-measure once they have been delivered.
            QTimer.singleShot(0, self._update_minimum_size_from_layout)

    @Slot()
    def _toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange and hasattr(self, "title_bar"):
            self.title_bar.set_maximized(self.isMaximized())

    def nativeEvent(self, event_type, message) -> tuple[bool, int]:
        chrome = self._native_chrome
        if chrome is not None and event_type == b"windows_generic_MSG":
            handled, result = chrome.handle(int(message))
            if handled:
                return handled, result
        return False, 0

    def _update_minimum_size_from_layout(self) -> None:
        """Honor the assembled shell and legacy Organizer page size hints."""
        hint = self.minimumSizeHint()
        organizer_hint = self.organizer_page.minimumSizeHint()
        self.setMinimumSize(
            max(960, hint.width()),
            max(640, hint.height(), organizer_hint.height()),
        )

    @Slot()
    def request_lightweight_mode(self) -> None:
        """Request a runtime window switch without changing startup preference."""
        if self.is_busy():
            QMessageBox.information(self, "任务进行中", "请等待当前任务结束后再切换模式。")
            return
        self.lightweight_requested.emit()

    def show_settings_page(self) -> None:
        """Select the shared Settings page for the lightweight settings action."""
        self.settings_page.refresh_explorer_registration()
        self.navigate_to(MainPage.SETTINGS)

    @Slot()
    def _navigate_from_sidebar_to_settings(self) -> None:
        """Route ordinary footer navigation without refreshing external state."""
        self.navigate_to(MainPage.SETTINGS)

    @Slot()
    def show_about_page(self) -> None:
        """Route the footer About action through the shared page identity map."""
        self.navigate_to(MainPage.ABOUT)

    def is_busy(self) -> bool:
        """Return whether any full-mode background operation is active."""
        return (
            self.lookup_page.is_busy()
            or self.organizer_page.is_busy()
            or self.about_page.is_busy()
        )

    def set_close_handler(self, handler: Callable[[object, object], None]) -> None:
        """Install the application lifecycle's explicit close boundary."""
        self._close_handler = handler

    @Slot(object)
    def _settings_saved(self, settings: object) -> None:
        """Apply validated values to live services and invalidate old plans."""
        saved_settings = (
            settings
            if isinstance(settings, AppSettings)
            else AppSettings.model_validate(settings)
        )
        previous_settings = self._settings_snapshot
        self.organizer_page.apply_settings(saved_settings)
        self._settings_snapshot = saved_settings
        if operation_settings_key(previous_settings) != operation_settings_key(saved_settings):
            self.organizer_page.invalidate_preview()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Avoid destroying a running QThread during a bounded network request."""
        if self._close_handler is not None:
            self._close_handler(self, event)
            return
        if self.is_busy():
            event.ignore()
            QMessageBox.information(self, "任务进行中", "请等待当前任务结束后再退出。")
            return
        event.accept()
