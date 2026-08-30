"""Persistence for current metadata cache and append-only observations."""

from __future__ import annotations
# ruff: noqa

import json
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from dlsite_organizer.domain.work import Availability, Work
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
    series_name: Mapped[str | None] = mapped_column(String, nullable=True)
    cvs_json: Mapped[str] = mapped_column(Text, default="[]")
    tags_json: Mapped[str] = mapped_column(Text, default="[]")
    cover_url: Mapped[str | None] = mapped_column(String, nullable=True)
    availability: Mapped[str] = mapped_column(String(32), default=Availability.UNKNOWN.value)
    source_section: Mapped[str | None] = mapped_column(String(32), nullable=True)
    translation_json: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


@dataclass(frozen=True, slots=True)
class CachedMetadata:
    work: Work
    translation_info: TranslationInfoSource | None
    source: str
    fetched_at: datetime
    regist_datetime: datetime | None = None


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
                    series_name=row.series_name,
                    cvs=json.loads(row.cvs_json),
                    tags=json.loads(row.tags_json),
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
        try:
            with self._database.session() as session:
                caches = session.scalars(select(WorkMetadataCache)).all()
                observations = session.scalars(
                    select(MetadataObservation).order_by(
                        MetadataObservation.observed_at.desc(), MetadataObservation.id.desc()
                    )
                ).all()
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
    ) -> None:
        now = _as_utc(fetched_at) or datetime.now(UTC)
        normalized_regist_datetime = _as_utc(regist_datetime)
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
                row.series_name, row.cvs_json, row.tags_json = (
                    work.series_name,
                    json.dumps(work.cvs),
                    json.dumps(work.tags),
                )
                row.cover_url, row.availability, row.source_section = (
                    work.cover_url,
                    work.availability.value,
                    work.source_section,
                )
                row.translation_json = (
                    translation_info.model_dump_json() if translation_info else None
                )
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
    ) -> None:
        normalized_observed_at = _as_utc(observed_at) or datetime.now(UTC)
        normalized_regist_datetime = _as_utc(regist_datetime)
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
                        age_category=str(age_category) if age_category is not None else None,
                        availability=work.availability.value,
                        source=source,
                        translation_json=translation_info.model_dump_json()
                        if translation_info
                        else None,
                        observed_at=normalized_observed_at,
                    )
                )
                session.commit()
        except Exception:
            logger.exception("Metadata observation write failed for %s", work.workno)


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
