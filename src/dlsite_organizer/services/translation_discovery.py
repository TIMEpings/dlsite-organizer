"""Resolve explicit language editions and their children with bounded AJAX requests."""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import StrEnum

from dlsite_organizer.domain.relation import RelationType, TranslationRole
from dlsite_organizer.domain.work import Availability, TranslationAttribution
from dlsite_organizer.persistence.metadata_store import MetadataStore
from dlsite_organizer.providers.dlsite.client import BONUS_BATCH_SIZE, DlsiteProvider
from dlsite_organizer.providers.dlsite.exceptions import DlsiteProviderError, WorkNotFoundError
from dlsite_organizer.providers.dlsite.sources import TranslationInfoSource
from dlsite_organizer.services.historical_relations import (
    HistoricalRelations,
    HistoricalRelationService,
)
from dlsite_organizer.services.translation_relations import (
    TranslationAnalysis,
    TranslationRelationService,
)

logger = logging.getLogger(__name__)
_JAPAN_TIME = timezone(timedelta(hours=9))


class TranslationDetailStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    NOT_RELEASED = "not_released"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class TranslatorListing:
    workno: str
    name: str | None = None
    detail_status: TranslationDetailStatus = TranslationDetailStatus.UNAVAILABLE


@dataclass(frozen=True, slots=True)
class LanguageListing:
    workno: str
    language: str | None
    translators: tuple[TranslatorListing, ...]
    children_loaded: bool


@dataclass(frozen=True, slots=True)
class TranslationListings:
    original: str | None = None
    versions: tuple[LanguageListing, ...] = ()
    ungrouped: tuple[TranslatorListing, ...] = ()
    evidence: tuple[str, ...] = ()
    original_checked: bool = False


@dataclass(frozen=True, slots=True)
class _Snapshot:
    analysis: TranslationAnalysis
    attribution: TranslationAttribution | None
    observed_at: datetime
    source: str
    release_date: date | None
    availability: str


class TranslationDiscovery:
    """Read the current listing, one layer of parents and one layer of children."""

    def __init__(
        self,
        provider: DlsiteProvider | None,
        store: MetadataStore | None,
        *,
        clock: Callable[[], datetime],
        ttl: timedelta,
    ) -> None:
        self._provider = provider
        self._store = store
        self._clock = clock
        self._ttl = ttl
        self._analyzer = TranslationRelationService()
        self._history = HistoricalRelationService(store) if store else None

    def discover(
        self,
        workno: str,
        analysis: TranslationAnalysis,
        attribution: TranslationAttribution | None,
        history: HistoricalRelations,
        observed_at: datetime | None,
        source: str,
        *,
        release_date: date | None,
        availability: Availability,
        publish: Callable[[TranslationListings], None],
        cancelled: Callable[[], bool],
    ) -> None:
        snapshots: dict[str, _Snapshot | None] = {
            workno: _Snapshot(
                analysis, attribution, observed_at or self._clock(), source, release_date,
                availability,
            ),
        }
        failed: set[str] = set()
        online = self._provider is not None and workno.startswith("RJ")

        def local(code: str) -> _Snapshot | None:
            if code not in snapshots:
                snapshots[code] = self._read(code)
            return snapshots[code]

        def fetch(codes: Iterable[str], *, original_probe: bool = False) -> None:
            nonlocal online
            if not online or self._provider is None or cancelled():
                return
            pending = []
            for code in dict.fromkeys(codes):
                if not code.startswith("RJ") or (code == workno and not original_probe):
                    continue
                cached = local(code)
                if (
                    cached
                    and self._clock() - cached.observed_at < self._ttl
                    and (not original_probe or cached.analysis.editions is not None)
                ):
                    continue
                pending.append(code)
                if len(pending) == BONUS_BATCH_SIZE:
                    break
            if not pending:
                return
            try:
                batch = self._provider.fetch_listing_batch(tuple(pending))
            except WorkNotFoundError:
                return
            except DlsiteProviderError as exc:
                logger.info("Directed translation AJAX stopped: %s", exc)
                failed.update(pending)
                online = False
                return
            for code, listing in batch.listings.items():
                info = listing.translation_info
                seen = self._clock()
                snapshots[code] = _Snapshot(
                    self._analyzer.analyze(code, info),
                    listing.translation_attribution,
                    seen,
                    listing.source,
                    listing.work.release_date,
                    listing.work.availability,
                )
                if self._store is not None:
                    self._store.append_observation(
                        listing.work,
                        info,
                        source=listing.source,
                        observed_at=seen,
                        translation_attribution=listing.translation_attribution,
                        core_source=listing.core_source,
                    )
            if batch.malformed:
                online = False

        original = (
            workno
            if analysis.role is TranslationRole.ORIGINAL
            else next(
                (
                    r.target_workno
                    for r in analysis.relations
                    if r.relation_type is RelationType.TRANSLATION_OF
                ),
                None,
            )
        )
        parent = (
            workno
            if analysis.role is TranslationRole.TRANSLATION_PARENT
            else next(
                (
                    r.target_workno
                    for r in analysis.relations
                    if r.relation_type is RelationType.CHILD_OF_TRANSLATION
                ),
                None,
            )
        )
        if parent is None and analysis.role is not TranslationRole.ORIGINAL:
            parent = next(
                (
                    r.subject_workno
                    for r in history.incoming
                    if r.relation_type is RelationType.HAS_TRANSLATION_CHILD
                ),
                None,
            )
        incoming = tuple(
            r.subject_workno
            for r in history.incoming
            if r.relation_type is RelationType.TRANSLATION_OF
        )
        if analysis.role is None and incoming and parent is None:
            original = workno
        if (
            original is None
            and parent is None
            and analysis.role is not TranslationRole.TRANSLATION_CHILD
        ):
            return

        def project() -> TranslationListings:
            today = self._clock().astimezone(_JAPAN_TIME).date()

            def translator(
                code: str, record: _Snapshot | None, names: dict[str, str],
            ) -> TranslatorListing:
                status = TranslationDetailStatus.UNAVAILABLE
                attribution = record.attribution if record else None
                if record and (
                    record.availability == Availability.NOT_RELEASED
                    or (record.release_date is not None and record.release_date > today)
                ):
                    status = TranslationDetailStatus.NOT_RELEASED
                elif code in failed:
                    status = TranslationDetailStatus.FAILED
                elif record:
                    status = TranslationDetailStatus.AVAILABLE
                return TranslatorListing(
                    code,
                    names.get(code)
                    or (attribution.maker_name or attribution.maker_id if attribution else None),
                    status,
                )

            base = local(workno) if original == workno else None
            editions = (base.analysis.editions or ()) if base else ()
            parents = {edition.workno: edition.language for edition in editions}
            if parent:
                parents[parent] = analysis.language
            ungrouped: dict[str, _Snapshot | None] = {}
            if parent is None and analysis.role is TranslationRole.TRANSLATION_CHILD:
                ungrouped[workno] = local(workno)
            for code in incoming:
                record = local(code)
                if record and record.analysis.role is TranslationRole.TRANSLATION_PARENT:
                    parents.setdefault(code, record.analysis.language)
                elif record and record.analysis.role is TranslationRole.TRANSLATION_CHILD:
                    for edge in record.analysis.relations:
                        if edge.relation_type is RelationType.CHILD_OF_TRANSLATION:
                            parents.setdefault(edge.target_workno, record.analysis.language)
                            break
                    else:
                        ungrouped[code] = record
                elif code not in parents:
                    ungrouped[code] = record

            versions = []
            evidence = []
            records = {workno}
            discovered_original = original
            for code, language in parents.items():
                record = local(code)
                node = record.analysis if record else TranslationAnalysis()
                claimed_original = next(
                    (
                        edge.target_workno
                        for edge in node.relations
                        if edge.relation_type is RelationType.TRANSLATION_OF
                    ),
                    None,
                )
                usable = node.role is TranslationRole.TRANSLATION_PARENT and (
                    discovered_original is None or claimed_original in (None, discovered_original)
                )
                children = {
                    edge.target_workno
                    for edge in node.relations
                    if usable and edge.relation_type is RelationType.HAS_TRANSLATION_CHILD
                }
                if usable:
                    discovered_original = discovered_original or claimed_original
                    records.add(code)
                if (
                    self._history and not usable and node.role is None
                    and code not in snapshots_with_history and not cancelled()
                ):
                    snapshots_with_history[code] = (
                        history if code == workno else self._history.for_work(code)
                    )
                parent_history = snapshots_with_history.get(code, HistoricalRelations())
                if not usable and node.role is None:
                    children.update(
                        edge.subject_workno
                        for edge in parent_history.incoming
                        if edge.relation_type is RelationType.CHILD_OF_TRANSLATION
                    )
                if (
                    code == parent and analysis.role is TranslationRole.TRANSLATION_CHILD
                    and (usable or node.role is None)
                ):
                    children.add(workno)
                translators = []
                for child in sorted(children - {code, discovered_original}):
                    child_record = local(child)
                    if child_record:
                        child_edges = child_record.analysis.relations
                        if child_record.analysis.role not in (
                            None,
                            TranslationRole.TRANSLATION_CHILD,
                        ):
                            continue
                        if any(
                            (
                                edge.relation_type is RelationType.CHILD_OF_TRANSLATION
                                and edge.target_workno != code
                            )
                            or (
                                edge.relation_type is RelationType.TRANSLATION_OF
                                and discovered_original
                                and edge.target_workno != discovered_original
                            )
                            for edge in child_edges
                        ):
                            continue
                    records.add(child)
                    translators.append(translator(child, child_record, node.translator_names))
                versions.append(
                    LanguageListing(
                        code,
                        (node.language if usable else None) or language,
                        tuple(translators),
                        usable,
                    )
                )
            for code in sorted(records):
                if (record := local(code)) is None:
                    continue
                evidence.extend(
                    f"{edge.source_workno} → {edge.target_workno} · "
                    f"{item.attributes.get('field', edge.detection_source)} · "
                    f"{record.source} · {record.observed_at:%Y-%m-%d %H:%M %Z}"
                    for edge in record.analysis.relations
                    for item in edge.evidence
                )
            return TranslationListings(
                discovered_original,
                tuple(versions),
                tuple(
                    translator(code, record, {})
                    for code, record in ungrouped.items()
                    if code not in parents
                ),
                tuple(evidence),
                base is not None and base.analysis.editions is not None,
            )

        snapshots_with_history: dict[str, HistoricalRelations] = {}
        current = project()
        publish(current)
        if cancelled():
            return
        if original == workno and analysis.editions is None:
            # Older full cache rows predate edition metadata. Upgrade using one exact AJAX
            # observation while leaving the full metadata cache intact.
            previous = self._read(workno)
            if previous and previous.analysis.editions is not None:
                snapshots[workno] = previous
            else:
                fetch((workno,), original_probe=True)
            updated = project()
            if updated != current and not cancelled():
                publish(updated)
            current = updated
        fetch(version.workno for version in current.versions)
        if cancelled():
            return
        updated = project()
        if updated != current and not cancelled():
            publish(updated)
        current = updated
        fetch(
            child.workno
            for version in current.versions
            for child in version.translators
            if not child.name
        )
        if not cancelled() and (updated := project()) != current:
            publish(updated)

    def _read(self, code: str) -> _Snapshot | None:
        if self._store is None:
            return None
        cached = self._store.get(code)
        observation = self._store.latest_translation_observation(code)
        if (
            observation
            and observation.translation_json
            and (
                cached is None
                or cached.translation_info is None
                or observation.observed_at > cached.fetched_at
                or (
                    observation.observed_at == cached.fetched_at
                    and cached.translation_info.language_editions is None
                )
            )
        ):
            try:
                info = TranslationInfoSource.model_validate_json(observation.translation_json)
            except ValueError:
                logger.warning("Ignoring malformed translation observation for %s", code)
            else:
                return _Snapshot(
                    self._analyzer.analyze(code, info),
                    observation.translation_attribution,
                    observation.observed_at,
                    observation.source,
                    observation.release_date,
                    observation.availability,
                )
        if cached is None:
            return None
        return _Snapshot(
            self._analyzer.analyze(code, cached.translation_info),
            cached.translation_attribution,
            cached.fetched_at,
            cached.source,
            cached.work.release_date,
            cached.work.availability,
        )
