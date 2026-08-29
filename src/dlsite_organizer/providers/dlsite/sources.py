"""Provider-local source models for DLsite product metadata.

These DTOs intentionally stop raw storefront payloads at the provider boundary.
They describe the subset of the ``product/info/ajax`` response contract that is
verified by reviewed, user-captured responses, not the ``Work`` domain model.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from dlsite_organizer.domain.work import Availability, Work
from dlsite_organizer.domain.work_code import WorkCode, WorkCodeError
from dlsite_organizer.providers.dlsite.exceptions import DlsiteParseError


class TranslationInfoSource(BaseModel):
    """Known translation fields retained for a future relation adapter.

    This is deliberately provider-local.  Its values are not evidence of a
    domain ``WorkRelation`` and this release performs no relation inference.
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
    translation_info: TranslationInfoSource | None = None

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
