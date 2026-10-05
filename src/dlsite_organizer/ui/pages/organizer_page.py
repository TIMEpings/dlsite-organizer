"""Organizer page: review RenamePlans and execute confirmed safe renames."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import cast

from PySide6.QtCore import (
    QEvent,
    QItemSelectionModel,
    QModelIndex,
    QPoint,
    QRect,
    QSize,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import QAction, QKeySequence, QResizeEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from dlsite_organizer.domain.organizer import (
    RenamePlan,
    RenamePlanStatus,
)
from dlsite_organizer.domain.rename_execution import RenameExecutionResult, UndoResult, UndoStatus
from dlsite_organizer.services.drop_input import (
    DropInputError,
    DropInputService,
    normalized_path_identity,
)
from dlsite_organizer.services.organizer import OrganizerPreview, OrganizerService
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.undo_service import UndoService
from dlsite_organizer.ui.delegates.organizer_delegates import (
    OrganizerCheckDelegate,
    OrganizerStatusDelegate,
    OrganizerTextDelegate,
    OrganizerWorkDelegate,
)
from dlsite_organizer.ui.models.organizer_preview_model import (
    OrganizerPreviewModel,
    plan_diagnostics,
)
from dlsite_organizer.ui.state.organizer_state import (
    BusyOperation,
    OrganizerState,
    ScanMode,
)
from dlsite_organizer.ui.state.organizer_state import (
    PreviewStaleReason as PreviewStaleReason,
)
from dlsite_organizer.ui.theme.icons import execution_check_icon
from dlsite_organizer.ui.theme.stylesheet import set_button_role
from dlsite_organizer.ui.theme.tokens import LIGHT_COLORS, ButtonRole
from dlsite_organizer.ui.widgets.compact_drop_area import CompactDirectoryDropArea
from dlsite_organizer.ui.widgets.drop_zone import local_directory_paths
from dlsite_organizer.ui.widgets.organizer_review import (
    OrganizerBulkMenuButton,
    OrganizerElidedLabel,
    OrganizerPathDisplay,
    OrganizerSummary,
)
from dlsite_organizer.ui.widgets.page_header import PageHeader
from dlsite_organizer.ui.widgets.status_banner import BannerState, StatusBanner
from dlsite_organizer.ui.workers.organizer_worker import OrganizerWorker, OrganizerWorkerResult
from dlsite_organizer.ui.workers.rename_worker import RenameActionWorker

logger = logging.getLogger(__name__)


class OrganizerPage(QWidget):
    """Present scan results and expose explicit rename and undo actions."""

    view_transaction_requested = Signal(str)

    def __init__(
        self,
        organizer_service: OrganizerService,
        parent: QWidget | None = None,
        *,
        execution_service: RenameExecutor | None = None,
        undo_service: UndoService | None = None,
        drop_input_service: DropInputService | None = None,
    ) -> None:
        super().__init__(parent)
        self._organizer_service = organizer_service
        self._execution_service = execution_service
        self._undo_service = undo_service
        self._drop_input_service = drop_input_service or DropInputService()
        self._thread: QThread | None = None
        self._worker: OrganizerWorker | None = None
        self._action_worker: RenameActionWorker | None = None
        self.state = OrganizerState(settings_generation=organizer_service.settings_generation)
        self.preview_model = OrganizerPreviewModel(self.state, self)
        self._next_scan_mode = ScanMode.REPLACE
        self._build_ui()
        self._install_workspace_drop_routing()

        self.browse_button.clicked.connect(self.choose_root)
        # QPushButton.clicked carries a checked: bool argument.  Keep that
        # signal payload away from start_scan(), whose optional argument is
        # reserved for selective dropped-folder scans.
        self.scan_button.clicked.connect(self._start_scan_from_button)
        self.cancel_button.clicked.connect(self.cancel_scan)
        self.execute_button.clicked.connect(self.execute_rename)
        self.undo_button.clicked.connect(self.undo_recent)
        self.view_transaction_button.clicked.connect(self._view_unresolved_transaction)
        self.root_input.textChanged.connect(self._input_root_changed)
        self.preview_model.checked_changed.connect(self._update_execute_button)
        self.table.selectionModel().selectionChanged.connect(self._table_selection_changed)
        self.table.selectionModel().currentChanged.connect(self._table_selection_changed)
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button.clicked.connect(self.clear_preview)
        self.detail_toggle.toggled.connect(self._detail_expanded_changed)
        self._refresh_recent_transaction()
        self._update_execute_button()
        self._update_preview_controls()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(8)

        self.page_header = PageHeader("整理", "扫描目录，检查名称，再执行重命名。")
        self.page_header.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        layout.addWidget(self.page_header)

        self.input_panel = CompactDirectoryDropArea(self)
        # Keep the established page attribute as a public drop-target hook for callers.
        self.drop_zone = self.input_panel
        self.drop_zone.paths_dropped.connect(self._handle_drop_paths)
        input_layout = QVBoxLayout(self.input_panel)
        input_layout.setContentsMargins(10, 9, 10, 8)
        input_layout.setSpacing(3)

        root_label = QLabel("下次扫描目录")
        self.root_label = root_label
        root_label.setObjectName("sectionLabel")
        root_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        root_label.setMinimumWidth(root_label.sizeHint().width() + 8)
        self.root_input = QLineEdit()
        root_label.setBuddy(self.root_input)
        self.root_input.setAccessibleName("下次扫描目录")
        self.root_input.setPlaceholderText("选择包含作品子目录的文件夹")
        self.root_input.setClearButtonEnabled(True)
        self.root_input.setMinimumWidth(0)
        self.root_input.setAcceptDrops(False)
        self.browse_button = QPushButton("选择…")
        self.browse_button.setAccessibleName("选择下次扫描目录")
        root_row = QHBoxLayout()
        root_row.setSpacing(7)
        root_row.addWidget(root_label)
        root_row.addWidget(self.root_input, 1)
        root_row.addWidget(self.browse_button)
        self.scan_button = QPushButton("扫描并预览")
        self.scan_button.setAccessibleName("扫描并预览下次扫描目录")
        set_button_role(self.scan_button, ButtonRole.SECONDARY)
        # Property selector specificity masked the existing global hover surface.
        secondary_style = f"""
            QPushButton[visualRole="secondary"]:enabled:hover {{
              background: {LIGHT_COLORS.surface_secondary};
            }}
            QPushButton[visualRole="secondary"]:enabled:pressed {{
              background: {LIGHT_COLORS.selection};
            }}
            QPushButton[visualRole="secondary"]:focus {{
              border-color: {LIGHT_COLORS.focus};
            }}
        """
        self.scan_button.setStyleSheet(secondary_style)
        self.cancel_button = QPushButton("取消扫描")
        set_button_role(self.cancel_button, ButtonRole.SECONDARY)
        self.cancel_button.setStyleSheet(secondary_style)
        self.cancel_button.setAccessibleName("取消扫描")
        self.cancel_button.setEnabled(False)
        self.cancel_button.hide()
        root_row.addWidget(self.scan_button)
        root_row.addWidget(self.cancel_button)
        input_layout.addLayout(root_row)

        self.input_hint_label = QLabel("拖入根目录或作品文件夹以追加预览")
        self.input_hint_label.setObjectName("pageDescription")
        self.input_hint_label.setAccessibleName("拖放说明")
        input_layout.addWidget(self.input_hint_label)
        layout.addWidget(self.input_panel)

        self.preview_context = QWidget(self)
        self.preview_context.setObjectName("organizerPreviewContext")
        context_layout = QVBoxLayout(self.preview_context)
        context_layout.setContentsMargins(0, 0, 0, 0)
        context_layout.setSpacing(2)
        root_context_row = QHBoxLayout()
        root_context_row.setContentsMargins(0, 0, 0, 0)
        root_context_row.setSpacing(7)
        current_root_label = QLabel("当前预览")
        current_root_label.setObjectName("sectionLabel")
        self.preview_root_display = OrganizerPathDisplay(copy_button=False)
        self.preview_root_display.setAccessibleName("当前预览目录")
        self.summary_label = OrganizerSummary()
        self.summary_label.setObjectName("organizerSummary")
        self.summary_label.setAccessibleName("预览项目统计")
        root_context_row.addWidget(current_root_label)
        root_context_row.addWidget(self.preview_root_display, 1)
        context_layout.addLayout(root_context_row)
        summary_row = QHBoxLayout()
        summary_row.setContentsMargins(0, 0, 0, 0)
        summary_row.addWidget(self.summary_label, 1)
        self.bulk_toolbar = QWidget(self.preview_context)
        bulk_layout = QHBoxLayout(self.bulk_toolbar)
        bulk_layout.setContentsMargins(0, 0, 0, 0)
        bulk_layout.setSpacing(2)
        self.bulk_menu_button = OrganizerBulkMenuButton(self.bulk_toolbar)
        self.bulk_menu_button.setFixedSize(30, 30)
        self.bulk_menu_button.setIcon(execution_check_icon("select"))
        self.bulk_menu_button.setIconSize(QSize(18, 18))
        self.bulk_menu_button.setToolTip("批量设置执行勾选")
        self.bulk_menu_button.setAccessibleName("批量设置执行勾选")
        self.bulk_menu_button.setAccessibleDescription("打开菜单，仅设置可执行项目的执行勾选。")
        self.bulk_menu_button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        set_button_role(self.bulk_menu_button, ButtonRole.QUIET)
        self.bulk_menu_button.setStyleSheet(f"""
            QPushButton {{ min-height: 0; padding: 0; }}
            QPushButton:focus {{ border: 1px solid {LIGHT_COLORS.focus}; }}
            QPushButton::menu-indicator {{ width: 5px; height: 5px; }}
        """)
        self.bulk_menu = QMenu(self.bulk_menu_button)
        self.bulk_actions: dict[str, QAction] = {}
        for action, label in (
            ("select", "全选所有可执行项"),
            ("invert", "反选所有可执行项"),
            ("clear", "清除所有执行勾选"),
        ):
            menu_action = self.bulk_menu.addAction(label)
            menu_action.triggered.connect(
                lambda _checked=False, action=action: self.preview_model.bulk_check(action)
            )
            self.bulk_actions[action] = menu_action
        self.bulk_menu_button.setMenu(self.bulk_menu)
        bulk_layout.addWidget(self.bulk_menu_button)
        summary_row.addWidget(self.bulk_toolbar)
        context_layout.addLayout(summary_row)
        self.input_root_difference_label = OrganizerElidedLabel()
        self.input_root_difference_label.setObjectName("organizerRootDifference")
        self.input_root_difference_label.hide()
        context_layout.addWidget(self.input_root_difference_label)
        layout.addWidget(self.preview_context)

        self.status_banner = StatusBanner(self)
        layout.addWidget(self.status_banner)
        self.retry_refresh_button = QPushButton("重新刷新")
        self.retry_refresh_button.setAccessibleName("重新刷新当前预览目录")
        self.retry_refresh_button.clicked.connect(self.retry_refresh)
        self.retry_refresh_button.hide()
        layout.addWidget(self.retry_refresh_button, 0, Qt.AlignmentFlag.AlignLeft)

        self.empty_state = QWidget(self)
        self.empty_state.setObjectName("organizerEmptyState")
        empty_layout = QVBoxLayout(self.empty_state)
        empty_layout.setContentsMargins(20, 28, 20, 28)
        empty_layout.setSpacing(8)
        empty_layout.addStretch(1)
        self.empty_title_label = QLabel("尚未生成预览")
        self.empty_title_label.setObjectName("sectionLabel")
        self.empty_description_label = QLabel(
            "选择上方目录并扫描，或拖入作品目录以生成预览。"
        )
        self.empty_description_label.setObjectName("pageDescription")
        self.empty_description_label.setWordWrap(True)
        empty_layout.addWidget(self.empty_title_label)
        empty_layout.addWidget(self.empty_description_label)
        empty_layout.addStretch(1)
        self.empty_title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_description_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty_state, 1)

        self.table = QTableView()
        self._column_layout_timer = QTimer(self)
        self._column_layout_timer.setSingleShot(True)
        self._column_layout_timer.timeout.connect(self._resize_table_columns)
        self.table.viewport().installEventFilter(self)
        self.table.setModel(self.preview_model)
        self.remove_action = QAction("从预览移除", self.table)
        self.remove_action.setShortcut(QKeySequence(Qt.Key.Key_Delete))
        self.remove_action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        self.remove_action.triggered.connect(self.remove_selected)
        self.table.addAction(self.remove_action)
        self.preview_menu = QMenu(self.table)
        self.preview_menu.addAction(self.remove_action)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_preview_menu)
        self.preview_model.modelReset.connect(self._schedule_column_layout)
        self.preview_model.rowsInserted.connect(self._schedule_column_layout)
        self.preview_model.rowsRemoved.connect(self._schedule_column_layout)
        # Keep a useful width hint while the empty state hides the table.
        self.table.setMinimumWidth(680)
        self.table.setSortingEnabled(False)
        self.table.setAcceptDrops(False)
        self.table.viewport().setAcceptDrops(False)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.table.setObjectName("organizerTable")
        self.table.setAccessibleName("重命名预览")
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        self.table.verticalHeader().setDefaultSectionSize(40)
        header = self.table.horizontalHeader()
        header.setMinimumSectionSize(36)
        header.setFixedHeight(32)
        header.setSectionsClickable(False)
        header.setSortIndicatorShown(False)
        for column in range(self.preview_model.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        self.table.setItemDelegateForColumn(0, OrganizerCheckDelegate(self.table))
        self.table.setItemDelegateForColumn(1, OrganizerStatusDelegate(self.table))
        self.table.setItemDelegateForColumn(2, OrganizerWorkDelegate(self.table))
        for column in (3, 4):
            self.table.setItemDelegateForColumn(column, OrganizerTextDelegate(self.table))
        self.table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.table.setMinimumHeight(150)
        self.table.hide()
        self.table.setStyleSheet(
            f"""
            QTableView#organizerTable {{
              background: {LIGHT_COLORS.surface};
              alternate-background-color: {LIGHT_COLORS.surface_secondary};
              border: 1px solid {LIGHT_COLORS.border};
              border-radius: 7px;
              selection-background-color: {LIGHT_COLORS.selection};
              selection-color: {LIGHT_COLORS.text_primary};
            }}
            QTableView#organizerTable::item {{ padding: 2px 3px; }}
            QHeaderView::section {{
              background: {LIGHT_COLORS.surface_secondary};
              color: {LIGHT_COLORS.text_secondary};
              border: 0;
              border-bottom: 1px solid {LIGHT_COLORS.border};
              padding: 5px 7px;
              font-weight: 600;
            }}
            """
        )
        layout.addWidget(self.table, 1)

        self.detail_panel = QFrame(self)
        self.detail_panel.setObjectName("organizerDetailPane")
        self.detail_panel.setStyleSheet(
            f"""
            QFrame#organizerDetailPane {{
              background: {LIGHT_COLORS.surface};
              border: 0;
              border-top: 1px solid {LIGHT_COLORS.border};
              border-radius: 0;
            }}
            """
        )
        detail_layout = QVBoxLayout(self.detail_panel)
        detail_layout.setContentsMargins(6, 3, 6, 4)
        detail_layout.setSpacing(1)
        detail_header = QHBoxLayout()
        detail_header.setContentsMargins(0, 0, 0, 0)
        detail_header.setSpacing(7)
        self.detail_toggle = QToolButton(self.detail_panel)
        self.detail_toggle.setObjectName("organizerDetailToggle")
        self.detail_toggle.setCheckable(True)
        self.detail_toggle.setChecked(True)
        self.detail_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.detail_toggle.setText("当前行详情")
        self.detail_toggle.setAccessibleName("展开或折叠当前行详情")
        self.detail_toggle.setAutoRaise(True)
        self.detail_toggle.setStyleSheet(
            f"""
            QToolButton#organizerDetailToggle {{
              background: transparent;
              border: 0;
              border-radius: 5px;
              color: {LIGHT_COLORS.text_secondary};
              padding: 3px 5px;
              font-weight: 600;
            }}
            QToolButton#organizerDetailToggle:hover {{
              background: {LIGHT_COLORS.surface_secondary};
              color: {LIGHT_COLORS.text_primary};
            }}
            QToolButton#organizerDetailToggle:focus {{
              border: 1px solid {LIGHT_COLORS.focus};
            }}
            """
        )
        self.detail_summary_label = OrganizerElidedLabel("尚未选择行")
        self.detail_summary_label.setObjectName("pageDescription")
        self.detail_summary_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        detail_header.addWidget(self.detail_toggle)
        detail_header.addWidget(self.detail_summary_label, 1)
        detail_layout.addLayout(detail_header)

        self.detail_content = QWidget(self.detail_panel)
        detail_content_layout = QVBoxLayout(self.detail_content)
        detail_content_layout.setContentsMargins(2, 0, 2, 0)
        detail_content_layout.setSpacing(1)
        self.detail_title_label = OrganizerElidedLabel()
        self.detail_title_label.setObjectName("sectionLabel")
        self.detail_metadata_label = OrganizerElidedLabel()
        self.detail_metadata_label.setObjectName("pageDescription")
        detail_content_layout.addWidget(self.detail_title_label)
        detail_content_layout.addWidget(self.detail_metadata_label)

        source_row = QHBoxLayout()
        source_row.setContentsMargins(0, 0, 0, 0)
        source_row.setSpacing(6)
        source_label = QLabel("来源路径")
        source_label.setObjectName("pageDescription")
        self.detail_source_path = OrganizerPathDisplay()
        source_label.setBuddy(self.detail_source_path.path_input)
        source_row.addWidget(source_label)
        source_row.addWidget(self.detail_source_path, 1)
        detail_content_layout.addLayout(source_row)

        target_row = QHBoxLayout()
        target_row.setContentsMargins(0, 0, 0, 0)
        target_row.setSpacing(6)
        target_label = QLabel("目标路径")
        target_label.setObjectName("pageDescription")
        self.detail_target_path = OrganizerPathDisplay()
        target_label.setBuddy(self.detail_target_path.path_input)
        target_row.addWidget(target_label)
        target_row.addWidget(self.detail_target_path, 1)
        detail_content_layout.addLayout(target_row)

        self.detail_diagnostics = QPlainTextEdit()
        self.detail_diagnostics.setObjectName("organizerDetailDiagnostics")
        self.detail_diagnostics.setReadOnly(True)
        self.detail_diagnostics.document().setDocumentMargin(0)
        self.detail_diagnostics.setFixedHeight(24)
        self.detail_diagnostics.setStyleSheet(
            f"""
            QPlainTextEdit#organizerDetailDiagnostics {{
              background: transparent;
              border: 0;
              color: {LIGHT_COLORS.text_secondary};
              min-height: 20px;
              padding: 1px 0;
            }}
            """
        )
        self.detail_diagnostics.setPlaceholderText("无警告或阻止原因")
        self.detail_diagnostics.setAccessibleName("当前行警告与错误")
        self.detail_diagnostics.setTabChangesFocus(True)
        detail_content_layout.addWidget(self.detail_diagnostics)
        detail_layout.addWidget(self.detail_content)
        self.detail_panel.hide()
        layout.addWidget(self.detail_panel)

        self.footer = QWidget()
        self.footer.setObjectName("organizerFooter")
        footer_layout = QVBoxLayout(self.footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(4)

        self.preview_actions = QWidget(self.footer)
        preview_action_layout = QVBoxLayout(self.preview_actions)
        preview_action_layout.setContentsMargins(0, 0, 0, 0)
        preview_action_layout.setSpacing(4)
        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        action_row.setSpacing(6)
        self.highlight_count_label = QLabel("高亮 0 项")
        self.highlight_count_label.setObjectName("pageDescription")
        self.remove_button = QPushButton("从预览移除（0）")
        self.remove_button.setAccessibleName("从预览移除高亮项目")
        self.remove_button.setEnabled(False)
        set_button_role(self.remove_button, ButtonRole.QUIET)
        self.clear_button = QPushButton("清空预览")
        self.clear_button.setAccessibleName("清空当前内存预览")
        self.clear_button.setToolTip("仅清空内存中的预览，不修改文件；保留下次扫描目录。")
        self.clear_button.setEnabled(False)
        set_button_role(self.clear_button, ButtonRole.QUIET)
        self.execute_button = QPushButton("执行重命名（0 项）")
        self.execute_button.setAccessibleName("执行已勾选的可执行重命名")
        self.execute_button.setDefault(False)
        self.execute_button.setAutoDefault(False)
        set_button_role(self.execute_button, ButtonRole.PRIMARY)
        self.execute_button.setEnabled(False)
        self.action_hint_label = OrganizerElidedLabel("请勾选要执行的可执行项目。")
        self.action_hint_label.setObjectName("pageDescription")
        self.highlight_count_label.hide()  # The Remove count already presents highlighted rows.
        action_row.addWidget(self.remove_button)
        action_row.addWidget(self.clear_button)
        action_row.addStretch(1)
        action_row.addWidget(self.execute_button)
        preview_action_layout.addWidget(self.action_hint_label)
        preview_action_layout.addLayout(action_row)
        footer_layout.addWidget(self.preview_actions)

        self.status_label = QLabel("请选择根目录开始扫描。")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        self.progress_label = QLabel()
        self.progress_label.setObjectName("organizerProgress")
        self.progress_label.setWordWrap(True)
        self.progress_label.hide()
        footer_layout.addWidget(self.progress_label)
        footer_layout.addWidget(self.status_label)

        self.undo_region = QWidget(self.footer)
        undo_layout = QVBoxLayout(self.undo_region)
        undo_layout.setContentsMargins(0, 0, 0, 0)
        undo_layout.setSpacing(4)
        recent_row = QHBoxLayout()
        recent_row.setContentsMargins(0, 0, 0, 0)
        recent_row.setSpacing(6)
        separator = QFrame()
        separator.setFixedHeight(1)
        separator.setStyleSheet(f"background: {LIGHT_COLORS.border}; border: 0;")
        undo_layout.addWidget(separator)
        self.recent_transaction_label = OrganizerElidedLabel("最近可撤销：无")
        self.recent_transaction_label.setObjectName("pageDescription")
        self.recent_transaction_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.undo_button = QPushButton("撤销最近一次")
        self.undo_button.setAccessibleName("撤销最近一次重命名")
        self.undo_button.setEnabled(False)
        set_button_role(self.undo_button, ButtonRole.QUIET)
        self.view_transaction_button = QPushButton("查看未解决事务")
        self.view_transaction_button.setAccessibleName("在重命名历史中查看未解决事务")
        self.view_transaction_button.setToolTip("在重命名历史中查看该事务的记录和恢复信息")
        set_button_role(self.view_transaction_button, ButtonRole.QUIET)
        self.view_transaction_button.hide()
        recent_row.addWidget(self.recent_transaction_label, 1)
        recent_row.addWidget(self.view_transaction_button)
        recent_row.addWidget(self.undo_button)
        undo_layout.addLayout(recent_row)
        footer_layout.addWidget(self.undo_region)
        layout.addWidget(self.footer)

        self._feedback_timer = QTimer(self)
        self._feedback_timer.setSingleShot(True)
        self._feedback_timer.setInterval(6500)
        self._feedback_timer.timeout.connect(self._clear_feedback)
        self._detail_user_touched = False
        self._compact_layout = False
        self._journal_unavailable = False
        self._undo_pending = False
        self._undo_successes_before_attempt: frozenset[int] = frozenset()
        self._journal_error_message = ""
        self._operation_message = ""
        self._resize_table_columns()
        self._update_preview_context()
        self._update_empty_state()
        self._update_detail_expanded(self.detail_toggle.isChecked())

    @Slot()
    def choose_root(self) -> None:
        """Choose a root directory; the dialog itself has no write operation."""
        selected = QFileDialog.getExistingDirectory(self, "选择作品根目录", self.root_input.text())
        if selected:
            self.root_input.setText(selected)

    def set_root_path(self, path: Path | str) -> None:
        """Set the root field for tests and callers that provide a known path."""
        self.root_input.setText(str(path))

    @Slot()
    def _start_scan_from_button(self) -> None:
        """Start an ordinary root scan from the button without its bool payload."""
        self.start_scan()

    @Slot()
    def start_scan(self, selected_paths: Sequence[Path | str] | None = None) -> None:
        """Start one background organizer run."""
        mode = self._next_scan_mode
        self._next_scan_mode = ScanMode.REPLACE
        self._start_scan(self.state.input_root.strip(), mode, selected_paths)

    def _start_scan(self, root: str, mode: ScanMode,
                    selected_paths: Sequence[Path | str] | None = None) -> None:
        if self._thread is not None:
            return
        if not root:
            self._set_feedback("请先选择下次扫描目录。", "error")
            return

        self.state.settings_generation = self._organizer_service.settings_generation
        self.state.begin_scan(mode)
        self._clear_feedback()
        self._set_operation_progress(
            f"重命名完成 {self.state.last_execution_result.success_count} 项，正在刷新预览…"
            if mode is ScanMode.RECONCILE and self.state.last_execution_result is not None
            else "正在扫描目录…")
        self._set_loading(True)

        thread: QThread | None = None
        worker: OrganizerWorker | None = None
        try:
            thread = QThread()
            thread.setObjectName("organizer-scan")
            worker = OrganizerWorker(
                self._organizer_service, root, selected_paths,
                request_id=self.state.request_id, preview_revision=self.state.preview_revision,
                reconcile=mode is ScanMode.RECONCILE,
            )
            worker.moveToThread(thread)
            thread.started.connect(worker.run)
            worker.progress.connect(self._show_progress)
            worker.result_ready.connect(self._show_preview)
            worker.request_failed.connect(self._show_scan_error)
            worker.finished.connect(thread.quit)
            worker.finished.connect(worker.deleteLater)
            thread.finished.connect(thread.deleteLater)
            thread.finished.connect(self._scan_finished)
            self._thread = thread
            self._worker = worker
            logger.debug(
                "organizer scan thread created name=%s id=%s root=%s",
                thread.objectName(),
                id(thread),
                root,
            )
            thread.start()
            logger.debug(
                "organizer scan thread started name=%s id=%s",
                thread.objectName(),
                id(thread),
            )
        except Exception:
            logger.exception("Failed to start organizer scan")
            if thread is not None:
                thread.quit()
                if thread.isRunning():
                    thread.wait()
                thread.deleteLater()
            self._thread = None
            self._worker = None
            self._show_error("无法启动扫描，请稍后重试。")
            self._set_operation_progress("")
            self._set_loading(False)
            self._update_execute_button()
            self._refresh_recent_transaction()

    @Slot(object)
    def _handle_drop_paths(self, value: object) -> None:
        """Interpret a full-mode drop before starting the existing scan worker."""
        if self._thread is not None:
            return
        if self.state.stale:
            if self.state.stale_reason is PreviewStaleReason.SETTINGS_CHANGED:
                self._show_error("当前预览已因设置变化失效；请先重新扫描或清空后再拖放。")
            elif self.state.stale_reason is PreviewStaleReason.CANCELLED:
                self._show_error("当前预览来自已取消的扫描；请先重新扫描或清空后再拖放。")
            else:
                self._show_error("当前预览已因文件系统变化失效；请先重新扫描或清空后再拖放。")
            return
        paths = tuple(cast(Sequence[Path | str], value))
        try:
            selection = self._drop_input_service.validate_full_drop(paths)
        except DropInputError as exc:
            self._show_error(exc.user_message)
            return
        has_existing_preview = self._has_preview_content() or bool(self.state.review_scope)
        if self.state.preview is not None and has_existing_preview and normalized_path_identity(
            self.state.preview.root_path
        ) != normalized_path_identity(selection.root_path):
            self._show_error(
                f"当前预览属于：{self.state.preview.root_path}\n"
                "无法将其他根目录下的作品加入同一批预览。请先清空预览，或重新扫描对应根目录。")
            return
        self.set_root_path(selection.root_path)
        # Keep start_scan compatible with existing callers while making this
        # drop explicitly append/merge instead of replacing the preview.
        self._next_scan_mode = ScanMode.APPEND if has_existing_preview else ScanMode.REPLACE
        try:
            self.start_scan(selection.selected_paths)
        finally:
            self._next_scan_mode = ScanMode.REPLACE

    @Slot()
    def cancel_scan(self) -> None:
        """Request cooperative cancellation and leave the current request bounded."""
        if self._worker is None:
            return
        self.state.cancel_requested = True
        self._worker.cancel()
        self.cancel_button.setEnabled(False)
        self.cancel_button.setText("正在取消…")
        self._set_operation_progress("正在取消；当前查询结束后会保留已完成结果…")

    @Slot(int, int, str)
    def _show_progress(self, completed: int, total: int, work_code: str) -> None:
        prefix = "正在刷新预览" if self.state.scan_mode is ScanMode.RECONCILE else "正在查询"
        self._set_operation_progress(f"{prefix} {completed + 1} / {total}：{work_code}")

    @Slot(object)
    def _show_preview(self, value: object) -> None:
        result = cast(OrganizerWorkerResult, value)
        preview = result.preview
        if not self.state.owns_result(result.request_id, result.preview_revision):
            return
        self.state.settings_generation = self._organizer_service.settings_generation
        if preview.settings_generation != self.state.settings_generation:
            self.invalidate_preview()
            return
        if self.state.cancel_requested:
            preview = replace(preview, cancelled=True)
        if preview.cancelled:
            if self.state.scan_mode is ScanMode.RECONCILE:
                self._show_error("预览刷新已取消。")
                return
            if self.state.scan_mode is ScanMode.APPEND:
                self._show_error("追加预览已取消；当前预览保持不变。")
                return
            if self.state.preview is not None:
                self._show_error("扫描已取消；当前预览保持不变。")
                return
            # With no previous collection, retain the existing useful behavior
            # of showing completed rows from a cancelled replace scan.
            self._commit_preview(preview)
            self._set_feedback("扫描已取消；已完成和未处理项目仍可检查，但不可执行。", "success")
            self._update_summary(preview)
            return

        if self.state.scan_mode is ScanMode.APPEND and self.state.preview is not None:
            if (
                self.state.stale
                or self.state.preview.settings_generation != preview.settings_generation
            ):
                self.invalidate_preview()
                return
            self._clear_highlight()
            self.preview_model.append_preview(preview)
            preview = self.state.preview
            assert preview is not None
            self._update_preview_controls()
            self._set_feedback("追加预览完成；未修改本地文件。", "success")
        else:
            self._commit_preview(preview, reconcile=self.state.scan_mode is ScanMode.RECONCILE)

        if self.state.scan_mode is ScanMode.RECONCILE:
            unmatched = self.state.completed_targets.difference(self.state.row_keys)
            if unmatched:
                logger.warning("Completed rename targets absent from refreshed preview: %s",
                               sorted(unmatched))
            if self.state.reconciliation_origin is BusyOperation.UNDO:
                undone = self.state.reconciliation_undo_result
                assert undone is not None
                message = f"撤销已恢复 {undone.success_count} 项，预览已刷新。"
                if undone.status.name == "UNDO_PARTIAL":
                    message += (f" 失败 {undone.failed_count} 项，"
                                f"剩余可撤销 {undone.pending_count} 项。")
            else:
                result = self.state.last_execution_result
                count = result.success_count if result is not None else 0
                message = f"已完成 {count} 项，预览已刷新。"
                if result is not None and result.status.name in {"PARTIAL", "FAILED"}:
                    message += (f" 本次失败 {result.failed_count} 项，"
                                f"未执行 {result.not_executed_count} 项。")
            self._set_feedback(message, "success")
            self._set_operation_progress("")
            self._update_summary(preview)
            return

        if (
            self.state.scan_mode is not ScanMode.APPEND
            and not preview.plans
            and not preview.scan.candidates
        ):
            self._set_feedback("未发现可整理的作品文件夹；未修改本地文件。", "success")
        elif self.state.scan_mode is not ScanMode.APPEND:
            self._set_feedback("预览生成完成；未修改本地文件。", "success")
        self._set_operation_progress("")
        self._update_summary(preview)

    def set_preview(self, preview: OrganizerPreview) -> None:
        """Render a preview directly for smoke tests and embedding callers."""
        self._commit_preview(preview)
        self._set_feedback(
            "扫描已取消；已完成和未处理项目仍可检查，但不可执行。"
            if preview.cancelled else "预览生成完成；未修改本地文件。",
            "success",
        )
        self._update_summary(preview)
        self._update_execute_button()

    @property
    def preview(self) -> OrganizerPreview | None:
        """Return the single authoritative collection behind the table."""
        return self.state.preview

    def selected_ready_plans(self) -> tuple[RenamePlan, ...]:
        self.state.settings_generation = self._organizer_service.settings_generation
        return self.state.checked_ready_plans()

    def selected_ready_count(self) -> int:
        self.state.settings_generation = self._organizer_service.settings_generation
        return self.state.checked_ready_count

    @property
    def preview_stale(self) -> bool:
        return self.state.stale

    @property
    def preview_stale_reason(self) -> PreviewStaleReason | None:
        return self.state.stale_reason

    def invalidate_preview(self) -> None:
        """Invalidate existing rows and in-flight ownership after runtime settings change."""
        self._invalidate_preview(PreviewStaleReason.SETTINGS_CHANGED)

    def apply_settings(self, settings: object) -> None:
        """Apply validated settings to the organizer's next preview."""
        self._organizer_service.apply_settings(settings)

    @Slot(str)
    def _input_root_changed(self, value: str) -> None:
        self.state.input_root = value
        self._update_root_difference()

    @Slot()
    def _table_selection_changed(self) -> None:
        selection = self.table.selectionModel()
        current = self.table.currentIndex()
        self.state.project_highlight(
            [index.row() for index in selection.selectedRows()],
            current.row() if current.isValid() else None,
        )
        self._update_preview_controls()
        self._update_action_labels()
        self._refresh_detail(current.row() if current.isValid() else None)

    def _clear_highlight(self) -> None:
        self.table.clearSelection()
        self.table.setCurrentIndex(QModelIndex())
        self.state.clear_highlight()
        self._update_action_labels()
        self._refresh_detail(None)

    def refresh_mutation_state(self) -> None:
        """Refresh only journal-backed controls after another UI entry point mutates."""
        self._refresh_recent_transaction()
        self._update_execute_button()

    @Slot()
    def mark_filesystem_changed(self) -> None:
        """Handle shared journal notifications; owned Undo results decide preview truth."""
        # Undo journal notifications include preflight conflicts and intent writes.
        # Our active Undo's authoritative result owns invalidation/reconciliation;
        # the generic notification cannot establish that a filesystem change occurred.
        if self.state.busy is BusyOperation.UNDO:
            self.refresh_mutation_state()
            return
        self._invalidate_preview(PreviewStaleReason.FILESYSTEM_CHANGED)
        self._refresh_recent_transaction()
        self._update_execute_button()

    def _update_execute_button(self) -> None:
        self.state.settings_generation = self._organizer_service.settings_generation
        self.state.executor_available = False
        blocked = True
        if self._execution_service is not None:
            try:
                self.state.executor_available = self._execution_service.available
                unresolved = self._execution_service.unresolved_transaction()
                if unresolved is not None:
                    self.state.unresolved_transaction_id = unresolved.transaction_id
                blocked = unresolved is not None or not self.state.executor_available
            except Exception:
                self._journal_unavailable = True
                self._journal_error_message = "无法读取重命名记录；执行和撤销已暂停。"
                blocked = True
        self.state.mutation_blocked = blocked or self._journal_unavailable
        self.execute_button.setText(f"执行重命名（{self.state.checked_ready_count} 项）")
        self.execute_button.setEnabled(self.state.can_execute)
        self._update_action_hint()
        self._update_bulk_controls()
        self._refresh_status_banner()

    @Slot(object)
    def _show_execution_result(self, value: object) -> None:
        result = cast(RenameExecutionResult, value)
        self.state.last_execution_result = result
        self._invalidate_preview(PreviewStaleReason.FILESYSTEM_CHANGED)
        if result.status.name == "COMPLETED":
            self._set_feedback(
                f"重命名完成：成功 {result.success_count}，失败 {result.failed_count}。 "
                f"当前预览已失效，请重新扫描。",
                "success",
            )
        elif result.status.name == "PARTIAL":
            self._set_feedback(
                f"重命名未全部完成：成功 {result.success_count}，失败 {result.failed_count}，"
                f"未执行 {result.not_executed_count}。已停止后续操作。"
                " 可以从最近事务尝试撤销已经成功的修改。",
                "error",
            )
        elif result.status.name == "RECOVERY_REQUIRED":
            self._set_feedback(
                "重命名状态需要恢复：文件系统与 journal 可能不同步。"
                " 请不要立即重新执行；详细信息已写入日志。",
                "error",
            )
        else:
            self._show_error(result.error or "重命名未执行。")
        self._refresh_recent_transaction()
        if (not self._journal_unavailable and self.state.unresolved_transaction_id is None
                and self.state.prepare_reconciliation(result)):
            self._set_feedback(
                f"重命名完成 {result.success_count} 项，正在刷新预览…", "success")
            if self._thread is None:
                self.retry_refresh()

    @Slot()
    def retry_refresh(self) -> None:
        """Refresh preserved members under the committed root through ownership gates."""
        root = self.state.reconciliation_root
        if self._thread is not None or root is None:
            return
        self._refresh_recent_transaction()
        if self._journal_unavailable or self.state.unresolved_transaction_id is not None:
            return
        self.state.reconciliation_pending = True
        self._start_scan(str(root), ScanMode.RECONCILE, tuple(self.state.review_scope.values()))

    @Slot(object)
    def _show_undo_result(self, value: object) -> None:
        result = cast(UndoResult, value)
        self.state.last_undo_result = result
        if result.status.name == "UNDONE":
            self._set_feedback(
                f"撤销完成：恢复 {result.success_count} 个目录。",
                "success",
            )
        elif result.status.name == "UNDO_PARTIAL":
            self._show_error(
                f"撤销未全部完成：成功 {result.success_count}，失败 {result.failed_count}。"
                " 已停止后续撤销操作。"
            )
        elif result.status.name == "RECOVERY_REQUIRED":
            self._show_error("撤销状态需要恢复；请不要立即重新执行。")
        else:
            self._show_error(result.error or "撤销未执行。")
        self._refresh_recent_transaction()

        previously_undone = self._undo_successes_before_attempt
        self._undo_successes_before_attempt = frozenset()
        self._reconcile_undo_result(result, previously_undone)

    def _reconcile_undo_result(self, result: UndoResult, previously_undone: frozenset[int]) -> None:
        relevant = self.state.undo_affects_preview(result)
        if (relevant and not self._journal_unavailable
                and self.state.unresolved_transaction_id is None
                and self.state.prepare_undo_reconciliation(
                    result, previously_undone=previously_undone)):
            if self.state.preview is not None:
                self._update_summary(self.state.preview)
            # The old preview cannot authorize mutation while either worker finishes.
            if self.state.reconciliation_pending:
                self._invalidate_preview(PreviewStaleReason.FILESYSTEM_CHANGED)
                self._set_feedback(
                    "撤销完成，正在刷新预览…" if result.status.name == "UNDONE"
                    else "撤销未全部完成，正在刷新预览…", "success")
                if self._thread is None:
                    self.retry_refresh()
        elif relevant and (result.status.name == "RECOVERY_REQUIRED"
                           or (result.transaction is not None and result.transaction.unresolved)
                           or (result.status.name in {"UNDONE", "UNDO_PARTIAL"}
                               and any(op.undo_status is UndoStatus.SUCCESS
                                       and op.sequence not in previously_undone
                                       for op in result.operations))):
            self.state.reconciliation_root = None
            self.state.reconciliation_pending = False
            self._invalidate_preview(PreviewStaleReason.FILESYSTEM_CHANGED)

    @Slot()
    def execute_rename(self) -> None:
        self._update_execute_button()
        plans = self.selected_ready_plans()
        if self._execution_service is None or not self.state.can_execute or not plans:
            return
        warning_count = sum(bool(plan.warnings) for plan in plans)
        text = f"即将重命名 {len(plans)} 个文件夹。 不会移动或删除文件。 不会覆盖已存在目录。"
        if warning_count:
            text += f" 其中 {warning_count} 项包含路径警告。"
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("确认执行重命名")
        dialog.setText(text)
        dialog.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        )
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        details = " ".join(f"{plan.source_path.name} -> {plan.target_path}" for plan in plans)
        dialog.setDetailedText(details)
        if dialog.exec() != QMessageBox.StandardButton.Yes:
            return
        root = (
            str(self.state.preview.root_path)
            if self.state.preview is not None
            else self.state.input_root.strip()
        )
        service = self._execution_service
        self._start_action(
            lambda callback: service.execute(
                root,
                plans,
                confirmed=True,
                progress_callback=callback,
            ),
            self._show_execution_result,
        )

    @Slot()
    def undo_recent(self) -> None:
        if self._undo_service is None or self._thread is not None:
            return
        try:
            transaction = self._undo_service.latest_transaction()
        except Exception as exc:
            self._show_error(f"无法读取最近事务：{exc}")
            return
        if transaction is None:
            self._refresh_recent_transaction()
            return
        count = sum(
            operation.status.name == "SUCCESS" and operation.undo_status.name != "SUCCESS"
            for operation in transaction.operations
        )
        dialog = QMessageBox(self)
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setWindowTitle("确认撤销重命名")
        dialog.setText(f"将撤销最近一次重命名。 {count} 个目录将恢复到执行前的名称。")
        dialog.setStandardButtons(
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel
        )
        dialog.setDefaultButton(QMessageBox.StandardButton.Cancel)
        if dialog.exec() != QMessageBox.StandardButton.Yes:
            return
        service = self._undo_service
        # Undo snapshots/counts include successes from earlier partial attempts.
        # Only new SUCCESS facts from this attempt changed the filesystem now.
        self._undo_successes_before_attempt = frozenset(
            op.sequence for op in transaction.operations if op.undo_status is UndoStatus.SUCCESS)
        self._start_action(
            lambda callback: service.undo(
                transaction.transaction_id,
                confirmed=True,
                progress_callback=callback,
            ),
            self._show_undo_result,
        )

    def _start_action(self, action, result_handler) -> None:
        if self._thread is not None:
            return
        thread = QThread()
        worker = RenameActionWorker(action)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._show_action_progress)
        worker.result_ready.connect(result_handler)
        worker.failed.connect(self._show_action_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._action_finished)
        self._thread = thread
        self._action_worker = worker
        self.state.busy = (
            BusyOperation.UNDO
            if result_handler == self._show_undo_result else BusyOperation.EXECUTE
        )
        self._clear_feedback()
        self._set_operation_progress(
            "正在撤销最近一次重命名…"
            if self.state.busy is BusyOperation.UNDO else "正在准备重命名…"
        )
        self._set_action_loading(True)
        thread.start()

    @Slot(int, int, str, str)
    def _show_action_progress(self, completed: int, total: int, source: str, target: str) -> None:
        self._set_operation_progress(
            f"正在尝试 {completed} / {total}：{source} → {target}"
        )

    @Slot(str)
    def _show_error(self, message: str) -> None:
        if self.state.scan_mode is ScanMode.RECONCILE:
            self.state.reconciliation_pending = False
            self._invalidate_preview(PreviewStaleReason.REFRESH_FAILED)
            self._set_feedback(message, "error")
            return
        if self._worker is not None and self.state.preview is not None:
            message = f"{message} 当前预览保持不变。"
        self._set_feedback(message, "error")

    @Slot(int, int, str)
    def _show_scan_error(self, request_id: int, revision: int, message: str) -> None:
        if self.state.owns_result(request_id, revision):
            self._show_error(message)

    @Slot(str)
    def _show_action_error(self, message: str) -> None:
        """Treat an escaped action exception as a stale preview as well."""
        self._undo_successes_before_attempt = frozenset()
        self._invalidate_preview(PreviewStaleReason.FILESYSTEM_CHANGED)
        self._show_error(message)
        self._refresh_recent_transaction()

    @Slot()
    def _scan_finished(self) -> None:
        logger.debug("organizer scan thread finished; clearing UI busy state")
        self._thread = None
        self._worker = None
        if (self.state.scan_mode is ScanMode.RECONCILE
                and self.state.reconciliation_root is not None):
            # Covers errors, cancellation and rejected settings/revision results.
            self.state.reconciliation_pending = False
            if self.state.stale_reason is not PreviewStaleReason.SETTINGS_CHANGED:
                self.preview_model.mark_stale(PreviewStaleReason.REFRESH_FAILED)
        self.state.finish_operation()
        self._set_loading(False)
        self.cancel_button.setText("取消扫描")
        self._set_operation_progress("")
        self._update_execute_button()
        self._refresh_recent_transaction()

    @Slot()
    def _action_finished(self) -> None:
        self._thread = None
        self._action_worker = None
        self.state.finish_operation()
        self._set_action_loading(False)
        self._set_operation_progress("")
        self._update_execute_button()
        self._refresh_recent_transaction()
        if self.state.reconciliation_pending:
            self.retry_refresh()

    def _set_action_loading(self, loading: bool) -> None:
        self.preview_model.refresh_editability()
        self.input_panel.setAcceptDrops(not loading)
        self.root_input.setEnabled(not loading)
        self.browse_button.setEnabled(not loading)
        self.scan_button.setEnabled(not loading)
        self.scan_button.setVisible(not loading)
        self.cancel_button.setEnabled(False)
        self.cancel_button.hide()
        self.execute_button.setEnabled(False)
        self.undo_button.setEnabled(False)
        self.table.setEnabled(not loading)
        self.input_hint_label.setVisible(not loading and not self._operation_message)
        self._update_preview_controls()

    def is_busy(self) -> bool:
        """Return whether the organizer worker thread is active."""
        return self.state.busy is not None

    def _set_loading(self, loading: bool) -> None:
        if not loading:
            self.state.finish_operation()
        self.preview_model.refresh_editability()
        self.input_panel.setAcceptDrops(not loading)
        self.root_input.setEnabled(not loading)
        self.browse_button.setEnabled(not loading)
        self.scan_button.setEnabled(not loading)
        self.scan_button.setVisible(not loading)
        self.cancel_button.setEnabled(loading)
        self.cancel_button.setVisible(loading)
        self.table.setEnabled(not loading)
        self.input_hint_label.setVisible(not loading and not self._operation_message)
        self._update_preview_controls()
        self._refresh_status_banner()
        if loading:
            self.execute_button.setEnabled(False)
            self.undo_button.setEnabled(False)

    def _invalidate_preview(self, reason: PreviewStaleReason) -> None:
        self.state.settings_generation = self._organizer_service.settings_generation
        self.preview_model.mark_stale(reason)
        if not self.state.stale:
            return
        # Rows and selection stay readable; the model itself removes check editability.
        self.table.setEnabled(not self.is_busy())
        self._update_preview_controls()
        self._update_execute_button()

    def _refresh_recent_transaction(self) -> None:
        self._undo_pending = False
        self._journal_unavailable = False
        self._journal_error_message = ""
        self.state.unresolved_transaction_id = None
        if self._execution_service is not None:
            try:
                if not self._execution_service.available:
                    self._journal_unavailable = True
                    self._journal_error_message = "重命名记录 journal 暂不可用。"
            except Exception:
                self._journal_unavailable = True
                self._journal_error_message = "无法读取重命名记录 journal。"
        if self._undo_service is None and self._execution_service is None:
            self.undo_button.setEnabled(False)
            self.view_transaction_button.hide()
            self.recent_transaction_label.setText("最近可撤销：撤销服务不可用")
            self._refresh_status_banner()
            self._update_empty_state()
            return
        try:
            transaction_service = self._undo_service or self._execution_service
            unresolved = (
                transaction_service.unresolved_transaction()
                if transaction_service is not None else None
            )
            if unresolved is not None:
                self.state.unresolved_transaction_id = unresolved.transaction_id
                self.view_transaction_button.show()
                self.undo_button.setEnabled(False)
                self.recent_transaction_label.setText(
                    f"需要恢复的事务：{unresolved.transaction_id} · "
                    f"{unresolved.display_status.value}"
                )
                self.recent_transaction_label.set_extra_tooltip(
                    f"事务：{unresolved.transaction_id}\n根目录：{unresolved.root}\n"
                    f"状态：{unresolved.display_status.value}"
                )
                self._refresh_status_banner()
                self._update_empty_state()
                return
            self.view_transaction_button.hide()
            transaction = (
                self._undo_service.latest_transaction()
                if self._undo_service is not None and not self._journal_unavailable else None
            )
        except Exception as exc:
            self.undo_button.setEnabled(False)
            self.view_transaction_button.hide()
            self._journal_unavailable = True
            self._journal_error_message = f"读取重命名记录失败：{exc}"
            self.recent_transaction_label.setText("最近可撤销：journal 暂不可用")
            self._refresh_status_banner()
            self._update_empty_state()
            return
        if self._journal_unavailable:
            self.undo_button.setEnabled(False)
            self.view_transaction_button.hide()
            self.recent_transaction_label.setText("最近可撤销：journal 暂不可用")
            self._refresh_status_banner()
            self._update_empty_state()
            return
        if transaction is None:
            self.undo_button.setEnabled(False)
            self.recent_transaction_label.setText("最近可撤销：无")
            self.recent_transaction_label.set_extra_tooltip("")
            self._refresh_status_banner()
            self._update_empty_state()
            return
        pending = sum(
            operation.status.name == "SUCCESS" and operation.undo_status.name != "SUCCESS"
            for operation in transaction.operations
        )
        self._undo_pending = pending > 0
        self.undo_button.setEnabled(self._thread is None and pending > 0)
        self.view_transaction_button.hide()
        if pending:
            self.recent_transaction_label.setText(
                f"最近可撤销：{transaction.created_at:%Y-%m-%d %H:%M} · "
                f"可撤销：{pending} 项"
            )
        else:
            self.recent_transaction_label.setText(
                f"最近事务：{transaction.created_at:%Y-%m-%d %H:%M} · 已无可撤销项"
            )
        self.recent_transaction_label.set_extra_tooltip(transaction.transaction_id)
        self._refresh_status_banner()
        self._update_empty_state()

    def _clear_preview(self) -> None:
        self._clear_highlight()
        self.preview_model.clear_preview()
        self.table.setEnabled(True)
        self._update_preview_context()
        self._update_summary(None)
        self._update_empty_state()
        self._refresh_detail(None)
        self._refresh_status_banner()
        self._update_preview_controls()

    def _has_preview_content(self) -> bool:
        return self.state.preview is not None and bool(self.state.preview.plans)

    @Slot()
    def remove_selected(self) -> None:
        """Remove selected rows from the in-memory preview only."""
        if self.is_busy() or self.state.preview is None or self.state.stale:
            return
        rows = [
            row for row, key in enumerate(self.state.row_keys)
            if key in self.state.highlighted_keys
        ]
        if not rows:
            return
        self._clear_highlight()
        self.preview_model.remove_rows(rows)
        assert self.state.preview is not None
        try:
            replanned = self._organizer_service.reevaluate_preview(self.state.preview)
        except Exception:
            logger.exception("Could not re-evaluate surviving Organizer plans")
            self._invalidate_preview(PreviewStaleReason.FILESYSTEM_CHANGED)
            self._show_error("项目已移除，但剩余计划无法重新检查；请重新扫描。")
            return
        self.preview_model.reevaluate_preview(replanned)
        self._set_feedback(f"已从当前预览移除 {len(rows)} 项；未修改本地文件。", "success")
        assert self.state.preview is not None
        self._update_summary(self.state.preview)
        self._update_preview_context()
        self._update_empty_state()
        self._refresh_detail(None)
        self._update_execute_button()

    def _select_context_row(self, pos: QPoint) -> bool:
        index = self.table.indexAt(pos)
        if not index.isValid():
            return False
        selection = self.table.selectionModel()
        flags = QItemSelectionModel.SelectionFlag.NoUpdate
        if not selection.isRowSelected(index.row(), QModelIndex()):
            flags = (QItemSelectionModel.SelectionFlag.ClearAndSelect
                     | QItemSelectionModel.SelectionFlag.Rows)
        selection.setCurrentIndex(index, flags)
        self.table.setFocus(Qt.FocusReason.MouseFocusReason)
        return True

    def _show_preview_menu(self, pos: QPoint) -> None:
        if self._select_context_row(pos):
            self.preview_menu.popup(self.table.viewport().mapToGlobal(pos))

    @Slot()
    def _view_unresolved_transaction(self) -> None:
        if self.state.unresolved_transaction_id is not None:
            self.view_transaction_requested.emit(self.state.unresolved_transaction_id)

    @Slot()
    def clear_preview(self) -> None:
        """Clear the in-memory preview without touching the selected root."""
        if self.is_busy():
            return
        self._clear_preview()
        self._set_feedback("预览已清空；未修改本地文件。", "success")
        self._update_execute_button()

    def _commit_preview(self, preview: OrganizerPreview, *, reconcile: bool = False) -> None:
        self.state.settings_generation = self._organizer_service.settings_generation
        if not self.state.input_root.strip():
            self.set_root_path(preview.root_path)
        self._clear_highlight()
        self.preview_model.replace_preview(preview, reconcile=reconcile)
        self.table.setEnabled(not self.is_busy())
        self._update_preview_context()
        self._update_summary(preview)
        self._update_empty_state()
        self._refresh_detail(None)
        self._refresh_status_banner()
        self._update_preview_controls()
        if self.state.stale:
            self._update_execute_button()

    def _update_preview_controls(self) -> None:
        active = not self.is_busy() and not self.state.stale
        has_rows = self.state.preview is not None and bool(self.state.preview.plans)
        has_selection = bool(self.state.highlighted_keys)
        self.remove_button.setEnabled(active and has_rows and has_selection)
        self.remove_action.setEnabled(self.remove_button.isEnabled())
        # Clearing is also the safe escape hatch for a stale preview.
        self.clear_button.setEnabled(not self.is_busy() and self.state.preview is not None)
        self._update_action_labels()
        self._update_bulk_controls()
        self._update_empty_state()

    def _update_bulk_controls(self) -> None:
        editable = self.state.checks_editable and not self._journal_unavailable and any(
            plan.status is RenamePlanStatus.READY for plan in self.state.plans
        )
        self.bulk_menu_button.setEnabled(editable)
        for action in self.bulk_actions.values():
            action.setEnabled(editable)

    def _update_summary(self, preview: OrganizerPreview | None) -> None:
        if preview is None:
            self.summary_label.clear_counts()
            return
        ready = preview.count(RenamePlanStatus.READY)
        unchanged = preview.count(RenamePlanStatus.UNCHANGED)
        total = len(preview.plans)
        non_executable = total - ready - unchanged
        completed = sum(self.state.completed(row) for row in range(len(preview.plans)))
        unchanged -= completed
        warning = sum(bool(plan.warnings) and plan.status is RenamePlanStatus.READY
                      for plan in preview.plans)
        skipped = preview.skipped_count
        self.summary_label.set_counts(total, ready, non_executable, warning, unchanged, skipped,
                                      completed=completed)

    def _refresh_status_style(self) -> None:
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def _update_preview_context(self) -> None:
        root = self.state.committed_preview_root
        self.preview_root_display.set_path(str(root) if root is not None else "")
        self._update_root_difference()

    def _update_root_difference(self) -> None:
        root = self.state.committed_preview_root
        if root is None:
            self.input_root_difference_label.hide()
            return
        input_root = self.state.input_root.strip()
        if input_root and normalized_path_identity(input_root) == normalized_path_identity(root):
            self.input_root_difference_label.hide()
            self.input_root_difference_label.clear()
            return
        if not input_root:
            message = f"下次扫描目录尚未设置；当前预览仍属于 {root}。"
        else:
            message = (
                f"输入目录已更改；当前预览仍属于 {root}。"
                "扫描成功后才会替换当前预览。"
            )
        self.input_root_difference_label.setText(message)
        self.input_root_difference_label.setAccessibleDescription(message)
        self.input_root_difference_label.show()

    def _update_empty_state(self) -> None:
        has_rows = bool(self.state.plans)
        has_preview = self.state.preview is not None
        self.preview_context.setVisible(has_preview)
        self.preview_actions.setVisible(has_preview)
        self.bulk_toolbar.setVisible(has_rows)
        self.undo_region.setVisible(
            has_preview or self._undo_pending
            or self.state.unresolved_transaction_id is not None or self._journal_unavailable
        )
        self.input_hint_label.setVisible(has_preview and not self.is_busy())
        if not has_rows:
            self.detail_panel.hide()
        self.table.setVisible(has_rows)
        self.empty_state.setVisible(not has_rows)
        if has_rows:
            return
        if self.state.preview is None:
            self.empty_title_label.setText("尚未生成预览")
            self.empty_description_label.setText(
                "选择上方目录并扫描，或拖入作品目录以生成预览。"
            )
        elif self.state.stale:
            self.empty_title_label.setText("当前预览没有可检查的行")
            self.empty_description_label.setText("预览行仍为空；状态说明会显示在上方。")
        else:
            self.empty_title_label.setText("当前预览没有项目")
            self.empty_description_label.setText(
                "没有发现可整理的作品文件夹。更改下次扫描目录后可再次扫描。"
            )

    def _refresh_detail(self, row: int | None) -> None:
        """Render detail from the current state identity and structured plan data."""
        plans = self.state.plans
        if not plans:
            self.detail_panel.hide()
            return
        self.detail_panel.show()
        if row is None or not 0 <= row < len(plans):
            self.detail_summary_label.setText("选择一行查看当前行详情")
            self.detail_title_label.setText("选择一行查看作品标题和诊断")
            self.detail_metadata_label.clear()
            self.detail_source_path.set_path("")
            self.detail_target_path.set_path("")
            self.detail_diagnostics.clear()
            return

        key = self.state.row_keys[row]
        if key != self.state.current_key:
            self.detail_summary_label.setText("选择一行查看当前行详情")
            return
        plan = plans[row]
        code = plan.work_code or "、".join(plan.work_codes) or "—"
        title = plan.work.title if plan.work is not None else "作品元数据不可用"
        maker = plan.work.maker_name if plan.work is not None else None
        codes = "、".join(plan.work_codes) if plan.work_codes else code
        metadata = f"作品编号：{codes}"
        if maker:
            metadata += f" · 社团：{maker}"
        status = self.preview_model.status_label(row)
        if plan.warnings and plan.status is RenamePlanStatus.READY:
            status += f" · 另有 {len(plan.warnings)} 条警告"
        self.detail_summary_label.setText(f"当前行：{code} · {status}")
        self.detail_title_label.setText(title)
        self.detail_metadata_label.setText(metadata)
        self.detail_source_path.set_path(str(plan.source_path))
        target = str(plan.target_path) if plan.target_path is not None else (
            plan.proposed_name or "未生成目标路径"
        )
        self.detail_target_path.set_path(target)
        diagnostics = plan_diagnostics(plan)
        self.detail_diagnostics.setPlainText(diagnostics or "无警告或阻止原因。")
        self.detail_diagnostics.setFixedHeight(56 if diagnostics else 24)

    def _detail_expanded_changed(self, expanded: bool) -> None:
        self._detail_user_touched = True
        self._update_detail_expanded(expanded)

    def _update_detail_expanded(self, expanded: bool) -> None:
        self.detail_content.setVisible(expanded)
        self.detail_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.detail_toggle.setAccessibleDescription(
            "折叠当前行详情" if expanded else "展开当前行详情"
        )
        self._schedule_column_layout()

    def _set_detail_expanded(self, expanded: bool) -> None:
        if self.detail_toggle.isChecked() == expanded:
            return
        self.detail_toggle.blockSignals(True)
        self.detail_toggle.setChecked(expanded)
        self.detail_toggle.blockSignals(False)
        self._update_detail_expanded(expanded)

    def _resize_table_columns(self) -> None:
        if not hasattr(self, "table"):
            return
        width = max(1, self.table.viewport().width())
        check_width = 44
        status_width = min(164, max(144, round(width * 0.15)))
        work_width = min(250, max(180, round(width * 0.22)))
        remaining = max(2, width - check_width - status_width - work_width)
        current_width = max(1, round(remaining * 0.46))
        target_width = max(1, remaining - current_width)
        for column, section_width in enumerate(
            (check_width, status_width, work_width, current_width, target_width)
        ):
            self.table.setColumnWidth(column, section_width)

    def _schedule_column_layout(self, *_args) -> None:
        if not self._column_layout_timer.isActive():
            self._column_layout_timer.start(0)

    def _install_workspace_drop_routing(self) -> None:
        """One event owner across Organizer children and scroll viewports."""
        self.drop_overlay = QLabel(self)
        self.drop_overlay.setObjectName("organizerDropOverlay")
        self.drop_overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.drop_overlay.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.drop_overlay.setStyleSheet(f"""
            QLabel#organizerDropOverlay {{
              background: rgba(237, 245, 255, 225);
              color: {LIGHT_COLORS.text_primary};
              border: 1px solid {LIGHT_COLORS.focus};
              border-radius: 7px;
              font-weight: 600;
            }}
        """)
        self.drop_overlay.hide()
        for target in (self, *self.findChildren(QWidget)):
            if target is self.drop_overlay:
                continue
            target.setAcceptDrops(True)
            target.installEventFilter(self)

    def _show_drop_overlay(self, active: bool) -> None:
        if active:
            self.drop_overlay.setText(
                "松开以追加到当前预览" if self.state.plans else "松开以添加作品目录"
            )
            self.drop_overlay.setGeometry(QRect(
                20, self.input_panel.y(), max(0, self.width() - 40),
                max(0, self.height() - self.input_panel.y() - 16),
            ))
            self.drop_overlay.raise_()
        self.drop_overlay.setVisible(active)

    def eventFilter(self, watched, event) -> bool:
        kind = event.type()
        if (hasattr(self, "table") and watched is self.table.viewport()
                and kind is QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.RightButton):
            self._select_context_row(event.position().toPoint())
            return True
        if hasattr(self, "drop_overlay"):
            if kind in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
                paths = local_directory_paths(event.mimeData())
                accepted = bool(paths)
                self._show_drop_overlay(accepted and kind != QEvent.Type.Drop)
                if accepted:
                    event.acceptProposedAction()
                    if kind == QEvent.Type.Drop:
                        self._handle_drop_paths(paths)
                else:
                    event.ignore()
                return True
            if kind == QEvent.Type.DragLeave:
                self._show_drop_overlay(False)
                event.accept()
                return True
            if kind == QEvent.Type.Hide and watched is self:
                self._show_drop_overlay(False)
        if hasattr(self, "table") and watched is self.table.viewport() and event.type() in (
            QEvent.Type.Resize, QEvent.Type.Show,
        ):
            # The page can keep its size while an empty/hidden table gains its
            # final geometry or a scrollbar appears. Read that actual viewport
            # after Qt's layout, coalescing signals into one constant-time pass.
            self._schedule_column_layout()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._schedule_column_layout()
        # At the native 1160 x 800 window the page has about 760 px of height.
        # An expanded diagnostic pane left only 3-4 rows visible in Windows UAT.
        compact = self.width() < 880 or self.height() < 780
        if compact != self._compact_layout:
            self._compact_layout = compact
            if compact or not self._detail_user_touched:
                self._set_detail_expanded(not compact)
            self._update_action_hint()

    def _update_action_labels(self) -> None:
        highlighted = len(self.state.highlighted_keys)
        checked = self.state.checked_ready_count
        self.highlight_count_label.setText(f"高亮 {highlighted} 项")
        self.remove_button.setText(f"从预览移除（{highlighted}）")
        self.execute_button.setText(f"执行重命名（{checked} 项）")
        self._update_action_hint()

    def _update_action_hint(self) -> None:
        if self.is_busy():
            message = "操作进行中；请等待当前任务完成。"
        elif self.state.unresolved_transaction_id is not None:
            message = "请先查看并处理未解决事务。"
        elif self._journal_unavailable:
            message = "重命名记录不可用，执行与撤销已暂停。"
        elif self.state.stale:
            message = "当前预览只可检查；重新扫描或清空后才能执行。"
        elif self.state.preview is None:
            message = "扫描生成预览后，勾选可执行项目。"
        elif self.state.checked_ready_count == 0:
            message = "勾选可执行行；高亮选择与执行勾选相互独立。"
        elif not self.state.executor_available:
            message = "重命名执行服务当前不可用。"
        else:
            message = ("执行前再次确认并检查路径。" if self._compact_layout
                       else "执行前会再次确认并检查目标路径。")
        self.action_hint_label.setText(message)

    def _refresh_status_banner(self) -> None:
        unresolved = self.state.unresolved_transaction_id
        retry = (self.state.reconciliation_root is not None and not self.is_busy()
                 and unresolved is None and not self._journal_unavailable)
        self.retry_refresh_button.setVisible(retry)
        self.retry_refresh_button.setEnabled(retry)
        if unresolved is not None:
            self.status_banner.show_status(
                BannerState.BLOCKED,
                "存在未解决的重命名事务",
                "重命名和普通撤销已暂停。请查看该事务的记录与恢复信息。",
            )
        elif self._journal_unavailable:
            message = self._journal_error_message or "重命名记录暂不可用。"
            self.status_banner.show_status(
                BannerState.BLOCKED,
                "重命名记录不可用",
                f"{message}为保证可恢复性，执行和撤销已暂停。",
            )
        elif (self.state.reconciliation_root is not None and self.is_busy()
              and self.state.scan_mode is ScanMode.RECONCILE):
            undo = self.state.reconciliation_origin is BusyOperation.UNDO
            result = (self.state.reconciliation_undo_result if undo
                      else self.state.last_execution_result)
            count = result.success_count if result is not None else 0
            title = f"重命名完成 {count} 项，正在刷新预览…"
            if undo:
                title = ("撤销完成，正在刷新预览…" if result is not None
                         and result.status.name == "UNDONE"
                         else "撤销未全部完成，正在刷新预览…")
            self.status_banner.show_status(
                BannerState.INFO, title,
                ("当前列表为撤销前的预览" if undo else "当前列表为执行前的预览")
                + "，仅供等待刷新；执行勾选与重命名暂不可用。")
        elif self.state.stale:
            if self.state.reconciliation_root is not None:
                title = "重命名已完成，但预览刷新失败。"
                if self.state.reconciliation_origin is BusyOperation.UNDO:
                    undone = self.state.reconciliation_undo_result
                    title = ("撤销未全部完成，且预览刷新失败。" if undone is not None
                             and undone.status.name == "UNDO_PARTIAL"
                             else "撤销已完成，但预览刷新失败。")
                self.status_banner.show_status(
                    BannerState.STALE, title,
                    "当前列表可能已过期，请重新刷新。行仍可检查，但不能勾选或执行。")
                return
            if self.state.stale_reason is PreviewStaleReason.SETTINGS_CHANGED:
                title = "当前预览因设置变化已失效"
                message = "行仍可查看和复制，但不能勾选或执行。请重新扫描，或清空预览。"
            elif self.state.stale_reason is PreviewStaleReason.CANCELLED:
                title = "扫描已取消；结果仅供检查"
                message = "已完成和未处理项目仍保留为诊断预览，不能执行。请重新扫描或清空。"
            else:
                title = "当前预览因文件系统变化已失效"
                message = "行仍可查看和复制，但不能勾选或执行。请重新扫描，或清空预览。"
            self.status_banner.show_status(BannerState.STALE, title, message)
        else:
            self.status_banner.clear_status()

    def _set_operation_progress(self, message: str) -> None:
        self._operation_message = message
        self.progress_label.setText(message)
        self.progress_label.setVisible(bool(message))
        if not message and not self.is_busy():
            self.input_hint_label.setVisible(self.state.preview is not None)

    def _set_feedback(self, message: str, state: str = "success") -> None:
        self._feedback_timer.stop()
        self.status_label.setProperty("state", state)
        self.status_label.setText(message)
        self.status_label.setAccessibleDescription(message)
        self._refresh_status_style()
        self.status_label.show()
        self._feedback_timer.start()

    def _clear_feedback(self) -> None:
        self._feedback_timer.stop()
        self.status_label.clear()
        self.status_label.setAccessibleDescription("")
        self.status_label.hide()
