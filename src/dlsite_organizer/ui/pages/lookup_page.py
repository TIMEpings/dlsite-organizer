"""Complete v0.1 manual RJcode lookup page."""

from __future__ import annotations

from typing import cast

from PySide6.QtCore import Qt, QThread, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupResult, LookupService
from dlsite_organizer.ui.workers.lookup_worker import LookupWorker


class LookupPage(QWidget):
    """Collect a code and present the normalized lookup result."""

    def __init__(
        self,
        lookup_service: LookupService,
        cover_service: CoverService,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._lookup_service = lookup_service
        self._cover_service = cover_service
        self._thread: QThread | None = None
        self._worker: LookupWorker | None = None
        self._formatted_name = ""

        self._build_ui()
        self.query_button.clicked.connect(self.start_lookup)
        self.code_input.returnPressed.connect(self.start_lookup)
        self.copy_button.clicked.connect(self.copy_name)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 28, 36, 28)
        layout.setSpacing(18)

        heading = QLabel("查询作品")
        heading.setObjectName("pageTitle")
        description = QLabel("输入 RJcode，读取当前作品信息并生成安全的格式化名称。")
        description.setObjectName("pageDescription")
        layout.addWidget(heading)
        layout.addWidget(description)

        query_row = QHBoxLayout()
        query_row.setSpacing(10)
        self.code_input = QLineEdit()
        self.code_input.setPlaceholderText("例如 RJ01609020")
        self.code_input.setClearButtonEnabled(True)
        self.code_input.setMinimumHeight(40)
        self.query_button = QPushButton("查询")
        self.query_button.setObjectName("primaryButton")
        self.query_button.setMinimumSize(96, 40)
        query_row.addWidget(self.code_input, 1)
        query_row.addWidget(self.query_button)
        layout.addLayout(query_row)

        self.status_label = QLabel("请输入 RJcode 开始查询。")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        result_frame = QFrame()
        result_frame.setObjectName("resultCard")
        result_layout = QHBoxLayout(result_frame)
        result_layout.setContentsMargins(22, 22, 22, 22)
        result_layout.setSpacing(24)

        self.cover_label = QLabel("暂无封面")
        self.cover_label.setObjectName("coverPlaceholder")
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setFixedSize(210, 280)
        result_layout.addWidget(self.cover_label)

        details = QVBoxLayout()
        details.setSpacing(14)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(12)
        self.workno_value = _value_label()
        self.title_value = _value_label()
        self.maker_value = _value_label()
        self.release_value = _value_label()
        self.series_value = _value_label()
        self.cvs_value = _value_label()
        self.tags_value = _value_label()
        form.addRow("RJcode", self.workno_value)
        form.addRow("标题", self.title_value)
        form.addRow("社团", self.maker_value)
        form.addRow("发售日期", self.release_value)
        form.addRow("系列", self.series_value)
        form.addRow("CV", self.cvs_value)
        form.addRow("标签", self.tags_value)
        details.addLayout(form)
        details.addStretch(1)
        result_layout.addLayout(details, 1)
        layout.addWidget(result_frame, 1)

        output_label = QLabel("格式化名称")
        output_label.setObjectName("sectionLabel")
        layout.addWidget(output_label)
        output_row = QHBoxLayout()
        output_row.setSpacing(10)
        self.name_output = QLineEdit()
        self.name_output.setReadOnly(True)
        self.name_output.setPlaceholderText("查询成功后显示")
        self.name_output.setMinimumHeight(40)
        self.copy_button = QPushButton("复制名称")
        self.copy_button.setMinimumSize(108, 40)
        self.copy_button.setEnabled(False)
        output_row.addWidget(self.name_output, 1)
        output_row.addWidget(self.copy_button)
        layout.addLayout(output_row)

    @Slot()
    def start_lookup(self) -> None:
        """Start one background lookup unless another is still active."""
        if self._thread is not None:
            return
        self._set_loading(True)
        self._clear_result()
        self.status_label.setProperty("state", "loading")
        self.status_label.setText("正在查询…")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

        thread = QThread()
        worker = LookupWorker(
            self._lookup_service,
            self._cover_service,
            self.code_input.text(),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.result_ready.connect(self._show_result)
        worker.cover_ready.connect(self._show_cover)
        worker.failed.connect(self._show_error)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._lookup_finished)
        self._thread = thread
        self._worker = worker
        thread.start()

    @Slot(object)
    def _show_result(self, value: object) -> None:
        result = cast(LookupResult, value)
        work = result.work
        self.code_input.setText(work.workno)
        self.workno_value.setText(work.workno)
        self.title_value.setText(work.title)
        maker = work.maker_name or "—"
        if work.maker_id and work.maker_name:
            maker = f"{work.maker_name} ({work.maker_id})"
        self.maker_value.setText(maker)
        self.release_value.setText(work.release_date.isoformat() if work.release_date else "—")
        self.series_value.setText(work.series_name or "—")
        self.cvs_value.setText("、".join(work.cvs) if work.cvs else "—")
        self.tags_value.setText("、".join(work.tags) if work.tags else "—")
        self._formatted_name = result.formatted_name
        self.name_output.setText(result.formatted_name)
        self.copy_button.setEnabled(True)
        self.status_label.setProperty("state", "success")
        self.status_label.setText("查询完成。")
        self._refresh_status_style()

    @Slot(bytes)
    def _show_cover(self, content: bytes) -> None:
        pixmap = QPixmap()
        if not pixmap.loadFromData(content):
            return
        scaled = pixmap.scaled(
            self.cover_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.cover_label.setText("")
        self.cover_label.setPixmap(scaled)

    @Slot(str)
    def _show_error(self, message: str) -> None:
        self.status_label.setProperty("state", "error")
        self.status_label.setText(message)
        self._refresh_status_style()

    @Slot()
    def _lookup_finished(self) -> None:
        self._thread = None
        self._worker = None
        self._set_loading(False)

    @Slot()
    def copy_name(self) -> None:
        """Copy the last successful formatted name."""
        if not self._formatted_name:
            return
        clipboard = QApplication.clipboard()
        clipboard.setText(self._formatted_name)
        self.status_label.setProperty("state", "success")
        self.status_label.setText("已复制格式化名称。")
        self._refresh_status_style()

    def is_busy(self) -> bool:
        """Return whether a worker thread is currently active."""
        return self._thread is not None

    def _set_loading(self, loading: bool) -> None:
        self.query_button.setEnabled(not loading)
        self.code_input.setEnabled(not loading)

    def _clear_result(self) -> None:
        self._formatted_name = ""
        self.copy_button.setEnabled(False)
        self.name_output.clear()
        for label in (
            self.workno_value,
            self.title_value,
            self.maker_value,
            self.release_value,
            self.series_value,
            self.cvs_value,
            self.tags_value,
        ):
            label.setText("—")
        self.cover_label.clear()
        self.cover_label.setText("暂无封面")

    def _refresh_status_style(self) -> None:
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)


def _value_label() -> QLabel:
    label = QLabel("—")
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return label
