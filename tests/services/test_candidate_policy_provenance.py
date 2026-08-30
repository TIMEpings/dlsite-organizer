from datetime import UTC, datetime
from pathlib import Path

import pytest

from dlsite_organizer import __version__
from dlsite_organizer.domain.candidate import (
    CandidatePolicyProvenance,
    CandidateSnapshotSource,
    KnownWorkSnapshot,
)
from dlsite_organizer.domain.evaluation import EvaluationRecordState
from dlsite_organizer.domain.manual_review import (
    CandidateEvidenceSnapshot,
    CandidateReviewOutcome,
    ManualRelationType,
    ManualReviewEvent,
    parse_candidate_evidence_snapshot,
)
from dlsite_organizer.services.candidate_relations import (
    CandidateRelationService,
    CandidateSearchPolicy,
)
from dlsite_organizer.services.evaluation_dataset import EvaluationDatasetService
from dlsite_organizer.services.manual_reviews import snapshot_from_candidate

WHEN = datetime(2026, 8, 30, 10, tzinfo=UTC)
PROVENANCE = CandidatePolicyProvenance(
    policy_id="same-maker-same-date",
    policy_version=1,
    application_version=__version__,
)


class KnownWorkStore:
    def list_known_work_summaries(self) -> tuple[KnownWorkSnapshot, ...]:
        return tuple(
            KnownWorkSnapshot(
                workno=workno,
                title=workno,
                maker_id="M",
                regist_datetime=datetime(2026, 8, 30, tzinfo=UTC),
                source=CandidateSnapshotSource.CURRENT_CACHE,
            )
            for workno in ("RJ00000001", "RJ00000002")
        )


def review(
    event_id: int,
    left: str,
    right: str,
    outcome: CandidateReviewOutcome,
    snapshot: CandidateEvidenceSnapshot,
) -> ManualReviewEvent:
    return ManualReviewEvent(
        id=event_id,
        workno_a=left,
        workno_b=right,
        outcome=outcome,
        relation_type=(
            ManualRelationType.OTHER if outcome is CandidateReviewOutcome.RELATED else None
        ),
        evidence_snapshot=snapshot,
        reviewed_at=WHEN,
    )


def test_default_policy_descriptor_and_search_result_are_provenanced() -> None:
    policy = CandidateSearchPolicy()
    result = CandidateRelationService(
        KnownWorkStore(), clock=lambda: WHEN
    ).for_work("RJ00000001")

    assert policy.policy_descriptor.policy_id == "same-maker-same-date"
    assert policy.policy_descriptor.policy_version == 1
    assert result.policy_provenance == PROVENANCE
    assert result.candidates[0].policy_provenance == PROVENANCE
    snapshot = snapshot_from_candidate(result.candidates[0])
    assert snapshot.schema_version == 2
    assert snapshot.policy_provenance == PROVENANCE


def test_v1_is_read_without_inventing_provenance_and_v2_is_strict() -> None:
    legacy = parse_candidate_evidence_snapshot('{"schema_version": 1}')
    assert legacy.policy_provenance is None
    with pytest.raises(ValueError):
        parse_candidate_evidence_snapshot('{"schema_version": 2}')
    with pytest.raises(ValueError):
        parse_candidate_evidence_snapshot('{"schema_version": 999}')


def test_mixed_latest_dataset_reports_legacy_and_policy_distribution(tmp_path: Path) -> None:
    legacy = CandidateEvidenceSnapshot(schema_version=1)
    current = CandidateEvidenceSnapshot(schema_version=2, policy_provenance=PROVENANCE)

    class Store:
        def list_reviews(self) -> tuple[ManualReviewEvent, ...]:
            return (
                review(1, "RJ00000001", "RJ00000002", CandidateReviewOutcome.RELATED, legacy),
                review(2, "RJ00000001", "RJ00000003", CandidateReviewOutcome.NOT_RELATED, current),
                review(3, "RJ00000001", "RJ00000004", CandidateReviewOutcome.UNSURE, current),
            )

    service = EvaluationDatasetService(Store())
    records = service.records()
    summary = service.summary()
    assert len(records) == 3
    assert records[0].state is EvaluationRecordState.VALID
    assert records[0].candidate_policy_id is None
    assert records[1].candidate_policy_id == "same-maker-same-date"
    assert records[1].candidate_policy_version == 1
    assert records[1].application_version == __version__
    assert summary.legacy_snapshot_count == 1
    assert summary.policy_provenance_available_count == 2
    assert [
        (item.policy_id, item.policy_version, item.record_count)
        for item in summary.policy_distribution
    ] == [
        ("LEGACY_UNKNOWN_POLICY", None, 1),
        ("same-maker-same-date", 1, 2),
    ]

    path = tmp_path / "evaluation.csv"
    service.export_csv(path)
    header = path.read_text(encoding="utf-8-sig").splitlines()[0].split(",")
    assert header[
        header.index("snapshot_schema_version") + 1 : header.index("candidate_evaluated_at")
    ] == [
        "candidate_policy_id",
        "candidate_policy_version",
        "application_version",
    ]
