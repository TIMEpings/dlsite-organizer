import json
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from dlsite_organizer.domain.work import Work
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.metadata_store import MetadataStore
from dlsite_organizer.providers.dlsite.client import DlsiteProvider
from dlsite_organizer.providers.dlsite.sources import TranslationInfoSource
from dlsite_organizer.services.lookup import LookupResult, LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.translation_discovery import (
    TranslationDetailStatus,
    TranslationListings,
)

ORIGINAL, PARENT, CHILD, SIBLING = "RJ01619571", "RJ01628429", "RJ01628430", "RJ01706442"
SIMPLIFIED, SIMPLIFIED_CHILD = "RJ01628427", "RJ01628428"
NOW = datetime(2026, 11, 13, tzinfo=UTC)


@pytest.fixture
def translation_lookup(tmp_path):
    fixture = Path(__file__).parents[1] / "fixtures" / "dlsite" / "translation_RJ01619571.json"
    products = json.loads(fixture.read_text(encoding="utf-8"))
    calls: list[tuple[str, ...]] = []

    def respond(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/ajax"):
            return httpx.Response(404)
        codes = tuple(request.url.params["product_id"].split(","))
        calls.append(codes)
        if isinstance(response := products.get(codes[-1]), httpx.Response):
            return response
        return httpx.Response(
            200, json={code: products[code] for code in codes if code in products}
        )

    database = Database(tmp_path / "translation.sqlite3")
    database.initialize()
    store = MetadataStore(database)
    provider = DlsiteProvider(
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(respond))
    )
    service = LookupService(provider, NamingService(), metadata_store=store, clock=lambda: NOW)
    return service, store, products, calls


def discover(service: LookupService, result: LookupResult) -> list[TranslationListings]:
    updates: list[TranslationListings] = []
    service.discover_translations(result, publish=updates.append, cancelled=lambda: False)
    return updates


def test_original_discovers_editions_translators_and_reuses_observations(translation_lookup):
    service, store, _, calls = translation_lookup
    result = service.lookup(ORIGINAL)
    assert not result.historical_relations.incoming
    assert [edge.target_workno for edge in result.relations] == [SIMPLIFIED, PARENT]
    updates = discover(service, result)
    assert [(row.workno, row.children_loaded) for row in updates[0].versions] == [
        (SIMPLIFIED, False),
        (PARENT, False),
    ]
    simplified, traditional = updates[-1].versions
    assert simplified.translators[0].workno == SIMPLIFIED_CHILD
    assert simplified.translators[0].name == "むしぞる中尉"
    assert [(item.workno, item.name) for item in traditional.translators] == [
        (CHILD, "むしぞる中尉"),
        (SIBLING, "Second translator"),
    ]
    assert calls == [(ORIGINAL,), (SIMPLIFIED, PARENT), (CHILD, SIBLING)]
    assert store.get(PARENT) is None
    calls.clear()
    repeated = discover(service, service.lookup(ORIGINAL))[-1]
    assert repeated.versions == updates[-1].versions
    assert calls == []


@pytest.mark.parametrize("code", [PARENT, CHILD])
def test_parent_and_child_find_original_and_same_language_translators(translation_lookup, code):
    service, _, _, calls = translation_lookup
    listings = discover(service, service.lookup(code))[-1]
    assert listings.original == ORIGINAL
    assert [version.workno for version in listings.versions] == [PARENT]
    version = listings.versions[0]
    assert version.language == "CHI_HANT"
    assert [child.workno for child in version.translators] == [CHILD, SIBLING]
    assert all(child.name for child in version.translators)
    assert len(calls) <= 3
    assert all(ORIGINAL not in batch for batch in calls)


def test_missing_parent_and_translator_preserve_explicit_ids(translation_lookup):
    service, _, products, calls = translation_lookup
    del products[SIMPLIFIED], products[SIBLING]
    listings = discover(service, service.lookup(ORIGINAL))[-1]
    missing_parent, known_parent = listings.versions
    assert missing_parent.workno == SIMPLIFIED
    assert not missing_parent.children_loaded
    assert known_parent.translators[-1].workno == SIBLING
    assert known_parent.translators[-1].name is None
    assert known_parent.translators[-1].detail_status is TranslationDetailStatus.UNAVAILABLE
    assert len(calls) == 3


@pytest.mark.parametrize(("changes", "expected"), [
    ({}, TranslationDetailStatus.AVAILABLE),
    ({"regist_date": "2099-11-14 00:00:00"}, TranslationDetailStatus.NOT_RELEASED),
    ({
        "is_reserve_work": True, "is_reservable": True, "is_sale": False, "regist_date": None,
    }, TranslationDetailStatus.NOT_RELEASED),
    ({"is_sale": False}, TranslationDetailStatus.AVAILABLE),
])
def test_child_release_status_requires_storefront_evidence(translation_lookup, changes, expected):
    service, _, products, _ = translation_lookup
    products[SIBLING].update(maker_name=None, maker_id=None, **changes)
    item = discover(service, service.lookup(ORIGINAL))[-1].versions[-1].translators[-1]
    assert item.workno == SIBLING and item.name is None
    assert item.detail_status is expected


@pytest.mark.parametrize("status", [403, 429, 503])
def test_failed_child_batch_preserves_relationships(translation_lookup, status):
    service, _, products, _ = translation_lookup
    products[SIBLING] = httpx.Response(status)
    items = discover(service, service.lookup(ORIGINAL))[-1].versions[-1].translators
    assert {item.workno for item in items} == {CHILD, SIBLING}
    assert all(item.detail_status is TranslationDetailStatus.FAILED for item in items)


def test_legacy_full_cache_is_upgraded_without_overwriting_work_metadata(translation_lookup):
    service, store, _, calls = translation_lookup
    original = Work(workno=ORIGINAL, title="Original", series_name="Rich series")
    store.save(
        original,
        TranslationInfoSource(is_original=True),
        source="legacy",
        fetched_at=NOW,
    )
    calls.clear()
    assert discover(service, service.lookup(ORIGINAL))[-1].versions
    assert calls[0] == (ORIGINAL,)
    calls.clear()
    assert discover(service, service.lookup(ORIGINAL))[-1].versions
    assert calls == []
    cached = store.get(ORIGINAL)
    assert cached and cached.work.series_name == "Rich series"


def test_cancelling_after_first_projection_skips_directed_requests(translation_lookup):
    service, _, _, calls = translation_lookup
    result = service.lookup(ORIGINAL)
    updates: list[TranslationListings] = []
    service.discover_translations(
        result,
        publish=updates.append,
        cancelled=lambda: bool(updates),
    )
    assert len(updates) == 1
    assert calls == [(ORIGINAL,)]
