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
RICH_FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "dlsite" / "product_metadata_RJ01609020.json"
)
CHILD_AJAX_FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "dlsite" / "product_info_RJ01637033.json"
)
CHILD_RICH_FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "dlsite" / "product_metadata_RJ01637033.json"
)


def client_factory(
    handler: Callable[[httpx.Request], httpx.Response],
) -> Callable[[], httpx.Client]:
    def create() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    return create


def test_provider_prefers_valid_product_info_ajax_and_returns_domain_work() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/maniax/product/info/ajax"):
            assert request.url.params["product_id"] == "RJ01609020"
            return httpx.Response(200, text=AJAX_FIXTURE.read_text(encoding="utf-8"))
        assert request.url.path.endswith("/maniax/api/=/product.json")
        assert request.url.params["workno"] == "RJ01609020"
        assert request.url.params["locale"] == "ja_jp"
        return httpx.Response(200, text=RICH_FIXTURE.read_text(encoding="utf-8"))

    provider = DlsiteProvider(client_factory=client_factory(handler))
    work = provider.fetch_work("rj01609020")

    assert work.workno == "RJ01609020"
    assert work.title.startswith("ご奉仕×癒しのプレシャスメイドタイム")
    assert work.source_section == "maniax"


def test_provider_lookup_enriches_structured_core_with_rich_product_metadata() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/product/info/ajax"):
            return httpx.Response(200, text=AJAX_FIXTURE.read_text(encoding="utf-8"))
        return httpx.Response(200, text=RICH_FIXTURE.read_text(encoding="utf-8"))

    lookup = DlsiteProvider(client_factory=client_factory(handler)).fetch_work_lookup("RJ01609020")

    assert len(requests) == 2
    assert lookup.work.workno == "RJ01609020"
    assert lookup.work.maker_id == "RG01058997"
    assert lookup.work.maker_name == "のりプロ"
    assert lookup.work.cvs == ["佃煮のりお"]
    assert len(lookup.work.tags) == 6
    assert lookup.source == "DLSITE_PRODUCT_INFO_AJAX+DLSITE_PRODUCT_JSON"
    assert lookup.core_source == "DLSITE_PRODUCT_INFO_AJAX"
    assert lookup.metadata_source == "DLSITE_PRODUCT_JSON"
    assert lookup.product_info is not None
    assert lookup.product_info.translation_info is not None
    assert lookup.product_info.translation_info.is_original is True


def test_provider_child_lookup_resolves_original_maker_and_keeps_attribution_separate() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/product/info/ajax"):
            return httpx.Response(200, text=CHILD_AJAX_FIXTURE.read_text(encoding="utf-8"))
        if request.url.params["workno"] == "RJ01637033":
            return httpx.Response(200, text=CHILD_RICH_FIXTURE.read_text(encoding="utf-8"))
        assert request.url.params["workno"] == "RJ01609020"
        return httpx.Response(200, text=RICH_FIXTURE.read_text(encoding="utf-8"))

    lookup = DlsiteProvider(client_factory=client_factory(handler)).fetch_work_lookup(
        "RJ01637033"
    )

    assert len(requests) == 3
    assert lookup.work.maker_id == "RG01058997"
    assert lookup.work.maker_name == "のりプロ"
    assert lookup.translation_attribution is not None
    assert lookup.translation_attribution.maker_id == "RG01001331"
    assert lookup.translation_attribution.maker_name == "MYHONYAKU"
    assert lookup.work.maker_name != lookup.translation_attribution.maker_name
    assert lookup.work.language == "CHI_HANS"


def test_provider_rich_failure_returns_core_work_without_throwing() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/product/info/ajax"):
            return httpx.Response(200, text=AJAX_FIXTURE.read_text(encoding="utf-8"))
        return httpx.Response(503)

    lookup = DlsiteProvider(client_factory=client_factory(handler)).fetch_work_lookup(
        "RJ01609020"
    )

    assert lookup.source == "DLSITE_PRODUCT_INFO_AJAX"
    assert lookup.core_source == "DLSITE_PRODUCT_INFO_AJAX"
    assert lookup.metadata_source is None
    assert lookup.work.maker_id == "RG01058997"
    assert lookup.work.maker_name is None
    assert lookup.work.cvs == []
    assert lookup.work.tags == []


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
    assert (
        provider.build_product_metadata_url("RJ01609020")
        == "https://example.test/home/api/=/product.json?workno=RJ01609020&locale=ja_jp"
    )
