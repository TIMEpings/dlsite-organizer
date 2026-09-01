"""Optional cover image retrieval, designed to run in a worker thread."""

from __future__ import annotations

import logging

import httpx

from dlsite_organizer import application_user_agent

logger = logging.getLogger(__name__)


class CoverService:
    """Download a small optional image without affecting metadata success."""

    def __init__(self, timeout_seconds: float = 10.0, max_bytes: int = 10_000_000) -> None:
        self._timeout = timeout_seconds
        self._max_bytes = max_bytes
        # Cover retrieval is optional.  Keep the bytes that were already
        # retrieved by the lookup flow available to other local views, while
        # keeping the queue boundary explicit: ``cached_cover_for`` never
        # performs I/O or a network request.
        self._cached_covers: dict[str, bytes] = {}

    def fetch(self, url: str, *, workno: str | None = None) -> bytes | None:
        """Return cover bytes, or None for any recoverable cover failure."""
        try:
            with httpx.Client(
                timeout=self._timeout,
                follow_redirects=True,
                headers={"User-Agent": application_user_agent()},
            ) as client:
                response = client.get(url)
                response.raise_for_status()
        except (httpx.HTTPError, ValueError):
            logger.info("Cover download failed for %s", url, exc_info=True)
            return None
        content_type = response.headers.get("content-type", "")
        if not content_type.lower().startswith("image/") or len(response.content) > self._max_bytes:
            logger.info("Ignoring invalid or oversized cover response for %s", url)
            return None
        if workno:
            self.remember_cached_cover(workno, response.content)
        return response.content

    def fetch_for_work(self, workno: str, url: str) -> bytes | None:
        """Fetch a cover and make the successful result locally addressable."""
        return self.fetch(url, workno=workno)

    def remember_cached_cover(self, workno: str, content: bytes) -> None:
        """Remember already-retrieved cover bytes without performing any I/O."""
        if content:
            self._cached_covers[workno.strip().upper()] = bytes(content)

    def cached_cover_for(self, workno: str) -> bytes | None:
        """Return a locally cached cover, never downloading or refreshing it."""
        return self._cached_covers.get(workno.strip().upper())
