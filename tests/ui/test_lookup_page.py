from datetime import UTC, datetime

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QLabel
from tests.services.test_lookup import FakeProvider

from dlsite_organizer.domain.relation import (
    Confidence,
    EvidenceType,
    RelationEvidence,
    RelationType,
    TranslationRole,
    WorkRelation,
)
from dlsite_organizer.domain.work import AgeCategory, TranslationAttribution, Work
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


def test_lookup_page_uses_generic_work_code_placeholder_and_subtitle(
    qapp: QApplication,
) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())

    assert page.code_input.placeholderText() == "输入完整RJ|BJ|VJ号"
    assert "RJ01609020" not in page.code_input.placeholderText()
    labels = "\n".join(label.text() for label in page.findChildren(QLabel))
    assert "读取当前作品信息并生成安全的格式化名称。" in labels
    assert "输入 RJcode" not in labels
    assert page.status_label.text() == "请输入作品编号开始查询。"
    assert page.status_label.property("state") != "error"
    page.close()


@pytest.mark.parametrize("workno", ["RJ00000001", "BJ00000001", "VJ00000001"])
def test_lookup_page_starts_lookup_for_every_supported_work_code(
    qapp: QApplication, workno: str
) -> None:
    page = LookupPage(
        LookupService(
            FakeProvider(work=Work(workno=workno, title="Fixture title")),
            NamingService(),
        ),
        CoverService(),
    )
    page.code_input.setText(workno.lower())

    page.start_lookup()
    thread = page._thread
    assert thread is not None

    loop = QEventLoop()
    thread.finished.connect(loop.quit)
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    qapp.processEvents()

    assert page.status_label.text().startswith("查询完成")
    assert page.workno_value.text() == workno
    page.close()


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

    assert page.relation_role_value.text() == "翻译父条目"
    assert page.relation_source_value.text() == "DLsite 明确提供"
    assert page.relation_confidence_value.text() == "已确认"
    assert page.relation_language_value.text() == "CHI_HANS"
    assert page.relation_details_value.text() == "翻译原作：RJ01609020"
    assert not page.relation_frame.isHidden()
    page.close()


def test_lookup_page_renders_rich_work_fields_and_separate_translation_attribution(
    qapp: QApplication,
) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    result = LookupResult(
        work=Work(
            workno="RJ01637033",
            title="中性测试作品",
            maker_id="RG01058997",
            maker_name="原作社团",
            series_name="测试系列",
            cvs=["CV A", "CV B"],
            tags=["ASMR", "标签"],
            language="CHI_HANS",
            age_category=AgeCategory.R18,
        ),
        formatted_name="[原作社团][RJ01637033] 中性测试作品",
        translation_attribution=TranslationAttribution(
            maker_id="RG01001331",
            maker_name="翻译署名",
        ),
    )

    page._show_result(result)

    assert page.maker_value.text() == "原作社团"
    assert page.maker_id_value.text() == "RG01058997"
    assert page.series_value.text() == "测试系列"
    assert page.cvs_value.text() == "CV A、CV B"
    assert page.tags_value.text() == "ASMR、标签"
    assert page.language_value.text() == "简体中文（CHI_HANS）"
    assert page.age_value.text() == "R18"
    assert page.relation_attribution_value.text() == "翻译署名 (RG01001331)"
    assert page.maker_value.text() != page.relation_attribution_value.text()
    assert not page.relation_frame.isHidden()
    page.close()


def test_lookup_page_renders_missing_rich_fields_as_dashes(qapp: QApplication) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())

    page._show_result(
        LookupResult(
            work=Work(workno="RJ01609020", title="只有标题"),
            formatted_name="[RJ01609020] 只有标题",
        )
    )

    assert page.maker_value.text() == "—"
    assert page.maker_id_value.text() == "—"
    assert page.series_value.text() == "—"
    assert page.cvs_value.text() == "—"
    assert page.tags_value.text() == "—"
    assert page.language_value.text() == "—"
    assert page.age_value.text() == "未知"
    assert page.relation_attribution_value.text() == "—"
    assert page.relation_frame.isHidden()
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


def test_lookup_page_renders_current_and_historical_confirmed_relations(
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
        )
    )

    assert "翻译原作：RJ01609020" in page.relation_details_value.text()
    assert "RJ01636950" in page.historical_relations_value.text()
    labels = "\n".join(label.text() for label in page.findChildren(QLabel))
    assert all(term not in labels for term in ("候选", "人工", "Candidate", "Review"))
    page.close()
