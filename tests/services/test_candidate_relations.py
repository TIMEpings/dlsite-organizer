from datetime import UTC, datetime

from dlsite_organizer.domain.candidate import (
    CandidateEvidenceKind,
    CandidateSearchState,
    CandidateSnapshotSource,
    KnownWorkSnapshot,
)
from dlsite_organizer.services.candidate_relations import (
    CandidateEvidenceEvaluator,
    CandidateRelationService,
    normalize_maker_name,
    rj_numeric_distance,
)


def snapshot(code: str, maker_id: str | None = "M", maker_name: str | None = None):
    return KnownWorkSnapshot(
        workno=code,
        title=code,
        maker_id=maker_id,
        maker_name=maker_name,
        regist_datetime=datetime(2026, 1, 1, tzinfo=UTC),
        source=CandidateSnapshotSource.CURRENT_CACHE,
    )


class Repository:
    def __init__(self, *items):
        self.items = items

    def list_known_work_summaries(self):
        return self.items


def test_default_policy_finds_same_maker_and_date_with_explainable_evidence():
    result = CandidateRelationService(
        Repository(snapshot("RJ00000001"), snapshot("RJ00000002"), snapshot("RJ00000003", "OTHER"))
    ).for_work("RJ00000001")

    assert result.state is CandidateSearchState.FOUND
    assert [item.target_workno for item in result.candidates] == ["RJ00000002"]
    assert {item.kind for item in result.candidates[0].supporting_evidence} == {
        CandidateEvidenceKind.SAME_MAKER_ID,
        CandidateEvidenceKind.SAME_REGIST_DATE,
        CandidateEvidenceKind.RJ_NUMERIC_DISTANCE,
    }
    assert result.candidates[0].context[0].value == 2


def test_missing_maker_is_insufficient_metadata():
    result = CandidateRelationService(
        Repository(snapshot("RJ00000001", None))
    ).for_work("RJ00000001")
    assert result.state is CandidateSearchState.INSUFFICIENT_METADATA


def test_name_fallback_is_exact_and_conservative():
    left = snapshot("RJ00000001", None, " Circle A ")
    right = snapshot("RJ00000002", None, "Circle A")
    evidence = CandidateEvidenceEvaluator().evaluate(left, right)
    assert evidence[0].kind is CandidateEvidenceKind.SAME_MAKER_NAME
    assert normalize_maker_name(" Circle A ") == "Circle A"


def test_rj_distance_is_numeric():
    assert rj_numeric_distance("RJ01636949", "RJ01637033") == 84
