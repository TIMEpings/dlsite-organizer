"""Qt view for the derived local candidate review queue."""
# ruff: noqa: E501

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Slot
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dlsite_organizer.domain.candidate import (
    CandidateQueueFilter,
    CandidateRelation,
    CandidateReviewQueueItem,
    KnownWorkSnapshot,
)
from dlsite_organizer.ui.manual_review_dialog import open_manual_review_dialog


class ReviewQueuePage(QWidget):
    """Present a local candidate queue and all review-critical local evidence."""

    def __init__(self, queue_service=None, manual_review_service=None, cover_service=None, parent=None) -> None:
        super().__init__(parent)
        self._queue_service: Any = queue_service
        self._manual_review_service: Any = manual_review_service
        self._cover_service: Any = cover_service
        self._result: Any = None
        self._items: tuple[CandidateReviewQueueItem, ...] = ()
        self._selected_item: CandidateReviewQueueItem | None = None
        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(36, 30, 36, 30)
        title = QLabel("候选审阅")
        title.setObjectName("pageTitle")
        layout.addWidget(title)
        self.policy_label = QLabel("当前策略：same-maker-same-date v1")
        self.summary_label = QLabel("本地已知作品：0  可参与筛选：0  候选 pair：0  未审：0  不确定：0  已决定：0")
        layout.addWidget(self.policy_label)
        layout.addWidget(self.summary_label)

        controls = QHBoxLayout()
        self.filter_combo = QComboBox()
        for value, label in (
            (CandidateQueueFilter.UNREVIEWED, "未审"),
            (CandidateQueueFilter.UNSURE, "不确定"),
            (CandidateQueueFilter.ALL, "全部"),
            (CandidateQueueFilter.REVIEWED, "已审"),
            (CandidateQueueFilter.RELATED, "有关联"),
            (CandidateQueueFilter.NOT_RELATED, "无关联"),
        ):
            self.filter_combo.addItem(label, value)
        self.filter_combo.currentIndexChanged.connect(self._filter_changed)
        controls.addWidget(self.filter_combo)
        self.refresh_button = QPushButton("刷新候选")
        self.refresh_button.clicked.connect(self.reload)
        controls.addWidget(self.refresh_button)
        controls.addStretch(1)
        self.previous_button = QPushButton("上一页")
        self.next_button = QPushButton("下一页")
        self.previous_button.clicked.connect(self.previous_page)
        self.next_button.clicked.connect(self.next_page)
        controls.addWidget(self.previous_button)
        controls.addWidget(self.next_button)
        self.page_label = QLabel("第 1 页")
        controls.addWidget(self.page_label)
        layout.addLayout(controls)

        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(
            ["RJ A", "标题 A", "RJ B", "标题 B", "Maker", "登记日期", "RJ 距离", "审阅状态"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._show_details)
        layout.addWidget(self.table, 1)

        self.empty_state = QLabel("")
        self.empty_state.setWordWrap(True)
        layout.addWidget(self.empty_state)

        # Keep the details area bounded and scrollable so the review action is
        # reachable on a normal 1080p window even for long Unicode titles.
        self.details_scroll = QScrollArea()
        self.details_scroll.setObjectName("reviewQueueDetailsScrollArea")
        self.details_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.details_scroll.setWidgetResizable(True)
        self.details_scroll.setMinimumHeight(245)
        self.details_scroll.setMaximumHeight(380)
        self.details_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        details_content = QWidget()
        details_layout = QVBoxLayout(details_content)
        details_layout.setContentsMargins(0, 0, 0, 0)
        details_layout.setSpacing(8)

        self._pair_cards = QWidget()
        pair_layout = QHBoxLayout(self._pair_cards)
        pair_layout.setContentsMargins(0, 0, 0, 0)
        pair_layout.setSpacing(10)
        (
            self.work_a_card,
            self.work_a_cover,
            self.work_a_workno_value,
            self.work_a_title_value,
            self.work_a_maker_id_value,
            self.work_a_maker_name_value,
            self.work_a_date_value,
            self.work_a_provenance_value,
        ) = self._build_work_card("Work A")
        (
            self.work_b_card,
            self.work_b_cover,
            self.work_b_workno_value,
            self.work_b_title_value,
            self.work_b_maker_id_value,
            self.work_b_maker_name_value,
            self.work_b_date_value,
            self.work_b_provenance_value,
        ) = self._build_work_card("Work B")
        pair_layout.addWidget(self.work_a_card, 1)
        pair_layout.addWidget(self.work_b_card, 1)
        details_layout.addWidget(self._pair_cards)

        self.details = QLabel("选择一条候选查看完整作品信息与 evidence/context。")
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details_layout.addWidget(self.details)
        self.details_scroll.setWidget(details_content)
        layout.addWidget(self.details_scroll)

        self.review_button = QPushButton("审阅选中候选")
        self.review_button.setEnabled(False)
        self.review_button.clicked.connect(self.review_selected)
        layout.addWidget(self.review_button)

    @staticmethod
    def _build_work_card(
        heading: str,
    ) -> tuple[QFrame, QLabel, QLabel, QLabel, QLabel, QLabel, QLabel, QLabel]:
        card = QFrame()
        card.setObjectName("relationCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(7)
        heading_label = QLabel(heading)
        heading_label.setObjectName("sectionLabel")
        card_layout.addWidget(heading_label)

        body = QHBoxLayout()
        body.setSpacing(10)
        cover = QLabel("封面未缓存")
        cover.setObjectName("coverPlaceholder")
        cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover.setWordWrap(True)
        cover.setFixedSize(180, 240)
        body.addWidget(cover, 0, Qt.AlignmentFlag.AlignTop)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(4)
        workno = QLabel("RJcode：—")
        workno.setWordWrap(True)
        full_title = QLabel("完整标题：标题不可用")
        full_title.setWordWrap(True)
        full_title.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        maker_id = QLabel("Maker ID：—")
        maker_id.setWordWrap(True)
        maker_name = QLabel("Maker name unavailable")
        maker_name.setWordWrap(True)
        registration_date = QLabel("Registration date：—")
        registration_date.setWordWrap(True)
        provenance = QLabel("Metadata provenance：—")
        provenance.setWordWrap(True)
        for value in (workno, full_title, maker_id, maker_name, registration_date, provenance):
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            text_layout.addWidget(value)
        text_layout.addStretch(1)
        body.addLayout(text_layout, 1)
        card_layout.addLayout(body)
        return card, cover, workno, full_title, maker_id, maker_name, registration_date, provenance

    @Slot()
    def reload(self) -> None:
        """Rebuild the derived queue and clear any details from the old build."""
        self._clear_selection()
        if self._queue_service is None:
            self._result = None
            self._items = ()
            self.table.setRowCount(0)
            self.summary_label.setText("当前本地数据库没有足够作品元数据。")
            self.empty_state.setText("当前本地数据库没有足够作品元数据。")
            self.page_label.setText("第 1 页 / 共 1 页")
            self.previous_button.setEnabled(False)
            self.next_button.setEnabled(False)
            return
        result = self._queue_service.build(
            filter=CandidateQueueFilter(self.filter_combo.currentData()),
            page=self._page_number(),
            page_size=100,
        )
        self._result = result
        self._items = result.items
        self.table.setRowCount(len(self._items))
        for row, item in enumerate(self._items):
            values = [
                item.workno_a,
                item.work_a_title or "标题不可用",
                item.workno_b,
                item.work_b_title or "标题不可用",
                item.maker_display,
                item.regist_date.isoformat(),
                str(item.rj_numeric_distance if item.rj_numeric_distance is not None else "-"),
                item.review_state.value,
            ]
            for col, value in enumerate(values):
                self.table.setItem(row, col, QTableWidgetItem(value))
        self._update_summary(result)
        if result.total_count:
            self.empty_state.setText("")
        elif result.known_work_count == 0:
            self.empty_state.setText("当前本地数据库没有足够作品元数据。")
        elif result.eligible_work_count == 0:
            self.empty_state.setText("当前没有满足候选策略的作品组合。")
        elif result.filter is CandidateQueueFilter.UNREVIEWED:
            self.empty_state.setText("当前符合条件的候选均已有人工判断。")
        else:
            self.empty_state.setText("当前没有符合筛选条件的候选。")
        self.page_label.setText(f"第 {self._page_number()} 页 / 共 {max(1, (result.total_count + 99) // 100)} 页")
        self.previous_button.setEnabled(self._page_number() > 1)
        self.next_button.setEnabled(self._page_number() * 100 < result.total_count)

    @Slot()
    def _filter_changed(self) -> None:
        self._page = 1
        self.reload()

    def _page_number(self) -> int:
        return getattr(self, "_page", 1)

    def previous_page(self) -> None:
        self._page = max(1, self._page_number() - 1)
        self.reload()

    def next_page(self) -> None:
        self._page = self._page_number() + 1
        self.reload()

    def _update_summary(self, result) -> None:
        # Counts are descriptive only; they are never a metric or ranking.
        self.summary_label.setText(
            f"本地已知作品：{result.known_work_count}  可参与筛选：{result.eligible_work_count}  候选 pair：{result.unreviewed_count + result.related_count + result.not_related_count + result.unsure_count}  未审：{result.unreviewed_count}  不确定：{result.unsure_count}  已决定：{result.related_count + result.not_related_count}"
        )

    def _clear_selection(self) -> None:
        self._selected_item = None
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.table.blockSignals(False)
        self.review_button.setEnabled(False)
        self._pair_cards.setVisible(False)
        self.details.setText("选择一条候选查看完整作品信息与 evidence/context。")

    @Slot()
    def _show_details(self) -> None:
        row = self.table.currentRow()
        if not (0 <= row < len(self._items)):
            self._selected_item = None
            self.review_button.setEnabled(False)
            self._pair_cards.setVisible(False)
            self.details.setText("选择一条候选查看完整作品信息与 evidence/context。")
            return

        item = self._items[row]
        self._selected_item = item
        self.review_button.setEnabled(True)
        self._pair_cards.setVisible(True)
        self._populate_work_card(
            self.work_a_cover,
            self.work_a_workno_value,
            self.work_a_title_value,
            self.work_a_maker_id_value,
            self.work_a_maker_name_value,
            self.work_a_date_value,
            self.work_a_provenance_value,
            item.source_snapshot,
        )
        self._populate_work_card(
            self.work_b_cover,
            self.work_b_workno_value,
            self.work_b_title_value,
            self.work_b_maker_id_value,
            self.work_b_maker_name_value,
            self.work_b_date_value,
            self.work_b_provenance_value,
            item.target_snapshot,
        )

        evidence = "\n".join(entry.description for entry in item.supporting_evidence) or "—"
        context = "\n".join(entry.description for entry in item.context) or "—"
        latest_review = self._latest_review_text(item)
        source_provenance = item.source_snapshot.source.value
        target_provenance = item.target_snapshot.source.value
        title_a = _display_title(item.source_snapshot)
        title_b = _display_title(item.target_snapshot)
        self.details.setText(
            "\n".join(
                (
                    "Candidate Pair Details",
                    f"Work A · {item.workno_a}",
                    f"Full title A: {title_a}",
                    f"Work B · {item.workno_b}",
                    f"Full title B: {title_b}",
                    f"Metadata provenance A: {source_provenance}",
                    f"Metadata provenance B: {target_provenance}",
                    "Supporting evidence",
                    evidence,
                    "Context",
                    context,
                    "Latest Manual Review",
                    latest_review,
                    "Cover availability: local only; no automatic cover fetch.",
                )
            )
        )

    def _populate_work_card(
        self,
        cover: QLabel,
        workno: QLabel,
        title: QLabel,
        maker_id: QLabel,
        maker_name: QLabel,
        registration_date: QLabel,
        provenance: QLabel,
        snapshot: KnownWorkSnapshot,
    ) -> None:
        workno.setText(f"RJcode：{snapshot.workno}")
        title.setText(f"完整标题：{_display_title(snapshot)}")
        maker_id.setText(f"Maker ID：{snapshot.maker_id or 'Maker ID unavailable'}")
        maker_name.setText(f"Maker name：{snapshot.maker_name or 'Maker name unavailable'}")
        registration_date.setText(
            "Registration date："
            + (snapshot.regist_datetime.isoformat() if snapshot.regist_datetime else "unavailable")
        )
        provenance.setText(f"Metadata provenance：{snapshot.source.value}")
        self._set_cached_cover(cover, snapshot.workno)

    def _set_cached_cover(self, label: QLabel, workno: str) -> None:
        label.clear()
        label.setPixmap(QPixmap())
        getter = getattr(self._cover_service, "cached_cover_for", None)
        content: bytes | None = None
        if callable(getter):
            try:
                value = getter(workno)
                if isinstance(value, (bytes, bytearray)):
                    content = bytes(value)
            except Exception:
                # A broken optional cover must never block review.
                content = None
        if not content:
            label.setText("封面未缓存")
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(content):
            label.setText("封面不可用")
            return
        label.setPixmap(
            pixmap.scaled(
                label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    @staticmethod
    def _latest_review_text(item: CandidateReviewQueueItem) -> str:
        latest = item.latest_manual_review
        if latest is None:
            return "No manual review"
        outcome = getattr(latest.outcome, "name", str(latest.outcome).upper())
        lines = [
            f"Reviewed {item.review_event_count} time"
            + ("s" if item.review_event_count != 1 else "")
        ]
        relation_type = latest.relation_type
        if latest.outcome.value == "related" and relation_type is not None:
            relation_name = getattr(relation_type, "name", str(relation_type).upper())
            lines.append(f"Latest review: {outcome} · {relation_name}")
            if relation_type.is_directional:
                lines.append(
                    f"Direction: {latest.subject_workno} {relation_name} {latest.target_workno}"
                )
        else:
            lines.append(f"Latest review: {outcome}")
        return "\n".join(lines)

    def review_selected(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self._items):
            self._open_review_dialog(self._items[row])

    def _open_review_dialog(self, item: CandidateReviewQueueItem) -> None:
        candidate = CandidateRelation(
            source_workno=item.workno_a,
            target_workno=item.workno_b,
            supporting_evidence=item.supporting_evidence,
            context=item.context,
            evaluated_at=self._result.generated_at,
            source_snapshot=item.source_snapshot,
            target_snapshot=item.target_snapshot,
            policy_provenance=item.policy_provenance,
        )
        if open_manual_review_dialog(self, self._manual_review_service, candidate):
            self.reload()


def _display_title(snapshot: KnownWorkSnapshot) -> str:
    return snapshot.title.strip() or "标题不可用"


CandidateReviewQueuePage = ReviewQueuePage
