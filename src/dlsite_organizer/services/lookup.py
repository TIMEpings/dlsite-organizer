from __future__ import annotations
# ruff: noqa

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Callable, Protocol, cast, runtime_checkable

from dlsite_organizer.domain.relation import TranslationRole, WorkRelation
from dlsite_organizer.domain.candidate import CandidateSearchResult
from dlsite_organizer.domain.manual_review import ManualReviewEvent
from dlsite_organizer.domain.work import Work
from dlsite_organizer.domain.work_code import WorkCodeError, normalize_rjcode
from dlsite_organizer.persistence.metadata_store import MetadataStore
from dlsite_organizer.providers.base import WorkProvider
from dlsite_organizer.providers.dlsite.exceptions import (
    DlsiteConnectionError,
    DlsiteHttpError,
    DlsiteParseError,
    WorkNotFoundError,
)
from dlsite_organizer.providers.dlsite.sources import TranslationInfoSource
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.translation_relations import (
    TranslationAnalysis,
    TranslationRelationService,
)
from dlsite_organizer.services.historical_relations import HistoricalRelationService, HistoricalRelations
from dlsite_organizer.services.candidate_relations import CandidateRelationService
from dlsite_organizer.services.manual_reviews import ManualReviewService

logger = logging.getLogger(__name__)


class LookupFailureKind(StrEnum):
    INVALID_CODE = "invalid_code"
    NOT_FOUND = "not_found"
    CONNECTION = "connection"
    RESPONSE = "response"
    UNEXPECTED = "unexpected"


class LookupFreshness(StrEnum):
    LIVE = "live"
    CACHE_FRESH = "cache_fresh"
    CACHE_STALE_FALLBACK = "cache_stale_fallback"


class LookupFailure(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind
        self.user_message = message


class _SourceAwareLookup(Protocol):
    @property
    def work(self) -> Work: ...
    @property
    def translation_info(self) -> TranslationInfoSource | None: ...


@runtime_checkable
class _SourceAwareProvider(Protocol):
    def fetch_work_lookup(self, workno: str) -> _SourceAwareLookup: ...


@dataclass(frozen=True, slots=True)
class LookupResult:
    work: Work
    formatted_name: str
    translation: TranslationAnalysis = field(default_factory=TranslationAnalysis)
    freshness: LookupFreshness = LookupFreshness.LIVE
    source: str = "LIVE"
    fetched_at: datetime | None = None
    historical_relations: HistoricalRelations = field(default_factory=HistoricalRelations)
    candidate_relations: CandidateSearchResult | None = None
    manual_reviews: tuple[ManualReviewEvent, ...] = ()

    @property
    def translation_role(self) -> TranslationRole | None:
        return self.translation.role

    @property
    def relations(self) -> tuple[WorkRelation, ...]:
        return self.translation.relations

    @property
    def candidates(self):
        """Derived candidate items, kept separate from confirmed relations."""
        return self.candidate_relations.candidates if self.candidate_relations else ()


LookupClock = Callable[[], datetime]


class LookupService:
    def __init__(
        self,
        provider: WorkProvider | _SourceAwareProvider,
        naming: NamingService,
        translation_relations: TranslationRelationService | None = None,
        metadata_store: MetadataStore | None = None,
        historical_relations: HistoricalRelationService | None = None,
        candidate_relations: CandidateRelationService | None = None,
        manual_review_service: ManualReviewService | None = None,
        cache_ttl_hours: float = 24.0,
        allow_stale_on_error: bool = True,
        clock: LookupClock | None = None,
    ):
        self._provider = provider
        self._naming = naming
        self._translation_relations = translation_relations or TranslationRelationService()
        self._metadata_store = metadata_store
        self._historical_relations = historical_relations or (HistoricalRelationService(metadata_store, self._translation_relations) if metadata_store else None)
        self._manual_review_service = manual_review_service
        self._candidate_relations = candidate_relations or (
            CandidateRelationService(
                metadata_store,
                historical_relations=self._historical_relations,
                manual_reviews=manual_review_service,
            )
            if metadata_store
            else None
        )
        self._cache_ttl = timedelta(hours=cache_ttl_hours)
        self._allow_stale_on_error = allow_stale_on_error
        self._clock = clock or (lambda: datetime.now(UTC))

    def lookup(self, raw_workno: str, *, force_refresh: bool = False) -> LookupResult:
        try:
            workno = normalize_rjcode(raw_workno)
        except WorkCodeError as exc:
            raise LookupFailure(LookupFailureKind.INVALID_CODE, str(exc)) from exc
        cached = self._metadata_store.get(workno) if self._metadata_store else None
        if (
            cached
            and not force_refresh
            and self._now() - cached.fetched_at < self._cache_ttl
        ):
            return self._result(
                workno,
                cached.work,
                cached.translation_info,
                LookupFreshness.CACHE_FRESH,
                cached.source,
                cached.fetched_at,
            )
        try:
            work, info, source, fetched, obj = self._fetch_work(workno)
        except WorkNotFoundError as exc:
            raise LookupFailure(LookupFailureKind.NOT_FOUND, f"Work not found: {workno}") from exc
        except DlsiteParseError as exc:
            logger.exception("DLsite provider contract failure for %s", workno)
            raise LookupFailure(
                LookupFailureKind.RESPONSE,
                "DLsite 返回的数据暂时无法读取，请稍后重试。",
            ) from exc
        except (DlsiteConnectionError, DlsiteHttpError) as exc:
            if cached and self._allow_stale_on_error:
                return self._result(
                    workno,
                    cached.work,
                    cached.translation_info,
                    LookupFreshness.CACHE_STALE_FALLBACK,
                    cached.source,
                    cached.fetched_at,
                )
            kind = (
                LookupFailureKind.CONNECTION
                if isinstance(exc, DlsiteConnectionError)
                else LookupFailureKind.RESPONSE
            )
            msg = (
                "连接 DLsite 失败，请检查网络后稍后重试。"
                if kind is LookupFailureKind.CONNECTION
                else "DLsite 返回的数据暂时无法读取，请稍后重试。"
            )
            raise LookupFailure(kind, msg) from exc
        except Exception as exc:
            logger.exception("Unexpected lookup failure for %s", workno)
            raise LookupFailure(LookupFailureKind.UNEXPECTED, "Unexpected lookup failure") from exc
        if self._metadata_store:
            product = getattr(obj, "product_info", None)
            regist_datetime = cast(datetime | None, getattr(product, "regist_datetime", None))
            work_type = cast(str | None, getattr(product, "work_type", None))
            age_category = cast(str | int | None, getattr(product, "age_category", None))
            try:
                self._metadata_store.save(
                    work,
                    info,
                    source=source,
                    fetched_at=fetched,
                    work_type=work_type,
                    age_category=age_category,
                    regist_datetime=regist_datetime,
                )
            except Exception:
                logger.exception("Metadata cache persistence failed for %s", workno)
            try:
                self._metadata_store.append_observation(
                    work,
                    info,
                    source=source,
                    observed_at=fetched,
                    work_type=work_type,
                    age_category=age_category,
                    regist_datetime=regist_datetime,
                )
            except Exception:
                logger.exception("Metadata observation persistence failed for %s", workno)
        return self._result(workno, work, info, LookupFreshness.LIVE, source, fetched)

    @property
    def manual_review_service(self) -> ManualReviewService | None:
        return self._manual_review_service

    def _fetch_work(self, workno):
        if isinstance(self._provider, _SourceAwareProvider):
            x = self._provider.fetch_work_lookup(workno)
            return x.work, x.translation_info, getattr(x, "source", "LIVE"), self._now(), x
        return self._provider.fetch_work(workno), None, "LIVE", self._now(), None

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("Lookup clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    def _result(self, workno, work, info, freshness, source, fetched):
        return LookupResult(
            work=work,
            formatted_name=self._naming.format(work),
            translation=self._translation_relations.analyze(workno, info),
            freshness=freshness,
            source=source,
            fetched_at=fetched,
            historical_relations=self._historical_relations.for_work(workno) if self._historical_relations else HistoricalRelations(),
            candidate_relations=self._candidate_relations.for_work(workno) if self._candidate_relations else None,
            manual_reviews=(self._manual_review_service.reviews_for_work(workno) if self._manual_review_service else ()),
        )
