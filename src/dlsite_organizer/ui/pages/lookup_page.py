"""Public DLsite work-number lookup page."""

from __future__ import annotations
# ruff: noqa

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from html import escape
from re import fullmatch
from typing import cast

from PySide6.QtCore import QEvent, QPoint, QRectF, QSignalBlocker, QSize, Qt, QThread, QTimer, Slot
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QFocusEvent,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QResizeEvent,
    QShortcut,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from dlsite_organizer.domain.bonus import BonusCampaign
from dlsite_organizer.domain.bonus_coverage import (
    MAX_COVERAGE_OFFSET,
    MIN_KNOWN_NEGATIVE_OFFSET,
    estimate_bonus_coverage,
)
from dlsite_organizer.domain.manual_review import CandidateReviewOutcome, ManualReviewProvenance
from dlsite_organizer.domain.relation import Confidence, RelationType, TranslationRole
from dlsite_organizer.domain.work import AgeCategory, TranslationAttribution, WorkLanguage
from dlsite_organizer.domain.work_code import WorkCode
from dlsite_organizer.services.bonus_search import (
    DEFAULT_RANGE_END,
    BonusPair,
    BonusSearchRequest,
    BonusSearchResult,
    bonus_range_count,
)
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupFreshness, LookupResult, LookupService
from dlsite_organizer.services.translation_discovery import (
    TranslationDetailStatus,
    TranslationListings,
    TranslatorListing,
)
from dlsite_organizer.services.translation_relations import TranslationAnalysisStatus
from dlsite_organizer.ui.theme import ButtonRole, set_button_role
from dlsite_organizer.ui.theme.tokens import LIGHT_COLORS, Shape, Spacing
from dlsite_organizer.ui.workers.lookup_worker import BonusSearchWorker, LookupWorker
from dlsite_organizer.ui.widgets.bonus_range_slider import SLIDER_MAX_END, BonusRangeSlider
from dlsite_organizer.ui.widgets.constrained_content import (
    ConstrainedContent,
    ContentAlignment,
)
from dlsite_organizer.ui.widgets.page_header import PageHeader

logger = logging.getLogger(__name__)

_HISTORY_LIMIT = 50


@dataclass(slots=True)
class _LookupVisit:
    result: LookupResult
    cover: bytes | None = None
    listings: TranslationListings | None = None
    bonus: BonusSearchResult | None = None
    bonus_preview: bool = True
    scroll: int = 0


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
        self._worker: LookupWorker | BonusSearchWorker | None = None
        self._current_result: LookupResult | None = None
        self._bonus_result: BonusSearchResult | None = None
        self._bonus_is_preview = True
        self._bonus_pending = False
        self._bonus_scan_visit: _LookupVisit | None = None
        self._pending_lookup: tuple[str, bool] | None = None
        self._accept_worker_updates = True
        self._history: list[_LookupVisit] = []
        self._history_index = -1
        self._scroll_restore_timer = QTimer(self)
        self._scroll_restore_timer.setSingleShot(True)
        self._scroll_restore_timer.timeout.connect(self._restore_history_scroll)
        self._translation_listings: TranslationListings | None = None
        self._formatted_name = ""
        self._cover_content: bytes | None = None
        self._cover_pixmap = QPixmap()
        self._lookup_content_layout: QVBoxLayout | None = None
        self._cover_resize_timer = QTimer(self)
        self._cover_resize_timer.setSingleShot(True)
        self._cover_resize_timer.timeout.connect(self._render_cover)
        self._bonus_activity_timer = QTimer(self)
        self._bonus_activity_timer.setInterval(60_000)
        self._bonus_activity_timer.timeout.connect(self._refresh_campaign_status)
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
        self.query_button.clicked.connect(self.start_lookup)
        self.refresh_button.clicked.connect(self.force_refresh)
        self.code_input.returnPressed.connect(self.start_lookup)
        self._copy_feedback_timer = QTimer(self)
        self._copy_feedback_timer.setSingleShot(True)
        self._copy_feedback_timer.setInterval(1400)
        self._copy_feedback_timer.timeout.connect(self._copy_feedback_expired)

    def _build_ui(self) -> None:
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        scroll_area = self._scroll_area = QScrollArea()
        scroll_area.setObjectName("lookupScrollArea")
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setWidgetResizable(True)
        content = ConstrainedContent(
            1120,
            alignment=ContentAlignment.LEFT_BOUNDED_WORKSPACE,
        )
        scroll_area.setAlignment(content.alignment.value | Qt.AlignmentFlag.AlignTop)
        layout = content.content_layout
        self._lookup_content_layout = layout
        layout.setContentsMargins(Spacing.XL, Spacing.XL, Spacing.XL, Spacing.XL)
        layout.setSpacing(Spacing.LG)

        layout.addWidget(
            PageHeader("查询作品", "读取当前作品信息并生成安全的格式化名称。")
        )

        query_row = QHBoxLayout()
        query_row.setSpacing(Spacing.SM)
        self.back_button = self._history_button("后退", QStyle.StandardPixmap.SP_ArrowLeft, -1)
        self.forward_button = self._history_button("前进", QStyle.StandardPixmap.SP_ArrowRight, 1)
        query_row.addWidget(self.back_button)
        query_row.addWidget(self.forward_button)
        self.code_input = QLineEdit()
        self.code_input.setObjectName("lookupCodeInput")
        self.code_input.setPlaceholderText("输入完整作品编号（RJ / BJ / VJ）")
        self.code_input.setClearButtonEnabled(True)
        self.code_input.setMinimumHeight(40)
        self.query_button = QPushButton("查询")
        self.query_button.setObjectName("primaryButton")
        for button in (self.query_button, self.refresh_button):
            button.setFixedSize(104, 40)
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
        self.cover_label.setWordWrap(True)
        result_layout.addWidget(self.cover_label, 0, Qt.AlignmentFlag.AlignTop)

        identity_details = QVBoxLayout()
        identity_details.setSpacing(Spacing.MD)
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
        self.facts = QFormLayout()
        self.facts.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.facts.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.facts.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.facts.setHorizontalSpacing(Spacing.MD)
        self.facts.setVerticalSpacing(Spacing.XS)
        self.maker_id_value = _value_label()
        self.release_value = _value_label()
        self.series_value = _value_label()
        self.cvs_value = _value_label()
        self.tags_value = _value_label()
        self.language_value = _value_label()
        self.age_value = _value_label()
        for caption, value in (
            ("作品编号", self.workno_value),
            ("社团", self.maker_value),
            ("社团编号", self.maker_id_value),
            ("系列", self.series_value),
            ("CV", self.cvs_value),
            ("标签", self.tags_value),
            ("语言", self.language_value),
            ("年龄分级", self.age_value),
            ("发售日期", self.release_value),
        ):
            label = QLabel(caption)
            label.setObjectName("lookupFactCaption")
            self.facts.addRow(label, value)
        identity_details.addLayout(self.facts)
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
        self.copy_feedback = QLabel()
        self.copy_feedback.setObjectName("lookupCopyFeedback")
        self.copy_feedback.setAccessibleName("复制状态")
        self.copy_feedback.setStyleSheet(f"color: {LIGHT_COLORS.success};")
        heading_row.addWidget(self.copy_feedback)
        heading_row.addStretch(1)
        name_layout.addLayout(heading_row)
        self.name_output = _CopyableNameLabel(self._name_copy_requested)
        self.name_output.setObjectName("lookupFormattedName")
        self.name_output.setMinimumHeight(40)
        self.name_output.setTextFormat(Qt.TextFormat.PlainText)
        self.name_output.setWordWrap(True)
        self.name_output.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.name_output.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.name_output.setAccessibleName("格式化名称")
        self.name_output.setAccessibleDescription("按回车或空格复制完整格式化名称")
        self.name_output.setStyleSheet(
            f"""
QLabel#lookupFormattedName {{
  background: transparent;
  border: 1px solid transparent;
  border-radius: {Shape.CONTROL_RADIUS}px;
  padding: 4px 28px 4px 6px;
}}
QLabel#lookupFormattedName:hover {{ background: {LIGHT_COLORS.surface_secondary}; }}
QLabel#lookupFormattedName:focus {{ border-color: {LIGHT_COLORS.focus}; }}
QLabel#lookupFormattedName:focus:hover {{ background: {LIGHT_COLORS.surface_secondary}; }}
"""
        )
        self.name_output.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        name_layout.addWidget(self.name_output)
        layout.addWidget(self.name_section)

        self.related_card = QFrame()
        self.related_card.setObjectName("relationCard")
        self.relation_frame = self.related_card
        relations = QVBoxLayout(self.related_card)
        relations.setContentsMargins(Spacing.LG, Spacing.MD, Spacing.LG, Spacing.MD)
        relations.setSpacing(Spacing.SM)
        heading = QLabel("翻译与语言版本")
        heading.setObjectName("sectionLabel")
        relations.addWidget(heading)
        self.relation_attribution_value = _value_label()
        relations.addWidget(self.relation_attribution_value)
        self.relation_details_value = self._linked_label()
        relations.addWidget(self.relation_details_value)
        self.translation_status = _value_label()
        self.translation_status.setObjectName("lookupSecondaryText")
        self.translation_loading = QWidget()
        loading_row = QHBoxLayout(self.translation_loading)
        loading_row.setContentsMargins(0, 0, 0, 0)
        loading_row.addWidget(self.translation_status)
        self.translation_cancel = QPushButton("停止补查")
        set_button_role(self.translation_cancel, ButtonRole.QUIET)
        self.translation_cancel.clicked.connect(self._cancel_translation_lookup)
        loading_row.addWidget(self.translation_cancel)
        relations.addWidget(self.translation_loading)
        self.historical_relations_value = _value_label()
        self.relation_evidence = QToolButton()
        self._configure_disclosure(
            self.relation_evidence, "查看依据与历史", self.historical_relations_value
        )
        relations.addWidget(self.relation_evidence, 0, Qt.AlignmentFlag.AlignLeft)
        relations.addWidget(self.historical_relations_value)
        layout.addWidget(self.related_card)
        self._clear_relations()
        self._build_bonus_search(layout)
        self.identity_frame.setVisible(False)
        self.name_section.setVisible(False)
        scroll_area.setWidget(content)
        page_layout.addWidget(scroll_area)
        layout.addStretch(1)

    def _history_button(
        self, name: str, icon: QStyle.StandardPixmap, offset: int,
    ) -> QPushButton:
        button = QPushButton()
        button.setObjectName("lookupHistoryButton")
        button.setIcon(self.style().standardIcon(icon))
        button.setFixedSize(36, 40)
        button.setToolTip(name)
        button.setAccessibleName(name)
        set_button_role(button, ButtonRole.SECONDARY)
        button.setEnabled(False)
        button.clicked.connect(lambda: self._navigate_history(offset))
        arrow = "Left" if offset < 0 else "Right"
        shortcut = QShortcut(QKeySequence(f"Alt+{arrow}"), self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(button.click)
        return button

    def _save_history_visit(self) -> None:
        if self._current_result is None or self._history_index < 0:
            return
        visit = self._history[self._history_index]
        visit.result = self._current_result
        visit.cover = self._cover_content
        visit.listings = self._translation_listings
        visit.bonus = self._bonus_result
        visit.bonus_preview = self._bonus_is_preview
        if not self._scroll_restore_timer.isActive():
            visit.scroll = self._scroll_area.verticalScrollBar().value()

    def _update_history_buttons(self) -> None:
        focused = QApplication.focusWidget()
        self.back_button.setEnabled(self._history_index > 0)
        self.forward_button.setEnabled(self._history_index + 1 < len(self._history))
        if focused in (self.back_button, self.forward_button) and not focused.isEnabled():
            self.code_input.setFocus(Qt.FocusReason.OtherFocusReason)

    def _navigate_history(self, offset: int) -> None:
        target = self._history_index + offset
        if not 0 <= target < len(self._history):
            return
        self._save_history_visit()
        self._pending_lookup = None
        self._stop_worker_updates()
        self._history_index = target
        self._restore_history_visit()

    def _stop_worker_updates(self) -> None:
        # Keep the thread alive until its normal cleanup, including any cache writes.
        self._accept_worker_updates = False
        if isinstance(self._worker, LookupWorker):
            self._worker.cancel_translations()
        elif isinstance(self._worker, BonusSearchWorker):
            self._worker.cancel()

    def _restore_history_visit(self) -> None:
        visit = self._history[self._history_index]
        self._clear_result()
        self._translation_listings = visit.listings
        self._show_result(visit.result, bonus=visit.bonus, bonus_preview=visit.bonus_preview)
        cover = visit.cover or self._cover_service.cached_cover_for(visit.result.work.workno)
        if cover is not None:
            self._display_cover(cover)
        self._set_loading(self._accept_worker_updates and self.is_busy())
        self._update_bonus_search_action()
        self._update_history_buttons()
        self._restore_history_scroll()
        self._scroll_restore_timer.start(0)

    def _restore_history_scroll(self) -> None:
        self._scroll_area.verticalScrollBar().setValue(self._history[self._history_index].scroll)

    def _build_bonus_search(self, layout: QVBoxLayout) -> None:
        self.bonus_panel = QFrame()
        self.bonus_panel.setObjectName("bonusCard")
        controls = QVBoxLayout(self.bonus_panel)
        controls.setContentsMargins(Spacing.LG, Spacing.MD, Spacing.LG, Spacing.MD)
        controls.setSpacing(Spacing.SM)

        heading = QLabel("期限特典")
        heading.setObjectName("sectionLabel")
        controls.addWidget(heading)
        self.bonus_campaign = QWidget()
        self.bonus_campaigns = QVBoxLayout(self.bonus_campaign)
        self.bonus_campaigns.setContentsMargins(0, 0, 0, 0)
        self.bonus_campaigns.setSpacing(Spacing.SM)
        self.bonus_campaign.hide()
        controls.addWidget(self.bonus_campaign)

        self.bonus_range = BonusRangeSlider()
        self.bonus_range.setObjectName("lookupBonusRange")
        self.bonus_range.setAccessibleName("特典 RJ 搜索范围")
        self.bonus_range.set_end_offset(DEFAULT_RANGE_END)
        self.bonus_start = QLineEdit("+1")
        self.bonus_end = QLineEdit(f"+{DEFAULT_RANGE_END}")
        for widget, name in ((self.bonus_start, "起始 RJ 偏移"), (self.bonus_end, "结束 RJ 偏移")):
            widget.setAccessibleName(name)
            widget.setToolTip(name)
            widget.setFixedWidth(widget.fontMetrics().horizontalAdvance("-9999999999") + 24)
            widget.editingFinished.connect(self._update_bonus_range_hint)
        self.bonus_end.textChanged.connect(self._sync_bonus_slider)
        self.bonus_search_button = QPushButton("查找特典 RJ")
        set_button_role(self.bonus_search_button, ButtonRole.SECONDARY)
        self.bonus_search_button.clicked.connect(self._activate_bonus_search)
        self.bonus_search_controls = QWidget()
        search_layout = QVBoxLayout(self.bonus_search_controls)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(Spacing.SM)
        slider_row = QHBoxLayout()
        slider_row.addWidget(self.bonus_range, 1)
        slider_row.addWidget(self.bonus_start)
        slider_row.addWidget(QLabel("至"))
        slider_row.addWidget(self.bonus_end)
        search_layout.addLayout(slider_row)
        self.bonus_range_label = _value_label()
        self.bonus_coverage_label = _value_label()
        self.bonus_coverage_label.setToolTip(
            "来源：2026 年统计表中的 494 对有效 RJ 配对（493 对正向、1 对负向）。"
            "覆盖率表示已知配对中，差值落入所选范围的样本比例；正向区间以对数坐标单调插值，"
            "超过 +172,074 后保持 493/494。包含 -18,545 至 +172,074 时为已知样本的 494/494。"
            "样本可能偏向容易发现的特典，不代表 DLsite 作品的实际发现率或官方保证。"
        )
        self.bonus_budget_hint = _value_label()
        self.bonus_range_progress = _value_label()
        for label in (self.bonus_range_label, self.bonus_coverage_label,
                      self.bonus_budget_hint, self.bonus_range_progress):
            search_layout.addWidget(label)
        actions = QHBoxLayout()
        actions.addWidget(self.bonus_search_button)
        actions.addStretch(1)
        search_layout.addLayout(actions)
        controls.addWidget(self.bonus_search_controls)

        self.bonus_advanced = QToolButton()
        advanced = QWidget()
        self._configure_disclosure(self.bonus_advanced, "高级选项", advanced)
        advanced_layout = QVBoxLayout(advanced)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        manual_row = QHBoxLayout()
        self.bonus_candidate_input = QLineEdit()
        self.bonus_candidate_input.setPlaceholderText("已知特典 RJ")
        self.bonus_candidate_input.setAccessibleName("已知特典 RJ 编号")
        self.bonus_candidate_input.setMaxLength(24)
        self.bonus_verify_button = QPushButton("验证编号")
        set_button_role(self.bonus_verify_button, ButtonRole.SECONDARY)
        self.bonus_verify_button.clicked.connect(lambda: self._start_bonus_search(verify=True))
        self.bonus_candidate_input.returnPressed.connect(
            lambda: self._start_bonus_search(verify=True)
        )
        manual_row.addWidget(self.bonus_candidate_input, 1)
        manual_row.addWidget(self.bonus_verify_button)
        advanced_layout.addLayout(manual_row)
        self.bonus_scan_details = _value_label()
        self.bonus_scan_details.hide()
        advanced_layout.addWidget(self.bonus_scan_details)
        controls.addWidget(self.bonus_advanced, 0, Qt.AlignmentFlag.AlignLeft)
        controls.addWidget(advanced)
        self.bonus_range.valueChanged.connect(self._set_bonus_end_from_slider)
        self.bonus_range.exact_step.connect(self._step_bonus_end)

        self.bonus_status = _value_label()
        self.bonus_status.hide()
        controls.addWidget(self.bonus_status)
        self.bonus_output = self._linked_label()
        self.bonus_output.hide()
        controls.addWidget(self.bonus_output)
        self.bonus_review_widget = QWidget()
        self.bonus_reviews = QVBoxLayout(self.bonus_review_widget)
        self.bonus_reviews.setContentsMargins(0, 0, 0, 0)
        self.bonus_reviews.setSpacing(Spacing.SM)
        controls.addWidget(self.bonus_review_widget)
        self.code_input.textEdited.connect(self._hide_bonus_for_edited_code)
        layout.addWidget(self.bonus_panel)
        self.bonus_panel.hide()

    @staticmethod
    def _configure_disclosure(button: QToolButton, title: str, details: QWidget) -> None:
        button.setText(title)
        button.setObjectName("lookupDisclosure")
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        button.setCheckable(True)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        button.setArrowType(Qt.ArrowType.RightArrow)
        details.hide()
        button.toggled.connect(details.setVisible)
        button.toggled.connect(lambda opened: button.setArrowType(
            Qt.ArrowType.DownArrow if opened else Qt.ArrowType.RightArrow
        ))

    def _hide_bonus_for_edited_code(self) -> None:
        result = self._current_result
        if result and self.code_input.text().strip().upper() != result.work.workno:
            self.bonus_panel.hide()
        elif result:
            self._show_bonus_preview(result)

    def _selected_bonus_offsets(self) -> tuple[int, int]:
        return self._parse_bonus_offset(self.bonus_start.text()), self._parse_bonus_offset(
            self.bonus_end.text()
        )

    @staticmethod
    def _parse_bonus_offset(text: str) -> int:
        value = text.strip()
        if not fullmatch(r"[+-]?[0-9]+", value):
            raise ValueError("请输入有效的 RJ 起止偏移整数。")
        digits = value.lstrip("+-").lstrip("0") or "0"
        if len(digits) > WorkCode.MAX_DIGITS:
            raise ValueError("搜索范围超出支持的 RJ 编号。")
        return int(digits) * (-1 if value.startswith("-") else 1)

    def _set_bonus_end_from_slider(self) -> None:
        with QSignalBlocker(self.bonus_end):
            self.bonus_end.setText(f"{self.bonus_range.end_offset():+d}")
        self._update_bonus_range_hint()

    def _step_bonus_end(self, step: int) -> None:
        try:
            end = self._parse_bonus_offset(self.bonus_end.text())
        except ValueError:
            end = self.bonus_range.end_offset()
        end = max(1, min(end, SLIDER_MAX_END))
        self.bonus_end.setText(f"{max(1, min(end + step, SLIDER_MAX_END)):+d}")
        self._update_bonus_range_hint()

    def _sync_bonus_slider(self, text: str) -> None:
        try:
            end = self._parse_bonus_offset(text)
        except ValueError:
            return
        with QSignalBlocker(self.bonus_range):
            self.bonus_range.set_end_offset(end)

    def _update_bonus_range_hint(self) -> None:
        self.bonus_budget_hint.hide()
        self.bonus_coverage_label.hide()
        self.bonus_range_progress.hide()
        if self._current_result is None:
            return
        try:
            first, last = offsets = self._selected_bonus_offsets()
            count = bonus_range_count(self._current_result.work.workno, offsets)
        except ValueError as exc:
            self.bonus_range_label.setText(str(exc))
            self._update_bonus_search_action()
            return
        target = (f"搜索本体 RJ 后方 {last:,} 个编号" if first == 1
                  else f"目标范围 RJ {first:+,} ～ {last:+,}（{count:,} 个编号，跳过本体）")
        self.bonus_range_label.setText(target)
        estimate = estimate_bonus_coverage(offsets)
        if estimate is None:
            coverage = "历史编号覆盖参考：暂无可靠覆盖参考"
        elif first <= MIN_KNOWN_NEGATIVE_OFFSET and last >= MAX_COVERAGE_OFFSET:
            coverage = "历史样本覆盖：100%（已知样本）"
        else:
            coverage = f"历史编号覆盖参考：约 {estimate:.1f}%"
            if first < 0:
                coverage += "（未计入负向贡献）"
        if last >= MAX_COVERAGE_OFFSET and first > MIN_KNOWN_NEGATIVE_OFFSET:
            coverage += "\n已达到历史样本最大正向差值；扩大范围不会提高该样本覆盖率，但仍可能发现样本外特典。"
        self.bonus_coverage_label.setText(coverage)
        self.bonus_coverage_label.show()
        if count > DEFAULT_RANGE_END:
            self.bonus_budget_hint.setText("当前预算下，此范围可能需要多次手动继续。")
            self.bonus_budget_hint.show()
        scan = self._bonus_result.scan if self._bonus_result else None
        if scan is not None and scan.offsets != offsets:
            overlap = sum(first <= offset <= last for offset in scan.verified_offsets)
            self.bonus_range_progress.setText(
                f"当前区间与上次搜索重叠的已核验编号有 {overlap:,} 个"
            )
            self.bonus_range_progress.show()
        self._update_bonus_search_action()

    @Slot()
    def _activate_bonus_search(self) -> None:
        if self._accept_worker_updates and isinstance(self._worker, BonusSearchWorker):
            self._cancel_bonus_search()
        else:
            self._start_bonus_search()

    @Slot()
    def _start_bonus_search(self, *, verify: bool = False) -> None:
        result = self._current_result
        if self.is_busy() or result is None:
            return
        work = result.work
        try:
            main = str(WorkCode.parse(self.code_input.text(), allowed_prefixes=("RJ",)))
            if main != work.workno or result.translation_role in {
                TranslationRole.TRANSLATION_PARENT, TranslationRole.TRANSLATION_CHILD,
            }:
                raise ValueError("请先查询本体的 RJ 编号。")
            candidate = ""
            offsets = None
            if verify:
                candidate = str(WorkCode.parse(
                    self.bonus_candidate_input.text(), allowed_prefixes=("RJ",)
                ))
                if int(candidate[2:]) == int(main[2:]):
                    raise ValueError("候选特典编号须与本体编号不同。")
            else:
                offsets = self._selected_bonus_offsets()
                bonus_range_count(main, offsets)
                self._update_bonus_range_hint()
        except ValueError as exc:
            self._show_bonus_message(str(exc))
            return
        request = BonusSearchRequest(
            main, work.maker_id, work.release_date,
            candidate, offsets, continue_online=not verify and (
                self._bonus_pending
                or bool(self._bonus_result and self._bonus_result.has_reliable_match)
                or bool(self._bonus_result and self._bonus_result.scan)
            ),
            main_evidence=result.bonus_evidence, main_ajax_at=result.bonus_ajax_at,
            main_evidence_at=result.bonus_observed_at,
        )
        thread = QThread(self)
        worker = BonusSearchWorker(self._lookup_service.bonus_search(), request)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.result_ready.connect(self._finish_bonus_result)
        worker.progress_changed.connect(self._show_bonus_progress)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._lookup_finished)
        self._thread, self._worker = thread, worker
        self._bonus_scan_visit = (
            self._history[self._history_index] if self._history_index >= 0 else None
        )
        self._accept_worker_updates = True
        self._set_loading(True)
        self.bonus_search_button.setText("取消搜索")
        self._show_bonus_message("正在搜索期限特典…")
        thread.start()

    @Slot()
    def _cancel_bonus_search(self) -> None:
        if isinstance(self._worker, BonusSearchWorker) and self.bonus_search_button.isEnabled():
            self._worker.cancel()
            self.bonus_search_button.setEnabled(False)
            self._show_bonus_message("正在取消搜索…")

    @Slot(str)
    def _show_bonus_progress(self, message: str) -> None:
        if (
            self._accept_worker_updates and self.sender() is self._worker
            and self.bonus_search_button.isEnabled()
        ):
            self._show_bonus_message(message)

    @Slot(object)
    def _finish_bonus_result(self, result: object) -> None:
        if self.sender() is not self._worker:
            return
        if isinstance(result, BonusSearchResult):
            visit = self._bonus_scan_visit
            if visit is not None and visit.result.work.workno == result.main_workno:
                if result.scan is None and visit.bonus is not None:
                    result = replace(result, scan=visit.bonus.scan)
                visit.bonus, visit.bonus_preview = result, False
            if (
                self._pending_lookup is None and self._current_result is not None
                and self._current_result.work.workno == result.main_workno
                and (self._accept_worker_updates or (
                    visit is not None and self._history[self._history_index] is visit
                ))
            ):
                self._show_bonus_result(result)
        elif self._accept_worker_updates:
            self._show_bonus_message("期限特典搜索失败，详细信息已写入日志。")

    @Slot(str)
    def _show_bonus_message(self, message: str) -> None:
        self.bonus_status.setText(message)
        self.bonus_status.show()

    def _show_bonus_preview(self, result: LookupResult) -> None:
        work = result.work
        available = work.workno.startswith("RJ") and result.translation_role not in {
            TranslationRole.TRANSLATION_PARENT, TranslationRole.TRANSLATION_CHILD,
        }
        self.bonus_search_controls.setVisible(available)
        self.bonus_advanced.setVisible(available)
        if not available:
            self.bonus_advanced.setChecked(False)
        if available:
            self._update_bonus_range_hint()
            preview = result.bonus_preview
            if preview is None and result.bonus_evidence is not None:
                observed = result.bonus_observed_at
                if observed is not None:
                    preview = BonusSearchResult(
                        work.workno,
                        campaigns=tuple(
                            BonusCampaign(entry, observed, result.source)
                            for entry in result.bonus_evidence.entries
                        ),
                        fresh_campaign_at=(
                            observed if result.freshness is LookupFreshness.LIVE else None
                        ),
                    )
            if self._bonus_result is None and preview is not None:
                self._show_bonus_result(preview, preview=True)
                return
        has_pairs = self._render_bonus_reviews()
        self.bonus_panel.setVisible(available or has_pairs)

    def _show_bonus_result(self, result: BonusSearchResult, *, preview: bool = False) -> None:
        self._bonus_result = result
        self._bonus_is_preview = preview
        self.bonus_status.hide()
        self._render_campaigns(result)
        if result.campaigns:
            self._bonus_activity_timer.start()
        else:
            self._bonus_activity_timer.stop()

        self._render_bonus_search_output(result, preview=preview)
        self._update_bonus_range_hint()
        self._render_bonus_reviews()
        self.bonus_panel.show()

    def _render_bonus_reviews(self) -> bool:
        self._bonus_pending = False
        while item := self.bonus_reviews.takeAt(0):
            if widget := item.widget():
                widget.hide()
                widget.deleteLater()
        current = self._current_result
        if current is None:
            return False
        code = current.work.workno
        if not code.startswith("RJ"):
            return False
        service = self._lookup_service.bonus_search()
        extra = tuple(
            match for match in (self._bonus_result.matches if self._bonus_result else ())
            if match.relation.detection_source != ManualReviewProvenance.MANUAL_USER_REVIEW
        )
        try:
            outgoing = service.pairs_for_work(code, extra=extra)
            incoming = service.pairs_for_work(code, as_bonus=True)
        except Exception:
            logger.exception("Could not read limited bonus reviews for %s", code)
            self._show_bonus_message("无法读取期限特典审阅记录，详细信息已写入日志。")
            return False
        self._bonus_pending = any(pair.pending for pair in outgoing)
        for pair in outgoing:
            if service.can_review or self._bonus_result is None:
                self._add_bonus_review(pair, incoming=False, editable=service.can_review)
        for pair in incoming:
            self._add_bonus_review(pair, incoming=True, editable=service.can_review)
        self.bonus_review_widget.setVisible(bool(self.bonus_reviews.count()))
        self._update_bonus_search_action()
        return bool(incoming or outgoing)

    def _update_bonus_search_action(self) -> None:
        if self._accept_worker_updates and isinstance(self._worker, BonusSearchWorker):
            return
        result = self._bonus_result
        scan = result.scan if result else None
        if result and scan and not scan.complete and not result.review_deferred:
            label = "继续搜索剩余范围"
        elif self._bonus_pending:
            label = "继续查找更多特典"
        elif self._bonus_result and self._bonus_result.has_reliable_match:
            label = "查找更多特典"
        else:
            label = "查找特典 RJ"
        self.bonus_search_button.setText(label)

    def _add_bonus_review(self, pair: BonusPair, *, incoming: bool, editable: bool) -> None:
        match = pair.match
        relation = match.relation
        main, bonus = relation.source_workno, relation.target_workno
        official = relation.confidence is Confidence.CONFIRMED
        decided = pair.confirmed or pair.rejected
        if pair.rejected:
            label = "已拒绝配对"
        elif pair.confirmed:
            label = "所属本体 · 关系来源：用户人工确认" if incoming else "人工确认"
        elif pair.automatically_linked:
            label = "自动关联 · 高置信 · DLsite AJAX 标题及元数据交叉验证"
        elif pair.pending:
            label = "疑似关联本体 · 待确认" if incoming else "待确认配对"
            if relation.confidence is Confidence.HIGH:
                label += " · 高置信标题匹配"
        else:
            label = "所属本体 · DLsite 官方直接证据" if incoming else "DLsite 官方直接证据"
        if match.automatically_linked and pair.review is not None:
            label += " · 保留自动关联证据"
        elif official and pair.review is not None:
            if decided:
                label += " · DLsite 官方直接证据"
            else:
                label = "DLsite 官方直接证据 · 人工待确认"
        title = f" · {escape(match.title)}" if not incoming and match.title != bonus else ""
        evidence_parts = [item.description for item in relation.evidence]
        if match.observed_at:
            evidence_parts.append(f"证据采集于 {match.observed_at.date()}")
        evidence = escape(" · ".join(evidence_parts))
        description = self._linked_label()
        description.setText(
            f"{label}<br>本体：{self._work_link(main)} · 特典：{self._work_link(bonus)}{title}"
            + (f"<br>{evidence}" if evidence else "")
        )
        row = QWidget()
        row_layout = QVBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.addWidget(description)
        if editable and (decided or pair.pending or pair.automatically_linked):
            actions = QHBoxLayout()
            choices = (
                (("更改判断", CandidateReviewOutcome.UNSURE),) if pair.automatically_linked else
                (("重新审阅", CandidateReviewOutcome.UNSURE),) if decided else (
                    ("确认匹配", CandidateReviewOutcome.RELATED),
                    ("不是该作品的特典", CandidateReviewOutcome.NOT_RELATED),
                )
            )
            for label, outcome in choices:
                button = QPushButton(label)
                button.setEnabled(not self.is_busy())
                role = (ButtonRole.DANGER if outcome is CandidateReviewOutcome.NOT_RELATED
                        else ButtonRole.SECONDARY)
                set_button_role(button, role)
                button.clicked.connect(
                    lambda checked=False, p=pair, value=outcome: self._review_bonus(p, value)
                )
                actions.addWidget(button)
            actions.addStretch(1)
            row_layout.addLayout(actions)
        self.bonus_reviews.addWidget(row)

    def _review_bonus(self, pair: BonusPair, outcome: CandidateReviewOutcome) -> None:
        current = self._current_result
        if self.is_busy() or current is None:
            return
        try:
            self._lookup_service.bonus_search().review_match(pair.match, outcome)
        except Exception:
            logger.exception("Could not save limited bonus review")
            self._show_bonus_message("未能保存期限特典审阅，详细信息已写入日志。")
            return
        try:
            preview = self._lookup_service.preview_bonuses(current)
            if preview is not None and self._bonus_result is not None:
                # Keep other candidates from this session when caching is disabled.
                projected = {
                    match.relation.target_workno for match in preview.matches
                }
                extra = tuple(
                    match for match in self._bonus_result.matches
                    if match.relation.detection_source
                    != ManualReviewProvenance.MANUAL_USER_REVIEW
                    and match.relation.target_workno not in projected
                    and match.relation.target_workno != pair.match.relation.target_workno
                )
                pairs = self._lookup_service.bonus_search().pairs_for_work(
                    current.work.workno, extra=extra,
                )
                preview = replace(
                    preview, matches=(*preview.matches, *(
                        item.match for item in pairs if item.pending
                        and item.match.relation.target_workno not in projected
                    )),
                )
            manual = self._lookup_service.manual_review_service
            self._current_result = replace(
                current, bonus_preview=preview,
                manual_reviews=manual.reviews_for_work(current.work.workno) if manual else (),
            )
            self._bonus_result = None
            self._show_relations()
            self._show_bonus_preview(self._current_result)
        except Exception:
            logger.exception("Could not refresh limited bonus review")
            self._show_bonus_message("审阅已保存；请重新查询以刷新显示。")
            return
        message = {
            CandidateReviewOutcome.RELATED: "已保存人工确认。",
            CandidateReviewOutcome.NOT_RELATED: "已拒绝此配对，可继续查找其它特典。",
            CandidateReviewOutcome.UNSURE: "已设为待确认，可以重新确认或排除此配对。",
        }[outcome]
        if self._bonus_pending:
            message += "还有待审阅的配对。"
        self._show_bonus_message(message)

    def _refresh_campaign_status(self) -> None:
        if self._bonus_result is not None:
            self._render_campaigns(self._bonus_result)

    def _render_campaigns(self, result: BonusSearchResult) -> None:
        now = datetime.now(UTC)
        recent = self._lookup_service.is_recent_bonus_observation(result.fresh_campaign_at)
        while item := self.bonus_campaigns.takeAt(0):
            if widget := item.widget():
                widget.hide()
                widget.deleteLater()
        for campaign in result.campaigns:
            entry = campaign.entry
            status = entry.activity_status(
                now, recently_reported=recent and campaign.observed_at == result.fresh_campaign_at
            )
            title = entry.title or entry.label or "未提供标题"
            codes = tuple(dict.fromkeys(
                match.relation.target_workno for match in result.matches
                if (match.entry == entry or match.relation.target_workno == campaign.workno)
                and match.relation.confidence in {
                    Confidence.CONFIRMED, Confidence.HIGH,
                }
            ))
            if codes:
                identifier = "、".join(self._work_link(code) for code in codes)
            elif campaign.workno and campaign.workno != result.main_workno:
                identifier = f"{self._work_link(campaign.workno)}（待验证）"
            else:
                identifier = "尚未发现独立编号"
            deadline = str(entry.end_at) if entry.end_at else "未知"
            headline = f"{status} · {title} · 截止 {deadline}"
            active = status == "活动进行中"
            row = QWidget(self.bonus_campaign)
            layout = QVBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            description = entry.description
            if description and description.strip():
                status_label = _CampaignStatusLink(headline, active=active)
                status_label.linkActivated.connect(
                    lambda _link, t=title, d=description: self._show_campaign_details(t, d)
                )
            else:
                status_label = _value_label()
                status_label.setTextFormat(Qt.TextFormat.RichText)
                status_label.setText(
                    f'<b style="color:{LIGHT_COLORS.success}">{escape(headline)}</b>'
                    if active else escape(headline)
                )
            layout.addWidget(status_label)
            label = self._linked_label()
            label.setText(f"{identifier} · 采集于 {campaign.observed_at.date()}")
            layout.addWidget(label)
            self.bonus_campaigns.addWidget(row)
        self.bonus_campaign.setVisible(bool(result.campaigns))

    def _show_campaign_details(self, title: str, description: str) -> None:
        dialog = QDialog(self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowTitle(title)
        layout = QVBoxLayout(dialog)
        details = QPlainTextEdit(dialog)
        details.setAccessibleName("活动完整说明")
        details.setReadOnly(True)
        details.setPlainText(description)
        layout.addWidget(details)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dialog)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.resize(480, 300)
        dialog.open()

    def _render_bonus_search_output(self, result: BonusSearchResult, *, preview: bool) -> None:
        blocks = []
        if result.cancelled:
            blocks.append("搜索已取消，保留已获得的结果。")
        if result.used_local_results:
            blocks.append("已使用本地关联，未发起在线请求。")
        if result.scan:
            first, last = result.scan.offsets
            if result.scan.complete:
                state = "编号范围查询完成"
            elif result.used_local_results:
                state = "本次仅检查了范围内可复用的本地记录"
            else:
                state = "目标范围尚未完成"
            blocks.append(f"{state} · RJ {first:+,} ～ {last:+,}\n{result.scan.describe()}")
            self.bonus_scan_details.setText(result.scan.describe_details())
            self.bonus_scan_details.show()
        elif not preview:
            blocks.append(f"在线请求 {result.requests} 次")
            self.bonus_scan_details.hide()
        if not result.has_reliable_match and result.campaigns and not result.cancelled and not preview:
            blocks.append("已有特典活动记录，尚未找到可确认的特典 RJ。")
        elif not result.matches and not result.cancelled:
            if result.scan and result.scan.complete:
                blocks.append("本次扫描覆盖了设定范围，未发现特典 RJ。")
            elif not preview and result.scan is None:
                blocks.append("未找到符合条件的特典 RJ。")
        blocks = [escape(block).replace("\n", "<br>") for block in blocks]
        can_review = self._lookup_service.manual_review_service is not None
        for match in result.matches:
            relation = match.relation
            if can_review:
                continue  # Evidence and links are shown with the review buttons.
            label = {
                Confidence.CONFIRMED: (
                    "人工确认" if relation.detection_source
                    == ManualReviewProvenance.MANUAL_USER_REVIEW else "DLsite 官方直接证据"
                ),
                Confidence.HIGH: (
                    "自动关联 · 高置信 · DLsite AJAX 标题及元数据交叉验证"
                    if match.automatically_linked else "待确认候选 · 高置信标题匹配"
                ),
            }.get(relation.confidence, "待确认候选")
            title = f" · {match.title}" if match.title != relation.target_workno else ""
            evidence = " · ".join(item.description for item in relation.evidence)
            observed = f" · 证据采集于 {match.observed_at.date()}" if match.observed_at else ""
            blocks.append(
                f"{label} · {self._work_link(relation.target_workno)}{escape(title)}<br>"
                f"{escape(evidence + observed)}"
            )
        blocks.extend(escape(notice).replace("\n", "<br>") for notice in result.notices)
        self.bonus_output.setText("<br><br>".join(blocks))
        self.bonus_output.setVisible(bool(blocks))

    @Slot()
    def start_lookup(self) -> None:
        """Start one background lookup unless another is still active."""
        if self._thread is not None:
            if not self._accept_worker_updates:
                self._pending_lookup = (self.code_input.text(), False)
            return
        self._run_lookup(force_refresh=False)

    @Slot()
    def force_refresh(self) -> None:
        if self._thread is not None:
            if not self._accept_worker_updates:
                self._pending_lookup = (self.code_input.text(), True)
            return
        self._run_lookup(force_refresh=True)

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
        worker.result_ready.connect(self._accept_lookup_result)
        worker.cover_ready.connect(self._accept_lookup_cover)
        worker.translations_ready.connect(self._show_translation_listings)
        worker.translations_finished.connect(self._translation_lookup_finished)
        worker.failed.connect(self._show_error)
        worker.finished.connect(thread.quit)
        thread.finished.connect(worker.deleteLater)
        thread.finished.connect(self._lookup_finished)
        self._thread, self._worker = thread, worker
        self._accept_worker_updates = True
        thread.start()

    def _enter_busy_state(self, force_refresh: bool) -> None:
        """Clear the previous projection and announce the admitted request."""
        self._focus_before_request = QApplication.focusWidget()
        self._focus_moved_while_busy = False
        self._save_history_visit()
        self._set_loading(True)
        self._clear_result()
        self._scroll_area.verticalScrollBar().setValue(0)
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
    def _accept_lookup_result(self, value: object) -> None:
        if not self._accept_worker_updates:
            return
        result = cast(LookupResult, value)
        index = self._history_index
        if index >= 0 and self._history[index].result.work.workno == result.work.workno:
            self._history[index] = _LookupVisit(result, scroll=self._history[index].scroll)
        else:
            del self._history[index + 1:]
            self._history.append(_LookupVisit(result))
            del self._history[:-_HISTORY_LIMIT]
            self._history_index = len(self._history) - 1
        self._show_result(result)
        self._update_history_buttons()

    def _show_result(
        self, result: LookupResult, *, bonus: BonusSearchResult | None = None,
        bonus_preview: bool = True,
    ) -> None:
        self._current_result = result
        work = result.work
        self._bonus_result = None
        self.identity_frame.setVisible(True)
        self.name_section.setVisible(True)
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
        for value in (
            self.maker_value, self.maker_id_value, self.release_value, self.series_value,
            self.cvs_value, self.tags_value, self.language_value,
        ):
            self.facts.setRowVisible(value, value.text() not in {"", "—"})
        self.facts.setRowVisible(self.age_value, work.age_category not in {None, AgeCategory.UNKNOWN})
        self._formatted_name = result.formatted_name
        self.name_output.setText(result.formatted_name)
        self.copy_feedback.clear()
        self._copy_feedback_timer.stop()
        self.name_output.set_copy_acknowledged(False)
        self._cover_content = None
        self._cover_pixmap = QPixmap()
        self.cover_status_label.setVisible(bool(
            work.cover_url and self._accept_worker_updates and isinstance(self._worker, LookupWorker)
        ))
        self._set_cover_placeholder()
        self._show_relations()
        if bonus is None:
            self._show_bonus_preview(result)
        else:
            self._show_bonus_result(bonus, preview=bonus_preview)
            self._show_bonus_preview(result)
        label = {
            LookupFreshness.LIVE: "实时",
            LookupFreshness.CACHE_FRESH: "缓存",
            LookupFreshness.CACHE_STALE_FALLBACK: "旧缓存",
            LookupFreshness.LOCAL_BONUS_REVIEW: "本地特典关系",
        }[result.freshness]
        if result.has_local_bonus_relation:
            if result.freshness is LookupFreshness.CACHE_FRESH:
                label = "本地特典关系"
            elif result.freshness is not LookupFreshness.LOCAL_BONUS_REVIEW:
                label += " · 本地特典关系"
        self.status_label.setProperty(
            "state", "stale" if result.freshness is LookupFreshness.CACHE_STALE_FALLBACK else "success"
        )
        self.status_label.setText(f"查询完成 · {label}")
        self._refresh_status_style()

    @Slot(str, bytes)
    def _accept_lookup_cover(self, workno: str, content: bytes) -> None:
        if (
            not self._accept_worker_updates or self.sender() is not self._worker
            or self._current_result is None or self._current_result.work.workno != workno
        ):
            return
        self.cover_status_label.hide()
        self._show_cover(content)
        if self._cover_pixmap.isNull():
            self._set_cover_placeholder()

    def _show_cover(self, content: bytes) -> None:
        if self._accept_worker_updates:
            self._display_cover(content)

    def _display_cover(self, content: bytes) -> None:
        pixmap = QPixmap()
        if not pixmap.loadFromData(content):
            return
        self._cover_content, self._cover_pixmap = content, pixmap
        self._render_cover()
        self.cover_status_label.setVisible(False)

    @Slot(str)
    def _show_error(self, message: str) -> None:
        if not self._accept_worker_updates:
            return
        if self._history_index >= 0:
            self._restore_history_visit()
        self.status_label.setProperty("state", "error")
        self.status_label.setText(message)
        self._refresh_status_style()

    @Slot()
    def _lookup_finished(self) -> None:
        thread = self._thread
        if thread is not None:
            thread.wait()
            thread.deleteLater()
        self._thread = None
        self._worker = None
        self._bonus_scan_visit = None
        self._accept_worker_updates = True
        self._update_bonus_search_action()
        self.cover_status_label.setVisible(False)
        if self._cover_pixmap.isNull():
            if self._current_result is not None:
                cover = self._cover_service.cached_cover_for(self._current_result.work.workno)
                if cover is not None:
                    self._display_cover(cover)
            if self._cover_pixmap.isNull():
                self._set_cover_placeholder()
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
        if (pending := self._pending_lookup) is not None:
            self._pending_lookup = None
            code, force_refresh = pending
            self.code_input.setText(code)
            self._run_lookup(force_refresh=force_refresh)

    @Slot()
    def copy_name(self) -> None:
        """Copy the last successful formatted name."""
        if not self._formatted_name:
            return
        clipboard = QApplication.clipboard()
        clipboard.setText(self._formatted_name)
        self.copy_feedback.setText("已复制")
        self.name_output.set_copy_acknowledged(True)
        self._copy_feedback_timer.start()

    @Slot()
    def _copy_feedback_expired(self) -> None:
        self.copy_feedback.clear()
        self.name_output.set_copy_acknowledged(False)

    def _name_copy_requested(self, captured_name: str) -> None:
        """Copy only if the result clicked is still the currently displayed result."""
        if (
            captured_name
            and captured_name == self._formatted_name
            and not self.name_section.isHidden()
        ):
            self.copy_name()

    def _set_cover_placeholder(self) -> None:
        self.cover_label.setPixmap(QPixmap())
        text = "暂无封面" if self.cover_status_label.isHidden() else ""
        if self.cover_label.text() != text:
            self.cover_label.setText(text)
        width = self._cover_display_width()
        target_size = QSize(width, min(150, max(96, round(width * 0.56))))
        if self.cover_label.size() != target_size:
            self.cover_label.setFixedSize(target_size)

    def _cover_display_width(self) -> int:
        scroll_area = self.findChild(QScrollArea, "lookupScrollArea")
        available = (
            scroll_area.viewport().width()
            if scroll_area is not None
            else self.identity_frame.width()
        )
        if available < 600:
            return max(96, round(available * 0.2))
        return max(160, min(380, round(available * 0.28 + 43)))

    def _render_cover(self) -> None:
        if self._cover_pixmap.isNull():
            self._set_cover_placeholder()
            return
        max_width = self._cover_display_width()
        max_height = 320
        scaled = self._cover_pixmap.scaled(
            QSize(max_width, max_height),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.cover_label.setText("")
        self.cover_label.setFixedSize(scaled.size())
        self.cover_label.setPixmap(scaled)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._apply_responsive_content_margins()
        if self.identity_frame.isVisible():
            self._cover_resize_timer.start(0)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._apply_responsive_content_margins()
        if self.identity_frame.isVisible():
            self._cover_resize_timer.start(0)

    def _apply_responsive_content_margins(self) -> None:
        layout = self._lookup_content_layout
        scroll_area = self.findChild(QScrollArea, "lookupScrollArea")
        if layout is None or scroll_area is None:
            return
        horizontal_margin = 16 if scroll_area.viewport().width() < 600 else 24
        margins = layout.contentsMargins()
        if margins.left() != horizontal_margin or margins.right() != horizontal_margin:
            layout.setContentsMargins(
                horizontal_margin, margins.top(), horizontal_margin, margins.bottom()
            )

    def is_busy(self) -> bool:
        """Return whether a worker thread is currently active."""
        return self._thread is not None

    def _set_loading(self, loading: bool) -> None:
        self.query_button.setEnabled(not loading)
        self.refresh_button.setEnabled(not loading)
        self.code_input.setEnabled(not loading)
        idle = not loading and not self.is_busy()
        scanning = self._accept_worker_updates and isinstance(self._worker, BonusSearchWorker)
        self.bonus_search_button.setEnabled(idle or scanning)
        self.bonus_advanced.setEnabled(idle)
        self.bonus_verify_button.setEnabled(idle)
        self.bonus_candidate_input.setEnabled(idle)
        self.bonus_range.setEnabled(idle)
        self.bonus_start.setEnabled(idle)
        self.bonus_end.setEnabled(idle)
        for button in self.bonus_review_widget.findChildren(QPushButton):
            button.setEnabled(idle)

    def _clear_result(self) -> None:
        self._scroll_restore_timer.stop()
        self._current_result = None
        self._bonus_result = None
        self._bonus_is_preview = True
        self._bonus_pending = False
        self._bonus_activity_timer.stop()
        self.bonus_status.hide()
        self.bonus_output.hide()
        self.bonus_scan_details.hide()
        self.bonus_review_widget.hide()
        self.bonus_campaign.hide()
        self.bonus_search_button.setText("查找特典 RJ")
        self.bonus_panel.hide()
        self.related_card.hide()
        self.bonus_candidate_input.clear()
        self._formatted_name = ""
        self._copy_feedback_timer.stop()
        self.name_output.set_copy_acknowledged(False)
        self.name_output.clear()
        self.copy_feedback.clear()
        self.identity_frame.setVisible(False)
        self.name_section.setVisible(False)
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
        self._cover_content = None
        self._cover_pixmap = QPixmap()
        self.cover_status_label.setVisible(False)
        self._set_cover_placeholder()
        self._clear_relations()

    def _linked_label(self) -> QLabel:
        label = _value_label()
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByMouse
            | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
        )
        label.linkActivated.connect(self._open_related_work)
        return label

    def _work_link(self, code: str) -> str:
        if self._current_result and code == self._current_result.work.workno:
            return f"{escape(code)}（当前）"
        return f'<a href="{escape(code)}" style="color:{LIGHT_COLORS.accent}">{escape(code)}</a>'

    @Slot(str)
    def _open_related_work(self, code: str) -> None:
        if self.is_busy():
            self._pending_lookup = (code, False)
            self._stop_worker_updates()
            self.translation_loading.hide()
            self.cover_status_label.hide()
            if self._cover_pixmap.isNull():
                self._set_cover_placeholder()
            return
        self.code_input.setText(code)
        self.start_lookup()

    @Slot()
    def _cancel_translation_lookup(self) -> None:
        if isinstance(self._worker, LookupWorker):
            self._worker.cancel_translations()
            self.translation_cancel.setEnabled(False)
            self.translation_status.setText("正在停止补查…")

    @Slot()
    def _translation_lookup_finished(self) -> None:
        if self._accept_worker_updates:
            self.translation_loading.hide()

    @Slot(object)
    def _show_translation_listings(self, value: object) -> None:
        if self._accept_worker_updates and isinstance(value, TranslationListings):
            self._translation_listings = value
            self._show_relations()

    def _show_relations(self) -> None:
        result = self._current_result
        if result is None:
            return
        analysis = result.translation
        listings = self._translation_listings
        attribution = result.translation_attribution
        history = result.historical_relations
        lines = []
        if listings:
            if listings.original:
                lines.append(f"原作 · {self._work_link(listings.original)}")
            else:
                lines.append("原作编号暂未获得")
            for version in listings.versions:
                language = _language_label(version.language) if version.language else "语言暂未获得"
                lines.append(f"<br><b>{escape(language)}</b> · {self._work_link(version.workno)}")
                for translator in version.translators:
                    name = _translator_label(translator)
                    lines.append(f"&nbsp;&nbsp;译者作品 · {self._work_link(translator.workno)} · {name}")
                if not version.children_loaded:
                    lines.append("&nbsp;&nbsp;译者列表暂未获得")
                elif not version.translators:
                    lines.append("&nbsp;&nbsp;DLsite 当前未列出译者作品")
            for translator in listings.ungrouped:
                lines.append(
                    f"译者作品 · {self._work_link(translator.workno)} · "
                    f"{_translator_label(translator)}"
                )
            if listings.original == result.work.workno and not listings.versions:
                lines.append(
                    "DLsite 当前未列出语言版本" if listings.original_checked else "语言版本暂未获得"
                )
        else:
            if analysis.role is TranslationRole.ORIGINAL:
                lines.append(f"原作 · {self._work_link(result.work.workno)}")
            if analysis.language:
                lines.append(escape(_language_label(analysis.language)))
            editions = {edition.workno: edition.language for edition in analysis.editions or ()}
            for edge in analysis.relations:
                language = editions.get(edge.target_workno)
                label = _language_label(language) if language else _relation_label(edge.relation_type)
                lines.append(f"{escape(label)} · {self._work_link(edge.target_workno)}")
        if analysis.status in {TranslationAnalysisStatus.INVALID, TranslationAnalysisStatus.INCOMPLETE}:
            lines.append("DLsite 提供的部分关联资料暂不可用。")
        self.relation_details_value.setText("<br>".join(lines))
        self.relation_details_value.setVisible(bool(lines))
        self.relation_attribution_value.setText(_attribution_label(attribution))
        self.relation_attribution_value.setVisible(attribution is not None)
        self.relation_attribution_value.setAccessibleName("当前作品译者")

        evidence = list(listings.evidence) if listings else [
            f"{edge.source_workno} → {edge.target_workno} · "
            f"{item.attributes.get('field', edge.detection_source)} · {result.source}"
            for edge in analysis.relations for item in edge.evidence
        ]
        for edge in (*history.outgoing, *history.incoming):
            sources = "、".join(dict.fromkeys(
                f"{item.provider_source} / {item.field}" for item in edge.evidence
            ))
            evidence.append(
                f"{edge.subject_workno} → {edge.target_workno} · {_relation_label(edge.relation_type)}"
                f" · 本地采集 {edge.first_seen.date()} 至 {edge.last_seen.date()}"
                f" · {edge.observation_count} 次" + (f" · {sources}" if sources else "")
            )
        for review in result.manual_reviews:
            pair = (
                f"{review.subject_workno} → {review.target_workno}" if review.subject_workno
                else f"{review.workno_a} / {review.workno_b}"
            )
            outcome = {"related": "相关", "not_related": "无关", "unsure": "待定"}[review.outcome.value]
            evidence.append(
                f"{pair} · 人工标记为{outcome} · {review.reviewed_at:%Y-%m-%d %H:%M}"
                + (f" · {review.relation_type.value}" if review.relation_type else "")
                + (f" · {review.notes}" if review.notes else "")
            )
        evidence = list(dict.fromkeys(evidence))
        self.historical_relations_value.setText("\n".join(evidence))
        self.relation_evidence.setVisible(bool(evidence))
        self.historical_relations_value.setVisible(bool(evidence) and self.relation_evidence.isChecked())
        if listings is None:
            self.translation_status.setText("正在加载语言版本与译者…")
            self.translation_loading.setVisible(
                self._accept_worker_updates
                and isinstance(self._worker, LookupWorker) and analysis.role is not None
            )
        self.relation_frame.setVisible(bool(lines or attribution or evidence))

    def _clear_relations(self) -> None:
        self._translation_listings = None
        self.relation_frame.hide()
        self.relation_evidence.setChecked(False)
        self.translation_loading.hide()
        self.translation_cancel.setEnabled(True)
        self.relation_attribution_value.clear()
        self.relation_details_value.clear()
        self.historical_relations_value.clear()

    def _refresh_status_style(self) -> None:
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)


class _CampaignStatusLink(QLabel):
    def __init__(self, headline: str, *, active: bool) -> None:
        super().__init__()
        self.setObjectName("lookupCampaignStatus")
        self.setAccessibleName(headline)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.LinksAccessibleByMouse)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        style = f"color:{LIGHT_COLORS.success};font-weight:bold" if active else ""
        self.setText(f'<a href="details" style="{style}">{escape(headline)}</a>')

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Enter, Qt.Key.Key_Return, Qt.Key.Key_Space):
            if not event.isAutoRepeat():
                self.linkActivated.emit("details")
            event.accept()
            return
        super().keyPressEvent(event)


class _CopyAffordance(QLabel):
    """Small noninteractive vector copy/check glyph inside the name region."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__("⧉", parent)
        self._acknowledged = False
        self.setObjectName("lookupCopyAffordance")
        self.setFixedSize(22, 22)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_acknowledged(self, acknowledged: bool) -> None:
        self._acknowledged = acknowledged
        self.setText("✓" if acknowledged else "⧉")
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        del event
        color = QColor(LIGHT_COLORS.success if self._acknowledged else LIGHT_COLORS.text_muted)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(color)
        pen.setWidthF(1.5 if not self._acknowledged else 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if self._acknowledged:
            painter.drawLine(5, 11, 9, 15)
            painter.drawLine(9, 15, 17, 7)
        else:
            painter.drawRoundedRect(QRectF(4.5, 3.5, 9, 12), 1.2, 1.2)
            painter.drawRoundedRect(QRectF(8.5, 7.5, 9, 12), 1.2, 1.2)
        painter.end()


class _CopyableNameLabel(QLabel):
    """Selectable formatted name with delayed single-click copy activation."""

    def __init__(
        self,
        copy_requested: Callable[[str], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._copy_requested = copy_requested
        self._copy_acknowledged = False
        self._press_position: QPoint | None = None
        self._press_name = ""
        self._dragged = False
        self._double_click = False
        self._captured_name = ""
        self._single_click_timer = QTimer(self)
        self._single_click_timer.setSingleShot(True)
        self._single_click_timer.setInterval(QApplication.doubleClickInterval())
        self._single_click_timer.timeout.connect(self._copy_captured_name)
        self.setMouseTracking(True)
        self.copy_affordance = _CopyAffordance(self)
        self.copy_affordance.hide()

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802
        super().enterEvent(event)
        self._update_copy_affordance()

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802
        super().leaveEvent(event)
        self._update_copy_affordance()

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        super().focusInEvent(event)
        self._update_copy_affordance()

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self._update_copy_affordance()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self.copy_affordance.move(
            self.width() - self.copy_affordance.width() - 6,
            (self.height() - self.copy_affordance.height()) // 2,
        )

    def set_copy_acknowledged(self, acknowledged: bool) -> None:
        self._copy_acknowledged = acknowledged
        self.copy_affordance.set_acknowledged(acknowledged)
        self._update_copy_affordance()

    def _update_copy_affordance(self) -> None:
        self.copy_affordance.setVisible(
            self.underMouse() or self.hasFocus() or self._copy_acknowledged
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._single_click_timer.stop()
            self._press_position = event.position().toPoint()
            self._press_name = self.text()
            self._dragged = False
            self._double_click = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if (
            self._press_position is not None
            and event.buttons() & Qt.MouseButton.LeftButton
            and (event.position().toPoint() - self._press_position).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            self._dragged = True
            self._single_click_timer.stop()
        super().mouseMoveEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self._single_click_timer.stop()
        self._double_click = True
        self._dragged = False
        super().mouseDoubleClickEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        super().mouseReleaseEvent(event)
        if event.button() != Qt.MouseButton.LeftButton:
            return
        if not self._double_click and not self._dragged:
            self._captured_name = self._press_name
            if self._captured_name:
                self._single_click_timer.start()
        self._press_position = None
        self._press_name = ""

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            name = self.text()
            if name:
                self._copy_requested(name)
            event.accept()
            return
        super().keyPressEvent(event)

    @Slot()
    def _copy_captured_name(self) -> None:
        name, self._captured_name = self._captured_name, ""
        if name:
            self._copy_requested(name)


def _value_label() -> QLabel:
    label = QLabel("—")
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
    return label


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


def _translator_label(translator: TranslatorListing) -> str:
    status = {
        TranslationDetailStatus.UNAVAILABLE: "作品详情暂不可获取",
        TranslationDetailStatus.NOT_RELEASED: "尚未发售",
        TranslationDetailStatus.FAILED: "查询失败，可稍后重试",
    }.get(translator.detail_status)
    if translator.name:
        return escape(f"{translator.name} · {status}" if status else translator.name)
    return status or "译者名称未公开"


def _attribution_label(value: TranslationAttribution | None) -> str:
    if value is None:
        return "—"
    if value.maker_name and value.maker_id:
        return f"{value.maker_name} ({value.maker_id})"
    return value.maker_name or value.maker_id or "—"


def _relation_label(relation_type: RelationType) -> str:
    return {
        RelationType.TRANSLATION_OF: "原作",
        RelationType.HAS_LANGUAGE_EDITION: "语言版本",
        RelationType.HAS_TRANSLATION_CHILD: "翻译子条目",
        RelationType.CHILD_OF_TRANSLATION: "翻译父条目",
    }.get(relation_type, relation_type.value)
