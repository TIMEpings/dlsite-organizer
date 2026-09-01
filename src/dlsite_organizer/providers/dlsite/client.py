"""HTTP client for the DLsite storefront."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from urllib.parse import quote

import httpx

from dlsite_organizer import application_user_agent
from dlsite_organizer.domain.bonus import BonusEvidenceSnapshot
from dlsite_organizer.domain.work import TranslationAttribution, Work
from dlsite_organizer.domain.work_code import WorkCode
from dlsite_organizer.providers.dlsite.exceptions import (
    DlsiteConnectionError,
    DlsiteHttpError,
    DlsiteParseError,
    WorkNotFoundError,
)
from dlsite_organizer.providers.dlsite.parser import (
    normalize_product_page_source,
    parse_product_page_source,
)
from dlsite_organizer.providers.dlsite.sources import (
    ProductInfoAjaxSource,
    ProductMetadataSource,
    TranslationInfoSource,
    merge_product_metadata,
    normalize_product_info_ajax,
    parse_product_info_ajax,
    parse_product_metadata,
    translation_attribution,
)

logger = logging.getLogger(__name__)

DLSITE_PRODUCT_INFO_AJAX = "DLSITE_PRODUCT_INFO_AJAX"
DLSITE_PRODUCT_JSON = "DLSITE_PRODUCT_JSON"
DLSITE_HTML_JSONLD = "DLSITE_HTML_JSONLD"
DLSITE_ENRICHED_SOURCE = f"{DLSITE_PRODUCT_INFO_AJAX}+{DLSITE_PRODUCT_JSON}"


class DlsiteSection(StrEnum):
    """Known public DLsite sections used by the bounded routing policy."""

    MANIAX = "maniax"
    HOME = "home"
    GIRLS = "girls"
    BOOKS = "books"
    SOFT = "soft"
    PRO = "pro"


@dataclass(frozen=True, slots=True)
class DlsiteSourceRoute:
    """A deterministic, bounded route policy for one WorkCode."""

    sections: tuple[str, ...]
    resolve_public_page: bool = False


def source_route_for(
    work_code: WorkCode,
    *,
    configured_section: str,
) -> DlsiteSourceRoute:
    """Map a typed work code to its known public sections.

    RJ keeps the user's configured section for backwards compatibility.  BJ
    has one known books route.  VJ is published in both soft and pro; the
    public product page is consulted in that fixed order so the provider never
    brute-forces storefront sections.
    """
    if work_code.prefix == "BJ":
        return DlsiteSourceRoute((DlsiteSection.BOOKS.value,))
    if work_code.prefix == "VJ":
        return DlsiteSourceRoute(
            (DlsiteSection.SOFT.value, DlsiteSection.PRO.value),
            resolve_public_page=True,
        )
    return DlsiteSourceRoute((configured_section,))


@dataclass(frozen=True, slots=True)
class DlsiteSite:
    """Centralized URL policy for one explicitly configured DLsite section.

    The provider never leaks this storefront routing detail to domain or UI.
    A future section resolver can replace the configured value here without
    changing callers or probing every DLsite section.
    """

    base_url: str
    section: str

    def product_info_url(self, workno: str) -> str:
        return (
            f"{self.base_url}/{quote(self.section, safe='')}/product/info/ajax?product_id="
            f"{quote(workno, safe='')}"
        )

    def product_page_url(self, workno: str) -> str:
        return (
            f"{self.base_url}/{quote(self.section, safe='')}/work/=/product_id/"
            f"{quote(workno, safe='')}.html"
        )

    def product_metadata_url(self, workno: str, *, locale: str) -> str:
        return (
            f"{self.base_url}/{quote(self.section, safe='')}/api/=/product.json?workno="
            f"{quote(workno, safe='')}&locale={quote(locale, safe='')}"
        )


@dataclass(frozen=True, slots=True)
class DlsiteWorkLookup:
    """One provider lookup with optional structured-source evidence.

    ``product_info`` remains available to provider callers that need the full
    validated DTO.  ``translation_info`` is the narrow source view consumed by
    the application relation service.  It is ``None`` when HTML fallback
    supplied the normalized work.  Translation attribution is kept separate
    from the normalized listing maker.
    """

    work: Work
    product_info: ProductInfoAjaxSource | None
    source: str = DLSITE_HTML_JSONLD
    core_source: str | None = None
    metadata_source: str | None = None
    regist_datetime: datetime | None = None
    translation_attribution: TranslationAttribution | None = None

    @property
    def translation_info(self) -> TranslationInfoSource | None:
        """Expose only the provider source object needed by relation mapping."""
        return self.product_info.translation_info if self.product_info is not None else None

    @property
    def bonus_evidence(self) -> BonusEvidenceSnapshot | None:
        """Expose normalized transient evidence without leaking the AJAX DTO."""
        return self.product_info.bonus_evidence if self.product_info is not None else None


class DlsiteProvider:
    """Fetch core DLsite metadata and enrich it from the product JSON API."""

    def __init__(
        self,
        *,
        section: str = "maniax",
        base_url: str = "https://www.dlsite.com",
        timeout_seconds: float = 15.0,
        metadata_locale: str = "ja_jp",
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        normalized_section = section.strip("/")
        self._site = DlsiteSite(base_url=base_url.rstrip("/"), section=normalized_section)
        self._timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10.0))
        self._metadata_locale = metadata_locale.strip() or "ja_jp"
        self._client_factory = client_factory or self._new_client

    def fetch_work(self, workno: str) -> Work:
        """Fetch normalized metadata, retaining the minimal legacy interface."""
        return self.fetch_work_lookup(workno).work

    def apply_settings(
        self,
        *,
        timeout_seconds: float | None = None,
        metadata_locale: str | None = None,
    ) -> None:
        """Apply provider settings to subsequent requests without restarting."""
        if timeout_seconds is not None:
            self._timeout = httpx.Timeout(
                timeout_seconds,
                connect=min(timeout_seconds, 10.0),
            )
        if metadata_locale is not None:
            self._metadata_locale = metadata_locale.strip() or "ja_jp"

    def fetch_work_lookup(self, workno: str) -> DlsiteWorkLookup:
        """Fetch core evidence, then optionally enrich it from product JSON."""
        parsed = WorkCode.parse(workno)
        normalized = str(parsed)
        route = source_route_for(parsed, configured_section=self._site.section)
        last_http_status: int | None = None
        last_parse_error: DlsiteParseError | None = None
        try:
            with self._client_factory() as client:
                for section in route.sections:
                    site = self._site_for_section(section)
                    page_response: httpx.Response | None = None
                    if route.resolve_public_page:
                        page_response = self._fetch_public_page(client, normalized, site)
                        if page_response is not None:
                            if page_response.status_code == httpx.codes.NOT_FOUND:
                                continue
                            site = self._site_for_section(
                                _resolved_section(page_response, fallback=site.section)
                            )

                    structured = self._fetch_structured(client, normalized, site)
                    if structured is not None:
                        return self._build_enriched_lookup(client, structured, site=site)

                    response = (
                        page_response
                        if page_response is not None
                        else client.get(site.product_page_url(normalized))
                    )
                    if response.status_code == httpx.codes.NOT_FOUND:
                        continue
                    if response.is_error:
                        last_http_status = response.status_code
                        logger.info(
                            "DLsite product page returned HTTP %s for %s in %s",
                            response.status_code,
                            normalized,
                            site.section,
                        )
                        continue

                    try:
                        source = parse_product_page_source(response.text)
                        return DlsiteWorkLookup(
                            work=normalize_product_page_source(
                                source,
                                workno=normalized,
                                section=site.section,
                            ),
                            product_info=None,
                            source=DLSITE_HTML_JSONLD,
                            core_source=DLSITE_HTML_JSONLD,
                        )
                    except DlsiteParseError as exc:
                        last_parse_error = exc
                        logger.info(
                            "DLsite product page unusable for %s in %s: %s",
                            normalized,
                            site.section,
                            exc,
                        )
        except httpx.TimeoutException as exc:
            logger.warning("DLsite request timed out for %s", normalized)
            raise DlsiteConnectionError("DLsite request timed out") from exc
        except httpx.RequestError as exc:
            logger.warning("DLsite connection failed for %s: %s", normalized, exc)
            raise DlsiteConnectionError("DLsite connection failed") from exc

        if last_parse_error is not None:
            raise last_parse_error
        if last_http_status is None:
            raise WorkNotFoundError(normalized)
        logger.warning("DLsite returned HTTP %s for %s", last_http_status, normalized)
        raise DlsiteHttpError(f"Unexpected HTTP status {last_http_status}")

    def build_product_url(self, workno: str) -> str:
        """Build a routed public product URL in one centralized location."""
        normalized = str(WorkCode.parse(workno))
        return self._site_for_workno(normalized).product_page_url(normalized)

    def build_product_info_url(self, workno: str) -> str:
        """Build the candidate structured endpoint URL for the routed section."""
        normalized = str(WorkCode.parse(workno))
        return self._site_for_workno(normalized).product_info_url(normalized)

    def build_product_metadata_url(self, workno: str) -> str:
        """Build the optional rich product metadata URL for one exact listing."""
        normalized = str(WorkCode.parse(workno))
        return self._site_for_workno(normalized).product_metadata_url(
            normalized,
            locale=self._metadata_locale,
        )

    def _build_enriched_lookup(
        self,
        client: httpx.Client,
        structured: ProductInfoAjaxSource,
        *,
        site: DlsiteSite,
    ) -> DlsiteWorkLookup:
        """Combine one core response with bounded, non-recursive rich probes."""
        core_work = normalize_product_info_ajax(structured, section=site.section)
        rich = self._fetch_product_metadata(client, structured.requested_workno, site=site)
        if rich is None:
            return DlsiteWorkLookup(
                work=core_work,
                product_info=structured,
                source=DLSITE_PRODUCT_INFO_AJAX,
                core_source=DLSITE_PRODUCT_INFO_AJAX,
                regist_datetime=structured.regist_datetime,
                translation_attribution=translation_attribution(structured, None, None),
            )

        original_rich: ProductMetadataSource | None = None
        translation = structured.translation_info
        if translation is not None and translation.is_child is True and translation.original_workno:
            # This is deliberately one bounded original lookup.  It is not a
            # recursive translation graph crawl and never follows child lists.
            original_rich = self._fetch_product_metadata(
                client,
                translation.original_workno,
                site=site,
            )

        work = merge_product_metadata(
            core_work,
            rich,
            translation_info=translation,
            original_rich=original_rich,
        )
        return DlsiteWorkLookup(
            work=work,
            product_info=structured,
            source=DLSITE_ENRICHED_SOURCE,
            core_source=DLSITE_PRODUCT_INFO_AJAX,
            metadata_source=DLSITE_PRODUCT_JSON,
            regist_datetime=structured.regist_datetime,
            translation_attribution=translation_attribution(
                structured,
                rich,
                original_rich,
            ),
        )

    def _fetch_product_metadata(
        self,
        client: httpx.Client,
        workno: str,
        *,
        site: DlsiteSite,
    ) -> ProductMetadataSource | None:
        """Fetch optional rich metadata without weakening core lookup."""
        try:
            response = client.get(site.product_metadata_url(workno, locale=self._metadata_locale))
        except httpx.RequestError as exc:
            logger.info("Rich DLsite source unavailable for %s: %s", workno, exc)
            return None
        if response.is_error:
            logger.info(
                "Rich DLsite source returned HTTP %s for %s",
                response.status_code,
                workno,
            )
            return None
        try:
            return parse_product_metadata(response.text, workno)
        except DlsiteParseError as exc:
            logger.info("Rich DLsite source unusable for %s: %s", workno, exc)
            return None

    def _fetch_structured(
        self,
        client: httpx.Client,
        workno: str,
        site: DlsiteSite,
    ) -> ProductInfoAjaxSource | None:
        """Return structured evidence when valid, otherwise defer to HTML once.

        An unavailable endpoint, an unexpected response, or a contract mismatch
        is not proof that the work is absent.  HTML receives one supplementary
        chance rather than triggering section guessing or further requests.
        """
        try:
            response = client.get(site.product_info_url(workno))
        except httpx.RequestError as exc:
            logger.info("Structured DLsite source unavailable for %s: %s", workno, exc)
            return None
        if response.is_error:
            logger.info(
                "Structured DLsite source returned HTTP %s for %s; using HTML fallback",
                response.status_code,
                workno,
            )
            return None
        try:
            source = parse_product_info_ajax(response.text, workno)
            return source
        except DlsiteParseError as exc:
            logger.info("Structured DLsite source unusable for %s: %s", workno, exc)
            return None

    def _fetch_public_page(
        self,
        client: httpx.Client,
        workno: str,
        site: DlsiteSite,
    ) -> httpx.Response | None:
        """Resolve multi-section VJ listings through one public-page request."""
        try:
            return client.get(site.product_page_url(workno))
        except httpx.RequestError as exc:
            logger.info(
                "DLsite route resolution unavailable for %s in %s: %s",
                workno,
                site.section,
                exc,
            )
            return None

    def _site_for_section(self, section: str) -> DlsiteSite:
        return DlsiteSite(base_url=self._site.base_url, section=section.strip("/"))

    def _site_for_workno(self, workno: str) -> DlsiteSite:
        """Return the first bounded route site for a public URL helper."""
        parsed = WorkCode.parse(workno)
        route = source_route_for(parsed, configured_section=self._site.section)
        return self._site_for_section(route.sections[0])

    def _new_client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self._timeout,
            follow_redirects=True,
            headers={
                "User-Agent": application_user_agent(),
                "Accept": "application/json,text/html,application/xhtml+xml",
                "Accept-Language": "ja,en;q=0.8",
            },
        )


def _resolved_section(response: httpx.Response, *, fallback: str) -> str:
    """Read the section selected by DLsite's public-page redirect."""
    parts = response.url.path.strip("/").split("/")
    try:
        work_index = parts.index("work")
    except ValueError:
        return fallback
    if work_index == 0 or not parts[work_index - 1]:
        return fallback
    return parts[work_index - 1]
