"""User-initiated limited bonus discovery over local metadata and exact RJ lookups."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from itertools import batched, chain, pairwise
from threading import Lock
from time import monotonic, sleep
from typing import Protocol

from dlsite_organizer.domain.bonus import (
    AJAX_TITLE_CROSS_VALIDATION,
    DEFAULT_BONUS_DATE_TOLERANCE_DAYS,
    BonusCampaign,
    BonusEvidence,
    BonusEvidenceSnapshot,
    BonusListingSnapshot,
    BonusMatch,
)
from dlsite_organizer.domain.candidate import KnownWorkSnapshot
from dlsite_organizer.domain.manual_review import (
    CandidateEvidenceSnapshot,
    CandidateReviewOutcome,
    ManualRelationType,
    ManualReviewEvent,
    ManualReviewProvenance,
)
from dlsite_organizer.domain.relation import (
    Confidence,
    EvidenceType,
    RelationEvidence,
    RelationType,
    WorkRelation,
)
from dlsite_organizer.domain.work_code import WorkCode
from dlsite_organizer.persistence.metadata_store import MetadataObservation, MetadataStore
from dlsite_organizer.providers.dlsite.client import (
    BONUS_BATCH_SIZE,
    DLSITE_ENRICHED_SOURCE,
    DLSITE_PRODUCT_INFO_AJAX,
    BonusBatchLookup,
    DlsiteWorkLookup,
)
from dlsite_organizer.providers.dlsite.exceptions import (
    DlsiteConnectionError,
    DlsiteHttpError,
    DlsiteParseError,
    WorkNotFoundError,
)
from dlsite_organizer.providers.dlsite.sources import TranslationInfoSource
from dlsite_organizer.services.manual_reviews import ManualReviewService

MAX_CANDIDATE_REQUESTS = 5
DEFAULT_RANGE_END = 100
MAX_BATCH_REQUESTS = DEFAULT_RANGE_END // BONUS_BATCH_SIZE
BONUS_REQUEST_INTERVAL = 10.0
BONUS_SEARCH_SECONDS = 240.0
_AJAX_SOURCES = {DLSITE_PRODUCT_INFO_AJAX, DLSITE_ENRICHED_SOURCE}


class BonusLookupProvider(Protocol):
    @property
    def supports_bonus_range(self) -> bool: ...

    def fetch_bonus_lookup(self, workno: str) -> DlsiteWorkLookup: ...
    def fetch_bonus_batch(self, worknos: tuple[str, ...]) -> BonusBatchLookup: ...


@dataclass(frozen=True, slots=True)
class BonusSearchRequest:
    main_workno: str
    maker_id: str | None
    regist_date: date | None
    candidate_workno: str = ""
    offsets: tuple[int, int] | None = None
    continue_online: bool = False
    local_only: bool = False
    main_evidence: BonusEvidenceSnapshot | None = None
    main_ajax_at: datetime | None = None
    main_evidence_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class BonusScanProgress:
    first_workno: str
    last_workno: str
    requested: int
    submitted: int
    verified: int
    missing: int
    malformed: int
    cache_hits: int
    requests: int
    offsets: tuple[int, int]
    failed: int = 0
    verified_offsets: frozenset[int] = frozenset()

    @property
    def complete(self) -> bool:
        return self.verified == self.requested

    @property
    def unchecked(self) -> int:
        return self.requested - self.verified - self.missing - self.malformed - self.failed

    def describe(self) -> str:
        remaining = self.requested - self.verified
        return (
            f"已核验 {self.verified:,} / {self.requested:,} 个编号"
            f" · 待核验 {remaining:,} 个"
        )

    def describe_details(self) -> str:
        return (
            f"{self.first_workno} ～ {self.last_workno}\n"
            f"本次新核验 {self.verified - self.cache_hits:,} · 复用 {self.cache_hits:,}"
            f" · 提交 {self.submitted:,} 个范围编号 · 在线请求 {self.requests} 次\n"
            f"未返回 {self.missing:,} · 响应无效 {self.malformed:,}"
            f" · 请求失败 {self.failed:,} · 尚未检查 {self.unchecked:,}"
        )


@dataclass(frozen=True, slots=True)
class BonusSearchResult:
    main_workno: str
    matches: tuple[BonusMatch, ...] = ()
    notices: tuple[str, ...] = ()
    cancelled: bool = False
    unmatched_entries: tuple[BonusEvidence, ...] = ()
    scan: BonusScanProgress | None = None
    campaigns: tuple[BonusCampaign, ...] = ()
    fresh_campaign_at: datetime | None = None
    used_local_results: bool = False
    requests: int = 0
    evaluated_at: datetime | None = None
    review_deferred: bool = False

    @property
    def has_reliable_match(self) -> bool:
        return any(_reliable(match) for match in self.matches)


@dataclass(frozen=True, slots=True)
class BonusPair:
    """Provider evidence plus the user's latest decision for this direction."""

    match: BonusMatch
    review: ManualReviewEvent | None = None

    @property
    def rejected(self) -> bool:
        return bool(self.review and self.review.outcome is CandidateReviewOutcome.NOT_RELATED)

    @property
    def confirmed(self) -> bool:
        return bool(self.review and self.review.outcome is CandidateReviewOutcome.RELATED)

    @property
    def automatically_linked(self) -> bool:
        return self.review is None and self.match.automatically_linked

    @property
    def pending(self) -> bool:
        if self.review is not None:
            return self.review.outcome is CandidateReviewOutcome.UNSURE
        return not (
            self.automatically_linked or self.match.relation.confidence is Confidence.CONFIRMED
        )

    def effective_match(self) -> BonusMatch:
        if not self.confirmed:
            return self.match
        assert self.review is not None
        relation = self.match.relation.model_copy(update={
            "confidence": Confidence.CONFIRMED,
            "detection_source": ManualReviewProvenance.MANUAL_USER_REVIEW.value,
            "evidence": [
                *self.match.relation.evidence,
                RelationEvidence(
                    evidence_type=EvidenceType.OTHER, description="本机人工确认",
                    attributes={"review_id": self.review.id},
                ),
            ],
        })
        return self.match.model_copy(update={"relation": relation})


def _review_direction(review: ManualReviewEvent) -> tuple[str, str] | None:
    # Non-RELATED legacy events cannot store subject/target. Our scoped review
    # snapshots carry direction without changing the generic review contract.
    for context in review.evidence_snapshot.context:
        main, bonus = context.get("limited_bonus_main"), context.get("limited_bonus_candidate")
        if isinstance(main, str) and isinstance(bonus, str) and {main, bonus} == {
            review.workno_a, review.workno_b,
        }:
            return main, bonus
    if (review.relation_type is ManualRelationType.LIMITED_BONUS_OF
            and review.target_workno and review.subject_workno
            and review.target_workno.startswith("RJ") and review.subject_workno.startswith("RJ")):
        return review.target_workno, review.subject_workno
    return None


@dataclass(frozen=True, slots=True)
class _Listing:
    snapshot: BonusListingSnapshot
    translation: TranslationInfoSource | None
    observed_at: datetime
    source: str
    core_conflict: bool


class _Unverified(StrEnum):
    MISSING = "missing"
    MALFORMED = "malformed"
    FAILED = "failed"


class BonusScanSession:
    """Share unresolved attempts and request spacing across lookup snapshots."""

    def __init__(self) -> None:
        self.lock = Lock()
        self.request_lock = Lock()
        self.next_request = 0.0
        self.unverified: dict[str, dict[str, tuple[_Unverified, datetime]]] = {}
        self.listings: dict[str, dict[str, _Listing]] = {}

    def discard_expired(self, cutoff: datetime) -> None:
        # Called under lock. Persistent listings keep their existing SQLite TTL.
        for main, attempts in tuple(self.unverified.items()):
            for code, (_, observed) in tuple(attempts.items()):
                if observed <= cutoff:
                    del attempts[code]
            if not attempts:
                del self.unverified[main]
        for main, listings in tuple(self.listings.items()):
            for code, listing in tuple(listings.items()):
                if listing.observed_at <= cutoff:
                    del listings[code]
            if not listings:
                del self.listings[main]


def _rj(value: str) -> str:
    return str(WorkCode.parse(value, allowed_prefixes=("RJ",)))


@dataclass(frozen=True, slots=True)
class _BonusRange:
    base: int
    first: int
    last: int
    width: int
    legacy_widths: bool

    @classmethod
    def from_offsets(cls, main: str, offsets: tuple[int, int]) -> _BonusRange:
        first, last = offsets
        if type(first) is not int or type(last) is not int or first > last:
            raise ValueError("RJ 偏移须为整数，且起点须小于或等于终点。")
        if first == last == 0:
            raise ValueError("RJ 范围须包含至少 1 个候选编号（跳过偏移 0）。")
        code = WorkCode.parse(main, allowed_prefixes=("RJ",))
        base, width = int(code.digits), len(code.digits)
        if base + first < 1 or base + last >= 10 ** WorkCode.MAX_DIGITS:
            raise ValueError("搜索范围超出支持的 RJ 编号。")
        return cls(base, first, last, width, width == 6 or (width == 8 and base >= 1_000_000))

    def workno(self, number: int) -> str:
        width = (6 if number < 1_000_000 else 8) if self.legacy_widths else self.width
        return f"RJ{number:0{width}d}"

    def __len__(self) -> int:
        return self.last - self.first + 1 - (self.first <= 0 <= self.last)

    def __contains__(self, code: str) -> bool:
        digits = code[2:]
        if not digits.isascii() or not digits.isdigit():
            return False
        number = int(digits)
        offset = number - self.base
        return offset != 0 and self.first <= offset <= self.last and code == self.workno(number)

    def __iter__(self) -> Iterator[str]:
        for offset in range(self.first, self.last + 1):
            if offset != 0:
                yield self.workno(self.base + offset)

    @property
    def endpoints(self) -> tuple[str, str]:
        first, last = self.first or 1, self.last or -1
        return self.workno(self.base + first), self.workno(self.base + last)

    def listing_bounds(self) -> tuple[tuple[str, str], ...]:
        # SQLite orders work numbers lexically. Split where their formatted width changes.
        first, last = self.base + self.first, self.base + self.last
        boundaries = (
            10 ** digits for digits in range(WorkCode.MIN_DIGITS, WorkCode.MAX_DIGITS)
            if first < 10 ** digits <= last
        )
        starts = (first, *boundaries, last + 1)
        return tuple(
            (self.workno(lower), self.workno(upper - 1))
            for lower, upper in pairwise(starts)
        )


def bonus_range_count(main: str, offsets: tuple[int, int]) -> int:
    return len(_BonusRange.from_offsets(main, offsets))


def bonus_range_worknos(main: str, offsets: tuple[int, int] | None) -> Iterable[str]:
    if offsets is None:
        return ()
    return _BonusRange.from_offsets(main, offsets)


def _translated(info: TranslationInfoSource | None, code: str) -> bool:
    return bool(info and (
        info.is_parent or info.is_child or info.is_translation_bonus_child
        or info.parent_workno or info.child_worknos
        or (info.original_workno and info.original_workno != code)
    ))


def _local_listing(row: MetadataObservation) -> _Listing | None:
    snapshot = row.bonus_listing
    if snapshot is None:
        return None
    try:
        info = TranslationInfoSource.model_validate_json(row.translation_json) if (
            row.translation_json is not None
        ) else None
    except ValueError:
        return None
    return _Listing(
        snapshot, info, row.observed_at.replace(tzinfo=UTC), row.source,
        _identity_conflict(snapshot, row.title, row.maker_id, row.release_date),
    )


def _identity_conflict(
    snapshot: BonusListingSnapshot, title: str | None, maker: str | None, day: date | None,
) -> bool:
    return any(
        observed is not None and other is not None and observed != other
        for observed, other in (
            (snapshot.title, title), (snapshot.maker_id, maker), (snapshot.regist_date, day),
        )
    )


class BonusSearchService:
    def __init__(
        self,
        provider: BonusLookupProvider | None,
        store: MetadataStore | None,
        manual_reviews: ManualReviewService | None,
        *,
        clock: Callable[[], datetime],
        cache_ttl: timedelta,
        save_scan_metadata: bool = True,
        date_tolerance_days: int = DEFAULT_BONUS_DATE_TOLERANCE_DAYS,
        scan_session: BonusScanSession | None = None,
    ) -> None:
        self._provider = provider
        self._store = store
        self._manual_reviews = manual_reviews
        self._clock = clock
        self._cache_ttl = cache_ttl
        self._save_scan_metadata = save_scan_metadata
        self._date_tolerance_days = date_tolerance_days
        self._scan_session = scan_session if scan_session is not None else BonusScanSession()

    @property
    def can_review(self) -> bool:
        return self._manual_reviews is not None

    def pairs_for_work(
        self, code: str, *, as_bonus: bool = False,
        extra: tuple[BonusMatch, ...] = (),
    ) -> tuple[BonusPair, ...]:
        matches: dict[tuple[str, str], BonusMatch] = {}
        for match in (
            *(self._store.list_limited_bonus_matches(code, as_bonus=as_bonus)
              if self._store else ()),
            *extra,
        ):
            key = (match.relation.source_workno, match.relation.target_workno)
            if key[int(as_bonus)] == code:
                previous = matches.get(key)
                if previous is None or _evidence_order(match) > _evidence_order(previous):
                    matches[key] = match
        reviews = self._reviews_for_work(code)
        for key, review in reviews.items():
            if key[int(as_bonus)] != code:
                continue
            # Includes legacy confirmations and evidence saved in the review
            # itself (for example, when the metadata cache was disabled).
            saved = next(
                (context["match"] for context in review.evidence_snapshot.context
                 if "limited_bonus_main" in context and "match" in context),
                None,
            )
            if saved is not None:
                saved_match = BonusMatch.model_validate(saved)
                current = matches.get(key)
                if current is None or _evidence_order(saved_match) > _evidence_order(current):
                    matches[key] = saved_match
            elif key not in matches and review.outcome is CandidateReviewOutcome.RELATED:
                matches[key] = BonusMatch(
                    relation=WorkRelation(
                        source_workno=key[0], target_workno=key[1],
                        relation_type=RelationType.LIMITED_BONUS, confidence=Confidence.UNKNOWN,
                        detection_source=review.provenance.value,
                    ),
                    title=key[1], observed_at=review.reviewed_at,
                )
        return tuple(
            BonusPair(match, reviews.get(key)) for key, match in matches.items()
            if key in reviews or match.automatically_linked
            or match.relation.confidence is Confidence.CONFIRMED
            or _within_date_tolerance(match, self._date_tolerance_days)
        )

    def _reviews_for_work(self, code: str) -> dict[tuple[str, str], ManualReviewEvent]:
        if self._manual_reviews is None:
            return {}
        events = sorted(self._manual_reviews.reviews_for_work(code),
                        key=lambda event: (event.reviewed_at, event.id or 0))
        decisions: dict[tuple[str, str], ManualReviewEvent] = {}
        legacy: dict[tuple[str, str], ManualReviewEvent] = {}
        for event in events:
            key = _review_direction(event)
            if any("limited_bonus_main" in context for context in event.evidence_snapshot.context):
                if key is not None:
                    decisions[key] = event
            else:
                legacy[event.workno_a, event.workno_b] = event
        for event in legacy.values():
            key = _review_direction(event)
            if key is not None:
                decisions.setdefault(key, event)
        return decisions

    def review_match(self, match: BonusMatch, outcome: CandidateReviewOutcome) -> None:
        if self._manual_reviews is None:
            raise RuntimeError("人工审阅数据库不可用。")
        main, bonus = _rj(match.relation.source_workno), _rj(match.relation.target_workno)
        previous = self._reviews_for_work(main).get((main, bonus))
        if previous is not None and previous.outcome is outcome:
            return
        related = outcome is CandidateReviewOutcome.RELATED
        self._manual_reviews.submit_review(
            main, bonus, outcome,
            relation_type=ManualRelationType.LIMITED_BONUS_OF if related else None,
            subject_workno=bonus if related else None,
            target_workno=main if related else None,
            evidence_snapshot=CandidateEvidenceSnapshot(
                supporting_evidence=tuple(
                    item.model_dump(mode="json") for item in match.relation.evidence
                ),
                context=({
                    "limited_bonus_main": main,
                    "limited_bonus_candidate": bonus,
                    "match": match.model_dump(mode="json"),
                },),
            ),
        )

    def search(
        self,
        request: BonusSearchRequest,
        *,
        cancelled: Callable[[], bool],
        progress: Callable[[str], None],
    ) -> BonusSearchResult:
        main = _rj(request.main_workno)
        range_codes = (
            _BonusRange.from_offsets(main, request.offsets) if request.offsets is not None else None
        )
        manual = _rj(request.candidate_workno) if request.candidate_workno.strip() else None
        if manual and int(manual[2:]) == int(main[2:]):
            raise ValueError("候选特典编号须与本体编号不同。")

        store = self._store
        pairs = self.pairs_for_work(main)
        decisions = self._reviews_for_work(main)
        rejected = {pair.match.relation.target_workno for pair in pairs if pair.rejected}
        confirmed = {pair.match.relation.target_workno for pair in pairs if pair.confirmed}
        matches = {
            pair.match.relation.target_workno: pair.effective_match()
            for pair in pairs if not pair.rejected
        }
        notices: list[str] = []
        conflicting_reviews: set[str] = set()
        network_available = self._provider is not None and not request.local_only

        submitted: set[str] = set()
        verified: set[str] = set()
        hits: set[str] = set()
        requests = 0
        deadline = monotonic() + BONUS_SEARCH_SECONDS
        campaigns: list[BonusCampaign] = []
        fresh_campaign_at: datetime | None = None
        now = self._clock()
        session = self._scan_session

        def fresh(observed: datetime | None) -> bool:
            return observed is not None and timedelta(0) <= now - observed < self._cache_ttl

        with session.lock:
            if range_codes:
                session.discard_expired(now - self._cache_ttl)
            unresolved = {
                code: state for code, (state, observed) in session.unverified.get(main, {}).items()
                if range_codes is not None and code in range_codes and fresh(observed)
            }
            session_listings = {
                code: listing for code, listing in session.listings.get(main, {}).items()
                if range_codes is not None and code in range_codes and fresh(listing.observed_at)
            }
        retry_order = tuple(unresolved)
        retry_codes = set(retry_order)

        def mark_unverified(codes: Iterable[str], state: _Unverified) -> None:
            observed = self._clock()
            with session.lock:
                for code in codes:
                    if range_codes is None or code not in range_codes:
                        continue
                    unresolved[code] = state
                    history = session.unverified.setdefault(main, {})
                    history.pop(code, None)
                    history[code] = (state, observed)

        def scan_progress(*, final: bool = False) -> BonusScanProgress | None:
            if not range_codes or request.offsets is None:
                return None
            states = tuple(unresolved.values())
            base = int(main[2:])
            first_code, last_code = range_codes.endpoints
            return BonusScanProgress(
                first_code, last_code, len(range_codes), len(submitted),
                len(verified), states.count(_Unverified.MISSING),
                states.count(_Unverified.MALFORMED),
                len(hits), requests, request.offsets, states.count(_Unverified.FAILED),
                frozenset(int(code[2:]) - base for code in verified) if final else frozenset(),
            )

        def result(
            *, used_local_results: bool = False, review_deferred: bool = False,
        ) -> BonusSearchResult:
            evaluated_at = self._clock()
            matched_entries = {
                match.entry.model_dump_json() for match in matches.values() if match.entry
            }
            latest_campaigns: dict[str, BonusCampaign] = {}
            for campaign in sorted(campaigns, key=lambda item: item.observed_at, reverse=True):
                if campaign.entry.model_dump(exclude_none=True):
                    latest_campaigns.setdefault(campaign.entry.model_dump_json(), campaign)
            return BonusSearchResult(
                main_workno=main,
                matches=tuple(sorted(matches.values(), key=lambda match: (
                    -_confidence_rank(match.relation.confidence),
                    abs(int(match.relation.target_workno[2:]) - int(main[2:])),
                    match.relation.target_workno,
                ))),
                notices=tuple(notices),
                cancelled=cancelled(),
                unmatched_entries=tuple(
                    campaign.entry for key, campaign in latest_campaigns.items()
                    if key not in matched_entries
                ),
                scan=scan_progress(final=True),
                campaigns=tuple(latest_campaigns.values()),
                fresh_campaign_at=(
                    fresh_campaign_at if fresh_campaign_at is not None
                    and evaluated_at - fresh_campaign_at < self._cache_ttl else None
                ),
                used_local_results=used_local_results,
                requests=requests,
                evaluated_at=evaluated_at,
                review_deferred=review_deferred,
            )

        @contextmanager
        def begin_request(codes: tuple[str, ...]) -> Iterator[bool]:
            nonlocal network_available, requests
            acquired = False
            try:
                while network_available and not cancelled() and monotonic() < deadline:
                    acquired = session.request_lock.acquire(timeout=0.1)
                    if acquired:
                        wait_until = min(session.next_request, deadline)
                        while (remaining := wait_until - monotonic()) > 0 and not cancelled():
                            sleep(min(0.1, remaining))
                        break
                if network_available and not cancelled() and monotonic() >= deadline:
                    notices.append("已达在线搜索时间上限。")
                    network_available = False
                if not acquired or not network_available or cancelled():
                    yield False
                    return
                requests += 1
                if range_codes is not None:
                    submitted.update(code for code in codes if code in range_codes)
                session.next_request = monotonic() + BONUS_REQUEST_INTERVAL
                yield True
            finally:
                if acquired:
                    session.request_lock.release()

        def stop_online(code: str, exc: Exception) -> None:
            nonlocal network_available
            detail = (
                "DLsite 返回了无法验证的 AJAX 响应。"
                if isinstance(exc, DlsiteParseError) else str(exc)
            )
            notices.append(f"{code} · {detail} 已停止在线查询。")
            network_available = False

        def fetch(code: str) -> DlsiteWorkLookup | None:
            if self._provider is None:
                return None
            with begin_request((code,)) as started:
                if not started:
                    return None
                try:
                    return self._provider.fetch_bonus_lookup(code)
                except WorkNotFoundError:
                    mark_unverified((code,), _Unverified.MISSING)
                    notices.append(f"DLsite AJAX 未返回 {code} 的记录。")
                except (DlsiteConnectionError, DlsiteHttpError, DlsiteParseError) as exc:
                    state = (
                        _Unverified.MALFORMED if isinstance(exc, DlsiteParseError)
                        else _Unverified.FAILED
                    )
                    mark_unverified((code,), state)
                    stop_online(code, exc)
            return None

        if cancelled():
            return result()
        progress("正在读取本体与历史特典记录…")
        observations = store.list_bonus_observations(main) if store else ()
        campaigns = [
            BonusCampaign(entry, row.observed_at, row.source)
            for row in observations if row.evidence is not None
            for entry in row.evidence.entries
        ]
        latest_evidence_at = next(
            (row.observed_at for row in reversed(observations) if row.evidence is not None), None
        )
        local_main = store.latest_bonus_listings((main,)).get(main) if store else None
        main_listing = _local_listing(local_main) if local_main is not None else None
        current_evidence = local_main.bonus_evidence if local_main is not None else None
        main_ajax_at = local_main.observed_at.replace(tzinfo=UTC) if local_main else None
        if request.main_ajax_at is not None:
            main_ajax_at = max(filter(None, (main_ajax_at, request.main_ajax_at)))
        if request.main_evidence is not None and request.main_evidence_at is not None:
            campaigns.extend(
                BonusCampaign(entry, request.main_evidence_at, DLSITE_PRODUCT_INFO_AJAX)
                for entry in request.main_evidence.entries
            )
            latest_evidence_at = max(
                filter(None, (latest_evidence_at, request.main_evidence_at))
            )

        if latest_evidence_at == main_ajax_at and fresh(latest_evidence_at):
            fresh_campaign_at = latest_evidence_at

        cached_main = store.get(main) if store and main_listing is None else None
        legacy_main = None
        translation = main_listing.translation if main_listing else (
            cached_main.translation_info if cached_main else None
        )
        if store and main_listing is None and cached_main is None:
            legacy_main = next(iter(reversed(store.list_observations(main))), None)
            if legacy_main and legacy_main.translation_json:
                try:
                    translation = TranslationInfoSource.model_validate_json(
                        legacy_main.translation_json
                    )
                except ValueError:
                    notices.append("本体的历史翻译关系无法验证。")
                    return result()
        if _translated(translation, main):
            notices.append("该编号属于翻译条目，请使用本体的 RJ 编号搜索。")
            return result()

        maker = (main_listing.snapshot.maker_id if main_listing else None) or (
            cached_main.work.maker_id if cached_main else
            legacy_main.maker_id if legacy_main else request.maker_id
        ) or request.maker_id
        day = (main_listing.snapshot.regist_date if main_listing else None) or (
            cached_main.work.release_date if cached_main else
            legacy_main.release_date if legacy_main else request.regist_date
        ) or request.regist_date
        days = self._date_tolerance_days
        discovered: dict[str, BonusMatch] = {}
        listings: dict[str, _Listing] = {}
        local_listings: dict[str, _Listing] = {}

        def evaluate(code: str, listing: _Listing, *, cached: bool) -> bool:
            listings[code] = listing
            if range_codes is not None and code in range_codes and _can_screen(listing) and (
                not cached or fresh(listing.observed_at)
            ):
                verified.add(code)
                if cached:
                    hits.add(code)
                unresolved.pop(code, None)
                with session.lock:
                    if history := session.unverified.get(main):
                        history.pop(code, None)
                    if not cached and (not store or not self._save_scan_metadata):
                        session.listings.setdefault(main, {})[code] = listing
            if maker is not None and day is not None:
                match = _match(main, code, maker, day, days, listing, campaigns)
                if match is not None:
                    if code in rejected:
                        if match.relation.confidence in {Confidence.HIGH, Confidence.CONFIRMED}:
                            conflicting_reviews.add(code)
                            discovered[code] = match
                        return True
                    discovered[code] = match
                    return True
            discovered.pop(code, None)
            return False

        def evaluate_local() -> list[str]:
            progress("正在检索本地候选…")
            same_day = store.list_bonus_candidate_summaries(maker, day, days=0) if (
                store and maker and day
            ) else ()
            local = store.list_bonus_candidate_summaries(maker, day, days=days) if (
                days and store and maker and day
            ) else same_day
            summaries = {item.workno: item for item in local}
            explicit = {code for campaign in campaigns if (code := campaign.workno) is not None}
            titles = {
                campaign.entry.title for campaign in campaigns
                if _allows_title_match(campaign) and campaign.entry.is_current_at(now)
            }
            historical_titles = {
                campaign.entry.title for campaign in campaigns if _allows_title_match(campaign)
            }
            codes = explicit | set(summaries) | set(matches)
            if manual:
                codes.add(manual)
            ordered = sorted(
                (code for code in codes if int(code[2:]) != int(main[2:])),
                key=lambda code: (
                    code != manual,
                    code not in explicit,
                    code not in summaries or summaries[code].title not in titles,
                    code not in summaries or summaries[code].title not in historical_titles,
                    _date_distance(day, summaries.get(code)),
                    abs(int(code[2:]) - int(main[2:])),
                    code,
                ),
            )
            rows = store.latest_bonus_listings(tuple(ordered)) if store and not cancelled() else {}
            if store and range_codes is not None and not cancelled():
                for row in store.latest_bonus_listings_in_ranges(range_codes.listing_bounds()):
                    if cancelled():
                        break
                    if row.workno in range_codes:
                        rows[row.workno] = row
            local_listings.clear()
            extra = (rows.keys() | session_listings.keys()).difference(ordered)
            for code in chain(ordered, sorted(extra, key=lambda code: int(code[2:]))):
                if cancelled():
                    break
                listing = _local_listing(rows[code]) if code in rows else None
                previous = session_listings.get(code)
                if previous is not None and (
                    listing is None or previous.observed_at > listing.observed_at
                ):
                    listing = previous
                if listing is not None:
                    local_listings[code] = listing
                    evaluate(code, listing, cached=True)
            # The date tolerance expands local evaluation only. Keep the original
            # same-day metadata seeds for individual network requests.
            online_seeds = {item.workno for item in same_day} | explicit | set(matches)
            if manual:
                online_seeds.add(manual)
            return [code for code in ordered if code in online_seeds]

        def retain_discoveries(*, persist: bool) -> None:
            for code in sorted(conflicting_reviews):
                notices.append(f"{code} 有匹配证据，已保留人工排除决定。")
            known = {
                code: match for code, match in matches.items()
                if code not in listings or code in confirmed
                or match.automatically_linked or match.relation.confidence is Confidence.CONFIRMED
            }
            known.update(discovered)
            for code in rejected:
                known.pop(code, None)
            resolved = _resolve_title_conflicts(known.values())
            evaluated_at = self._clock()
            current = (
                current_evidence.entries
                if current_evidence is not None and main_listing is not None
                and main_listing.source in _AJAX_SOURCES and not main_listing.core_conflict
                and main_listing.observed_at == main_ajax_at
                and timedelta(0) <= evaluated_at - main_listing.observed_at < self._cache_ttl
                and not _identity_conflict(
                    main_listing.snapshot, None, request.maker_id, request.regist_date,
                )
                else ()
            )
            explicit_pairs = (
                set(store.list_current_relation_pairs_for_work(main))
                if current and persist and store and not request.local_only else set()
            )
            proposed: list[BonusMatch] = []
            for match in resolved:
                code = match.relation.target_workno
                if code not in discovered:
                    continue
                listing = listings[code]
                if (
                    persist and store is not None and not request.local_only and not cancelled()
                    and main_listing is not None and code not in confirmed
                    and (main, code) not in decisions
                    and _can_auto_link(
                        match, main_listing, listing, current, evaluated_at, self._cache_ttl,
                    )
                ):
                    cached = store.get(code)
                    if not (
                        cached is not None and (
                            _translated(cached.translation_info, code)
                            or _identity_conflict(
                                listing.snapshot, cached.work.title,
                                cached.work.maker_id, cached.work.release_date,
                            )
                        )
                    ) and tuple(sorted((main, code))) not in explicit_pairs:
                        match = match.model_copy(update={"relation": match.relation.model_copy(
                            update={"detection_source": AJAX_TITLE_CROSS_VALIDATION}
                        )})
                proposed.append(match)
            proposed.extend(
                discovered[code] for code in sorted(conflicting_reviews & discovered.keys())
            )
            retained = (
                store.save_limited_bonus_matches(tuple(proposed))
                if persist and store and proposed else proposed
            )
            latest = self._reviews_for_work(main)
            for match in retained:
                code = match.relation.target_workno
                pair = BonusPair(match, latest.get((main, code)))
                if pair.rejected:
                    matches.pop(code, None)
                    if code not in conflicting_reviews and _reliable(match):
                        notices.append(f"{code} 有匹配证据，已保留人工排除决定。")
                elif pair.confirmed:
                    matches[code] = pair.effective_match()
                else:
                    self._retain(matches, match)

        ordered = evaluate_local()
        if manual in rejected:
            notices.append(f"{manual} 已被拒绝；可在审阅记录中重新审阅。")
            return result(used_local_results=True)
        if manual in matches and not request.continue_online:
            retain_discoveries(persist=not cancelled())
            return result(used_local_results=not cancelled())
        if manual is None and not request.continue_online and not request.local_only and any(
            pair.pending for pair in pairs
        ):
            retain_discoveries(persist=not cancelled())
            return result(used_local_results=not cancelled(), review_deferred=not cancelled())
        reliable = any(_reliable(match) for match in (
            *matches.values(), *_resolve_title_conflicts(
                match for code, match in discovered.items() if code not in rejected
            ),
        ))
        manual_match = matches.get(manual) if manual else None
        manual_listing = local_listings.get(manual) if manual else None
        manual_needs_check = manual is not None and manual_match is None and not (
            manual_listing is not None and _can_screen(manual_listing)
            and fresh(manual_listing.observed_at)
        )
        if cancelled() or request.local_only or (
            reliable and not request.continue_online and not manual_needs_check
        ):
            retain_discoveries(persist=not cancelled())
            return result(used_local_results=not cancelled() and not request.local_only)

        if not network_available:
            notices.append("当前提供方不支持 DLsite 特典 AJAX 查询。")
        if store is None:
            notices.append("元数据缓存已关闭或不可用。")

        if not fresh(main_ajax_at):
            status = scan_progress()
            progress(f"{status.describe()}\n正在查询本体 AJAX…" if status else "正在查询本体 AJAX…")
            live_main = fetch(main)
            if live_main is not None and live_main.product_info is not None and not cancelled():
                main_listing = self._record_listing(live_main, retain_evidence=True)
                main_ajax_at = main_listing.observed_at
                current_evidence = live_main.bonus_evidence
                if _translated(main_listing.translation, main):
                    notices.append("该编号属于翻译条目，请使用本体的 RJ 编号搜索。")
                    return result()
                maker = main_listing.snapshot.maker_id or maker
                day = main_listing.snapshot.regist_date or day
                fresh_campaign_at = None
                if live_main.bonus_evidence is not None:
                    fresh_campaign_at = main_listing.observed_at
                    campaigns.extend(
                        BonusCampaign(entry, fresh_campaign_at, live_main.source)
                        for entry in live_main.bonus_evidence.entries
                    )
                discovered.clear()
                listings.clear()
                ordered = evaluate_local()

        if cancelled() or not maker or not day:
            if not cancelled():
                notices.append("本体缺少可核对的社团编号或登记日期。")
            retain_discoveries(persist=True)
            return result()

        attempts = 0
        for code in ordered:
            if cancelled() or not network_available:
                break
            listing = local_listings.get(code)
            screenable = listing is not None and _can_screen(listing)
            if code in rejected or (code != manual and (code in retry_codes or code in verified)):
                continue
            if code != manual and (code in matches or screenable):
                continue
            if code == manual and listing is not None and screenable and fresh(listing.observed_at):
                if code not in discovered and code not in matches:
                    notices.append(f"{code} 未通过社团、日期、特典状态或翻译关系检查。")
                continue
            if attempts >= MAX_CANDIDATE_REQUESTS:
                notices.append(
                    f"已达 {MAX_CANDIDATE_REQUESTS} 次候选请求上限，部分本地编号待验证。"
                )
                break
            attempts += 1
            status = scan_progress()
            message = f"正在查询候选 · {code}"
            progress(f"{status.describe()}\n{message}" if status else message)
            lookup = fetch(code)
            if cancelled() and (range_codes is None or code not in range_codes):
                break
            if lookup is not None and lookup.product_info is not None:
                listing = self._record_listing(lookup)
                if range_codes is not None and code in range_codes and not _can_screen(listing):
                    mark_unverified((code,), _Unverified.MALFORMED)
                found = evaluate(code, listing, cached=False)
                if found and not self._save_scan_metadata:
                    self._record_evidence(lookup, listing)
                if not found and code == manual and code not in matches:
                    notices.append(f"{code} 未通过社团、日期、特典状态或翻译关系检查。")

        provider = self._provider
        if (
            range_codes is not None and provider is not None and network_available
            and not cancelled()
        ):
            untried = (code for code in range_codes if code not in retry_codes)
            pending = (
                code for code in chain(untried, retry_order)
                if code not in verified and code not in submitted
            )
            for index, batch in enumerate(batched(pending, BONUS_BATCH_SIZE)):
                if cancelled() or not network_available:
                    break
                if not provider.supports_bonus_range:
                    notices.append("当前 DLsite 分类未验证批量查询，本次编号范围仅使用本机记录。")
                    break
                if index >= MAX_BATCH_REQUESTS:
                    notices.append(f"已达 {MAX_BATCH_REQUESTS} 次范围请求上限，部分编号尚未查询。")
                    break
                status = scan_progress()
                assert status is not None
                progress(f"{status.describe()}\n正在查找期限特典…")
                with begin_request(batch) as started:
                    if not started:
                        break
                    try:
                        response = provider.fetch_bonus_batch(batch)
                    except WorkNotFoundError:
                        mark_unverified(batch, _Unverified.MISSING)
                        continue
                    except (DlsiteConnectionError, DlsiteHttpError, DlsiteParseError) as exc:
                        state = (
                            _Unverified.MALFORMED if isinstance(exc, DlsiteParseError)
                            else _Unverified.FAILED
                        )
                        mark_unverified(batch, state)
                        stop_online(batch[0], exc)
                        break
                mark_unverified((code for code in batch if code in response.missing),
                                _Unverified.MISSING)
                mark_unverified((code for code in batch if code in response.malformed),
                                _Unverified.MALFORMED)
                for code, lookup in response.listings.items():
                    listing = self._record_listing(lookup)
                    if not _can_screen(listing):
                        mark_unverified((code,), _Unverified.MALFORMED)
                    found = evaluate(code, listing, cached=False)
                    if found and not self._save_scan_metadata:
                        self._record_evidence(lookup, listing)
                if response.malformed:
                    stop_online(batch[0], DlsiteParseError())
                    break
        retain_discoveries(persist=True)
        return result()

    def _record_listing(
        self, lookup: DlsiteWorkLookup, *, retain_evidence: bool = False
    ) -> _Listing:
        product = lookup.product_info
        assert product is not None
        listing = _Listing(
            product.bonus_listing, lookup.translation_info, self._clock(), lookup.source,
            _identity_conflict(product.bonus_listing, lookup.work.title, lookup.work.maker_id,
                               lookup.work.release_date),
        )
        if self._store and self._save_scan_metadata:
            self._store.save(
                lookup.work, listing.translation, source=listing.source,
                fetched_at=listing.observed_at,
                work_type=product.work_type, age_category=product.age_category,
                regist_datetime=lookup.regist_datetime, core_source=lookup.core_source,
                translation_attribution=lookup.translation_attribution, from_bonus_scan=True,
            )
        if self._save_scan_metadata or (retain_evidence and lookup.bonus_evidence is not None):
            self._record_evidence(lookup, listing)
        return listing

    def _record_evidence(self, lookup: DlsiteWorkLookup, listing: _Listing) -> None:
        product = lookup.product_info
        if self._store and product is not None:
            self._store.append_observation(
                lookup.work, listing.translation, source=listing.source,
                observed_at=listing.observed_at,
                work_type=product.work_type, age_category=product.age_category,
                regist_datetime=lookup.regist_datetime, bonus_evidence=lookup.bonus_evidence,
                bonus_listing=listing.snapshot, core_source=lookup.core_source,
                translation_attribution=lookup.translation_attribution,
            )

    @staticmethod
    def _retain(matches: dict[str, BonusMatch], match: BonusMatch) -> None:
        code = match.relation.target_workno
        previous = matches.get(code)
        if previous is None or _evidence_order(match) > _evidence_order(previous):
            matches[code] = match


def _date_distance(day: date | None, item: KnownWorkSnapshot | None) -> int:
    return abs((day - item.regist_date).days) if day and item and item.regist_date else 999999


def _can_screen(listing: _Listing) -> bool:
    snapshot = listing.snapshot
    return snapshot.status.is_complete and (
        not snapshot.status.matches_bonus_profile
        or (snapshot.maker_id is not None and snapshot.regist_date is not None)
    )


def _allows_title_match(campaign: BonusCampaign) -> bool:
    return not (campaign.entry.workno or campaign.entry.product_id or campaign.workno)


def _can_auto_link(
    match: BonusMatch, main: _Listing, candidate: _Listing,
    entries: tuple[BonusEvidence, ...], now: datetime, ttl: timedelta,
) -> bool:
    entry = match.entry
    if (
        match.relation.confidence is not Confidence.HIGH or entry is None
        or match.observed_at != main.observed_at or candidate.core_conflict
        or candidate.source not in _AJAX_SOURCES
        or not timedelta(0) <= now - candidate.observed_at < ttl
        or main.snapshot.maker_id is None or main.snapshot.regist_date is None
        or main.snapshot.maker_id != candidate.snapshot.maker_id
        or main.snapshot.regist_date != candidate.snapshot.regist_date
    ):
        return False
    plausible_entries = {
        item.model_dump_json() for item in entries if item.title == match.title
        and item.activity_status(now, recently_reported=True) not in {"已结束", "尚未开始"}
    }
    return (
        entry.activity_status(now, recently_reported=True) == "活动进行中"
        and plausible_entries == {entry.model_dump_json()}
    )


def _resolve_title_conflicts(matches: Iterable[BonusMatch]) -> tuple[BonusMatch, ...]:
    matches = tuple(matches)
    title_counts: dict[str, int] = {}
    for match in matches:
        title_counts[match.title] = title_counts.get(match.title, 0) + 1
    resolved = []
    for match in matches:
        if match.relation.confidence == Confidence.HIGH and title_counts[match.title] > 1:
            relation = match.relation.model_copy(update={
                "confidence": Confidence.LOW,
                "evidence": [
                    *match.relation.evidence,
                    RelationEvidence(
                        evidence_type=EvidenceType.OTHER,
                        description="多个特典候选使用相同标题",
                    ),
                ],
            })
            match = match.model_copy(update={"relation": relation})
        resolved.append(match)
    return tuple(resolved)


def _reliable(match: BonusMatch) -> bool:
    return match.relation.confidence in {Confidence.CONFIRMED, Confidence.HIGH}


def _confidence_rank(confidence: Confidence) -> int:
    return {Confidence.CONFIRMED: 3, Confidence.HIGH: 2, Confidence.LOW: 1}.get(confidence, 0)


def _evidence_order(match: BonusMatch) -> tuple[int, bool, datetime]:
    return (
        _confidence_rank(match.relation.confidence),
        match.automatically_linked,
        match.observed_at or datetime.min.replace(tzinfo=UTC),
    )


def _within_date_tolerance(match: BonusMatch, days: int) -> bool:
    for item in match.relation.evidence:
        main = item.attributes.get("main_date")
        bonus = item.attributes.get("bonus_date")
        if isinstance(main, str) and isinstance(bonus, str):
            try:
                return abs((date.fromisoformat(main) - date.fromisoformat(bonus)).days) <= days
            except ValueError:
                continue
    return True


def _match(
    main: str, code: str, maker: str, day: date, days: int,
    listing: _Listing, campaigns: list[BonusCampaign],
) -> BonusMatch | None:
    snapshot = listing.snapshot
    if (
        snapshot.maker_id != maker or snapshot.regist_date is None
        or abs((day - snapshot.regist_date).days) > days
        or not snapshot.status.matches_bonus_profile
        or _translated(listing.translation, code)
    ):
        return None
    confidence = Confidence.LOW
    evidence = [
        RelationEvidence(
            evidence_type=EvidenceType.DLSITE_PARAMETER,
            description="AJAX 特典状态符合",
            attributes={"source": listing.source, "observed_at": listing.observed_at.isoformat(),
                        **snapshot.status.model_dump(mode="json")},
        ),
        RelationEvidence(
            evidence_type=EvidenceType.SAME_MAKER, description=f"同社团 {maker}",
            attributes={"maker_id": maker},
        ),
        RelationEvidence(
            evidence_type=(
                EvidenceType.SAME_RELEASE_DATE
                if day == snapshot.regist_date else EvidenceType.OTHER
            ),
            description=(
                f"同登记日 {day}" if day == snapshot.regist_date
                else f"登记日相差 {abs((day - snapshot.regist_date).days)} 天"
            ),
            attributes={
                "main_date": day.isoformat(), "bonus_date": snapshot.regist_date.isoformat()
            },
        ),
    ]
    direct = [campaign for campaign in campaigns if campaign.workno == code]
    exact = [
        campaign for campaign in campaigns
        if _allows_title_match(campaign) and campaign.entry.title == snapshot.title
    ]
    campaign = max(direct or exact, key=lambda item: item.observed_at, default=None)
    if campaign:
        if day == snapshot.regist_date:
            confidence = Confidence.CONFIRMED if direct else Confidence.HIGH
        evidence.insert(0, RelationEvidence(
            evidence_type=EvidenceType.DLSITE_PARAMETER,
            description="本体 AJAX 明示特典编号" if direct else "与本体 AJAX 特典标题完全一致",
            attributes={"source": campaign.source, "observed_at": campaign.observed_at.isoformat()},
        ))
    return BonusMatch(
        relation=WorkRelation(
            source_workno=main, target_workno=code, relation_type=RelationType.LIMITED_BONUS,
            confidence=confidence, evidence=evidence,
            detection_source=campaign.source if campaign else listing.source,
        ),
        title=snapshot.title, entry=campaign.entry if campaign else None,
        observed_at=campaign.observed_at if campaign else listing.observed_at,
    )
