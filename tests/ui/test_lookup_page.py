import pytest
from PySide6.QtWidgets import QApplication
from tests.services.test_lookup import FakeProvider

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
from dlsite_organizer.services.lookup import LookupResult, LookupService
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
