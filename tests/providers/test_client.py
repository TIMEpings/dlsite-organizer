from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from dlsite_organizer.providers.dlsite.client import DlsiteProvider
from dlsite_organizer.providers.dlsite.exceptions import (
    DlsiteConnectionError,
    DlsiteHttpError,
    WorkNotFoundError,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "product_semantic.html"


def client_factory(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Callable[[], httpx.Client]:
    def create() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    return create


def test_provider_fetches_page_and_returns_domain_work() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/maniax/work/=/product_id/RJ01609020.html")
        return httpx.Response(200, text=FIXTURE.read_text(encoding="utf-8"))

    provider = DlsiteProvider(client_factory=client_factory(handler))
    work = provider.fetch_work("rj01609020")

    assert work.workno == "RJ01609020"
    assert work.title == "雨音と過ごす夜"
    assert work.source_section == "maniax"


def test_provider_maps_not_found() -> None:
    provider = DlsiteProvider(client_factory=client_factory(lambda _request: httpx.Response(404)))
    with pytest.raises(WorkNotFoundError):
        provider.fetch_work("RJ01609020")


def test_provider_maps_unexpected_http_status() -> None:
    provider = DlsiteProvider(client_factory=client_factory(lambda _request: httpx.Response(503)))
    with pytest.raises(DlsiteHttpError):
        provider.fetch_work("RJ01609020")


def test_provider_maps_transport_timeout() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    provider = DlsiteProvider(client_factory=client_factory(handler))
    with pytest.raises(DlsiteConnectionError):
        provider.fetch_work("RJ01609020")


def test_url_construction_is_centralized_and_section_scoped() -> None:
    provider = DlsiteProvider(section="home", base_url="https://example.test/")
    assert (
        provider.build_product_url("RJ01609020")
        == "https://example.test/home/work/=/product_id/RJ01609020.html"
    )
