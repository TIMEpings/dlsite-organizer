"""Check the latest published GitHub release without changing application state."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import unquote, urlsplit

import httpx

from dlsite_organizer import __version__, application_user_agent

GITHUB_LATEST_RELEASE_URL = (
    "https://api.github.com/repos/TIMEpings/dlsite-organizer/releases/latest"
)
GITHUB_API_VERSION = "2026-03-10"
UPDATE_CHECK_TIMEOUT_SECONDS = 8.0
_MAX_DETAIL_LENGTH = 160


class UpdateCheckStatus(StrEnum):
    """Stable outcomes for a release check."""

    UPDATE_AVAILABLE = "update_available"
    UP_TO_DATE = "up_to_date"
    CHECK_FAILED = "check_failed"


@dataclass(frozen=True, slots=True)
class UpdateCheckResult:
    """Immutable, UI-independent output from one release check."""

    status: UpdateCheckStatus
    current_version: str
    latest_version: str | None = None
    release_url: str | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class SemVer:
    """A small SemVer 2-style value object with an optional leading ``v``."""

    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()
    build: tuple[str, ...] = ()

    @classmethod
    def parse(cls, value: str) -> SemVer:
        """Parse a strict SemVer 2-style version string."""
        if not isinstance(value, str):
            raise ValueError("version must be a string")

        match = _SEMVER_PATTERN.fullmatch(value)
        if match is None:
            raise ValueError("malformed version")

        prerelease = _identifiers(match.group("prerelease"))
        for identifier in prerelease:
            if identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0"):
                raise ValueError("numeric prerelease identifiers cannot have leading zeroes")

        return cls(
            major=int(match.group("major")),
            minor=int(match.group("minor")),
            patch=int(match.group("patch")),
            prerelease=prerelease,
            build=_identifiers(match.group("build")),
        )

    def compare(self, other: SemVer) -> int:
        """Return ``-1``, ``0``, or ``1`` according to SemVer precedence."""
        for left, right in (
            (self.major, other.major),
            (self.minor, other.minor),
            (self.patch, other.patch),
        ):
            if left != right:
                return -1 if left < right else 1

        if not self.prerelease and not other.prerelease:
            return 0
        if not self.prerelease:
            return 1
        if not other.prerelease:
            return -1

        for left, right in zip(self.prerelease, other.prerelease, strict=False):
            if left == right:
                continue
            left_numeric = left.isdigit()
            right_numeric = right.isdigit()
            if left_numeric and right_numeric:
                return -1 if int(left) < int(right) else 1
            if left_numeric != right_numeric:
                return -1 if left_numeric else 1
            return -1 if left < right else 1

        if len(self.prerelease) == len(other.prerelease):
            return 0
        return -1 if len(self.prerelease) < len(other.prerelease) else 1


class UpdateCheckService:
    """Fetch and compare the one official repository's latest release."""

    def __init__(
        self,
        current_version: str | None = None,
        *,
        client: httpx.Client | None = None,
        transport: httpx.BaseTransport | None = None,
        timeout_seconds: float = UPDATE_CHECK_TIMEOUT_SECONDS,
    ) -> None:
        if client is not None and transport is not None:
            raise ValueError("client and transport are mutually exclusive")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be a positive finite number")

        self._current_version = __version__ if current_version is None else current_version
        self._client = client
        self._transport = transport
        self._timeout = httpx.Timeout(timeout_seconds)

    def check(self) -> UpdateCheckResult:
        """Return a safe result; expected network and response errors never escape."""
        current_version = self._current_version
        try:
            current = SemVer.parse(current_version)
        except ValueError:
            return self._failed(current_version, "invalid current version")

        try:
            if self._client is not None:
                response = self._client.get(
                    GITHUB_LATEST_RELEASE_URL,
                    headers=_request_headers(),
                    timeout=self._timeout,
                    follow_redirects=False,
                )
                return self._evaluate_response(response, current_version, current)

            with httpx.Client(
                transport=self._transport,
                timeout=self._timeout,
                follow_redirects=False,
                headers=_request_headers(),
            ) as client:
                response = client.get(
                    GITHUB_LATEST_RELEASE_URL,
                    headers=_request_headers(),
                    timeout=self._timeout,
                    follow_redirects=False,
                )
                return self._evaluate_response(response, current_version, current)
        except httpx.TimeoutException:
            return self._failed(current_version, "request timed out")
        except httpx.RequestError:
            return self._failed(current_version, "network request failed")
        except httpx.HTTPError:
            return self._failed(current_version, "HTTP request failed")
        except ValueError:
            return self._failed(current_version, "invalid release response")

    def check_for_update(self) -> UpdateCheckResult:
        """Descriptive alias for callers that prefer the operation name."""
        return self.check()

    def _evaluate_response(
        self,
        response: httpx.Response,
        current_version: str,
        current: SemVer,
    ) -> UpdateCheckResult:
        if response.status_code != httpx.codes.OK:
            return self._failed(current_version, f"HTTP status {response.status_code}")

        try:
            payload = response.json()
        except ValueError:
            return self._failed(current_version, "invalid JSON response")
        if not isinstance(payload, dict):
            return self._failed(current_version, "release response is not an object")

        tag_name = payload.get("tag_name")
        if tag_name is None:
            return self._failed(current_version, "release response is missing tag_name")
        if not isinstance(tag_name, str):
            return self._failed(current_version, "tag_name is not a string")
        if not tag_name:
            return self._failed(current_version, "tag_name is empty")

        release_url = payload.get("html_url")
        if release_url is None:
            return self._failed(current_version, "release response is missing html_url")
        if not isinstance(release_url, str):
            return self._failed(current_version, "html_url is not a string")
        if not release_url:
            return self._failed(current_version, "html_url is empty")

        try:
            latest = SemVer.parse(tag_name)
        except ValueError:
            return self._failed(current_version, "invalid latest version")
        if not _is_allowed_release_url(release_url, tag_name):
            return self._failed(current_version, "release URL is not allowed")

        status = (
            UpdateCheckStatus.UPDATE_AVAILABLE
            if current.compare(latest) < 0
            else UpdateCheckStatus.UP_TO_DATE
        )
        return UpdateCheckResult(
            status=status,
            current_version=current_version,
            latest_version=tag_name,
            release_url=release_url,
        )

    @staticmethod
    def _failed(current_version: str, detail: str) -> UpdateCheckResult:
        return UpdateCheckResult(
            status=UpdateCheckStatus.CHECK_FAILED,
            current_version=current_version,
            detail=detail[:_MAX_DETAIL_LENGTH],
        )


_SEMVER_PATTERN = re.compile(
    r"^(?:v)?"
    r"(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


def _identifiers(value: str | None) -> tuple[str, ...]:
    return tuple(value.split(".")) if value is not None else ()


def _request_headers() -> dict[str, str]:
    return {
        "User-Agent": application_user_agent(),
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
    }


def _is_allowed_release_url(value: str, tag_name: str) -> bool:
    """Accept only the canonical public release page for the returned tag."""
    if value != value.strip():
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return False

    if parsed.scheme.lower() != "https" or hostname is None or hostname.lower() != "github.com":
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    if port not in (None, 443) or parsed.query or parsed.fragment:
        return False

    expected_path = f"/TIMEpings/dlsite-organizer/releases/tag/{tag_name}"
    return unquote(parsed.path) == expected_path
