"""Local, human-authored labels for derived relation candidates.

Manual labels are deliberately a separate provenance layer from facts read
from DLsite.  Events are append-only; consumers select the latest event for a
canonical unordered pair when they need the current annotation.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from dlsite_organizer.domain.candidate import CandidatePolicyProvenance
from dlsite_organizer.domain.work_code import WorkCode


class CandidateReviewOutcome(StrEnum):
    RELATED = "related"
    NOT_RELATED = "not_related"
    UNSURE = "unsure"


class ManualRelationType(StrEnum):
    TRANSLATION_OF = "translation_of"
    BONUS_OF = "bonus_of"
    LIMITED_BONUS_OF = "limited_bonus_of"
    CHILD_OF = "child_of"
    BUNDLED_WITH = "bundled_with"
    OTHER = "other"
    UNKNOWN = "unknown"
    SAME_SERIES = "same_series"
    SAME_WORK_VARIANT = "same_work_variant"
    SAME_WORK_LANGUAGE_VARIANT = "same_work_language_variant"
    INCLUDED_IN = "included_in"

    @property
    def is_directional(self) -> bool:
        return is_manual_relation_directional(self)


# This is the authoritative directionality contract for manual relation
# labels.  Validation, UI behavior, and consumers should use this mapping (or
# ``ManualRelationType.is_directional``) rather than maintaining their own
# relation-type sets.
MANUAL_RELATION_DIRECTIONALITY: Mapping[ManualRelationType, bool] = MappingProxyType(
    {
        ManualRelationType.TRANSLATION_OF: True,
        ManualRelationType.BONUS_OF: True,
        ManualRelationType.LIMITED_BONUS_OF: True,
        ManualRelationType.CHILD_OF: True,
        ManualRelationType.INCLUDED_IN: True,
        ManualRelationType.BUNDLED_WITH: False,
        ManualRelationType.OTHER: False,
        ManualRelationType.UNKNOWN: False,
        ManualRelationType.SAME_SERIES: False,
        ManualRelationType.SAME_WORK_VARIANT: False,
        ManualRelationType.SAME_WORK_LANGUAGE_VARIANT: False,
    }
)


def is_manual_relation_directional(relation_type: ManualRelationType) -> bool:
    """Return whether a manual relation stores an explicit subject/target."""
    try:
        return MANUAL_RELATION_DIRECTIONALITY[relation_type]
    except KeyError as exc:  # Keep additions from silently bypassing validation.
        raise ValueError(f"Unsupported manual relation type: {relation_type!r}") from exc


class ManualReviewProvenance(StrEnum):
    MANUAL_USER_REVIEW = "MANUAL_USER_REVIEW"


class CandidateEvidenceSnapshot(BaseModel):
    """Immutable review-time copy of the evidence visible to the reviewer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    policy_provenance: CandidatePolicyProvenance | None = None
    same_maker_id: str | None = None
    same_maker_name: str | None = None
    same_regist_date: str | None = None
    rj_numeric_distance: int | None = None
    local_maker_day_group_size: int | None = None
    local_known_maker_work_count: int | None = None
    candidate_evaluated_at: datetime | None = None
    supporting_evidence: tuple[dict[str, Any], ...] = ()
    context: tuple[dict[str, Any], ...] = ()

    @model_validator(mode="after")
    def normalize_time(self) -> CandidateEvidenceSnapshot:
        if self.schema_version not in {1, 2}:
            raise ValueError(
                f"Unsupported candidate evidence snapshot schema: {self.schema_version}"
            )
        if self.schema_version == 1 and self.policy_provenance is not None:
            raise ValueError("Schema v1 snapshots must not contain policy provenance")
        if self.schema_version == 2 and self.policy_provenance is None:
            raise ValueError("Schema v2 snapshots require policy provenance")
        if self.candidate_evaluated_at is not None and self.candidate_evaluated_at.tzinfo is None:
            object.__setattr__(
                self,
                "candidate_evaluated_at",
                self.candidate_evaluated_at.replace(tzinfo=UTC),
            )
        return self


def parse_candidate_evidence_snapshot(
    payload: str | Mapping[str, Any],
) -> CandidateEvidenceSnapshot:
    """Parse supported snapshot schemas explicitly; never migrate stored JSON."""
    raw: Mapping[str, Any]
    if isinstance(payload, str):
        decoded = json.loads(payload)
        if not isinstance(decoded, dict):
            raise ValueError("Candidate evidence snapshot must be a JSON object")
        raw = decoded
    else:
        raw = payload
    schema_version = raw.get("schema_version")
    if schema_version == 1:
        return CandidateEvidenceSnapshot.model_validate(raw)
    if schema_version == 2:
        return CandidateEvidenceSnapshot.model_validate(raw)
    raise ValueError(f"Unsupported candidate evidence snapshot schema: {schema_version!r}")


class ManualReviewEvent(BaseModel):
    """One append-only user decision for a pair of works."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: int | None = None
    workno_a: str
    workno_b: str
    outcome: CandidateReviewOutcome
    relation_type: ManualRelationType | None = None
    subject_workno: str | None = None
    target_workno: str | None = None
    notes: str | None = None
    evidence_snapshot: CandidateEvidenceSnapshot
    reviewed_at: datetime
    updated_at: datetime | None = None
    provenance: ManualReviewProvenance = ManualReviewProvenance.MANUAL_USER_REVIEW

    @field_validator("subject_workno", "target_workno")
    @classmethod
    def normalize_direction_workno(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return str(WorkCode.parse(value, allowed_prefixes={"RJ"}))

    @model_validator(mode="before")
    @classmethod
    def canonicalize_pair(cls, values: Any) -> Any:
        if isinstance(values, dict):
            a, b = values.get("workno_a"), values.get("workno_b")
            if a is not None and b is not None:
                a = str(WorkCode.parse(a, allowed_prefixes={"RJ"}))
                b = str(WorkCode.parse(b, allowed_prefixes={"RJ"}))
                if a > b:
                    values = dict(values)
                    values["workno_a"], values["workno_b"] = b, a
        return values

    @model_validator(mode="after")
    def validate_contract(self) -> ManualReviewEvent:
        if self.workno_a == self.workno_b:
            raise ValueError("A work cannot be reviewed against itself")
        for field_name in ("subject_workno", "target_workno"):
            value = getattr(self, field_name)
            if value is not None and value not in {self.workno_a, self.workno_b}:
                raise ValueError("Direction work numbers must match the reviewed pair")
        if self.outcome is CandidateReviewOutcome.RELATED:
            if self.relation_type is None:
                raise ValueError("RELATED reviews require a relation_type")
            if self.relation_type.is_directional:
                if self.subject_workno is None or self.target_workno is None:
                    raise ValueError(
                        "Directional relations require subject and target work numbers"
                    )
                if self.subject_workno == self.target_workno:
                    raise ValueError("Directional relation subject and target must differ")
            elif self.subject_workno is not None or self.target_workno is not None:
                raise ValueError("Symmetric relations must not include direction")
        elif (
            self.relation_type is not None
            or self.subject_workno is not None
            or self.target_workno is not None
        ):
            raise ValueError("Only RELATED reviews may include relation type or direction")
        return self


# Friendly application-layer name used by callers that do not need event wording.
ManualRelationReview = ManualReviewEvent


def canonical_pair(left: str, right: str) -> tuple[str, str]:
    a = str(WorkCode.parse(left, allowed_prefixes={"RJ"}))
    b = str(WorkCode.parse(right, allowed_prefixes={"RJ"}))
    if a == b:
        raise ValueError("A work cannot be reviewed against itself")
    return (a, b) if a < b else (b, a)
