from datetime import UTC, datetime

import pytest
from PySide6.QtWidgets import QApplication, QLabel
from tests.services.test_lookup import FakeProvider

from dlsite_organizer.domain.candidate import (
    CandidateEvidence,
    CandidateEvidenceKind,
    CandidateEvidencePolarity,
    CandidateRelation,
    CandidateSearchResult,
    CandidateSearchState,
    CandidateSnapshotSource,
    KnownWorkSnapshot,
)
from dlsite_organizer.domain.relation import (
    Confidence,
    EvidenceType,
    RelationEvidence,
    RelationType,
    TranslationRole,
    WorkRelation,
)
from dlsite_organizer.domain.work import Work
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.historical_relations import (
    HistoricalRelation,
    HistoricalRelations,
)
from dlsite_organizer.services.lookup import LookupFreshness, LookupResult, LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.translation_relations import (
    TranslationAnalysis,
    TranslationAnalysisStatus,
)
from dlsite_organizer.ui.pages.lookup_page import LookupPage


@pytest.fixture
def qapp() -> QApplication:
    application = QApplication.instance()
    if isinstance(application, QApplication):
        return application
    return QApplication([])


def candidate_result(
    *,
    state: CandidateSearchState = CandidateSearchState.FOUND,
    target_source: CandidateSnapshotSource = CandidateSnapshotSource.CURRENT_CACHE,
    target_workno: str = "RJ01637033",
    truncated: bool = False,
    total_candidate_count: int = 1,
) -> CandidateSearchResult:
    evaluated_at = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
    source = KnownWorkSnapshot(
        workno="RJ01636949",
        title="源作品",
        maker_id="RG60289",
        regist_datetime=datetime(2026, 7, 7, tzinfo=UTC),
        source=CandidateSnapshotSource.CURRENT_CACHE,
    )
    target = KnownWorkSnapshot(
        workno=target_workno,
        title="候选作品",
        maker_id="RG60289",
        regist_datetime=datetime(2026, 7, 7, tzinfo=UTC),
        source=target_source,
        observed_at=(
            evaluated_at
            if target_source is CandidateSnapshotSource.HISTORICAL_OBSERVATION
            else None
        ),
    )
    candidate = CandidateRelation(
        source_workno=source.workno,
        target_workno=target.workno,
        evaluated_at=evaluated_at,
        target_snapshot=target,
        source_snapshot=source,
        supporting_evidence=(
            CandidateEvidence(
                kind=CandidateEvidenceKind.RJ_NUMERIC_DISTANCE,
                value=84,
                description="RJ distance",
            ),
        ),
        context=(
            CandidateEvidence(
                kind=CandidateEvidenceKind.LOCAL_MAKER_DAY_GROUP_SIZE,
                value=3,
                polarity=CandidateEvidencePolarity.CONTEXT,
                description="group",
            ),
        ),
    )
    return CandidateSearchResult(
        source_work=source,
        candidates=(candidate,) if state is CandidateSearchState.FOUND else (),
        state=state,
        evaluated_at=evaluated_at,
        truncated=truncated,
        total_candidate_count=total_candidate_count,
    )


def historical_relation(target: str) -> HistoricalRelation:
    when = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
    return HistoricalRelation(
        subject_workno="RJ01636949",
        relation_type=RelationType.TRANSLATION_OF,
        target_workno=target,
        confidence=Confidence.CONFIRMED,
        first_seen=when,
        last_seen=when,
        observation_count=1,
        evidence=(),
    )


def test_lookup_page_renders_application_relation_result(qapp: QApplication) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    relation = WorkRelation(
        source_workno="RJ01636949",
        target_workno="RJ01609020",
        relation_type=RelationType.TRANSLATION_OF,
        confidence=Confidence.CONFIRMED,
        evidence=[
            RelationEvidence(
                evidence_type=EvidenceType.EXPLICIT_TRANSLATION_REFERENCE,
                description="fixture",
            )
        ],
        detection_source="fixture",
    )
    result = LookupResult(
        work=Work(workno="RJ01636949", title="翻译作品"),
        formatted_name="[RJ01636949] 翻译作品",
        translation=TranslationAnalysis(
            role=TranslationRole.TRANSLATION_PARENT,
            relations=(relation,),
            language="CHI_HANS",
            status=TranslationAnalysisStatus.CONFIRMED,
        ),
    )

    page._show_result(result)

    assert page.relation_role_value.text() == "翻译作品 Parent"
    assert page.relation_source_value.text() == "DLsite translation_info"
    assert page.relation_confidence_value.text() == "已确认"
    assert page.relation_language_value.text() == "CHI_HANS"
    assert page.relation_details_value.text() == "翻译原作：RJ01609020"
    page.close()


@pytest.mark.parametrize(
    ("freshness", "label"),
    [
        (LookupFreshness.LIVE, "实时"),
        (LookupFreshness.CACHE_FRESH, "缓存"),
        (LookupFreshness.CACHE_STALE_FALLBACK, "旧缓存"),
    ],
)
def test_lookup_page_shows_delivery_freshness(
    qapp: QApplication, freshness: LookupFreshness, label: str
) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())

    page._show_result(
        LookupResult(
            work=Work(workno="RJ01609020", title="标题"),
            formatted_name="[RJ01609020] 标题",
            freshness=freshness,
            source="DLSITE_PRODUCT_INFO_AJAX",
        )
    )

    assert label in page.status_label.text()
    page.close()


def test_lookup_page_force_refresh_uses_bypass_cache_path_and_is_disabled_while_busy(
    qapp: QApplication,
) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    page.code_input.setText("RJ01609020")
    calls: list[bool] = []
    page._run_lookup = lambda *, force_refresh: calls.append(force_refresh)

    page.force_refresh()
    assert calls == [True]

    page._thread = object()  # type: ignore[assignment]
    page.force_refresh()
    assert calls == [True]
    page._thread = None
    page._set_loading(True)
    assert not page.refresh_button.isEnabled()
    page._set_loading(False)
    assert page.refresh_button.isEnabled()
    page.close()


def test_lookup_page_renders_candidate_only_with_disclaimer_and_context(qapp: QApplication) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())

    page._show_result(
        LookupResult(
            work=Work(workno="RJ01636949", title="源作品"),
            formatted_name="[RJ01636949] 源作品",
            candidate_relations=candidate_result(),
        )
    )

    assert "RJ01637033" in page.candidate_relations_value.text()
    labels = "\n".join(label.text() for label in page.findChildren(QLabel))
    assert "候选关系（非确认）" in labels
    assert (
        "根据本地元数据筛选，仅供检查，不代表 DLsite 已确认关系。"
        in page.candidate_relations_value.text()
    )
    assert "本地已知同社团同日作品数：3" in page.candidate_relations_value.text()
    assert page.historical_relations_value.text() == "暂无历史已确认关系"
    page.close()


def test_lookup_page_keeps_current_historical_and_candidate_layers_separate(
    qapp: QApplication,
) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    current = WorkRelation(
        source_workno="RJ01636949",
        target_workno="RJ01609020",
        relation_type=RelationType.TRANSLATION_OF,
        confidence=Confidence.CONFIRMED,
        evidence=[],
        detection_source="fixture",
    )

    page._show_result(
        LookupResult(
            work=Work(workno="RJ01636949", title="源作品"),
            formatted_name="[RJ01636949] 源作品",
            translation=TranslationAnalysis(
                role=TranslationRole.TRANSLATION_PARENT,
                relations=(current,),
                status=TranslationAnalysisStatus.CONFIRMED,
            ),
            historical_relations=HistoricalRelations(
                outgoing=(historical_relation("RJ01636950"),)
            ),
            candidate_relations=candidate_result(target_workno="RJ01637033"),
        )
    )

    assert "翻译原作：RJ01609020" in page.relation_details_value.text()
    assert "RJ01636950" in page.historical_relations_value.text()
    assert "RJ01637033" in page.candidate_relations_value.text()
    assert "RJ01636950" not in page.candidate_relations_value.text()
    assert "RJ01609020" not in page.candidate_relations_value.text()
    page.close()


def test_lookup_page_does_not_duplicate_historical_confirmed_target_as_candidate(
    qapp: QApplication,
) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    confirmed_target = "RJ01637033"

    page._show_result(
        LookupResult(
            work=Work(workno="RJ01636949", title="源作品"),
            formatted_name="[RJ01636949] 源作品",
            historical_relations=HistoricalRelations(
                outgoing=(historical_relation(confirmed_target),)
            ),
            candidate_relations=candidate_result(
                state=CandidateSearchState.NONE,
                target_workno=confirmed_target,
            ),
        )
    )

    assert confirmed_target in page.historical_relations_value.text()
    assert confirmed_target not in page.candidate_relations_value.text()
    assert "暂无" in page.candidate_relations_value.text()
    page.close()


def test_lookup_page_explains_insufficient_metadata_distinct_from_none(qapp: QApplication) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())

    page._show_candidates(candidate_result(state=CandidateSearchState.INSUFFICIENT_METADATA))
    insufficient = page.candidate_relations_value.text()
    page._show_candidates(candidate_result(state=CandidateSearchState.NONE))
    none = page.candidate_relations_value.text()

    assert "无法生成候选" in insufficient
    assert "缺少可比较的社团身份" in insufficient
    assert "当前本地元数据中暂无符合筛选条件的关联作品候选" in none
    assert insufficient != none
    page.close()


def test_lookup_page_labels_historical_candidate_provenance(qapp: QApplication) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())

    page._show_candidates(
        candidate_result(target_source=CandidateSnapshotSource.HISTORICAL_OBSERVATION)
    )

    assert "本地历史元数据" in page.candidate_relations_value.text()
    page.close()


def test_lookup_page_explains_truncated_candidate_results(qapp: QApplication) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())

    page._show_candidates(candidate_result(truncated=True, total_candidate_count=5))

    assert "仅显示 1/5 项" in page.candidate_relations_value.text()
    page.close()
