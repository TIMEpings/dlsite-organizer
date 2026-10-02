"""Persistence for current metadata cache and append-only observations."""

from __future__ import annotations
# ruff: noqa

import json
import logging
import sqlite3
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import cast

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, case, func, or_, select
from sqlalchemy.orm import Mapped, mapped_column

from dlsite_organizer.domain.bonus import BonusEvidenceSnapshot
from dlsite_organizer.domain.work import (
    AgeCategory,
    Availability,
    TranslationAttribution,
    Work,
    WorkLanguage,
    normalize_age_category,
    normalize_language_code,
)
from dlsite_organizer.domain.candidate import CandidateSnapshotSource, KnownWorkSnapshot
from dlsite_organizer.persistence.database import Base, Database
from dlsite_organizer.providers.dlsite.sources import TranslationInfoSource

logger = logging.getLogger(__name__)


class WorkMetadataCache(Base):
    __tablename__ = "work_metadata_cache"
    workno: Mapped[str] = mapped_column(String(16), primary_key=True)
    title: Mapped[str] = mapped_column(String)
    maker_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    maker_name: Mapped[str | None] = mapped_column(String, nullable=True)
    release_date: Mapped[date | None] = mapped_column(nullable=True)
    regist_datetime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    work_regist_datetime: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    series_name: Mapped[str | None] = mapped_column(String, nullable=True)
    cvs_json: Mapped[str] = mapped_column(Text, default="[]")
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    cover_url: Mapped[str | None] = mapped_column(String, nullable=True)
    availability: Mapped[str] = mapped_column(String(32), default=Availability.UNKNOWN.value)
    source_section: Mapped[str | None] = mapped_column(String(32), nullable=True)
    translation_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    age_category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    translation_attribution_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    core_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source: Mapped[str] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MetadataObservation(Base):
    __tablename__ = "metadata_observations"
    __table_args__ = (UniqueConstraint("id", name="uq_metadata_observation_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workno: Mapped[str] = mapped_column(String(16), index=True)
    title: Mapped[str] = mapped_column(String)
    maker_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    maker_name: Mapped[str | None] = mapped_column(String, nullable=True)
    release_date: Mapped[date | None] = mapped_column(nullable=True)
    regist_datetime: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    work_type: Mapped[str | None] = mapped_column(String, nullable=True)
    age_category: Mapped[str | None] = mapped_column(String, nullable=True)
    availability: Mapped[str] = mapped_column(String(32), default=Availability.UNKNOWN.value)
    source: Mapped[str] = mapped_column(String(64))
    translation_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    bonus_evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    series_name: Mapped[str | None] = mapped_column(String, nullable=True)
    cvs_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    translation_attribution_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    core_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_source: Mapped[str | None] = mapped_column(String(64), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    @property
    def bonus_evidence(self) -> BonusEvidenceSnapshot | None:
        """Decode the optional typed bonus snapshot without changing legacy rows."""
        return _parse_bonus_evidence(self.bonus_evidence_json)

    @property
    def cvs(self) -> tuple[str, ...] | None:
        """Return observed CVs, preserving NULL as not observed."""
        return _parse_string_list(self.cvs_json)

    @property
    def tags(self) -> tuple[str, ...] | None:
        """Return observed tags, preserving NULL as not observed."""
        return _parse_string_list(self.tags_json)

    @property
    def cv_names(self) -> tuple[str, ...] | None:
        return self.cvs

    @property
    def tag_names(self) -> tuple[str, ...] | None:
        return self.tags

    @property
    def translation_attribution(self) -> TranslationAttribution | None:
        return _parse_translation_attribution(self.translation_attribution_json)


@dataclass(frozen=True, slots=True)
class CachedMetadata:
    work: Work
    translation_info: TranslationInfoSource | None
    source: str
    fetched_at: datetime
    regist_datetime: datetime | None = None
    translation_attribution: TranslationAttribution | None = None
    core_source: str | None = None
    metadata_source: str | None = None


@dataclass(frozen=True, slots=True)
class BonusObservation:
    """Local-only historical bonus evidence tied to one observation row."""

    observation_id: int
    workno: str
    observed_at: datetime
    source: str
    evidence: BonusEvidenceSnapshot | None


@dataclass(frozen=True, slots=True)
class RelationObservation:
    """Detached historical fields needed by the explicit relation evaluator."""

    id: int
    workno: str
    translation_json: str | None
    source: str
    observed_at: datetime


class MetadataStore:
    def __init__(self, database: Database) -> None:
        self._database = database
        self._database.initialize_metadata()

    def get(self, workno: str) -> CachedMetadata | None:
        try:
            with self._database.session() as session:
                row = session.get(WorkMetadataCache, workno)
                if row is None:
                    return None
                translation = (
                    TranslationInfoSource.model_validate(json.loads(row.translation_json))
                    if row.translation_json
                    else None
                )
                work = Work(
                    workno=row.workno,
                    title=row.title,
                    maker_id=row.maker_id,
                    maker_name=row.maker_name,
                    release_date=row.release_date,
                    regist_datetime=_as_utc(row.work_regist_datetime),
                    series_name=row.series_name,
                    cvs=list(_parse_string_list(row.cvs_json) or ()),
                    tags=list(_parse_string_list(row.tags_json) or ()),
                    language=normalize_language_code(row.language),
                    age_category=normalize_age_category(row.age_category),
                    cover_url=row.cover_url,
                    availability=Availability(row.availability),
                    source_section=row.source_section,
                )
                fetched_at = _as_utc(row.fetched_at)
                if fetched_at is None:
                    raise ValueError("cached metadata is missing fetched_at")
                return CachedMetadata(
                    work,
                    translation,
                    row.source,
                    fetched_at,
                    _as_utc(row.regist_datetime),
                    _parse_translation_attribution(row.translation_attribution_json),
                    row.core_source,
                    row.metadata_source,
                )
        except Exception:
            logger.exception("Metadata cache read failed for %s", workno)
            return None

    def list_observations(self, workno: str) -> tuple[MetadataObservation, ...]:
        """Return historical rows in deterministic observation order."""
        try:
            with self._database.session() as session:
                rows = session.scalars(
                    select(MetadataObservation)
                    .where(MetadataObservation.workno == workno)
                    .order_by(
                        MetadataObservation.observed_at.asc(),
                        MetadataObservation.id.asc(),
                    )
                ).all()
                for row in rows:
                    row.observed_at = _as_utc(row.observed_at)  # type: ignore[assignment]
                    row.regist_datetime = _as_utc(row.regist_datetime)
                return tuple(rows)
        except Exception:
            logger.exception("Metadata observations read failed for %s", workno)
            return ()

    def list_bonus_observations(self, workno: str) -> tuple[BonusObservation, ...]:
        """Return typed bonus evidence from local history without provider calls."""
        return tuple(
            BonusObservation(
                observation_id=row.id,
                workno=row.workno,
                observed_at=row.observed_at,
                source=row.source,
                evidence=_parse_bonus_evidence(row.bonus_evidence_json),
            )
            for row in self.list_observations(workno)
        )

    def list_all_observations(self) -> tuple[MetadataObservation, ...]:
        """Return all persisted observations for historical relation derivation."""
        try:
            with self._database.session() as session:
                rows = session.scalars(
                    select(MetadataObservation).order_by(
                        MetadataObservation.observed_at.asc(), MetadataObservation.id.asc()
                    )
                ).all()
                for row in rows:
                    row.observed_at = _as_utc(row.observed_at)  # type: ignore[assignment]
                return tuple(rows)
        except Exception:
            logger.exception("All metadata observations read failed")
            return ()

    def list_known_work_summaries(self) -> tuple[KnownWorkSnapshot, ...]:
        """Return one latest local metadata snapshot per known work.

        Current cache rows take precedence.  A work without a current cache is
        represented by its most recently observed historical row instead.
        """
        return self._known_work_summaries()

    def _known_work_summaries(self, worknos=None, *, session=None) -> tuple[KnownWorkSnapshot, ...]:
        """Keep cache/newest-valid-observation authority for a constrained universe."""
        try:
            with (self._database.session() if session is None else nullcontext(session)) as session:
                cache_query = select(WorkMetadataCache)
                observation_query = select(MetadataObservation).order_by(
                    MetadataObservation.observed_at.desc(), MetadataObservation.id.desc()
                )
                if worknos is not None:
                    cache_query = cache_query.where(WorkMetadataCache.workno.in_(worknos))
                    observation_query = observation_query.where(MetadataObservation.workno.in_(worknos))
                caches = session.scalars(cache_query).all()
                observations = session.scalars(observation_query).all()
                snapshots: dict[str, KnownWorkSnapshot] = {}
                for row in caches:
                    try:
                        snapshots[row.workno] = KnownWorkSnapshot(
                            workno=row.workno,
                            title=row.title,
                            maker_id=row.maker_id,
                            maker_name=row.maker_name,
                            regist_datetime=_as_utc(row.regist_datetime),
                            source=CandidateSnapshotSource.CURRENT_CACHE,
                            fetched_at=_as_utc(row.fetched_at),
                        )
                    except Exception:
                        logger.warning(
                            "Skipping malformed current metadata cache %s",
                            row.workno,
                            exc_info=True,
                        )
                for row in observations:
                    if row.workno in snapshots:
                        continue
                    try:
                        snapshots[row.workno] = KnownWorkSnapshot(
                            workno=row.workno,
                            title=row.title,
                            maker_id=row.maker_id,
                            maker_name=row.maker_name,
                            regist_datetime=_as_utc(row.regist_datetime),
                            source=CandidateSnapshotSource.HISTORICAL_OBSERVATION,
                            observed_at=_as_utc(row.observed_at),
                        )
                    except Exception:
                        logger.warning(
                            "Skipping malformed historical metadata observation %s",
                            row.id,
                            exc_info=True,
                        )
                return tuple(sorted(snapshots.values(), key=lambda item: item.workno))
        except Exception:
            logger.exception("Known metadata summary read failed")
            return ()

    def list_relation_candidate_summaries(self, workno: str) -> tuple[KnownWorkSnapshot, ...]:
        """Load the source and its maker context, across all registration dates.

        Candidate policy/evidence remain in the service. Historical maker matches
        only constrain work identities: complete effective snapshots are then
        selected using the same authority rules as the full-universe API.
        """
        from dlsite_organizer.services.candidate_relations import maker_identity_key, normalize_maker_name

        source = next(iter(self._known_work_summaries((workno,))), None)
        if source is None or (identity := maker_identity_key(source)) is None:
            return (source,) if source is not None else ()
        try:
            with self._database.session() as session:
                # This stateless function reproduces Python's NFKC/strip rule.
                # It stores no metadata or derived results and changes no schema.
                connection = cast(sqlite3.Connection, session.connection().connection.driver_connection)
                connection.create_function(
                    "dlsite_normalize_maker_name", 1, normalize_maker_name, deterministic=True
                )
                def matching_maker(model):
                    if identity[0] == "id":
                        return model.maker_id == identity[1]
                    return (
                        or_(model.maker_id.is_(None), model.maker_id == "")
                        & (func.dlsite_normalize_maker_name(model.maker_name) == identity[1])
                    )
                worknos = select(WorkMetadataCache.workno).where(
                    matching_maker(WorkMetadataCache)
                ).union(
                    select(MetadataObservation.workno).where(matching_maker(MetadataObservation))
                )
                # Execute on this connection so its normalization function is
                # available for both constrained reads, even with pooled callers.
                snapshots = self._known_work_summaries(worknos, session=session)
            return tuple(item for item in snapshots if maker_identity_key(item) == identity)
        except Exception:
            logger.exception("Relation candidate context read failed for %s", workno)
            return ()

    def list_relation_observations(self, workno: str) -> tuple[RelationObservation, ...]:
        """Load only outgoing or potentially incoming historical evidence.

        JSON filtering is a recall seam, never a confirmation decision. The
        existing DTO and topology evaluator still validate every selected row.
        """
        try:
            with self._database.session() as session:
                rows = session.execute(
                    select(
                        MetadataObservation.id, MetadataObservation.workno,
                        MetadataObservation.translation_json, MetadataObservation.source,
                        MetadataObservation.observed_at,
                    ).where(_relation_context_predicate(MetadataObservation, workno)).order_by(
                        MetadataObservation.observed_at.asc(), MetadataObservation.id.asc()
                    )
                )
                return tuple(
                    RelationObservation(row.id, row.workno, row.translation_json, row.source,
                                        cast(datetime, _as_utc(row.observed_at)))
                    for row in rows
                )
        except Exception:
            # Preserve the existing list_all_observations read-error contract.
            logger.exception("Relation observations read failed for %s", workno)
            return ()

    def list_current_relation_pairs_for_work(self, workno: str) -> tuple[tuple[str, str], ...]:
        """Return current confirmed exclusions involving this work only."""
        from dlsite_organizer.services.translation_relations import TranslationRelationService

        pairs: set[tuple[str, str]] = set()
        try:
            translator = TranslationRelationService()
            with self._database.session() as session:
                rows = session.execute(
                    select(WorkMetadataCache.workno, WorkMetadataCache.translation_json).where(
                        _relation_context_predicate(WorkMetadataCache, workno)
                    )
                )
                for row in rows:
                    if not row.translation_json:
                        continue
                    try:
                        info = TranslationInfoSource.model_validate(json.loads(row.translation_json))
                        for relation in translator.analyze(row.workno, info).relations:
                            if workno in (relation.source_workno, relation.target_workno):
                                left, right = sorted((relation.source_workno, relation.target_workno))
                                pairs.add((left, right))
                    except Exception:
                        logger.warning("Skipping malformed current translation cache %s", row.workno)
            return tuple(sorted(pairs))
        except Exception:
            logger.exception("Current relation read failed")
            return ()

    def list_current_relation_pairs(self) -> tuple[tuple[str, str], ...]:
        """Return canonical pairs from current cached explicit relations."""
        pairs: set[tuple[str, str]] = set()
        try:
            from dlsite_organizer.services.translation_relations import TranslationRelationService

            translator = TranslationRelationService()
            with self._database.session() as session:
                rows = session.scalars(select(WorkMetadataCache)).all()
                for row in rows:
                    if not row.translation_json:
                        continue
                    try:
                        info = TranslationInfoSource.model_validate(json.loads(row.translation_json))
                        for relation in translator.analyze(row.workno, info).relations:
                            pairs.add(
                                tuple(
                                    sorted((relation.source_workno, relation.target_workno))
                                )  # type: ignore[arg-type]
                            )
                    except Exception:
                        logger.warning("Skipping malformed current translation cache %s", row.workno)
            return tuple(sorted(pairs))
        except Exception:
            logger.exception("Current relation read failed")
            return ()

    def save(
        self,
        work: Work,
        translation_info: TranslationInfoSource | None,
        *,
        source: str,
        fetched_at: datetime | None = None,
        work_type: str | None = None,
        age_category: str | int | None = None,
        regist_datetime: datetime | None = None,
        translation_attribution: TranslationAttribution | None = None,
        core_source: str | None = None,
        metadata_source: str | None = None,
    ) -> None:
        now = _as_utc(fetched_at) or datetime.now(UTC)
        normalized_regist_datetime = _as_utc(regist_datetime or work.regist_datetime)
        stored_age_category = _storage_age_category(age_category, work.age_category)
        stored_language = _storage_language(work.language)
        try:
            with self._database.session() as session:
                row = session.get(WorkMetadataCache, work.workno) or WorkMetadataCache(
                    workno=work.workno
                )
                row.title, row.maker_id, row.maker_name = work.title, work.maker_id, work.maker_name
                row.release_date, row.regist_datetime = (
                    work.release_date,
                    normalized_regist_datetime,
                )
                row.work_regist_datetime = _as_utc(work.regist_datetime)
                row.series_name, row.cvs_json, row.tags_json = (
                    work.series_name,
                    json.dumps(work.cvs, ensure_ascii=False),
                    json.dumps(work.tags, ensure_ascii=False),
                )
                row.cover_url, row.availability, row.source_section = (
                    work.cover_url,
                    work.availability.value,
                    work.source_section,
                )
                row.age_category, row.language = stored_age_category, stored_language
                row.translation_json = (
                    translation_info.model_dump_json() if translation_info else None
                )
                row.translation_attribution_json = (
                    translation_attribution.model_dump_json()
                    if translation_attribution is not None
                    else None
                )
                row.core_source, row.metadata_source = core_source, metadata_source
                row.source, row.fetched_at = source, now
                session.add(row)
                session.commit()
        except Exception:
            logger.exception("Metadata cache write failed for %s", work.workno)

    def append_observation(
        self,
        work: Work,
        translation_info: TranslationInfoSource | None,
        *,
        source: str,
        observed_at: datetime | None = None,
        work_type: str | None = None,
        age_category: str | int | None = None,
        regist_datetime: datetime | None = None,
        bonus_evidence: BonusEvidenceSnapshot | None = None,
        translation_attribution: TranslationAttribution | None = None,
        core_source: str | None = None,
        metadata_source: str | None = None,
    ) -> None:
        normalized_observed_at = _as_utc(observed_at) or datetime.now(UTC)
        normalized_regist_datetime = _as_utc(regist_datetime or work.regist_datetime)
        stored_age_category = _storage_age_category(age_category, work.age_category)
        stored_language = _storage_language(work.language)
        try:
            with self._database.session() as session:
                session.add(
                    MetadataObservation(
                        workno=work.workno,
                        title=work.title,
                        maker_id=work.maker_id,
                        maker_name=work.maker_name,
                        release_date=work.release_date,
                        regist_datetime=normalized_regist_datetime,
                        work_type=work_type,
                        age_category=stored_age_category,
                        availability=work.availability.value,
                        source=source,
                        translation_json=translation_info.model_dump_json()
                        if translation_info
                        else None,
                        series_name=work.series_name,
                        cvs_json=json.dumps(work.cvs, ensure_ascii=False),
                        tags_json=json.dumps(work.tags, ensure_ascii=False),
                        language=stored_language,
                        translation_attribution_json=(
                            translation_attribution.model_dump_json()
                            if translation_attribution is not None
                            else None
                        ),
                        core_source=core_source,
                        metadata_source=metadata_source,
                        bonus_evidence_json=bonus_evidence.model_dump_json()
                        if bonus_evidence is not None
                        else None,
                        observed_at=normalized_observed_at,
                    )
                )
                session.commit()
        except Exception:
            logger.exception("Metadata observation write failed for %s", work.workno)


def _relation_context_predicate(model, workno: str):
    # Decode before matching: escaped JSON, lowercase prefixes, and surrounding
    # whitespace must not hide an incoming reference. Invalid JSON is safe and
    # cannot establish an edge. Extra string fields can only add false positives.
    values = func.json_tree(
        case((func.json_valid(model.translation_json), model.translation_json), else_="{}")
    ).table_valued("value", "type")
    references = select(1).select_from(values).where(
        values.c.type == "text", func.instr(func.upper(values.c.value), workno) > 0
    ).exists()
    return or_(func.instr(func.upper(model.workno), workno) > 0, references)


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalize datetimes before SQLite strips timezone metadata.

    DLsite's current ``regist_date`` source values are naive timestamps.  The
    application contract treats those values as UTC rather than silently using
    the machine's local timezone.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _parse_bonus_evidence(value: str | None) -> BonusEvidenceSnapshot | None:
    """Parse a snapshot while treating absent/legacy or corrupt data as unknown."""
    if not value:
        return None
    try:
        return BonusEvidenceSnapshot.model_validate_json(value)
    except Exception:
        logger.warning("Ignoring malformed persisted bonus evidence snapshot", exc_info=True)
        return None


def _parse_string_list(value: str | None) -> tuple[str, ...] | None:
    """Decode a nullable JSON string list without turning corruption into data."""
    if value is None:
        return None
    try:
        decoded = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        logger.warning("Ignoring malformed persisted metadata string list", exc_info=True)
        return None
    if not isinstance(decoded, list):
        return None
    return tuple(
        dict.fromkeys(
            item.strip()
            for item in decoded
            if isinstance(item, str) and item.strip()
        )
    )


def _parse_translation_attribution(value: str | None) -> TranslationAttribution | None:
    if not value:
        return None
    try:
        return TranslationAttribution.model_validate_json(value)
    except Exception:
        logger.warning("Ignoring malformed persisted translation attribution", exc_info=True)
        return None


def _storage_age_category(
    value: str | int | AgeCategory | None,
    fallback: AgeCategory,
) -> str:
    if value is None:
        return fallback.value
    if isinstance(value, AgeCategory):
        return value.value
    return str(value).strip() or fallback.value


def _storage_language(value: WorkLanguage | str | None) -> str | None:
    if value is None:
        return None
    return value.value if isinstance(value, WorkLanguage) else str(value).strip() or None
