"""Main window and lightweight navigation shell."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QCloseEvent, QHideEvent, QShowEvent
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from dlsite_organizer import __version__
from dlsite_organizer.app.branding import load_application_icon
from dlsite_organizer.app.quick_action_controller import QuickActionFinished
from dlsite_organizer.app.runtime import RuntimeSignals
from dlsite_organizer.app.settings import AppSettings, SettingsService
from dlsite_organizer.domain.rename_execution import TransactionStatus
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.drop_input import DropInputService
from dlsite_organizer.services.explorer_integration import ExplorerIntegrationService
from dlsite_organizer.services.folder_scanner import FolderScanner
from dlsite_organizer.services.lookup import LookupService, operation_settings_key
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.services.quick_rename import QuickRenameResult, QuickRenameStatus
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
from dlsite_organizer.ui.theme.icons import navigation_icon
from dlsite_organizer.ui.theme.palette import use_custom_focus_border
from dlsite_organizer.ui.theme.tokens import Geometry
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


@dataclass(frozen=True)
class _QuickActionFailure:
    request_id: int
    summary: str
    details: str


def _quick_action_failure(value: QuickActionFinished) -> _QuickActionFailure | None:
    """Keep result facts and safe diagnostics, without retaining metadata payloads."""
    result = value.result
    lines: list[str] = []
    if isinstance(result, QuickRenameResult):
        if result.status in {QuickRenameStatus.SUCCESS, QuickRenameStatus.NO_CHANGE}:
            return None
        execution = result.execution
        if result.status is QuickRenameStatus.RECOVERY_REQUIRED or (
            execution is not None and execution.status is TransactionStatus.RECOVERY_REQUIRED
        ):
            outcome = "需要恢复，请先处理该事务"
        elif result.mutated or (
            execution is not None and execution.status is TransactionStatus.PARTIAL
        ):
            outcome = "部分操作已完成"
        elif execution is None:
            outcome = "重命名失败，未修改任何文件"
        else:
            outcome = "重命名执行失败"
        reason = result.error or (execution.error if execution else None)
        summary = f"{outcome}：{reason}" if reason else outcome
        lines.append(f"结果：{result.status.value}\n{summary}")
        if result.preview is not None:
            lines.extend(
                f"{lookup.work_code}：{lookup.error}"
                for lookup in result.preview.lookups if lookup.error
            )
            lines.extend(
                f"{plan.source_path}：{plan.error}" for plan in result.plans if plan.error
            )
        if execution is not None:
            lines.append(f"执行状态：{execution.status.value}；已完成 {execution.success_count} 项")
            if execution.transaction_id:
                lines.append(f"事务：{execution.transaction_id}")
            if execution.error and execution.error != reason:
                lines.append(execution.error)
            lines.extend(
                f"{operation.source_path} → {operation.target_path}："
                f"{operation.status.value}" + (f"；{operation.error}" if operation.error else "")
                for operation in execution.operations
            )
            if execution.transaction is not None and execution.transaction.recovery_error:
                lines.append(execution.transaction.recovery_error)
    else:
        summary = "Quick Rename 异常结束，无法确认执行结果；请查看详情"
        lines.append(summary)
    if value.error:
        lines.append(value.error)
    identity = f"Quick Rename #{value.request.request_id}"
    paths = "\n".join(str(path) for path in value.request.paths)
    return _QuickActionFailure(
        value.request.request_id, f"{identity} · {summary}",
        f"{identity}\n{paths}\n\n" + "\n\n".join(lines),
    )


class MainWindow(QMainWindow):
    """Host the public pages of the full application."""

    lightweight_requested = Signal()
    QUICK_FAILURE_LIMIT = 10

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
        self.navigation.setFixedWidth(Geometry.SIDEBAR_WIDTH)
        nav_layout = QVBoxLayout(self.navigation)
        nav_layout.setContentsMargins(16, 28, 16, 16)
        nav_layout.setSpacing(0)

        self.navigation_list = QListWidget()
        self.navigation_list.setObjectName("navigationList")
        self.navigation_list.setSpacing(4)
        self.navigation_list.setIconSize(QSize(18, 18))
        self.navigation_list.setItemDelegate(NavigationItemDelegate(self.navigation_list))
        nav_layout.addWidget(self.navigation_list, 1)

        self.settings_button = QPushButton("设置")
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.setCheckable(True)
        self.settings_button.setIcon(navigation_icon("settings"))
        self.settings_button.setIconSize(QSize(18, 18))
        self.settings_button.setAccessibleName("设置")
        self.settings_button.setToolTip("打开设置页面")
        self.settings_button.clicked.connect(self._navigate_from_sidebar_to_settings)

        self.about_button = QPushButton(f"关于 · v{__version__}")
        self.about_button.setObjectName("aboutButton")
        self.about_button.setCheckable(True)
        self.about_button.setIcon(navigation_icon("about"))
        self.about_button.setIconSize(QSize(18, 18))
        self.about_button.setAccessibleName(f"关于，版本 {__version__}")
        self.about_button.setToolTip("打开关于页面")
        self.about_button.clicked.connect(self.show_about_page)
        footer_group = QVBoxLayout()
        footer_group.setContentsMargins(0, 0, 0, 0)
        footer_group.setSpacing(4)
        footer_group.addWidget(self.settings_button)
        footer_group.addWidget(self.about_button)
        nav_layout.addLayout(footer_group)

        self.lightweight_button = QPushButton("切换到轻量模式")
        self.lightweight_button.setObjectName("modeButton")
        nav_layout.addSpacing(12)
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

        for page_identity, label, icon_name in (
            (MainPage.ORGANIZER, "整理", "organizer"),
            (MainPage.LOOKUP, "查询", "lookup"),
            (MainPage.HISTORY, "重命名历史", "history"),
        ):
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, page_identity.value)
            item.setData(Qt.ItemDataRole.AccessibleTextRole, label)
            item.setIcon(navigation_icon(icon_name))
            self.navigation_list.addItem(item)

        self._current_page: MainPage | None = None
        self.navigation_list.currentItemChanged.connect(self._navigation_item_changed)
        self.organizer_page.view_transaction_requested.connect(self.show_transaction_history)
        self.navigate_to(MainPage.ORGANIZER)
        shell.addWidget(self.navigation)
        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(0)
        content.addWidget(self.pages, 1)
        self.quick_failure_panel = QWidget()
        feedback = QVBoxLayout(self.quick_failure_panel)
        feedback.setContentsMargins(16, 4, 16, 8)
        summary_row = QHBoxLayout()
        self.quick_failure_summary = QLabel()
        self.quick_failure_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.quick_failure_summary.setObjectName("statusLabel")
        self.quick_failure_summary.setProperty("state", "error")
        self.quick_failure_summary.setAccessibleName("Quick Rename 失败状态")
        self.quick_failure_summary.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed,
        )
        summary_row.addWidget(self.quick_failure_summary, 1)
        self.quick_failure_details_button = QPushButton("详情（最近 10 次失败）")
        self.quick_failure_details_button.clicked.connect(self._show_quick_failure_details)
        self.quick_failure_details_button.setAccessibleName("查看 Quick Rename 失败详情")
        summary_row.addWidget(self.quick_failure_details_button)
        feedback.addLayout(summary_row)

        self.quick_failure_dialog = QDialog(self, Qt.WindowType.Window)
        self.quick_failure_dialog.setWindowTitle("Quick Rename 失败详情")
        self.quick_failure_dialog.setModal(False)
        self.quick_failure_dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        details_layout = QVBoxLayout(self.quick_failure_dialog)
        details_layout.setContentsMargins(16, 12, 16, 16)
        self.quick_failure_selector = QComboBox()
        self.quick_failure_selector.setAccessibleName("最近 10 次 Quick Rename 失败请求")
        self.quick_failure_selector.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon,
        )
        self.quick_failure_selector.setMinimumContentsLength(16)
        self.quick_failure_dialog_summary = QLabel()
        self.quick_failure_dialog_summary.setTextFormat(Qt.TextFormat.PlainText)
        self.quick_failure_dialog_summary.setObjectName("statusLabel")
        self.quick_failure_dialog_summary.setProperty("state", "error")
        self.quick_failure_dialog_summary.setAccessibleName("所选 Quick Rename 失败摘要")
        self.quick_failure_dialog_summary.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed,
        )
        self.quick_failure_details = QPlainTextEdit()
        self.quick_failure_details.setReadOnly(True)
        self.quick_failure_details.setTabChangesFocus(True)
        self.quick_failure_details.setAccessibleName("Quick Rename 完整失败详情")
        details_layout.addWidget(self.quick_failure_selector)
        details_layout.addWidget(self.quick_failure_dialog_summary)
        details_layout.addWidget(self.quick_failure_details, 1)
        self.quick_failure_dialog.resize(760, 520)
        self.quick_failure_dialog.hide()
        self.quick_failure_panel.hide()
        self.quick_failure_selector.currentIndexChanged.connect(self._select_quick_failure)
        content.addWidget(self.quick_failure_panel)
        shell.addLayout(content, 1)
        self.setCentralWidget(central)
        # Reach shell feedback before entering page tables that use Tab for cells.
        self.setTabOrder(self.lightweight_button, self.quick_failure_details_button)
        self.setStyleSheet(light_stylesheet())
        # These controls retain the shell's focus border (or an explicit local
        # equivalent). Keep this opt-in list explicit: notably, Organizer's
        # path-copy actions override that border and need native focus painting.
        for button in (
            self.settings_button,
            self.about_button,
            self.lightweight_button,
            self.quick_failure_details_button,
            self.settings_page.save_button,
            self.settings_page.remove_explorer_button,
            self.lookup_page.query_button,
            self.lookup_page.refresh_button,
            self.rename_history_page.newer_button,
            self.rename_history_page.older_button,
            self.rename_history_page.refresh_button,
            self.rename_history_page.observe_button,
            self.rename_history_page.copy_root_path_button,
            self.rename_history_page.copy_source_path_button,
            self.rename_history_page.copy_target_path_button,
            self.rename_history_page.copy_transaction_id_button,
            self.organizer_page.browse_button,
            self.organizer_page.scan_button,
            self.organizer_page.cancel_button,
            self.organizer_page.bulk_menu_button,
            self.organizer_page.retry_refresh_button,
            self.organizer_page.execute_button,
        ):
            use_custom_focus_border(button)
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

    def present_quick_action_failure(self, value: QuickActionFinished) -> None:
        """Retain at most ten failures across all pages without changing focus or mode."""
        failure = _quick_action_failure(value)
        if failure is None:
            return
        selector = self.quick_failure_selector
        selected_record = selector.currentData()
        inspected_id = (
            selected_record.request_id
            if isinstance(selected_record, _QuickActionFailure) else None
        )
        selector.blockSignals(True)
        try:
            if selector.count() == self.QUICK_FAILURE_LIMIT:
                selector.removeItem(0)
            selector.addItem(f"Quick Rename #{failure.request_id}", failure)
            if inspected_id is None:
                selector.setCurrentIndex(selector.count() - 1)
            else:
                retained_index = next(
                    (
                        index for index in range(selector.count())
                        if isinstance(selector.itemData(index), _QuickActionFailure)
                        and selector.itemData(index).request_id == inspected_id
                    ), -1,
                )
                selector.setCurrentIndex(
                    retained_index if retained_index >= 0 else 0,
                )
        finally:
            selector.blockSignals(False)
        if not self.quick_failure_dialog.isVisible():
            selector.setCurrentIndex(selector.count() - 1)
        self._select_quick_failure(selector.currentIndex())
        self.quick_failure_summary.setText(
            self._failure_summary_preview(failure.summary),
        )
        self.quick_failure_summary.setToolTip(failure.summary)
        self.quick_failure_summary.setAccessibleDescription(failure.summary)
        self.quick_failure_panel.show()

    @staticmethod
    def _failure_summary_preview(summary: str) -> str:
        preview = " ".join(summary.split())
        return preview[:120] + ("…" if len(preview) > 120 else "")

    @Slot()
    def _show_quick_failure_details(self) -> None:
        if self.quick_failure_selector.count() == 0:
            return
        if not self.quick_failure_dialog.isVisible():
            screen = QApplication.screenAt(self.frameGeometry().center())
            if screen is None:
                screen = QApplication.primaryScreen()
            if screen is not None:
                available = screen.availableGeometry()
                geometry = self._quick_failure_viewer_geometry(available)
                self.quick_failure_dialog.setMaximumSize(available.size())
                self.quick_failure_dialog.resize(geometry.size())
                self.quick_failure_dialog.move(geometry.topLeft())
        self.quick_failure_dialog.show()
        self.quick_failure_dialog.raise_()
        self.quick_failure_dialog.activateWindow()

    @staticmethod
    def _quick_failure_viewer_geometry(available: QRect) -> QRect:
        """Center a comfortably sized viewer within the usable screen rectangle."""
        width = min(760, available.width())
        height = min(520, available.height())
        x = available.x() + max(0, (available.width() - width) // 2)
        y = available.y() + max(0, (available.height() - height) // 2)
        return QRect(x, y, width, height)

    @Slot(int)
    def _select_quick_failure(self, index: int) -> None:
        failure = self.quick_failure_selector.itemData(index)
        if not isinstance(failure, _QuickActionFailure):
            return
        self.quick_failure_dialog_summary.setText(failure.summary)
        self.quick_failure_dialog_summary.setToolTip(failure.summary)
        self.quick_failure_dialog_summary.setAccessibleDescription(failure.summary)
        self.quick_failure_details.setPlainText(failure.details)

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

    def hideEvent(self, event: QHideEvent) -> None:
        """Keep the owned details viewer hidden with Full Mode."""
        self.quick_failure_dialog.hide()
        super().hideEvent(event)

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
