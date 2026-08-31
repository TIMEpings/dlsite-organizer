"""Organizer page: review RenamePlans and execute confirmed safe renames."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import cast

from PySide6.QtCore import Qt, QThread, Slot
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dlsite_organizer.domain.organizer import RenamePlan, RenamePlanStatus
from dlsite_organizer.domain.rename_execution import RenameExecutionResult, UndoResult
from dlsite_organizer.services.drop_input import (
    DropInputError,
    DropInputService,
)
from dlsite_organizer.services.organizer import OrganizerPreview, OrganizerService
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.undo_service import UndoService
from dlsite_organizer.ui.widgets.drop_zone import DirectoryDropZone, local_directory_paths
from dlsite_organizer.ui.workers.organizer_worker import OrganizerWorker
from dlsite_organizer.ui.workers.rename_worker import RenameActionWorker


class OrganizerPage(QWidget):
    """Present scan results and expose explicit rename and undo actions."""

    _COLUMNS = ("状态", "当前目录名", "RJcode", "社团", "标题", "目标目录名", "详情")

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
        self._preview: OrganizerPreview | None = None
        self._preview_stale = False
        self._last_execution_result: RenameExecutionResult | None = None
        self._last_undo_result: UndoResult | None = None
        self._build_ui()

        self.browse_button.clicked.connect(self.choose_root)
        self.scan_button.clicked.connect(self.start_scan)
        self.cancel_button.clicked.connect(self.cancel_scan)
        self.execute_button.clicked.connect(self.execute_rename)
        self.undo_button.clicked.connect(self.undo_recent)
        self.table.itemChanged.connect(self._selection_changed)
        self._refresh_recent_transaction()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 28, 36, 28)
        layout.setSpacing(14)

        heading = QLabel("整理")
        heading.setObjectName("pageTitle")
        description = QLabel("扫描作品根目录，查询 RJcode 并生成重命名预览；执行前需要明确确认。")
        description.setObjectName("pageDescription")
        description.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(description)

        self.drop_zone = DirectoryDropZone(
            "将作品文件夹或作品根目录拖到这里",
            "完整模式只生成预览，不会因拖放立即重命名。",
        )
        self.drop_zone.paths_dropped.connect(self._handle_drop_paths)
        layout.addWidget(self.drop_zone)

        root_label = QLabel("作品根目录")
        root_label.setObjectName("sectionLabel")
        layout.addWidget(root_label)
        root_row = QHBoxLayout()
        root_row.setSpacing(10)
        self.root_input = QLineEdit()
        self.root_input.setPlaceholderText("选择包含作品子目录的文件夹")
        self.root_input.setClearButtonEnabled(True)
        self.root_input.setMinimumHeight(40)
        self.browse_button = QPushButton("选择…")
        self.browse_button.setMinimumSize(92, 40)
        root_row.addWidget(self.root_input, 1)
        root_row.addWidget(self.browse_button)
        layout.addLayout(root_row)

        action_row = QHBoxLayout()
        action_row.setSpacing(10)
        self.scan_button = QPushButton("扫描并预览")
        self.scan_button.setObjectName("primaryButton")
        self.scan_button.setMinimumSize(124, 40)
        self.cancel_button = QPushButton("取消")
        self.cancel_button.setMinimumSize(92, 40)
        self.cancel_button.setEnabled(False)
        action_row.addWidget(self.scan_button)
        action_row.addWidget(self.cancel_button)
        self.execute_button = QPushButton("执行重命名")
        self.execute_button.setObjectName("primaryButton")
        self.execute_button.setMinimumSize(124, 40)
        self.execute_button.setEnabled(False)
        action_row.addWidget(self.execute_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self.status_label = QLabel("请选择根目录开始扫描。")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, len(self._COLUMNS))
        self.table.setHorizontalHeaderLabels(self._COLUMNS)
        self.table.setObjectName("organizerTable")
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(260)
        layout.addWidget(self.table, 1)

        self.summary_label = QLabel("尚未生成预览。")
        self.summary_label.setObjectName("pageDescription")
        layout.addWidget(self.summary_label)
        recent_row = QHBoxLayout()
        self.recent_transaction_label = QLabel("最近一次重命名：无")
        self.recent_transaction_label.setObjectName("pageDescription")
        self.recent_transaction_label.setWordWrap(True)
        self.undo_button = QPushButton("撤销最近一次重命名")
        self.undo_button.setMinimumSize(152, 36)
        self.undo_button.setEnabled(False)
        recent_row.addWidget(self.recent_transaction_label, 1)
        recent_row.addWidget(self.undo_button)
        layout.addLayout(recent_row)

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
    def start_scan(self, selected_paths: Sequence[Path | str] | None = None) -> None:
        """Start one background organizer run."""
        if self._thread is not None:
            return
        root = self.root_input.text().strip()
        if not root:
            self.status_label.setProperty("state", "error")
            self.status_label.setText("请先选择作品根目录。")
            self._refresh_status_style()
            return

        self._clear_preview()
        self._set_loading(True)
        self.status_label.setProperty("state", "loading")
        self.status_label.setText("正在扫描目录…")
        self._refresh_status_style()

        thread = QThread()
        worker = OrganizerWorker(self._organizer_service, root, selected_paths)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._show_progress)
        worker.result_ready.connect(self._show_preview)
        worker.failed.connect(self._show_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._scan_finished)
        self._thread = thread
        self._worker = worker
        thread.start()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        paths = local_directory_paths(event.mimeData())
        if paths:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        paths = local_directory_paths(event.mimeData())
        if paths:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = local_directory_paths(event.mimeData())
        if not paths:
            event.ignore()
            return
        event.acceptProposedAction()
        self._handle_drop_paths(paths)

    @Slot(object)
    def _handle_drop_paths(self, value: object) -> None:
        """Interpret a full-mode drop before starting the existing scan worker."""
        if self._thread is not None:
            return
        paths = tuple(cast(Sequence[Path | str], value))
        try:
            selection = self._drop_input_service.validate_full_drop(paths)
        except DropInputError as exc:
            self._show_error(exc.user_message)
            return
        self.set_root_path(selection.root_path)
        self.start_scan(selection.selected_paths)

    @Slot()
    def cancel_scan(self) -> None:
        """Request cooperative cancellation and leave the current request bounded."""
        if self._worker is None:
            return
        self._worker.cancel()
        self.cancel_button.setEnabled(False)
        self.status_label.setProperty("state", "loading")
        self.status_label.setText("正在取消；当前网络请求结束后会保留已完成结果…")
        self._refresh_status_style()

    @Slot(int, int, str)
    def _show_progress(self, completed: int, total: int, work_code: str) -> None:
        self.status_label.setProperty("state", "loading")
        self.status_label.setText(f"正在查询 {completed + 1} / {total}：{work_code}")
        self._refresh_status_style()

    @Slot(object)
    def _show_preview(self, value: object) -> None:
        preview = cast(OrganizerPreview, value)
        self._preview = preview
        self._preview_stale = False
        self.table.setRowCount(0)
        self.table.setEnabled(True)
        for plan in preview.plans:
            self._append_plan(plan)

        if preview.cancelled:
            self.status_label.setProperty("state", "loading")
            self.status_label.setText("扫描已取消；表格保留已完成查询及未处理项目。")
        else:
            self.status_label.setProperty("state", "success")
            self.status_label.setText("预览生成完成；未修改本地文件。")
        self._refresh_status_style()
        ready = preview.count(RenamePlanStatus.READY)
        unchanged = preview.count(RenamePlanStatus.UNCHANGED)
        conflict = preview.count(RenamePlanStatus.CONFLICT)
        failed = preview.count(RenamePlanStatus.LOOKUP_FAILED)
        self.summary_label.setText(
            f"共 {len(preview.plans)} 个 · Ready {ready} · Unchanged {unchanged} · "
            f"Conflict {conflict} · Failed {failed} · Skipped {preview.skipped_count}"
        )

    def set_preview(self, preview: OrganizerPreview) -> None:
        """Render a preview directly for smoke tests and embedding callers."""
        self._show_preview(preview)
        self._update_execute_button()

    def selected_ready_plans(self) -> tuple[RenamePlan, ...]:
        if self._preview is None or self._preview_stale:
            return ()
        selected: list[RenamePlan] = []
        for row, plan in enumerate(self._preview.plans):
            item = self.table.item(row, 0)
            if (
                plan.status is RenamePlanStatus.READY
                and item is not None
                and item.checkState() is Qt.CheckState.Checked
            ):
                selected.append(plan)
        return tuple(selected)

    def selected_ready_count(self) -> int:
        return len(self.selected_ready_plans())

    @property
    def preview_stale(self) -> bool:
        return self._preview_stale

    def invalidate_preview(self) -> None:
        """Invalidate an existing preview after a naming-settings change."""
        if self._preview is not None:
            self._invalidate_preview()

    def apply_settings(self, settings: object) -> None:
        """Apply validated settings to the organizer's next preview."""
        self._organizer_service.apply_settings(settings)

    @Slot(QTableWidgetItem)
    def _selection_changed(self, _item: QTableWidgetItem) -> None:
        self._update_execute_button()

    def _update_execute_button(self) -> None:
        unresolved = None
        if self._execution_service is not None:
            try:
                unresolved = self._execution_service.unresolved_transaction()
            except Exception:
                unresolved = True
        self.execute_button.setEnabled(
            self._execution_service is not None
            and self._execution_service.available
            and unresolved is None
            and self._thread is None
            and not self._preview_stale
            and self.selected_ready_count() > 0
        )

    @Slot(object)
    def _show_execution_result(self, value: object) -> None:
        result = cast(RenameExecutionResult, value)
        self._last_execution_result = result
        self._invalidate_preview()
        if result.status.name == "COMPLETED":
            self.status_label.setProperty("state", "success")
            self.status_label.setText(
                f"重命名完成：成功 {result.success_count}，失败 {result.failed_count}。 "
                f"Transaction: {result.transaction_id}。当前预览已失效，请重新扫描。"
            )
        elif result.status.name == "PARTIAL":
            self.status_label.setProperty("state", "error")
            self.status_label.setText(
                f"重命名未全部完成：成功 {result.success_count}，失败 {result.failed_count}，"
                f"未执行 {result.not_executed_count}。已停止后续操作。"
                " 可以从最近事务尝试撤销已经成功的修改。"
            )
        elif result.status.name == "RECOVERY_REQUIRED":
            self.status_label.setProperty("state", "error")
            self.status_label.setText(
                "重命名状态需要恢复：文件系统与 journal 可能不同步。"
                " 请不要立即重新执行；详细信息已写入日志。"
            )
        else:
            self._show_error(result.error or "重命名未执行。")
        self._refresh_status_style()
        self._refresh_recent_transaction()

    @Slot(object)
    def _show_undo_result(self, value: object) -> None:
        result = cast(UndoResult, value)
        self._last_undo_result = result
        self._invalidate_preview()
        if result.status.name == "UNDONE":
            self.status_label.setProperty("state", "success")
            self.status_label.setText(
                f"撤销完成：恢复 {result.success_count} 个目录。当前预览已失效，请重新扫描。"
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
        self._refresh_status_style()
        self._refresh_recent_transaction()

    @Slot()
    def execute_rename(self) -> None:
        plans = self.selected_ready_plans()
        if self._execution_service is None or not plans:
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
        root = self.root_input.text().strip()
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
        self._set_action_loading(True)
        thread.start()

    @Slot(int, int, str, str)
    def _show_action_progress(self, completed: int, total: int, source: str, target: str) -> None:
        self.status_label.setProperty("state", "loading")
        self.status_label.setText(f"正在执行 {completed} / {total}: {source} -> {target}")
        self._refresh_status_style()

    def _append_plan(self, plan: RenamePlan) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        work = plan.work
        work_code = plan.work_code or "、".join(plan.work_codes) or "—"
        maker = work.maker_name if work is not None and work.maker_name else "—"
        title = work.title if work is not None else "—"
        details = plan.error or "；".join(plan.warnings) or "—"
        values = (
            plan.status.name,
            plan.current_name,
            work_code,
            maker,
            title,
            plan.proposed_name or "—",
            details,
        )
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            item.setToolTip(value)
            if column == 0 and plan.status is RenamePlanStatus.READY:
                item.setFlags(
                    item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                )
                item.setCheckState(Qt.CheckState.Checked)
            elif column == 0:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            if column == 1:
                item.setToolTip(str(plan.source_path))
            if column == 5 and plan.target_path is not None:
                item.setToolTip(str(plan.target_path))
            if column == 6 and plan.warnings:
                warning_text = "\n".join(plan.warnings)
                item.setToolTip(warning_text + (f"\n{plan.error}" if plan.error else ""))
            self.table.setItem(row, column, item)

    @Slot(str)
    def _show_error(self, message: str) -> None:
        self.status_label.setProperty("state", "error")
        self.status_label.setText(message)
        self._refresh_status_style()

    @Slot(str)
    def _show_action_error(self, message: str) -> None:
        """Treat an escaped action exception as a stale preview as well."""
        self._invalidate_preview()
        self._show_error(message)
        self._refresh_recent_transaction()

    @Slot()
    def _scan_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._set_loading(False)
        self._update_execute_button()
        self._refresh_recent_transaction()

    @Slot()
    def _action_finished(self) -> None:
        self._thread = None
        self._action_worker = None
        self._set_action_loading(False)
        self._update_execute_button()
        self._refresh_recent_transaction()

    def _set_action_loading(self, loading: bool) -> None:
        self.drop_zone.setEnabled(not loading)
        self.root_input.setEnabled(not loading)
        self.browse_button.setEnabled(not loading)
        self.scan_button.setEnabled(not loading)
        self.cancel_button.setEnabled(False)
        self.execute_button.setEnabled(False)
        self.undo_button.setEnabled(False)

    def is_busy(self) -> bool:
        """Return whether the organizer worker thread is active."""
        return self._thread is not None

    def _set_loading(self, loading: bool) -> None:
        self.drop_zone.setEnabled(not loading)
        self.root_input.setEnabled(not loading)
        self.browse_button.setEnabled(not loading)
        self.scan_button.setEnabled(not loading)
        self.cancel_button.setEnabled(loading)
        if loading:
            self.execute_button.setEnabled(False)
            self.undo_button.setEnabled(False)

    def _invalidate_preview(self) -> None:
        self._preview_stale = True
        self.table.setEnabled(False)
        self.execute_button.setEnabled(False)
        self.summary_label.setText("Preview stale: 请重新扫描以刷新状态。")

    def _refresh_recent_transaction(self) -> None:
        if self._undo_service is None:
            self.undo_button.setEnabled(False)
            return
        try:
            unresolved = self._undo_service.unresolved_transaction()
            if unresolved is not None:
                self.undo_button.setEnabled(False)
                self.execute_button.setEnabled(False)
                self.recent_transaction_label.setText(
                    f"检测到未解决的重命名事务: {unresolved.transaction_id} "
                    f"| root={unresolved.root} | 状态={unresolved.status.value}"
                )
                self.status_label.setProperty("state", "error")
                self.status_label.setText(
                "检测到未解决的重命名事务。为防止进一步改变文件系统，新的重命名和普通撤销已暂时禁用。"
                "请人工检查 SQLite 与文件系统。"
                )
                self._refresh_status_style()
                return
            transaction = self._undo_service.latest_transaction()
        except Exception:
            self.undo_button.setEnabled(False)
            self.recent_transaction_label.setText("最近一次重命名：journal 不可用")
            return
        if transaction is None:
            self.undo_button.setEnabled(False)
            self.recent_transaction_label.setText("最近一次重命名：无")
            return
        pending = sum(
            operation.status.name == "SUCCESS" and operation.undo_status.name != "SUCCESS"
            for operation in transaction.operations
        )
        self.undo_button.setEnabled(self._thread is None and pending > 0)
        self.recent_transaction_label.setText(
            f"最近一次重命名：{transaction.created_at:%Y-%m-%d %H:%M} | "
            f"可撤销：{pending} | 事务：{transaction.transaction_id}"
        )

    def _clear_preview(self) -> None:
        self._preview = None
        self._preview_stale = False
        self.table.setRowCount(0)
        self.table.setEnabled(True)
        self.summary_label.setText("尚未生成预览。")

    def _refresh_status_style(self) -> None:
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
