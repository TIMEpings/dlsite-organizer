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

HTML_FIXTURE = Path(__file__).parents[1] / "fixtures" / "product_semantic.html"
AJAX_FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "dlsite" / "product_info_RJ01609020.json"
)


def client_factory(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Callable[[], httpx.Client]:
    def create() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    return create


def test_provider_prefers_valid_product_info_ajax_and_returns_domain_work() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/maniax/product/info/ajax")
        assert request.url.params["product_id"] == "RJ01609020"
        return httpx.Response(200, text=AJAX_FIXTURE.read_text(encoding="utf-8"))

    provider = DlsiteProvider(client_factory=client_factory(handler))
    work = provider.fetch_work("rj01609020")

    assert work.workno == "RJ01609020"
    assert work.title.startswith("ご奉仕×癒しのプレシャスメイドタイム")
    assert work.source_section == "maniax"


def test_provider_lookup_retains_structured_evidence_without_a_second_request() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, text=AJAX_FIXTURE.read_text(encoding="utf-8"))

    lookup = DlsiteProvider(client_factory=client_factory(handler)).fetch_work_lookup("RJ01609020")

    assert len(requests) == 1
    assert lookup.work.workno == "RJ01609020"
    assert lookup.product_info is not None
    assert lookup.product_info.translation_info is not None
    assert lookup.product_info.translation_info.is_original is True


def test_provider_uses_html_when_structured_source_is_unusable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/product/info/ajax"):
            return httpx.Response(200, json={"unexpected": "payload"})
        assert request.url.path.endswith("/maniax/work/=/product_id/RJ01609020.html")
        return httpx.Response(200, text=HTML_FIXTURE.read_text(encoding="utf-8"))

    work = DlsiteProvider(client_factory=client_factory(handler)).fetch_work("RJ01609020")
    assert work.title == "雨音と過ごす夜"


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
    assert (
        provider.build_product_info_url("RJ01609020")
        == "https://example.test/home/product/info/ajax?product_id=RJ01609020"
    )
