from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from dlsite_organizer.domain.work_code import WorkCode
from dlsite_organizer.providers.dlsite.client import (
    DlsiteProvider,
    DlsiteSection,
    source_route_for,
)
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
BJ_AJAX_FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "dlsite" / "product_info_BJ00000001.json"
)
BJ_RICH_FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "dlsite"
    / "product_metadata_BJ00000001.json"
)
VJ_AJAX_FIXTURE = (
    Path(__file__).parents[1] / "fixtures" / "dlsite" / "product_info_VJ00000001.json"
)
VJ_RICH_FIXTURE = (
    Path(__file__).parents[1]
    / "fixtures"
    / "dlsite"
    / "product_metadata_VJ00000001.json"
)


def client_factory(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    follow_redirects: bool = False,
) -> Callable[[], httpx.Client]:
    def create() -> httpx.Client:
        return httpx.Client(
            transport=httpx.MockTransport(handler),
            follow_redirects=follow_redirects,
        )

    return create


def test_source_route_is_typed_and_bounded_for_all_supported_prefixes() -> None:
    assert source_route_for(
        WorkCode.parse("RJ01609020"), configured_section="home"
    ).sections == ("home",)
    assert source_route_for(
        WorkCode.parse("BJ00000001"), configured_section="maniax"
    ).sections == (DlsiteSection.BOOKS.value,)
    vj_route = source_route_for(
        WorkCode.parse("VJ00000001"), configured_section="maniax"
    )
    assert vj_route.sections == (DlsiteSection.SOFT.value, DlsiteSection.PRO.value)
    assert vj_route.resolve_public_page is True


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


def test_provider_routes_bj_to_books_and_accepts_missing_optional_fields() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/books/product/info/ajax"):
            assert request.url.params["product_id"] == "BJ00000001"
            return httpx.Response(200, text=BJ_AJAX_FIXTURE.read_text(encoding="utf-8"))
        assert request.url.path.endswith("/books/api/=/product.json")
        assert request.url.params["workno"] == "BJ00000001"
        return httpx.Response(200, text=BJ_RICH_FIXTURE.read_text(encoding="utf-8"))

    lookup = DlsiteProvider(client_factory=client_factory(handler)).fetch_work_lookup(
        "bj00000001"
    )

    assert [request.url.path for request in requests] == [
        "/books/product/info/ajax",
        "/books/api/=/product.json",
    ]
    assert lookup.work.workno == "BJ00000001"
    assert lookup.work.source_section == "books"
    assert lookup.work.maker_id == "BG00001"
    assert lookup.work.maker_name == "Fixture Publisher"
    assert lookup.work.series_name == "Fixture Series"
    assert lookup.work.cvs == []
    assert lookup.work.language is None
    assert lookup.metadata_source == "DLSITE_PRODUCT_JSON"


def test_provider_resolves_vj_public_redirect_before_structured_lookup() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/soft/work/=/product_id/VJ00000001.html"):
            return httpx.Response(
                307,
                headers={
                    "Location": (
                        "https://example.test/pro/work/=/product_id/"
                        "VJ00000001.html"
                    )
                },
            )
        if request.url.path.endswith("/pro/work/=/product_id/VJ00000001.html"):
            return httpx.Response(200, text="<html>route resolved</html>")
        if request.url.path.endswith("/pro/product/info/ajax"):
            assert request.url.params["product_id"] == "VJ00000001"
            return httpx.Response(200, text=VJ_AJAX_FIXTURE.read_text(encoding="utf-8"))
        assert request.url.path.endswith("/pro/api/=/product.json")
        assert request.url.params["workno"] == "VJ00000001"
        return httpx.Response(200, text=VJ_RICH_FIXTURE.read_text(encoding="utf-8"))

    lookup = DlsiteProvider(
        client_factory=client_factory(handler, follow_redirects=True)
    ).fetch_work_lookup("VJ00000001")

    assert [request.url.path for request in requests] == [
        "/soft/work/=/product_id/VJ00000001.html",
        "/pro/work/=/product_id/VJ00000001.html",
        "/pro/product/info/ajax",
        "/pro/api/=/product.json",
    ]
    assert lookup.work.workno == "VJ00000001"
    assert lookup.work.source_section == "pro"
    assert lookup.work.maker_id == "VG00001"
    assert lookup.work.cvs == ["Fixture Voice"]
    assert lookup.work.tags == ["Fixture genre"]


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


def test_url_helpers_use_the_prefix_route_for_bj_and_vj() -> None:
    provider = DlsiteProvider(base_url="https://example.test")

    assert provider.build_product_url("BJ00000001") == (
        "https://example.test/books/work/=/product_id/BJ00000001.html"
    )
    assert provider.build_product_info_url("VJ00000001") == (
        "https://example.test/soft/product/info/ajax?product_id=VJ00000001"
    )
    assert provider.build_product_metadata_url("VJ00000001") == (
        "https://example.test/soft/api/=/product.json?workno=VJ00000001&locale=ja_jp"
    )
