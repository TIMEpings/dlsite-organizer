"""Persistence for current metadata cache and append-only observations."""
from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime
from dataclasses import dataclass

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column

from dlsite_organizer.domain.work import Availability, Work
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


class MetadataStore:
    def __init__(self, database: Database) -> None:
        self._database = database

    def get(self, workno: str) -> CachedMetadata | None:
        try:
            with self._database.session() as session:
                row = session.get(WorkMetadataCache, workno)
                if row is None:
                    return None
                translation = TranslationInfoSource.model_validate(json.loads(row.translation_json)) if row.translation_json else None
                work = Work(workno=row.workno, title=row.title, maker_id=row.maker_id, maker_name=row.maker_name,
                            release_date=row.release_date, series_name=row.series_name,
                            cvs=json.loads(row.cvs_json), tags=json.loads(row.tags_json), cover_url=row.cover_url,
                            availability=Availability(row.availability), source_section=row.source_section)
                return CachedMetadata(work, translation, row.source, row.fetched_at)
        except Exception:
            logger.exception("Metadata cache read failed for %s", workno)
            return None

    def save(self, work: Work, translation_info: TranslationInfoSource | None, *, source: str,
             fetched_at: datetime | None = None, work_type: str | None = None,
             age_category: str | int | None = None, regist_datetime: datetime | None = None) -> None:
        now = fetched_at or datetime.now(UTC)
        try:
            with self._database.session() as session:
                row = session.get(WorkMetadataCache, work.workno) or WorkMetadataCache(workno=work.workno)
                row.title, row.maker_id, row.maker_name = work.title, work.maker_id, work.maker_name
                row.release_date, row.regist_datetime = work.release_date, regist_datetime
                row.series_name, row.cvs_json, row.tags_json = work.series_name, json.dumps(work.cvs), json.dumps(work.tags)
                row.cover_url, row.availability, row.source_section = work.cover_url, work.availability.value, work.source_section
                row.translation_json = translation_info.model_dump_json() if translation_info else None
                row.source, row.fetched_at = source, now
                session.add(row); session.commit()
        except Exception:
            logger.exception("Metadata cache write failed for %s", work.workno)

    def append_observation(self, work: Work, translation_info: TranslationInfoSource | None, *, source: str,
                           observed_at: datetime | None = None, work_type: str | None = None,
                           age_category: str | int | None = None, regist_datetime: datetime | None = None) -> None:
        try:
            with self._database.session() as session:
                session.add(MetadataObservation(workno=work.workno, title=work.title, maker_id=work.maker_id,
                    maker_name=work.maker_name, release_date=work.release_date, regist_datetime=regist_datetime,
                    work_type=work_type, age_category=str(age_category) if age_category is not None else None,
                    availability=work.availability.value, source=source,
                    translation_json=translation_info.model_dump_json() if translation_info else None,
                    observed_at=observed_at or datetime.now(UTC)))
                session.commit()
        except Exception:
            logger.exception("Metadata observation write failed for %s", work.workno)
