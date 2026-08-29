"""Main window and lightweight navigation shell."""

from __future__ import annotations

from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.folder_scanner import FolderScanner
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.rename_planner import RenamePlanner
from dlsite_organizer.services.undo_service import UndoService
from dlsite_organizer.ui.pages.lookup_page import LookupPage
from dlsite_organizer.ui.pages.organizer_page import OrganizerPage


class MainWindow(QMainWindow):
    """Host the organizer preview, lookup page, and honest future placeholders."""

    def __init__(
        self,
        lookup_service: LookupService,
        cover_service: CoverService,
        organizer_service: OrganizerService | None = None,
        rename_executor: RenameExecutor | None = None,
        undo_service: UndoService | None = None,
    ) -> None:
        super().__init__()
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
        version = QLabel("v0.4.0 · 安全重命名")
        version.setObjectName("versionLabel")
        nav_layout.addWidget(version)

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
        )
        page_definitions = [
            ("整理", self.organizer_page),
            ("查询", self.lookup_page),
            (
                "关系",
                _placeholder(
                    "关系",
                    "请在“查询”页输入 RJcode，确认的翻译关系会显示在作品信息下方。",
                ),
            ),
            ("设置", _placeholder("设置", "图形化设置页面尚未实现；当前可使用 TOML 配置。")),
        ]
        for label, page in page_definitions:
            self.navigation_list.addItem(QListWidgetItem(label))
            self.pages.addWidget(page)

        self.navigation_list.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation_list.setCurrentRow(1)
        shell.addWidget(navigation)
        shell.addWidget(self.pages, 1)
        self.setCentralWidget(central)
        self.setStyleSheet(_STYLE)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Avoid destroying a running QThread during a bounded network request."""
        if self.lookup_page.is_busy() or self.organizer_page.is_busy():
            event.ignore()
            QMessageBox.information(self, "任务进行中", "请等待当前任务结束后再退出。")
            return
        event.accept()


def _placeholder(title: str, message: str) -> QWidget:
    page = QWidget()
    layout = QVBoxLayout(page)
    layout.setContentsMargins(40, 36, 40, 36)
    heading = QLabel(title)
    heading.setObjectName("pageTitle")
    description = QLabel(message)
    description.setObjectName("pageDescription")
    layout.addWidget(heading)
    layout.addWidget(description)
    layout.addStretch(1)
    return page


_STYLE = """
QMainWindow, QStackedWidget { background: #f5f7fb; }
QWidget { color: #1e293b; font-family: "Segoe UI", "Microsoft YaHei UI"; font-size: 14px; }
#navigation { background: #172033; }
#brand { color: white; font-size: 21px; font-weight: 700; line-height: 1.2; }
#versionLabel { color: #8190aa; font-size: 12px; }
#navigationList { background: transparent; border: 0; color: #c9d2e3; outline: 0; }
#navigationList::item { border-radius: 7px; padding: 11px 12px; }
#navigationList::item:selected { background: #2e3c55; color: white; font-weight: 600; }
#navigationList::item:hover:!selected { background: #222e43; }
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
"""
