import base64
from datetime import UTC, datetime

import pytest
from PySide6.QtWidgets import QApplication, QDialog, QDialogButtonBox

from dlsite_organizer.domain.candidate import (
    CandidateQueueFilter,
    CandidateRelation,
    CandidateSnapshotSource,
    KnownWorkSnapshot,
)
from dlsite_organizer.domain.manual_review import (
    CandidateReviewOutcome,
    ManualRelationType,
    ManualReviewEvent,
)
from dlsite_organizer.services.candidate_review_queue import CandidateReviewQueueService
from dlsite_organizer.services.cover import CoverService
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
    title: str | None = None,
    maker_id: str | None = "RG1",
    maker_name: str | None = "Circle A",
    day: int = 1,
    regist_datetime: datetime | None = None,
    source: CandidateSnapshotSource = CandidateSnapshotSource.CURRENT_CACHE,
) -> KnownWorkSnapshot:
    return KnownWorkSnapshot(
        workno=workno,
        title=title if title is not None else f"Title {workno}",
        maker_id=maker_id,
        maker_name=maker_name,
        regist_datetime=regist_datetime or datetime(2026, 1, day, tzinfo=UTC),
        source=source,
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


ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class LocalCoverSpy:
    def __init__(self, covers: dict[str, bytes]) -> None:
        self.covers = covers
        self.cached_calls = 0
        self.fetch_calls = 0

    def cached_cover_for(self, workno: str) -> bytes | None:
        self.cached_calls += 1
        return self.covers.get(workno)

    def fetch(self, _url: str) -> bytes:
        self.fetch_calls += 1
        raise AssertionError("Queue must never fetch a cover")


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


def test_queue_details_keep_full_unicode_titles_and_mixed_provenance(qapp: QApplication) -> None:
    prefix = "同じ長い共通タイトル｜Unicode 🌏 "
    work_a = snapshot(
        "RJ00000001",
        title=prefix + "シリーズ1 完全版",
        source=CandidateSnapshotSource.CURRENT_CACHE,
    )
    work_b = snapshot(
        "RJ00000002",
        title=prefix + "シリーズ2 特別編",
        source=CandidateSnapshotSource.HISTORICAL_OBSERVATION,
    )
    cover_service = CoverService()
    cover_service.remember_cached_cover(work_a.workno, ONE_PIXEL_PNG)
    cover_service.remember_cached_cover(work_b.workno, ONE_PIXEL_PNG)
    page = ReviewQueuePage(queue_service((work_a, work_b)), cover_service=cover_service)

    page.table.selectRow(0)

    assert "シリーズ1 完全版" in page.details.text()
    assert "シリーズ2 特別編" in page.details.text()
    assert page.work_a_title_value.text().endswith("シリーズ1 完全版")
    assert page.work_b_title_value.text().endswith("シリーズ2 特別編")
    assert "Metadata provenance A: current_cache" in page.details.text()
    assert "Metadata provenance B: historical_observation" in page.details.text()
    assert page.work_a_cover.text() == ""
    assert page.work_b_cover.text() == ""
    page.close()


def test_queue_cover_read_is_local_only_and_missing_cover_is_safe(qapp: QApplication) -> None:
    covers = {"RJ00000001": ONE_PIXEL_PNG}
    cover_service = LocalCoverSpy(covers)
    page = ReviewQueuePage(
        queue_service((snapshot("RJ00000001"), snapshot("RJ00000002"))),
        cover_service=cover_service,
    )

    page.table.selectRow(0)

    assert cover_service.fetch_calls == 0
    assert cover_service.cached_calls == 2
    assert page.work_a_cover.text() == ""
    assert page.work_b_cover.text() == "封面未缓存"
    assert "no automatic cover fetch" in page.details.text()
    page.close()


def test_queue_latest_review_detail_preserves_direction_and_history(qapp: QApplication) -> None:
    store = ReviewStore()
    manual_reviews = ManualReviewService(store, clock=lambda: WHEN)
    service = queue_service(
        (snapshot("RJ00000001"), snapshot("RJ00000002")),
        manual_reviews=manual_reviews,
    )
    first = service.build(filter=CandidateQueueFilter.ALL).items[0]
    candidate = CandidateRelation(
        source_workno=first.workno_a,
        target_workno=first.workno_b,
        supporting_evidence=first.supporting_evidence,
        context=first.context,
        evaluated_at=WHEN,
        source_snapshot=first.source_snapshot,
        target_snapshot=first.target_snapshot,
        policy_provenance=first.policy_provenance,
    )
    manual_reviews.submit_review(
        first.workno_a,
        first.workno_b,
        CandidateReviewOutcome.NOT_RELATED,
        candidate=candidate,
    )
    manual_reviews.submit_review(
        first.workno_a,
        first.workno_b,
        CandidateReviewOutcome.RELATED,
        relation_type=ManualRelationType.BONUS_OF,
        subject_workno=first.workno_b,
        target_workno=first.workno_a,
        candidate=candidate,
        reviewed_at=WHEN.replace(minute=1),
    )
    page = ReviewQueuePage(service, manual_reviews)
    page.filter_combo.setCurrentIndex(page.filter_combo.findData(CandidateQueueFilter.ALL))
    page.table.selectRow(0)

    assert "Reviewed 2 times" in page.details.text()
    assert "RELATED · BONUS_OF" in page.details.text()
    assert "Direction: RJ00000002 BONUS_OF RJ00000001" in page.details.text()
    page.close()


@pytest.mark.parametrize(
    ("relation_type", "subject", "target"),
    [
        (ManualRelationType.SAME_WORK_LANGUAGE_VARIANT, None, None),
        (ManualRelationType.INCLUDED_IN, "RJ00000002", "RJ00000001"),
    ],
)
def test_queue_latest_review_detail_displays_new_relation_semantics(
    qapp: QApplication,
    relation_type: ManualRelationType,
    subject: str | None,
    target: str | None,
) -> None:
    store = ReviewStore()
    manual_reviews = ManualReviewService(store, clock=lambda: WHEN)
    service = queue_service(
        (snapshot("RJ00000001"), snapshot("RJ00000002")),
        manual_reviews=manual_reviews,
    )
    item = service.build(filter=CandidateQueueFilter.ALL).items[0]
    candidate = CandidateRelation(
        source_workno=item.workno_a,
        target_workno=item.workno_b,
        supporting_evidence=item.supporting_evidence,
        context=item.context,
        evaluated_at=WHEN,
        source_snapshot=item.source_snapshot,
        target_snapshot=item.target_snapshot,
        policy_provenance=item.policy_provenance,
    )
    manual_reviews.submit_review(
        item.workno_a,
        item.workno_b,
        CandidateReviewOutcome.RELATED,
        relation_type=relation_type,
        subject_workno=subject,
        target_workno=target,
        candidate=candidate,
    )

    page = ReviewQueuePage(service, manual_reviews)
    page.filter_combo.setCurrentIndex(page.filter_combo.findData(CandidateQueueFilter.ALL))
    page.table.selectRow(0)

    assert f"RELATED · {relation_type.name}" in page.details.text()
    if relation_type is ManualRelationType.INCLUDED_IN:
        assert "Direction: RJ00000002 INCLUDED_IN RJ00000001" in page.details.text()
    else:
        assert "Direction:" not in page.details.text()
    page.close()


def test_queue_selection_clears_on_pagination_filter_and_refresh(qapp: QApplication) -> None:
    items = tuple(snapshot(f"RJ{i:08d}") for i in range(1, 17))
    page = ReviewQueuePage(queue_service(items))
    page.table.selectRow(0)
    assert "Full title A" in page.details.text()

    page.next_page()
    assert page.table.currentRow() == -1
    assert page.details.text() == "选择一条候选查看完整作品信息与 evidence/context。"
    assert not page.review_button.isEnabled()

    page.filter_combo.setCurrentIndex(page.filter_combo.findData(CandidateQueueFilter.ALL))
    assert page.table.currentRow() == -1
    assert "Full title A" not in page.details.text()
    page.table.selectRow(0)
    page.reload()
    assert page.table.currentRow() == -1
    assert "Full title A" not in page.details.text()
    page.close()
