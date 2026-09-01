from collections.abc import Callable

import httpx
import pytest

from dlsite_organizer import __version__, application_user_agent
from dlsite_organizer.services.update_checker import (
    GITHUB_API_VERSION,
    GITHUB_LATEST_RELEASE_URL,
    SemVer,
    UpdateCheckResult,
    UpdateCheckService,
    UpdateCheckStatus,
)

RELEASE_URL = "https://github.com/TIMEpings/dlsite-organizer/releases/tag/{}"


def run_check(
    payload: object,
    *,
    current_version: str = __version__,
    status_code: int = 200,
    response_text: str | None = None,
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
) -> tuple[UpdateCheckResult, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def transport_handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if handler is not None:
            return handler(request)
        if response_text is not None:
            return httpx.Response(status_code, text=response_text)
        return httpx.Response(status_code, json=payload)

    result = UpdateCheckService(
        current_version=current_version,
        transport=httpx.MockTransport(transport_handler),
    ).check()
    return result, requests


def valid_payload(tag_name: str = "v1.1.0", release_url: str | None = None) -> dict[str, object]:
    return {
        "tag_name": tag_name,
        "html_url": release_url or RELEASE_URL.format(tag_name),
        "name": "ignored",
        "body": "ignored",
        "assets": [{"browser_download_url": "ignored"}],
    }


@pytest.mark.parametrize(
    ("remote", "current", "expected"),
    [
        ("1.1.0", "1.0.0", UpdateCheckStatus.UPDATE_AVAILABLE),
        ("v1.1.0", "1.0.0", UpdateCheckStatus.UPDATE_AVAILABLE),
        ("1.0.0", "1.0.0", UpdateCheckStatus.UP_TO_DATE),
        ("1.0.0", "1.1.0", UpdateCheckStatus.UP_TO_DATE),
        ("1.0.0", "v1.0.0", UpdateCheckStatus.UP_TO_DATE),
        ("1.1.0", "1.1.0-rc.1", UpdateCheckStatus.UPDATE_AVAILABLE),
        ("1.0.0-rc.2", "1.0.0-rc.1", UpdateCheckStatus.UPDATE_AVAILABLE),
        ("1.0.0-rc.1", "1.0.0-rc.2", UpdateCheckStatus.UP_TO_DATE),
        ("1.0.0+release.2", "1.0.0+release.1", UpdateCheckStatus.UP_TO_DATE),
    ],
)
def test_version_decision(remote: str, current: str, expected: UpdateCheckStatus) -> None:
    result, _ = run_check(valid_payload(remote), current_version=current)

    assert result.status is expected
    assert result.latest_version == remote
    assert result.release_url == RELEASE_URL.format(remote)


def test_candidate_is_up_to_date_when_published_release_is_older() -> None:
    result, _ = run_check(valid_payload("v1.0.0"), current_version=__version__)

    assert result.status is UpdateCheckStatus.UP_TO_DATE
    assert result.current_version == __version__
    assert result.latest_version == "v1.0.0"
    assert result.release_url == RELEASE_URL.format("v1.0.0")


@pytest.mark.parametrize("value", ["1.0", "1.0.0.0", "1.01.0", "v1.0", "1.0.0-01"])
def test_semver_parser_rejects_malformed_versions(value: str) -> None:
    with pytest.raises(ValueError):
        SemVer.parse(value)


def test_malformed_current_version_fails_without_a_network_request() -> None:
    result, requests = run_check(valid_payload(), current_version="1.0")

    assert result.status is UpdateCheckStatus.CHECK_FAILED
    assert result.latest_version is None
    assert result.release_url is None
    assert requests == []


def test_malformed_latest_version_fails() -> None:
    result, _ = run_check(valid_payload("v1.0"))

    assert result.status is UpdateCheckStatus.CHECK_FAILED
    assert result.latest_version is None
    assert result.release_url is None


@pytest.mark.parametrize(
    "exception",
    [httpx.ReadTimeout("timed out"), httpx.ConnectError("failed")],
)
def test_network_failures_are_soft(exception: httpx.RequestError) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exception

    result, _ = run_check(valid_payload(), handler=handler)

    assert result.status is UpdateCheckStatus.CHECK_FAILED
    assert result.latest_version is None
    assert result.release_url is None


@pytest.mark.parametrize("status_code", [301, 403, 404, 429, 500])
def test_non_200_statuses_are_soft(status_code: int) -> None:
    result, _ = run_check({}, status_code=status_code, response_text="large body is ignored")

    assert result.status is UpdateCheckStatus.CHECK_FAILED
    assert result.latest_version is None
    assert result.release_url is None
    assert result.detail == f"HTTP status {status_code}"


def test_redirect_is_not_followed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            301,
            headers={"Location": RELEASE_URL.format("v1.1.0")},
        )

    result, requests = run_check({}, handler=handler)

    assert result.status is UpdateCheckStatus.CHECK_FAILED
    assert len(requests) == 1


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        [],
        {"html_url": RELEASE_URL.format("v1.1.0")},
        {"tag_name": 110, "html_url": RELEASE_URL.format("v1.1.0")},
        {"tag_name": "v1.1.0"},
        {"tag_name": "v1.1.0", "html_url": 110},
        {"tag_name": "", "html_url": RELEASE_URL.format("")},
        {"tag_name": "v1.1.0", "html_url": ""},
    ],
)
def test_malformed_json_shapes_are_soft(payload: object) -> None:
    if isinstance(payload, str):
        result, _ = run_check({}, response_text=payload)
    else:
        result, _ = run_check(payload)

    assert result.status is UpdateCheckStatus.CHECK_FAILED
    assert result.latest_version is None
    assert result.release_url is None


@pytest.mark.parametrize(
    "release_url",
    [
        "http://github.com/TIMEpings/dlsite-organizer/releases/tag/v1.1.0",
        "https://evil.example/TIMEpings/dlsite-organizer/releases/tag/v1.1.0",
        "https://user:password@github.com/TIMEpings/dlsite-organizer/releases/tag/v1.1.0",
        "https://github.com:8443/TIMEpings/dlsite-organizer/releases/tag/v1.1.0",
        "https://github.com/TIMEpings/dlsite-organizer/releases/tag/v1.1.0?download=1",
        "https://github.com/TIMEpings/dlsite-organizer/releases/tag/v1.1.0#assets",
        "https://github.com/other/repo/releases/tag/v1.1.0",
        "https://github.com/TIMEpings/dlsite-organizer/releases/tag/v9.9.9",
    ],
)
def test_release_url_allowlist_rejects_untrusted_urls(release_url: str) -> None:
    result, _ = run_check(valid_payload(release_url=release_url))

    assert result.status is UpdateCheckStatus.CHECK_FAILED
    assert result.latest_version is None
    assert result.release_url is None


def test_release_url_can_use_percent_encoding_for_the_same_path() -> None:
    result, _ = run_check(
        valid_payload(
            "v1.1.0",
            "https://github.com/TIMEpings/dlsite-organizer/releases/tag/%76%31.1.0",
        ),
        current_version="1.0.0",
    )

    assert result.status is UpdateCheckStatus.UPDATE_AVAILABLE
    assert result.release_url is not None


def test_request_contract_and_environment_tokens_are_ignored(monkeypatch) -> None:
    monkeypatch.setenv("GH_TOKEN", "SHOULD_NOT_BE_USED")
    monkeypatch.setenv("GITHUB_TOKEN", "SHOULD_NOT_BE_USED")

    result, requests = run_check(valid_payload("v1.2.0"))

    assert result.status is UpdateCheckStatus.UPDATE_AVAILABLE
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "GET"
    assert str(request.url) == GITHUB_LATEST_RELEASE_URL
    assert request.headers["User-Agent"] == application_user_agent()
    assert request.headers["Accept"] == "application/vnd.github+json"
    assert request.headers["X-GitHub-Api-Version"] == GITHUB_API_VERSION
    assert "Authorization" not in request.headers


def test_injected_client_is_not_closed_by_service() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json=valid_payload("v1.2.0"))
        )
    )
    try:
        result = UpdateCheckService(current_version=__version__, client=client).check()

        assert result.status is UpdateCheckStatus.UPDATE_AVAILABLE
        assert client.is_closed is False
    finally:
        client.close()


def test_failure_detail_is_bounded_and_does_not_copy_response_body() -> None:
    secret_body = "SECRET_RESPONSE_BODY " * 10_000
    result, _ = run_check({}, status_code=500, response_text=secret_body)

    assert result.status is UpdateCheckStatus.CHECK_FAILED
    assert len(result.detail) <= 160
    assert "SECRET_RESPONSE_BODY" not in result.detail
