"""Application service for validating and recording manual candidate labels."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol, cast

from dlsite_organizer.domain.candidate import CandidateRelation
from dlsite_organizer.domain.manual_review import (
    CandidateEvidenceSnapshot,
    CandidateReviewOutcome,
    ManualRelationType,
    ManualReviewEvent,
    canonical_pair,
)


class ManualReviewStore(Protocol):
    def append(self, event: ManualReviewEvent) -> ManualReviewEvent: ...
    def latest_for_pair(self, workno_a: str, workno_b: str) -> ManualReviewEvent | None: ...
    def history_for_pair(self, workno_a: str, workno_b: str) -> tuple[ManualReviewEvent, ...]: ...
    def list_reviews(self) -> tuple[ManualReviewEvent, ...]: ...


class ManualReviewService:
    def __init__(self, repository: ManualReviewStore, *, clock=None) -> None:
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))

    def submit_review(
        self,
        workno_a: str,
        workno_b: str,
        outcome: CandidateReviewOutcome,
        *,
        relation_type: ManualRelationType | None = None,
        subject_workno: str | None = None,
        target_workno: str | None = None,
        notes: str | None = None,
        evidence_snapshot: CandidateEvidenceSnapshot | dict | None = None,
        candidate: CandidateRelation | None = None,
        reviewed_at: datetime | None = None,
    ) -> ManualReviewEvent:
        """Validate and atomically append a review event.

        Candidate reviews should pass ``candidate`` (or an explicit snapshot).
        The service never catches repository failures: a failed commit is a
        failed user action and must be surfaced by the UI.
        """
        outcome = CandidateReviewOutcome(outcome)
        pair = canonical_pair(workno_a, workno_b)
        if candidate is not None:
            candidate_pair = canonical_pair(candidate.source_workno, candidate.target_workno)
            if candidate_pair != pair:
                raise ValueError("Candidate does not match reviewed pair")
            if evidence_snapshot is None:
                evidence_snapshot = snapshot_from_candidate(candidate)
        if evidence_snapshot is None:
            raise ValueError("evidence_snapshot is required for a manual review")
        snapshot = (
            evidence_snapshot
            if isinstance(evidence_snapshot, CandidateEvidenceSnapshot)
            else CandidateEvidenceSnapshot.model_validate(evidence_snapshot)
        )
        now = reviewed_at or self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        event = ManualReviewEvent(
            workno_a=pair[0],
            workno_b=pair[1],
            outcome=outcome,
            relation_type=relation_type,
            subject_workno=subject_workno,
            target_workno=target_workno,
            notes=notes,
            evidence_snapshot=snapshot,
            reviewed_at=now.astimezone(UTC),
        )
        return self._repository.append(event)

    def latest_review_for_pair(self, workno_a: str, workno_b: str) -> ManualReviewEvent | None:
        return self._repository.latest_for_pair(workno_a, workno_b)

    latest_for_pair = latest_review_for_pair

    def history_for_pair(self, workno_a: str, workno_b: str) -> tuple[ManualReviewEvent, ...]:
        return self._repository.history_for_pair(workno_a, workno_b)

    def reviews_for_work(self, workno: str) -> tuple[ManualReviewEvent, ...]:
        getter = getattr(self._repository, "reviews_for_work", None)
        if callable(getter):
            return cast(tuple[ManualReviewEvent, ...], getter(workno))
        return tuple(
            event
            for event in self._repository.list_reviews()
            if workno in (event.workno_a, event.workno_b)
        )

    def list_manual_reviews(self) -> tuple[ManualReviewEvent, ...]:
        return self._repository.list_reviews()


def snapshot_from_candidate(candidate: CandidateRelation) -> CandidateEvidenceSnapshot:
    values = {item.kind.value: item.value for item in candidate.supporting_evidence}
    context = {item.kind.value: item.value for item in candidate.context}
    return CandidateEvidenceSnapshot(
        same_maker_id=values.get("same_maker_id"),
        same_maker_name=values.get("same_maker_name"),
        same_regist_date=values.get("same_regist_date"),
        rj_numeric_distance=values.get("rj_numeric_distance"),
        local_maker_day_group_size=context.get("local_maker_day_group_size"),
        local_known_maker_work_count=context.get("local_known_maker_work_count"),
        candidate_evaluated_at=candidate.evaluated_at,
        supporting_evidence=tuple(
            item.model_dump(mode="json") for item in candidate.supporting_evidence
        ),
        context=tuple(item.model_dump(mode="json") for item in candidate.context),
    )
