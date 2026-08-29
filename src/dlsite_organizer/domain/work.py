"""Work aggregate shared by lookup, future scanning, and relation analysis."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dlsite_organizer.domain.work_code import WorkCode


class Availability(StrEnum):
    """Known storefront availability at observation time."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class Work(BaseModel):
    """Normalized metadata for one DLsite work."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workno: str
    title: str = Field(min_length=1)
    maker_id: str | None = None
    maker_name: str | None = None
    release_date: date | None = None
    series_name: str | None = None
    cvs: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    cover_url: str | None = None
    availability: Availability = Availability.UNKNOWN
    source_section: str | None = None

    @field_validator("workno")
    @classmethod
    def normalize_workno(cls, value: str) -> str:
        """Store every work number in its canonical form."""
        return str(WorkCode.parse(value))

    @field_validator("title")
    @classmethod
    def trim_title(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("title must not be empty")
        return stripped

    @field_validator("maker_id", "maker_name", "series_name")
    @classmethod
    def trim_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("cvs", "tags")
    @classmethod
    def normalize_string_lists(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))
