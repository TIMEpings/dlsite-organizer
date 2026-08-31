"""Work aggregate shared by lookup, future scanning, and relation analysis."""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from dlsite_organizer.domain.work_code import WorkCode


class Availability(StrEnum):
    """Known storefront availability at observation time."""

    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class AgeCategory(StrEnum):
    """Normalized DLsite age classification.

    The storefront currently exposes numeric values for this field.  Keeping
    the application value semantic means callers never confuse an age rating
    with a product/work type such as ``SOU``.
    """

    GENERAL = "general"
    R15 = "r15"
    R18 = "r18"
    UNKNOWN = "unknown"


class WorkLanguage(StrEnum):
    """Known DLsite work-language codes.

    ``Work.language`` also accepts a raw string for future codes so a new
    storefront language cannot make an otherwise valid lookup fail.
    """

    JPN = "JPN"
    CHI_HANS = "CHI_HANS"
    CHI_HANT = "CHI_HANT"
    ENG = "ENG"
    KO_KR = "KO_KR"
    KOR = "KOR"
    SPA = "SPA"
    GER = "GER"
    FRE = "FRE"
    IND = "IND"
    ITA = "ITA"
    POR = "POR"
    SWE = "SWE"
    THA = "THA"
    VIE = "VIE"


class TranslationAttribution(BaseModel):
    """A translation-specific publisher/signature kept outside maker fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    maker_id: str | None = None
    maker_name: str | None = None

    @field_validator("maker_id", "maker_name")
    @classmethod
    def trim_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


def normalize_age_category(value: Any) -> AgeCategory:
    """Map known DLsite age values and fail softly for future values."""

    if isinstance(value, AgeCategory):
        return value
    if value is None or isinstance(value, bool):
        return AgeCategory.UNKNOWN
    if isinstance(value, int):
        return {
            1: AgeCategory.GENERAL,
            2: AgeCategory.R15,
            3: AgeCategory.R18,
        }.get(value, AgeCategory.UNKNOWN)
    if not isinstance(value, str):
        return AgeCategory.UNKNOWN
    normalized = value.strip().upper().replace("-", "_").replace(" ", "_")
    return {
        "1": AgeCategory.GENERAL,
        "GEN": AgeCategory.GENERAL,
        "GENERAL": AgeCategory.GENERAL,
        "ALL_AGES": AgeCategory.GENERAL,
        "全年齢": AgeCategory.GENERAL,
        "2": AgeCategory.R15,
        "R15": AgeCategory.R15,
        "3": AgeCategory.R18,
        "R18": AgeCategory.R18,
    }.get(normalized, AgeCategory.UNKNOWN)


def normalize_language_code(value: Any) -> WorkLanguage | str | None:
    """Normalize a source code without rejecting unknown future languages."""

    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, WorkLanguage):
        return value
    if not isinstance(value, (str, int)):
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    normalized = normalized.upper().replace("-", "_")
    try:
        return WorkLanguage(normalized)
    except ValueError:
        return normalized


class Work(BaseModel):
    """Normalized metadata for one DLsite work."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workno: str
    title: str = Field(min_length=1)
    maker_id: str | None = None
    maker_name: str | None = None
    release_date: date | None = None
    regist_datetime: datetime | None = None
    series_name: str | None = None
    cvs: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    language: WorkLanguage | str | None = None
    age_category: AgeCategory = AgeCategory.UNKNOWN
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

    @field_validator("language", mode="before")
    @classmethod
    def normalize_language(cls, value: Any) -> WorkLanguage | str | None:
        return normalize_language_code(value)

    @field_validator("age_category", mode="before")
    @classmethod
    def normalize_age(cls, value: Any) -> AgeCategory:
        return normalize_age_category(value)

    @field_validator("cvs", "tags")
    @classmethod
    def normalize_string_lists(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @property
    def cv_names(self) -> tuple[str, ...]:
        """Immutable view used by new metadata consumers."""
        return tuple(self.cvs)

    @property
    def tag_names(self) -> tuple[str, ...]:
        """Immutable view used by new metadata consumers."""
        return tuple(self.tags)
