from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from dlsite_organizer.domain.relation import Confidence, EvidenceType, RelationType, TranslationRole
from dlsite_organizer.domain.work import Work
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.metadata_store import MetadataStore, WorkMetadataCache
from dlsite_organizer.providers.dlsite.client import DlsiteWorkLookup
from dlsite_organizer.providers.dlsite.exceptions import (
    DlsiteConnectionError,
    DlsiteHttpError,
    DlsiteParseError,
    WorkNotFoundError,
)
from dlsite_organizer.providers.dlsite.sources import (
    ProductInfoAjaxSource,
    TranslationInfoSource,
    normalize_product_info_ajax,
    parse_product_info_ajax,
)
from dlsite_organizer.services.lookup import (
    LookupFailure,
    LookupFailureKind,
    LookupFreshness,
    LookupResult,
    LookupService,
)
from dlsite_organizer.services.naming import NamingService

FIXTURE_DIR = Path(__file__).parents[1] / "fixtures" / "dlsite"


@dataclass
class MutableClock:
    value: datetime

    def __call__(self) -> datetime:
        return self.value


@dataclass
class SequencedProvider:
    responses: Sequence[DlsiteWorkLookup | Exception]
    calls: list[str] | None = None

    def __post_init__(self) -> None:
        if self.calls is None:
            self.calls = []

    def fetch_work_lookup(self, workno: str) -> DlsiteWorkLookup:
        assert self.calls is not None
        self.calls.append(workno)
        assert self.responses, "unexpected provider call"
        response = self.responses[0]
        self.responses = self.responses[1:]
        if isinstance(response, Exception):
            raise response
        return response


def make_work(
    workno: str = "RJ01609020",
    *,
    title: str = "旧标题",
    maker_id: str = "RG01058997",
    maker_name: str | None = "社团",
) -> Work:
    return Work(
        workno=workno,
        title=title,
        maker_id=maker_id,
        maker_name=maker_name,
        release_date=date(2026, 8, 30),
        series_name="系列",
        cvs=["CV"],
        tags=["tag"],
        cover_url="https://img.example.test/cover.jpg",
        source_section="maniax",
    )


def make_translation(**overrides: object) -> TranslationInfoSource:
    values: dict[str, object] = {
        "is_original": False,
        "is_parent": True,
        "is_child": False,
        "original_workno": "RJ01609020",
        "parent_workno": None,
        "child_worknos": ["RJ01637033"],
        "lang": "CHI_HANS",
    }
    values.update(overrides)
    return TranslationInfoSource.model_validate(values)


def make_lookup(
    work: Work,
    *,
    translation_info: TranslationInfoSource | None = None,
    source: str = "DLSITE_PRODUCT_INFO_AJAX",
    regist_datetime: datetime | None = None,
) -> DlsiteWorkLookup:
    product = ProductInfoAjaxSource.model_validate(
        {
            "requested_workno": work.workno,
            "envelope_workno": work.workno,
            "work_name": work.title,
            "maker_id": work.maker_id,
            "maker_name": work.maker_name,
            "regist_date": regist_datetime,
            "work_image": work.cover_url,
            "work_type": "game",
            "age_category": 18,
            "translation_info": translation_info,
        }
    ) if source == "DLSITE_PRODUCT_INFO_AJAX" else None
    return DlsiteWorkLookup(work=work, product_info=product, source=source)


def open_service(
    path: Path,
    provider: SequencedProvider,
    clock: MutableClock,
    *,
    ttl_hours: float = 1.0,
) -> tuple[Database, MetadataStore, LookupService]:
    database = Database(path)
    database.initialize()
    store = MetadataStore(database)
    service = LookupService(
        provider,
        NamingService(),
        metadata_store=store,
        cache_ttl_hours=ttl_hours,
        clock=clock,
    )
    return database, store, service


def test_cache_miss_saves_live_current_row_and_one_observation(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
    clock = MutableClock(now)
    work = make_work()
    provider = SequencedProvider(
        [
            make_lookup(
                work,
                translation_info=make_translation(),
                regist_datetime=datetime(2026, 8, 30, 9, 59, 58, tzinfo=UTC),
            )
        ]
    )
    database, store, service = open_service(tmp_path / "metadata.sqlite3", provider, clock)

    result = service.lookup("rj01609020")

    assert result.freshness is LookupFreshness.LIVE
    assert result.source == "DLSITE_PRODUCT_INFO_AJAX"
    assert result.fetched_at == now
    assert result.fetched_at is not None and result.fetched_at.tzinfo is UTC
    assert provider.calls == ["RJ01609020"]
    cached = store.get("RJ01609020")
    assert cached is not None and cached.work == work
    assert cached.source == "DLSITE_PRODUCT_INFO_AJAX"
    assert cached.fetched_at == now
    assert len(store.list_observations("RJ01609020")) == 1
    database.dispose()


def test_fresh_cache_hit_does_not_call_provider_or_append_observation(tmp_path: Path) -> None:
    now = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
    clock = MutableClock(now)
    provider = SequencedProvider([make_lookup(make_work(), translation_info=make_translation())])
    database, store, service = open_service(tmp_path / "metadata.sqlite3", provider, clock)

    first = service.lookup("RJ01609020")
    second = service.lookup("RJ01609020")

    assert first.freshness is LookupFreshness.LIVE
    assert second.freshness is LookupFreshness.CACHE_FRESH
    assert second.source == "DLSITE_PRODUCT_INFO_AJAX"
    assert second.fetched_at == first.fetched_at == now
    assert provider.calls == ["RJ01609020"]
    assert len(store.list_observations("RJ01609020")) == 1
    database.dispose()


def test_force_refresh_replaces_current_row_and_appends_observation(tmp_path: Path) -> None:
    first_time = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
    second_time = datetime(2026, 8, 30, 10, 1, tzinfo=UTC)
    clock = MutableClock(first_time)
    provider = SequencedProvider(
        [
            make_lookup(make_work(title="A", maker_id="maker1")),
            make_lookup(make_work(title="B", maker_id="maker2")),
        ]
    )
    database, store, service = open_service(tmp_path / "metadata.sqlite3", provider, clock)

    first = service.lookup("RJ01609020")
    clock.value = second_time
    refreshed = service.lookup("RJ01609020", force_refresh=True)

    assert first.freshness is LookupFreshness.LIVE
    assert refreshed.freshness is LookupFreshness.LIVE
    assert refreshed.work.title == "B"
    assert refreshed.fetched_at == second_time
    assert provider.calls == ["RJ01609020", "RJ01609020"]
    cached = store.get("RJ01609020")
    assert cached is not None and cached.work.title == "B"
    observations = store.list_observations("RJ01609020")
    assert [(row.title, row.maker_id) for row in observations] == [("A", "maker1"), ("B", "maker2")]
    database.dispose()


def test_stale_cache_live_success_replaces_current_row_and_keeps_old_observation(
    tmp_path: Path,
) -> None:
    start = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
    clock = MutableClock(start)
    provider = SequencedProvider(
        [
            make_lookup(make_work(title="old title", maker_id="maker1")),
            make_lookup(make_work(title="new title", maker_id="maker2")),
        ]
    )
    database, store, service = open_service(tmp_path / "metadata.sqlite3", provider, clock)

    service.lookup("RJ01609020")
    clock.value = start + timedelta(hours=1)
    result = service.lookup("RJ01609020")

    assert result.freshness is LookupFreshness.LIVE
    assert result.work.title == "new title"
    assert provider.calls == ["RJ01609020", "RJ01609020"]
    cached = store.get("RJ01609020")
    assert cached is not None and cached.work.title == "new title"
    observations = store.list_observations("RJ01609020")
    assert [(row.title, row.maker_id) for row in observations] == [
        ("old title", "maker1"),
        ("new title", "maker2"),
    ]
    database.dispose()


@pytest.mark.parametrize(
    ("age", "expected_freshness"),
    [
        (timedelta(hours=1) - timedelta(microseconds=1), LookupFreshness.CACHE_FRESH),
        (timedelta(hours=1), LookupFreshness.LIVE),
        (timedelta(hours=1) + timedelta(microseconds=1), LookupFreshness.LIVE),
    ],
)
def test_cache_ttl_boundary_treats_age_at_or_above_ttl_as_stale(
    tmp_path: Path,
    age: timedelta,
    expected_freshness: LookupFreshness,
) -> None:
    start = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
    clock = MutableClock(start)
    provider = SequencedProvider(
        [make_lookup(make_work()), make_lookup(make_work(title="fresh response"))]
    )
    database, _store, service = open_service(tmp_path / "metadata.sqlite3", provider, clock)

    service.lookup("RJ01609020")
    clock.value = start + age
    result = service.lookup("RJ01609020")

    assert result.freshness is expected_freshness
    expected_calls = 1 if expected_freshness is LookupFreshness.CACHE_FRESH else 2
    assert len(provider.calls or []) == expected_calls
    database.dispose()


@pytest.mark.parametrize("failure", [DlsiteConnectionError("offline"), DlsiteHttpError("503")])
def test_stale_cache_falls_back_only_for_transient_transport_or_server_failure(
    tmp_path: Path,
    failure: Exception,
) -> None:
    start = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
    clock = MutableClock(start)
    provider = SequencedProvider([make_lookup(make_work(title="old")), failure])
    database, store, service = open_service(tmp_path / "metadata.sqlite3", provider, clock)
    service.lookup("RJ01609020")
    clock.value = start + timedelta(hours=1)

    result = service.lookup("RJ01609020")

    assert result.freshness is LookupFreshness.CACHE_STALE_FALLBACK
    assert result.work.title == "old"
    assert result.source == "DLSITE_PRODUCT_INFO_AJAX"
    assert result.fetched_at == start
    assert len(store.list_observations("RJ01609020")) == 1
    database.dispose()


@pytest.mark.parametrize(
    "failure",
    [WorkNotFoundError("RJ01609020"), DlsiteParseError("bad contract")],
)
def test_stale_cache_does_not_hide_not_found_or_parse_contract_failure(
    tmp_path: Path,
    failure: Exception,
) -> None:
    start = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
    clock = MutableClock(start)
    provider = SequencedProvider([make_lookup(make_work(title="old")), failure])
    database, store, service = open_service(tmp_path / "metadata.sqlite3", provider, clock)
    service.lookup("RJ01609020")
    clock.value = start + timedelta(hours=1)

    with pytest.raises(LookupFailure) as caught:
        service.lookup("RJ01609020")

    expected_kind = (
        LookupFailureKind.NOT_FOUND
        if isinstance(failure, WorkNotFoundError)
        else LookupFailureKind.RESPONSE
    )
    assert caught.value.kind is expected_kind
    assert len(store.list_observations("RJ01609020")) == 1
    database.dispose()


def test_live_ajax_and_html_append_once_but_fresh_stale_and_failed_lookups_do_not(
    tmp_path: Path,
) -> None:
    start = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
    clock = MutableClock(start)
    provider = SequencedProvider(
        [
            make_lookup(make_work(title="AJAX")),
            make_lookup(make_work(title="HTML"), source="DLSITE_HTML_JSONLD"),
            DlsiteConnectionError("offline"),
            DlsiteConnectionError("offline"),
        ]
    )
    database, store, service = open_service(tmp_path / "metadata.sqlite3", provider, clock)

    service.lookup("RJ01609020")
    assert len(store.list_observations("RJ01609020")) == 1
    assert service.lookup("RJ01609020").freshness is LookupFreshness.CACHE_FRESH
    service.lookup("RJ01609020", force_refresh=True)
    assert len(store.list_observations("RJ01609020")) == 2
    clock.value = start + timedelta(hours=1)
    assert service.lookup("RJ01609020").freshness is LookupFreshness.CACHE_STALE_FALLBACK
    assert len(store.list_observations("RJ01609020")) == 2
    with pytest.raises(LookupFailure):
        service.lookup("RJ01636949")
    assert store.list_observations("RJ01636949") == ()
    database.dispose()


def test_metadata_persistence_failures_do_not_turn_live_lookup_into_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    now = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
    clock = MutableClock(now)
    provider = SequencedProvider([make_lookup(make_work())])
    database, store, _service = open_service(tmp_path / "metadata.sqlite3", provider, clock)

    def fail_save(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("cache disk failure")

    def fail_append(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("observation disk failure")

    monkeypatch.setattr(store, "save", fail_save)
    monkeypatch.setattr(store, "append_observation", fail_append)
    service = LookupService(
        provider,
        NamingService(),
        metadata_store=store,
        clock=clock,
    )

    result = service.lookup("RJ01609020")

    assert isinstance(result, LookupResult)
    assert result.freshness is LookupFreshness.LIVE
    assert "Metadata cache persistence failed" in caplog.text
    assert "Metadata observation persistence failed" in caplog.text
    database.dispose()


def test_restart_returns_fresh_cache_without_process_memory_or_provider_call(
    tmp_path: Path,
) -> None:
    path = tmp_path / "metadata.sqlite3"
    start = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
    first_clock = MutableClock(start)
    first_provider = SequencedProvider([make_lookup(make_work(title="persisted"))])
    first_database, _store, first_service = open_service(path, first_provider, first_clock)
    first_service.lookup("RJ01609020")
    first_database.dispose()

    restarted_clock = MutableClock(start + timedelta(minutes=5))
    restarted_provider = SequencedProvider([])
    second_database, _store, second_service = open_service(
        path,
        restarted_provider,
        restarted_clock,
    )

    result = second_service.lookup("RJ01609020")

    assert result.freshness is LookupFreshness.CACHE_FRESH
    assert result.work.title == "persisted"
    assert restarted_provider.calls == []
    assert result.fetched_at == start
    second_database.dispose()


@pytest.mark.parametrize(
    ("workno", "role", "relations"),
    [
        ("RJ01609020", TranslationRole.ORIGINAL, ()),
        (
            "RJ01636949",
            TranslationRole.TRANSLATION_PARENT,
            (
                ("RJ01609020", RelationType.TRANSLATION_OF),
                ("RJ01637033", RelationType.HAS_TRANSLATION_CHILD),
                ("RJ01636950", RelationType.HAS_TRANSLATION_CHILD),
                ("RJ01663275", RelationType.HAS_TRANSLATION_CHILD),
            ),
        ),
        (
            "RJ01637033",
            TranslationRole.TRANSLATION_CHILD,
            (
                ("RJ01636949", RelationType.CHILD_OF_TRANSLATION),
                ("RJ01609020", RelationType.TRANSLATION_OF),
            ),
        ),
    ],
)
def test_translation_relation_application_result_round_trips_across_restart(
    tmp_path: Path,
    workno: str,
    role: TranslationRole,
    relations: tuple[tuple[str, RelationType], ...],
) -> None:
    payload = (FIXTURE_DIR / f"product_info_{workno}.json").read_text(encoding="utf-8")
    source = parse_product_info_ajax(payload, workno)
    work = normalize_product_info_ajax(source, section="maniax")
    response = DlsiteWorkLookup(
        work=work,
        product_info=source,
        source="DLSITE_PRODUCT_INFO_AJAX",
    )
    start = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
    first_provider = SequencedProvider([response])
    first_database, _store, first_service = open_service(
        tmp_path / "metadata.sqlite3", first_provider, MutableClock(start)
    )
    live_result = first_service.lookup(workno)
    first_database.dispose()

    restarted_provider = SequencedProvider([])
    second_database, _store, second_service = open_service(
        tmp_path / "metadata.sqlite3",
        restarted_provider,
        MutableClock(start + timedelta(minutes=5)),
    )
    cached_result = second_service.lookup(workno)

    assert cached_result.freshness is LookupFreshness.CACHE_FRESH
    assert restarted_provider.calls == []
    assert cached_result.translation == live_result.translation
    assert cached_result.translation_role is role
    translation_info = source.translation_info
    assert translation_info is not None
    assert cached_result.translation.language == translation_info.lang
    assert cached_result.translation.status.value == "confirmed"
    actual_relations = {
        (relation.target_workno, relation.relation_type) for relation in cached_result.relations
    }
    assert actual_relations == set(relations)
    assert all(relation.confidence is Confidence.CONFIRMED for relation in cached_result.relations)
    assert all(
        evidence.evidence_type is EvidenceType.EXPLICIT_TRANSLATION_REFERENCE
        for relation in cached_result.relations
        for evidence in relation.evidence
    )
    if role is TranslationRole.ORIGINAL:
        assert not cached_result.relations
    second_database.dispose()


def test_malformed_cached_translation_and_provider_failure_is_normal_lookup_failure(
    tmp_path: Path,
) -> None:
    path = tmp_path / "metadata.sqlite3"
    start = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
    first_database, _store, first_service = open_service(
        path,
        SequencedProvider([make_lookup(make_work(), translation_info=make_translation())]),
        MutableClock(start),
    )
    first_service.lookup("RJ01609020")
    with first_database.session() as session:
        row = session.get(WorkMetadataCache, "RJ01609020")
        assert row is not None
        row.translation_json = "not-json"
        session.commit()
    first_database.dispose()

    second_provider = SequencedProvider([DlsiteParseError("provider contract changed")])
    second_database, _store, second_service = open_service(
        path,
        second_provider,
        MutableClock(start + timedelta(minutes=1)),
    )

    with pytest.raises(LookupFailure) as caught:
        second_service.lookup("RJ01609020")

    assert caught.value.kind is LookupFailureKind.RESPONSE
    assert second_provider.calls == ["RJ01609020"]
    second_database.dispose()
