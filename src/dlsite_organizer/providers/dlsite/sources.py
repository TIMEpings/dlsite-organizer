"""Provider-local source models for DLsite product metadata.

These DTOs intentionally stop raw storefront payloads at the provider boundary.
They describe the subset of the ``product/info/ajax`` response contract that is
verified by reviewed, user-captured responses, not the ``Work`` domain model.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any
from urllib.parse import urljoin

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
)

from dlsite_organizer.domain.bonus import BonusEvidence, BonusEvidenceSnapshot
from dlsite_organizer.domain.work import Availability, Work
from dlsite_organizer.domain.work_code import WorkCode, WorkCodeError
from dlsite_organizer.providers.dlsite.exceptions import DlsiteParseError

logger = logging.getLogger(__name__)


class BonusEvidenceSource(BaseModel):
    """Provider-local representation of one optional bonus entry.

    The known aliases cover the fields needed for historical evidence while
    accepting storefront additions at the provider boundary.  Malformed
    optional scalar values degrade to missing values; they never become
    fabricated dates, identifiers, or URLs.
    """

    model_config = ConfigDict(extra="allow", frozen=True)

    title: str | None = Field(
        default=None,
        validation_alias=AliasChoices("title", "name", "bonus_title", "bonus_name"),
    )
    description: str | None = Field(
        default=None,
        validation_alias=AliasChoices("description", "body", "bonus_description"),
    )
    start_at: date | datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("start_at", "start_datetime", "start_date"),
    )
    end_at: date | datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("end_at", "end_datetime", "end_date"),
    )
    workno: str | None = Field(
        default=None,
        validation_alias=AliasChoices("workno", "work_no", "bonus_workno"),
    )
    product_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("product_id", "bonus_product_id"),
    )
    url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("url", "href", "link", "bonus_url", "detail_url"),
    )
    bonus_type: str | None = Field(
        default=None,
        validation_alias=AliasChoices("bonus_type", "type", "kind"),
    )
    label: str | None = Field(
        default=None,
        validation_alias=AliasChoices("label", "bonus_label"),
    )

    @field_validator(
        "title",
        "description",
        "workno",
        "product_id",
        "url",
        "bonus_type",
        "label",
        mode="before",
    )
    @classmethod
    def normalize_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            stripped = str(value).strip()
            return stripped or None
        return None

    @field_validator("start_at", "end_at", mode="before")
    @classmethod
    def normalize_optional_temporal(
        cls,
        value: Any,
        info: ValidationInfo,
    ) -> date | datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return value
        if not isinstance(value, str):
            return None
        raw = value.strip()
        if not raw:
            return None
        try:
            if "T" not in raw and " " not in raw:
                return date.fromisoformat(raw.replace("/", "-"))
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("Ignoring malformed optional bonus %s value", info.field_name)
            return None

    def to_domain(self) -> BonusEvidence:
        """Normalize provider aliases into the infrastructure-free model."""
        return BonusEvidence.model_validate(
            {
                "title": self.title,
                "description": self.description,
                "start_at": self.start_at,
                "end_at": self.end_at,
                "workno": self.workno,
                "product_id": self.product_id,
                "url": self.url,
                "bonus_type": self.bonus_type,
                "label": self.label,
            }
        )


class TranslationInfoSource(BaseModel):
    """Known translation fields retained for the application relation adapter.

    This is deliberately provider-local.  ``TranslationRelationService``
    interprets its explicit fields into domain ``WorkRelation`` objects; the
    DTO itself does not perform that interpretation.
    """

    model_config = ConfigDict(extra="allow", frozen=True)

    original_workno: str | None = None
    parent_workno: str | None = None
    child_worknos: list[str] = Field(default_factory=list)
    lang: str | None = None
    is_translation_agree: bool | None = None
    is_volunteer: bool | None = None
    is_original: bool | None = None
    is_parent: bool | None = None
    is_child: bool | None = None
    is_translation_bonus_child: bool | None = None

    @field_validator("original_workno", "parent_workno")
    @classmethod
    def normalize_optional_workno(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        return str(WorkCode.parse(stripped, allowed_prefixes={"RJ"}))

    @field_validator("lang")
    @classmethod
    def strip_optional_language(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("child_worknos")
    @classmethod
    def normalize_child_worknos(cls, values: list[str]) -> list[str]:
        return list(
            dict.fromkeys(
                str(WorkCode.parse(value.strip(), allowed_prefixes={"RJ"}))
                for value in values
                if value.strip()
            )
        )


class ProductInfoAjaxSource(BaseModel):
    """Supported subset of a DLsite ``product/info/ajax`` product object.

    Extra fields are accepted to tolerate storefront additions.  Required
    identifiers and title remain typed so corrupt core metadata fails clearly.
    """

    model_config = ConfigDict(extra="allow", frozen=True)

    requested_workno: str
    envelope_workno: str
    work_name: str
    product_id: str | None = None
    maker_id: str | None = None
    maker_name: str | None = None
    regist_datetime: datetime | None = Field(default=None, validation_alias="regist_date")
    work_image: str | None = None
    work_type: str | None = None
    age_category: str | int | None = None
    bonuses: list[BonusEvidenceSource] | None = None
    translation_info: TranslationInfoSource | None = None

    @field_validator("bonuses", mode="before")
    @classmethod
    def tolerate_malformed_bonus_collection(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, list) and all(isinstance(item, Mapping) for item in value):
            return value
        logger.warning("Ignoring malformed optional product bonuses collection")
        return None

    @property
    def bonus_evidence(self) -> BonusEvidenceSnapshot | None:
        """Return normalized bonus evidence while preserving absent vs empty."""
        if self.bonuses is None:
            return None
        return BonusEvidenceSnapshot(entries=tuple(item.to_domain() for item in self.bonuses))

    @field_validator("requested_workno", "envelope_workno", "work_name")
    @classmethod
    def require_nonempty_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be empty")
        return stripped

    @field_validator("maker_id", "maker_name", "work_image", "work_type")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("product_id")
    @classmethod
    def normalize_optional_product_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        return str(WorkCode.parse(stripped, allowed_prefixes={"RJ"}))


def parse_product_info_ajax(payload: str, requested_workno: str) -> ProductInfoAjaxSource:
    """Parse one JSON response only when it matches the supported DTO contract."""
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise DlsiteParseError("product_info_ajax did not return valid JSON") from exc

    try:
        normalized_requested_workno = str(WorkCode.parse(requested_workno, allowed_prefixes={"RJ"}))
    except WorkCodeError as exc:
        raise DlsiteParseError("product_info_ajax was requested with an invalid workno") from exc

    product = _extract_product_object(decoded, normalized_requested_workno)
    try:
        source = ProductInfoAjaxSource.model_validate(
            {
                **product,
                "requested_workno": normalized_requested_workno,
                "envelope_workno": normalized_requested_workno,
            }
        )
    except ValidationError as exc:
        raise DlsiteParseError("product_info_ajax contained malformed core metadata") from exc

    if source.product_id is not None and source.product_id != source.envelope_workno:
        raise DlsiteParseError(
            "product_info_ajax product_id mismatch: "
            f"envelope {source.envelope_workno}, metadata {source.product_id}"
        )
    return source


def normalize_product_info_ajax(source: ProductInfoAjaxSource, *, section: str) -> Work:
    """Normalize a validated provider DTO into the application domain model."""
    return Work(
        workno=source.requested_workno,
        title=source.work_name,
        maker_id=source.maker_id,
        maker_name=source.maker_name,
        release_date=source.regist_datetime.date() if source.regist_datetime is not None else None,
        cover_url=_normalize_url(source.work_image),
        availability=Availability.AVAILABLE,
        source_section=section,
    )


def _extract_product_object(decoded: Any, requested_workno: str) -> Mapping[str, Any]:
    if not isinstance(decoded, Mapping):
        raise DlsiteParseError("product_info_ajax root must be an object")
    if requested_workno not in decoded:
        raise DlsiteParseError(
            f"product_info_ajax did not contain requested key {requested_workno}"
        )
    unexpected_keys = set(decoded) - {requested_workno}
    if unexpected_keys:
        rendered_keys = ", ".join(sorted(str(key) for key in unexpected_keys))
        raise DlsiteParseError(
            f"product_info_ajax contained unexpected top-level key(s): {rendered_keys}"
        )
    nested = decoded[requested_workno]
    if not isinstance(nested, Mapping):
        raise DlsiteParseError("product_info_ajax requested value must be an object")
    return nested


def _normalize_url(value: str | None) -> str | None:
    return urljoin("https://www.dlsite.com", value) if value is not None else None
