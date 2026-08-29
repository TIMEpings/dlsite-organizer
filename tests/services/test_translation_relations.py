from dataclasses import dataclass
from pathlib import Path

import pytest
from pydantic import ValidationError

from dlsite_organizer.domain.relation import RelationType, TranslationRole
from dlsite_organizer.providers.dlsite.client import DlsiteWorkLookup
from dlsite_organizer.providers.dlsite.sources import (
    ProductInfoAjaxSource,
    TranslationInfoSource,
    normalize_product_info_ajax,
    parse_product_info_ajax,
)
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.translation_relations import (
    TranslationAnalysisStatus,
    TranslationRelationService,
)

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "dlsite"


def fixture_source(workno: str) -> ProductInfoAjaxSource:
    payload = (FIXTURE_DIR / f"product_info_{workno}.json").read_text(encoding="utf-8")
    return parse_product_info_ajax(payload, workno)


@dataclass
class FixtureProvider:
    source: ProductInfoAjaxSource

    def fetch_work_lookup(self, workno: str) -> DlsiteWorkLookup:
        return DlsiteWorkLookup(
            work=normalize_product_info_ajax(self.source, section="maniax"),
            product_info=self.source,
        )


def test_lookup_result_maps_real_original_without_inventing_parent() -> None:
    source = fixture_source("RJ01609020")
    result = LookupService(FixtureProvider(source), NamingService()).lookup("RJ01609020")

    assert result.translation_role is TranslationRole.ORIGINAL
    assert result.relations == ()
    assert result.translation.status is TranslationAnalysisStatus.CONFIRMED


def test_lookup_result_maps_real_parent_with_directional_confirmed_edges() -> None:
    source = fixture_source("RJ01636949")
    result = LookupService(FixtureProvider(source), NamingService()).lookup("RJ01636949")

    assert result.translation_role is TranslationRole.TRANSLATION_PARENT
    assert {(edge.target_workno, edge.relation_type) for edge in result.relations} == {
        ("RJ01609020", RelationType.TRANSLATION_OF),
        ("RJ01637033", RelationType.HAS_TRANSLATION_CHILD),
        ("RJ01636950", RelationType.HAS_TRANSLATION_CHILD),
        ("RJ01663275", RelationType.HAS_TRANSLATION_CHILD),
    }
    assert all(edge.source_workno == "RJ01636949" for edge in result.relations)
    assert all(edge.confidence.value == "confirmed" for edge in result.relations)
    assert all(
        edge.evidence[0].attributes["field"].startswith("translation_info.")
        for edge in result.relations
    )


def test_lookup_result_maps_real_child_to_parent_and_original() -> None:
    source = fixture_source("RJ01637033")
    result = LookupService(FixtureProvider(source), NamingService()).lookup("RJ01637033")

    assert result.translation_role is TranslationRole.TRANSLATION_CHILD
    assert {(edge.target_workno, edge.relation_type) for edge in result.relations} == {
        ("RJ01636949", RelationType.CHILD_OF_TRANSLATION),
        ("RJ01609020", RelationType.TRANSLATION_OF),
    }
    assert all(edge.source_workno == "RJ01637033" for edge in result.relations)


def test_missing_translation_info_is_distinct_from_no_translation_claim() -> None:
    result = TranslationRelationService().analyze("RJ01609020", None)

    assert result.status is TranslationAnalysisStatus.NO_INFORMATION
    assert result.role is None
    assert result.relations == ()


def test_all_false_flags_do_not_infer_a_role_or_edges() -> None:
    info = TranslationInfoSource(
        is_original=False,
        is_parent=False,
        is_child=False,
        original_workno="RJ01609020",
        parent_workno="RJ01636949",
    )

    result = TranslationRelationService().analyze("RJ01637033", info)

    assert result.status is TranslationAnalysisStatus.NO_INFORMATION
    assert result.role is None
    assert result.relations == ()


def test_contradictory_role_flags_are_invalid_without_picking_a_role() -> None:
    info = TranslationInfoSource(is_parent=True, is_child=True)

    result = TranslationRelationService().analyze("RJ01636949", info)

    assert result.status is TranslationAnalysisStatus.INVALID
    assert result.role is None
    assert result.relations == ()
    assert result.contract_issue is not None


def test_parent_missing_original_degrades_without_an_imaginary_target() -> None:
    info = TranslationInfoSource(is_parent=True, child_worknos=["RJ01637033"])

    result = TranslationRelationService().analyze("RJ01636949", info)

    assert result.status is TranslationAnalysisStatus.INCOMPLETE
    assert result.role is TranslationRole.TRANSLATION_PARENT
    assert [(edge.target_workno, edge.relation_type) for edge in result.relations] == [
        ("RJ01637033", RelationType.HAS_TRANSLATION_CHILD)
    ]


def test_child_missing_parent_keeps_only_the_explicit_original_edge() -> None:
    info = TranslationInfoSource(is_child=True, original_workno="RJ01609020")

    result = TranslationRelationService().analyze("RJ01637033", info)

    assert result.status is TranslationAnalysisStatus.INCOMPLETE
    assert [(edge.target_workno, edge.relation_type) for edge in result.relations] == [
        ("RJ01609020", RelationType.TRANSLATION_OF)
    ]


def test_duplicate_child_worknos_are_normalized_before_relation_creation() -> None:
    info = TranslationInfoSource(
        is_parent=True,
        original_workno="RJ01609020",
        child_worknos=["rj01637033", "RJ01637033"],
    )

    result = TranslationRelationService().analyze("RJ01636949", info)

    child_edges = [
        edge
        for edge in result.relations
        if edge.relation_type is RelationType.HAS_TRANSLATION_CHILD
    ]
    assert len(child_edges) == 1


def test_self_references_are_reported_and_never_constructed() -> None:
    info = TranslationInfoSource(
        is_child=True,
        original_workno="RJ01609020",
        parent_workno="RJ01637033",
    )

    result = TranslationRelationService().analyze("RJ01637033", info)

    assert result.status is TranslationAnalysisStatus.INCOMPLETE
    assert [(edge.target_workno, edge.relation_type) for edge in result.relations] == [
        ("RJ01609020", RelationType.TRANSLATION_OF)
    ]
    assert result.contract_issue is not None


def test_queried_work_cannot_be_used_as_its_own_original() -> None:
    info = TranslationInfoSource(is_parent=True, original_workno="RJ01636949")

    result = TranslationRelationService().analyze("RJ01636949", info)

    assert result.status is TranslationAnalysisStatus.INCOMPLETE
    assert result.relations == ()
    assert result.contract_issue is not None


def test_malformed_work_code_is_rejected_at_provider_source_boundary() -> None:
    with pytest.raises(ValidationError):
        TranslationInfoSource(is_child=True, parent_workno="not-a-work-code")


def test_translation_service_rejects_malformed_queried_work_without_crashing() -> None:
    result = TranslationRelationService().analyze(
        "not-a-work-code",
        TranslationInfoSource(is_original=True),
    )

    assert result.status is TranslationAnalysisStatus.INVALID
    assert result.role is None
