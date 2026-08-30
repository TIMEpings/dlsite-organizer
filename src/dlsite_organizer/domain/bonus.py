"""Typed snapshots of bonus evidence reported by a provider."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class BonusEvidence(BaseModel):
    """One normalized bonus entry captured from a metadata response.

    These are observations, not classifications.  Every field is optional
    because storefront responses may expose only part of an entry.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str | None = None
    description: str | None = None
    start_at: date | datetime | None = None
    end_at: date | datetime | None = None
    workno: str | None = None
    product_id: str | None = None
    url: str | None = None
    bonus_type: str | None = None
    label: str | None = None

    @field_validator("title", "description", "workno", "product_id", "url", "bonus_type", "label")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class BonusEvidenceSnapshot(BaseModel):
    """Versioned, typed evidence for one successful metadata observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    entries: tuple[BonusEvidence, ...] = Field(default_factory=tuple)

    @property
    def bonuses(self) -> tuple[BonusEvidence, ...]:
        """Expose the source vocabulary without changing snapshot storage."""
        return self.entries
