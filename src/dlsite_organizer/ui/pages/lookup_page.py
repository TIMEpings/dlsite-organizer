"""Public DLsite work-number lookup page."""

from __future__ import annotations
# ruff: noqa

from typing import cast

from PySide6.QtCore import Qt, QThread, QTimer, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFormLayout,
    QFrame,
    QGridLayout,
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
from dlsite_organizer.ui.theme import ButtonRole, set_button_role
from dlsite_organizer.ui.workers.lookup_worker import LookupWorker
from dlsite_organizer.ui.widgets.constrained_content import (
    ConstrainedContent,
    ContentAlignment,
)
from dlsite_organizer.ui.widgets.page_header import PageHeader


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
        self._focus_before_request: QWidget | None = None
        self._focus_after_busy_entry: QWidget | None = None
        self._focus_moved_while_busy = False
        self._watch_busy_focus = False
        self.refresh_button = QPushButton("强制刷新")

        self._build_ui()
        self._focus_watch_timer = QTimer(self)
        self._focus_watch_timer.setInterval(50)
        self._focus_watch_timer.timeout.connect(self._track_busy_focus)
        set_button_role(self.query_button, ButtonRole.PRIMARY)
        set_button_role(self.refresh_button, ButtonRole.SECONDARY)
        set_button_role(self.copy_button, ButtonRole.SECONDARY)
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
        content = ConstrainedContent(
            1120,
            alignment=ContentAlignment.LEFT_BOUNDED_WORKSPACE,
        )
        scroll_area.setAlignment(content.alignment.value | Qt.AlignmentFlag.AlignTop)
        layout = content.content_layout
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(18)

        layout.addWidget(
            PageHeader("查询作品", "读取当前作品信息并生成安全的格式化名称。")
        )

        query_row = QHBoxLayout()
        query_row.setSpacing(10)
        self.code_input = QLineEdit()
        self.code_input.setObjectName("lookupCodeInput")
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

        self.identity_frame = QWidget()
        self.identity_frame.setObjectName("lookupIdentity")
        result_layout = QHBoxLayout(self.identity_frame)
        result_layout.setContentsMargins(0, 2, 0, 2)
        result_layout.setSpacing(16)
        self.cover_label = QLabel()
        self.cover_label.setObjectName("coverPlaceholder")
        self.cover_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cover_label.setFixedSize(96, 128)
        result_layout.addWidget(self.cover_label, 0, Qt.AlignmentFlag.AlignTop)

        identity_details = QVBoxLayout()
        identity_details.setSpacing(5)
        self.title_value = _value_label()
        self.title_value.setObjectName("lookupTitle")
        title_font = self.title_value.font()
        title_font.setPointSize(title_font.pointSize() + 2)
        title_font.setBold(True)
        self.title_value.setFont(title_font)
        identity_details.addWidget(self.title_value)
        self.workno_value = _value_label()
        self.workno_value.setObjectName("lookupWorkno")
        self.maker_value = _value_label()
        self.maker_value.setObjectName("lookupMaker")
        facts = QGridLayout()
        facts.setContentsMargins(0, 0, 0, 0)
        facts.setHorizontalSpacing(8)
        facts.setVerticalSpacing(5)
        for row, (caption, value) in enumerate(
            (("作品编号", self.workno_value), ("社团", self.maker_value))
        ):
            label = QLabel(caption)
            label.setObjectName("lookupFactCaption")
            facts.addWidget(
                label,
                row,
                0,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
            )
            facts.addWidget(value, row, 1, Qt.AlignmentFlag.AlignTop)
        facts.setColumnStretch(1, 1)
        identity_details.addLayout(facts)
        self.cover_status_label = QLabel("正在读取封面…")
        self.cover_status_label.setObjectName("lookupCoverStatus")
        self.cover_status_label.setVisible(False)
        identity_details.addWidget(self.cover_status_label)
        result_layout.addLayout(identity_details, 1)
        layout.addWidget(self.identity_frame)

        self.name_section = QWidget()
        self.name_section.setObjectName("lookupNameSection")
        name_layout = QVBoxLayout(self.name_section)
        name_layout.setContentsMargins(0, 0, 0, 0)
        name_layout.setSpacing(7)
        heading_row = QHBoxLayout()
        heading_row.setSpacing(10)
        output_label = QLabel("格式化名称")
        output_label.setObjectName("sectionLabel")
        heading_row.addWidget(output_label)
        self.copy_button = QPushButton("复制名称")
        self.copy_button.setMinimumSize(108, 40)
        self.copy_button.setEnabled(False)
        heading_row.addWidget(self.copy_button)
        heading_row.addStretch(1)
        name_layout.addLayout(heading_row)
        self.name_output = _value_label()
        self.name_output.setObjectName("lookupFormattedName")
        self.name_output.setMinimumHeight(40)
        self.name_output.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        name_layout.addWidget(self.name_output)
        self.copy_feedback = QLabel()
        self.copy_feedback.setObjectName("lookupCopyFeedback")
        self.copy_feedback.setAlignment(Qt.AlignmentFlag.AlignRight)
        name_layout.addWidget(self.copy_feedback)
        layout.addWidget(self.name_section)

        self.metadata_frame = QFrame()
        self.metadata_frame.setObjectName("lookupMetadata")
        metadata_layout = QVBoxLayout(self.metadata_frame)
        metadata_layout.setContentsMargins(0, 4, 0, 0)
        metadata_layout.setSpacing(8)
        metadata_heading = QLabel("作品信息")
        metadata_heading.setObjectName("sectionLabel")
        metadata_layout.addWidget(metadata_heading)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        form.setHorizontalSpacing(18)
        form.setVerticalSpacing(8)
        self.maker_id_value = _value_label()
        self.release_value = _value_label()
        self.series_value = _value_label()
        self.cvs_value = _value_label()
        self.tags_value = _value_label()
        self.language_value = _value_label()
        self.age_value = _value_label()
        form.addRow("社团编号", self.maker_id_value)
        form.addRow("系列", self.series_value)
        form.addRow("CV", self.cvs_value)
        form.addRow("标签", self.tags_value)
        form.addRow("语言", self.language_value)
        form.addRow("年龄分级", self.age_value)
        form.addRow("发售日期", self.release_value)
        metadata_layout.addLayout(form)
        layout.addWidget(self.metadata_frame)

        relation_frame = QFrame()
        self.relation_frame = relation_frame
        relation_frame.setObjectName("relationCard")
        relation_layout = QVBoxLayout(relation_frame)
        relation_layout.setContentsMargins(22, 18, 22, 18)
        relation_layout.setSpacing(10)
        relation_heading = QLabel("作品关系")
        relation_heading.setObjectName("sectionLabel")
        relation_layout.addWidget(relation_heading)
        current_heading = QLabel("当前响应")
        current_heading.setObjectName("subsectionLabel")
        relation_layout.addWidget(current_heading)
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
        historical_heading = QLabel("历史确认关系")
        historical_heading.setObjectName("subsectionLabel")
        relation_layout.addWidget(historical_heading)
        self.historical_relations_value = _value_label()
        relation_layout.addWidget(self.historical_relations_value)
        layout.addWidget(relation_frame)
        self._clear_relations()
        relation_frame.setVisible(False)
        self.identity_frame.setVisible(False)
        self.name_section.setVisible(False)
        self.metadata_frame.setVisible(False)
        scroll_area.setWidget(content)
        page_layout.addWidget(scroll_area)
        layout.addStretch(1)

    @Slot()
    def start_lookup(self) -> None:
        """Start one background lookup unless another is still active."""
        if self._thread is not None:
            return
        self._run_lookup(force_refresh=False)

    @Slot()
    def force_refresh(self) -> None:
        if self._thread is not None:
            return
        self._start_lookup_worker(force_refresh=True)

    def _start_lookup_worker(self, *, force_refresh: bool = False) -> None:
        self.start_lookup() if not force_refresh else self._run_lookup(force_refresh=True)

    def _run_lookup(self, *, force_refresh: bool) -> None:
        if self._thread is not None:
            return
        self._enter_busy_state(force_refresh)
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

    def _enter_busy_state(self, force_refresh: bool) -> None:
        """Clear the previous projection and announce the admitted request."""
        self._focus_before_request = QApplication.focusWidget()
        self._focus_moved_while_busy = False
        self._set_loading(True)
        self._clear_result()
        self.status_label.setProperty("state", "loading")
        self.status_label.setText(
            "正在强制刷新…" if force_refresh else "正在查询…"
        )
        self._refresh_status_style()
        self._watch_busy_focus = True
        self._focus_after_busy_entry = QApplication.focusWidget()
        self._focus_watch_timer.start()

    @Slot()
    def _track_busy_focus(self) -> None:
        if (
            self._watch_busy_focus
            and QApplication.focusWidget() is not self._focus_after_busy_entry
        ):
            self._focus_moved_while_busy = True

    @Slot(object)
    def _show_result(self, value: object) -> None:
        result = cast(LookupResult, value)
        self._current_result = result
        work = result.work
        self.identity_frame.setVisible(True)
        self.name_section.setVisible(True)
        self.metadata_frame.setVisible(True)
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
        self.copy_feedback.clear()
        self.cover_label.setPixmap(QPixmap())
        self.cover_label.setText("暂无封面")
        self.cover_status_label.setVisible(bool(work.cover_url and self._thread is not None))
        self._show_relations(
            result.translation,
            result.historical_relations,
            result.translation_attribution,
        )
        label = {
            LookupFreshness.LIVE: "实时",
            LookupFreshness.CACHE_FRESH: "缓存",
            LookupFreshness.CACHE_STALE_FALLBACK: "旧缓存",
        }[result.freshness]
        freshness_text = f"查询完成 · {label}"
        self.status_label.setProperty(
            "state", "stale" if result.freshness is LookupFreshness.CACHE_STALE_FALLBACK else "success"
        )
        self.status_label.setText(freshness_text)
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
        self.cover_status_label.setVisible(False)

    @Slot(str)
    def _show_error(self, message: str) -> None:
        self.status_label.setProperty("state", "error")
        self.status_label.setText(message)
        self._refresh_status_style()

    @Slot()
    def _lookup_finished(self) -> None:
        self._thread = None
        self._worker = None
        self.cover_status_label.setVisible(False)
        self._focus_watch_timer.stop()
        self._watch_busy_focus = False
        self._set_loading(False)
        primary_controls = {self.code_input, self.query_button, self.refresh_button}
        if (
            self.isVisible()
            and self._focus_before_request in primary_controls
            and not self._focus_moved_while_busy
        ):
            self.code_input.setFocus(Qt.FocusReason.OtherFocusReason)
        self._focus_before_request = None

    @Slot()
    def copy_name(self) -> None:
        """Copy the last successful formatted name."""
        if not self._formatted_name:
            return
        clipboard = QApplication.clipboard()
        clipboard.setText(self._formatted_name)
        self.copy_feedback.setText("已复制名称")

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
        self.copy_feedback.clear()
        self.identity_frame.setVisible(False)
        self.name_section.setVisible(False)
        self.metadata_frame.setVisible(False)
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
        self.cover_label.setPixmap(QPixmap())
        self.cover_status_label.setVisible(False)
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
    label.setTextFormat(Qt.TextFormat.PlainText)
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
