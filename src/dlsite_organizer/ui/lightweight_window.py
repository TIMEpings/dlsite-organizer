"""Compact drag-and-drop window for the immediate, journalled rename flow."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import cast

from PySide6.QtCore import QThread, Signal, Slot
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from dlsite_organizer.app.settings import SettingsService
from dlsite_organizer.domain.organizer import RenamePlanStatus
from dlsite_organizer.domain.rename_execution import UndoResult
from dlsite_organizer.services.quick_rename import (
    QuickRenameResult,
    QuickRenameService,
    QuickRenameStatus,
)
from dlsite_organizer.services.undo_service import UndoService
from dlsite_organizer.ui.widgets.drop_zone import DirectoryDropZone
from dlsite_organizer.ui.workers.quick_rename_worker import QuickRenameWorker
from dlsite_organizer.ui.workers.rename_worker import RenameActionWorker


class LightweightWindow(QMainWindow):
    """A small, shared-services window whose drop is explicit quick-action intent."""

    full_mode_requested = Signal()
    settings_requested = Signal()

    def __init__(
        self,
        quick_rename_service: QuickRenameService,
        undo_service: UndoService,
        settings_service: SettingsService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._quick_rename_service = quick_rename_service
        self._undo_service = undo_service
        self._settings_service = settings_service
        self._thread: QThread | None = None
        self._worker: QuickRenameWorker | RenameActionWorker | None = None
        self._last_result: QuickRenameResult | None = None
        self._last_undo_result: UndoResult | None = None
        self.setWindowTitle("DLsite Organizer — 轻量模式")
        self.resize(540, 360)
        self.setMinimumSize(460, 280)
        self._build_ui()
        self.drop_zone.paths_dropped.connect(self._handle_drop)
        self.undo_button.clicked.connect(self.undo_recent)
        self.settings_button.clicked.connect(self.settings_requested.emit)
        self.full_mode_button.clicked.connect(self._request_full_mode)
        self._refresh_recent_transaction()

    def _build_ui(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)

        heading = QLabel("DLsite Organizer · 轻量模式")
        heading.setObjectName("pageTitle")
        layout.addWidget(heading)
        description = QLabel(
            "拖入一个或多个同一父目录下的 DLsite 作品文件夹，即按当前设置安全重命名。"
        )
        description.setObjectName("pageDescription")
        description.setWordWrap(True)
        layout.addWidget(description)

        self.drop_zone = DirectoryDropZone(
            "将 DLsite 作品文件夹拖到这里",
            "拖入后将立即按当前设置重命名",
        )
        layout.addWidget(self.drop_zone)

        self.status_label = QLabel("等待拖入作品文件夹。")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        recent_label = QLabel("最近操作：")
        recent_label.setObjectName("sectionLabel")
        layout.addWidget(recent_label)
        self.operation_list = QListWidget()
        self.operation_list.setObjectName("quickOperationList")
        self.operation_list.setMaximumHeight(105)
        layout.addWidget(self.operation_list, 1)

        actions = QHBoxLayout()
        self.undo_button = QPushButton("撤销最近一次")
        self.settings_button = QPushButton("设置")
        self.full_mode_button = QPushButton("完整模式")
        actions.addWidget(self.undo_button)
        actions.addStretch(1)
        actions.addWidget(self.settings_button)
        actions.addWidget(self.full_mode_button)
        layout.addLayout(actions)
        self.setCentralWidget(central)
        self.setStyleSheet(_STYLE)

    @Slot(object)
    def _handle_drop(self, value: object) -> None:
        if self._thread is not None:
            return
        paths = tuple(cast(Sequence[Path | str], value))
        if not paths:
            return
        self.operation_list.clear()
        self.status_label.setProperty("state", "loading")
        self.status_label.setText("正在查询 metadata 并准备安全重命名…")
        self._refresh_status_style()
        thread = QThread()
        worker = QuickRenameWorker(self._quick_rename_service, paths)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._show_progress)
        worker.result_ready.connect(self._show_result)
        worker.failed.connect(self._show_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._operation_finished)
        self._thread = thread
        self._worker = worker
        self._set_busy(True)
        thread.start()

    @Slot(str)
    def _show_progress(self, message: str) -> None:
        self.status_label.setProperty("state", "loading")
        self.status_label.setText(message)
        self._refresh_status_style()

    @Slot(object)
    def _show_result(self, value: object) -> None:
        result = cast(QuickRenameResult, value)
        self._last_result = result
        self.operation_list.clear()
        for plan in result.plans:
            code = plan.work_code or "、".join(plan.work_codes) or "—"
            target = plan.proposed_name or "—"
            if result.status is QuickRenameStatus.SUCCESS and plan.status is RenamePlanStatus.READY:
                outcome = "✓ 已重命名"
            elif plan.status is RenamePlanStatus.UNCHANGED:
                outcome = "无需重命名"
            else:
                outcome = plan.error or "未执行"
            self.operation_list.addItem(f"{code}  {outcome}\n{plan.current_name} → {target}")

        if result.status is QuickRenameStatus.SUCCESS:
            self.status_label.setProperty("state", "success")
            self.status_label.setText(
                f"✓ 已重命名 {result.execution.success_count if result.execution else 0} 个目录。"
            )
        elif result.status is QuickRenameStatus.NO_CHANGE:
            self.status_label.setProperty("state", "success")
            self.status_label.setText("无需重命名：目录已经是目标名称。")
        elif result.status is QuickRenameStatus.RECOVERY_REQUIRED:
            self.status_label.setProperty("state", "error")
            self.status_label.setText("需要恢复：最近一次重命名需要恢复，请先处理该事务。")
        elif result.mutated:
            self.status_label.setProperty("state", "error")
            self.status_label.setText("部分操作已完成；可以撤销最近一次已记录的事务。")
        else:
            self.status_label.setProperty("state", "error")
            self.status_label.setText(
                "未执行任何文件修改。" + (f" {result.error}" if result.error else "")
            )
        self._refresh_status_style()
        self._refresh_recent_transaction()

    @Slot()
    def undo_recent(self) -> None:
        if self._thread is not None:
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
        answer = QMessageBox.question(
            self,
            "确认撤销重命名",
            f"将撤销最近一次重命名。 {count} 个目录将恢复到执行前的名称。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._start_action(
            lambda callback: self._undo_service.undo(
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
        worker.failed.connect(self._show_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._operation_finished)
        self._thread = thread
        self._worker = worker
        self._set_busy(True)
        thread.start()

    @Slot(int, int, str, str)
    def _show_action_progress(self, completed: int, total: int, source: str, target: str) -> None:
        self.status_label.setProperty("state", "loading")
        self.status_label.setText(f"正在处理 {completed} / {total}: {source} → {target}")
        self._refresh_status_style()

    @Slot(object)
    def _show_undo_result(self, value: object) -> None:
        result = cast(UndoResult, value)
        self._last_undo_result = result
        if result.status.name == "UNDONE":
            self.status_label.setProperty("state", "success")
            self.status_label.setText(f"撤销完成：恢复 {result.success_count} 个目录。")
            self.operation_list.addItem(f"撤销  ✓ 已恢复 {result.success_count} 个目录")
        elif result.status.name == "RECOVERY_REQUIRED":
            self._show_error("需要恢复：撤销状态需要恢复，请先处理该事务。")
        else:
            self._show_error(result.error or "撤销未执行。")
        self._refresh_status_style()
        self._refresh_recent_transaction()

    @Slot(str)
    def _show_error(self, message: str) -> None:
        self.status_label.setProperty("state", "error")
        self.status_label.setText(message)
        self._refresh_status_style()

    @Slot()
    def _operation_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._set_busy(False)
        self._refresh_recent_transaction()

    @Slot()
    def _request_full_mode(self) -> None:
        if self._thread is not None:
            return
        self.full_mode_requested.emit()

    def _set_busy(self, busy: bool) -> None:
        self.drop_zone.setEnabled(not busy)
        self.undo_button.setEnabled(False if busy else self.undo_button.isEnabled())
        self.settings_button.setEnabled(not busy)
        self.full_mode_button.setEnabled(not busy)

    def _refresh_recent_transaction(self) -> None:
        try:
            unresolved = self._undo_service.unresolved_transaction()
            if unresolved is not None:
                self.drop_zone.setEnabled(False)
                self.undo_button.setEnabled(False)
                if self._thread is None:
                    self.status_label.setProperty("state", "error")
                    self.status_label.setText("最近一次重命名需要恢复，请先处理该事务。")
                    self._refresh_status_style()
                return
            transaction = self._undo_service.latest_transaction()
        except Exception:
            self.drop_zone.setEnabled(False)
            self.undo_button.setEnabled(False)
            self.status_label.setProperty("state", "error")
            self.status_label.setText("事务日志不可用，已禁用轻量模式重命名。")
            self._refresh_status_style()
            return
        if self._thread is not None:
            return
        if transaction is None:
            self.drop_zone.setEnabled(True)
            self.undo_button.setEnabled(False)
            return
        pending = sum(
            operation.status.name == "SUCCESS" and operation.undo_status.name != "SUCCESS"
            for operation in transaction.operations
        )
        self.drop_zone.setEnabled(True)
        self.undo_button.setEnabled(pending > 0)

    def is_busy(self) -> bool:
        return self._thread is not None

    def _refresh_status_style(self) -> None:
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def closeEvent(self, event: QCloseEvent) -> None:
        if self.is_busy():
            event.ignore()
            QMessageBox.information(self, "任务进行中", "请等待当前任务结束后再退出。")
            return
        event.accept()


_STYLE = """
QMainWindow, QWidget { background: #f5f7fb; }
QWidget { color: #1e293b; font-family: "Segoe UI", "Microsoft YaHei UI"; font-size: 14px; }
#pageTitle { font-size: 21px; font-weight: 700; color: #111827; }
#pageDescription { color: #64748b; }
#sectionLabel { font-weight: 600; color: #334155; }
#dropZone { background: #ffffff; border: 2px dashed #a9b8d0; border-radius: 10px; }
#dropZone[dragActive="true"] { background: #edf3ff; border-color: #3867e8; }
#dropZoneTitle { color: #23499d; font-size: 16px; font-weight: 600; }
#dropZoneDescription { color: #64748b; }
#statusLabel { color: #64748b; min-height: 22px; }
#statusLabel[state="loading"] { color: #315fc9; }
#statusLabel[state="success"] { color: #16805b; }
#statusLabel[state="error"] { color: #c53b47; }
QPushButton { background: white; border: 1px solid #cbd5e1; border-radius: 7px; padding: 7px 12px; }
QPushButton:hover { background: #f1f5f9; }
QPushButton:disabled { color: #94a3b8; background: #f1f5f9; }
QListWidget { background: white; border: 1px solid #e2e8f0; border-radius: 7px; }
"""
