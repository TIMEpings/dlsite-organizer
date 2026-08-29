"""HTTP client for the DLsite storefront."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from dlsite_organizer.domain.work import Work
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
    TranslationInfoSource,
    normalize_product_info_ajax,
    parse_product_info_ajax,
)

logger = logging.getLogger(__name__)


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


@dataclass(frozen=True, slots=True)
class DlsiteWorkLookup:
    """One provider lookup with optional structured-source evidence.

    ``product_info`` remains available to provider callers that need the full
    validated DTO.  ``translation_info`` is the narrow source view consumed by
    the application relation service.  It is ``None`` when HTML fallback
    supplied the normalized work.
    """

    work: Work
    product_info: ProductInfoAjaxSource | None
    source: str = "DLSITE_HTML_JSONLD"

    @property
    def translation_info(self) -> TranslationInfoSource | None:
        """Expose only the provider source object needed by relation mapping."""
        return self.product_info.translation_info if self.product_info is not None else None


class DlsiteProvider:
    """Fetch DLsite metadata from structured JSON, then semantic HTML fallback."""

    def __init__(
        self,
        *,
        section: str = "maniax",
        base_url: str = "https://www.dlsite.com",
        timeout_seconds: float = 15.0,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        normalized_section = section.strip("/")
        self._site = DlsiteSite(base_url=base_url.rstrip("/"), section=normalized_section)
        self._timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10.0))
        self._client_factory = client_factory or self._new_client

    def fetch_work(self, workno: str) -> Work:
        """Fetch normalized metadata, retaining the minimal legacy interface."""
        return self.fetch_work_lookup(workno).work

    def fetch_work_lookup(self, workno: str) -> DlsiteWorkLookup:
        """Fetch a work and its validated structured evidence in one request flow."""
        normalized = str(WorkCode.parse(workno, allowed_prefixes={"RJ"}))
        try:
            with self._client_factory() as client:
                structured = self._fetch_structured(client, normalized)
                if structured is not None:
                    return DlsiteWorkLookup(
                        work=normalize_product_info_ajax(structured, section=self._site.section),
                        product_info=structured,
                        source="DLSITE_PRODUCT_INFO_AJAX",
                    )
                response = client.get(self.build_product_url(normalized))
        except httpx.TimeoutException as exc:
            logger.warning("DLsite request timed out for %s", normalized)
            raise DlsiteConnectionError("DLsite request timed out") from exc
        except httpx.RequestError as exc:
            logger.warning("DLsite connection failed for %s: %s", normalized, exc)
            raise DlsiteConnectionError("DLsite connection failed") from exc

        if response.status_code == httpx.codes.NOT_FOUND:
            raise WorkNotFoundError(normalized)
        if response.is_error:
            logger.warning("DLsite returned HTTP %s for %s", response.status_code, normalized)
            raise DlsiteHttpError(f"Unexpected HTTP status {response.status_code}")

        try:
            source = parse_product_page_source(response.text)
            return DlsiteWorkLookup(
                work=normalize_product_page_source(
                    source,
                    workno=normalized,
                    section=self._site.section,
                ),
                product_info=None,
                source="DLSITE_HTML_JSONLD",
            )
        except DlsiteParseError:
            logger.exception("Failed to parse DLsite metadata for %s", normalized)
            raise

    def build_product_url(self, workno: str) -> str:
        """Build a section-scoped public product URL in one centralized location."""
        return self._site.product_page_url(workno)

    def build_product_info_url(self, workno: str) -> str:
        """Build the candidate structured endpoint URL for the configured site."""
        return self._site.product_info_url(workno)

    def _fetch_structured(self, client: httpx.Client, workno: str) -> ProductInfoAjaxSource | None:
        """Return structured evidence when valid, otherwise defer to HTML once.

        An unavailable endpoint, an unexpected response, or a contract mismatch
        is not proof that the work is absent.  HTML receives one supplementary
        chance rather than triggering section guessing or further requests.
        """
        try:
            response = client.get(self.build_product_info_url(workno))
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

    def _new_client(self) -> httpx.Client:
        return httpx.Client(
            timeout=self._timeout,
            follow_redirects=True,
            headers={
                "User-Agent": "dlsite-organizer/0.1 (desktop metadata lookup)",
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "ja,en;q=0.8",
            },
        )
