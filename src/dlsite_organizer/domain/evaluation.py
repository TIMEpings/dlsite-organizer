"""Derived, auditable dataset contracts for manual review analysis.

Evaluation records are rebuilt from append-only review events.  They are not
persisted and do not change candidate generation or confirmation semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from dlsite_organizer.domain.manual_review import (
    CandidateEvidenceSnapshot,
    CandidateReviewOutcome,
    ManualRelationType,
    ManualReviewProvenance,
)


class EvaluationRecordState(StrEnum):
    VALID = "VALID"
    UNSURE = "UNSURE"
    INVALID_SNAPSHOT = "INVALID_SNAPSHOT"


class EvaluationMetricState(StrEnum):
    INSUFFICIENT_LABELS = "INSUFFICIENT_LABELS"
    SUFFICIENT_LABELS = "SUFFICIENT_LABELS"


@dataclass(frozen=True)
class InvalidSnapshotReview:
    """A review row whose label is readable but whose snapshot is not.

    This read-only projection lets evaluation report a damaged latest snapshot
    without making the normal v0.8 history API accept malformed events.
    """

    id: int
    workno_a: str
    workno_b: str
    outcome: CandidateReviewOutcome
    relation_type: ManualRelationType | None
    subject_workno: str | None
    target_workno: str | None
    reviewed_at: datetime
    provenance: ManualReviewProvenance
    snapshot_error: str
    snapshot_schema_version: int | None = None


class EvaluationRecord(BaseModel):
    """One latest manual label for one canonical unordered pair."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workno_a: str
    workno_b: str
    review_event_id: int
    outcome: CandidateReviewOutcome
    relation_type: ManualRelationType | None = None
    subject_workno: str | None = None
    target_workno: str | None = None
    reviewed_at: datetime
    provenance: ManualReviewProvenance = ManualReviewProvenance.MANUAL_USER_REVIEW
    state: EvaluationRecordState
    evidence_snapshot: CandidateEvidenceSnapshot | None = None
    snapshot_schema_version: int | None = None
    snapshot_error: str | None = None

    @property
    def candidate_evaluated_at(self) -> datetime | None:
        return self.evidence_snapshot.candidate_evaluated_at if self.evidence_snapshot else None

    @property
    def same_maker_id(self) -> str | None:
        return self.evidence_snapshot.same_maker_id if self.evidence_snapshot else None

    @property
    def same_maker_name(self) -> str | None:
        return self.evidence_snapshot.same_maker_name if self.evidence_snapshot else None

    @property
    def same_regist_date(self) -> str | None:
        return self.evidence_snapshot.same_regist_date if self.evidence_snapshot else None

    @property
    def rj_numeric_distance(self) -> int | None:
        return self.evidence_snapshot.rj_numeric_distance if self.evidence_snapshot else None

    @property
    def local_maker_day_group_size(self) -> int | None:
        return (
            self.evidence_snapshot.local_maker_day_group_size
            if self.evidence_snapshot
            else None
        )

    @property
    def local_known_maker_work_count(self) -> int | None:
        return (
            self.evidence_snapshot.local_known_maker_work_count
            if self.evidence_snapshot
            else None
        )


class RelationTypeCount(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    relation_type: ManualRelationType
    count: int = Field(ge=0)


class EvidenceGroupSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    group_key: str
    related_count: int = Field(ge=0)
    not_related_count: int = Field(ge=0)
    decided_count: int = Field(ge=0)
    manual_related_rate: float | None = None
    metric_state: EvaluationMetricState


class EvaluationSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evaluation_policy_version: int = 1
    minimum_decided_labels: int = Field(default=20, ge=0)
    total_review_events: int = Field(ge=0)
    reviewed_pair_count: int = Field(ge=0)
    latest_related_count: int = Field(ge=0)
    latest_not_related_count: int = Field(ge=0)
    latest_unsure_count: int = Field(ge=0)
    decided_pair_count: int = Field(ge=0)
    valid_snapshot_count: int = Field(ge=0)
    invalid_snapshot_count: int = Field(ge=0)
    manual_related_rate: float | None = None
    metric_state: EvaluationMetricState
    unsure_share: float | None = None
    relation_type_counts: tuple[RelationTypeCount, ...] = ()
    manual_bonus_related_pairs: int = Field(default=0, ge=0)
    review_events_mean_per_pair: float = 0.0
    review_events_median_per_pair: float = 0.0
    review_events_max_per_pair: int = 0
    pairs_reviewed_once: int = 0
    pairs_reviewed_more_than_once: int = 0
    evidence_groups: tuple[EvidenceGroupSummary, ...] = ()

    @property
    def relation_type_distribution(self) -> dict[ManualRelationType, int]:
        """Convenient mapping view while keeping the DTO deterministic."""
        return {item.relation_type: item.count for item in self.relation_type_counts}


class EvaluationExportRecord(BaseModel):
    """Stable, privacy-preserving CSV projection of an evaluation record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workno_a: str
    workno_b: str
    outcome: CandidateReviewOutcome
    relation_type: ManualRelationType | None
    subject_workno: str | None
    target_workno: str | None
    reviewed_at: datetime
    review_event_id: int
    provenance: ManualReviewProvenance
    state: EvaluationRecordState
    snapshot_schema_version: int | None
    candidate_evaluated_at: datetime | None
    same_maker_id: str | None
    same_maker_name: str | None
    same_regist_date: str | None
    rj_numeric_distance: int | None
    local_maker_day_group_size: int | None
    local_known_maker_work_count: int | None
