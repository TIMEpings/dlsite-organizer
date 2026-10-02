"""Organizer page: review RenamePlans and execute confirmed safe renames."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import cast

from PySide6.QtCore import QModelIndex, QThread, Signal, Slot
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from dlsite_organizer.domain.organizer import (
    RenamePlan,
    RenamePlanStatus,
)
from dlsite_organizer.domain.rename_execution import RenameExecutionResult, UndoResult
from dlsite_organizer.services.drop_input import (
    DropInputError,
    DropInputService,
    normalized_path_identity,
)
from dlsite_organizer.services.organizer import OrganizerPreview, OrganizerService
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.undo_service import UndoService
from dlsite_organizer.ui.models.organizer_preview_model import (
    OrganizerPreviewModel,
    StatusPresentation,
    plan_presentation,
)
from dlsite_organizer.ui.state.organizer_state import (
    BusyOperation,
    OrganizerState,
    ScanMode,
)
from dlsite_organizer.ui.state.organizer_state import (
    PreviewStaleReason as PreviewStaleReason,
)
from dlsite_organizer.ui.widgets.drop_zone import DirectoryDropZone, local_directory_paths
from dlsite_organizer.ui.widgets.page_header import PageHeader
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
        self._refresh_recent_transaction()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)

        layout.addWidget(PageHeader("整理"))

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
        self.remove_button = QPushButton("删除选中项")
        self.remove_button.setMinimumSize(112, 40)
        self.remove_button.setEnabled(False)
        action_row.addWidget(self.remove_button)
        self.clear_button = QPushButton("清空")
        self.clear_button.setMinimumSize(82, 40)
        self.clear_button.setEnabled(False)
        action_row.addWidget(self.clear_button)
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

        self.table = QTableView()
        self.table.setModel(self.preview_model)
        self.table.setSortingEnabled(False)
        self.table.setAcceptDrops(False)
        self.table.viewport().setAcceptDrops(False)
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.table.setObjectName("organizerTable")
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(40)
        header = self.table.horizontalHeader()
        header.setMinimumSectionSize(40)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 52)
        self.table.setColumnWidth(1, 110)
        for column in (2, 3, 4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.Stretch)
        self.table.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.table.setMinimumHeight(260)
        layout.addWidget(self.table, 1)

        self.footer = QWidget()
        self.footer.setObjectName("organizerFooter")
        footer_layout = QVBoxLayout(self.footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(14)
        self.summary_label = QLabel("尚未生成预览。")
        self.summary_label.setObjectName("pageDescription")
        footer_layout.addWidget(self.summary_label)
        recent_row = QHBoxLayout()
        self.recent_transaction_label = QLabel("最近一次重命名：无")
        self.recent_transaction_label.setObjectName("pageDescription")
        self.recent_transaction_label.setWordWrap(True)
        self.undo_button = QPushButton("撤销最近一次重命名")
        self.undo_button.setMinimumSize(152, 36)
        self.undo_button.setEnabled(False)
        self.view_transaction_button = QPushButton("查看事务详情")
        self.view_transaction_button.setToolTip("在重命名历史中查看该事务的记录和恢复信息")
        self.view_transaction_button.hide()
        recent_row.addWidget(self.view_transaction_button)
        recent_row.addWidget(self.recent_transaction_label, 1)
        recent_row.addWidget(self.undo_button)
        footer_layout.addLayout(recent_row)
        layout.addWidget(self.footer)

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
        if self._thread is not None:
            return
        root = self.state.input_root.strip()
        if not root:
            self.status_label.setProperty("state", "error")
            self.status_label.setText("请先选择作品根目录。")
            self._refresh_status_style()
            return

        self.state.settings_generation = self._organizer_service.settings_generation
        self.state.begin_scan(self._next_scan_mode)
        self._next_scan_mode = ScanMode.REPLACE
        self._set_loading(True)
        self.status_label.setProperty("state", "loading")
        self.status_label.setText("正在扫描目录…")
        self._refresh_status_style()

        thread: QThread | None = None
        worker: OrganizerWorker | None = None
        try:
            thread = QThread()
            thread.setObjectName("organizer-scan")
            worker = OrganizerWorker(
                self._organizer_service, root, selected_paths,
                request_id=self.state.request_id, preview_revision=self.state.preview_revision,
            )
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
            self.state.scan_mode = ScanMode.REPLACE
            self._show_error("无法启动扫描，请稍后重试。")
            self._set_loading(False)
            self._update_execute_button()
            self._refresh_recent_transaction()

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
        has_existing_preview = self._has_preview_content()
        if self.state.preview is not None and has_existing_preview and normalized_path_identity(
            self.state.preview.root_path
        ) != normalized_path_identity(selection.root_path):
            self._show_error("追加预览必须来自当前预览的同一作品根目录。")
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
            if self.state.scan_mode is ScanMode.APPEND:
                self._show_error("追加预览已取消；当前预览保持不变。")
                return
            if self.state.preview is not None:
                self._show_error("扫描已取消；当前预览保持不变。")
                return
            # With no previous collection, retain the existing useful behavior
            # of showing completed rows from a cancelled replace scan.
            self._commit_preview(preview)
            self.status_label.setProperty("state", "success")
            self.status_label.setText("扫描已取消；表格保留已完成查询及未处理项目。")
            self._refresh_status_style()
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
            self.status_label.setProperty("state", "success")
            self.status_label.setText("追加预览完成；未修改本地文件。")
        else:
            self._commit_preview(preview)

        if (
            self.state.scan_mode is not ScanMode.APPEND
            and not preview.plans
            and not preview.scan.candidates
        ):
            self.status_label.setProperty("state", "success")
            self.status_label.setText("未发现可整理的作品文件夹；未修改本地文件。")
        elif self.state.scan_mode is not ScanMode.APPEND:
            self.status_label.setProperty("state", "success")
            self.status_label.setText("预览生成完成；未修改本地文件。")
        self._refresh_status_style()
        self._update_summary(preview)

    def set_preview(self, preview: OrganizerPreview) -> None:
        """Render a preview directly for smoke tests and embedding callers."""
        self._commit_preview(preview)
        self.status_label.setProperty("state", "success")
        self.status_label.setText("预览生成完成；未修改本地文件。")
        self._refresh_status_style()
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

    @Slot()
    def _table_selection_changed(self) -> None:
        selection = self.table.selectionModel()
        current = self.table.currentIndex()
        self.state.project_highlight(
            [index.row() for index in selection.selectedRows()],
            current.row() if current.isValid() else None,
        )
        self._update_preview_controls()

    def _clear_highlight(self) -> None:
        self.table.clearSelection()
        self.table.setCurrentIndex(QModelIndex())
        self.state.clear_highlight()

    def refresh_mutation_state(self) -> None:
        """Refresh only journal-backed controls after another UI entry point mutates."""
        self._refresh_recent_transaction()
        self._update_execute_button()

    @Slot()
    def mark_filesystem_changed(self) -> None:
        """Invalidate an existing preview after a shared filesystem mutation."""
        self._invalidate_preview(PreviewStaleReason.FILESYSTEM_CHANGED)
        self._refresh_recent_transaction()
        self._update_execute_button()

    def _update_execute_button(self) -> None:
        self.state.settings_generation = self._organizer_service.settings_generation
        self.state.executor_available = False
        self.state.mutation_blocked = True
        if self._execution_service is not None:
            try:
                self.state.executor_available = self._execution_service.available
                self.state.mutation_blocked = (
                    self._execution_service.unresolved_transaction() is not None
                )
            except Exception:
                self.state.mutation_blocked = True
        self.execute_button.setEnabled(self.state.can_execute)

    @Slot(object)
    def _show_execution_result(self, value: object) -> None:
        result = cast(RenameExecutionResult, value)
        self.state.last_execution_result = result
        self._invalidate_preview(PreviewStaleReason.FILESYSTEM_CHANGED)
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
        self.state.last_undo_result = result
        self._invalidate_preview(PreviewStaleReason.FILESYSTEM_CHANGED)
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
        self._set_action_loading(True)
        thread.start()

    @Slot(int, int, str, str)
    def _show_action_progress(self, completed: int, total: int, source: str, target: str) -> None:
        self.status_label.setProperty("state", "loading")
        self.status_label.setText(f"正在执行 {completed} / {total}: {source} -> {target}")
        self._refresh_status_style()

    @Slot(str)
    def _show_error(self, message: str) -> None:
        if self._worker is not None and self.state.preview is not None:
            message = f"{message} 当前预览保持不变。"
        self.status_label.setProperty("state", "error")
        self.status_label.setText(message)
        self._refresh_status_style()

    @Slot(str)
    def _show_action_error(self, message: str) -> None:
        """Treat an escaped action exception as a stale preview as well."""
        self._invalidate_preview(PreviewStaleReason.FILESYSTEM_CHANGED)
        self._show_error(message)
        self._refresh_recent_transaction()

    @Slot()
    def _scan_finished(self) -> None:
        logger.debug("organizer scan thread finished; clearing UI busy state")
        self._thread = None
        self._worker = None
        self.state.finish_operation()
        self._set_loading(False)
        self._update_execute_button()
        self._refresh_recent_transaction()

    @Slot()
    def _action_finished(self) -> None:
        self._thread = None
        self._action_worker = None
        self.state.finish_operation()
        self._set_action_loading(False)
        self._update_execute_button()
        self._refresh_recent_transaction()

    def _set_action_loading(self, loading: bool) -> None:
        self.preview_model.refresh_editability()
        self.drop_zone.setEnabled(not loading)
        self.root_input.setEnabled(not loading)
        self.browse_button.setEnabled(not loading)
        self.scan_button.setEnabled(not loading)
        self.cancel_button.setEnabled(False)
        self.execute_button.setEnabled(False)
        self.undo_button.setEnabled(False)
        self.table.setEnabled(not loading and not self.state.stale)
        self._update_preview_controls()

    def is_busy(self) -> bool:
        """Return whether the organizer worker thread is active."""
        return self.state.busy is not None

    def _set_loading(self, loading: bool) -> None:
        if not loading:
            self.state.finish_operation()
        self.preview_model.refresh_editability()
        self.drop_zone.setEnabled(not loading)
        self.root_input.setEnabled(not loading)
        self.browse_button.setEnabled(not loading)
        self.scan_button.setEnabled(not loading)
        self.cancel_button.setEnabled(loading)
        self.table.setEnabled(not loading and not self.state.stale)
        self._update_preview_controls()
        if loading:
            self.execute_button.setEnabled(False)
            self.undo_button.setEnabled(False)

    def _invalidate_preview(self, reason: PreviewStaleReason) -> None:
        self.state.settings_generation = self._organizer_service.settings_generation
        self.preview_model.mark_stale(reason)
        if not self.state.stale:
            return
        self.table.setEnabled(False)
        self.execute_button.setEnabled(False)
        self.summary_label.setText("预览已失效：请重新扫描以刷新状态。")
        self._update_preview_controls()

    def _refresh_recent_transaction(self) -> None:
        if self._undo_service is None:
            self.undo_button.setEnabled(False)
            self.view_transaction_button.hide()
            return
        try:
            unresolved = self._undo_service.unresolved_transaction()
            if unresolved is not None:
                self.state.unresolved_transaction_id = unresolved.transaction_id
                self.view_transaction_button.show()
                self.undo_button.setEnabled(False)
                self.execute_button.setEnabled(False)
                self.recent_transaction_label.setText(
                    f"检测到未解决的重命名事务: {unresolved.transaction_id} "
                    f"| root={unresolved.root} | 状态={unresolved.display_status.value}"
                )
                self.status_label.setProperty("state", "error")
                self.status_label.setText(
                    "检测到未解决的重命名事务。为防止进一步改变文件系统，新的重命名和普通撤销已暂时禁用。"
                    "请查看重命名历史中的事务详情与当前路径观察。"
                )
                self._refresh_status_style()
                return
            self.state.unresolved_transaction_id = None
            self.view_transaction_button.hide()
            transaction = self._undo_service.latest_transaction()
        except Exception:
            self.undo_button.setEnabled(False)
            self.state.unresolved_transaction_id = None
            self.view_transaction_button.hide()
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
        self.state.unresolved_transaction_id = None
        self.view_transaction_button.hide()
        self.recent_transaction_label.setText(
            f"最近一次重命名：{transaction.created_at:%Y-%m-%d %H:%M} | "
            f"可撤销：{pending} | 事务：{transaction.transaction_id}"
        )

    def _clear_preview(self) -> None:
        self._clear_highlight()
        self.preview_model.clear_preview()
        self.table.setEnabled(True)
        self.summary_label.setText("尚未生成预览。")
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
        self.status_label.setProperty("state", "success")
        self.status_label.setText(f"已从当前预览移除 {len(rows)} 项；未修改本地文件。")
        self._refresh_status_style()
        self._update_summary(self.state.preview)
        self._update_execute_button()

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
        self.status_label.setProperty("state", "success")
        self.status_label.setText("预览已清空；未修改本地文件。")
        self._refresh_status_style()
        self._update_execute_button()

    def _commit_preview(self, preview: OrganizerPreview) -> None:
        self.state.settings_generation = self._organizer_service.settings_generation
        self._clear_highlight()
        self.preview_model.replace_preview(preview)
        self.table.setEnabled(not self.is_busy() and not self.state.stale)
        self._update_preview_controls()
        if self.state.stale:
            self.execute_button.setEnabled(False)
            self.summary_label.setText("预览已失效：请重新扫描以刷新状态。")

    def _update_preview_controls(self) -> None:
        active = not self.is_busy() and not self.state.stale
        has_rows = self.state.preview is not None and bool(self.state.preview.plans)
        has_selection = bool(self.state.highlighted_keys)
        self.remove_button.setEnabled(active and has_rows and has_selection)
        # Clearing is also the safe escape hatch for a stale preview.
        self.clear_button.setEnabled(not self.is_busy() and has_rows)

    def _update_summary(self, preview: OrganizerPreview) -> None:
        ready = preview.count(RenamePlanStatus.READY)
        unchanged = preview.count(RenamePlanStatus.UNCHANGED)
        warning = sum(
            plan_presentation(plan) is StatusPresentation.WARNING
            for plan in preview.plans
        )
        conflict = preview.count(RenamePlanStatus.CONFLICT)
        failed = preview.count(RenamePlanStatus.LOOKUP_FAILED)
        blocked = sum(
            plan_presentation(plan) is StatusPresentation.BLOCKED
            for plan in preview.plans
        )
        self.summary_label.setText(
            f"共 {len(preview.plans)} 个 · 可执行 {ready} · 无需重命名 {unchanged} · "
            f"警告 {warning} · 阻止 {blocked} · 冲突 {conflict} · 查询失败 {failed} · "
            f"已跳过 {preview.skipped_count}"
        )

    def _refresh_status_style(self) -> None:
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
