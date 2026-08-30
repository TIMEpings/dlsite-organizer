from datetime import UTC, datetime
from pathlib import Path

from dlsite_organizer.domain.evaluation import (
    EvaluationMetricState,
    EvaluationRecordState,
    InvalidSnapshotReview,
)
from dlsite_organizer.domain.manual_review import (
    CandidateEvidenceSnapshot,
    CandidateReviewOutcome,
    ManualRelationType,
    ManualReviewEvent,
)
from dlsite_organizer.services.evaluation_dataset import EvaluationDatasetService

WHEN = datetime(2026, 8, 30, 10, tzinfo=UTC)
SNAPSHOT = CandidateEvidenceSnapshot(
    schema_version=1,
    same_maker_id="M1",
    same_maker_name="Circle 日本語",
    same_regist_date="2026-08-30",
    rj_numeric_distance=4,
    local_maker_day_group_size=3,
    local_known_maker_work_count=5,
    candidate_evaluated_at=WHEN,
)


class Store:
    def __init__(self, *events: ManualReviewEvent) -> None:
        self.events = events

    def list_reviews(self) -> tuple[ManualReviewEvent, ...]:
        return self.events


class EvaluationStore(Store):
    def __init__(self, *items: ManualReviewEvent | InvalidSnapshotReview) -> None:
        self.items = items

    def list_reviews_for_evaluation(self) -> tuple[ManualReviewEvent | InvalidSnapshotReview, ...]:
        return self.items


def review(
    event_id: int,
    a: str,
    b: str,
    outcome: CandidateReviewOutcome,
    reviewed_at: datetime,
    *,
    relation_type: ManualRelationType | None = None,
    subject: str | None = None,
    target: str | None = None,
) -> ManualReviewEvent:
    if outcome is CandidateReviewOutcome.RELATED and relation_type is None:
        relation_type = ManualRelationType.OTHER
        subject = None
        target = None
    return ManualReviewEvent(
        id=event_id,
        workno_a=a,
        workno_b=b,
        outcome=outcome,
        relation_type=relation_type,
        subject_workno=subject,
        target_workno=target,
        evidence_snapshot=SNAPSHOT,
        reviewed_at=reviewed_at,
    )


def test_latest_record_per_pair_preserves_history_and_direction() -> None:
    service = EvaluationDatasetService(
        Store(
            review(1, "RJ00000001", "RJ00000002", CandidateReviewOutcome.UNSURE, WHEN),
            review(
                2,
                "RJ00000002",
                "RJ00000001",
                CandidateReviewOutcome.RELATED,
                WHEN,
                relation_type=ManualRelationType.BONUS_OF,
                subject="RJ00000002",
                target="RJ00000001",
            ),
            review(3, "RJ00000001", "RJ00000003", CandidateReviewOutcome.NOT_RELATED, WHEN),
            review(4, "RJ00000001", "RJ00000004", CandidateReviewOutcome.UNSURE, WHEN),
        )
    )

    records = service.build_latest_review_dataset()
    assert len(records) == 3
    assert [(r.workno_a, r.workno_b, r.outcome) for r in records] == [
        ("RJ00000001", "RJ00000002", CandidateReviewOutcome.RELATED),
        ("RJ00000001", "RJ00000003", CandidateReviewOutcome.NOT_RELATED),
        ("RJ00000001", "RJ00000004", CandidateReviewOutcome.UNSURE),
    ]
    assert records[0].subject_workno == "RJ00000002"
    assert records[0].target_workno == "RJ00000001"
    assert len(service._repository.list_reviews()) == 4


def test_summary_excludes_unsure_and_guards_small_samples() -> None:
    events = [
        review(i, f"RJ{i:08d}", f"RJ{i + 100:08d}", outcome, WHEN)
        for i, outcome in enumerate(
            [
                CandidateReviewOutcome.RELATED,
                CandidateReviewOutcome.RELATED,
                CandidateReviewOutcome.RELATED,
                CandidateReviewOutcome.RELATED,
                CandidateReviewOutcome.NOT_RELATED,
                CandidateReviewOutcome.NOT_RELATED,
                *([CandidateReviewOutcome.UNSURE] * 10),
            ],
            start=1,
        )
    ]
    summary = EvaluationDatasetService(Store(*events)).summary()
    assert summary.decided_pair_count == 6
    assert summary.manual_related_rate == 4 / 6
    assert summary.latest_unsure_count == 10
    assert summary.metric_state is EvaluationMetricState.INSUFFICIENT_LABELS
    assert summary.unsure_share == 10 / 16


def test_zero_decided_is_safe() -> None:
    summary = EvaluationDatasetService(
        Store(review(1, "RJ00000001", "RJ00000002", CandidateReviewOutcome.UNSURE, WHEN))
    ).summary()
    assert summary.decided_pair_count == 0
    assert summary.manual_related_rate is None
    assert summary.metric_state is EvaluationMetricState.INSUFFICIENT_LABELS


def test_latest_invalid_snapshot_does_not_fall_back_and_label_still_counts() -> None:
    older = review(
        1,
        "RJ00000001",
        "RJ00000002",
        CandidateReviewOutcome.RELATED,
        WHEN,
        relation_type=ManualRelationType.OTHER,
    )
    latest = InvalidSnapshotReview(
        id=2,
        workno_a="RJ00000001",
        workno_b="RJ00000002",
        outcome=CandidateReviewOutcome.NOT_RELATED,
        relation_type=None,
        subject_workno=None,
        target_workno=None,
        reviewed_at=WHEN.replace(hour=11),
        provenance=older.provenance,
        snapshot_error="invalid JSON",
        snapshot_schema_version=1,
    )
    service = EvaluationDatasetService(EvaluationStore(older, latest))
    record = service.records()[0]
    summary = service.summary()
    assert record.outcome is CandidateReviewOutcome.NOT_RELATED
    assert record.state is EvaluationRecordState.INVALID_SNAPSHOT
    assert record.evidence_snapshot is None
    assert summary.latest_related_count == 0
    assert summary.latest_not_related_count == 1
    assert summary.invalid_snapshot_count == 1
    assert summary.valid_snapshot_count == 0
    assert summary.evidence_groups == ()


def test_relation_counts_bonus_subset_and_evidence_groups() -> None:
    events = [
        review(
            i,
            f"RJ{i:08d}",
            f"RJ{i + 100:08d}",
            CandidateReviewOutcome.RELATED,
            WHEN,
            relation_type=relation,
            subject=f"RJ{i:08d}" if relation.is_directional else None,
            target=f"RJ{i + 100:08d}" if relation.is_directional else None,
        )
        for i, relation in enumerate(
            [
                ManualRelationType.BONUS_OF,
                ManualRelationType.LIMITED_BONUS_OF,
                ManualRelationType.UNKNOWN,
            ],
            start=1,
        )
    ]
    summary = EvaluationDatasetService(Store(*events), minimum_decided_labels=1).summary()
    assert summary.manual_bonus_related_pairs == 2
    assert {item.relation_type for item in summary.relation_type_counts} == {
        ManualRelationType.BONUS_OF,
        ManualRelationType.LIMITED_BONUS_OF,
        ManualRelationType.UNKNOWN,
    }
    assert summary.metric_state is EvaluationMetricState.SUFFICIENT_LABELS
    assert any(
        group.group_key == "SAME_MAKER_ID + SAME_DATE + RJ<=5"
        for group in summary.evidence_groups
    )


def test_csv_is_deterministic_utf8_bom_and_excludes_notes(tmp_path: Path) -> None:
    event = review(1, "RJ00000002", "RJ00000001", CandidateReviewOutcome.UNSURE, WHEN)
    event = event.model_copy(update={"notes": "private note"})
    service = EvaluationDatasetService(Store(event))
    first = tmp_path / "one.csv"
    second = tmp_path / "two.csv"
    service.export_csv(first)
    service.export_csv(second)
    assert first.read_bytes() == second.read_bytes()
    content = first.read_text(encoding="utf-8-sig")
    assert "notes" not in content
    assert "private note" not in content
    assert content.splitlines()[1].startswith("RJ00000001,RJ00000002")
    assert first.read_bytes().startswith(b"\xef\xbb\xbf")
