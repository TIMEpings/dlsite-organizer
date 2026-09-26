"""Read-only transaction history and recovery inspection page."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, tzinfo

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QBrush, QColor, QPalette, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
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
)
from dlsite_organizer.services.rename_history import (
    CurrentPathObservation,
    CurrentPathState,
    RenameHistoryService,
)


class RenameHistoryPage(QWidget):
    """Show persisted rename facts separately from current path observations."""

    PAGE_SIZE = 50
    _TRANSACTION_TIMESTAMP_SAMPLE = "YYYY-MM-DD HH:mm:ss"
    _TRANSACTION_ID_ROLE = Qt.ItemDataRole.UserRole

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
        self._build_ui()
        self.transaction_list.currentItemChanged.connect(self._selection_changed)
        self.operations_table.currentCellChanged.connect(self._operation_selection_changed)
        self.refresh_button.clicked.connect(self.refresh_history)
        self.newer_button.clicked.connect(self._show_newer_page)
        self.older_button.clicked.connect(self._show_older_page)
        self.observe_button.clicked.connect(self.refresh_observations)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 28, 36, 28)
        layout.setSpacing(14)

        title = QLabel("重命名历史")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        description = QLabel(
            "查看已保存的重命名事务和操作记录。记录状态来自 journal；当前路径观察单独显示。"
        )
        description.setObjectName("pageDescription")
        description.setWordWrap(True)
        layout.addWidget(description)

        content = QSplitter(Qt.Orientation.Horizontal)
        content.setObjectName("renameHistorySplitter")
        content.setChildrenCollapsible(False)
        content.setHandleWidth(0)
        master = QWidget()
        master.setObjectName("renameHistoryMaster")
        master_layout = QVBoxLayout(master)
        master_layout.setContentsMargins(0, 0, 0, 0)
        master_layout.setSpacing(8)
        list_heading = QLabel("事务列表")
        list_heading.setObjectName("sectionLabel")
        master_layout.addWidget(list_heading)
        self.transaction_list = QListWidget()
        self.transaction_list.setObjectName("renameTransactionList")
        self.transaction_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        master_layout.addWidget(self.transaction_list, 1)
        self.page_label = QLabel("尚未加载")
        self.page_label.setObjectName("pageDescription")
        master_layout.addWidget(self.page_label)
        page_controls = QHBoxLayout()
        self.newer_button = QPushButton("较新")
        self.newer_button.setEnabled(False)
        self.older_button = QPushButton("较早")
        page_controls.addWidget(self.newer_button)
        page_controls.addWidget(self.older_button)
        page_controls.addWidget(QLabel("50 条 / 页"), 1)
        master_layout.addLayout(page_controls)
        refresh_row = QHBoxLayout()
        self.refresh_button = QPushButton("刷新列表")
        refresh_row.addWidget(self.refresh_button)
        master_layout.addLayout(refresh_row)
        master_width = self._preferred_master_width(master_layout, page_controls, list_heading)
        master.setFixedWidth(master_width)
        content.addWidget(master)

        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.detail_content = QWidget()
        self.detail_layout = QVBoxLayout(self.detail_content)
        self.detail_layout.setContentsMargins(0, 0, 4, 0)
        self.detail_layout.setSpacing(12)
        self.detail_scroll.setWidget(self.detail_content)

        self.detail_heading = QLabel("选择一个事务以查看记录。")
        self.detail_heading.setObjectName("sectionLabel")
        self.detail_heading.setWordWrap(True)
        self.detail_layout.addWidget(self.detail_heading)

        self.query_status_label = QLabel("")
        self.query_status_label.setObjectName("statusLabel")
        self.query_status_label.setWordWrap(True)
        self.query_status_label.hide()
        self.detail_layout.addWidget(self.query_status_label)

        self.transaction_summary = QLabel("")
        self.transaction_summary.setWordWrap(True)
        self.detail_layout.addWidget(self.transaction_summary)

        self.technical_section = QWidget()
        technical_layout = QVBoxLayout(self.technical_section)
        technical_layout.setContentsMargins(0, 0, 0, 0)
        technical_layout.setSpacing(5)
        technical_layout.addWidget(_section_title("技术信息"))
        transaction_id_row = QHBoxLayout()
        transaction_id_label = QLabel("事务 ID")
        self.transaction_id_value = QLineEdit()
        self.transaction_id_value.setReadOnly(True)
        self.transaction_id_value.setObjectName("renameHistoryTransactionId")
        self.copy_transaction_id_button = QPushButton("复制")
        self.copy_transaction_id_button.clicked.connect(
            lambda: QApplication.clipboard().setText(self.transaction_id_value.text())
        )
        transaction_id_row.addWidget(transaction_id_label)
        transaction_id_row.addWidget(self.transaction_id_value, 1)
        transaction_id_row.addWidget(self.copy_transaction_id_button)
        technical_layout.addLayout(transaction_id_row)
        self.detail_layout.addWidget(self.technical_section)
        self.technical_section.hide()

        self.recovery_section = QWidget()
        recovery_layout = QVBoxLayout(self.recovery_section)
        recovery_layout.setContentsMargins(10, 8, 10, 8)
        recovery_layout.setSpacing(5)
        recovery_layout.addWidget(_section_title("记录的恢复信息"))
        self.recovery_label = QLabel("")
        self.recovery_label.setWordWrap(True)
        recovery_layout.addWidget(self.recovery_label)
        self.detail_layout.addWidget(self.recovery_section)
        self.recovery_section.hide()

        self.operations_heading = _section_title("操作记录")
        self.detail_layout.addWidget(self.operations_heading)
        self.operations_table = QTableWidget(0, 5)
        self.operations_table.setObjectName("renameHistoryOperations")
        self.operations_table.setHorizontalHeaderLabels(["#", "执行", "撤销", "源路径", "目标路径"])
        self._configure_operations_table(self.operations_table)
        self.detail_layout.addWidget(self.operations_table)

        self.operation_detail_section = QWidget()
        operation_detail_layout = QVBoxLayout(self.operation_detail_section)
        operation_detail_layout.setContentsMargins(0, 0, 0, 0)
        operation_detail_layout.setSpacing(6)
        operation_detail_layout.addWidget(_section_title("操作详情"))
        self.operation_sequence_label = QLabel("")
        operation_detail_layout.addWidget(self.operation_sequence_label)
        self.operation_status_label = QLabel("")
        self.operation_status_label.setWordWrap(True)
        operation_detail_layout.addWidget(self.operation_status_label)

        self.source_path_value, self.copy_source_path_button = self._add_path_detail_row(
            operation_detail_layout,
            "源路径",
            "复制源路径",
        )
        self.target_path_value, self.copy_target_path_button = self._add_path_detail_row(
            operation_detail_layout,
            "目标路径",
            "复制目标路径",
        )
        self.execution_error_value = self._add_error_detail_row(
            operation_detail_layout,
            "执行错误",
        )
        self.undo_error_value = self._add_error_detail_row(
            operation_detail_layout,
            "撤销错误",
        )
        self.detail_layout.addWidget(self.operation_detail_section)
        self.operation_detail_section.hide()

        self.observation_section = QWidget()
        observation_layout = QVBoxLayout(self.observation_section)
        observation_layout.setContentsMargins(10, 8, 10, 8)
        observation_layout.setSpacing(6)
        observation_header = QHBoxLayout()
        self.observation_heading = _section_title("当前文件系统观察")
        observation_header.addWidget(self.observation_heading, 1)
        self.observe_button = QPushButton("重新观察路径")
        observation_header.addWidget(self.observe_button)
        observation_layout.addLayout(observation_header)
        self.observation_caveat = QLabel(
            "以下是查看时记录路径的当前状态。路径存在不证明它仍指向原目录；"
            "路径也可能已被删除、重建或外部修改。"
        )
        self.observation_caveat.setWordWrap(True)
        self.observation_caveat.setObjectName("pageDescription")
        observation_layout.addWidget(self.observation_caveat)
        self.observations_table = QTableWidget(0, 3)
        self.observations_table.setObjectName("renameHistoryObservations")
        self.observations_table.setHorizontalHeaderLabels(
            ["顺序", "源路径的当前观察", "目标路径的当前观察"]
        )
        self._configure_table(self.observations_table)
        observation_layout.addWidget(self.observations_table)
        self.detail_layout.addWidget(self.observation_section)
        self.observation_section.hide()
        self.detail_layout.addStretch(1)
        content.addWidget(self.detail_scroll)
        content.setStretchFactor(0, 0)
        content.setStretchFactor(1, 1)
        content.setSizes([master_width, 650])
        layout.addWidget(content, 1)

    def _preferred_master_width(
        self,
        master_layout: QVBoxLayout,
        page_controls: QHBoxLayout,
        list_heading: QLabel,
    ) -> int:
        """Fit the timestamp row and controls while keeping the detail pane flexible."""
        metrics = self.transaction_list.fontMetrics()
        timestamp_width = metrics.horizontalAdvance(self._TRANSACTION_TIMESTAMP_SAMPLE)
        style = self.transaction_list.style()
        scrollbar_width = style.pixelMetric(
            QStyle.PixelMetric.PM_ScrollBarExtent,
            None,
            self.transaction_list,
        )
        focus_margin = style.pixelMetric(
            QStyle.PixelMetric.PM_FocusFrameHMargin,
            None,
            self.transaction_list,
        )
        list_width = (
            timestamp_width
            + self.transaction_list.iconSize().width()
            + max(8, focus_margin * 2 + 4)
            + scrollbar_width
            + self.transaction_list.frameWidth() * 2
        )
        margins = master_layout.contentsMargins()
        controls_width = max(
            page_controls.sizeHint().width(),
            self.refresh_button.sizeHint().width(),
            list_heading.sizeHint().width(),
        )
        return max(list_width, controls_width) + margins.left() + margins.right()

    @staticmethod
    def _add_path_detail_row(
        parent_layout: QVBoxLayout,
        label_text: str,
        copy_text: str,
    ) -> tuple[QLineEdit, QPushButton]:
        row = QHBoxLayout()
        label = QLabel(label_text)
        field = QLineEdit()
        field.setReadOnly(True)
        field.setToolTip("完整路径可横向滚动查看，也可复制")
        button = QPushButton(copy_text)
        button.clicked.connect(lambda: QApplication.clipboard().setText(field.text()))
        row.addWidget(label)
        row.addWidget(field, 1)
        row.addWidget(button)
        parent_layout.addLayout(row)
        return field, button

    @staticmethod
    def _add_error_detail_row(
        parent_layout: QVBoxLayout,
        label_text: str,
    ) -> QPlainTextEdit:
        row = QVBoxLayout()
        label = QLabel(label_text)
        value = QPlainTextEdit()
        value.setReadOnly(True)
        value.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        value.setMaximumHeight(88)
        row.addWidget(label)
        row.addWidget(value)
        parent_layout.addLayout(row)
        return value

    @staticmethod
    def _configure_operations_table(table: QTableWidget) -> None:
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setWordWrap(False)
        table.setSortingEnabled(False)
        table.setAlternatingRowColors(True)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        table.setMinimumHeight(90)
        table.setMaximumHeight(205)
        table.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        table.verticalHeader().setDefaultSectionSize(30)
        header = table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        metrics = table.fontMetrics()
        for column, labels in (
            (
                1,
                ("执行", "待处理", "成功", "失败", "未执行", "预检失败", "拒绝", "需检查"),
            ),
            (2, ("撤销", "未撤销", "—", "已撤销", "失败", "冲突")),
        ):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
            status_width = max(metrics.horizontalAdvance(label) for label in labels) + 20
            table.setColumnWidth(column, status_width)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Stretch)
        table.setItemDelegate(_OperationRowDelegate(table))

    def _resize_operations_table(self) -> None:
        table = self.operations_table
        sequence_text = str(max(1, table.rowCount()))
        sequence_width = max(
            table.fontMetrics().horizontalAdvance("#"),
            table.fontMetrics().horizontalAdvance(sequence_text),
        ) + 16
        table.setColumnWidth(0, sequence_width)
        visible_rows = min(table.rowCount(), 5)
        header_height = table.horizontalHeader().sizeHint().height()
        row_height = table.verticalHeader().defaultSectionSize()
        frame_height = table.frameWidth() * 2 + 4
        height = max(90, header_height + visible_rows * row_height + frame_height)
        table.setFixedHeight(min(height, 205))

    @staticmethod
    def _configure_table(table: QTableWidget) -> None:
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.setWordWrap(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setStretchLastSection(True)
        table.setMinimumHeight(115)

    @Slot()
    def refresh_history(self) -> None:
        """Refresh only the currently selected bounded page."""
        preserved_id = self._selected_id
        if self._history_service is None:
            self.query_status_label.setText("重命名历史不可用：事务日志未初始化。")
            self.query_status_label.show()
            self.page_label.setText("无法加载")
            self.older_button.setEnabled(False)
            return
        try:
            transactions = self._history_service.list_recent(
                limit=self.PAGE_SIZE + 1,
                offset=self._offset,
            )
        except Exception as exc:
            self.query_status_label.setText(f"无法读取重命名历史：{exc}")
            self.query_status_label.show()
            self.page_label.setText("读取失败")
            self.older_button.setEnabled(False)
            return

        self.query_status_label.clear()
        self.query_status_label.hide()
        has_older_page = len(transactions) > self.PAGE_SIZE
        transactions = transactions[: self.PAGE_SIZE]
        self.transaction_list.blockSignals(True)
        self.transaction_list.clear()
        for transaction in transactions:
            self._add_transaction_item(transaction)
        if preserved_id is not None and not any(
            self.transaction_list.item(index).data(self._TRANSACTION_ID_ROLE) == preserved_id
            for index in range(self.transaction_list.count())
        ):
            self._selected_id = None
        self.transaction_list.blockSignals(False)
        page_number = self._offset // self.PAGE_SIZE + 1
        self.page_label.setText(f"第 {page_number} 页 · {len(transactions)} 条")
        self.newer_button.setEnabled(self._offset > 0)
        self.older_button.setEnabled(has_older_page)

        if not transactions:
            self._selected_id = None
            self._selected_transaction = None
            self._show_empty_detail("没有已保存的重命名事务。")
            return
        selected_item = self._find_item(preserved_id)
        if selected_item is None:
            selected_item = self.transaction_list.item(0)
        self.transaction_list.setCurrentItem(selected_item)

    @Slot()
    def refresh_after_mutation(self) -> None:
        """Show the newest page after journal changes so offsets cannot shift silently."""
        self._offset = 0
        self._selected_id = None
        self.refresh_history()
    def open_transaction(self, transaction_id: str, *, refresh: bool = True) -> None:
        """Open a journal transaction directly, including one outside page one."""
        self._offset = 0
        if refresh:
            self.refresh_history()
        if self._history_service is None:
            return
        item = self._find_item(transaction_id)
        if item is None:
            try:
                transaction = self._history_service.get_transaction(transaction_id)
            except Exception as exc:
                self._show_transaction_error(transaction_id, exc)
                return
            self.transaction_list.setCurrentRow(-1)
            self._show_transaction(transaction)
            return
        self.transaction_list.blockSignals(True)
        self.transaction_list.setCurrentItem(item)
        self.transaction_list.blockSignals(False)
        if self._selected_id != transaction_id:
            self._load_transaction(transaction_id)

    @Slot()
    def refresh_observations(self) -> None:
        """Refresh no-follow filesystem observations for the selected unresolved row."""
        transaction = self._selected_transaction
        if transaction is None or self._history_service is None or not _is_unresolved(transaction):
            return
        try:
            observations = self._history_service.observe_paths(transaction)
        except Exception as exc:
            self.query_status_label.setText(f"无法观察当前路径状态：{exc}")
            self.query_status_label.show()
            observations = ()
        self.observations_table.setRowCount(len(observations) or len(transaction.operations))
        observations_by_sequence = {item.sequence: item for item in observations}
        for row, operation in enumerate(transaction.operations):
            self.observations_table.setItem(row, 0, _cell(str(operation.sequence)))
            result = observations_by_sequence.get(operation.sequence)
            if result is None:
                source = CurrentPathObservation(operation.source_path, CurrentPathState.ERROR)
                target = CurrentPathObservation(operation.target_path, CurrentPathState.ERROR)
            else:
                source, target = result.source, result.target
            self.observations_table.setItem(row, 1, _cell(_observation_text(source)))
            self.observations_table.setItem(row, 2, _cell(_observation_text(target)))
        self.observations_table.resizeRowsToContents()

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
        self._selected_id = transaction.transaction_id
        self._selected_transaction = transaction
        self.query_status_label.clear()
        self.query_status_label.hide()
        self.detail_heading.setText("事务详情")
        self.transaction_id_value.setText(transaction.transaction_id)
        self.transaction_id_value.setToolTip(transaction.transaction_id)
        self.technical_section.show()
        created_at = _format_datetime(transaction.created_at)
        completed_at = (
            _format_datetime(transaction.completed_at) if transaction.completed_at else "无"
        )
        self.transaction_summary.setText(
            f"创建时间：{created_at}\n"
            f"根目录：{transaction.root}\n"
            f"事务状态：{_transaction_status_text(transaction.status)}\n"
            f"完成时间：{completed_at}"
        )
        self._render_recovery(transaction)
        self._render_operations(transaction)
        if _is_unresolved(transaction):
            self.observation_section.show()
            self.refresh_observations()
        else:
            self.observation_section.hide()
            self.observations_table.setRowCount(0)

    def _show_transaction_error(self, transaction_id: str, error: Exception) -> None:
        self.transaction_list.setCurrentRow(-1)
        self._selected_id = None
        self._selected_transaction = None
        self.detail_heading.setText("事务详情不可用")
        self.query_status_label.setText(f"无法读取事务详情：{error}")
        self.query_status_label.show()
        self.transaction_summary.clear()
        self.recovery_label.clear()
        self.recovery_section.hide()
        self.transaction_id_value.setText(transaction_id)
        self.transaction_id_value.setToolTip(transaction_id)
        self.technical_section.show()
        self.operations_table.setRowCount(0)
        self._resize_operations_table()
        self._operations = ()
        self._clear_operation_detail()
        self.observation_section.hide()
        self.observations_table.setRowCount(0)

    def _render_recovery(self, transaction: RenameTransaction) -> None:
        has_recovery_info = any(
            value is not None
            for value in (
                transaction.recovery_stage,
                transaction.recovery_sequence,
                transaction.recovery_error,
            )
        )
        if not has_recovery_info and not _is_unresolved(transaction):
            self.recovery_section.hide()
            return
        values = [
            f"恢复阶段：{transaction.recovery_stage or '未记录'}",
            f"恢复序号：{_recovery_sequence_text(transaction.recovery_sequence)}",
            f"恢复错误：{transaction.recovery_error or '未记录'}",
        ]
        if _is_unresolved(transaction):
            values.append("该事务仍处于未解决状态，因此 Organizer 会继续阻止新的重命名和普通撤销。")
        self.recovery_label.setText("\n".join(values))
        self.recovery_section.show()

    def _render_operations(self, transaction: RenameTransaction) -> None:
        self._operations = transaction.operations
        table = self.operations_table
        table.blockSignals(True)
        table.setRowCount(len(self._operations))
        self._resize_operations_table()
        for row, operation in enumerate(self._operations):
            values = (
                str(operation.sequence),
                _execution_overview_text(operation.status),
                _undo_overview_text(operation),
                str(operation.source_path),
                str(operation.target_path),
            )
            for column, value in enumerate(values):
                item = _cell(value)
                if column in {3, 4}:
                    item.setToolTip(value)
                table.setItem(row, column, item)
        if self._operations:
            table.setCurrentCell(0, 0)
            table.selectRow(0)
        else:
            table.clearSelection()
        table.blockSignals(False)
        if self._operations:
            self._show_operation_detail(0)
        else:
            self._clear_operation_detail()

    def _operation_selection_changed(
        self,
        current_row: int,
        _current_column: int,
        _previous_row: int,
        _previous_column: int,
    ) -> None:
        self._show_operation_detail(current_row)

    def _show_operation_detail(self, row: int) -> None:
        if row < 0 or row >= len(self._operations):
            self._clear_operation_detail()
            return
        operation = self._operations[row]
        self.operation_sequence_label.setText(f"记录序号：{operation.sequence}")
        self.operation_status_label.setText(
            f"执行结果：{_execution_status_text(operation.status)}　"
            f"撤销结果：{_undo_status_text(operation.undo_status)}"
        )
        self.source_path_value.setText(str(operation.source_path))
        self.target_path_value.setText(str(operation.target_path))
        self.source_path_value.setToolTip(str(operation.source_path))
        self.target_path_value.setToolTip(str(operation.target_path))
        self.execution_error_value.setPlainText(operation.error or "无")
        self.undo_error_value.setPlainText(operation.undo_error or "无")
        self.operation_detail_section.show()

    def _clear_operation_detail(self) -> None:
        self.operation_sequence_label.clear()
        self.operation_status_label.clear()
        self.source_path_value.clear()
        self.target_path_value.clear()
        self.source_path_value.setToolTip("")
        self.target_path_value.setToolTip("")
        self.execution_error_value.clear()
        self.undo_error_value.clear()
        self.operation_detail_section.hide()

    def _add_transaction_item(
        self,
        transaction: RenameTransactionSummary,
        *,
        prefix: str = "",
    ) -> QListWidgetItem:
        item = QListWidgetItem(
            f"{prefix}{_transaction_status_text(transaction.status)}\n"
            f"{_format_local_wall_time(transaction.created_at)}"
        )
        item.setIcon(self.style().standardIcon(_transaction_status_icon(transaction.status)))
        item.setData(self._TRANSACTION_ID_ROLE, transaction.transaction_id)
        item.setToolTip(str(transaction.root))
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

    def _show_empty_detail(self, message: str) -> None:
        self.detail_heading.setText(message)
        self.query_status_label.clear()
        self.query_status_label.hide()
        self.transaction_summary.clear()
        self.recovery_section.hide()
        self.technical_section.hide()
        self.transaction_id_value.clear()
        self.observation_section.hide()
        self.operations_table.setRowCount(0)
        self._resize_operations_table()
        self._operations = ()
        self._clear_operation_detail()
        self.observations_table.setRowCount(0)

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


def _cell(value: str) -> QTableWidgetItem:
    item = QTableWidgetItem(value)
    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
    item.setToolTip(value)
    return item


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


def _transaction_status_text(status: TransactionStatus) -> str:
    return {
        TransactionStatus.PENDING: "进行中 / 未决",
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
        return "未撤销" if operation.status is ExecutionStatus.SUCCESS else "—"
    return {
        UndoStatus.SUCCESS: "已撤销",
        UndoStatus.FAILED: "失败",
        UndoStatus.CONFLICT: "冲突",
        UndoStatus.PENDING: "未撤销",
    }[operation.undo_status]


def _execution_status_text(status: ExecutionStatus) -> str:
    return {
        ExecutionStatus.PENDING: "记录为待处理",
        ExecutionStatus.SUCCESS: "Journal 记录成功",
        ExecutionStatus.FAILED: "记录为失败",
        ExecutionStatus.NOT_EXECUTED: "记录为未执行",
        ExecutionStatus.PRECONDITION_FAILED: "记录为预检失败",
        ExecutionStatus.REJECTED: "记录为拒绝",
        ExecutionStatus.RECOVERY_REQUIRED: "记录为需要检查",
    }[status]


def _undo_status_text(status: UndoStatus) -> str:
    return {
        UndoStatus.PENDING: "记录为待处理",
        UndoStatus.SUCCESS: "Journal 记录已撤销",
        UndoStatus.FAILED: "记录为失败",
        UndoStatus.CONFLICT: "记录为冲突",
    }[status]


def _observation_text(observation: CurrentPathObservation) -> str:
    label = {
        CurrentPathState.DIRECTORY: "存在，目录",
        CurrentPathState.FILE: "存在，文件",
        CurrentPathState.OTHER: "存在，其他类型",
        CurrentPathState.LINK_LIKE: "存在，link-like entry",
        CurrentPathState.ABSENT: "不存在",
        CurrentPathState.ERROR: "无法检查当前路径状态",
    }[observation.state]
    if observation.error:
        error = observation.error
        if len(error) > 300:
            error = f"{error[:299]}…"
        label = f"{label}：{error}"
    return label


def _is_unresolved(transaction: RenameTransaction) -> bool:
    return transaction.status in {
        TransactionStatus.PENDING,
        TransactionStatus.RECOVERY_REQUIRED,
    }


class _OperationRowDelegate(QStyledItemDelegate):
    """Paint readable palette-aware row selection and elide overview paths."""

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        if option.text and index.column() in {3, 4}:
            option.text = option.fontMetrics.elidedText(
                option.text,
                Qt.TextElideMode.ElideMiddle,
                max(0, option.rect.width() - 16),
            )

    def paint(self, painter, option, index) -> None:
        if not option.state & QStyle.StateFlag.State_Selected:
            super().paint(painter, option, index)
            return

        selection_background = _operation_selection_background(option.palette)
        text_color = option.palette.color(QPalette.ColorRole.Text)
        selected_option = type(option)(option)
        selected_option.state &= ~QStyle.StateFlag.State_Selected
        selected_option.palette.setColor(QPalette.ColorRole.Base, selection_background)
        selected_option.palette.setColor(QPalette.ColorRole.AlternateBase, selection_background)
        selected_option.palette.setColor(QPalette.ColorRole.Text, text_color)
        selected_option.palette.setColor(QPalette.ColorRole.WindowText, text_color)
        selected_option.backgroundBrush = QBrush(selection_background)
        super().paint(painter, selected_option, index)

        painter.save()
        if index.column() == 0:
            indicator_height = max(0, option.rect.height() - 6)
            painter.fillRect(
                option.rect.left(),
                option.rect.top() + 3,
                min(3, option.rect.width()),
                indicator_height,
                option.palette.color(QPalette.ColorRole.Highlight),
            )
        if option.state & QStyle.StateFlag.State_HasFocus:
            focus_pen = QPen(option.palette.color(QPalette.ColorRole.Highlight))
            focus_pen.setStyle(Qt.PenStyle.DotLine)
            painter.setPen(focus_pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(option.rect.adjusted(1, 1, -2, -2))
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
