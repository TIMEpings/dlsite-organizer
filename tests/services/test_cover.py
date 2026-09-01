import httpx

from dlsite_organizer import __version__
from dlsite_organizer.services.cover import CoverService

ONE_PIXEL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\x0dIDAT"
    b"\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00\x05\x00\x01\xff\x89\x99\x1d\x00\x00"
    b"\x00\x00IEND\xaeB\x60\x82"
)


def test_cover_request_uses_authoritative_application_user_agent(monkeypatch) -> None:
    requests: list[httpx.Request] = []

    class CapturingClient:
        def __init__(self, **kwargs) -> None:
            self._headers = kwargs["headers"]

        def __enter__(self) -> "CapturingClient":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def get(self, url: str) -> httpx.Response:
            request = httpx.Request("GET", url, headers=self._headers)
            requests.append(request)
            return httpx.Response(
                200,
                headers={"content-type": "image/png"},
                content=ONE_PIXEL_PNG,
                request=request,
            )

    monkeypatch.setattr(httpx, "Client", CapturingClient)

    assert CoverService().fetch("https://example.test/cover.png") == ONE_PIXEL_PNG
    assert requests[0].headers["User-Agent"] == f"dlsite-organizer/{__version__}"
