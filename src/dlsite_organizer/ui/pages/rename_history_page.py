"""Read-only transaction history and recovery inspection page."""

from __future__ import annotations

from datetime import UTC, datetime

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dlsite_organizer.domain.rename_execution import (
    ExecutionStatus,
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
        self._build_ui()
        self.transaction_list.currentItemChanged.connect(self._selection_changed)
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

        content = QHBoxLayout()
        content.setSpacing(16)
        master = QWidget()
        master.setMinimumWidth(230)
        master.setMaximumWidth(300)
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
        self.detail_layout.addWidget(self.query_status_label)

        self.transaction_summary = QLabel("")
        self.transaction_summary.setWordWrap(True)
        self.detail_layout.addWidget(self.transaction_summary)

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
        self.operations_table = QTableWidget(0, 7)
        self.operations_table.setObjectName("renameHistoryOperations")
        self.operations_table.setHorizontalHeaderLabels(
            [
                "顺序",
                "记录的源路径",
                "记录的目标路径",
                "执行结果",
                "撤销结果",
                "执行错误",
                "撤销错误",
            ]
        )
        self._configure_table(self.operations_table)
        self.detail_layout.addWidget(self.operations_table)

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
        content.addWidget(self.detail_scroll, 1)
        layout.addLayout(content, 1)

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
            self.page_label.setText("读取失败")
            self.older_button.setEnabled(False)
            return

        self.query_status_label.clear()
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
        self.detail_heading.setText(f"事务详情 · {transaction.transaction_id}")
        created_at = _format_datetime(transaction.created_at)
        completed_at = (
            _format_datetime(transaction.completed_at) if transaction.completed_at else "无"
        )
        self.transaction_summary.setText(
            f"创建时间：{created_at}\n"
            f"记录的根目录：{transaction.root}\n"
            f"记录的事务状态：{_transaction_status_text(transaction.status)}\n"
            f"记录的完成时间：{completed_at}"
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
        self.detail_heading.setText(f"事务详情不可用 · {transaction_id}")
        self.query_status_label.setText(f"无法读取事务详情：{error}")
        self.transaction_summary.clear()
        self.recovery_label.clear()
        self.recovery_section.hide()
        self.operations_table.setRowCount(0)
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
            f"事务状态：{_transaction_status_text(transaction.status)}",
            f"恢复阶段：{transaction.recovery_stage or '未记录'}",
            f"恢复序号：{_recovery_sequence_text(transaction.recovery_sequence)}",
            f"恢复错误：{transaction.recovery_error or '未记录'}",
        ]
        if _is_unresolved(transaction):
            values.append("该事务仍处于未解决状态，因此 Organizer 会继续阻止新的重命名和普通撤销。")
        self.recovery_label.setText("\n".join(values))
        self.recovery_section.show()

    def _render_operations(self, transaction: RenameTransaction) -> None:
        self.operations_table.setRowCount(len(transaction.operations))
        for row, operation in enumerate(transaction.operations):
            values = (
                str(operation.sequence),
                str(operation.source_path),
                str(operation.target_path),
                _execution_status_text(operation.status),
                _undo_status_text(operation.undo_status),
                operation.error or "—",
                operation.undo_error or "—",
            )
            for column, value in enumerate(values):
                self.operations_table.setItem(row, column, _cell(value))
        self.operations_table.resizeRowsToContents()

    def _add_transaction_item(
        self,
        transaction: RenameTransactionSummary,
        *,
        prefix: str = "",
    ) -> QListWidgetItem:
        item = QListWidgetItem(
            f"{prefix}{_format_datetime(transaction.created_at)}\n"
            f"{_transaction_status_text(transaction.status)} · {transaction.transaction_id}"
        )
        item.setData(self._TRANSACTION_ID_ROLE, transaction.transaction_id)
        item.setToolTip(f"{transaction.root}\n{transaction.transaction_id}")
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
        self.transaction_summary.clear()
        self.recovery_section.hide()
        self.observation_section.hide()
        self.operations_table.setRowCount(0)
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


def _format_datetime(value: datetime) -> str:
    local = (
        value.astimezone()
        if value.tzinfo is not None
        else value.replace(tzinfo=UTC).astimezone()
    )
    return local.strftime("%Y-%m-%d %H:%M:%S %Z")


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
