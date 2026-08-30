from datetime import UTC, datetime

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from dlsite_organizer.domain.candidate import (
    CandidateQueueFilter,
    CandidateSnapshotSource,
    KnownWorkSnapshot,
)
from dlsite_organizer.domain.manual_review import ManualReviewEvent
from dlsite_organizer.services.candidate_review_queue import CandidateReviewQueueService
from dlsite_organizer.services.manual_reviews import ManualReviewService
from dlsite_organizer.ui.pages.review_queue_page import ReviewQueuePage

WHEN = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)


@pytest.fixture
def qapp() -> QApplication:
    application = QApplication.instance()
    if isinstance(application, QApplication):
        return application
    return QApplication([])


def snapshot(
    workno: str,
    *,
    maker_id: str | None = "RG1",
    maker_name: str | None = "Circle A",
    day: int = 1,
    regist_datetime: datetime | None = None,
) -> KnownWorkSnapshot:
    return KnownWorkSnapshot(
        workno=workno,
        title=f"Title {workno}",
        maker_id=maker_id,
        maker_name=maker_name,
        regist_datetime=regist_datetime or datetime(2026, 1, day, tzinfo=UTC),
        source=CandidateSnapshotSource.CURRENT_CACHE,
    )


class Repository:
    def __init__(self, items: tuple[KnownWorkSnapshot, ...]) -> None:
        self.items = items

    def list_known_work_summaries(self) -> tuple[KnownWorkSnapshot, ...]:
        return self.items

    def list_current_relation_pairs(self) -> tuple[tuple[str, str], ...]:
        return ()


class ReviewStore:
    def __init__(self) -> None:
        self.events: list[ManualReviewEvent] = []

    def append(self, event: ManualReviewEvent) -> ManualReviewEvent:
        saved = event.model_copy(update={"id": len(self.events) + 1})
        self.events.append(saved)
        return saved

    def list_reviews(self) -> tuple[ManualReviewEvent, ...]:
        return tuple(self.events)

    def latest_for_pair(self, workno_a: str, workno_b: str) -> ManualReviewEvent | None:
        pair = tuple(sorted((workno_a, workno_b)))
        return next(
            (event for event in reversed(self.events) if (event.workno_a, event.workno_b) == pair),
            None,
        )

    def history_for_pair(self, workno_a: str, workno_b: str) -> tuple[ManualReviewEvent, ...]:
        pair = tuple(sorted((workno_a, workno_b)))
        return tuple(event for event in self.events if (event.workno_a, event.workno_b) == pair)


def queue_service(
    items: tuple[KnownWorkSnapshot, ...],
    *,
    manual_reviews=None,
) -> CandidateReviewQueueService:
    return CandidateReviewQueueService(
        Repository(items),
        manual_reviews=manual_reviews,
        clock=lambda: WHEN,
    )


def test_queue_page_renders_pair_and_structured_details(qapp: QApplication) -> None:
    page = ReviewQueuePage(
        queue_service(
            (
                snapshot("RJ00000001"),
                snapshot("RJ00000002"),
            )
        )
    )

    assert CandidateQueueFilter(page.filter_combo.currentData()) is CandidateQueueFilter.UNREVIEWED
    assert page.table.rowCount() == 1
    workno_cell = page.table.item(0, 0)
    assert workno_cell is not None
    assert workno_cell.text() == "RJ00000001"

    page.table.selectRow(0)

    assert "RJ numeric distance: 1." in page.details.text()
    assert "Locally known same-maker same-date works: 2." in page.details.text()
    assert "current_cache" in page.details.text()
    assert "score" not in page.details.text().lower()
    page.close()


def test_queue_page_distinguishes_empty_metadata_from_empty_candidate_group(
    qapp: QApplication,
) -> None:
    empty_page = ReviewQueuePage(queue_service(()))
    assert empty_page.empty_state.text() == "当前本地数据库没有足够作品元数据。"
    empty_page.close()

    insufficient_page = ReviewQueuePage(
        queue_service(
            (
                snapshot("RJ00000001", maker_id=None, maker_name=None),
            )
        )
    )
    assert insufficient_page.empty_state.text() == "当前没有满足候选策略的作品组合。"
    insufficient_page.close()


def test_queue_review_uses_manual_service_and_refreshes_default_filter(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ReviewStore()
    manual_reviews = ManualReviewService(store, clock=lambda: WHEN)
    page = ReviewQueuePage(
        queue_service(
            (
                snapshot("RJ00000001"),
                snapshot("RJ00000002"),
            ),
            manual_reviews=manual_reviews,
        ),
        manual_reviews,
    )
    page.table.selectRow(0)

    def drive(dialog: QDialog) -> int:
        buttons = dialog.findChild(QDialogButtonBox)
        assert buttons is not None
        buttons.accepted.emit()
        return int(dialog.result())

    monkeypatch.setattr(QDialog, "exec", drive)
    page.review_selected()

    assert len(store.events) == 1
    assert store.events[0].evidence_snapshot.schema_version == 2
    assert page.table.rowCount() == 0
    assert page.empty_state.text() == "当前符合条件的候选均已有人工判断。"

    page.filter_combo.setCurrentIndex(page.filter_combo.findData(CandidateQueueFilter.ALL))
    assert page.table.rowCount() == 1
    state_cell = page.table.item(0, 7)
    assert state_cell is not None
    assert state_cell.text() == "related"
    assert "已决定：1" in page.summary_label.text()
    page.close()
