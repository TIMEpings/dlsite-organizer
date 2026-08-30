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


class CandidatePolicyDescriptor(BaseModel):
    """Stable business identity and semantic version of a discovery policy."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str = Field(min_length=1)
    policy_version: int = Field(ge=1)


class CandidatePolicyProvenance(BaseModel):
    """Discovery policy identity captured alongside a candidate review."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_id: str = Field(min_length=1)
    policy_version: int = Field(ge=1)
    application_version: str = Field(min_length=1)


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
    policy_provenance: CandidatePolicyProvenance | None = None

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
    policy_provenance: CandidatePolicyProvenance | None = None


class CandidateQueueReviewState(StrEnum):
    """Latest manual state of a derived queue pair."""

    UNREVIEWED = "unreviewed"
    RELATED = "related"
    NOT_RELATED = "not_related"
    UNSURE = "unsure"


class CandidateQueueFilter(StrEnum):
    UNREVIEWED = "unreviewed"
    UNSURE = "unsure"
    REVIEWED = "reviewed"
    ALL = "all"
    RELATED = "related"
    NOT_RELATED = "not_related"


class CandidateReviewQueueItem(BaseModel):
    """Typed, derived presentation item for one canonical candidate pair."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workno_a: str
    workno_b: str
    canonical_pair: tuple[str, str]
    work_a_title: str
    work_b_title: str
    maker_identity: str
    maker_display: str
    regist_date: date
    rj_numeric_distance: int | None = None
    supporting_evidence: tuple[CandidateEvidence, ...] = ()
    context: tuple[CandidateEvidence, ...] = ()
    policy_provenance: CandidatePolicyProvenance
    # Kept as an object here to avoid the candidate/manual-review import cycle;
    # queue construction always supplies a ManualReviewEvent.
    latest_manual_review: Any = None
    review_event_count: int = 0
    source_snapshot: KnownWorkSnapshot
    target_snapshot: KnownWorkSnapshot

    @property
    def review_state(self) -> CandidateQueueReviewState:
        if self.latest_manual_review is None:
            return CandidateQueueReviewState.UNREVIEWED
        return CandidateQueueReviewState(self.latest_manual_review.outcome.value)

    @property
    def candidate_policy_id(self) -> str:
        return self.policy_provenance.policy_id

    @property
    def candidate_policy_version(self) -> int:
        return self.policy_provenance.policy_version

    @property
    def application_version(self) -> str:
        return self.policy_provenance.application_version


class CandidateReviewQueueResult(BaseModel):
    """One deterministic, paginated build of the local review queue."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    policy_provenance: CandidatePolicyProvenance
    filter: CandidateQueueFilter
    items: tuple[CandidateReviewQueueItem, ...] = ()
    total_count: int = 0
    returned_count: int = 0
    generated_at: datetime
    known_work_count: int = 0
    eligible_work_count: int = 0
    excluded_insufficient_metadata_count: int = 0
    unreviewed_count: int = 0
    related_count: int = 0
    not_related_count: int = 0
    unsure_count: int = 0
    truncated: bool = False

    @property
    def total_pair_count(self) -> int:
        return self.total_count

    @property
    def candidate_policy_id(self) -> str:
        return self.policy_provenance.policy_id

    @property
    def candidate_policy_version(self) -> int:
        return self.policy_provenance.policy_version

    @property
    def application_version(self) -> str:
        return self.policy_provenance.application_version


# Explicit aliases keep the application vocabulary readable at call sites.
CandidateReviewQueueFilter = CandidateQueueFilter
CandidateReviewState = CandidateQueueReviewState
