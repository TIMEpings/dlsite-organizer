"""Provider-local source models for DLsite product metadata.

These DTOs intentionally stop raw storefront payloads at the provider boundary.
They describe the reviewed subsets of the ``product/info/ajax`` and
``api/=/product.json`` response contracts, not the ``Work`` domain model.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, date, datetime
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
from dlsite_organizer.domain.work import (
    AgeCategory,
    Availability,
    TranslationAttribution,
    Work,
    WorkLanguage,
    normalize_age_category,
    normalize_language_code,
)
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
        return str(WorkCode.parse(stripped))

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
                str(WorkCode.parse(value.strip()))
                for value in values
                if value.strip()
            )
        )


class ProductMetadataPersonSource(BaseModel):
    """One person entry from the rich product metadata API."""

    model_config = ConfigDict(extra="allow", frozen=True)

    name: str | None = None
    identifier: str | None = Field(default=None, validation_alias=AliasChoices("identifier", "id"))

    @field_validator("name", "identifier", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            stripped = str(value).strip()
            return stripped or None
        return None


class ProductMetadataGenreSource(BaseModel):
    """One source-provided genre/tag entry."""

    model_config = ConfigDict(extra="allow", frozen=True)

    name: str | None = None
    identifier: str | None = Field(default=None, validation_alias=AliasChoices("identifier", "id"))

    @field_validator("name", "identifier", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            stripped = str(value).strip()
            return stripped or None
        return None


class ProductMetadataCreatorsSource(BaseModel):
    """Typed subset of the API's intentionally misspelled ``creaters`` map."""

    model_config = ConfigDict(extra="allow", frozen=True)

    voice_by: list[ProductMetadataPersonSource] = Field(default_factory=list)

    @field_validator("voice_by", mode="before")
    @classmethod
    def tolerate_malformed_people(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
        return []


class ProductMetadataLanguageEditionSource(BaseModel):
    """One language edition entry used to identify the queried listing."""

    model_config = ConfigDict(extra="allow", frozen=True)

    workno: str | None = None
    edition_type: str | None = None
    display_order: int | None = None
    label: str | None = None
    lang: str | None = None

    @field_validator("workno", mode="before")
    @classmethod
    def normalize_optional_workno(cls, value: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            return None
        stripped = str(value).strip()
        if not stripped:
            return None
        try:
            return str(WorkCode.parse(stripped))
        except WorkCodeError:
            return None

    @field_validator("edition_type", "label", "lang", mode="before")
    @classmethod
    def normalize_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            stripped = str(value).strip()
            return stripped or None
        return None

    @field_validator("display_order", mode="before")
    @classmethod
    def tolerate_malformed_display_order(cls, value: Any) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None


class ProductMetadataImageSource(BaseModel):
    """Primary image entry from ``image_main``."""

    model_config = ConfigDict(extra="allow", frozen=True)

    url: str | None = None

    @field_validator("url", mode="before")
    @classmethod
    def normalize_optional_url(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            stripped = value.strip()
            return stripped or None
        return None


class ProductMetadataSource(BaseModel):
    """Typed provider-local DTO for ``/{section}/api/=/product.json``.

    The rich endpoint is optional enrichment.  Its DTO deliberately retains
    only typed values needed by the normalized model; the storefront's many
    unrelated fields remain provider-local extras and never reach services or
    the UI.
    """

    model_config = ConfigDict(extra="allow", frozen=True)

    requested_workno: str
    workno: str
    work_name: str
    maker_id: str | None = None
    maker_name: str | None = None
    circle_id: str | None = None
    age_category: str | int | None = None
    age_category_string: str | None = None
    regist_datetime: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("regist_datetime", "regist_date"),
    )
    series_id: str | None = None
    series_name: str | None = None
    genres: list[ProductMetadataGenreSource] | None = None
    creaters: ProductMetadataCreatorsSource | None = None
    voice_by: list[ProductMetadataPersonSource] | None = None
    language_editions: list[ProductMetadataLanguageEditionSource] | None = None
    image_main: ProductMetadataImageSource | None = None
    product_id: str | None = None

    @field_validator("requested_workno", "workno", "work_name")
    @classmethod
    def require_nonempty_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be empty")
        return stripped

    @field_validator(
        "maker_id",
        "maker_name",
        "circle_id",
        "age_category_string",
        "series_id",
        "series_name",
        "product_id",
        mode="before",
    )
    @classmethod
    def strip_optional_text(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, (str, int)) and not isinstance(value, bool):
            stripped = str(value).strip()
            return stripped or None
        return None

    @field_validator("regist_datetime", mode="before")
    @classmethod
    def parse_optional_regist_datetime(cls, value: Any) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime.combine(value, datetime.min.time())
        if not isinstance(value, str):
            return None
        raw = value.strip().replace("/", "-")
        if not raw:
            return None
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("Ignoring malformed optional product metadata regist_date")
            return None

    @field_validator("age_category", mode="before")
    @classmethod
    def tolerate_malformed_age_category(cls, value: Any) -> str | int | None:
        if value is None or (isinstance(value, bool)):
            return None
        return value if isinstance(value, (str, int)) else None

    @field_validator("genres", mode="before")
    @classmethod
    def tolerate_malformed_genres(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
        return None

    @field_validator("creaters", mode="before")
    @classmethod
    def tolerate_malformed_creators(cls, value: Any) -> Any:
        return value if isinstance(value, Mapping) else None

    @field_validator("voice_by", mode="before")
    @classmethod
    def tolerate_malformed_direct_voice_by(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
        return None

    @field_validator("language_editions", mode="before")
    @classmethod
    def tolerate_malformed_language_editions(cls, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
        return None

    @field_validator("image_main", mode="before")
    @classmethod
    def tolerate_malformed_image(cls, value: Any) -> Any:
        return value if isinstance(value, Mapping) else None

    @property
    def maker_identity(self) -> tuple[str | None, str | None]:
        """Return the rich source's own maker identity without guessing IDs."""
        return self.maker_id or self.circle_id, self.maker_name

    @property
    def cv_names(self) -> tuple[str, ...] | None:
        """Return voice names in source order, or ``None`` when not captured."""
        if self.creaters is not None:
            return tuple(person.name for person in self.creaters.voice_by if person.name)
        if self.voice_by is not None:
            return tuple(person.name for person in self.voice_by if person.name)
        return None

    @property
    def tag_names(self) -> tuple[str, ...] | None:
        """Return every source-provided genre name in source order."""
        if self.genres is None:
            return None
        return tuple(genre.name for genre in self.genres if genre.name)

    @property
    def cover_url(self) -> str | None:
        return _normalize_url(self.image_main.url) if self.image_main is not None else None

    @property
    def release_date(self) -> date | None:
        return self.regist_datetime.date() if self.regist_datetime is not None else None

    @property
    def normalized_age_category(self) -> AgeCategory | None:
        if self.age_category is not None:
            normalized = normalize_age_category(self.age_category)
            if normalized is not AgeCategory.UNKNOWN:
                return normalized
        if self.age_category_string is not None:
            return normalize_age_category(self.age_category_string)
        return AgeCategory.UNKNOWN if self.age_category is not None else None

    def language_for(self, workno: str) -> WorkLanguage | str | None:
        """Return only the language attached to this exact listing."""
        if self.language_editions is None:
            return None
        try:
            normalized_workno = str(WorkCode.parse(workno))
        except WorkCodeError:
            return None
        for edition in self.language_editions:
            if edition.workno != normalized_workno or not edition.lang:
                continue
            if edition.edition_type is not None and edition.edition_type.lower() != "language":
                continue
            return normalize_language_code(edition.lang)
        return None


def parse_product_metadata(payload: str, requested_workno: str) -> ProductMetadataSource:
    """Parse one current product API response into a typed provider DTO."""
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise DlsiteParseError("product_metadata did not return valid JSON") from exc

    try:
        normalized_requested_workno = str(WorkCode.parse(requested_workno))
    except WorkCodeError as exc:
        raise DlsiteParseError("product_metadata was requested with an invalid workno") from exc

    if not isinstance(decoded, list) or len(decoded) != 1 or not isinstance(decoded[0], Mapping):
        raise DlsiteParseError("product_metadata root must be a one-item array")

    try:
        source = ProductMetadataSource.model_validate(
            {
                **decoded[0],
                "requested_workno": normalized_requested_workno,
            }
        )
    except ValidationError as exc:
        raise DlsiteParseError("product_metadata contained malformed core metadata") from exc

    try:
        normalized_metadata_workno = str(
            WorkCode.parse(source.workno)
        )
    except WorkCodeError as exc:
        raise DlsiteParseError("product_metadata contained malformed core metadata") from exc
    if normalized_metadata_workno != normalized_requested_workno:
        raise DlsiteParseError(
            "product_metadata workno mismatch: "
            f"requested {normalized_requested_workno}, metadata {source.workno}"
        )
    if source.product_id is not None:
        try:
            product_id = str(WorkCode.parse(source.product_id))
        except WorkCodeError:
            logger.warning("Ignoring malformed optional product metadata product_id")
            return source.model_copy(update={"product_id": None})
        if product_id != normalized_requested_workno:
            raise DlsiteParseError(
                "product_metadata product_id mismatch: "
                f"requested {normalized_requested_workno}, metadata {product_id}"
            )
    return source


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
    regist_datetime: datetime | None = Field(
        default=None,
        validation_alias=AliasChoices("regist_datetime", "regist_date"),
    )
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
        return str(WorkCode.parse(stripped))


def parse_product_info_ajax(payload: str, requested_workno: str) -> ProductInfoAjaxSource:
    """Parse one JSON response only when it matches the supported DTO contract."""
    try:
        decoded = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise DlsiteParseError("product_info_ajax did not return valid JSON") from exc

    try:
        normalized_requested_workno = str(WorkCode.parse(requested_workno))
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
    """Normalize the core DTO into the application domain model.

    A translation child commonly reports its translator/publisher in the
    AJAX maker fields.  Those fields are retained in the provider DTO for
    attribution, but are intentionally not promoted to ``Work.maker_*``.
    ``DlsiteProvider`` can later fill the listing maker from the original
    product metadata source.
    """
    translation = source.translation_info
    is_translation_child = translation is not None and translation.is_child is True
    return Work(
        workno=source.requested_workno,
        title=source.work_name,
        maker_id=None if is_translation_child else source.maker_id,
        maker_name=None if is_translation_child else source.maker_name,
        release_date=source.regist_datetime.date() if source.regist_datetime is not None else None,
        regist_datetime=_as_utc_datetime(source.regist_datetime),
        language=translation.lang if translation is not None else None,
        age_category=normalize_age_category(source.age_category),
        cover_url=_normalize_url(source.work_image),
        availability=Availability.AVAILABLE,
        source_section=section,
    )


def normalize_product_metadata(source: ProductMetadataSource, *, section: str) -> Work:
    """Normalize a rich product DTO when no core source is available."""
    return Work(
        workno=source.requested_workno,
        title=source.work_name,
        maker_id=source.maker_identity[0],
        maker_name=source.maker_identity[1],
        release_date=source.release_date,
        regist_datetime=_as_utc_datetime(source.regist_datetime),
        series_name=source.series_name,
        cvs=list(source.cv_names or ()),
        tags=list(source.tag_names or ()),
        language=source.language_for(source.requested_workno),
        age_category=source.normalized_age_category or AgeCategory.UNKNOWN,
        cover_url=source.cover_url,
        availability=Availability.AVAILABLE,
        source_section=section,
    )


def merge_product_metadata(
    core_work: Work,
    rich: ProductMetadataSource,
    *,
    translation_info: TranslationInfoSource | None = None,
    original_rich: ProductMetadataSource | None = None,
) -> Work:
    """Merge core and rich sources using the current deterministic policy.

    Core AJAX fields remain authoritative for title, precise registration
    timestamp, cover, translation evidence, and availability.  The rich
    product API supplies descriptive metadata.  A child translation gets its
    listing maker/series from the original rich listing when available; its
    own rich maker is kept separately as translation attribution.
    """
    is_translation_child = translation_info is not None and translation_info.is_child is True
    rich_identity = rich.maker_identity
    if is_translation_child:
        if original_rich is not None and _identity_is_present(original_rich.maker_identity):
            maker_id, maker_name = original_rich.maker_identity
            series_name = original_rich.series_name or rich.series_name
        else:
            # The child source's maker is an attribution, not a safe listing
            # maker.  Do not guess or silently reintroduce it on enrichment
            # failure.
            maker_id, maker_name = None, None
            series_name = rich.series_name
    else:
        if _identity_is_present(rich_identity):
            maker_id, maker_name = rich_identity
        else:
            maker_id, maker_name = core_work.maker_id, core_work.maker_name
        series_name = (
            rich.series_name
            if "series_name" in rich.model_fields_set
            else core_work.series_name
        )

    cv_names = rich.cv_names if rich.cv_names is not None else tuple(core_work.cvs)
    tag_names = rich.tag_names if rich.tag_names is not None else tuple(core_work.tags)
    language = rich.language_for(core_work.workno)
    if language is None and translation_info is not None and translation_info.lang:
        language = normalize_language_code(translation_info.lang)
    if language is None:
        language = core_work.language

    rich_age = rich.normalized_age_category
    age_category = (
        rich_age
        if rich_age is not None and rich_age is not AgeCategory.UNKNOWN
        else core_work.age_category
    )

    return Work(
        workno=core_work.workno,
        title=core_work.title,
        maker_id=maker_id,
        maker_name=maker_name,
        release_date=core_work.release_date or rich.release_date,
        regist_datetime=core_work.regist_datetime or _as_utc_datetime(rich.regist_datetime),
        series_name=series_name,
        cvs=list(cv_names),
        tags=list(tag_names),
        language=language,
        age_category=age_category,
        cover_url=core_work.cover_url or rich.cover_url,
        availability=core_work.availability,
        source_section=core_work.source_section,
    )


def translation_attribution(
    core_source: ProductInfoAjaxSource,
    rich: ProductMetadataSource | None,
    original_rich: ProductMetadataSource | None,
) -> TranslationAttribution | None:
    """Return a child-only translator signature without changing ``Work``."""
    info = core_source.translation_info
    if info is None or info.is_child is not True:
        return None
    attribution = rich.maker_identity if rich is not None else (None, None)
    if not _identity_is_present(attribution):
        attribution = core_source.maker_id, core_source.maker_name
    if not _identity_is_present(attribution):
        return None
    resolved = original_rich.maker_identity if original_rich is not None else (None, None)
    if _same_identity(attribution, resolved):
        return None
    return TranslationAttribution(maker_id=attribution[0], maker_name=attribution[1])


def _identity_is_present(identity: tuple[str | None, str | None]) -> bool:
    return identity[0] is not None or identity[1] is not None


def _same_identity(
    left: tuple[str | None, str | None],
    right: tuple[str | None, str | None],
) -> bool:
    if not _identity_is_present(left) or not _identity_is_present(right):
        return False
    if left[0] is not None and right[0] is not None:
        return left[0] == right[0]
    if left[1] is not None and right[1] is not None:
        return left[1] == right[1]
    return False


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


def _as_utc_datetime(value: datetime | None) -> datetime | None:
    """Give normalized Work timestamps one stable timezone-aware meaning."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
