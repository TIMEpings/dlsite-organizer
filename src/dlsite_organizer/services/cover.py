"""Optional cover image retrieval, designed to run in a worker thread."""

from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


class CoverService:
    """Download a small optional image without affecting metadata success."""

    def __init__(self, timeout_seconds: float = 10.0, max_bytes: int = 10_000_000) -> None:
        self._timeout = timeout_seconds
        self._max_bytes = max_bytes

    def fetch(self, url: str) -> bytes | None:
        """Return cover bytes, or None for any recoverable cover failure."""
        try:
            with httpx.Client(
                timeout=self._timeout,
                follow_redirects=True,
                headers={"User-Agent": "dlsite-organizer/0.1 (cover lookup)"},
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
        return response.content
