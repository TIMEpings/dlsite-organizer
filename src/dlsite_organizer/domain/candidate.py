"""Explainable, non-confirmed relation candidate contracts."""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dlsite_organizer.domain.work_code import WorkCode


class CandidateEvidenceKind(StrEnum):
    SAME_MAKER_KEY = "same_maker_key"
    SAME_MAKER_ID = "same_maker_id"
    SAME_MAKER_NAME = "same_maker_name"
    SAME_REGIST_DATE = "same_regist_date"
    RJ_NUMERIC_DISTANCE = "rj_numeric_distance"
    LOCAL_MAKER_DAY_GROUP_SIZE = "local_maker_day_group_size"
    LOCAL_KNOWN_MAKER_WORK_COUNT = "local_known_maker_work_count"
    REGISTRATION_YEAR = "registration_year"


class CandidateEvidencePolarity(StrEnum):
    SUPPORTING = "supporting"
    CONTEXT = "context"
    CONFOUNDING = "confounding"


class CandidateType(StrEnum):
    RELATED_WORK_CANDIDATE = "related_work_candidate"


class CandidateSearchState(StrEnum):
    FOUND = "found"
    NONE = "none"
    INSUFFICIENT_METADATA = "insufficient_metadata"


class CandidateSnapshotSource(StrEnum):
    CURRENT_CACHE = "current_cache"
    HISTORICAL_OBSERVATION = "historical_observation"


class CandidateEvidence(BaseModel):
    """One structured observation; it is not a score or probability."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: CandidateEvidenceKind
    value: Any = None
    polarity: CandidateEvidencePolarity = CandidateEvidencePolarity.SUPPORTING
    description: str = Field(min_length=1)
    provenance: str | None = None


class KnownWorkSnapshot(BaseModel):
    """Latest locally known metadata for one work."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workno: str
    title: str
    maker_id: str | None = None
    maker_name: str | None = None
    regist_datetime: datetime | None = None
    source: CandidateSnapshotSource
    observed_at: datetime | None = None
    fetched_at: datetime | None = None

    @field_validator("workno")
    @classmethod
    def normalize_workno(cls, value: str) -> str:
        return str(WorkCode.parse(value, allowed_prefixes={"RJ"}))

    @field_validator("regist_datetime", "observed_at", "fetched_at")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @property
    def regist_date(self) -> date | None:
        return self.regist_datetime.date() if self.regist_datetime else None

    @property
    def registration_year(self) -> int | None:
        return self.regist_datetime.year if self.regist_datetime else None


class CandidateRelation(BaseModel):
    """A derived, undirected relation candidate for a queried work."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_workno: str
    target_workno: str
    candidate_type: CandidateType = CandidateType.RELATED_WORK_CANDIDATE
    supporting_evidence: tuple[CandidateEvidence, ...] = ()
    context: tuple[CandidateEvidence, ...] = ()
    evaluated_at: datetime
    provenance: str = "generated from local metadata"
    source_snapshot: KnownWorkSnapshot | None = None
    target_snapshot: KnownWorkSnapshot | None = None

    @field_validator("source_workno", "target_workno")
    @classmethod
    def normalize_workno(cls, value: str) -> str:
        return str(WorkCode.parse(value, allowed_prefixes={"RJ"}))

    @field_validator("evaluated_at")
    @classmethod
    def normalize_evaluated_at(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class CandidateSearchResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_work: KnownWorkSnapshot | None = None
    candidates: tuple[CandidateRelation, ...] = ()
    state: CandidateSearchState = CandidateSearchState.NONE
    reason: str | None = None
    evaluated_at: datetime
    truncated: bool = False
    total_candidate_count: int = 0

