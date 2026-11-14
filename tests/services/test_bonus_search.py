from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text

from dlsite_organizer.app.settings import AppSettings, CacheSettings
from dlsite_organizer.domain.bonus import (
    AJAX_TITLE_CROSS_VALIDATION,
    BonusEvidence,
    BonusEvidenceSnapshot,
)
from dlsite_organizer.domain.bonus_coverage import estimate_bonus_coverage
from dlsite_organizer.domain.manual_review import CandidateReviewOutcome, ManualRelationType
from dlsite_organizer.domain.relation import Confidence
from dlsite_organizer.domain.work import Work
from dlsite_organizer.persistence.database import Database, WorkObservation
from dlsite_organizer.persistence.manual_reviews import ManualReviewRepository
from dlsite_organizer.persistence.metadata_store import LimitedBonusRelationRecord, MetadataStore
from dlsite_organizer.providers.dlsite.client import DLSITE_PRODUCT_INFO_AJAX, DlsiteProvider
from dlsite_organizer.providers.dlsite.sources import parse_product_info_ajax
from dlsite_organizer.services import bonus_search
from dlsite_organizer.services.bonus_search import (
    BonusSearchRequest,
    BonusSearchService,
    bonus_range_count,
    bonus_range_worknos,
)
from dlsite_organizer.services.lookup import LookupFreshness, LookupService
from dlsite_organizer.services.manual_reviews import ManualReviewService
from dlsite_organizer.services.naming import NamingService

MAIN = "RJ01690645"
BONUS = "RJ01690654"
OTHER_MAIN = "RJ01690646"
OTHER_BONUS = "RJ01690655"
MAKER = "RG01061132"
DAY = date(2026, 8, 24)
PRE = datetime(2026, 8, 25, tzinfo=UTC)
POST = datetime(2026, 11, 13, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "dlsite"


def _listing(title="Bonus", *, day=DAY, maker=MAKER, **changes):
    return {
        "work_name": title, "maker_id": maker, "regist_date": str(day),
        "is_sale": False, "is_free": True, "is_oly": True,
        "price": 0, "wishlist_count": 1, "bonuses": [], **changes,
    }


def _provider(products, calls, *, offline=False):
    def response(request):
        if not request.url.path.endswith("/ajax"):
            if offline:
                raise httpx.ConnectError("offline", request=request)
            return httpx.Response(404)
        query = request.url.params["product_id"]
        calls.append(query)
        if offline:
            raise httpx.ConnectError("offline", request=request)
        if isinstance(product := products.get(query), httpx.Response):
            return product
        return httpx.Response(200, json={
            code: products[code] for code in query.split(",") if code in products
        })

    return DlsiteProvider(client_factory=lambda: httpx.Client(
        transport=httpx.MockTransport(response), follow_redirects=True,
    ))


def _store(tmp_path):
    database = Database(tmp_path / "metadata.sqlite3")
    database.initialize()
    return database, MetadataStore(database)


def _service(store, products, calls, now=POST, manual=None, *, days=7):
    return BonusSearchService(
        _provider(products, calls), store, manual, clock=lambda: now, cache_ttl=timedelta(hours=24),
        date_tolerance_days=days,
    )


def _search(
    service, *, main=MAIN, candidate="", cancelled=lambda: False, offsets=None,
    online=False,
):
    return service.search(
        BonusSearchRequest(main, MAKER, DAY, candidate, offsets, continue_online=online),
        cancelled=cancelled, progress=lambda _: None,
    )


def _known(store, code, title="Bonus", day=DAY):
    work = Work(workno=code, title=title, maker_id=MAKER, release_date=day, cvs=["Preserve"])
    store.save(work, None, source="RICH", fetched_at=PRE)
    return work


def test_title_match_uses_capture_date_and_survives_expiry(tmp_path):
    database, store = _store(tmp_path)
    before = (FIXTURES / f"product_info_{MAIN}_pre_expiry.json").read_text(encoding="utf-8")
    after = (FIXTURES / f"product_info_{MAIN}_post_expiry.json").read_text(encoding="utf-8")
    pre_entry = parse_product_info_ajax(before, MAIN).bonus_evidence
    assert pre_entry is not None
    title = pre_entry.entries[0].title
    assert title is not None
    # The checked-in captures redact titles independently. Use a synthetic exact match.
    original = _known(store, BONUS, title)
    folder = tmp_path / f"[{MAIN}] existing"
    folder.mkdir()
    sentinel = folder / "original.txt"
    sentinel.write_bytes(b"preserve")
    calls = []
    pre = _service(
        store, {MAIN: httpx.Response(200, text=before), BONUS: _listing(title)}, calls, PRE
    )
    active = _search(pre)
    assert calls == [MAIN, BONUS]
    assert len(active.matches) == 1
    match = active.matches[0]
    assert match.relation.confidence is Confidence.HIGH
    assert match.relation.detection_source == AJAX_TITLE_CROSS_VALIDATION
    assert match.observed_at == PRE
    assert match.entry and match.entry.end_at == datetime(2026, 9, 6, 23, 59, 59)
    cached = store.get(BONUS)
    assert cached and cached.work == original

    calls.clear()
    post = _service(store, {MAIN: httpx.Response(200, text=after)}, calls)
    assert _search(post).matches == (match,)
    assert calls == []
    assert _search(post, online=True).matches == (match,)
    assert calls == [MAIN]
    assert store.list_bonus_observations(MAIN)[-1].evidence == BonusEvidenceSnapshot()
    assert store.list_limited_bonus_matches(MAIN) == (match,)
    assert sentinel.read_bytes() == b"preserve"
    assert list(folder.iterdir()) == [sentinel]
    database.dispose()

    reopened, store = _store(tmp_path)
    offline = LookupService(
        _provider({}, calls, offline=True),
        NamingService(), metadata_store=store, clock=lambda: POST,
    )
    calls.clear()
    for code in (MAIN, BONUS):
        result = offline.lookup(code, prefer_bonus_review=True)
        assert result.has_local_bonus_relation
        assert result.freshness is LookupFreshness.CACHE_STALE_FALLBACK
        (pair,) = offline.bonus_search().pairs_for_work(code, as_bonus=code == BONUS)
        assert pair.automatically_linked and not pair.pending and pair.match == match
    assert set(calls) == {MAIN, BONUS}
    with reopened.session() as session:
        assert session.execute(text("PRAGMA integrity_check")).scalar_one() == "ok"
        assert not session.scalars(select(WorkObservation)).all()
    reopened.dispose()


@pytest.fixture
def no_scan_delay(monkeypatch):
    monkeypatch.setattr(bonus_search, "BONUS_REQUEST_INTERVAL", 0.0)


pytestmark = pytest.mark.usefixtures("no_scan_delay")


@pytest.mark.parametrize(("main", "bonus", "day", "entries", "confidence"), [
    (
        "RJ01741384", "RJ01741449", date(2026, 11, 6),
        [{"title": "期間限定4大特典", "end_date": "2026-11-19 23:59:59"}], Confidence.HIGH,
    ),
    ("RJ01468440", "RJ01468442", date(2025, 10, 5), [], Confidence.LOW),
])
def test_default_range_discovers_unknown_rj_and_reuses_observations(
    tmp_path, main, bonus, day, entries, confidence
):
    database, store = _store(tmp_path)
    calls = []
    maker = "RG42470" if entries else "RG49556"
    title = entries[0]["title"] if entries else "Historical bonus"
    ordinary, translation, other_bonus = bonus_range_worknos(main, (3, 5))
    original_flags = {"is_original": True, "is_child": False, "is_parent": False}
    service = _service(store, {
        main: _listing("Main", day=day, maker=maker, is_sale=True, bonuses=entries),
        bonus: _listing(title, day=day, maker=maker, translation_info=original_flags),
        ordinary: _listing(title, day=day, maker=maker, is_oly=False),
        translation: _listing(
            title, day=day, maker=maker,
            translation_info={"is_child": True, "original_workno": main},
        ),
        other_bonus: _listing("Other bonus", day=day, maker=maker),
    }, calls)
    first = _search(service, main=main, offsets=(1, 100))
    assert first.scan is not None and not first.scan.complete
    assert first.scan.requests == len(calls) == 21
    assert first.scan.submitted == 100
    assert first.scan.verified == 4
    assert first.scan.missing == 96
    assert {match.relation.target_workno: match.relation.confidence for match in first.matches} == {
        bonus: confidence,
        other_bonus: Confidence.LOW,
    }
    calls.clear()
    local = _search(service, main=main, offsets=(1, 100))
    assert local.used_local_results and local.review_deferred and local.matches == first.matches
    assert local.scan and local.scan.cache_hits == 4
    assert calls == []
    repeated = _search(service, main=main, offsets=(1, 100), online=True)
    assert repeated.matches == first.matches
    assert repeated.scan is not None and repeated.scan.cache_hits == 4
    assert not {main, bonus, other_bonus, ordinary, translation}.intersection(
        code for query in calls for code in query.split(",")
    )
    assert set(m.relation.target_workno for m in store.list_limited_bonus_matches(main)) == {
        bonus, other_bonus,
    }
    database.dispose()


@pytest.mark.parametrize("outcome", [403, 429, "malformed", "cancelled", "budget", "timeout"])
def test_range_partial_results_and_progress(tmp_path, monkeypatch, outcome):
    database, store = _store(tmp_path)
    calls = []
    codes = tuple(bonus_range_worknos(MAIN, (1, 20)))
    failing = ",".join(codes[5:10])
    if outcome == "budget":
        monkeypatch.setattr(bonus_search, "MAX_BATCH_REQUESTS", 1)
    products: dict[str, Any] = {
        MAIN: _listing("Main", is_sale=True),
        codes[0]: _listing(),
    }
    if isinstance(outcome, int):
        products[failing] = httpx.Response(outcome)
    elif outcome == "malformed":
        products[failing] = httpx.Response(200, json={
            codes[5]: _listing("Another bonus"),
            codes[6]: _listing(product_id=codes[7]),
        })
    provider = _provider(products, calls)
    if outcome == "timeout":
        def raise_timeout(request: httpx.Request) -> httpx.Response:
            calls.append(request.url.params["product_id"])
            if calls[-1] == failing:
                raise httpx.ReadTimeout("timeout", request=request)
            return httpx.Response(200, json={
                code: products[code] for code in calls[-1].split(",") if code in products
            })

        provider = DlsiteProvider(client_factory=lambda: httpx.Client(
            transport=httpx.MockTransport(raise_timeout)
        ))
    service = BonusSearchService(
        provider, store, None, clock=lambda: POST, cache_ttl=timedelta(hours=24),
    )
    result = _search(
        service, offsets=(1, 20), cancelled=lambda: outcome == "cancelled" and len(calls) >= 2,
    )
    scan = result.scan
    assert scan is not None and not scan.complete
    assert result.cancelled == (outcome == "cancelled")
    assert scan.requests == len(calls) == (2 if outcome in {"cancelled", "budget"} else 3)
    assert scan.submitted == (len(calls) - 1) * 5
    assert scan.verified == len(result.matches) == (2 if outcome == "malformed" else 1)
    assert scan.malformed == (1 if outcome == "malformed" else 0)
    assert scan.failed == (5 if outcome in {403, 429, "timeout"} else 0)
    assert result.matches[0].relation.target_workno == codes[0]
    if outcome != "budget":
        calls.clear()
        _search(service, offsets=(1, 20), online=True)
        first_unchecked = 5 if outcome == "cancelled" else 10
        assert calls[0].split(",") == list(codes[first_unchecked:first_unchecked + 5])
    database.dispose()


@pytest.mark.parametrize(
    ("offsets", "count"), [((-50, -1), 50), ((-50, 100), 150), ((1, 100), 100)]
)
def test_signed_range_submits_each_candidate_once_up_to_budget(tmp_path, offsets, count):
    database, store = _store(tmp_path)
    codes = tuple(bonus_range_worknos(MAIN, offsets))
    calls = []
    service = _service(store, {MAIN: _listing("Main", is_sale=True)}, calls)
    result = _search(service, offsets=offsets)
    scan = result.scan
    assert scan and scan.offsets == offsets
    assert len(set(codes)) == count and MAIN not in codes
    assert calls[0] == MAIN
    assert tuple(code for call in calls[1:] for code in call.split(",")) == codes[:100]
    assert all(len(call.split(",")) <= 5 for call in calls[1:])
    assert scan.submitted == scan.missing == min(count, 100)
    assert scan.verified == 0 and not scan.complete
    assert scan.unchecked == max(0, count - 100)
    assert any("范围请求上限" in notice for notice in result.notices) == (count > 100)
    database.dispose()


@pytest.mark.parametrize("cache_enabled", [True, False])
def test_continue_large_range_prioritizes_unchecked_and_reuses_successes(tmp_path, cache_enabled):
    database, store = _store(tmp_path)
    codes = tuple(bonus_range_worknos(MAIN, (1, 500)))
    gap = codes[0]
    products = dict.fromkeys(codes[1:], _listing("Ordinary", is_oly=False, is_sale=True))
    products[MAIN] = _listing("Main", is_sale=True)
    calls = []
    now = POST
    provider = _provider(products, calls)
    session = bonus_search.BonusScanSession()

    def search(offsets, *, main=MAIN):
        return _search(BonusSearchService(
            provider, store if cache_enabled else None, None,
            clock=lambda: now, cache_ttl=timedelta(hours=24), scan_session=session,
        ), main=main, offsets=offsets, online=True)

    first = search((1, 500))
    assert first.scan and (first.scan.verified, first.scan.missing, first.scan.unchecked) == (
        99, 1, 400,
    )
    calls.clear()
    # The expanded interval covers nearly the whole supported RJ space.
    end = 9_999_999_999 - int(MAIN[2:])
    expanded = search((1, end))
    assert expanded.scan and not expanded.scan.complete
    assert expanded.scan.last_workno == "RJ9999999999"
    assert expanded.scan.requested == end and expanded.scan.cache_hits == 99
    batches = [call for call in calls if call != MAIN]
    assert tuple(code for call in batches for code in call.split(",")) == codes[100:200]

    for start in range(200, 500, 100):
        calls.clear()
        scan = search((1, 500)).scan
        assert scan and not scan.complete and scan.verified == start + 99
        assert calls[-20:] == [",".join(codes[i:i + 5]) for i in range(start, start + 100, 5)]

    calls.clear()
    assert (small := search((2, 100)).scan) and small.complete and small.cache_hits == 99
    assert all(call == MAIN for call in calls)
    if cache_enabled:
        assert calls == []
    calls.clear()
    products[gap] = products[codes[1]]
    finished = search((1, 500))
    assert finished.scan and finished.scan.complete and finished.scan.cache_hits == 499
    assert [call for call in calls if call != MAIN] == [gap]

    if cache_enabled:
        restarted = _search(_service(store, products, []), offsets=(1, 500), online=True)
        assert restarted.scan and restarted.scan.complete and restarted.requests == 0
    else:
        other = search((1, 1), main=codes[0])
        assert other.scan and other.scan.cache_hits == 0 and other.scan.verified == 1
    now += timedelta(days=1)
    expired = search((1, 100))
    assert expired.scan and expired.scan.cache_hits == 0 and expired.scan.submitted == 100
    with database.session() as db_session:
        assert db_session.execute(text("PRAGMA integrity_check")).scalar() == "ok"
    database.dispose()


def test_request_spacing_survives_resume_and_deadline_stops_new_requests(monkeypatch):
    clock = 0.0

    def sleep_for(seconds):
        nonlocal clock
        clock += seconds

    monkeypatch.setattr(bonus_search, "monotonic", lambda: clock)
    monkeypatch.setattr(bonus_search, "sleep", sleep_for)
    monkeypatch.setattr(bonus_search, "BONUS_REQUEST_INTERVAL", 10.0)
    monkeypatch.setattr(bonus_search, "BONUS_SEARCH_SECONDS", 25.0)
    calls = []
    codes = tuple(bonus_range_worknos(MAIN, (1, 30)))
    service = _service(None, {MAIN: _listing("Main", is_sale=True)}, calls)
    first = _search(service, offsets=(1, 30))
    assert first.scan and first.scan.submitted == 10 and clock == 25.0
    assert any("时间上限" in notice for notice in first.notices)
    second = _search(service, offsets=(1, 30), online=True, cancelled=lambda: len(calls) >= 4)
    assert second.cancelled and second.requests == 1
    assert clock == 30.0 and calls == [MAIN, ",".join(codes[:5]), ",".join(codes[5:10]), MAIN]


@pytest.mark.parametrize(("end", "expected"), [
    (10, 10.93), (20, 20.04), (50, 28.54), (100, 30.77), (500, 33.20),
    (1000, 34.41), (5000, 49.19), (10_000, 57.29), (20_000, 72.47), (40_000, 91.30),
    (50_000, 95.55), (60_000, 97.37), (80_000, 98.18), (100_000, 98.79),
    (120_000, 98.99), (140_000, 99.39), (165_701, 99.60), (172_074, 99.80),
])
def test_historical_coverage_uses_monotone_positive_sample(end, expected):
    assert estimate_bonus_coverage((1, end)) == expected
    before = estimate_bonus_coverage((1, end - 1))
    last_only = estimate_bonus_coverage((end, end))
    assert before is not None and before < expected
    assert last_only is not None and 0 < last_only < expected
    assert estimate_bonus_coverage((-20, -1)) is None
    assert estimate_bonus_coverage((-2, end)) == expected
    old_coverage = estimate_bonus_coverage((1, 40_000))
    next_coverage = estimate_bonus_coverage((1, 40_001))
    assert old_coverage is not None and next_coverage is not None
    assert next_coverage > old_coverage
    assert estimate_bonus_coverage((1, 200_000)) == 99.80
    assert estimate_bonus_coverage((1, 300_000)) == 99.80
    assert estimate_bonus_coverage((200_000, 300_000)) == 0.0
    assert estimate_bonus_coverage((-18_545, 172_074)) == 100.0
    assert estimate_bonus_coverage((-18_546, 300_000)) == 100.0
    assert estimate_bonus_coverage((-18_544, 300_000)) == 99.80
    values: list[float] = []
    for end in (40_000, 50_000, 60_000, 100_000, 172_074, 200_000):
        value = estimate_bonus_coverage((1, end))
        assert value is not None and 0 <= value <= 100
        values.append(value)
    assert values == sorted(values)


def test_scan_cache_toggle_and_ordinary_lookup_share_current_cache(tmp_path):
    database, store = _store(tmp_path)
    manual = ManualReviewService(ManualReviewRepository(database))
    calls = []
    ordinary, other_ordinary = bonus_range_worknos(MAIN, (1, 2))
    explicit = next(iter(bonus_range_worknos(MAIN, (3, 3))))
    later_explicit = next(iter(bonus_range_worknos(MAIN, (4, 4))))
    products = {
        MAIN: _listing("Main", is_sale=True, bonuses=[{"workno": explicit}]),
        ordinary: _listing("Ordinary", is_oly=False),
        other_ordinary: _listing("Other ordinary", is_oly=False),
        explicit: _listing(),
        later_explicit: _listing("Later bonus"),
    }

    def response(request):
        if not request.url.path.endswith("/ajax"):
            calls.append(request.url.path)
            return httpx.Response(404)
        query = request.url.params["product_id"]
        calls.append(query)
        return httpx.Response(200, json={
            code: products[code] for code in query.split(",") if code in products
        })

    provider = DlsiteProvider(client_factory=lambda: httpx.Client(
        transport=httpx.MockTransport(response),
    ))
    settings = AppSettings()
    lookup = LookupService(
        provider, NamingService(), metadata_store=store, clock=lambda: POST,
        settings=settings, manual_review_service=manual,
    )
    _search(lookup.bonus_search(), offsets=(1, 1))
    calls.clear()
    result = lookup.lookup(ordinary)
    assert result.freshness is LookupFreshness.CACHE_FRESH and result.work.title == "Ordinary"
    assert calls == []
    assert lookup.lookup(ordinary, force_refresh=True).freshness is LookupFreshness.LIVE
    assert calls[0] == ordinary

    manual.submit_review(
        MAIN, BONUS, CandidateReviewOutcome.RELATED,
        relation_type=ManualRelationType.LIMITED_BONUS_OF, subject_workno=BONUS,
        target_workno=MAIN, evidence_snapshot={},
    )
    lookup.apply_settings(settings.model_copy(update={
        "cache": CacheSettings(save_bonus_scan_metadata=False),
    }))
    calls.clear()
    local = _search(lookup.bonus_search(), offsets=(1, 2))
    assert local.used_local_results and calls == []
    assert any(m.relation.target_workno == BONUS for m in local.matches)
    online = _search(lookup.bonus_search(), offsets=(1, 2), online=True)
    assert online.scan and online.scan.cache_hits == 1 and online.scan.complete
    assert calls == [other_ordinary]
    assert store.get(other_ordinary) is None and store.list_observations(other_ordinary) == ()
    assert any(m.relation.target_workno == BONUS for m in online.matches)
    assert store.list_limited_bonus_matches(MAIN)[0].relation.target_workno == explicit
    products[MAIN] = _listing("Main", is_sale=True, bonuses=[{"workno": later_explicit}])
    lookup.lookup(MAIN, force_refresh=True)
    discovered = _search(lookup.bonus_search(), offsets=(4, 4), online=True)
    assert discovered.scan and discovered.scan.verified == 1
    assert store.get(later_explicit) is None
    assert {m.relation.target_workno for m in store.list_limited_bonus_matches(MAIN)} == {
        explicit, later_explicit,
    }
    assert manual.reviews_for_work(MAIN)[0].subject_workno == BONUS
    with database.session() as session:
        assert session.execute(text("PRAGMA integrity_check")).scalar_one() == "ok"
        assert session.scalars(select(WorkObservation)).all() == []
    database.dispose()


def test_local_preview_distinguishes_fresh_and_historical_campaigns(tmp_path):
    database, store = _store(tmp_path)
    calls = []
    products = {MAIN: _listing("Main", bonuses=[{"title": "Bonus", "end_date": "2099-01-01"}])}
    lookup = LookupService(
        _provider(products, calls), NamingService(), metadata_store=store, clock=lambda: PRE,
    )
    _search(lookup.bonus_search())
    calls.clear()
    preview = lookup.preview_bonuses(lookup.lookup(MAIN))
    assert preview and preview.fresh_campaign_at == PRE and calls == []
    later = POST
    historical_lookup = LookupService(
        _provider({}, calls), NamingService(), metadata_store=store, clock=lambda: later,
    )
    historical = historical_lookup.bonus_search().search(
        BonusSearchRequest(MAIN, MAKER, DAY, local_only=True),
        cancelled=lambda: False, progress=lambda _: None,
    )
    assert len(historical.campaigns) == 1 and historical.fresh_campaign_at is None
    assert historical.campaigns[0].entry.activity_status(
        later, recently_reported=False
    ) == "状态未知"
    assert calls == []
    database.dispose()


def test_range_keeps_leading_zeros_and_handles_old_number_width():
    assert tuple(bonus_range_worknos("RJ000001", (1, 2))) == ("RJ000002", "RJ000003")
    assert tuple(bonus_range_worknos("RJ999998", (1, 2))) == ("RJ999999", "RJ01000000")
    assert tuple(bonus_range_worknos("RJ01000000", (-2, 1))) == (
        "RJ999998", "RJ999999", "RJ01000001",
    )
    assert tuple(bonus_range_worknos("RJ01741384", (65, 65))) == ("RJ01741449",)
    with pytest.raises(ValueError):
        bonus_range_worknos("RJ01741384", (2, 1))
    with pytest.raises(ValueError):
        bonus_range_worknos("RJ9999999999", (1, 1))
    with pytest.raises(ValueError):
        bonus_range_worknos("RJ000001", (-1, 1))
    with pytest.raises(ValueError):
        bonus_range_worknos(MAIN, (0, 0))
    assert bonus_range_count(MAIN, (-10_001, 40_000)) == 50_001
    assert bonus_range_count(MAIN, (-1500, -1498)) == 3


@pytest.mark.parametrize("days", [0, 3, 7, 14, 30])
def test_local_date_tolerance_filters_candidates_without_adding_online_requests(tmp_path, days):
    database, store = _store(tmp_path)
    fallback_day = DAY + timedelta(days=days)
    ordinary, translated, other_maker, too_far = (
        "RJ01690656", "RJ01690657", "RJ01690658", "RJ01690659"
    )
    for code in (BONUS, ordinary, translated, other_maker):
        _known(store, code)
    _known(store, OTHER_BONUS, day=fallback_day)
    _known(store, too_far, day=fallback_day + timedelta(days=1))
    products = {
        MAIN: _listing("Main", is_sale=True),
        BONUS: _listing(),
        ordinary: _listing(is_oly=False),
        translated: _listing(translation_info={"is_child": True, "original_workno": MAIN}),
        other_maker: _listing(maker="RG99999"),
        OTHER_BONUS: _listing(day=fallback_day),
    }
    calls = []
    service = _service(store, products, calls, days=days)
    initial = _search(service)
    assert {m.relation.target_workno for m in initial.matches} == (
        {BONUS} if days else {BONUS, OTHER_BONUS}
    )
    if days:
        assert OTHER_BONUS not in calls and too_far not in calls
        # An already obtained listing is eligible at the exact tolerance boundary.
        _search(service, candidate=OTHER_BONUS, online=True)
    calls.clear()
    extended = _search(service, online=True)
    assert OTHER_BONUS not in calls and too_far not in calls
    assert {m.relation.target_workno for m in extended.matches} == {BONUS, OTHER_BONUS}
    assert all(m.relation.confidence is Confidence.LOW for m in extended.matches)
    assert len(store.list_limited_bonus_matches(MAIN)) == 2
    database.dispose()


def test_narrowing_tolerance_keeps_manual_decisions_and_saved_links(tmp_path):
    database, store = _store(tmp_path)
    manual = ManualReviewService(ManualReviewRepository(database))
    calls = []
    pending, accepted, rejected = OTHER_BONUS, "RJ01690656", "RJ01690657"
    different = _listing("Other bonus", day=DAY + timedelta(days=3))
    service = _service(store, {
        MAIN: _listing("Main", bonuses=[{"title": "Bonus", "end_date": "2026-09-06"}]),
        BONUS: _listing(),
        pending: different,
        accepted: different,
        rejected: different,
    }, calls, PRE, manual, days=7)
    result = _search(service, offsets=(9, 12), online=True)
    pairs = {match.relation.target_workno: match for match in result.matches}
    service.review_match(pairs[accepted], CandidateReviewOutcome.RELATED)
    service.review_match(pairs[rejected], CandidateReviewOutcome.NOT_RELATED)
    strict = _service(store, {}, calls, PRE, manual, days=0)
    calls.clear()
    pairs = {pair.match.relation.target_workno: pair for pair in strict.pairs_for_work(MAIN)}
    assert pending not in pairs
    assert pairs[accepted].confirmed and pairs[rejected].rejected
    assert BONUS in pairs
    assert len(store.list_limited_bonus_matches(MAIN)) == 4 and calls == []
    database.dispose()


def test_persists_many_to_many_and_verifies_previously_unknown_exact_rjs(tmp_path):
    database, store = _store(tmp_path)
    calls = []
    products = {
        MAIN: _listing(
            "Main", is_sale=True, bonuses=[{"workno": BONUS}, {"product_id": OTHER_BONUS}]
        ),
        OTHER_MAIN: _listing("Other main", is_sale=True, bonuses=[{"workno": BONUS}]),
        BONUS: _listing(),
        OTHER_BONUS: _listing("Other bonus"),
    }
    service = _service(store, products, calls)
    first = _search(service)
    second = _search(service, main=OTHER_MAIN)
    assert [m.relation.target_workno for m in first.matches] == [BONUS, OTHER_BONUS]
    assert [m.relation.target_workno for m in second.matches] == [BONUS, OTHER_BONUS]
    assert calls == [MAIN, BONUS, OTHER_BONUS, OTHER_MAIN]
    assert all(match.relation.confidence is Confidence.CONFIRMED for match in first.matches)
    assert second.matches[0].relation.confidence is Confidence.CONFIRMED
    assert second.matches[1].relation.confidence is Confidence.LOW
    assert len(store.list_limited_bonus_matches(MAIN)) == 2
    assert len(store.list_limited_bonus_matches(OTHER_MAIN)) == 2
    assert _search(service).matches == first.matches
    with database.session() as session:
        assert len(session.scalars(select(LimitedBonusRelationRecord)).all()) == 4
    database.dispose()


def test_manual_rj_and_existing_manual_review(tmp_path):
    database, store = _store(tmp_path)
    manual = ManualReviewService(ManualReviewRepository(database))
    manual.submit_review(
        MAIN, BONUS, CandidateReviewOutcome.RELATED,
        relation_type=ManualRelationType.LIMITED_BONUS_OF, subject_workno=BONUS,
        target_workno=MAIN, evidence_snapshot={},
    )
    calls = []
    service = _service(
        store, {MAIN: _listing("Main"), OTHER_BONUS: _listing()}, calls, manual=manual
    )
    result = _search(service, candidate=OTHER_BONUS.lower())
    assert calls == [MAIN, OTHER_BONUS]
    assert result.matches[0].relation.confidence is Confidence.CONFIRMED
    assert result.matches[0].relation.detection_source == "MANUAL_USER_REVIEW"
    assert result.matches[1].relation.confidence is Confidence.LOW
    assert result.matches[1].relation.target_workno == OTHER_BONUS
    database.dispose()


def test_directional_reviews_survive_reopen_and_do_not_block_other_pairs(tmp_path):
    database, store = _store(tmp_path)
    manual = ManualReviewService(ManualReviewRepository(database))
    calls = []
    products = {
        MAIN: _listing("Main", is_sale=True, bonuses=[{"title": "Bonus"}]),
        OTHER_MAIN: _listing("Other main", is_sale=True),
        BONUS: _listing(),
        OTHER_BONUS: _listing("Another bonus"),
    }
    service = _service(store, products, calls, manual=manual)
    initial = _search(service, offsets=(9, 9))
    (candidate,) = initial.matches
    assert candidate.relation.confidence is Confidence.HIGH
    calls.clear()
    assert _search(service, offsets=(10, 10)).review_deferred
    assert calls == []
    service.review_match(candidate, CandidateReviewOutcome.NOT_RELATED)
    service.review_match(candidate, CandidateReviewOutcome.NOT_RELATED)
    assert len(manual.reviews_for_work(MAIN)) == 1
    assert store.get(BONUS) is not None
    assert service.pairs_for_work(BONUS, as_bonus=True)[0].rejected
    another = _search(service, offsets=(9, 10))
    assert [m.relation.target_workno for m in another.matches] == [OTHER_BONUS]
    assert calls == [OTHER_BONUS]
    calls.clear()
    service.review_match(another.matches[0], CandidateReviewOutcome.RELATED)
    service.review_match(candidate, CandidateReviewOutcome.UNSURE)
    assert _search(service, offsets=(10, 11)).review_deferred
    assert calls == []
    service.review_match(candidate, CandidateReviewOutcome.RELATED)
    service.review_match(candidate, CandidateReviewOutcome.RELATED)
    assert len(manual.reviews_for_work(MAIN)) == 4
    other_parent = _search(service, main=OTHER_MAIN)
    shared = next(m for m in other_parent.matches if m.relation.target_workno == BONUS)
    service.review_match(shared, CandidateReviewOutcome.RELATED)
    calls.clear()
    service.review_match(candidate, CandidateReviewOutcome.UNSURE)
    service.review_match(candidate, CandidateReviewOutcome.UNSURE)
    assert len(manual.reviews_for_work(MAIN)) == 5
    assert calls == []
    database.dispose()

    reopened, store = _store(tmp_path)
    service = _service(
        store, {}, calls, manual=ManualReviewService(ManualReviewRepository(reopened)),
    )
    assert {
        p.match.relation.target_workno for p in service.pairs_for_work(MAIN) if p.confirmed
    } == {OTHER_BONUS}
    assert {
        p.match.relation.source_workno for p in service.pairs_for_work(BONUS, as_bonus=True)
        if p.confirmed
    } == {OTHER_MAIN}
    (outgoing,) = tuple(p for p in service.pairs_for_work(MAIN) if p.pending)
    (incoming,) = tuple(p for p in service.pairs_for_work(BONUS, as_bonus=True) if p.pending)
    assert outgoing.match == incoming.match == candidate
    assert store.get(BONUS) is not None
    assert calls == []
    with reopened.session() as session:
        assert session.execute(text("PRAGMA integrity_check")).scalar_one() == "ok"
    reopened.dispose()


@pytest.mark.parametrize("status", [403, 429, 500, 302, 200])
def test_http_and_bad_responses_stop_requests_without_erasing_local_results(tmp_path, status):
    database, store = _store(tmp_path)
    _known(store, BONUS)
    _known(store, OTHER_BONUS)
    calls = []
    service = _service(store, {
        MAIN: _listing("Main", bonuses=[{"title": "Campaign", "end_date": "2026-09-06"}]),
        BONUS: httpx.Response(status, text="<html>unexpected</html>"),
        OTHER_BONUS: _listing(),
    }, calls)
    result = _search(service)
    assert calls == [MAIN, BONUS]
    assert result.matches == ()
    assert any("已停止在线查询" in notice for notice in result.notices)
    assert result.unmatched_entries[0].end_at == date(2026, 9, 6)
    database.dispose()


def test_cancel_in_flight_and_request_budget(tmp_path):
    database, store = _store(tmp_path)
    codes = [f"RJ016906{number}" for number in range(54, 62)]
    for code in codes:
        _known(store, code)
    calls = []
    products = {MAIN: _listing("Main"), **{code: _listing() for code in codes}}
    service = _service(store, products, calls)
    assert _search(service, cancelled=lambda: len(calls) == 2).cancelled
    assert calls == [MAIN, BONUS]
    assert store.latest_bonus_listings((BONUS,)) == {}
    calls.clear()
    result = _search(service)
    assert len(calls) == len(result.matches) == 5
    assert any("请求上限" in notice for notice in result.notices)
    database.dispose()


def test_normal_lookup_populates_bonus_identity_for_local_search(tmp_path):
    database, store = _store(tmp_path)
    calls = []

    def response(request):
        calls.append(request.url.path)
        if request.url.path.endswith("/ajax"):
            return httpx.Response(200, json={BONUS: _listing()})
        return httpx.Response(404)

    provider = DlsiteProvider(client_factory=lambda: httpx.Client(
        transport=httpx.MockTransport(response)
    ))
    lookup = LookupService(
        provider, NamingService("{workno}"), metadata_store=store, clock=lambda: PRE
    )
    assert lookup.lookup(BONUS).core_source == DLSITE_PRODUCT_INFO_AJAX
    observed = store.latest_bonus_listings((BONUS,))[BONUS].bonus_listing
    assert observed and observed.status.matches_bonus_profile
    assert observed.status.wishlist_count == 1
    database.dispose()


def test_empty_main_ajax_can_use_legacy_index_and_bonus_observation(tmp_path):
    database, store = _store(tmp_path)
    store.append_observation(
        Work(workno=MAIN, title="Main", maker_id=MAKER, release_date=DAY),
        None, source=DLSITE_PRODUCT_INFO_AJAX, observed_at=PRE,
        bonus_evidence=BonusEvidenceSnapshot(entries=(BonusEvidence(title="Bonus"),)),
    )
    _known(store, BONUS)
    calls = []
    result = _search(_service(store, {BONUS: _listing()}, calls))
    assert calls == [MAIN, BONUS]
    assert result.matches[0].relation.confidence is Confidence.HIGH
    assert result.matches[0].observed_at == PRE
    database.dispose()


def test_duplicate_titles_remain_candidates(tmp_path):
    database, store = _store(tmp_path)
    _known(store, BONUS)
    _known(store, OTHER_BONUS)
    calls = []
    service = _service(store, {
        MAIN: _listing("Main", is_sale=True, bonuses=[{
            "title": "Bonus", "end_date": "2099-01-01",
        }]),
        BONUS: _listing(),
        OTHER_BONUS: _listing(),
    }, calls)
    result = _search(service)
    assert len(result.matches) == 2
    assert all(match.relation.confidence is Confidence.LOW for match in result.matches)
    assert all(
        "多个特典候选" in match.relation.evidence[-1].description for match in result.matches
    )
    assert len(store.list_limited_bonus_matches(MAIN)) == 2
    database.dispose()


@pytest.mark.parametrize("condition", [
    "ended", "future", "unknown_deadline", "invalid_start", "empty", "fuzzy", "offset_date",
    "multiple_campaigns", "core_conflict", "stale", "superseded",
])
def test_active_title_requires_current_unambiguous_ajax_identity(tmp_path, condition):
    database, store = _store(tmp_path)
    calls = []
    entry = {"title": "Bonus", "end_date": "2026-09-06"}
    if condition == "ended":
        entry["end_date"] = "2026-08-23"
    elif condition == "future":
        entry["start_date"] = "2026-09-01"
    elif condition == "unknown_deadline":
        entry["end_date"] = "unknown"
    elif condition == "invalid_start":
        entry["start_date"] = "unknown"
    entries = [entry]
    if condition == "multiple_campaigns":
        entries.append({**entry, "end_date": "2026-09-07"})
    main = _listing("Main", is_sale=True, bonuses=[] if condition == "empty" else entries)
    candidate = _listing(
        "Bonus Extra" if condition == "fuzzy" else "Bonus",
        day=DAY + timedelta(days=1) if condition == "offset_date" else DAY,
    )
    products = {MAIN: main, BONUS: candidate}
    if condition == "core_conflict":
        store.save(
            Work(workno=BONUS, title="Bonus", maker_id="RG00001", release_date=DAY),
            None, source="RICH", fetched_at=PRE,
        )
    now = PRE
    if condition in {"stale", "superseded"}:
        _search(_service(store, {MAIN: main}, calls, PRE))
        if condition == "stale":
            now += timedelta(days=2)
            products = {BONUS: candidate}
        else:
            now += timedelta(minutes=1)
            # Write the newer empty observation through ordinary lookup.
            empty_lookup = LookupService(
                _provider({MAIN: _listing("Main")}, []), NamingService(),
                metadata_store=store, clock=lambda: now,
            )
            empty_lookup.lookup(MAIN, force_refresh=True)
    service = _service(store, products, calls, now)
    result = _search(service, offsets=(9, 9), online=True)
    assert len(result.matches) == 1
    assert not result.matches[0].automatically_linked
    assert service.pairs_for_work(MAIN)[0].pending
    database.dispose()


def test_independent_active_titles_link_many_to_many_with_manual_priority(tmp_path):
    database, store = _store(tmp_path)
    calls = []
    manual = ManualReviewService(ManualReviewRepository(database))
    entry = {"title": "Bonus", "end_date": "2026-09-06"}
    service = _service(store, {
        MAIN: _listing("Main", is_sale=True, bonuses=[entry, {**entry, "title": "Extra"}]),
        OTHER_MAIN: _listing("Other main", is_sale=True, bonuses=[entry]),
        BONUS: _listing(),
        OTHER_BONUS: _listing("Extra"),
    }, calls, PRE, manual)
    first = _search(service, offsets=(9, 10))
    assert len(first.matches) == 2 and all(m.automatically_linked for m in first.matches)
    second = _search(service, main=OTHER_MAIN, offsets=(8, 8))
    assert second.matches[0].automatically_linked
    assert len(service.pairs_for_work(BONUS, as_bonus=True)) == 2
    assert not manual.list_manual_reviews()

    auto = first.matches[0]
    service.review_match(auto, CandidateReviewOutcome.UNSURE)
    repeated = _search(service, offsets=(9, 10), online=True)
    assert repeated.matches == first.matches
    assert service.pairs_for_work(MAIN)[0].pending
    service.review_match(auto, CandidateReviewOutcome.NOT_RELATED)
    repeated = _search(service, offsets=(9, 10), online=True)
    assert repeated.matches == first.matches[1:]
    assert any("已保留人工排除" in notice for notice in repeated.notices)
    service.review_match(auto, CandidateReviewOutcome.RELATED)
    repeated = _search(service, offsets=(9, 10), online=True)
    assert repeated.matches[0].relation.detection_source == "MANUAL_USER_REVIEW"
    assert store.list_limited_bonus_matches(MAIN) == first.matches
    assert len(manual.list_manual_reviews()) == 3
    database.dispose()


@pytest.mark.parametrize("outcome", list(CandidateReviewOutcome))
def test_review_written_after_search_read_prevents_automatic_save(tmp_path, monkeypatch, outcome):
    database, store = _store(tmp_path)
    manual = ManualReviewService(ManualReviewRepository(database))
    service = _service(store, {
        MAIN: _listing("Main", is_sale=True, bonuses=[{
            "title": "Bonus", "end_date": "2026-09-06",
        }]),
        BONUS: _listing(),
    }, [], PRE, manual)
    save = store.save_limited_bonus_matches

    def save_after_review(matches):
        (match,) = matches
        assert match.automatically_linked
        service.review_match(match.model_copy(update={
            "relation": match.relation.model_copy(update={
                "detection_source": DLSITE_PRODUCT_INFO_AJAX,
            }),
        }), outcome)
        return save(matches)

    monkeypatch.setattr(store, "save_limited_bonus_matches", save_after_review)
    result = _search(service, offsets=(9, 9))
    (saved,) = store.list_limited_bonus_matches(MAIN)
    assert not saved.automatically_linked
    (pair,) = service.pairs_for_work(MAIN)
    assert pair.review and pair.review.outcome is outcome
    if outcome is CandidateReviewOutcome.NOT_RELATED:
        assert not result.matches and result.notices
    elif outcome is CandidateReviewOutcome.RELATED:
        assert result.matches[0].relation.detection_source == "MANUAL_USER_REVIEW"
    else:
        assert pair.pending and result.matches == (saved,)
    database.dispose()


def test_timeout_stops_online_search_and_keeps_local_candidates(tmp_path):
    database, store = _store(tmp_path)
    calls = []
    saved = _service(store, {MAIN: _listing("Main"), BONUS: _listing()}, calls, PRE)
    assert _search(saved, candidate=BONUS).matches

    def response(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.params["product_id"])
        assert request.extensions["timeout"]["read"] == 5.0
        raise httpx.ReadTimeout("timeout", request=request)

    calls.clear()
    provider = DlsiteProvider(client_factory=lambda: httpx.Client(
        transport=httpx.MockTransport(response)
    ))
    service = BonusSearchService(
        provider, store, None, clock=lambda: POST, cache_ttl=timedelta(hours=24)
    )
    result = _search(service, candidate=OTHER_BONUS)
    assert calls == [MAIN]
    assert any("超时" in notice for notice in result.notices)
    assert [match.relation.target_workno for match in result.matches] == [BONUS]
    database.dispose()
