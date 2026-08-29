from dataclasses import dataclass

import pytest

from dlsite_organizer.domain.work import Work
from dlsite_organizer.providers.dlsite.exceptions import DlsiteConnectionError
from dlsite_organizer.services.lookup import LookupFailure, LookupFailureKind, LookupService
from dlsite_organizer.services.naming import NamingService


@dataclass
class FakeProvider:
    work: Work | None = None
    failure: Exception | None = None
    received_workno: str | None = None

    def fetch_work(self, workno: str) -> Work:
        self.received_workno = workno
        if self.failure:
            raise self.failure
        assert self.work is not None
        return self.work


def test_lookup_normalizes_fetches_and_names_work() -> None:
    provider = FakeProvider(
        work=Work(
            workno="RJ01609020",
            title="日本語タイトル",
            maker_name="サークル",
        )
    )
    service = LookupService(provider, NamingService())

    result = service.lookup(" rj01609020 ")

    assert provider.received_workno == "RJ01609020"
    assert result.formatted_name == "[サークル][RJ01609020] 日本語タイトル"


def test_lookup_exposes_validation_error_as_user_safe_failure() -> None:
    service = LookupService(FakeProvider(), NamingService())
    with pytest.raises(LookupFailure) as caught:
        service.lookup("not-a-code")

    assert caught.value.kind is LookupFailureKind.INVALID_CODE
    assert "RJ" in caught.value.user_message


def test_lookup_maps_connection_failure_for_ui() -> None:
    service = LookupService(
        FakeProvider(failure=DlsiteConnectionError("timeout")),
        NamingService(),
    )
    with pytest.raises(LookupFailure) as caught:
        service.lookup("RJ01609020")

    assert caught.value.kind is LookupFailureKind.CONNECTION
    assert "连接 DLsite 失败" in caught.value.user_message
