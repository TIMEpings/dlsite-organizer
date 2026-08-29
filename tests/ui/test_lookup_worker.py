from tests.services.test_lookup import FakeProvider

from dlsite_organizer.providers.dlsite.exceptions import DlsiteConnectionError
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.ui.workers.lookup_worker import LookupWorker


def test_worker_emits_user_safe_service_failure() -> None:
    service = LookupService(
        FakeProvider(failure=DlsiteConnectionError("internal details")),
        NamingService(),
    )
    worker = LookupWorker(service, CoverService(), "RJ01609020")
    messages: list[str] = []
    completions: list[bool] = []
    worker.failed.connect(messages.append)
    worker.finished.connect(lambda: completions.append(True))

    worker.run()

    assert messages == ["连接 DLsite 失败，请检查网络后稍后重试。"]
    assert completions == [True]
    assert "internal details" not in messages[0]
