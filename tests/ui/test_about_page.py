from __future__ import annotations

import threading
import time

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QPushButton

from dlsite_organizer import __version__
from dlsite_organizer.services.update_checker import (
    UpdateCheckResult,
    UpdateCheckStatus,
)
from dlsite_organizer.ui.pages.about_page import AboutPage


@pytest.fixture
def qapp() -> QApplication:
    application = QApplication.instance()
    if isinstance(application, QApplication):
        return application
    return QApplication([])


class FakeUpdateService:
    def __init__(
        self,
        *results: UpdateCheckResult,
        block: bool = False,
    ) -> None:
        self._results = list(results)
        self.block = block
        self.calls = 0
        self.call_thread_ids: list[int] = []
        self.started = threading.Event()
        self.release = threading.Event()

    def check(self) -> UpdateCheckResult:
        self.calls += 1
        self.call_thread_ids.append(threading.get_ident())
        self.started.set()
        if self.block:
            self.release.wait(timeout=2)
        return self._results.pop(0)


def _wait_until(qapp: QApplication, predicate, timeout_seconds: float = 2.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        qapp.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    qapp.processEvents()
    assert predicate(), "timed out waiting for Qt state"


def _result(
    status: UpdateCheckStatus,
    *,
    latest_version: str | None = None,
    release_url: str | None = None,
    detail: str = "",
) -> UpdateCheckResult:
    return UpdateCheckResult(
        status=status,
        current_version=__version__,
        latest_version=latest_version,
        release_url=release_url,
        detail=detail,
    )


def test_about_page_exposes_public_identity_and_resource_actions(qapp: QApplication) -> None:
    page = AboutPage()

    assert page.branding_image.objectName() == "aboutBrandingImage"
    pixmap = page.branding_image.pixmap()
    assert pixmap is not None and not pixmap.isNull()
    assert page.version_value.text() == f"v{__version__}"
    assert page.description_value.text() == "DLsite 作品元数据查询与文件夹整理工具"
    assert page.developer_value.text() == "TIMEpings"
    assert "contributors" not in page.developer_value.text()
    assert page.copyright_value.text() == "Copyright © 2026 TIMEpings"
    assert "contributors" not in page.copyright_value.text()
    assert page.license_value.text() == "MIT"
    assert "非官方工具" in page.disclaimer_value.text()
    assert {
        "查看 LICENSE",
        "查看 THIRD_PARTY_NOTICES",
        "查看第三方许可证目录",
    } <= {button.text() for button in page.findChildren(QPushButton)}
    page.close()


def test_about_update_check_initial_state_does_not_access_network(qapp: QApplication) -> None:
    service = FakeUpdateService()
    page = AboutPage(update_check_service=service)

    assert page.update_current_version_value.text() == f"v{__version__}"
    assert page.check_update_button.isEnabled()
    assert page.update_status_label.text() == "尚未检查更新"
    assert page.release_page_button.isHidden()
    assert not page.release_page_button.isEnabled()
    assert service.calls == 0
    page.close()


def test_about_update_check_runs_in_worker_and_blocks_duplicate_clicks(
    qapp: QApplication,
) -> None:
    service = FakeUpdateService(
        _result(UpdateCheckStatus.UP_TO_DATE),
        block=True,
    )
    page = AboutPage(update_check_service=service)
    page.show()

    try:
        page.check_update_button.click()
        _wait_until(qapp, service.started.is_set)

        assert page.is_busy()
        assert not page.check_update_button.isEnabled()
        assert page.update_status_label.text() == "正在检查…"
        assert page.release_page_button.isHidden()
        assert not page.release_page_button.isEnabled()
        page.check_update_button.click()
        assert service.calls == 1
        assert service.call_thread_ids[0] != threading.get_ident()

        service.release.set()
        _wait_until(qapp, lambda: not page.is_busy())
        assert page.check_update_button.isEnabled()
        assert page.update_status_label.text() == "已是最新版本"
        assert page.release_page_button.isHidden()
        assert not page.release_page_button.isEnabled()
    finally:
        service.release.set()
        _wait_until(qapp, lambda: not page.is_busy())
        page.close()


def test_about_update_available_shows_and_opens_exact_validated_release_url(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release_url = "https://github.com/TIMEpings/dlsite-organizer/releases/tag/v1.1.0"
    service = FakeUpdateService(
        _result(
            UpdateCheckStatus.UPDATE_AVAILABLE,
            latest_version="v1.1.0",
            release_url=release_url,
        )
    )
    opened: list[str] = []

    def fake_open_url(url: QUrl) -> bool:
        opened.append(url.toString())
        return True

    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(fake_open_url))
    page = AboutPage(update_check_service=service)
    page.show()

    try:
        page.check_update_button.click()
        _wait_until(qapp, lambda: not page.is_busy())

        assert page.update_status_label.text() == "发现新版本 v1.1.0"
        assert page.release_page_button.isVisible()
        assert page.release_page_button.isEnabled()
        assert opened == []

        page.release_page_button.click()
        assert opened == [release_url]
    finally:
        page.close()


def test_about_update_check_failure_hides_release_action_and_detail(
    qapp: QApplication,
) -> None:
    service = FakeUpdateService(
        _result(UpdateCheckStatus.CHECK_FAILED, detail="SECRET HTTP 500 response")
    )
    page = AboutPage(update_check_service=service)

    try:
        page.check_update_button.click()
        _wait_until(qapp, lambda: not page.is_busy())

        assert page.update_status_label.text() == "检查失败，请稍后重试"
        assert "SECRET" not in page.update_status_label.text()
        assert page.check_update_button.isEnabled()
        assert page.release_page_button.isHidden()
        assert not page.release_page_button.isEnabled()
    finally:
        page.close()


def test_about_update_check_retry_clears_stale_release_action(qapp: QApplication) -> None:
    release_url = "https://github.com/TIMEpings/dlsite-organizer/releases/tag/v1.1.0"
    service = FakeUpdateService(
        _result(
            UpdateCheckStatus.UPDATE_AVAILABLE,
            latest_version="v1.1.0",
            release_url=release_url,
        ),
        _result(UpdateCheckStatus.CHECK_FAILED, detail="network detail"),
    )
    page = AboutPage(update_check_service=service)
    page.show()

    try:
        page.check_update_button.click()
        _wait_until(qapp, lambda: not page.is_busy())
        assert page.release_page_button.isVisible()

        page.check_update_button.click()
        assert page.release_page_button.isHidden()
        assert not page.release_page_button.isEnabled()
        _wait_until(qapp, lambda: not page.is_busy())

        assert page.update_status_label.text() == "检查失败，请稍后重试"
        assert page.release_page_button.isHidden()
        assert not page.release_page_button.isEnabled()
        assert service.calls == 2
    finally:
        page.close()


def test_about_update_check_can_retry_failure_to_success(qapp: QApplication) -> None:
    service = FakeUpdateService(
        _result(UpdateCheckStatus.CHECK_FAILED, detail="temporary failure"),
        _result(UpdateCheckStatus.UP_TO_DATE),
    )
    page = AboutPage(update_check_service=service)

    try:
        page.check_update_button.click()
        _wait_until(qapp, lambda: not page.is_busy())
        assert page.update_status_label.text() == "检查失败，请稍后重试"

        page.check_update_button.click()
        _wait_until(qapp, lambda: not page.is_busy())
        assert page.update_status_label.text() == "已是最新版本"
        assert page.check_update_button.isEnabled()
        assert service.calls == 2
    finally:
        page.close()
