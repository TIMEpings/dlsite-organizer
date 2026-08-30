"""Qt view for the derived local candidate review queue."""
# ruff: noqa: E501

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dlsite_organizer.domain.candidate import (
    CandidateQueueFilter,
    CandidateRelation,
    CandidateReviewQueueItem,
)
from dlsite_organizer.ui.manual_review_dialog import open_manual_review_dialog


class ReviewQueuePage(QWidget):
    def __init__(self, queue_service=None, manual_review_service=None, parent=None) -> None:
        super().__init__(parent)
        self._queue_service: Any = queue_service
        self._manual_review_service: Any = manual_review_service
        self._result: Any = None
        self._items: tuple[CandidateReviewQueueItem, ...] = ()
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
        for value, label in ((CandidateQueueFilter.UNREVIEWED, "未审"), (CandidateQueueFilter.UNSURE, "不确定"), (CandidateQueueFilter.ALL, "全部"), (CandidateQueueFilter.REVIEWED, "已审"), (CandidateQueueFilter.RELATED, "有关联"), (CandidateQueueFilter.NOT_RELATED, "无关联")):
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
        self.table.setHorizontalHeaderLabels(["RJ A", "标题 A", "RJ B", "标题 B", "Maker", "登记日期", "RJ 距离", "审阅状态"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._show_details)
        layout.addWidget(self.table, 1)
        self.empty_state = QLabel("")
        self.empty_state.setWordWrap(True)
        layout.addWidget(self.empty_state)
        self.details = QLabel("选择一条候选查看 evidence/context。")
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.details)
        self.review_button = QPushButton("审阅选中候选")
        self.review_button.setEnabled(False)
        self.review_button.clicked.connect(self.review_selected)
        layout.addWidget(self.review_button)

    @Slot()
    def reload(self) -> None:
        if self._queue_service is None:
            self._result = None
            self._items = ()
            self.table.setRowCount(0)
            self.summary_label.setText("当前本地数据库没有足够作品元数据。")
            self.empty_state.setText("当前本地数据库没有足够作品元数据。")
            self.page_label.setText("第 1 页 / 共 1 页")
            self.previous_button.setEnabled(False)
            self.next_button.setEnabled(False)
            self._show_details()
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
            values = [item.workno_a, item.work_a_title, item.workno_b, item.work_b_title, item.maker_display, item.regist_date.isoformat(), str(item.rj_numeric_distance or "-"), item.review_state.value]
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
        self._show_details()

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

    @Slot()
    def _show_details(self) -> None:
        row = self.table.currentRow()
        self.review_button.setEnabled(0 <= row < len(self._items))
        if not (0 <= row < len(self._items)):
            self.details.setText("选择一条候选查看 evidence/context。")
            return
        item = self._items[row]
        evidence = "\n".join(f"{entry.description}" for entry in item.supporting_evidence)
        context = "\n".join(f"{entry.description}" for entry in item.context)
        provenance = "；".join(
            sorted({item.source_snapshot.source.value, item.target_snapshot.source.value})
        )
        self.details.setText(f"Supporting evidence\n{evidence}\nContext\n{context}\nMetadata provenance: {provenance}")

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


CandidateReviewQueuePage = ReviewQueuePage
