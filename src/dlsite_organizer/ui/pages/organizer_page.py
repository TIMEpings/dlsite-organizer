"""Organizer page: review RenamePlans and execute confirmed safe renames."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast

from PySide6.QtCore import Qt, QThread, Slot
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDragMoveEvent, QDropEvent, QFont, QIcon
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
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
from dlsite_organizer.services.organizer import OrganizerPreview, OrganizerService, WorkLookup
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.undo_service import UndoService
from dlsite_organizer.ui.widgets.drop_zone import DirectoryDropZone, local_directory_paths
from dlsite_organizer.ui.workers.organizer_worker import OrganizerWorker
from dlsite_organizer.ui.workers.rename_worker import RenameActionWorker

logger = logging.getLogger(__name__)


class _PathItem(Protocol):
    @property
    def source_path(self) -> Path: ...


class _ScanMode(StrEnum):
    """How one completed worker result joins the current preview collection."""

    REPLACE = "replace"
    APPEND = "append"


class _StatusPresentation(StrEnum):
    """UI-only severity grouping; the domain RenamePlanStatus remains unchanged."""

    NORMAL = "normal"
    WARNING = "warning"
    BLOCKED = "blocked"


class OrganizerPage(QWidget):
    """Present scan results and expose explicit rename and undo actions."""

    _COLUMNS = ("状态", "当前目录名", "RJcode", "社团", "标题", "目标目录名")

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
        self._scan_mode = _ScanMode.REPLACE
        self._next_scan_mode = _ScanMode.REPLACE
        self._last_execution_result: RenameExecutionResult | None = None
        self._last_undo_result: UndoResult | None = None
        self._build_ui()

        self.browse_button.clicked.connect(self.choose_root)
        # QPushButton.clicked carries a checked: bool argument.  Keep that
        # signal payload away from start_scan(), whose optional argument is
        # reserved for selective dropped-folder scans.
        self.scan_button.clicked.connect(self._start_scan_from_button)
        self.cancel_button.clicked.connect(self.cancel_scan)
        self.execute_button.clicked.connect(self.execute_rename)
        self.undo_button.clicked.connect(self.undo_recent)
        self.table.itemChanged.connect(self._selection_changed)
        self.table.itemSelectionChanged.connect(self._table_selection_changed)
        self.remove_button.clicked.connect(self.remove_selected)
        self.clear_button.clicked.connect(self.clear_preview)
        self._refresh_recent_transaction()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 28, 36, 28)
        layout.setSpacing(14)

        heading = QLabel("整理")
        heading.setObjectName("pageTitle")
        description = QLabel(
            "可选择根目录，也可将文件夹拖入此窗口。完整模式拖入只生成预览，不会立即重命名。"
        )
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

        self.table = QTableWidget(0, len(self._COLUMNS))
        self.table.setHorizontalHeaderLabels(self._COLUMNS)
        self.table.setObjectName("organizerTable")
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
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
    def _start_scan_from_button(self) -> None:
        """Start an ordinary root scan from the button without its bool payload."""
        self.start_scan()

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

        self._scan_mode = self._next_scan_mode
        self._next_scan_mode = _ScanMode.REPLACE
        self._set_loading(True)
        self.status_label.setProperty("state", "loading")
        self.status_label.setText("正在扫描目录…")
        self._refresh_status_style()

        thread: QThread | None = None
        worker: OrganizerWorker | None = None
        try:
            thread = QThread()
            thread.setObjectName("organizer-scan")
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
            self._scan_mode = _ScanMode.REPLACE
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
        if self._preview_stale:
            self._show_error("当前预览已因设置变化失效；请先重新扫描或清空后再拖放。")
            return
        paths = tuple(cast(Sequence[Path | str], value))
        try:
            selection = self._drop_input_service.validate_full_drop(paths)
        except DropInputError as exc:
            self._show_error(exc.user_message)
            return
        if self._preview is not None and normalized_path_identity(
            self._preview.root_path
        ) != normalized_path_identity(selection.root_path):
            self._show_error("追加预览必须来自当前预览的同一作品根目录。")
            return
        self.set_root_path(selection.root_path)
        # Keep start_scan compatible with existing callers while making this
        # drop explicitly append/merge instead of replacing the preview.
        self._next_scan_mode = _ScanMode.APPEND
        try:
            self.start_scan(selection.selected_paths)
        finally:
            self._next_scan_mode = _ScanMode.REPLACE

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
        previous_checked = self._checked_source_keys()
        if preview.cancelled:
            if self._scan_mode is _ScanMode.APPEND:
                self._show_error("追加预览已取消；当前预览保持不变。")
                return
            if self._preview is not None:
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

        if self._scan_mode is _ScanMode.APPEND and self._preview is not None:
            preview = _merge_previews(self._preview, preview)
            self._commit_preview(preview, checked_source_keys=previous_checked)
            self.status_label.setProperty("state", "success")
            self.status_label.setText("追加预览完成；未修改本地文件。")
        else:
            self._commit_preview(preview)

        if (
            self._scan_mode is not _ScanMode.APPEND
            and not preview.plans
            and not preview.scan.candidates
        ):
            self.status_label.setProperty("state", "success")
            self.status_label.setText("未发现可整理的作品文件夹；未修改本地文件。")
        elif self._scan_mode is not _ScanMode.APPEND:
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
        return self._preview

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

    @Slot()
    def _table_selection_changed(self) -> None:
        self._update_preview_controls()
        self._update_execute_button()

    def refresh_mutation_state(self) -> None:
        """Refresh only journal-backed controls after another UI entry point mutates."""
        self._refresh_recent_transaction()
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
        root = (
            str(self._preview.root_path)
            if self._preview is not None
            else self.root_input.text().strip()
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
        presentation = _plan_presentation(plan)
        status_label = _plan_status_label(plan.status)
        if presentation is not _StatusPresentation.NORMAL:
            status_label += "（悬停查看原因）"
        values = (
            status_label,
            plan.current_name,
            work_code,
            maker,
            title,
            plan.proposed_name or "—",
        )
        diagnostics = _plan_diagnostics(plan)
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column == 0 and presentation is not _StatusPresentation.NORMAL:
                item.setFont(_emphasized_font(item))
                item.setIcon(self._status_icon(presentation))
                detail = diagnostics or "该项目没有生成可执行的重命名计划。"
                item.setToolTip(
                    f"{status_label}\n原因：{detail}\n\n悬停查看具体原因。"
                )
                item.setData(
                    Qt.ItemDataRole.AccessibleTextRole,
                    f"{status_label}。原因：{detail}",
                )
            if column == 0 and plan.status is RenamePlanStatus.READY:
                item.setFlags(
                    item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled
                )
                item.setCheckState(Qt.CheckState.Checked)
            elif column == 0:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            if column == 0 and diagnostics and presentation is _StatusPresentation.NORMAL:
                item.setToolTip(
                    f"{value}\n原因：{diagnostics}\n\n悬停查看具体原因。"
                )
                item.setData(
                    Qt.ItemDataRole.AccessibleTextRole,
                    f"{value}。原因：{diagnostics}",
                )
            if column == 1:
                item.setToolTip(str(plan.source_path))
            if column == 5 and plan.target_path is not None:
                item.setToolTip(str(plan.target_path))
            self.table.setItem(row, column, item)

    def _status_icon(self, presentation: _StatusPresentation) -> QIcon:
        standard_pixmap = (
            QStyle.StandardPixmap.SP_MessageBoxWarning
            if presentation is _StatusPresentation.WARNING
            else QStyle.StandardPixmap.SP_MessageBoxCritical
        )
        return self.style().standardIcon(standard_pixmap)

    @Slot(str)
    def _show_error(self, message: str) -> None:
        if self._worker is not None and self._preview is not None:
            message = f"{message} 当前预览保持不变。"
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
        logger.debug("organizer scan thread finished; clearing UI busy state")
        self._thread = None
        self._worker = None
        self._scan_mode = _ScanMode.REPLACE
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
        self.table.setEnabled(not loading and not self._preview_stale)
        self._update_preview_controls()

    def is_busy(self) -> bool:
        """Return whether the organizer worker thread is active."""
        return self._thread is not None

    def _set_loading(self, loading: bool) -> None:
        self.drop_zone.setEnabled(not loading)
        self.root_input.setEnabled(not loading)
        self.browse_button.setEnabled(not loading)
        self.scan_button.setEnabled(not loading)
        self.cancel_button.setEnabled(loading)
        self.table.setEnabled(not loading and not self._preview_stale)
        self._update_preview_controls()
        if loading:
            self.execute_button.setEnabled(False)
            self.undo_button.setEnabled(False)

    def _invalidate_preview(self) -> None:
        self._preview_stale = True
        self.table.setEnabled(False)
        self.execute_button.setEnabled(False)
        self.summary_label.setText("预览已失效：请重新扫描以刷新状态。")
        self._update_preview_controls()

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
        self._update_preview_controls()

    @Slot()
    def remove_selected(self) -> None:
        """Remove selected rows from the in-memory preview only."""
        if self.is_busy() or self._preview is None or self._preview_stale:
            return
        rows = sorted(
            {index.row() for index in self.table.selectionModel().selectedRows()},
            reverse=True,
        )
        if not rows:
            return
        checked = self._checked_source_keys()
        remaining = tuple(
            plan for row, plan in enumerate(self._preview.plans) if row not in rows
        )
        self._preview = replace(self._preview, plans=remaining)
        self._render_preview(self._preview, checked_source_keys=checked)
        self.status_label.setProperty("state", "success")
        self.status_label.setText(f"已从当前预览移除 {len(rows)} 项；未修改本地文件。")
        self._refresh_status_style()
        self._update_summary(self._preview)
        self._update_execute_button()

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

    def _checked_source_keys(self) -> set[str]:
        checked: set[str] = set()
        if self._preview is None:
            return checked
        for row, plan in enumerate(self._preview.plans):
            item = self.table.item(row, 0)
            if item is not None and item.checkState() is Qt.CheckState.Checked:
                checked.add(normalized_path_identity(plan.source_path))
        return checked

    def _commit_preview(
        self,
        preview: OrganizerPreview,
        *,
        checked_source_keys: set[str] | None = None,
    ) -> None:
        self._preview = preview
        self._preview_stale = False
        self._render_preview(preview, checked_source_keys=checked_source_keys)

    def _render_preview(
        self,
        preview: OrganizerPreview,
        *,
        checked_source_keys: set[str] | None = None,
    ) -> None:
        self.table.blockSignals(True)
        try:
            self.table.setRowCount(0)
            for plan in preview.plans:
                self._append_plan(plan)
                if plan.status is RenamePlanStatus.READY and checked_source_keys is not None:
                    item = self.table.item(self.table.rowCount() - 1, 0)
                    if item is not None:
                        item.setCheckState(
                            Qt.CheckState.Checked
                            if normalized_path_identity(plan.source_path) in checked_source_keys
                            else Qt.CheckState.Unchecked
                        )
        finally:
            self.table.blockSignals(False)
        self.table.setEnabled(not self.is_busy() and not self._preview_stale)
        self._update_preview_controls()

    def _update_preview_controls(self) -> None:
        active = not self.is_busy() and not self._preview_stale
        has_rows = self._preview is not None and bool(self._preview.plans)
        has_selection = bool(self.table.selectionModel().selectedRows())
        self.remove_button.setEnabled(active and has_rows and has_selection)
        # Clearing is also the safe escape hatch for a stale preview.
        self.clear_button.setEnabled(not self.is_busy() and has_rows)

    def _update_summary(self, preview: OrganizerPreview) -> None:
        ready = preview.count(RenamePlanStatus.READY)
        unchanged = preview.count(RenamePlanStatus.UNCHANGED)
        warning = sum(
            _plan_presentation(plan) is _StatusPresentation.WARNING
            for plan in preview.plans
        )
        conflict = preview.count(RenamePlanStatus.CONFLICT)
        failed = preview.count(RenamePlanStatus.LOOKUP_FAILED)
        blocked = sum(
            _plan_presentation(plan) is _StatusPresentation.BLOCKED
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


def _plan_status_label(status: RenamePlanStatus) -> str:
    """Translate internal plan states into clear user-facing labels."""
    return {
        RenamePlanStatus.READY: "可执行",
        RenamePlanStatus.UNCHANGED: "无需重命名",
        RenamePlanStatus.CONFLICT: "冲突",
        RenamePlanStatus.LOOKUP_FAILED: "查询失败",
        RenamePlanStatus.INVALID_CODE: "无效 RJcode",
        RenamePlanStatus.AMBIGUOUS_CODE: "RJcode 不唯一",
        RenamePlanStatus.INVALID_TARGET: "目标名称无效",
        RenamePlanStatus.CANCELLED: "已取消",
    }.get(status, "需检查")


def _plan_diagnostics(plan: RenamePlan) -> str:
    """Keep non-normal plan explanations available from the status cell."""
    messages: list[str] = []
    if plan.error:
        messages.append(plan.error)
    messages.extend(plan.warnings)
    return "\n".join(dict.fromkeys(messages))


def _plan_presentation(plan: RenamePlan) -> _StatusPresentation:
    """Map existing domain states to a theme-safe UI severity."""
    if plan.status in {RenamePlanStatus.READY, RenamePlanStatus.UNCHANGED}:
        return (
            _StatusPresentation.WARNING
            if plan.warnings
            else _StatusPresentation.NORMAL
        )
    if plan.status is RenamePlanStatus.CANCELLED:
        return _StatusPresentation.WARNING
    return _StatusPresentation.BLOCKED


def _emphasized_font(item: QTableWidgetItem) -> QFont:
    font = item.font()
    font.setBold(True)
    return font


def _merge_previews(
    current: OrganizerPreview,
    incoming: OrganizerPreview,
) -> OrganizerPreview:
    """Upsert a successful drop result while retaining existing row order."""
    candidates = _merge_path_items(current.scan.candidates, incoming.scan.candidates)
    skipped = _merge_path_items(current.scan.skipped, incoming.scan.skipped)
    scan = replace(
        current.scan,
        candidates=candidates,
        skipped=skipped,
        cancelled=False,
    )
    return replace(
        current,
        scan=scan,
        lookups=_merge_lookup_items(current.lookups, incoming.lookups),
        plans=_merge_path_items(current.plans, incoming.plans),
        cancelled=False,
    )


def _merge_path_items[PathItemT: _PathItem](
    existing: Sequence[PathItemT], incoming: Sequence[PathItemT]
) -> tuple[PathItemT, ...]:
    merged = list(existing)
    positions = {
        normalized_path_identity(item.source_path): index
        for index, item in enumerate(merged)
    }
    for item in incoming:
        key = normalized_path_identity(item.source_path)
        existing_index = positions.get(key)
        if existing_index is None:
            positions[key] = len(merged)
            merged.append(item)
        else:
            merged[existing_index] = item
    return tuple(merged)


def _merge_lookup_items(
    existing: Sequence[WorkLookup], incoming: Sequence[WorkLookup]
) -> tuple[WorkLookup, ...]:
    merged = list(existing)
    positions = {item.work_code: index for index, item in enumerate(merged)}
    for item in incoming:
        existing_index = positions.get(item.work_code)
        if existing_index is None:
            positions[item.work_code] = len(merged)
            merged.append(item)
        else:
            merged[existing_index] = item
    return tuple(merged)
