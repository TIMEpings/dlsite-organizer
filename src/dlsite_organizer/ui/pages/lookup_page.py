"""Public DLsite work-number lookup page."""

from __future__ import annotations
# ruff: noqa

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
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from dlsite_organizer.domain.relation import RelationType, TranslationRole
from dlsite_organizer.domain.work import AgeCategory, TranslationAttribution, WorkLanguage
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupFreshness, LookupResult, LookupService
from dlsite_organizer.services.translation_relations import (
    TranslationAnalysis,
    TranslationAnalysisStatus,
)
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
        self.refresh_button = QPushButton("强制刷新")

        self._build_ui()
        self.query_button.clicked.connect(self.start_lookup)
        self.refresh_button.clicked.connect(self.force_refresh)
        self.code_input.returnPressed.connect(self.start_lookup)
        self.copy_button.clicked.connect(self.copy_name)

    def _build_ui(self) -> None:
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        scroll_area = QScrollArea()
        scroll_area.setObjectName("lookupScrollArea")
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(36, 28, 36, 28)
        layout.setSpacing(18)

        heading = QLabel("查询作品")
        heading.setObjectName("pageTitle")
        description = QLabel("读取当前作品信息并生成安全的格式化名称。")
        description.setObjectName("pageDescription")
        layout.addWidget(heading)
        layout.addWidget(description)

        query_row = QHBoxLayout()
        query_row.setSpacing(10)
        self.code_input = QLineEdit()
        self.code_input.setPlaceholderText("输入完整RJ|BJ|VJ号")
        self.code_input.setClearButtonEnabled(True)
        self.code_input.setMinimumHeight(40)
        self.query_button = QPushButton("查询")
        self.query_button.setObjectName("primaryButton")
        self.query_button.setMinimumSize(96, 40)
        query_row.addWidget(self.code_input, 1)
        query_row.addWidget(self.query_button)
        query_row.addWidget(self.refresh_button)
        layout.addLayout(query_row)

        self.status_label = QLabel("请输入作品编号开始查询。")
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
        self.maker_id_value = _value_label()
        self.release_value = _value_label()
        self.series_value = _value_label()
        self.cvs_value = _value_label()
        self.tags_value = _value_label()
        self.language_value = _value_label()
        self.age_value = _value_label()
        form.addRow("作品编号", self.workno_value)
        form.addRow("标题", self.title_value)
        form.addRow("社团", self.maker_value)
        form.addRow("社团编号", self.maker_id_value)
        form.addRow("系列", self.series_value)
        form.addRow("CV", self.cvs_value)
        form.addRow("标签", self.tags_value)
        form.addRow("语言", self.language_value)
        form.addRow("年龄分级", self.age_value)
        form.addRow("发售日期", self.release_value)
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

        relation_frame = QFrame()
        self.relation_frame = relation_frame
        relation_frame.setObjectName("relationCard")
        relation_layout = QVBoxLayout(relation_frame)
        relation_layout.setContentsMargins(22, 18, 22, 18)
        relation_layout.setSpacing(10)
        relation_heading = QLabel("作品关系")
        relation_heading.setObjectName("sectionLabel")
        relation_layout.addWidget(relation_heading)
        relation_form = QFormLayout()
        relation_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignTop)
        relation_form.setHorizontalSpacing(18)
        relation_form.setVerticalSpacing(8)
        self.relation_role_value = _value_label()
        self.relation_source_value = _value_label()
        self.relation_confidence_value = _value_label()
        self.relation_language_value = _value_label()
        self.relation_attribution_value = _value_label()
        relation_form.addRow("角色", self.relation_role_value)
        relation_form.addRow("来源", self.relation_source_value)
        relation_form.addRow("可信度", self.relation_confidence_value)
        relation_form.addRow("语言", self.relation_language_value)
        relation_form.addRow("翻译署名", self.relation_attribution_value)
        relation_layout.addLayout(relation_form)
        self.relation_details_value = _value_label()
        relation_layout.addWidget(self.relation_details_value)
        relation_layout.addWidget(QLabel("历史确认关系"))
        self.historical_relations_value = _value_label()
        relation_layout.addWidget(self.historical_relations_value)
        layout.addWidget(relation_frame)
        self._clear_relations()
        relation_frame.setVisible(False)
        scroll_area.setWidget(content)
        page_layout.addWidget(scroll_area)

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

    @Slot()
    def force_refresh(self) -> None:
        if self._thread is not None:
            return
        self._start_lookup_worker(force_refresh=True)

    def _start_lookup_worker(self, *, force_refresh: bool = False) -> None:
        self.start_lookup() if not force_refresh else self._run_lookup(force_refresh=True)

    def _run_lookup(self, *, force_refresh: bool) -> None:
        self._set_loading(True)
        self._clear_result()
        thread = QThread()
        worker = LookupWorker(
            self._lookup_service, self._cover_service, self.code_input.text(), force_refresh
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
        self._thread, self._worker = thread, worker
        thread.start()

    @Slot(object)
    def _show_result(self, value: object) -> None:
        result = cast(LookupResult, value)
        self._current_result = result
        work = result.work
        self.code_input.setText(work.workno)
        self.workno_value.setText(work.workno)
        self.title_value.setText(work.title)
        self.maker_value.setText(work.maker_name or "—")
        self.maker_id_value.setText(work.maker_id or "—")
        self.release_value.setText(work.release_date.isoformat() if work.release_date else "—")
        self.series_value.setText(work.series_name or "—")
        self.cvs_value.setText("、".join(work.cvs) if work.cvs else "—")
        self.tags_value.setText("、".join(work.tags) if work.tags else "—")
        self.language_value.setText(_language_label(work.language))
        self.age_value.setText(_age_label(work.age_category))
        self._formatted_name = result.formatted_name
        self.name_output.setText(result.formatted_name)
        self.copy_button.setEnabled(True)
        self._show_relations(
            result.translation,
            result.historical_relations,
            result.translation_attribution,
        )
        self.status_label.setProperty("state", "success")
        label = {
            LookupFreshness.LIVE: "实时",
            LookupFreshness.CACHE_FRESH: "缓存",
            LookupFreshness.CACHE_STALE_FALLBACK: "旧缓存",
        }[result.freshness]
        self.status_label.setText(f"查询完成 · {label}")
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
        self.refresh_button.setEnabled(not loading)
        self.code_input.setEnabled(not loading)

    def _clear_result(self) -> None:
        self._formatted_name = ""
        self.copy_button.setEnabled(False)
        self.name_output.clear()
        for label in (
            self.workno_value,
            self.title_value,
            self.maker_value,
            self.maker_id_value,
            self.release_value,
            self.series_value,
            self.cvs_value,
            self.tags_value,
            self.language_value,
            self.age_value,
        ):
            label.setText("—")
        self.cover_label.clear()
        self.cover_label.setText("暂无封面")
        self._clear_relations()

    def _show_relations(
        self,
        analysis: TranslationAnalysis,
        historical=None,
        attribution: TranslationAttribution | None = None,
    ) -> None:
        """Render only explicit and historical confirmed relation facts."""
        has_historical = bool(
            historical is not None and (historical.outgoing or historical.incoming)
        )
        has_current = bool(
            analysis.relations
            or analysis.role is not None
            or analysis.language
            or attribution is not None
            or analysis.status in {
                TranslationAnalysisStatus.INVALID,
                TranslationAnalysisStatus.INCOMPLETE,
            }
        )
        self.relation_frame.setVisible(has_current or has_historical)
        self.relation_role_value.setText(_role_label(analysis.role))
        has_translation_info = analysis.status is not TranslationAnalysisStatus.NO_INFORMATION
        self.relation_source_value.setText(
            "DLsite 明确提供" if has_translation_info else "—"
        )
        self.relation_confidence_value.setText(_confidence_label(analysis.status))
        self.relation_language_value.setText(analysis.language or "—")
        self.relation_attribution_value.setText(_attribution_label(attribution))

        relation_lines = [
            f"{_relation_label(relation.relation_type)}：{relation.target_workno}"
            for relation in analysis.relations
        ]
        if analysis.status is TranslationAnalysisStatus.NO_INFORMATION:
            message = "作品关系：未发现明确关系"
        elif analysis.status is TranslationAnalysisStatus.INVALID:
            message = "DLsite 关系信息存在矛盾，未生成不安全的关系。"
        elif analysis.status is TranslationAnalysisStatus.INCOMPLETE:
            message = "DLsite 关系信息不完整；仅显示已明确且安全的关系。"
        elif not relation_lines:
            message = "当前响应未列出具体的关联作品编号。"
        else:
            message = "\n".join(relation_lines)
        self.relation_details_value.setText(message)
        if historical is None or (not historical.outgoing and not historical.incoming):
            self.historical_relations_value.setText("暂无历史已确认关系")
        else:
            lines = [f"传出：{r.target_workno} · {_relation_label(r.relation_type)} · 首次 {r.first_seen.date()} · 最近 {r.last_seen.date()} · {r.observation_count} 次" for r in historical.outgoing]
            lines += [f"传入：{r.subject_workno} · {_relation_label(r.relation_type)} · 首次 {r.first_seen.date()} · 最近 {r.last_seen.date()} · {r.observation_count} 次" for r in historical.incoming]
            self.historical_relations_value.setText("\n".join(lines))

    def _clear_relations(self) -> None:
        self.relation_frame.setVisible(False)
        self.relation_role_value.setText("—")
        self.relation_source_value.setText("—")
        self.relation_confidence_value.setText("—")
        self.relation_language_value.setText("—")
        self.relation_attribution_value.setText("—")
        self.relation_details_value.setText("—")
        self.historical_relations_value.setText("—")

    def _refresh_status_style(self) -> None:
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)


def _value_label() -> QLabel:
    label = QLabel("—")
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return label


def _role_label(role: TranslationRole | None) -> str:
    if role is None:
        return "—"
    return {
        TranslationRole.ORIGINAL: "原作品",
        TranslationRole.TRANSLATION_PARENT: "翻译父条目",
        TranslationRole.TRANSLATION_CHILD: "翻译子条目",
    }[role]


def _confidence_label(status: TranslationAnalysisStatus) -> str:
    return {
        TranslationAnalysisStatus.CONFIRMED: "已确认",
        TranslationAnalysisStatus.INCOMPLETE: "部分确认",
        TranslationAnalysisStatus.INVALID: "数据矛盾",
    }.get(status, "—")


def _language_label(value: WorkLanguage | str | None) -> str:
    if value is None:
        return "—"
    code = value.value if isinstance(value, WorkLanguage) else str(value)
    if not code:
        return "—"
    return {
        "JPN": "日语（JPN）",
        "CHI_HANS": "简体中文（CHI_HANS）",
        "CHI_HANT": "繁体中文（CHI_HANT）",
        "ENG": "英语（ENG）",
        "KO_KR": "韩语（KO_KR）",
        "KOR": "韩语（KOR）",
        "SPA": "西班牙语（SPA）",
        "GER": "德语（GER）",
        "FRE": "法语（FRE）",
        "IND": "印度尼西亚语（IND）",
        "ITA": "意大利语（ITA）",
        "POR": "葡萄牙语（POR）",
        "SWE": "瑞典语（SWE）",
        "THA": "泰语（THA）",
        "VIE": "越南语（VIE）",
    }.get(code, f"未知（{code}）")


def _age_label(value: AgeCategory | str | None) -> str:
    if value is None:
        return "—"
    code = value.value if isinstance(value, AgeCategory) else str(value)
    return {
        AgeCategory.GENERAL.value: "全年龄",
        AgeCategory.R15.value: "R15",
        AgeCategory.R18.value: "R18",
        AgeCategory.UNKNOWN.value: "未知",
    }.get(code, f"未知（{code}）")


def _attribution_label(value: TranslationAttribution | None) -> str:
    if value is None:
        return "—"
    if value.maker_name and value.maker_id:
        return f"{value.maker_name} ({value.maker_id})"
    return value.maker_name or value.maker_id or "—"


def _relation_label(relation_type: RelationType) -> str:
    return {
        RelationType.TRANSLATION_OF: "翻译原作",
        RelationType.HAS_TRANSLATION_CHILD: "翻译子条目",
        RelationType.CHILD_OF_TRANSLATION: "翻译父条目",
    }.get(relation_type, relation_type.value)
