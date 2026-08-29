"""HTTP client for the DLsite storefront."""

from __future__ import annotations

import logging
from collections.abc import Callable
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
from dlsite_organizer.providers.dlsite.parser import parse_product_page

logger = logging.getLogger(__name__)


class DlsiteProvider:
    """Fetch public product pages from one explicitly configured DLsite section."""

    def __init__(
        self,
        *,
        section: str = "maniax",
        base_url: str = "https://www.dlsite.com",
        timeout_seconds: float = 15.0,
        client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self._section = section.strip("/")
        self._base_url = base_url.rstrip("/")
        self._timeout = httpx.Timeout(timeout_seconds, connect=min(timeout_seconds, 10.0))
        self._client_factory = client_factory or self._new_client

    def fetch_work(self, workno: str) -> Work:
        """Fetch and parse one public product page."""
        normalized = str(WorkCode.parse(workno, allowed_prefixes={"RJ"}))
        url = self.build_product_url(normalized)
        try:
            with self._client_factory() as client:
                response = client.get(url)
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
            return parse_product_page(response.text, normalized, section=self._section)
        except DlsiteParseError:
            logger.exception("Failed to parse DLsite metadata for %s", normalized)
            raise

    def build_product_url(self, workno: str) -> str:
        """Build a section-scoped public product URL in one centralized location."""
        return (
            f"{self._base_url}/{quote(self._section, safe='')}/work/=/product_id/"
            f"{quote(workno, safe='')}.html"
        )

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
