from datetime import UTC, datetime
from typing import Any

import pytest

from dlsite_organizer.domain.candidate import (
    CandidateEvidence,
    CandidateEvidenceKind,
    CandidateEvidencePolarity,
    CandidateRelation,
    CandidateSnapshotSource,
    KnownWorkSnapshot,
)
from dlsite_organizer.domain.manual_review import (
    CandidateEvidenceSnapshot,
    CandidateReviewOutcome,
    ManualRelationType,
    ManualReviewEvent,
    canonical_pair,
)
from dlsite_organizer.services.candidate_relations import CandidateRelationService
from dlsite_organizer.services.manual_reviews import ManualReviewService

WHEN = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)


def candidate(
    *,
    distance: int = 4,
    group_size: int = 2,
    source_workno: str = "RJ00000001",
    target_workno: str = "RJ00000002",
) -> CandidateRelation:
    source = KnownWorkSnapshot(
        workno=source_workno,
        title="source",
        maker_id="RG12345678",
        regist_datetime=WHEN,
        source=CandidateSnapshotSource.CURRENT_CACHE,
    )
    target = KnownWorkSnapshot(
        workno=target_workno,
        title="target",
        maker_id="RG12345678",
        regist_datetime=WHEN,
        source=CandidateSnapshotSource.HISTORICAL_OBSERVATION,
        observed_at=WHEN,
    )
    return CandidateRelation(
        source_workno=source_workno,
        target_workno=target_workno,
        evaluated_at=WHEN,
        source_snapshot=source,
        target_snapshot=target,
        supporting_evidence=(
            CandidateEvidence(
                kind=CandidateEvidenceKind.SAME_MAKER_ID,
                value="RG12345678",
                description="same maker",
                provenance="local metadata",
            ),
            CandidateEvidence(
                kind=CandidateEvidenceKind.SAME_REGIST_DATE,
                value="2026-08-30",
                description="same date",
                provenance="local metadata regist_datetime date component",
            ),
            CandidateEvidence(
                kind=CandidateEvidenceKind.RJ_NUMERIC_DISTANCE,
                value=distance,
                description="distance",
                provenance="local work numbers",
            ),
        ),
        context=(
            CandidateEvidence(
                kind=CandidateEvidenceKind.LOCAL_MAKER_DAY_GROUP_SIZE,
                value=group_size,
                polarity=CandidateEvidencePolarity.CONTEXT,
                description="same-day group",
                provenance="local known-work universe",
            ),
            CandidateEvidence(
                kind=CandidateEvidenceKind.LOCAL_KNOWN_MAKER_WORK_COUNT,
                value=5,
                polarity=CandidateEvidencePolarity.CONTEXT,
                description="maker count",
                provenance="local known-work universe",
            ),
        ),
    )


class InMemoryReviewStore:
    def __init__(self) -> None:
        self.events: list[ManualReviewEvent] = []

    def append(self, event: ManualReviewEvent) -> ManualReviewEvent:
        saved = event.model_copy(update={"id": len(self.events) + 1})
        self.events.append(saved)
        return saved

    def latest_for_pair(self, workno_a: str, workno_b: str) -> ManualReviewEvent | None:
        history = self.history_for_pair(workno_a, workno_b)
        return history[-1] if history else None

    def history_for_pair(self, workno_a: str, workno_b: str) -> tuple[ManualReviewEvent, ...]:
        pair = canonical_pair(workno_a, workno_b)
        return tuple(
            sorted(
                (item for item in self.events if (item.workno_a, item.workno_b) == pair),
                key=lambda item: (item.reviewed_at, item.id or 0),
            )
        )

    def list_reviews(self) -> tuple[ManualReviewEvent, ...]:
        return tuple(sorted(self.events, key=lambda item: (item.reviewed_at, item.id or 0)))

    def reviews_for_work(self, workno: str) -> tuple[ManualReviewEvent, ...]:
        return tuple(
            item
            for item in self.list_reviews()
            if workno in (item.workno_a, item.workno_b)
        )


def submit(
    service: ManualReviewService,
    outcome: CandidateReviewOutcome,
    *,
    candidate_value: CandidateRelation | None = None,
    **kwargs: Any,
) -> ManualReviewEvent:
    value = candidate_value or candidate()
    return service.submit_review(
        value.source_workno,
        value.target_workno,
        outcome,
        candidate=value,
        **kwargs,
    )


def test_service_captures_all_candidate_snapshot_fields_and_provenance() -> None:
    store = InMemoryReviewStore()
    saved = submit(
        ManualReviewService(store),
        CandidateReviewOutcome.RELATED,
        relation_type=ManualRelationType.BONUS_OF,
        subject_workno="RJ00000002",
        target_workno="RJ00000001",
    )

    snapshot = saved.evidence_snapshot
    assert snapshot.schema_version == 1
    assert snapshot.candidate_evaluated_at == WHEN
    assert snapshot.same_maker_id == "RG12345678"
    assert snapshot.same_maker_name is None
    assert snapshot.same_regist_date == "2026-08-30"
    assert snapshot.rj_numeric_distance == 4
    assert snapshot.local_maker_day_group_size == 2
    assert snapshot.local_known_maker_work_count == 5
    assert snapshot.supporting_evidence[0]["provenance"] == "local metadata"
    assert snapshot.context[0]["provenance"] == "local known-work universe"
    assert saved.provenance.value == "MANUAL_USER_REVIEW"


def test_later_candidate_evidence_gets_a_new_immutable_snapshot() -> None:
    store = InMemoryReviewStore()
    service = ManualReviewService(store)
    first = submit(
        service,
        CandidateReviewOutcome.UNSURE,
        candidate_value=candidate(distance=4, group_size=2),
    )
    second = submit(
        service,
        CandidateReviewOutcome.NOT_RELATED,
        candidate_value=candidate(distance=99, group_size=3),
    )

    assert first.evidence_snapshot.rj_numeric_distance == 4
    assert first.evidence_snapshot.local_maker_day_group_size == 2
    assert second.evidence_snapshot.rj_numeric_distance == 99
    assert second.evidence_snapshot.local_maker_day_group_size == 3
    assert first.evidence_snapshot != second.evidence_snapshot
    assert store.events[0].evidence_snapshot.rj_numeric_distance == 4


def test_no_review_is_distinct_from_an_unsure_review() -> None:
    store = InMemoryReviewStore()
    service = ManualReviewService(store)
    assert service.latest_review_for_pair("RJ00000001", "RJ00000002") is None

    saved = submit(service, CandidateReviewOutcome.UNSURE)
    assert saved.outcome is CandidateReviewOutcome.UNSURE
    assert service.latest_review_for_pair("RJ00000002", "RJ00000001") == saved


def test_manual_related_candidate_is_excluded_but_other_outcomes_remain_candidates() -> None:
    source = KnownWorkSnapshot(
        workno="RJ00000001",
        title="source",
        maker_id="RG12345678",
        regist_datetime=WHEN,
        source=CandidateSnapshotSource.CURRENT_CACHE,
    )
    target = KnownWorkSnapshot(
        workno="RJ00000002",
        title="target",
        maker_id="RG12345678",
        regist_datetime=WHEN,
        source=CandidateSnapshotSource.CURRENT_CACHE,
    )

    class KnownRepository:
        def list_known_work_summaries(self) -> tuple[KnownWorkSnapshot, ...]:
            return (source, target)

        def list_current_relation_pairs(self) -> tuple[tuple[str, str], ...]:
            return ()

    class ReviewLookup:
        def __init__(self, outcome: CandidateReviewOutcome) -> None:
            self.outcome = outcome

        def latest_review_for_pair(self, workno_a: str, workno_b: str) -> ManualReviewEvent:
            return ManualReviewEvent(
                workno_a=workno_a,
                workno_b=workno_b,
                outcome=self.outcome,
                relation_type=(
                    ManualRelationType.OTHER
                    if self.outcome is CandidateReviewOutcome.RELATED
                    else None
                ),
                evidence_snapshot=CandidateEvidenceSnapshot(),
                reviewed_at=WHEN,
            )

    related = CandidateRelationService(
        KnownRepository(),
        manual_reviews=ReviewLookup(CandidateReviewOutcome.RELATED),
    ).for_work(source.workno)
    not_related = CandidateRelationService(
        KnownRepository(),
        manual_reviews=ReviewLookup(CandidateReviewOutcome.NOT_RELATED),
    ).for_work(source.workno)
    unsure = CandidateRelationService(
        KnownRepository(),
        manual_reviews=ReviewLookup(CandidateReviewOutcome.UNSURE),
    ).for_work(source.workno)

    assert related.candidates == ()
    assert len(not_related.candidates) == 1
    assert len(unsure.candidates) == 1


def test_repository_failure_is_not_hidden_by_the_service() -> None:
    class FailingStore(InMemoryReviewStore):
        def append(self, event: ManualReviewEvent) -> ManualReviewEvent:
            raise RuntimeError("manual review write failed")

    with pytest.raises(RuntimeError, match="manual review write failed"):
        submit(ManualReviewService(FailingStore()), CandidateReviewOutcome.UNSURE)


def test_candidate_pair_mismatch_is_rejected_before_append() -> None:
    store = InMemoryReviewStore()
    service = ManualReviewService(store)
    with pytest.raises(ValueError, match="does not match"):
        service.submit_review(
            "RJ00000001",
            "RJ00000003",
            CandidateReviewOutcome.UNSURE,
            candidate=candidate(),
        )
    assert store.events == []
