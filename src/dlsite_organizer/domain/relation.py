"""Contracts for future evidence-based work relation analysis."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from dlsite_organizer.domain.work_code import WorkCode


class RelationType(StrEnum):
    TRANSLATION = "translation"
    ORIGINAL = "original"
    PARENT = "parent"
    CHILD = "child"
    BONUS = "bonus"
    LIMITED_BONUS = "limited_bonus"
    BUNDLE = "bundle"
    RELATED = "related"
    SUSPECTED = "suspected_relation"


class Confidence(StrEnum):
    CONFIRMED = "confirmed"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class EvidenceType(StrEnum):
    DLSITE_PARAMETER = "dlsite_parameter"
    SAME_MAKER = "same_maker"
    SAME_RELEASE_DATE = "same_release_date"
    ADJACENT_WORKNO = "adjacent_workno"
    HISTORICAL_OBSERVATION = "historical_observation"
    OTHER = "other"


class RelationEvidence(BaseModel):
    """One human-readable observation supporting a relation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_type: EvidenceType
    description: str = Field(min_length=1)
    attributes: dict[str, Any] = Field(default_factory=dict)


class WorkRelation(BaseModel):
    """A directional, evidence-backed relationship between two works."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    source_workno: str
    target_workno: str
    relation_type: RelationType
    confidence: Confidence = Confidence.UNKNOWN
    evidence: list[RelationEvidence] = Field(default_factory=list)
    detection_source: str = Field(min_length=1)

    @field_validator("source_workno", "target_workno")
    @classmethod
    def normalize_workno(cls, value: str) -> str:
        return str(WorkCode.parse(value))

    @model_validator(mode="after")
    def works_must_differ(self) -> WorkRelation:
        if self.source_workno == self.target_workno:
            raise ValueError("A work cannot be related to itself")
        return self
