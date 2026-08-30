from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from dlsite_organizer.domain.manual_review import (
    CandidateEvidenceSnapshot,
    CandidateReviewOutcome,
    ManualRelationType,
    ManualReviewEvent,
    canonical_pair,
)

WHEN = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
SNAPSHOT = CandidateEvidenceSnapshot(
    same_maker_id="RG12345678",
    same_regist_date="2026-08-30",
    rj_numeric_distance=4,
    local_maker_day_group_size=2,
    local_known_maker_work_count=5,
    candidate_evaluated_at=WHEN,
)


def test_canonical_pair_normalizes_and_rejects_self_pair() -> None:
    assert canonical_pair("rj00000002", "RJ00000001") == (
        "RJ00000001",
        "RJ00000002",
    )
    with pytest.raises(ValueError):
        canonical_pair("RJ00000001", "RJ00000001")


@pytest.mark.parametrize("relation_type", list(ManualRelationType))
def test_related_accepts_every_declared_relation_type(
    relation_type: ManualRelationType,
) -> None:
    kwargs: dict[str, Any] = {}
    if relation_type.is_directional:
        kwargs = {"subject_workno": "RJ00000002", "target_workno": "RJ00000001"}

    event = ManualReviewEvent(
        workno_a="RJ00000002",
        workno_b="RJ00000001",
        outcome=CandidateReviewOutcome.RELATED,
        relation_type=relation_type,
        evidence_snapshot=SNAPSHOT,
        reviewed_at=WHEN,
        **kwargs,
    )

    assert event.workno_a == "RJ00000001"
    assert event.workno_b == "RJ00000002"
    assert event.relation_type is relation_type


def test_direction_is_normalized_after_pair_canonicalization() -> None:
    event = ManualReviewEvent(
        workno_a="RJ00000002",
        workno_b="RJ00000001",
        outcome=CandidateReviewOutcome.RELATED,
        relation_type=ManualRelationType.BONUS_OF,
        subject_workno="rj00000002",
        target_workno="rj00000001",
        evidence_snapshot=SNAPSHOT,
        reviewed_at=WHEN,
    )

    assert event.subject_workno == "RJ00000002"
    assert event.target_workno == "RJ00000001"


@pytest.mark.parametrize(
    "outcome", [CandidateReviewOutcome.NOT_RELATED, CandidateReviewOutcome.UNSURE]
)
def test_non_related_outcomes_have_no_relation_or_direction(
    outcome: CandidateReviewOutcome,
) -> None:
    event = ManualReviewEvent(
        workno_a="RJ00000001",
        workno_b="RJ00000002",
        outcome=outcome,
        evidence_snapshot=SNAPSHOT,
        reviewed_at=WHEN,
    )

    assert event.relation_type is None
    assert event.subject_workno is None
    assert event.target_workno is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"outcome": CandidateReviewOutcome.RELATED},
        {
            "outcome": CandidateReviewOutcome.NOT_RELATED,
            "relation_type": ManualRelationType.BONUS_OF,
        },
        {
            "outcome": CandidateReviewOutcome.UNSURE,
            "subject_workno": "RJ00000001",
        },
        {
            "outcome": CandidateReviewOutcome.RELATED,
            "relation_type": ManualRelationType.BONUS_OF,
            "subject_workno": "RJ00000001",
        },
        {
            "outcome": CandidateReviewOutcome.RELATED,
            "relation_type": ManualRelationType.BUNDLED_WITH,
            "subject_workno": "RJ00000001",
            "target_workno": "RJ00000002",
        },
    ],
)
def test_invalid_outcome_relation_and_direction_combinations_are_rejected(
    kwargs: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        payload: dict[str, object] = {
            "workno_a": "RJ00000001",
            "workno_b": "RJ00000002",
            "evidence_snapshot": SNAPSHOT,
            "reviewed_at": WHEN,
            **kwargs,
        }
        ManualReviewEvent.model_validate(payload)


def test_directional_subject_and_target_must_be_distinct_pair_members() -> None:
    with pytest.raises(ValidationError):
        ManualReviewEvent(
            workno_a="RJ00000001",
            workno_b="RJ00000002",
            outcome=CandidateReviewOutcome.RELATED,
            relation_type=ManualRelationType.BONUS_OF,
            subject_workno="RJ00000001",
            target_workno="RJ00000001",
            evidence_snapshot=SNAPSHOT,
            reviewed_at=WHEN,
        )

    with pytest.raises(ValidationError):
        ManualReviewEvent(
            workno_a="RJ00000001",
            workno_b="RJ00000002",
            outcome=CandidateReviewOutcome.RELATED,
            relation_type=ManualRelationType.BONUS_OF,
            subject_workno="RJ00000003",
            target_workno="RJ00000002",
            evidence_snapshot=SNAPSHOT,
            reviewed_at=WHEN,
        )


def test_self_review_is_rejected_even_with_relation_type() -> None:
    with pytest.raises(ValidationError):
        ManualReviewEvent(
            workno_a="RJ00000001",
            workno_b="RJ00000001",
            outcome=CandidateReviewOutcome.RELATED,
            relation_type=ManualRelationType.OTHER,
            evidence_snapshot=SNAPSHOT,
            reviewed_at=WHEN,
        )
