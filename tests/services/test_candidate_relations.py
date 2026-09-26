from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic import ValidationError

from dlsite_organizer.domain.candidate import (
    CandidateEvidenceKind,
    CandidateEvidencePolarity,
    CandidateRelation,
    CandidateSearchState,
    CandidateSnapshotSource,
    CandidateType,
    KnownWorkSnapshot,
)
from dlsite_organizer.providers.dlsite.sources import parse_product_info_ajax
from dlsite_organizer.services.candidate_relations import (
    CandidateEvidenceEvaluator,
    CandidateRelationService,
    CandidateSearchPolicy,
    normalize_maker_name,
    rj_numeric_distance,
)
from dlsite_organizer.services.historical_relations import HistoricalRelationService
from dlsite_organizer.services.translation_relations import TranslationRelationService

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "dlsite"
EVALUATED_AT = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)


def snapshot(
    code: str,
    maker_id: str | None = "M",
    maker_name: str | None = None,
    *,
    day: int = 1,
    source: CandidateSnapshotSource = CandidateSnapshotSource.CURRENT_CACHE,
    observed_at: datetime | None = None,
    fetched_at: datetime | None = None,
) -> KnownWorkSnapshot:
    return KnownWorkSnapshot(
        workno=code,
        title=code,
        maker_id=maker_id,
        maker_name=maker_name,
        regist_datetime=datetime(2026, 1, day, tzinfo=UTC),
        source=source,
        observed_at=observed_at,
        fetched_at=fetched_at,
    )


class Repository:
    def __init__(self, *items: KnownWorkSnapshot, current_pairs=()) -> None:
        self.items = tuple(items)
        self.current_pairs = tuple(current_pairs)
        self.known_calls = 0
        self.current_calls = 0

    def list_known_work_summaries(self) -> tuple[KnownWorkSnapshot, ...]:
        self.known_calls += 1
        return self.items

    def list_current_relation_pairs(self) -> tuple[tuple[str, str], ...]:
        self.current_calls += 1
        return self.current_pairs


class HistoricalProbe:
    def __init__(self, subject: str, target: str) -> None:
        self.subject = subject
        self.target = target
        self.calls = 0

    def for_work(self, workno: str):
        self.calls += 1
        relation = SimpleNamespace(subject_workno=self.subject, target_workno=self.target)
        return SimpleNamespace(outgoing=(relation,), incoming=())


def fixed_service(repository: Repository, **kwargs) -> CandidateRelationService:
    return CandidateRelationService(repository, clock=lambda: EVALUATED_AT, **kwargs)


def test_candidate_public_contract_is_derived_generic_and_not_confirmed() -> None:
    candidate = CandidateRelation(
        source_workno="RJ00000001",
        target_workno="RJ00000002",
        evaluated_at=EVALUATED_AT,
    )

    assert candidate.candidate_type is CandidateType.RELATED_WORK_CANDIDATE
    assert not hasattr(candidate, "confidence")
    assert {"confidence", "score", "probability", "confidence_percent"}.isdisjoint(
        CandidateRelation.model_fields
    )
    with pytest.raises(ValidationError):
        CandidateRelation.model_validate(
            {
                "source_workno": "RJ00000001",
                "target_workno": "RJ00000002",
                "evaluated_at": EVALUATED_AT,
                "confidence": "confirmed",
            }
        )


def test_search_policy_and_evidence_evaluator_have_independent_responsibilities() -> None:
    left = snapshot("RJ00000001", day=1)
    right = snapshot("RJ00000002", day=2)

    evidence = CandidateEvidenceEvaluator().evaluate(left, right)

    assert not CandidateSearchPolicy().eligible(left, right)
    assert {item.kind for item in evidence} == {
        CandidateEvidenceKind.SAME_MAKER_ID,
        CandidateEvidenceKind.RJ_NUMERIC_DISTANCE,
    }


def test_default_policy_finds_same_maker_and_date_with_explainable_evidence() -> None:
    result = fixed_service(
        Repository(
            snapshot("RJ00000001"),
            snapshot("RJ00000002"),
            snapshot("RJ00000003", "OTHER"),
        )
    ).for_work("RJ00000001")

    assert result.state is CandidateSearchState.FOUND
    assert [item.target_workno for item in result.candidates] == ["RJ00000002"]
    candidate = result.candidates[0]
    assert candidate.candidate_type is CandidateType.RELATED_WORK_CANDIDATE
    assert {item.kind for item in candidate.supporting_evidence} == {
        CandidateEvidenceKind.SAME_MAKER_ID,
        CandidateEvidenceKind.SAME_REGIST_DATE,
        CandidateEvidenceKind.RJ_NUMERIC_DISTANCE,
    }
    assert candidate.context[0].value == 2
    assert all("bonus" not in item.kind.value for item in candidate.supporting_evidence)


def test_candidate_policy_does_not_require_title_similarity() -> None:
    source = snapshot("RJ00000001").model_copy(update={"title": "A Quiet Harbor"})
    target = snapshot("RJ00000002").model_copy(update={"title": "Nine Objects in Orbit"})

    result = fixed_service(Repository(source, target)).for_work(source.workno)

    assert result.state is CandidateSearchState.FOUND
    assert [item.target_workno for item in result.candidates] == [target.workno]


def test_different_maker_near_rj_does_not_become_candidate() -> None:
    result = fixed_service(
        Repository(snapshot("RJ00000001", "M"), snapshot("RJ00000002", "OTHER"))
    ).for_work("RJ00000001")

    assert result.state is CandidateSearchState.NONE
    assert not result.candidates


def test_maker_ids_take_precedence_over_equal_names() -> None:
    left = snapshot("RJ00000001", "M1", "Circle A")
    right = snapshot("RJ00000002", "M2", "Circle A")

    assert not CandidateSearchPolicy().eligible(left, right)
    assert CandidateEvidenceKind.SAME_MAKER_ID not in {
        item.kind for item in CandidateEvidenceEvaluator().evaluate(left, right)
    }
    assert CandidateEvidenceKind.SAME_MAKER_NAME not in {
        item.kind for item in CandidateEvidenceEvaluator().evaluate(left, right)
    }


def test_name_fallback_is_trimmed_nfkc_and_is_not_id_evidence() -> None:
    left = snapshot("RJ00000001", None, " Circle Ａ ")
    right = snapshot("RJ00000002", None, "Circle A")
    evidence = CandidateEvidenceEvaluator().evaluate(left, right)

    assert normalize_maker_name(" Circle Ａ ") == "Circle A"
    assert evidence[0].kind is CandidateEvidenceKind.SAME_MAKER_NAME
    assert CandidateEvidenceKind.SAME_MAKER_ID not in {item.kind for item in evidence}
    assert CandidateSearchPolicy().eligible(left, right)


def test_mixed_maker_id_and_name_does_not_infer_identity() -> None:
    result = fixed_service(
        Repository(
            snapshot("RJ00000001", "M", "Circle A"),
            snapshot("RJ00000002", None, "Circle A"),
        )
    ).for_work("RJ00000001")

    assert result.state is CandidateSearchState.NONE


def test_missing_source_maker_is_insufficient_metadata() -> None:
    result = fixed_service(Repository(snapshot("RJ00000001", None))).for_work("RJ00000001")

    assert result.state is CandidateSearchState.INSUFFICIENT_METADATA
    assert result.state is not CandidateSearchState.NONE


def test_none_is_distinct_when_source_metadata_is_sufficient_but_no_target_matches() -> None:
    result = fixed_service(
        Repository(snapshot("RJ00000001"), snapshot("RJ00000002", "OTHER"))
    ).for_work("RJ00000001")

    assert result.state is CandidateSearchState.NONE
    assert result.reason is None


def test_current_confirmed_pair_is_excluded_from_candidates() -> None:
    result = fixed_service(
        Repository(
            snapshot("RJ00000001"),
            snapshot("RJ00000002"),
            current_pairs=(("RJ00000001", "RJ00000002"),),
        )
    ).for_work("RJ00000001")

    assert not result.candidates


def test_current_confirmed_pair_exclusion_is_canonical_for_reverse_lookup() -> None:
    result = fixed_service(
        Repository(
            snapshot("RJ00000001"),
            snapshot("RJ00000002"),
            current_pairs=(("RJ00000001", "RJ00000002"),),
        )
    ).for_work("RJ00000002")

    assert not result.candidates


def test_historical_confirmed_pair_is_excluded_from_candidates() -> None:
    history = HistoricalProbe("RJ00000001", "RJ00000002")
    result = fixed_service(
        Repository(snapshot("RJ00000001"), snapshot("RJ00000002")),
        historical_relations=history,
    ).for_work("RJ00000001")

    assert not result.candidates
    assert history.calls == 1


def test_fixture_translation_relation_has_priority_over_candidate_discovery() -> None:
    source = parse_product_info_ajax(
        (FIXTURE_DIR / "product_info_RJ01636949.json").read_text(encoding="utf-8"),
        "RJ01636949",
    )
    info = source.translation_info
    assert info is not None
    confirmed = TranslationRelationService().analyze("RJ01636949", info).relations
    target = "RJ01637033"
    assert any(relation.target_workno == target for relation in confirmed)

    result = fixed_service(
        Repository(
            snapshot("RJ01636949", "RG60289"),
            snapshot(target, "RG60289", day=1),
            current_pairs=tuple(
                (relation.source_workno, relation.target_workno) for relation in confirmed
            ),
        )
    ).for_work("RJ01636949")

    assert result.candidates == ()


def test_historical_pair_exclusion_is_symmetric_for_reverse_lookup() -> None:
    from dlsite_organizer.providers.dlsite.sources import TranslationInfoSource

    info = TranslationInfoSource(is_parent=True, original_workno="RJ00000002")
    observation = SimpleNamespace(
        id=1,
        workno="RJ00000001",
        source="fixture",
        translation_json=info.model_dump_json(),
        observed_at=EVALUATED_AT,
    )
    history_store = SimpleNamespace(list_all_observations=lambda: (observation,))
    history = HistoricalRelationService(cast(Any, history_store))
    result = fixed_service(
        Repository(snapshot("RJ00000001"), snapshot("RJ00000002")),
        historical_relations=history,
    ).for_work("RJ00000002")

    assert result.candidates == ()


def test_candidate_evidence_is_canonical_and_symmetric() -> None:
    left = snapshot("RJ00000001")
    right = snapshot("RJ00000002")
    forward = fixed_service(Repository(left, right)).for_work(left.workno).candidates[0]
    reverse = fixed_service(Repository(left, right)).for_work(right.workno).candidates[0]

    assert forward.candidate_type is reverse.candidate_type
    assert [forward.target_workno, reverse.target_workno] == [right.workno, left.workno]
    def facts(candidate: CandidateRelation) -> tuple:
        return tuple((e.kind, e.value, e.polarity) for e in candidate.supporting_evidence)

    assert facts(forward) == facts(reverse)


def test_rj_numeric_distance_is_numeric_and_symmetric() -> None:
    left = "RJ01636949"
    right = "RJ01637033"
    assert rj_numeric_distance(left, right) == 84
    assert rj_numeric_distance(left, right) == rj_numeric_distance(right, left)


def test_evaluated_at_is_evaluation_time_not_snapshot_observation_time() -> None:
    observed_at = datetime(2020, 1, 1, tzinfo=UTC)
    result = fixed_service(
        Repository(
            snapshot("RJ00000001", observed_at=observed_at),
            snapshot("RJ00000002", observed_at=observed_at),
        )
    ).for_work("RJ00000001")

    assert result.evaluated_at == EVALUATED_AT
    assert result.candidates[0].evaluated_at == EVALUATED_AT
    assert result.candidates[0].evaluated_at != observed_at


def test_known_work_repository_is_read_once_and_history_is_not_scanned_per_candidate() -> None:
    repository = Repository(
        snapshot("RJ00000001"),
        snapshot("RJ00000002"),
        snapshot("RJ00000003"),
    )
    history = HistoricalProbe("RJ10000000", "RJ10000001")

    fixed_service(repository, historical_relations=history).for_work("RJ00000001")

    assert repository.known_calls == 1
    assert history.calls == 1


def test_default_policy_does_not_apply_an_rj_distance_cutoff() -> None:
    result = fixed_service(
        Repository(snapshot("RJ00000001"), snapshot("RJ00001001"))
    ).for_work("RJ00000001")

    assert [candidate.target_workno for candidate in result.candidates] == ["RJ00001001"]
    assert next(
        evidence.value
        for evidence in result.candidates[0].supporting_evidence
        if evidence.kind is CandidateEvidenceKind.RJ_NUMERIC_DISTANCE
    ) == 1000


def test_context_counts_include_source_and_all_same_maker_day_works() -> None:
    items = [snapshot(f"RJ0000000{n}") for n in range(1, 6)]
    results = fixed_service(Repository(*items)).for_work("RJ00000001")

    assert len(results.candidates) == 4
    for candidate in results.candidates:
        context = {item.kind: item for item in candidate.context}
        assert context[CandidateEvidenceKind.LOCAL_MAKER_DAY_GROUP_SIZE].value == 5
        assert context[CandidateEvidenceKind.LOCAL_KNOWN_MAKER_WORK_COUNT].value == 5
        assert all(item.polarity is CandidateEvidencePolarity.CONTEXT for item in candidate.context)


def test_context_is_not_a_score_or_confidence_field() -> None:
    result = fixed_service(
        Repository(snapshot("RJ00000001"), snapshot("RJ00000002"))
    ).for_work("RJ00000001")
    context_kinds = {item.kind for item in result.candidates[0].context}

    assert context_kinds == {
        CandidateEvidenceKind.LOCAL_MAKER_DAY_GROUP_SIZE,
        CandidateEvidenceKind.LOCAL_KNOWN_MAKER_WORK_COUNT,
    }
    assert not hasattr(result.candidates[0], "score")
    assert not hasattr(result.candidates[0], "probability")


def test_candidates_are_sorted_by_rj_distance_then_workno() -> None:
    result = fixed_service(
        Repository(
            snapshot("RJ00000010"),
            snapshot("RJ00000020"),
            snapshot("RJ00000012"),
            snapshot("RJ00000015"),
        )
    ).for_work("RJ00000010")

    assert [item.target_workno for item in result.candidates] == [
        "RJ00000012",
        "RJ00000015",
        "RJ00000020",
    ]


def test_equal_distance_candidates_have_stable_workno_tie_breaking() -> None:
    result = fixed_service(
        Repository(
            snapshot("RJ00000010"),
            snapshot("RJ00000008"),
            snapshot("RJ00000012"),
        )
    ).for_work("RJ00000010")

    assert [item.target_workno for item in result.candidates] == [
        "RJ00000008",
        "RJ00000012",
    ]


def test_max_results_reports_total_before_truncation() -> None:
    result = fixed_service(
        Repository(
            snapshot("RJ00000001"),
            snapshot("RJ00000002"),
            snapshot("RJ00000003"),
            snapshot("RJ00000004"),
        ),
        max_results=2,
    ).for_work("RJ00000001")

    assert len(result.candidates) == 2
    assert result.truncated is True
    assert result.total_candidate_count == 3


def test_result_is_not_truncated_when_all_candidates_fit() -> None:
    result = fixed_service(
        Repository(snapshot("RJ00000001"), snapshot("RJ00000002")), max_results=2
    ).for_work("RJ00000001")

    assert result.truncated is False
    assert result.total_candidate_count == len(result.candidates) == 1
