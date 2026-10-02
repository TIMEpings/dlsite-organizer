"""Observable local relation behavior, independent of repository query shape."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import delete

from dlsite_organizer.domain.candidate import CandidateEvidenceKind
from dlsite_organizer.domain.relation import Confidence, RelationType
from dlsite_organizer.domain.work import TranslationAttribution, Work
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.metadata_store import (
    MetadataObservation,
    MetadataStore,
    WorkMetadataCache,
)
from dlsite_organizer.providers.dlsite.sources import TranslationInfoSource
from dlsite_organizer.services.candidate_relations import CandidateRelationService
from dlsite_organizer.services.historical_relations import HistoricalRelationService
from dlsite_organizer.services.lookup import LookupFreshness, LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.translation_relations import TranslationAnalysisStatus

NOW = datetime(2026, 10, 2, 4, tzinfo=UTC)
DAY = datetime(2026, 9, 1, tzinfo=UTC)
TARGET = "RJ00000010"


class OfflineProvider:
    def fetch_work(self, workno: str) -> Work:
        raise AssertionError(f"Fresh cache attempted network lookup: {workno}")


def make_work(number: int, *, maker_id: str | None = "RG同人", name="夢・中文") -> Work:
    return Work(
        workno=f"RJ{number:08}",
        title=f"日本語作品・中文 {number}",
        maker_id=maker_id,
        maker_name=name,
        regist_datetime=DAY,
    )


def populate(store: MetadataStore, size: int, related: bool) -> None:
    """Same fixtures used by before/after probes; population is never timed."""
    for number in range(1, size + 1):
        work = make_work(number, maker_id="RG同人" if related and number <= 14 else f"M{number}")
        info = None
        if related and number == 10:
            info = TranslationInfoSource(
                is_parent=True,
                original_workno="RJ00000001",
                child_worknos=["RJ00000011", "RJ00000012", "RJ00000011"],
                lang="CHI_HANS",
            )
        if related and number == 11:
            info = TranslationInfoSource(
                is_child=True,
                original_workno="RJ00000001",
                parent_workno=TARGET,
            )
        store.save(
            work,
            info,
            source="fixture",
            fetched_at=NOW,
            translation_attribution=TranslationAttribution(maker_name="翻訳・汉化组"),
            core_source="CORE",
            metadata_source="RICH",
        )
        store.append_observation(work, info, source="fixture", observed_at=NOW)
    if related:
        store.append_observation(
            make_work(10),
            TranslationInfoSource(is_parent=True, original_workno="RJ00000002"),
            source="older-provider",
            observed_at=NOW - timedelta(days=1),
        )


def service_for(store: MetadataStore) -> LookupService:
    history = HistoricalRelationService(store)
    return LookupService(
        OfflineProvider(),
        NamingService(),
        metadata_store=store,
        historical_relations=history,
        candidate_relations=CandidateRelationService(
            store,
            historical_relations=history,
            clock=lambda: NOW,
        ),
        clock=lambda: NOW,
    )


@pytest.fixture
def store(tmp_path: Path):
    database = Database(tmp_path / "relations.sqlite3")
    database.initialize()
    repository = MetadataStore(database)
    yield repository
    database.dispose()


def test_fresh_lookup_without_relations_is_absent_and_offline(store: MetadataStore) -> None:
    populate(store, 20, False)
    result = service_for(store).lookup(TARGET)
    assert result.freshness is LookupFreshness.CACHE_FRESH
    assert result.translation.status is TranslationAnalysisStatus.NO_INFORMATION
    assert result.relations == ()
    assert result.historical_relations.outgoing == result.historical_relations.incoming == ()
    assert result.candidates == ()


def test_current_history_overlap_direction_counts_provenance_and_order(
    store: MetadataStore,
) -> None:
    populate(store, 20, True)
    store.append_observation(
        make_work(10),
        TranslationInfoSource(
            is_parent=True,
            original_workno="RJ00000001",
            child_worknos=["RJ00000011"],
        ),
        source="repeat",
        observed_at=NOW,
    )
    result = service_for(store).lookup(TARGET)
    assert result.freshness is LookupFreshness.CACHE_FRESH
    assert result.work.title == "日本語作品・中文 10"
    assert result.translation_attribution == TranslationAttribution(maker_name="翻訳・汉化组")
    assert (result.core_source, result.metadata_source, result.source) == (
        "CORE",
        "RICH",
        "fixture",
    )
    assert result.translation.language == "CHI_HANS"
    assert [(r.relation_type, r.target_workno) for r in result.relations] == [
        (RelationType.TRANSLATION_OF, "RJ00000001"),
        (RelationType.HAS_TRANSLATION_CHILD, "RJ00000011"),
        (RelationType.HAS_TRANSLATION_CHILD, "RJ00000012"),
    ]
    assert all(r.confidence is Confidence.CONFIRMED for r in result.relations)
    assert result.relations[0].evidence[0].attributes == {
        "field": "translation_info.original_workno",
        "value": "RJ00000001",
    }
    outgoing = result.historical_relations.outgoing
    assert [(r.relation_type.value, r.target_workno) for r in outgoing] == [
        ("has_translation_child", "RJ00000011"),
        ("has_translation_child", "RJ00000012"),
        ("translation_of", "RJ00000001"),
        ("translation_of", "RJ00000002"),
    ]
    assert outgoing[0].observation_count == 2
    assert outgoing[0].first_seen == outgoing[0].last_seen == NOW
    assert [e.provider_source for e in outgoing[0].evidence] == ["fixture", "repeat"]
    assert [e.observation_id for e in outgoing[0].evidence] == sorted(
        e.observation_id for e in outgoing[0].evidence
    )
    assert outgoing[-1].first_seen == NOW - timedelta(days=1)
    assert [(r.subject_workno, r.relation_type) for r in result.historical_relations.incoming] == [
        ("RJ00000011", RelationType.CHILD_OF_TRANSLATION),
    ]
    # Current and historical confirmed pairs are excluded, but no candidate is promoted.
    assert [r.target_workno for r in result.candidates] == [
        "RJ00000009",
        "RJ00000008",
        "RJ00000007",
        "RJ00000013",
        "RJ00000006",
        "RJ00000014",
        "RJ00000005",
        "RJ00000004",
        "RJ00000003",
    ]
    assert result.candidate_relations is not None
    assert result.candidate_relations.candidates[0].context[0].value == 14
    assert result.candidate_relations.candidates[0].context[1].value == 14


@pytest.mark.parametrize(
    ("info", "status", "targets"),
    [
        (None, TranslationAnalysisStatus.NO_INFORMATION, ()),
        (
            TranslationInfoSource(original_workno="RJ00000001"),
            TranslationAnalysisStatus.NO_INFORMATION,
            (),
        ),
        (
            TranslationInfoSource(is_child=True, is_parent=True, original_workno="RJ00000001"),
            TranslationAnalysisStatus.INVALID,
            (),
        ),
        (
            TranslationInfoSource(is_child=True, original_workno="RJ00000001"),
            TranslationAnalysisStatus.INCOMPLETE,
            ("RJ00000001",),
        ),
        (
            TranslationInfoSource(is_parent=True, original_workno=TARGET),
            TranslationAnalysisStatus.INCOMPLETE,
            (),
        ),
    ],
)
def test_unknown_invalid_incomplete_evidence_stays_safe(
    store: MetadataStore,
    info: TranslationInfoSource | None,
    status: TranslationAnalysisStatus,
    targets: tuple[str, ...],
) -> None:
    store.save(make_work(10), info, source="fixture", fetched_at=NOW)
    store.append_observation(make_work(10), info, source="fixture", observed_at=NOW)
    result = service_for(store).lookup(TARGET)
    assert result.translation.status is status
    assert tuple(r.target_workno for r in result.relations) == targets
    assert tuple(r.target_workno for r in result.historical_relations.outgoing) == targets


def test_historical_facts_survive_current_removal_and_missing_target_metadata(
    store: MetadataStore,
) -> None:
    populate(store, 20, True)
    store.save(make_work(10), None, source="current-without-references", fetched_at=NOW)
    with store._database.session() as session:
        session.execute(delete(WorkMetadataCache).where(WorkMetadataCache.workno == "RJ00000002"))
        session.execute(
            delete(MetadataObservation).where(MetadataObservation.workno == "RJ00000002")
        )
        session.commit()
    result = service_for(store).lookup(TARGET)
    assert result.relations == ()
    assert [r.target_workno for r in result.historical_relations.outgoing] == [
        "RJ00000011",
        "RJ00000012",
        "RJ00000001",
        "RJ00000002",
    ]


def test_unicode_maker_identity_latest_snapshot_and_all_date_context(store: MetadataStore) -> None:
    for number, name, day in [
        (10, "Ａ夢 中文", DAY),
        (11, "A夢 中文", DAY),
        (12, "A夢 中文", DAY + timedelta(days=1)),
    ]:
        work = make_work(number, maker_id=None, name=name).model_copy(
            update={"regist_datetime": day}
        )
        store.save(work, None, source="current", fetched_at=NOW)
    # Old matching history must not override a current different maker.
    store.append_observation(
        make_work(13, maker_id=None, name="Ａ夢 中文"),
        None,
        source="old",
        observed_at=NOW - timedelta(days=1),
    )
    store.save(make_work(13, maker_id="other"), None, source="current", fetched_at=NOW)
    # Observation-only work uses the latest complete snapshot, with ID tie-breaking.
    store.append_observation(make_work(14, maker_id="other"), None, source="old", observed_at=NOW)
    store.append_observation(
        make_work(14, maker_id=None, name="A夢 中文"), None, source="new", observed_at=NOW
    )
    result = service_for(store).lookup(TARGET)
    assert [r.target_workno for r in result.candidates] == ["RJ00000011", "RJ00000014"]
    assert result.candidate_relations is not None
    context = {e.kind: e.value for e in result.candidate_relations.candidates[0].context}
    assert context[CandidateEvidenceKind.LOCAL_KNOWN_MAKER_WORK_COUNT] == 4
    assert context[CandidateEvidenceKind.LOCAL_MAKER_DAY_GROUP_SIZE] == 3
