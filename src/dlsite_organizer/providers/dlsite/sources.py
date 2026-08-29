"""Provider-local source models for DLsite product metadata.

These DTOs intentionally stop raw storefront payloads at the provider boundary.
They describe the supported *product_info_ajax contract*, not the ``Work``
domain model.  The live endpoint could not be reached while this code was
written, so the contract remains explicitly fixture-backed and provisional.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime
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

    @field_validator("original_workno", "parent_workno", "lang")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("child_worknos")
    @classmethod
    def normalize_child_worknos(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class ProductInfoAjaxSource(BaseModel):
    """Supported subset of a DLsite ``product/info/ajax`` product object.

    Extra fields are accepted to tolerate storefront additions.  Required
    identifiers and title remain typed so corrupt core metadata fails clearly.
    """

    model_config = ConfigDict(extra="allow", frozen=True)

    workno: str
    work_name: str
    maker_id: str | None = None
    maker_name: str | None = None
    regist_date: str | None = None
    work_image: str | None = None
    work_type: str | None = None
    age_category: str | int | None = None
    translation_info: TranslationInfoSource | None = None

    @field_validator("workno", "work_name")
    @classmethod
    def require_nonempty_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be empty")
        return stripped

    @field_validator("maker_id", "maker_name", "work_image", "work_type", "regist_date")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


def parse_product_info_ajax(payload: str, requested_workno: str) -> ProductInfoAjaxSource:
    """Parse one JSON response only when it matches the supported DTO contract."""
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise DlsiteParseError("product_info_ajax did not return valid JSON") from exc

    product = _extract_product_object(decoded, requested_workno)
    try:
        source = ProductInfoAjaxSource.model_validate(product)
    except ValidationError as exc:
        raise DlsiteParseError("product_info_ajax contained malformed core metadata") from exc

    try:
        response_workno = str(WorkCode.parse(source.workno, allowed_prefixes={"RJ"}))
    except WorkCodeError as exc:
        raise DlsiteParseError("product_info_ajax contained an invalid workno") from exc
    if response_workno != requested_workno:
        raise DlsiteParseError(
            "product_info_ajax workno mismatch: "
            f"requested {requested_workno}, got {response_workno}"
        )
    return source


def normalize_product_info_ajax(source: ProductInfoAjaxSource, *, section: str) -> Work:
    """Normalize a validated provider DTO into the application domain model."""
    return Work(
        workno=source.workno,
        title=source.work_name,
        maker_id=source.maker_id,
        maker_name=source.maker_name,
        release_date=_parse_regist_date(source.regist_date),
        cover_url=_normalize_url(source.work_image),
        availability=Availability.AVAILABLE,
        source_section=section,
    )


def _extract_product_object(decoded: Any, requested_workno: str) -> Mapping[str, Any]:
    if not isinstance(decoded, Mapping):
        raise DlsiteParseError("product_info_ajax root must be an object")
    nested = decoded.get(requested_workno)
    if isinstance(nested, Mapping):
        return nested
    return decoded


def _parse_regist_date(value: str | None) -> date | None:
    if value is None:
        return None
    normalized = value.strip().replace("/", "-")
    try:
        return date.fromisoformat(normalized[:10])
    except ValueError:
        try:
            return datetime.fromisoformat(normalized).date()
        except ValueError as exc:
            raise DlsiteParseError("product_info_ajax contained an invalid regist_date") from exc


def _normalize_url(value: str | None) -> str | None:
    return urljoin("https://www.dlsite.com", value) if value is not None else None
