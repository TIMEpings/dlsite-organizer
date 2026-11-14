from __future__ import annotations
# ruff: noqa

import logging
from copy import copy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from threading import Lock
from typing import TYPE_CHECKING, Any, Callable, Protocol, cast, runtime_checkable

if TYPE_CHECKING:
    from dlsite_organizer.app.settings import AppSettings

from dlsite_organizer.domain.bonus import DEFAULT_BONUS_DATE_TOLERANCE_DAYS, BonusEvidenceSnapshot
from dlsite_organizer.domain.relation import TranslationRole, WorkRelation
from dlsite_organizer.domain.candidate import CandidateSearchResult
from dlsite_organizer.domain.manual_review import ManualReviewEvent
from dlsite_organizer.domain.work import TranslationAttribution, Work
from dlsite_organizer.domain.work_code import WorkCodeError, normalize_workno
from dlsite_organizer.persistence.metadata_store import BONUS_SCAN_CACHE_SOURCE, MetadataStore
from dlsite_organizer.providers.base import WorkProvider
from dlsite_organizer.providers.dlsite.client import DLSITE_PRODUCT_INFO_AJAX, DlsiteProvider
from dlsite_organizer.providers.dlsite.exceptions import (
    DlsiteConnectionError,
    DlsiteHttpError,
    DlsiteParseError,
    WorkNotFoundError,
)
from dlsite_organizer.providers.dlsite.sources import TranslationInfoSource
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.bonus_search import (
    BonusScanSession,
    BonusSearchRequest, BonusSearchResult, BonusSearchService,
)
from dlsite_organizer.services.translation_relations import (
    TranslationAnalysis,
    TranslationRelationService,
)
from dlsite_organizer.services.translation_discovery import TranslationDiscovery, TranslationListings
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
    LOCAL_BONUS_REVIEW = "local_bonus_review"


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

    @property
    def bonus_evidence(self) -> BonusEvidenceSnapshot | None: ...


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
    core_source: str | None = None
    metadata_source: str | None = None
    translation_attribution: TranslationAttribution | None = None
    bonus_evidence: BonusEvidenceSnapshot | None = None
    bonus_observed_at: datetime | None = None
    bonus_preview: BonusSearchResult | None = None
    has_local_bonus_relation: bool = False

    @property
    def bonus_ajax_at(self) -> datetime | None:
        return self.fetched_at if self.core_source == DLSITE_PRODUCT_INFO_AJAX else None

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


@dataclass(frozen=True, slots=True)
class LookupConfiguration:
    """Published together; configuration objects are never mutated after publication."""

    generation: int
    naming: NamingService
    provider: WorkProvider | _SourceAwareProvider
    metadata_store: MetadataStore | None
    cache_ttl: timedelta
    allow_stale_on_error: bool
    cache_enabled: bool = True
    settings: AppSettings | None = None


def operation_settings_key(settings: AppSettings) -> tuple[object, ...]:
    """Launch-time fields do not change the active profile or provider route."""
    cache = settings.cache
    return (
        settings.naming_template, settings.cv_separator, settings.cv_prefix, settings.cv_suffix,
        settings.tag_separator, settings.max_tags, settings.hide_general_age,
        settings.date_format, settings.illegal_char_replacement,
        settings.provider.timeout_seconds, settings.provider.metadata_locale,
        cache.enabled, cache.ttl_hours, cache.allow_stale_on_error,
    )


def _configured_provider(
    provider: WorkProvider | _SourceAwareProvider, settings: AppSettings
) -> WorkProvider | _SourceAwareProvider:
    """Copy configurable providers, retaining request-owned client factories and shared stores."""
    if getattr(provider, "apply_settings", None) is None:
        return provider
    snapshot = getattr(provider, "snapshot", None)
    configured = cast(
        WorkProvider | _SourceAwareProvider, snapshot() if callable(snapshot) else copy(provider)
    )
    getattr(configured, "apply_settings")(
        timeout_seconds=settings.provider.timeout_seconds,
        metadata_locale=settings.provider.metadata_locale,
    )
    return configured


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
        settings: AppSettings | None = None,
    ):
        self._settings_lock = Lock()
        self._store = metadata_store
        naming = copy(naming)
        if settings is not None:
            naming.apply_settings(settings)
            provider = _configured_provider(provider, settings)
        self._configuration = LookupConfiguration(
            generation=0,
            naming=naming,
            provider=provider,
            metadata_store=metadata_store if settings is None or settings.cache.enabled else None,
            cache_ttl=timedelta(
                hours=settings.cache.ttl_hours if settings is not None else cache_ttl_hours
            ),
            allow_stale_on_error=(
                settings.cache.allow_stale_on_error if settings is not None else allow_stale_on_error
            ),
            cache_enabled=settings.cache.enabled if settings is not None else True,
            settings=settings,
        )
        self._translation_relations = translation_relations or TranslationRelationService()
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
        self._clock = clock or (lambda: datetime.now(UTC))
        self._bonus_scan_session = BonusScanSession()

    def lookup(
        self, raw_workno: str, *, force_refresh: bool = False, prefer_bonus_review: bool = False,
    ) -> LookupResult:
        return self.snapshot()._lookup(
            raw_workno, force_refresh=force_refresh, prefer_bonus_review=prefer_bonus_review,
        )

    @property
    def settings_generation(self) -> int:
        return self._configuration.generation

    def snapshot(self) -> LookupService:
        """Capture one published configuration without duplicating persistence resources."""
        with self._settings_lock:
            return copy(self)

    def bonus_search(self) -> BonusSearchService:
        configuration = self.snapshot()._configuration
        provider = configuration.provider
        settings = configuration.settings
        return BonusSearchService(
            provider if isinstance(provider, DlsiteProvider) else None,
            configuration.metadata_store,
            self._manual_review_service,
            clock=self._now,
            cache_ttl=configuration.cache_ttl,
            scan_session=self._bonus_scan_session,
            save_scan_metadata=settings is None or settings.cache.save_bonus_scan_metadata,
            date_tolerance_days=(
                settings.bonus_date_tolerance_days if settings else DEFAULT_BONUS_DATE_TOLERANCE_DAYS
            ),
        )

    def preview_bonuses(self, result: LookupResult) -> BonusSearchResult | None:
        work = result.work
        if not work.workno.startswith("RJ") or result.translation_role in {
            TranslationRole.TRANSLATION_PARENT, TranslationRole.TRANSLATION_CHILD,
        }:
            return None
        return self.bonus_search().search(
            BonusSearchRequest(
                work.workno, work.maker_id, work.release_date, local_only=True,
                main_evidence=result.bonus_evidence,
                main_ajax_at=result.bonus_ajax_at,
                main_evidence_at=result.bonus_observed_at,
            ),
            cancelled=lambda: False, progress=lambda _: None,
        )

    def discover_translations(
        self,
        result: LookupResult,
        *,
        publish: Callable[[TranslationListings], None],
        cancelled: Callable[[], bool],
        allow_online: bool = True,
    ) -> None:
        configuration = self._configuration
        provider = configuration.provider
        TranslationDiscovery(
            provider if allow_online and isinstance(provider, DlsiteProvider) else None,
            configuration.metadata_store,
            clock=self._now,
            ttl=configuration.cache_ttl,
        ).discover(
            result.work.workno, result.translation, result.translation_attribution,
            result.historical_relations, result.fetched_at, result.source,
            release_date=result.work.release_date, availability=result.work.availability,
            publish=publish, cancelled=cancelled,
        )

    def is_recent_bonus_observation(self, observed_at: datetime | None) -> bool:
        return observed_at is not None and (
            timedelta(0) <= self._now() - observed_at < self._configuration.cache_ttl
        )

    def _lookup(
        self, raw_workno: str, *, force_refresh: bool = False, prefer_bonus_review: bool = False,
    ) -> LookupResult:
        try:
            workno = normalize_workno(raw_workno)
        except WorkCodeError as exc:
            raise LookupFailure(LookupFailureKind.INVALID_CODE, str(exc)) from exc
        if force_refresh or not prefer_bonus_review or not workno.startswith("RJ"):
            return self._lookup_metadata(workno, force_refresh=force_refresh)

        bonus = self.bonus_search()
        incoming = bonus.pairs_for_work(workno, as_bonus=True)
        if not incoming and not bonus.pairs_for_work(workno):
            return self._lookup_metadata(workno, force_refresh=False)

        try:
            result = self._lookup_metadata(workno, force_refresh=False, require_full_metadata=True)
        except LookupFailure as exc:
            if not isinstance(
                exc.__cause__, (DlsiteConnectionError, DlsiteHttpError, WorkNotFoundError),
            ):
                raise
            store = self._configuration.metadata_store
            observations = store.list_observations(workno) if store else ()
            observation = observations[-1] if observations else None
            work = Work(
                workno=workno,
                title=observation.title if observation else next(
                    (pair.match.title for pair in incoming if pair.match.title != workno),
                    workno,
                ),
                maker_id=observation.maker_id if observation else None,
                maker_name=observation.maker_name if observation else None,
                release_date=observation.release_date if observation else None,
            )
            result = self._result(
                workno, work, None, LookupFreshness.LOCAL_BONUS_REVIEW,
                observation.source if observation else "LOCAL_BONUS_RELATION",
                observation.observed_at if observation else None,
            )
        return replace(result, has_local_bonus_relation=True)

    def _lookup_metadata(
        self, workno: str, *, force_refresh: bool, require_full_metadata: bool = False,
    ) -> LookupResult:
        configuration = self._configuration
        store = configuration.metadata_store
        cached = store.get(workno) if store else None
        if (
            cached
            and not force_refresh
            # Bonus scans can save core listing data without the ordinary enrichment.
            and not (require_full_metadata and cached.source == BONUS_SCAN_CACHE_SOURCE)
            and self._now() - cached.fetched_at < configuration.cache_ttl
        ):
            return self._result(
                workno,
                cached.work,
                cached.translation_info,
                LookupFreshness.CACHE_FRESH,
                cached.source,
                cached.fetched_at,
                core_source=cached.core_source,
                metadata_source=cached.metadata_source,
                translation_attribution=cached.translation_attribution,
                bonus_evidence=cached.bonus_evidence,
                bonus_observed_at=cached.bonus_observed_at,
            )
        try:
            (
                work,
                info,
                attribution,
                source,
                fetched,
                core_source,
                metadata_source,
                obj,
            ) = self._fetch_work(workno)
        except WorkNotFoundError as exc:
            raise LookupFailure(LookupFailureKind.NOT_FOUND, f"Work not found: {workno}") from exc
        except DlsiteParseError as exc:
            logger.exception("DLsite provider contract failure for %s", workno)
            raise LookupFailure(
                LookupFailureKind.RESPONSE,
                "DLsite 返回的数据暂时无法读取，请稍后重试。",
            ) from exc
        except (DlsiteConnectionError, DlsiteHttpError) as exc:
            if cached and configuration.allow_stale_on_error:
                return self._result(
                    workno,
                    cached.work,
                    cached.translation_info,
                    LookupFreshness.CACHE_STALE_FALLBACK,
                    cached.source,
                    cached.fetched_at,
                    core_source=cached.core_source,
                    metadata_source=cached.metadata_source,
                    translation_attribution=cached.translation_attribution,
                    bonus_evidence=cached.bonus_evidence,
                    bonus_observed_at=cached.bonus_observed_at,
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
        bonus_evidence = cast(
            BonusEvidenceSnapshot | None,
            getattr(obj, "bonus_evidence", None),
        )
        if store:
            product = getattr(obj, "product_info", None)
            regist_datetime = cast(
                datetime | None,
                getattr(obj, "regist_datetime", None)
                or getattr(product, "regist_datetime", None)
                or work.regist_datetime,
            )
            work_type = cast(str | None, getattr(product, "work_type", None))
            age_category = cast(str | int | None, getattr(product, "age_category", None))
            try:
                store.save(
                    work,
                    info,
                    source=source,
                    fetched_at=fetched,
                    work_type=work_type,
                    age_category=age_category,
                    regist_datetime=regist_datetime,
                    translation_attribution=attribution,
                    core_source=core_source,
                    metadata_source=metadata_source,
                )
            except Exception:
                logger.exception("Metadata cache persistence failed for %s", workno)
            try:
                store.append_observation(
                    work,
                    info,
                    source=source,
                    observed_at=fetched,
                    work_type=work_type,
                    age_category=age_category,
                    regist_datetime=regist_datetime,
                    bonus_evidence=bonus_evidence,
                    bonus_listing=getattr(product, "bonus_listing", None),
                    translation_attribution=attribution,
                    core_source=core_source,
                    metadata_source=metadata_source,
                )
            except Exception:
                logger.exception("Metadata observation persistence failed for %s", workno)
        return self._result(
            workno,
            work,
            info,
            LookupFreshness.LIVE,
            source,
            fetched,
            core_source=core_source,
            metadata_source=metadata_source,
            translation_attribution=attribution,
            bonus_evidence=bonus_evidence,
            bonus_observed_at=fetched if bonus_evidence is not None else None,
        )

    @property
    def manual_review_service(self) -> ManualReviewService | None:
        return self._manual_review_service

    def apply_settings(self, settings: AppSettings) -> None:
        """Publish a complete configuration after the caller successfully persists settings."""
        with self._settings_lock:
            current = self._configuration
            if current.settings is not None and (
                operation_settings_key(current.settings) == operation_settings_key(settings)
            ):
                if current.settings.cache.save_bonus_scan_metadata != settings.cache.save_bonus_scan_metadata:
                    self._configuration = replace(current, settings=settings)
                return
            naming = copy(current.naming)
            naming.apply_settings(settings)
            provider = _configured_provider(current.provider, settings)
            self._configuration = LookupConfiguration(
                generation=current.generation + 1,
                naming=naming,
                provider=provider,
                metadata_store=self._store if settings.cache.enabled else None,
                cache_ttl=timedelta(hours=settings.cache.ttl_hours),
                allow_stale_on_error=settings.cache.allow_stale_on_error,
                cache_enabled=settings.cache.enabled,
                settings=settings,
            )

    def _fetch_work(self, workno):
        provider = self._configuration.provider
        if isinstance(provider, _SourceAwareProvider):
            x = provider.fetch_work_lookup(workno)
            return (
                x.work,
                x.translation_info,
                getattr(x, "translation_attribution", None),
                getattr(x, "source", "LIVE"),
                self._now(),
                getattr(x, "core_source", None),
                getattr(x, "metadata_source", None),
                x,
            )
        return provider.fetch_work(workno), None, None, "LIVE", self._now(), None, None, None

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("Lookup clock must return a timezone-aware datetime")
        return value.astimezone(UTC)

    def _result(
        self,
        workno,
        work,
        info,
        freshness,
        source,
        fetched,
        *,
        core_source: str | None = None,
        metadata_source: str | None = None,
        translation_attribution: TranslationAttribution | None = None,
        bonus_evidence: BonusEvidenceSnapshot | None = None,
        bonus_observed_at: datetime | None = None,
    ):
        formatted_name = self._configuration.naming.format(work)
        translation = self._translation_relations.analyze(workno, info)
        historical = (
            self._historical_relations.for_work(workno)
            if self._configuration.cache_enabled and self._historical_relations
            else HistoricalRelations()
        )
        candidates = None
        if self._configuration.cache_enabled and self._candidate_relations:
            if (
                type(self._candidate_relations) is CandidateRelationService
                and self._candidate_relations.historical_relations_service is self._historical_relations
            ):
                candidates = self._candidate_relations.for_work(workno, historical_relations=historical)
            else:
                candidates = self._candidate_relations.for_work(workno)
        return LookupResult(
            work=work,
            formatted_name=formatted_name,
            translation=translation,
            freshness=freshness,
            source=source,
            fetched_at=fetched,
            core_source=core_source,
            metadata_source=metadata_source,
            translation_attribution=translation_attribution,
            bonus_evidence=bonus_evidence,
            bonus_observed_at=bonus_observed_at,
            historical_relations=historical,
            candidate_relations=candidates,
            manual_reviews=(self._manual_review_service.reviews_for_work(workno) if self._manual_review_service else ()),
        )
