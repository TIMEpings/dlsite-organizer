"""Organizer page: scan folders and review a read-only RenamePlan table."""

from __future__ import annotations

from pathlib import Path
from typing import cast

from PySide6.QtCore import QThread, Slot
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dlsite_organizer.domain.organizer import RenamePlan, RenamePlanStatus
from dlsite_organizer.services.organizer import OrganizerPreview, OrganizerService
from dlsite_organizer.ui.workers.organizer_worker import OrganizerWorker


class OrganizerPage(QWidget):
    """Present scan, lookup and rename-plan results without an execute action."""

    _COLUMNS = ("状态", "当前目录名", "RJcode", "社团", "标题", "目标目录名", "详情")

    def __init__(self, organizer_service: OrganizerService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._organizer_service = organizer_service
        self._thread: QThread | None = None
        self._worker: OrganizerWorker | None = None
        self._build_ui()

        self.browse_button.clicked.connect(self.choose_root)
        self.scan_button.clicked.connect(self.start_scan)
        self.cancel_button.clicked.connect(self.cancel_scan)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 28, 36, 28)
        layout.setSpacing(14)

        heading = QLabel("整理")
        heading.setObjectName("pageTitle")
        description = QLabel(
            "扫描作品根目录，查询 RJcode 并生成重命名预览。当前版本不会修改本地文件。"
        )
        description.setObjectName("pageDescription")
        description.setWordWrap(True)
        layout.addWidget(heading)
        layout.addWidget(description)

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
    def start_scan(self) -> None:
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
        worker = OrganizerWorker(self._organizer_service, root)
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
        self.table.setRowCount(0)
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

    @Slot()
    def _scan_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._set_loading(False)

    def is_busy(self) -> bool:
        """Return whether the organizer worker thread is active."""
        return self._thread is not None

    def _set_loading(self, loading: bool) -> None:
        self.root_input.setEnabled(not loading)
        self.browse_button.setEnabled(not loading)
        self.scan_button.setEnabled(not loading)
        self.cancel_button.setEnabled(loading)

    def _clear_preview(self) -> None:
        self.table.setRowCount(0)
        self.summary_label.setText("尚未生成预览。")

    def _refresh_status_style(self) -> None:
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)
