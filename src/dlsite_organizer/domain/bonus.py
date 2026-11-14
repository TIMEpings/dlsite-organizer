"""Typed snapshots of bonus evidence reported by a provider."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator

from dlsite_organizer.domain.relation import Confidence, WorkRelation
from dlsite_organizer.domain.work_code import WorkCode, WorkCodeError

_DLSITE_TIMEZONE = timezone(timedelta(hours=9))
AJAX_TITLE_CROSS_VALIDATION = "ajax_title_cross_validation"
DEFAULT_BONUS_DATE_TOLERANCE_DAYS = 7
MAX_BONUS_DATE_TOLERANCE_DAYS = 30


class BonusListingStatus(BaseModel):
    """AJAX listing attributes used to screen independent bonus works."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    is_sale: StrictBool | None = None
    is_free: StrictBool | None = None
    is_oly: StrictBool | None = None
    wishlist_count: StrictInt | None = None
    price: StrictInt | None = None

    @property
    def matches_bonus_profile(self) -> bool:
        return (
            self.is_sale is False
            and self.is_free is True
            and self.is_oly is True
            and self.price == 0
        )

    @property
    def is_complete(self) -> bool:
        values = (self.is_sale, self.is_free, self.is_oly, self.price)
        return all(value is not None for value in values)


class BonusListingSnapshot(BaseModel):
    """Exact listing identity and status from a single AJAX response."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    title: str
    maker_id: str | None = None
    regist_date: date | None = None
    status: BonusListingStatus


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
    invalid_schedule: bool | None = None

    @field_validator("title", "description", "workno", "product_id", "url", "bonus_type", "label")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    def is_current_at(self, instant: datetime) -> bool:
        """Compare storefront dates and unzoned timestamps in Japan time."""
        start = self._in_storefront_time(self.start_at, time.min)
        end = self._in_storefront_time(self.end_at, time.max)
        return (start is None or start <= instant) and (end is None or instant <= end)

    @property
    def explicit_rj(self) -> str | None:
        values = [self.workno, self.product_id]
        if self.url:
            try:
                url = urlsplit(self.url)
                prefix = "/work/=/product_id/"
                if url.hostname in (None, "www.dlsite.com", "dlsite.com") and prefix in url.path:
                    values.append(url.path.split(prefix, 1)[1].removesuffix(".html"))
            except ValueError:
                pass
        codes = set()
        for value in values:
            if value:
                try:
                    codes.add(str(WorkCode.parse(value, allowed_prefixes=("RJ",))))
                except WorkCodeError:
                    continue
        return next(iter(codes)) if len(codes) == 1 else None

    def activity_status(self, now: datetime, *, recently_reported: bool) -> str:
        start = self._in_storefront_time(self.start_at, time.min)
        end = self._in_storefront_time(self.end_at, time.max)
        if self.invalid_schedule or (start is not None and end is not None and start > end):
            return "状态未知"
        if end is not None and end < now:
            return "已结束"
        if recently_reported:
            if start is not None and start > now:
                return "尚未开始"
            if end is not None:
                return "活动进行中"
        return "状态未知"

    @staticmethod
    def _in_storefront_time(value: date | datetime | None, boundary: time) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, datetime):
            return datetime.combine(value, boundary, _DLSITE_TIMEZONE)
        return value if value.tzinfo is not None else value.replace(tzinfo=_DLSITE_TIMEZONE)


class BonusEvidenceSnapshot(BaseModel):
    """Versioned, typed evidence for one successful metadata observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    entries: tuple[BonusEvidence, ...] = Field(default_factory=tuple)

    @property
    def bonuses(self) -> tuple[BonusEvidence, ...]:
        """Expose the source vocabulary without changing snapshot storage."""
        return self.entries


@dataclass(frozen=True, slots=True)
class BonusCampaign:
    entry: BonusEvidence
    observed_at: datetime
    source: str

    @property
    def workno(self) -> str | None:
        return self.entry.explicit_rj


class BonusMatch(BaseModel):
    """A directional main-to-bonus edge with its strongest retained evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    relation: WorkRelation
    title: str
    entry: BonusEvidence | None = None
    observed_at: datetime | None = None

    @property
    def automatically_linked(self) -> bool:
        return (
            self.relation.confidence is Confidence.HIGH
            and self.relation.detection_source == AJAX_TITLE_CROSS_VALIDATION
        )
