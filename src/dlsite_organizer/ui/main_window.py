"""Main window and lightweight navigation shell."""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QCloseEvent, QShowEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
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
from dlsite_organizer.app.runtime import RuntimeSignals
from dlsite_organizer.app.settings import AppSettings, SettingsService
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.drop_input import DropInputService
from dlsite_organizer.services.explorer_integration import ExplorerIntegrationService
from dlsite_organizer.services.folder_scanner import FolderScanner
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.rename_planner import RenamePlanner
from dlsite_organizer.services.undo_service import UndoService
from dlsite_organizer.ui.pages.about_page import AboutPage
from dlsite_organizer.ui.pages.lookup_page import LookupPage
from dlsite_organizer.ui.pages.organizer_page import OrganizerPage
from dlsite_organizer.ui.pages.settings_page import SettingsPage


class MainPage(StrEnum):
    """Stable identities for the three main pages and the About destination."""

    ORGANIZER = "organizer"
    LOOKUP = "lookup"
    SETTINGS = "settings"
    ABOUT = "about"


class MainWindow(QMainWindow):
    """Host the four public pages of the full application."""

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
    ) -> None:
        super().__init__()
        self._lookup_service = lookup_service
        self._settings_service = settings_service or SettingsService(AppSettings())
        self._quick_rename_service = quick_rename_service
        self._runtime_signals = runtime_signals
        # Kept in the constructor for callers from the research-era shell.
        # Candidate services remain backend capabilities but are intentionally
        # not mounted in the public application navigation.
        self._candidate_review_queue_service = candidate_review_queue_service
        self.setWindowTitle("DLsite Organizer")
        self.resize(1080, 760)
        self.setMinimumSize(860, 640)

        central = QWidget()
        shell = QHBoxLayout(central)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        navigation = QWidget()
        navigation.setObjectName("navigation")
        navigation.setFixedWidth(176)
        nav_layout = QVBoxLayout(navigation)
        nav_layout.setContentsMargins(18, 24, 18, 20)
        nav_layout.setSpacing(16)
        brand = QLabel("DLsite\nOrganizer")
        brand.setObjectName("brand")
        nav_layout.addWidget(brand)
        self.navigation_list = QListWidget()
        self.navigation_list.setObjectName("navigationList")
        self.navigation_list.setSpacing(4)
        nav_layout.addWidget(self.navigation_list, 1)
        self.about_button = QPushButton(f"关于 · v{__version__}")
        self.about_button.setObjectName("aboutButton")
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
        self.about_page = AboutPage()
        self._settings_service.subscribe(self._settings_saved)
        page_definitions = (
            (MainPage.ORGANIZER, "整理", self.organizer_page),
            (MainPage.LOOKUP, "查询", self.lookup_page),
            (MainPage.SETTINGS, "设置", self.settings_page),
            (MainPage.ABOUT, "关于", self.about_page),
        )
        self.page_indices: dict[str, int] = {}
        for index, (page_key, label, page) in enumerate(page_definitions):
            self.page_indices[page_key.value] = index
            if page_key is not MainPage.ABOUT:
                self.navigation_list.addItem(QListWidgetItem(label))
            self.pages.addWidget(page)

        self.navigation_list.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation_list.setCurrentRow(self.page_indices[MainPage.ORGANIZER.value])
        shell.addWidget(navigation)
        shell.addWidget(self.pages, 1)
        self.setCentralWidget(central)
        self.setStyleSheet(_STYLE)
        if self._runtime_signals is not None:
            self._runtime_signals.mutation_history_changed.connect(
                self.refresh_mutation_state
            )

    @Slot()
    def refresh_mutation_state(self) -> None:
        """Refresh journal-backed mutation controls from the shared service."""
        self.organizer_page.refresh_mutation_state()

    def showEvent(self, event: QShowEvent) -> None:
        """Re-read mutation state whenever a hidden full-mode window is activated."""
        super().showEvent(event)
        self.refresh_mutation_state()

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
        self.navigation_list.setCurrentRow(self.page_indices[MainPage.SETTINGS.value])

    @Slot()
    def show_about_page(self) -> None:
        """Show About from the single footer entry without a magic nav index."""
        self.navigation_list.blockSignals(True)
        try:
            self.navigation_list.clearSelection()
            self.navigation_list.setCurrentRow(-1)
        finally:
            self.navigation_list.blockSignals(False)
        self.pages.setCurrentIndex(self.page_indices[MainPage.ABOUT.value])

    def is_busy(self) -> bool:
        """Return whether any full-mode background operation is active."""
        return self.lookup_page.is_busy() or self.organizer_page.is_busy()

    @Slot(object)
    def _settings_saved(self, settings: object) -> None:
        """Apply validated values to live services and invalidate old plans."""
        self._lookup_service.apply_settings(settings)
        self.organizer_page.apply_settings(settings)
        self.organizer_page.invalidate_preview()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Avoid destroying a running QThread during a bounded network request."""
        if self.lookup_page.is_busy() or self.organizer_page.is_busy():
            event.ignore()
            QMessageBox.information(self, "任务进行中", "请等待当前任务结束后再退出。")
            return
        event.accept()


_STYLE = """
QMainWindow, QStackedWidget { background: #f5f7fb; }
QWidget { color: #1e293b; font-family: "Segoe UI", "Microsoft YaHei UI"; font-size: 14px; }
#navigation { background: #172033; }
#brand { color: white; font-size: 21px; font-weight: 700; line-height: 1.2; }
#navigationList { background: transparent; border: 0; color: #c9d2e3; outline: 0; }
#navigationList::item { border-radius: 7px; padding: 11px 12px; }
#navigationList::item:selected { background: #2e3c55; color: white; font-weight: 600; }
#navigationList::item:hover:!selected { background: #222e43; }
#aboutButton {
  color: #c9d2e3; background: transparent; border: 0;
  padding: 8px 12px; text-align: left;
}
#aboutButton:hover { color: white; background: #222e43; }
#pageTitle { font-size: 26px; font-weight: 700; color: #111827; }
#pageDescription { color: #64748b; }
#resultCard { background: white; border: 1px solid #e2e8f0; border-radius: 10px; }
#relationCard { background: white; border: 1px solid #e2e8f0; border-radius: 10px; }
#coverPlaceholder {
  background: #eef2f7;
  border: 1px solid #dbe2ea;
  border-radius: 7px;
  color: #94a3b8;
}
#sectionLabel { font-weight: 600; color: #334155; }
QLineEdit { background: white; border: 1px solid #cbd5e1; border-radius: 7px; padding: 7px 10px; }
QLineEdit:focus { border: 1px solid #4f7cff; }
QLineEdit:read-only { background: #f8fafc; }
QPushButton { background: white; border: 1px solid #cbd5e1; border-radius: 7px; padding: 7px 14px; }
QPushButton:hover { background: #f1f5f9; }
QPushButton:disabled { color: #94a3b8; background: #f1f5f9; }
#primaryButton { background: #3867e8; color: white; border: 0; font-weight: 600; }
#primaryButton:hover { background: #2f5bd0; }
#primaryButton:disabled { background: #94a9df; color: #eef2ff; }
#statusLabel { color: #64748b; min-height: 22px; }
#statusLabel[state="loading"] { color: #315fc9; }
#statusLabel[state="success"] { color: #16805b; }
#statusLabel[state="error"] { color: #c53b47; }
#dropZone { background: #ffffff; border: 2px dashed #a9b8d0; border-radius: 10px; }
#dropZone[dragActive="true"] { background: #edf3ff; border-color: #3867e8; }
#dropZoneTitle { color: #23499d; font-size: 16px; font-weight: 600; }
#dropZoneDescription { color: #64748b; }
#modeButton { color: #c9d2e3; background: #222e43; border-color: #3b4a64; }
#modeButton:hover { background: #2e3c55; color: white; }
"""
