"""Read-only transaction history and recovery inspection page."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo
from math import cos, hypot, radians, sin

from PySide6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    QPointF,
    QRectF,
    QSize,
    Qt,
    Slot,
)
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QStyle,
    QStyledItemDelegate,
    QTableView,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from dlsite_organizer.domain.rename_execution import (
    ExecutionStatus,
    RenameOperation,
    RenameTransaction,
    RenameTransactionSummary,
    TransactionStatus,
    UndoStatus,
    UndoTransactionState,
)
from dlsite_organizer.services.rename_history import (
    CurrentPathObservation,
    CurrentPathState,
    OperationPathObservations,
    RenameHistoryService,
)
from dlsite_organizer.ui.theme.tokens import LIGHT_COLORS
from dlsite_organizer.ui.widgets.page_header import PageHeader
from dlsite_organizer.ui.widgets.status_banner import BannerState, StatusBanner


class RenameHistoryPage(QWidget):
    """Show persisted rename facts separately from current path observations."""

    PAGE_SIZE = 50
    _TRANSACTION_ID_ROLE = Qt.ItemDataRole.UserRole
    _MASTER_STATUS_ROLE = Qt.ItemDataRole.UserRole + 1

    def __init__(
        self,
        history_service: RenameHistoryService | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._history_service = history_service
        self._offset = 0
        self._selected_id: str | None = None
        self._selected_transaction: RenameTransaction | None = None
        self._operations: tuple[RenameOperation, ...] = ()
        self._observations_by_sequence: dict[int, OperationPathObservations] = {}
        self._build_ui()
        self.transaction_list.currentItemChanged.connect(self._selection_changed)
        self.operations_table.selectionModel().currentChanged.connect(
            self._operation_selection_changed
        )
        self.refresh_button.clicked.connect(self.refresh_history)
        self.newer_button.clicked.connect(self._show_newer_page)
        self.older_button.clicked.connect(self._show_older_page)
        self.observe_button.clicked.connect(self.refresh_observations)
        self.technical_toggle.toggled.connect(self._toggle_technical_details)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)

        layout.addWidget(
            PageHeader(
                "重命名历史",
                "查看已记录的重命名事务，并在操作列表中查看记录结果与当前路径观察。",
            )
        )

        content = QSplitter(Qt.Orientation.Horizontal)
        content.setObjectName("renameHistorySplitter")
        content.setChildrenCollapsible(False)
        content.setHandleWidth(6)
        content.setStyleSheet(f"""
            QSplitter::handle:horizontal {{
              background: {LIGHT_COLORS.border};
              margin: 7px 2px;
            }}
            """)
        master = QWidget()
        master.setObjectName("renameHistoryMaster")
        master.setMinimumWidth(232)
        master.setMaximumWidth(300)
        master_layout = QVBoxLayout(master)
        master_layout.setContentsMargins(0, 0, 0, 0)
        master_layout.setSpacing(10)
        list_heading = QLabel("事务")
        list_heading.setObjectName("sectionLabel")
        master_layout.addWidget(list_heading)
        self.transaction_list = QListWidget()
        self.transaction_list.setObjectName("renameTransactionList")
        self.transaction_list.setAccessibleName("重命名事务列表")
        self.transaction_list.setAccessibleDescription("按时间倒序排列的事务记录")
        self.transaction_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.transaction_list.setUniformItemSizes(True)
        self.transaction_list.setSpacing(3)
        self.transaction_list.setItemDelegate(_HistoryMasterDelegate(self.transaction_list))
        self.transaction_list.setStyleSheet("QListWidget { border: 0; background: transparent; }")
        self.transaction_stack = QStackedWidget()
        self.transaction_stack.setObjectName("renameTransactionStack")
        self.master_empty_state = QWidget()
        self.transaction_stack.addWidget(self.transaction_list)
        self.transaction_stack.addWidget(self.master_empty_state)
        master_layout.addWidget(self.transaction_stack, 1)
        self.page_label = QLabel("尚未加载")
        self.page_label.setObjectName("pageDescription")
        self.page_label.setWordWrap(True)
        master_layout.addWidget(self.page_label)
        page_controls = QHBoxLayout()
        self.newer_button = QPushButton("较新")
        self.newer_button.setEnabled(False)
        self.older_button = QPushButton("较早")
        self.newer_button.setAccessibleName("显示较新的事务")
        self.older_button.setAccessibleName("显示较早的事务")
        page_controls.addWidget(self.newer_button)
        page_controls.addWidget(self.older_button)
        master_layout.addLayout(page_controls)
        self.refresh_button = QPushButton("刷新历史")
        self.refresh_button.setAccessibleName("刷新历史记录和当前观察")
        master_layout.addWidget(self.refresh_button)
        content.addWidget(master)

        self.detail_scroll = QScrollArea()
        self.detail_scroll.setObjectName("renameHistoryDetailScroll")
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.detail_content = QWidget()
        self.detail_layout = QVBoxLayout(self.detail_content)
        self.detail_layout.setContentsMargins(18, 0, 4, 0)
        self.detail_layout.setSpacing(12)
        self.detail_scroll.setWidget(self.detail_content)

        self.empty_state = QWidget()
        empty_layout = QVBoxLayout(self.empty_state)
        empty_layout.setContentsMargins(0, 54, 0, 0)
        empty_layout.setSpacing(8)
        self.empty_title = QLabel("选择一条事务查看详情")
        self.empty_title.setObjectName("sectionLabel")
        self.empty_description = QLabel("")
        self.empty_description.setObjectName("pageDescription")
        self.empty_description.setWordWrap(True)
        empty_layout.addWidget(self.empty_title)
        empty_layout.addWidget(self.empty_description)
        empty_layout.addStretch(1)
        self.detail_layout.addWidget(self.empty_state, 1)

        self.history_detail = QWidget()
        history_layout = QVBoxLayout(self.history_detail)
        history_layout.setContentsMargins(0, 0, 0, 0)
        history_layout.setSpacing(12)
        self.detail_heading = QLabel("事务详情")
        self.detail_heading.setObjectName("sectionLabel")
        history_layout.addWidget(self.detail_heading)

        self.transaction_timestamp = QLabel("")
        self.transaction_timestamp.setStyleSheet("font-size: 20px; font-weight: 600;")
        self.transaction_timestamp.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        history_layout.addWidget(self.transaction_timestamp)

        self.query_status_label = QLabel("")
        self.query_status_label.setObjectName("statusLabel")
        self.query_status_label.setWordWrap(True)
        self.query_status_label.hide()
        history_layout.addWidget(self.query_status_label)

        self.transaction_summary = QLabel("")
        self.transaction_summary.setObjectName("pageDescription")
        self.transaction_summary.setWordWrap(True)
        history_layout.addWidget(self.transaction_summary)

        self.recovery_banner = StatusBanner(self)
        history_layout.addWidget(self.recovery_banner)

        history_layout.addWidget(_section_divider())
        journal_heading = _section_title("记录事实")
        history_layout.addWidget(journal_heading)
        self.journal_facts_label = _FactGrid()
        history_layout.addWidget(self.journal_facts_label)
        self.root_path_value, self.copy_root_path_button = self._add_path_detail_row(
            history_layout,
            "记录的根目录",
            "复制根目录",
        )
        self.recovery_label = QLabel("")
        self.recovery_label.setObjectName("pageDescription")
        self.recovery_label.setWordWrap(True)
        self.recovery_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.recovery_label.hide()
        history_layout.addWidget(self.recovery_label)

        self.technical_section = QWidget()
        technical_layout = QVBoxLayout(self.technical_section)
        technical_layout.setContentsMargins(0, 0, 0, 0)
        technical_layout.setSpacing(8)
        self.transaction_id_value = QLabel("")
        self.transaction_id_value.setObjectName("renameHistoryTransactionId")
        self.transaction_id_value.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.transaction_id_value.setWordWrap(True)
        self.copy_transaction_id_button = QPushButton("复制")
        self.copy_transaction_id_button.clicked.connect(
            lambda: QApplication.clipboard().setText(self.transaction_id_value.text())
        )
        transaction_id_row = QHBoxLayout()
        transaction_id_row.addWidget(QLabel("事务 ID"))
        transaction_id_row.addWidget(self.transaction_id_value, 1)
        transaction_id_row.addWidget(self.copy_transaction_id_button)
        technical_layout.addLayout(transaction_id_row)
        self.technical_facts_label = QLabel("")
        self.technical_facts_label.setWordWrap(True)
        self.technical_facts_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        technical_layout.addWidget(self.technical_facts_label)
        self.technical_operation_label = QLabel("")
        self.technical_operation_label.setWordWrap(True)
        self.technical_operation_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        technical_layout.addWidget(self.technical_operation_label)
        self.technical_section.hide()

        self.technical_toggle = _DisclosureButton()
        self.technical_toggle.setStyleSheet(
            f"QToolButton {{ background: transparent; color: {LIGHT_COLORS.text_secondary}; "
            "border: 0; padding: 4px 2px; }"
            f"QToolButton:focus {{ border-bottom: 1px solid {LIGHT_COLORS.focus}; }}"
        )
        self.technical_toggle.setObjectName("renameHistoryTechnicalToggle")
        self.technical_toggle.setText("技术详情")
        self.technical_toggle.setAccessibleName("技术详情")
        self.technical_toggle.setAccessibleDescription("展开或收起事务 ID、原始状态和精确时间")
        self.technical_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.technical_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.technical_toggle.setCheckable(True)
        self.detail_layout.addWidget(self.history_detail)
        self.history_detail.hide()

        history_layout.addWidget(_section_divider())
        self.operations_heading = _section_title("重命名操作")
        observation_header = QHBoxLayout()
        observation_header.setContentsMargins(0, 0, 0, 0)
        observation_header.addWidget(self.operations_heading)
        observation_header.addStretch(1)
        self.observation_updated_label = QLabel("")
        self.observation_updated_label.setObjectName("pageDescription")
        self.observe_button = QPushButton("重新观察")
        self.observe_button.setStyleSheet(
            "QPushButton { min-height: 24px; padding: 2px 8px; }"
        )
        self.observe_button.setAccessibleName("重新观察当前文件系统路径")
        observation_header.addWidget(self.observation_updated_label)
        observation_header.addWidget(self.observe_button)
        history_layout.addLayout(observation_header)
        self.operations_table = QTableView()
        self.operations_table.setObjectName("renameHistoryOperations")
        self.operations_table.setAccessibleName("重命名操作历史，重命名与撤销结果分别显示")
        self.operations_model = _OperationTableModel(self)
        self.operations_table.setModel(self.operations_model)
        self._configure_operations_table(self.operations_table)
        history_layout.addWidget(self.operations_table)
        self.observation_summary = QLabel("")
        self.observation_summary.setObjectName("pageDescription")
        self.observation_summary.setWordWrap(True)
        history_layout.addWidget(self.observation_summary)
        self.observation_caveat = QLabel(
            "路径观察只显示当前存在状态和类型；路径存在不证明它仍指向原目录。"
        )
        self.observation_caveat.setObjectName("pageDescription")
        self.observation_caveat.setWordWrap(True)
        history_layout.addWidget(self.observation_caveat)
        self.observation_status_banner = StatusBanner(self)
        history_layout.addWidget(self.observation_status_banner)

        self.operation_detail_section = QWidget()
        operation_detail_layout = QVBoxLayout(self.operation_detail_section)
        operation_detail_layout.setContentsMargins(0, 0, 0, 0)
        operation_detail_layout.setSpacing(8)
        operation_detail_layout.addWidget(_section_title("所选操作"))
        self.operation_sequence_label = QLabel("")
        self.operation_sequence_label.hide()
        operation_detail_layout.addWidget(self.operation_sequence_label)
        self.operation_status_label = _FactGrid()
        operation_detail_layout.addWidget(self.operation_status_label)

        self.source_path_value, self.copy_source_path_button = self._add_path_detail_row(
            operation_detail_layout,
            "源路径",
            "复制源路径",
        )
        self.source_observation_value = QLabel("")
        self.source_observation_value.setObjectName("pageDescription")
        operation_detail_layout.addWidget(self.source_observation_value)
        self.source_observation_diagnostic = QLabel("")
        self.source_observation_diagnostic.setObjectName("pageDescription")
        self.source_observation_diagnostic.setWordWrap(True)
        operation_detail_layout.addWidget(self.source_observation_diagnostic)
        self.target_path_value, self.copy_target_path_button = self._add_path_detail_row(
            operation_detail_layout,
            "目标路径",
            "复制目标路径",
        )
        self.target_observation_value = QLabel("")
        self.target_observation_value.setObjectName("pageDescription")
        operation_detail_layout.addWidget(self.target_observation_value)
        self.target_observation_diagnostic = QLabel("")
        self.target_observation_diagnostic.setObjectName("pageDescription")
        self.target_observation_diagnostic.setWordWrap(True)
        operation_detail_layout.addWidget(self.target_observation_diagnostic)
        self.execution_error_value = self._add_error_detail_row(
            operation_detail_layout,
            "执行错误",
        )
        self.undo_error_value = self._add_error_detail_row(
            operation_detail_layout,
            "撤销错误",
        )
        history_layout.addWidget(self.operation_detail_section)
        self.operation_detail_section.hide()

        history_layout.addWidget(_section_divider())
        history_layout.addWidget(self.technical_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        history_layout.addWidget(self.technical_section)

        history_layout.addStretch(1)
        self.detail_layout.addStretch(1)
        content.addWidget(self.detail_scroll)
        content.setStretchFactor(0, 0)
        content.setStretchFactor(1, 1)
        content.setSizes([248, 860])
        layout.addWidget(content, 1)
        for first, second in (
            (self.transaction_list, self.newer_button),
            (self.newer_button, self.older_button),
            (self.older_button, self.refresh_button),
            (self.refresh_button, self.copy_root_path_button),
            (self.copy_root_path_button, self.observe_button),
            (self.observe_button, self.operations_table),
            (self.operations_table, self.source_path_value),
            (self.source_path_value, self.copy_source_path_button),
            (self.copy_source_path_button, self.target_path_value),
            (self.target_path_value, self.copy_target_path_button),
            (self.copy_target_path_button, self.technical_toggle),
            (self.technical_toggle, self.transaction_id_value),
            (self.transaction_id_value, self.copy_transaction_id_button),
        ):
            self.setTabOrder(first, second)

    @staticmethod
    def _add_path_detail_row(
        parent_layout: QVBoxLayout,
        label_text: str,
        copy_text: str,
    ) -> tuple[_ElidedPathLabel, QPushButton]:
        group = QWidget()
        group_layout = QVBoxLayout(group)
        group_layout.setContentsMargins(0, 0, 0, 0)
        group_layout.setSpacing(4)
        row = QHBoxLayout()
        label = QLabel(label_text)
        label.setObjectName("pageDescription")
        group_layout.addWidget(label)
        field = _ElidedPathLabel()
        button = QPushButton(copy_text)
        button.setStyleSheet("QPushButton { min-height: 24px; padding: 2px 8px; }")
        button.setAccessibleName(copy_text)
        button.clicked.connect(lambda: QApplication.clipboard().setText(field.full_path))
        field.setMaximumWidth(560)
        row.addWidget(field)
        row.addWidget(button)
        row.addStretch(1)
        group_layout.addLayout(row)
        parent_layout.addWidget(group)
        return field, button

    @staticmethod
    def _add_error_detail_row(
        parent_layout: QVBoxLayout,
        label_text: str,
    ) -> QPlainTextEdit:
        group = QWidget()
        row = QVBoxLayout(group)
        row.setContentsMargins(0, 0, 0, 0)
        label = QLabel(label_text)
        value = QPlainTextEdit()
        value.setReadOnly(True)
        value.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        value.setMaximumHeight(88)
        value.setMinimumHeight(42)
        group.hide()
        row.addWidget(label)
        row.addWidget(value)
        parent_layout.addWidget(group)
        return value

    @staticmethod
    def _configure_operations_table(table: QTableView) -> None:
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setWordWrap(False)
        table.setSortingEnabled(False)
        table.setAlternatingRowColors(False)
        table.setShowGrid(False)
        table.setFrameShape(QFrame.Shape.NoFrame)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setMinimumHeight(90)
        table.setMaximumHeight(320)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        table.verticalHeader().setDefaultSectionSize(54)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        metrics = table.fontMetrics()
        for column, labels in (
            (
                1,
                (
                    "重命名",
                    "成功",
                    "失败",
                    "预检失败",
                    "部分成功",
                    "未执行",
                    "需检查",
                ),
            ),
            (2, ("撤销", "不适用", "未撤销", "已撤销", "部分撤销", "失败", "冲突")),
        ):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            status_width = max(metrics.horizontalAdvance(label) for label in labels) + 24
            table.setColumnWidth(column, status_width)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        table.setItemDelegate(_OperationRowDelegate(table))

    def _resize_operations_table(self) -> None:
        table = self.operations_table
        row_count = self.operations_model.rowCount()
        sequence_text = str(max(1, row_count))
        sequence_width = (
            max(
                table.fontMetrics().horizontalAdvance("#"),
                table.fontMetrics().horizontalAdvance(sequence_text),
            )
            + 16
        )
        table.setColumnWidth(0, sequence_width)
        visible_rows = min(row_count, 5)
        header_height = table.horizontalHeader().sizeHint().height()
        row_height = table.verticalHeader().defaultSectionSize()
        frame_height = table.frameWidth() * 2 + 4
        height = max(90, header_height + visible_rows * row_height + frame_height)
        table.setFixedHeight(min(height, 320))

    @Slot()
    def refresh_history(self) -> None:
        """Reload the visible journal page while keeping a still-valid selection."""
        preserved_id = self._selected_id
        if self._history_service is None:
            self.page_label.setText("无法加载")
            self._show_empty_detail("历史暂不可用", "事务日志未初始化。")
            return
        try:
            transactions = self._history_service.list_recent(
                limit=self.PAGE_SIZE + 1,
                offset=self._offset,
            )
        except Exception as exc:
            self.page_label.setText("读取失败")
            if self._selected_transaction is None:
                self._show_empty_detail("无法读取重命名历史", _short_error(str(exc)))
            else:
                self.query_status_label.setText(f"无法读取重命名历史：{_short_error(str(exc))}")
                self.query_status_label.show()
            return

        self.query_status_label.clear()
        self.query_status_label.hide()
        has_older_page = len(transactions) > self.PAGE_SIZE
        transactions = transactions[: self.PAGE_SIZE]
        was_blocked = self.transaction_list.blockSignals(True)
        try:
            self.transaction_list.clear()
            for transaction in transactions:
                self._add_transaction_item(transaction)
        finally:
            self.transaction_list.blockSignals(was_blocked)

        self.transaction_stack.setCurrentWidget(
            self.transaction_list if transactions else self.master_empty_state
        )

        page_number = self._offset // self.PAGE_SIZE + 1
        self.page_label.setText(f"第 {page_number} 页 · {len(transactions)} 条")
        self.newer_button.setEnabled(self._offset > 0)
        self.older_button.setEnabled(has_older_page)
        if not transactions:
            self._selected_id = None
            self._selected_transaction = None
            self._show_empty_detail(
                "暂无重命名历史",
                "完成一次重命名后，事务记录会显示在这里。",
            )
            return

        selected_item = self._find_item(preserved_id)
        if selected_item is not None:
            self.transaction_list.setCurrentItem(selected_item)
            return

        if preserved_id is not None:
            try:
                transaction = self._history_service.get_transaction(preserved_id)
            except Exception:
                self._selected_id = None
                self._selected_transaction = None
            else:
                self.transaction_list.setCurrentRow(-1)
                self.page_label.setText(f"第 {page_number} 页 · 所选事务在其他页")
                self._show_transaction(transaction)
                return

        self.transaction_list.setCurrentItem(self.transaction_list.item(0))

    @Slot()
    def refresh_after_mutation(self) -> None:
        """Refresh journal facts without treating the signal as filesystem truth."""
        self.refresh_history()

    def open_transaction(self, transaction_id: str, *, refresh: bool = True) -> None:
        """Open a journal transaction directly, including one outside page one."""
        self._offset = 0
        if refresh:
            self.refresh_history()
        if self._history_service is None:
            return
        item = self._find_item(transaction_id)
        if item is not None:
            self.transaction_list.setCurrentItem(item)
            return
        try:
            transaction = self._history_service.get_transaction(transaction_id)
        except Exception as exc:
            self._show_transaction_error(transaction_id, exc)
            return
        self.transaction_list.setCurrentRow(-1)
        self._show_transaction(transaction)

    @Slot()
    def refresh_observations(self) -> None:
        """Recheck the selected transaction's recorded paths without changing facts."""
        transaction = self._selected_transaction
        if transaction is None or self._history_service is None:
            return
        try:
            observations = self._history_service.observe_paths(transaction)
        except Exception as exc:
            error = _short_error(str(exc))
            observations = tuple(
                OperationPathObservations(
                    sequence=operation.sequence,
                    source=CurrentPathObservation(
                        operation.source_path,
                        CurrentPathState.ERROR,
                        error,
                    ),
                    target=CurrentPathObservation(
                        operation.target_path,
                        CurrentPathState.ERROR,
                        error,
                    ),
                )
                for operation in transaction.operations
            )

        by_sequence = {item.sequence: item for item in observations}
        completed: dict[int, OperationPathObservations] = {}
        for operation in transaction.operations:
            observation = by_sequence.get(operation.sequence)
            if observation is None:
                error_observation = CurrentPathObservation(
                    operation.source_path,
                    CurrentPathState.ERROR,
                    "当前观察没有返回此操作的结果",
                )
                observation = OperationPathObservations(
                    sequence=operation.sequence,
                    source=error_observation,
                    target=CurrentPathObservation(
                        operation.target_path,
                        CurrentPathState.ERROR,
                        "当前观察没有返回此操作的结果",
                    ),
                )
            completed[operation.sequence] = observation
        self._observations_by_sequence = completed
        self.operations_model.set_observations(completed)

        error_details: list[str] = []
        target_present = 0
        target_absent = 0
        target_unknown = 0
        for observation in completed.values():
            for path_observation in (observation.source, observation.target):
                if path_observation.state is CurrentPathState.ERROR:
                    error_details.append(path_observation.error or "访问失败")
            if observation.target.state is CurrentPathState.ABSENT:
                target_absent += 1
            elif observation.target.state is CurrentPathState.ERROR:
                target_unknown += 1
            else:
                target_present += 1

        observed_at = datetime.now().astimezone()
        self.observation_updated_label.setText(
            f"最近观察：{_format_local_wall_time(observed_at)}"
        )
        self.observation_updated_label.setAccessibleDescription(
            f"当前文件系统路径观察时间：{observed_at.isoformat(timespec='microseconds')}"
        )
        if len(transaction.operations) > 1:
            self.observation_summary.setText(
                f"目标路径：{len(transaction.operations)} 项 · "
                + " · ".join(
                    f"{count} 个{label}"
                    for count, label in (
                        (target_present, "当前存在"),
                        (target_absent, "不存在"),
                        (target_unknown, "无法确认"),
                    )
                    if count
                )
            )
            self.observation_summary.show()
        else:
            self.observation_summary.clear()
            self.observation_summary.hide()
        if error_details:
            self.observation_status_banner.show_status(
                BannerState.WARNING,
                "当前状态暂时无法确认",
                f"{len(error_details)} 条路径无法确认。{_short_error(error_details[0])}",
            )
        else:
            self.observation_status_banner.clear_status()
        current = self.operations_table.currentIndex()
        if current.isValid():
            operation = self.operations_model.operation_at(current.row())
            if operation is not None:
                self._show_operation_detail_for(operation)

    @Slot(QListWidgetItem, QListWidgetItem)
    def _selection_changed(
        self, current: QListWidgetItem | None, _previous: QListWidgetItem | None
    ) -> None:
        if current is None:
            return
        transaction_id = current.data(self._TRANSACTION_ID_ROLE)
        if isinstance(transaction_id, str):
            self._load_transaction(transaction_id)

    def _load_transaction(self, transaction_id: str) -> None:
        if self._history_service is None:
            return
        try:
            transaction = self._history_service.get_transaction(transaction_id)
        except Exception as exc:
            self._show_transaction_error(transaction_id, exc)
            return
        self._show_transaction(transaction)

    def _show_transaction(self, transaction: RenameTransaction) -> None:
        previous_id = self._selected_id
        current = self.operations_table.currentIndex()
        previous_sequence = None
        if current.isValid() and self._selected_id == transaction.transaction_id:
            previous_operation = self.operations_model.operation_at(current.row())
            previous_sequence = previous_operation.sequence if previous_operation else None
        self._selected_id = transaction.transaction_id
        self._selected_transaction = transaction
        self.query_status_label.clear()
        self.query_status_label.hide()
        self.empty_state.hide()
        self.history_detail.show()
        self.detail_heading.setText("事务详情")
        self.technical_toggle.setEnabled(True)
        self.operations_heading.show()
        self.operations_table.show()
        self.transaction_timestamp.setText(_format_local_wall_time(transaction.created_at))
        self.transaction_timestamp.setAccessibleDescription(
            f"事务创建时间：{_format_datetime(transaction.created_at)}"
        )
        status = _transaction_status_text(transaction.display_status)
        self.transaction_summary.setText(f"{len(transaction.operations)} 项 · {status}")
        self._render_journal_facts(transaction)
        self.root_path_value.set_path(str(transaction.root))
        self._render_recovery(transaction)
        self._update_technical_details(transaction)
        self._observations_by_sequence = {}
        self._render_operations(
            transaction,
            preserve_sequence=(
                previous_sequence if previous_id == transaction.transaction_id else None
            ),
        )
        self.refresh_observations()
        if self.technical_toggle.isChecked():
            self.technical_section.show()

    def _render_journal_facts(self, transaction: RenameTransaction) -> None:
        success_count = sum(
            operation.status is ExecutionStatus.SUCCESS for operation in transaction.operations
        )
        failed_count = sum(
            operation.status is ExecutionStatus.FAILED for operation in transaction.operations
        )
        precondition_failed_count = sum(
            operation.status is ExecutionStatus.PRECONDITION_FAILED
            for operation in transaction.operations
        )
        rejected_count = sum(
            operation.status is ExecutionStatus.REJECTED for operation in transaction.operations
        )
        pending_count = sum(
            operation.status is ExecutionStatus.PENDING for operation in transaction.operations
        )
        recovery_required_count = sum(
            operation.status is ExecutionStatus.RECOVERY_REQUIRED
            for operation in transaction.operations
        )
        not_run_count = sum(
            operation.status is ExecutionStatus.NOT_EXECUTED for operation in transaction.operations
        )
        undo_success_count = sum(
            operation.undo_status is UndoStatus.SUCCESS for operation in transaction.operations
        )
        undo_failed_count = sum(
            operation.undo_status is UndoStatus.FAILED for operation in transaction.operations
        )
        undo_conflict_count = sum(
            operation.undo_status is UndoStatus.CONFLICT for operation in transaction.operations
        )
        undo_waiting_count = sum(
            operation.status is ExecutionStatus.SUCCESS
            and operation.undo_status is UndoStatus.PENDING
            for operation in transaction.operations
        )
        forward = (
            (success_count, "成功"),
            (failed_count, "失败"),
            (precondition_failed_count, "预检失败"),
            (rejected_count, "拒绝"),
            (not_run_count, "未执行"),
            (pending_count, "待处理"),
            (recovery_required_count, "需检查"),
        )
        undo = (
            (undo_success_count, "成功"),
            (undo_failed_count, "失败"),
            (undo_conflict_count, "冲突"),
            (undo_waiting_count, "尚未撤销"),
        )
        undo_summary = " · ".join(f"{count} 项{label}" for count, label in undo if count)
        undo_state = _undo_transaction_text(transaction)
        if transaction.undo_state is None and transaction.status not in {
            TransactionStatus.UNDONE,
            TransactionStatus.UNDO_PARTIAL,
        }:
            undo_state = ""
        facts = [
            ("事务状态", _transaction_status_text(transaction.display_status)),
            (
                "重命名",
                " · ".join(f"{count} 项{label}" for count, label in forward if count) or "无操作",
            ),
            (
                "撤销",
                " · ".join(value for value in (undo_state, undo_summary) if value)
                or "尚无撤销记录",
            ),
            ("记录时间", _format_local_wall_time(transaction.created_at)[:16]),
        ]
        if transaction.completed_at is not None:
            facts.append(("完成时间", _format_local_wall_time(transaction.completed_at)[:16]))
        if transaction.undo_completed_at is not None:
            facts.append(("撤销完成", _format_local_wall_time(transaction.undo_completed_at)[:16]))
        self.journal_facts_label.set_facts(facts)

    def _render_recovery(self, transaction: RenameTransaction) -> None:
        has_recovery_info = any(
            value is not None
            for value in (
                transaction.recovery_stage,
                transaction.recovery_sequence,
                transaction.recovery_error,
            )
        )
        if transaction.unresolved:
            stage = "撤销" if transaction.recovery_stage == "undo" else "重命名执行"
            self.recovery_banner.show_status(
                BannerState.BLOCKED,
                "事务需要检查",
                (
                    f"{stage}记录尚未确认完成。当前路径观察仅作参考，不会改写记录结果。"
                    "整理页会继续阻止新的重命名和普通撤销。"
                ),
            )
        else:
            self.recovery_banner.clear_status()
        if not has_recovery_info and not transaction.unresolved:
            self.recovery_label.clear()
            self.recovery_label.hide()
            return
        details = [
            f"恢复阶段：{_recovery_stage_text(transaction.recovery_stage)}",
            f"操作序号：{_recovery_sequence_text(transaction.recovery_sequence)}",
        ]
        if transaction.recovery_error:
            details.append(f"记录的恢复说明：{_short_error(transaction.recovery_error)}")
        if transaction.undo_state is not None and transaction.undo_state.unresolved:
            details.append(
                f"撤销尚无持久化终态；此前事务记录状态为："
                f"{_transaction_status_text(transaction.status)}"
            )
        self.recovery_label.setText("\n".join(details))
        self.recovery_label.show()

    def _render_operations(
        self,
        transaction: RenameTransaction,
        *,
        preserve_sequence: int | None = None,
    ) -> None:
        self._operations = transaction.operations
        table = self.operations_table
        table.blockSignals(True)
        self.operations_model.set_operations(self._operations)
        self._resize_operations_table()
        preserved_row = 0
        if self._operations:
            preserved_row = next(
                (
                    row
                    for row, operation in enumerate(self._operations)
                    if operation.sequence == preserve_sequence
                ),
                0,
            )
            table.setCurrentIndex(self.operations_model.index(preserved_row, 0))
            table.selectRow(preserved_row)
        else:
            table.clearSelection()
        table.blockSignals(False)
        if self._operations:
            self._show_operation_detail_for(self._operations[preserved_row])
        else:
            self._clear_operation_detail()

    def _operation_selection_changed(
        self, current: QModelIndex, _previous: QModelIndex
    ) -> None:
        self._show_operation_detail(current.row())

    def _show_operation_detail(self, row: int) -> None:
        operation = self.operations_model.operation_at(row)
        if operation is None:
            self._clear_operation_detail()
            return
        self._show_operation_detail_for(operation)

    def _show_operation_detail_for(self, operation: RenameOperation) -> None:
        self.operation_sequence_label.setText(f"操作 {operation.sequence}")
        self.operation_status_label.set_facts(
            [
                ("重命名", _execution_overview_text(operation.status)),
                ("撤销", _undo_overview_text(operation)),
            ]
        )
        self.source_path_value.set_path(str(operation.source_path))
        self.target_path_value.set_path(str(operation.target_path))
        observation = self._observations_by_sequence.get(operation.sequence)
        self._set_selected_path_observation(
            self.source_observation_value,
            self.source_observation_diagnostic,
            observation.source if observation else None,
        )
        self._set_selected_path_observation(
            self.target_observation_value,
            self.target_observation_diagnostic,
            observation.target if observation else None,
        )
        self.execution_error_value.setPlainText(operation.error or "")
        execution_group = self.execution_error_value.parentWidget()
        assert execution_group is not None
        execution_group.setVisible(bool(operation.error))
        self.undo_error_value.setPlainText(operation.undo_error or "")
        undo_group = self.undo_error_value.parentWidget()
        assert undo_group is not None
        undo_group.setVisible(bool(operation.undo_error))
        self.operation_detail_section.show()
        self._update_technical_operation(operation)

    @staticmethod
    def _set_selected_path_observation(
        label: QLabel,
        diagnostic: QLabel,
        observation: CurrentPathObservation | None,
    ) -> None:
        label.setText(_observation_text(observation) if observation else "当前：无法观察")
        detail = (
            observation.error
            if observation and observation.state is CurrentPathState.ERROR
            else ""
        )
        diagnostic.setText(_short_error(detail) if detail else "")
        diagnostic.setVisible(bool(detail))

    def _clear_operation_detail(self) -> None:
        self.operation_sequence_label.clear()
        self.operation_status_label.clear()
        self.source_path_value.set_path("")
        self.target_path_value.set_path("")
        self.source_observation_value.clear()
        self.source_observation_diagnostic.clear()
        self.source_observation_diagnostic.hide()
        self.target_observation_value.clear()
        self.target_observation_diagnostic.clear()
        self.target_observation_diagnostic.hide()
        self.execution_error_value.clear()
        self.undo_error_value.clear()
        self.operation_detail_section.hide()
        self.technical_operation_label.clear()

    def _update_technical_details(self, transaction: RenameTransaction) -> None:
        self.transaction_id_value.setText(transaction.transaction_id)
        self.technical_facts_label.setText(
            "\n".join(
                (
                    f"原始事务状态：{transaction.status.value}",
                    (
                        f"撤销状态：{transaction.undo_state.value}"
                        if transaction.undo_state
                        else "撤销状态：未记录"
                    ),
                    f"创建时间（精确）：{transaction.created_at.isoformat()}",
                    (
                        f"完成时间（精确）：{transaction.completed_at.isoformat()}"
                        if transaction.completed_at
                        else "完成时间（精确）：未记录"
                    ),
                    (
                        f"撤销开始时间（精确）：{transaction.undo_started_at.isoformat()}"
                        if transaction.undo_started_at
                        else "撤销开始时间（精确）：未记录"
                    ),
                    (
                        f"撤销完成时间（精确）：{transaction.undo_completed_at.isoformat()}"
                        if transaction.undo_completed_at
                        else "撤销完成时间（精确）：未记录"
                    ),
                )
            )
        )
        self._update_technical_operation(None)

    def _update_technical_operation(self, operation: RenameOperation | None) -> None:
        if operation is None:
            self.technical_operation_label.setText(
                "选择操作后，可查看其 journal operation ID 与原始状态。"
            )
            return
        operation_id = (
            str(operation.operation_id) if operation.operation_id is not None else "未记录"
        )
        self.technical_operation_label.setText(
            "\n".join(
                (
                    f"操作序号：{operation.sequence}",
                    f"Journal operation ID：{operation_id}",
                    f"原始执行状态：{operation.status.value}",
                    f"原始撤销状态：{operation.undo_status.value}",
                    (
                        f"执行时间（精确）：{operation.executed_at.isoformat()}"
                        if operation.executed_at
                        else "执行时间（精确）：未记录"
                    ),
                    (
                        f"撤销时间（精确）：{operation.undone_at.isoformat()}"
                        if operation.undone_at
                        else "撤销时间（精确）：未记录"
                    ),
                )
            )
        )

    @Slot(bool)
    def _toggle_technical_details(self, expanded: bool) -> None:
        self.technical_section.setVisible(expanded)
        self.technical_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        action = "收起" if expanded else "展开"
        self.technical_toggle.setAccessibleDescription(f"{action}事务 ID、原始状态和精确时间")

    def _show_transaction_error(self, transaction_id: str, error: Exception) -> None:
        self._selected_id = None
        self._selected_transaction = None
        self.transaction_list.setCurrentRow(-1)
        self.empty_state.hide()
        self.history_detail.show()
        self.detail_heading.setText("事务详情不可用")
        self.query_status_label.setText(f"无法读取事务详情：{_short_error(str(error))}")
        self.query_status_label.show()
        self.transaction_timestamp.clear()
        self.transaction_summary.clear()
        self.journal_facts_label.clear()
        self.root_path_value.set_path("")
        self.recovery_banner.clear_status()
        self.recovery_label.clear()
        self.recovery_label.hide()
        self.observation_status_banner.clear_status()
        self.observation_summary.clear()
        self.observation_summary.hide()
        self.observation_updated_label.clear()
        self.technical_toggle.setChecked(False)
        self.technical_toggle.setEnabled(False)
        self.transaction_id_value.setText(transaction_id)
        self.technical_facts_label.clear()
        self.technical_operation_label.clear()
        self.operations_heading.hide()
        self.operations_table.hide()
        self.operations_model.set_operations(())
        self.operations_model.set_observations({})
        self._operations = ()
        self._observations_by_sequence = {}
        self._clear_operation_detail()

    def _add_transaction_item(
        self,
        transaction: RenameTransactionSummary,
    ) -> QListWidgetItem:
        local_time = _format_local_wall_time(transaction.created_at)
        status = _transaction_status_text(transaction.display_status)
        count = transaction.operation_count
        accessible_text = f"{local_time}，{count} 项，{status}"
        item = QListWidgetItem(f"{local_time}\n{count} 项 · {status}")
        item.setSizeHint(QSize(0, 58))
        item.setData(self._TRANSACTION_ID_ROLE, transaction.transaction_id)
        item.setData(self._MASTER_STATUS_ROLE, transaction.display_status)
        item.setData(Qt.ItemDataRole.AccessibleTextRole, accessible_text)
        item.setData(Qt.ItemDataRole.AccessibleDescriptionRole, accessible_text)
        self.transaction_list.addItem(item)
        return item

    def _find_item(self, transaction_id: str | None) -> QListWidgetItem | None:
        if transaction_id is None:
            return None
        for index in range(self.transaction_list.count()):
            item = self.transaction_list.item(index)
            if item.data(self._TRANSACTION_ID_ROLE) == transaction_id:
                return item
        return None

    def _show_empty_detail(self, title: str, description: str) -> None:
        self._selected_id = None
        self._selected_transaction = None
        self.empty_title.setText(title)
        self.empty_description.setText(description)
        self.empty_state.show()
        self.history_detail.hide()
        self.transaction_summary.clear()
        self.transaction_timestamp.clear()
        self.root_path_value.set_path("")
        self.transaction_id_value.clear()
        self.journal_facts_label.clear()
        self.recovery_label.clear()
        self.recovery_label.hide()
        self.recovery_banner.clear_status()
        self.observation_summary.clear()
        self.observation_summary.hide()
        self.observation_updated_label.clear()
        self.observation_status_banner.clear_status()
        self.technical_toggle.setChecked(False)
        self.technical_toggle.setEnabled(False)
        self.technical_facts_label.clear()
        self.technical_operation_label.clear()
        self.operations_heading.show()
        self.operations_table.show()
        self.operations_model.set_operations(())
        self.operations_model.set_observations({})
        self._operations = ()
        self._observations_by_sequence = {}
        self._clear_operation_detail()

    @Slot()
    def _show_older_page(self) -> None:
        self._offset += self.PAGE_SIZE
        self._selected_id = None
        self.refresh_history()

    @Slot()
    def _show_newer_page(self) -> None:
        self._offset = max(0, self._offset - self.PAGE_SIZE)
        self._selected_id = None
        self.refresh_history()


def _section_title(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("sectionLabel")
    return label


def _section_divider() -> QFrame:
    divider = QFrame()
    divider.setFrameShape(QFrame.Shape.HLine)
    divider.setFrameShadow(QFrame.Shadow.Plain)
    divider.setFixedHeight(1)
    divider.setStyleSheet(f"border: 0; background: {LIGHT_COLORS.border};")
    return divider


def _local_datetime(value: datetime, local_timezone: tzinfo | None = None) -> datetime:
    timestamp = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return (
        timestamp.astimezone(local_timezone)
        if local_timezone is not None
        else timestamp.astimezone()
    )


def _format_local_wall_time(value: datetime, local_timezone: tzinfo | None = None) -> str:
    local = _local_datetime(value, local_timezone)
    return f"{local:%Y-%m-%d %H:%M:%S}"


def _format_datetime(value: datetime, local_timezone: tzinfo | None = None) -> str:
    local = _local_datetime(value, local_timezone)
    offset = local.utcoffset() or timedelta(0)
    offset_minutes = int(offset.total_seconds() // 60)
    sign = "+" if offset_minutes >= 0 else "-"
    hours, minutes = divmod(abs(offset_minutes), 60)
    return f"{local:%Y-%m-%d %H:%M:%S} (UTC{sign}{hours:02d}:{minutes:02d})"


def _recovery_sequence_text(sequence: int | None) -> str:
    return str(sequence) if sequence is not None else "未记录"


def _recovery_stage_text(stage: str | None) -> str:
    if stage is None:
        return "未记录"
    return {"forward": "重命名执行", "undo": "撤销"}.get(stage, "未记录")


def _short_error(value: str, *, limit: int = 240) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else f"{compact[: limit - 1]}…"


def _undo_transaction_text(transaction: RenameTransaction) -> str:
    state = transaction.undo_state
    if state is None:
        if transaction.status is TransactionStatus.UNDONE:
            return "已完成（旧版记录）"
        if transaction.status is TransactionStatus.UNDO_PARTIAL:
            return "部分完成（旧版记录）"
        return "尚无撤销记录"
    return {
        UndoTransactionState.IN_PROGRESS: "进行中",
        UndoTransactionState.RECOVERY_REQUIRED: "需要检查",
        UndoTransactionState.SUCCESS: "已完成",
        UndoTransactionState.PARTIAL: "部分完成",
    }[state]


def _transaction_status_text(status: TransactionStatus) -> str:
    return {
        TransactionStatus.PENDING: "进行中",
        TransactionStatus.COMPLETED: "已完成",
        TransactionStatus.PARTIAL: "部分完成",
        TransactionStatus.FAILED: "失败",
        TransactionStatus.UNDONE: "已撤销",
        TransactionStatus.UNDO_PARTIAL: "部分撤销",
        TransactionStatus.RECOVERY_REQUIRED: "需要检查",
    }[status]


def _transaction_status_icon(status: TransactionStatus) -> QStyle.StandardPixmap:
    return {
        TransactionStatus.PENDING: QStyle.StandardPixmap.SP_BrowserReload,
        TransactionStatus.COMPLETED: QStyle.StandardPixmap.SP_DialogApplyButton,
        TransactionStatus.PARTIAL: QStyle.StandardPixmap.SP_MessageBoxWarning,
        TransactionStatus.FAILED: QStyle.StandardPixmap.SP_MessageBoxCritical,
        TransactionStatus.UNDONE: QStyle.StandardPixmap.SP_DialogResetButton,
        TransactionStatus.UNDO_PARTIAL: QStyle.StandardPixmap.SP_MessageBoxWarning,
        TransactionStatus.RECOVERY_REQUIRED: QStyle.StandardPixmap.SP_MessageBoxWarning,
    }[status]


def _execution_overview_text(status: ExecutionStatus) -> str:
    return {
        ExecutionStatus.PENDING: "待处理",
        ExecutionStatus.SUCCESS: "成功",
        ExecutionStatus.FAILED: "失败",
        ExecutionStatus.NOT_EXECUTED: "未执行",
        ExecutionStatus.PRECONDITION_FAILED: "预检失败",
        ExecutionStatus.REJECTED: "拒绝",
        ExecutionStatus.RECOVERY_REQUIRED: "需检查",
    }[status]


def _undo_overview_text(operation: RenameOperation) -> str:
    if operation.undo_status is UndoStatus.PENDING:
        return "未撤销" if operation.status is ExecutionStatus.SUCCESS else "不适用"
    return {
        UndoStatus.SUCCESS: "已撤销",
        UndoStatus.FAILED: "失败",
        UndoStatus.CONFLICT: "冲突",
        UndoStatus.PENDING: "未撤销",
    }[operation.undo_status]


def _observation_text(observation: CurrentPathObservation | None) -> str:
    if observation is None:
        return "当前：无法观察"
    label = {
        CurrentPathState.DIRECTORY: "存在 · 目录",
        CurrentPathState.FILE: "存在 · 文件",
        CurrentPathState.OTHER: "存在 · 其他类型",
        CurrentPathState.LINK_LIKE: "存在 · 链接类对象",
        CurrentPathState.ABSENT: "不存在",
        CurrentPathState.ERROR: "无法观察",
    }[observation.state]
    return f"当前：{label}"


def _is_unresolved(transaction: RenameTransaction) -> bool:
    return transaction.unresolved


class _DisclosureButton(QToolButton):
    """Support both native Space activation and Enter for disclosure."""

    def keyPressEvent(self, event) -> None:
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            self.click()
            event.accept()
        else:
            super().keyPressEvent(event)


class _FactGrid(QWidget):
    """Small selectable label/value facts, without form controls or borders."""

    def __init__(self) -> None:
        super().__init__()
        self._text = ""
        self._layout = QGridLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setHorizontalSpacing(20)
        self._layout.setVerticalSpacing(5)
        self._layout.setColumnStretch(1, 1)
        self.setMaximumWidth(560)

        self._rows: list[tuple[QLabel, QLabel]] = []
        for row in range(6):
            key = QLabel()
            key.setObjectName("pageDescription")
            text = QLabel()
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self._layout.addWidget(key, row, 0, Qt.AlignmentFlag.AlignTop)
            self._layout.addWidget(text, row, 1)
            self._rows.append((key, text))
        self.clear()

    def set_facts(self, facts: list[tuple[str, str]]) -> None:
        self.clear()
        self._text = "\n".join(f"{label}：{value}" for label, value in facts)
        self.setAccessibleDescription(self._text)
        for (key, text), (label, value) in zip(self._rows, facts, strict=False):
            key.setText(label)
            text.setText(value)
            key.show()
            text.show()

    def text(self) -> str:
        return self._text

    def clear(self) -> None:
        self._text = ""
        for key, text in self._rows:
            key.clear()
            text.clear()
            key.hide()
            text.hide()
        self.setAccessibleDescription("")


def _durable_status_color(operation: RenameOperation, column: int) -> QColor:
    if column == 1:
        color = {
            ExecutionStatus.SUCCESS: LIGHT_COLORS.success,
            ExecutionStatus.FAILED: LIGHT_COLORS.error,
            ExecutionStatus.PRECONDITION_FAILED: LIGHT_COLORS.warning,
            ExecutionStatus.REJECTED: LIGHT_COLORS.warning,
            ExecutionStatus.RECOVERY_REQUIRED: LIGHT_COLORS.warning,
        }.get(operation.status, LIGHT_COLORS.text_secondary)
    else:
        color = {
            UndoStatus.SUCCESS: LIGHT_COLORS.success,
            UndoStatus.FAILED: LIGHT_COLORS.error,
            UndoStatus.CONFLICT: LIGHT_COLORS.warning,
        }.get(operation.undo_status, LIGHT_COLORS.text_secondary)
    return QColor(color)


def _master_status_visual(status: TransactionStatus) -> tuple[str, str]:
    """UI-only shape/color mapping of the existing durable projection."""
    return {
        TransactionStatus.PENDING: ("pending", LIGHT_COLORS.text_secondary),
        TransactionStatus.COMPLETED: ("success", LIGHT_COLORS.success),
        TransactionStatus.PARTIAL: ("partial", LIGHT_COLORS.warning),
        TransactionStatus.FAILED: ("error", LIGHT_COLORS.error),
        TransactionStatus.UNDONE: ("reversed", LIGHT_COLORS.text_secondary),
        TransactionStatus.UNDO_PARTIAL: ("partial", LIGHT_COLORS.warning),
        TransactionStatus.RECOVERY_REQUIRED: ("attention", LIGHT_COLORS.warning),
    }[status]


def _undo_arrow_geometry() -> tuple[QPainterPath, QPointF, tuple[QPointF, QPointF]]:
    """Return a curved undo shaft and tangent-aligned wings in one logical space."""
    tip = QPointF(2.6, 4.2)
    first_control = QPointF(4.8, 4.5)
    shaft = QPainterPath(tip)
    shaft.cubicTo(first_control, QPointF(11.8, 2.2), QPointF(11.5, 7.2))
    shaft.cubicTo(QPointF(11.2, 11.0), QPointF(7.2, 12.2), QPointF(4.6, 10.0))

    tangent_x = tip.x() - first_control.x()
    tangent_y = tip.y() - first_control.y()
    tangent_length = hypot(tangent_x, tangent_y)
    direction_x = tangent_x / tangent_length
    direction_y = tangent_y / tangent_length
    wing_length = 3.8
    wing_angle = radians(29)
    wings = tuple(
        QPointF(
            tip.x()
            - (direction_x * cos(angle) - direction_y * sin(angle)) * wing_length,
            tip.y()
            - (direction_x * sin(angle) + direction_y * cos(angle)) * wing_length,
        )
        for angle in (-wing_angle, wing_angle)
    )
    return shaft, tip, (wings[0], wings[1])


def _paint_master_status_mark(painter, rect: QRectF, shape: str, color: str) -> None:
    """Small vector marks using the application's rounded line-icon convention."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(rect.topLeft())
    painter.setPen(
        QPen(
            QColor(color),
            1.5,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
    )
    painter.setBrush(Qt.BrushStyle.NoBrush)
    if shape == "success":
        painter.drawPolyline(QPolygonF([QPointF(2, 7), QPointF(5.5, 10.5), QPointF(12, 3.5)]))
    elif shape == "reversed":
        shaft, tip, wings = _undo_arrow_geometry()
        painter.drawPath(shaft)
        for wing in wings:
            painter.drawLine(tip, wing)
    elif shape == "error":
        painter.drawLine(QPointF(3, 3), QPointF(11, 11))
        painter.drawLine(QPointF(11, 3), QPointF(3, 11))
    elif shape == "attention":
        painter.drawPolygon(QPolygonF([QPointF(7, 1.5), QPointF(13, 12), QPointF(1, 12)]))
        painter.drawLine(QPointF(7, 5), QPointF(7, 8))
        painter.drawPoint(QPointF(7, 10))
    else:
        painter.drawEllipse(QRectF(2, 2, 10, 10))
        if shape == "partial":
            painter.setBrush(QColor(color))
            painter.drawPie(QRectF(2, 2, 10, 10), 90 * 16, 180 * 16)
        else:
            painter.drawLine(QPointF(7, 4), QPointF(7, 7))
            painter.drawLine(QPointF(7, 7), QPointF(10, 8))
    painter.restore()


class _HistoryMasterDelegate(QStyledItemDelegate):
    """Compact two-line history entries with a local selection accent."""

    def paint(self, painter, option, index) -> None:
        painter.save()
        rect = option.rect.adjusted(0, 1, -1, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            painter.fillRect(
                rect, QColor(LIGHT_COLORS.selection if selected else LIGHT_COLORS.sidebar_hover)
            )
        if selected:
            painter.fillRect(
                rect.left(), rect.top() + 7, 3, rect.height() - 14, QColor(LIGHT_COLORS.accent)
            )
        lines = str(index.data(Qt.ItemDataRole.DisplayRole)).splitlines()
        font = option.font
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QColor(LIGHT_COLORS.text_primary))
        painter.drawText(
            rect.adjusted(12, 7, -8, -27),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
            lines[0] if lines else "",
        )
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QColor(LIGHT_COLORS.text_secondary))
        secondary = rect.adjusted(12, 29, -8, -5)
        count, separator, label = (lines[1] if len(lines) > 1 else "").partition(" · ")
        prefix = count + separator
        painter.drawText(
            secondary, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, prefix
        )
        status = index.data(RenameHistoryPage._MASTER_STATUS_ROLE)
        if status is not None:
            shape, color = _master_status_visual(TransactionStatus(status))
            left = secondary.left() + painter.fontMetrics().horizontalAdvance(prefix)
            mark = QRectF(left, secondary.center().y() - 7, 14, 14)
            _paint_master_status_mark(painter, mark, shape, color)
            painter.setPen(QColor(color))
            text_rect = secondary.adjusted(left - secondary.left() + 18, 0, 0, 0)
            shown = painter.fontMetrics().elidedText(
                label, Qt.TextElideMode.ElideRight, max(0, text_rect.width())
            )
            painter.drawText(
                text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, shown
            )
        if option.state & QStyle.StateFlag.State_HasFocus:
            painter.setPen(QColor(LIGHT_COLORS.focus))
            painter.drawLine(rect.bottomLeft(), rect.bottomRight())
        painter.restore()


_EMPTY_MODEL_INDEX = QModelIndex()


class _OperationTableModel(QAbstractTableModel):
    """Project each journal operation beside its sequence-matched observation."""

    _HEADERS = ("#", "重命名", "撤销", "来源", "→ 目标")

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._operations: tuple[RenameOperation, ...] = ()
        self._observations: dict[int, OperationPathObservations] = {}

    def rowCount(
        self, parent: QModelIndex | QPersistentModelIndex = _EMPTY_MODEL_INDEX
    ) -> int:
        return 0 if parent.isValid() else len(self._operations)

    def columnCount(
        self, parent: QModelIndex | QPersistentModelIndex = _EMPTY_MODEL_INDEX
    ) -> int:
        return 0 if parent.isValid() else len(self._HEADERS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> str | None:
        if (
            orientation is Qt.Orientation.Horizontal
            and role == Qt.ItemDataRole.DisplayRole
            and 0 <= section < len(self._HEADERS)
        ):
            return self._HEADERS[section]
        return None

    def data(
        self,
        index: QModelIndex | QPersistentModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if not index.isValid() or not (0 <= index.row() < len(self._operations)):
            return None
        operation = self._operations[index.row()]
        column = index.column()
        observation = self._observations.get(operation.sequence)
        if role == Qt.ItemDataRole.DisplayRole:
            if column == 0:
                return str(operation.sequence)
            if column == 1:
                return _execution_overview_text(operation.status)
            if column == 2:
                return _undo_overview_text(operation)
            path = operation.source_path if column == 3 else operation.target_path
            current = None
            if observation is not None:
                current = observation.source if column == 3 else observation.target
            return f"{path.name or path}\n{_observation_text(current)}"
        if role == Qt.ItemDataRole.AccessibleTextRole:
            if column in {3, 4}:
                path = operation.source_path if column == 3 else operation.target_path
                current = None
                if observation is not None:
                    current = observation.source if column == 3 else observation.target
                return f"{path}；{_observation_text(current)}"
            return self.data(index, Qt.ItemDataRole.DisplayRole)
        if role == Qt.ItemDataRole.ToolTipRole and column in {3, 4}:
            return str(operation.source_path if column == 3 else operation.target_path)
        if role == Qt.ItemDataRole.ForegroundRole:
            if column in {1, 2}:
                return QBrush(_durable_status_color(operation, column))
            if column == 0:
                return QBrush(QColor(LIGHT_COLORS.text_muted))
        return None

    def flags(self, index: QModelIndex | QPersistentModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable

    def operation_at(self, row: int) -> RenameOperation | None:
        return self._operations[row] if 0 <= row < len(self._operations) else None

    def set_operations(self, operations: tuple[RenameOperation, ...]) -> None:
        self.beginResetModel()
        self._operations = operations
        self._observations = {}
        self.endResetModel()

    def set_observations(
        self, observations: dict[int, OperationPathObservations]
    ) -> None:
        self._observations = observations
        if self._operations:
            self.dataChanged.emit(
                self.index(0, 3),
                self.index(len(self._operations) - 1, 4),
                [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole],
            )


class _OperationRowDelegate(QStyledItemDelegate):
    """Paint readable palette-aware row selection and elide overview paths."""

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        if option.text and index.column() in {1, 2, 3, 4}:
            option.text = option.fontMetrics.elidedText(
                option.text,
                Qt.TextElideMode.ElideMiddle,
                max(0, option.rect.width() - 16),
            )

    def paint(self, painter, option, index) -> None:
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        foreground = index.data(Qt.ItemDataRole.ForegroundRole)
        text_color = (
            foreground.color()
            if isinstance(foreground, QBrush)
            else option.palette.color(QPalette.ColorRole.Text)
        )
        painter.save()
        background = (
            _operation_selection_background(option.palette)
            if selected
            else option.palette.color(QPalette.ColorRole.Base)
        )
        painter.fillRect(option.rect, background)
        display = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if index.column() in {3, 4}:
            primary, _, secondary = display.partition("\n")
            text_rect = option.rect.adjusted(8, 5, -8, -4)
            primary_font = option.font
            primary_font.setWeight(QFont.Weight.Medium)
            painter.setFont(primary_font)
            painter.setPen(text_color)
            primary = option.fontMetrics.elidedText(
                primary,
                Qt.TextElideMode.ElideMiddle,
                max(0, text_rect.width()),
            )
            painter.drawText(
                QRectF(text_rect.left(), text_rect.top(), text_rect.width(), 22),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                primary,
            )
            painter.setFont(option.font)
            painter.setPen(QColor(LIGHT_COLORS.text_secondary))
            secondary = option.fontMetrics.elidedText(
                secondary,
                Qt.TextElideMode.ElideRight,
                max(0, text_rect.width()),
            )
            painter.drawText(
                QRectF(text_rect.left(), text_rect.top() + 23, text_rect.width(), 18),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                secondary,
            )
        else:
            painter.setFont(option.font)
            painter.setPen(text_color)
            text = option.fontMetrics.elidedText(
                display,
                Qt.TextElideMode.ElideMiddle,
                max(0, option.rect.width() - 16),
            )
            painter.drawText(
                option.rect.adjusted(8, 0, -8, 0),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                text,
            )
        painter.setPen(QColor(LIGHT_COLORS.border))
        painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        if selected and index.column() == 0:
            painter.fillRect(
                option.rect.left(),
                option.rect.top() + 4,
                3,
                max(0, option.rect.height() - 8),
                option.palette.color(QPalette.ColorRole.Highlight),
            )
        if option.state & QStyle.StateFlag.State_HasFocus:
            painter.setPen(QPen(option.palette.color(QPalette.ColorRole.Highlight)))
            painter.drawLine(option.rect.bottomLeft(), option.rect.bottomRight())
        painter.restore()


def _operation_selection_background(palette: QPalette) -> QColor:
    """Tint the palette base toward its highlight while keeping text contrast."""
    base = palette.color(QPalette.ColorRole.Base)
    highlight = palette.color(QPalette.ColorRole.Highlight)
    text = palette.color(QPalette.ColorRole.Text)
    base_contrast = _contrast_ratio(base, text)
    for strength in (0.24, 0.20, 0.16, 0.12, 0.08, 0.04):
        candidate = QColor(
            round(base.red() * (1 - strength) + highlight.red() * strength),
            round(base.green() * (1 - strength) + highlight.green() * strength),
            round(base.blue() * (1 - strength) + highlight.blue() * strength),
        )
        if _contrast_ratio(candidate, text) >= min(4.5, base_contrast):
            return candidate
    return base


def _contrast_ratio(first: QColor, second: QColor) -> float:
    def luminance(color: QColor) -> float:
        channels = (color.redF(), color.greenF(), color.blueF())
        linear = tuple(
            channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
            for channel in channels
        )
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    first_luminance = luminance(first)
    second_luminance = luminance(second)
    lighter, darker = max(first_luminance, second_luminance), min(first_luminance, second_luminance)
    return (lighter + 0.05) / (darker + 0.05)


class _ElidedPathLabel(QLabel):
    """Selectable read-only path text with a complete-path copy affordance nearby."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.full_path = ""
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setMinimumWidth(60)
        self.setAccessibleName("完整路径")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def set_path(self, path: str) -> None:
        self.full_path = path
        self.setAccessibleDescription(path)
        self.updateGeometry()
        self._update_elision()

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        hint.setWidth(min(560, max(60, self.fontMetrics().horizontalAdvance(self.full_path) + 8)))
        return hint

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._update_elision()

    def _update_elision(self) -> None:
        shown = self.fontMetrics().elidedText(
            self.full_path,
            Qt.TextElideMode.ElideMiddle,
            max(0, self.width() - 8),
        )
        self.setText(shown)
        self.setToolTip(self.full_path if shown != self.full_path else "")
