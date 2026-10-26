from __future__ import annotations

import threading
import time

import pytest
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QImage, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QPushButton,
    QStyle,
    QStyleOptionButton,
    QWidget,
)

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


def _render_button_state(button: QPushButton, state: QStyle.StateFlag) -> QImage:
    option = QStyleOptionButton()
    option.initFrom(button)
    option.rect = button.rect()
    option.text = button.text()
    option.state = state
    image = QImage(button.size(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    button.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, button)
    painter.end()
    return image


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
    assert "GitHub" in page.privacy_value.text()
    assert "检查更新" in page.privacy_value.text()
    assert {
        "LICENSE",
        "第三方声明",
        "第三方许可证目录",
    } <= {button.text() for button in page.findChildren(QPushButton)}
    page.close()


def test_about_update_check_initial_state_does_not_access_network(qapp: QApplication) -> None:
    service = FakeUpdateService()
    page = AboutPage(update_check_service=service)
    page.resize(720, 540)
    page.show()
    qapp.processEvents()

    assert page.update_current_version_value.text() == f"v{__version__}"
    assert page.check_update_button.isEnabled()
    assert page.check_update_button.text() == "检查更新"
    assert page.check_update_button.property("visualRole") == "secondary"
    assert page.update_status_label.text() == "尚未检查更新"
    assert page.release_page_button.isHidden()
    assert not page.release_page_button.isEnabled()
    assert (
        abs(
            page.check_update_button.mapTo(page, page.check_update_button.rect().center()).y()
            - page.update_status_label.mapTo(page, page.update_status_label.rect().center()).y()
        )
        <= 8
    )
    assert service.calls == 0
    page.close()


def test_about_secondary_update_buttons_render_hover_pressed_and_disabled_states(
    qapp: QApplication,
) -> None:
    from dlsite_organizer.ui.theme.tokens import LIGHT_COLORS

    page = AboutPage()
    page.resize(960, 640)
    page.show()
    qapp.processEvents()

    for label in ("检查更新", "重新检查"):
        button = page.check_update_button
        button.setText(label)
        normal = _render_button_state(button, QStyle.StateFlag.State_Enabled)
        hover = _render_button_state(
            button, QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_MouseOver
        )
        pressed = _render_button_state(
            button,
            QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Sunken,
        )
        disabled = _render_button_state(button, QStyle.StateFlag.State_None)
        disabled_hover = _render_button_state(button, QStyle.StateFlag.State_MouseOver)

        sample = (button.width() - 8, button.height() // 2)
        normal_pixel = normal.pixelColor(*sample)
        hover_pixel = hover.pixelColor(*sample)
        pressed_pixel = pressed.pixelColor(*sample)
        disabled_pixel = disabled.pixelColor(*sample)
        assert hover_pixel == QColor(LIGHT_COLORS.sidebar_hover)
        assert normal_pixel != hover_pixel
        assert pressed_pixel == QColor(LIGHT_COLORS.selection)
        assert pressed_pixel != hover_pixel
        assert disabled_pixel == QColor(LIGHT_COLORS.surface_secondary)
        assert disabled_hover.pixelColor(*sample) == disabled_pixel

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
        assert page.check_update_button.text() == "重新检查"
        assert page.check_update_button.property("visualRole") == "secondary"
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
        assert page.check_update_button.text() == "重新检查"
        assert page.check_update_button.property("visualRole") == "secondary"
        assert page.release_page_button.property("visualRole") == "primary"
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


@pytest.mark.parametrize(
    ("button_name", "relative_path", "display_name"),
    [
        ("license_button", "LICENSE", "LICENSE"),
        ("third_party_notices_button", "THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES"),
        ("third_party_licenses_button", "licenses", "第三方许可证目录"),
    ],
)
def test_about_resource_actions_keep_exact_targets(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    button_name: str,
    relative_path: str,
    display_name: str,
) -> None:
    page = AboutPage()
    opened: list[tuple[str, str]] = []
    monkeypatch.setattr(page, "_open_resource", lambda path, name: opened.append((path, name)))
    getattr(page, button_name).click()
    assert opened == [(relative_path, display_name)]
    page.close()


@pytest.mark.parametrize("failure", ["missing", "invalid", "open_failed"])
def test_about_resource_failure_keeps_warning(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    failure: str,
) -> None:
    from dlsite_organizer.ui.pages import about_page

    path = tmp_path / "LICENSE"
    if failure == "open_failed":
        path.write_text("fixture", encoding="utf-8")

    def resolve(relative):
        assert relative == "LICENSE"
        if failure == "invalid":
            raise ValueError("fixture")
        return path

    warnings: list[tuple[str, str]] = []
    monkeypatch.setattr(about_page, "application_resource_path", resolve)
    monkeypatch.setattr(QDesktopServices, "openUrl", staticmethod(lambda url: False))
    monkeypatch.setattr(
        about_page.QMessageBox,
        "warning",
        lambda parent, title, message: warnings.append((title, message)),
    )
    page = AboutPage()
    page.license_button.click()
    assert warnings == [("无法打开资源", "未找到或无法打开 LICENSE。")]
    page.close()


def test_about_legal_disclosures_are_complete(qapp: QApplication) -> None:
    page = AboutPage()
    assert page.disclaimer_value.text() == (
        "本项目为非官方工具，与 DLsite / 株式会社エイシス无官方关联。"
    )
    assert page.privacy_value.text() == (
        "应用数据保存在本机。没有遥测、分析或远程错误上报。作品查询和可选封面下载会访问 "
        "DLsite；只有用户点击“检查更新”时才会以匿名 HTTPS 访问 GitHub 公开 Releases。"
    )
    page.close()


def test_about_resource_keyboard_order_and_activation(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    page = AboutPage()
    page.resize(720, 540)
    page.show()
    page.activateWindow()
    qapp.processEvents()
    opened: list[str] = []
    monkeypatch.setattr(page, "_open_resource", lambda path, name: opened.append(path))
    page.check_update_button.setFocus(Qt.FocusReason.TabFocusReason)
    for button, target in [
        (page.license_button, "LICENSE"),
        (page.third_party_notices_button, "THIRD_PARTY_NOTICES.md"),
        (page.third_party_licenses_button, "licenses"),
    ]:
        focused = qapp.focusWidget()
        assert focused is not None
        QTest.keyClick(focused, Qt.Key.Key_Tab)
        assert qapp.focusWidget() is button
        QTest.keyClick(button, Qt.Key.Key_Space)
        assert opened[-1] == target
    page.close()


@pytest.mark.parametrize("width", [420, 720])
def test_about_links_wrap_and_keep_keyboard_targets(qapp: QApplication, width: int) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QScrollArea

    page = AboutPage()
    page.resize(width, 720)
    page.show()
    qapp.processEvents()
    links = [page.license_button, page.third_party_notices_button, page.third_party_licenses_button]
    assert all(link.isVisible() and link.height() == 28 for link in links), [
        (link.isVisible(), link.height(), link.minimumHeight(), link.maximumHeight())
        for link in links
    ]
    assert all(link.focusPolicy() == Qt.FocusPolicy.StrongFocus for link in links)
    assert all(link.cursor().shape() == Qt.CursorShape.PointingHandCursor for link in links)
    scroll = page.findChild(QScrollArea)
    assert scroll is not None
    assert scroll.horizontalScrollBar().maximum() == 0
    if width == 420:
        from PySide6.QtCore import QRect

        layout = page.notices_box.layout()
        assert layout is not None
        item = layout.itemAt(2)
        assert item is not None
        flow = item.layout()
        assert flow is not None
        flow.setGeometry(QRect(0, 0, 180, 100))
        assert links[-1].y() > links[0].y()
    else:
        assert len({link.y() for link in links}) == 1
    assert page.check_update_button.height() == 32
    page.close()


def test_about_info_icon_uses_shared_vector_size_and_color_states(qapp: QApplication) -> None:
    from PySide6.QtCore import QSize
    from PySide6.QtGui import QIcon

    from dlsite_organizer.ui.theme.icons import _draw_icon, navigation_icon

    icon = navigation_icon("about")
    assert icon is navigation_icon("about")
    pixmap = _draw_icon("about", "#123456")
    assert pixmap.size() == QSize(36, 36)
    assert pixmap.devicePixelRatio() == 2
    normal = icon.pixmap(QSize(18, 18), QIcon.Mode.Normal).toImage()
    selected = icon.pixmap(QSize(18, 18), QIcon.Mode.Selected).toImage()
    assert normal != selected
    assert normal.pixelColor(9, 5).alpha() > 0
    assert normal.pixelColor(0, 0).alpha() == 0


def test_about_update_keyboard_retry_is_secondary(qapp: QApplication) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    service = FakeUpdateService(_result(UpdateCheckStatus.CHECK_FAILED))
    page = AboutPage(update_check_service=service)
    page.show()
    page.check_update_button.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(page.check_update_button, Qt.Key.Key_Space)
    _wait_until(qapp, lambda: not page.is_busy())
    assert service.calls == 1
    assert page.check_update_button.text() == "重新检查"
    assert page.check_update_button.property("visualRole") == "secondary"
    assert page._release_url is None
    page.close()


@pytest.mark.parametrize("width", [960, 1160, 1540])
@pytest.mark.parametrize("state", ["initial", "checking", "latest", "available", "failed"])
def test_about_update_row_aligns_visible_controls(
    qapp: QApplication,
    width: int,
    state: str,
) -> None:
    from PySide6.QtWidgets import QScrollArea

    page = AboutPage(update_check_service=FakeUpdateService())
    page.resize(width, 800)
    page.show()
    if state == "checking":
        page.check_update_button.setEnabled(False)
        page.update_status_label.setText("正在检查…")
    elif state == "latest":
        page._render_update_result(_result(UpdateCheckStatus.UP_TO_DATE))
    elif state == "available":
        page._render_update_result(
            _result(
                UpdateCheckStatus.UPDATE_AVAILABLE,
                latest_version="9.9.9",
                release_url="https://github.com/TIMEpings/dlsite-organizer/releases/tag/v9.9.9",
            )
        )
    elif state == "failed":
        page._render_update_result(_result(UpdateCheckStatus.CHECK_FAILED))
    qapp.processEvents()

    visible: list[QWidget] = [page.update_current_version_value, page.update_status_label]
    if page.release_page_button.isVisible():
        visible.append(page.release_page_button)
    visible.append(page.check_update_button)
    assert page.check_update_button.height() == 32
    if page.release_page_button.isVisible():
        assert page.release_page_button.height() == 32
    centers = [widget.mapTo(page.update_section, widget.rect().center()).y() for widget in visible]
    x_positions = [
        widget.mapTo(page.update_section, widget.rect().topLeft()).x() for widget in visible
    ]
    assert x_positions == sorted(x_positions)
    assert max(centers) - min(centers) <= 2, [
        (widget.objectName(), widget.geometry().getRect(), center)
        for widget, center in zip(visible, centers, strict=True)
    ]
    if state == "failed":
        from PySide6.QtGui import QFontMetrics

        failure_text = "检查失败，请稍后重试"
        metrics = QFontMetrics(page.update_status_label.font())
        assert page.update_status_label.text() == failure_text
        assert page.update_status_label.width() >= metrics.horizontalAdvance(failure_text)
        assert page.update_status_label.height() <= page.update_status_label.minimumHeight()
    assert all(
        widget.mapTo(page.update_section, widget.rect().bottomRight()).x()
        < page.update_section.width()
        for widget in visible
    )
    scroll = page.findChild(QScrollArea)
    assert scroll is not None
    assert scroll.horizontalScrollBar().maximum() == 0
    page.close()


def test_about_identity_reflows_when_resized_without_losing_content(qapp: QApplication) -> None:
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QScrollArea

    page = AboutPage(update_check_service=FakeUpdateService())
    page.show()
    for width in (1160, 420, 720, 1160):
        page.resize(width, 420)
        qapp.processEvents()
        scroll = page.findChild(QScrollArea)
        assert scroll is not None
        content = scroll.widget()
        assert content is not None
        assert content.width() <= scroll.viewport().width()
        assert scroll.horizontalScrollBar().maximum() == 0
        assert page.about_header.title_label.text() == "DLsite Organizer"
        assert page.copyright_value.text() == "Copyright © 2026 TIMEpings"
        assert page.branding_image.size().width() == 104
        for label in (page.about_header.title_label, page.copyright_value):
            assert label.isVisible()
            assert label.height() >= label.heightForWidth(label.width())
            bounds = label.rect().translated(label.mapTo(content, QPoint(0, 0)))
            assert content.rect().contains(bounds)
        if width == 420:
            assert page.about_header.title_label.height() > (
                page.about_header.title_label.fontMetrics().height()
            )
            assert scroll.verticalScrollBar().maximum() > 0
        else:
            assert page.about_header.title_label.height() < (
                2 * page.about_header.title_label.fontMetrics().height()
            )
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        qapp.processEvents()
        link = page.third_party_licenses_button
        assert scroll.viewport().rect().contains(
            link.rect().translated(link.mapTo(scroll.viewport(), QPoint(0, 0)))
        )
    page.close()


def test_about_update_actions_allow_larger_fonts_after_repolish(qapp: QApplication) -> None:
    page = AboutPage(update_check_service=FakeUpdateService())
    page.resize(720, 720)
    for button in (page.check_update_button, page.release_page_button):
        font = button.font()
        font.setPointSize(18)
        button.setFont(font)
    page.show()
    for result in (
        _result(
            UpdateCheckStatus.UPDATE_AVAILABLE,
            latest_version="9.9.9",
            release_url="https://github.com/TIMEpings/dlsite-organizer/releases/tag/v9.9.9",
        ),
        _result(UpdateCheckStatus.CHECK_FAILED),
    ):
        page._render_update_result(result)
        qapp.processEvents()
        for button in (page.check_update_button, page.release_page_button):
            if button.isVisible():
                assert button.height() >= 32
                assert button.height() >= button.fontMetrics().height() + 10
    page.close()


def test_about_long_failure_status_wraps_inside_narrow_update_flow(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QScrollArea

    page = AboutPage(update_check_service=FakeUpdateService())
    page.resize(420, 720)
    page.show()
    long_status = "检查失败，" + "网络连接异常" * 30
    page._set_update_status(long_status, "error")
    qapp.processEvents()

    assert page.update_status_label.text() == long_status
    assert page.update_status_label.width() <= page.update_section.width()
    assert page.update_status_label.height() > 2 * page.update_status_label.fontMetrics().height()
    assert page.check_update_button.y() >= (
        page.update_status_label.y() + page.update_status_label.height()
    )
    scroll = page.findChild(QScrollArea)
    assert scroll is not None
    assert scroll.horizontalScrollBar().maximum() == 0
    page.close()


def test_about_update_row_wraps_at_narrow_width_without_clipping(qapp: QApplication) -> None:
    from PySide6.QtCore import QPoint, QRect
    from PySide6.QtWidgets import QScrollArea

    page = AboutPage(update_check_service=FakeUpdateService())
    page.resize(420, 720)
    page.show()
    page._render_update_result(
        _result(
            UpdateCheckStatus.UPDATE_AVAILABLE,
            latest_version="9.9.9-very-long-fixture-version",
            release_url="https://github.com/TIMEpings/dlsite-organizer/releases/tag/v9.9.9",
        )
    )
    qapp.processEvents()

    update_layout = page.update_section.layout()
    assert update_layout is not None
    flow_item = update_layout.itemAt(1)
    assert flow_item is not None
    flow = flow_item.layout()
    assert flow is not None
    flow.setGeometry(QRect(0, 0, 180, 200))

    actions = [page.release_page_button, page.check_update_button]
    assert page.release_page_button.isVisible()
    assert page.release_page_button.y() < page.check_update_button.y()
    action_rects = [
        button.rect().translated(button.mapTo(page.update_section, QPoint(0, 0)))
        for button in actions
    ]
    assert not action_rects[0].intersects(action_rects[1])
    scroll = page.findChild(QScrollArea)
    assert scroll is not None
    assert scroll.horizontalScrollBar().maximum() == 0
    assert all(button.geometry().right() < page.update_section.width() for button in actions)
    page.close()


def test_about_mouse_update_focus_stays_with_update_section(
    qapp: QApplication,
) -> None:
    from PySide6.QtCore import QEvent, QObject, Qt
    from PySide6.QtTest import QTest

    service = FakeUpdateService(
        _result(UpdateCheckStatus.UP_TO_DATE),
        _result(UpdateCheckStatus.CHECK_FAILED),
        block=True,
    )
    page = AboutPage(update_check_service=service)
    page.resize(960, 640)
    page.show()
    qapp.processEvents()
    focus_events: list[tuple[str, str]] = []

    class FocusRecorder(QObject):
        def eventFilter(self, watched, event) -> bool:
            if event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
                focus_events.append((watched.objectName(), event.reason().name))
            return False

    recorder = FocusRecorder()
    for widget in (page.check_update_button, page.update_section, page.license_button):
        widget.installEventFilter(recorder)

    try:
        QTest.mouseClick(page.check_update_button, Qt.MouseButton.LeftButton)
        assert service.started.wait(1)
        assert page.is_busy()
        assert not page.check_update_button.isEnabled()
        assert qapp.focusWidget() is page.update_section
        assert qapp.focusWidget() is not page.license_button
        page.check_update_button.click()
        assert service.calls == 1
        service.release.set()
        _wait_until(qapp, lambda: not page.is_busy())
        assert qapp.focusWidget() is page.update_section

        QTest.mouseClick(page.check_update_button, Qt.MouseButton.LeftButton)
        _wait_until(qapp, lambda: service.calls == 2)
        assert service.calls == 2
        _wait_until(qapp, lambda: not page.is_busy())
        assert page.check_update_button.text() == "重新检查"
        assert qapp.focusWidget() is page.update_section
        assert page.license_button not in [widget for widget, _reason in focus_events]

        page.check_update_button.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(page.check_update_button, Qt.Key.Key_Tab)
        assert qapp.focusWidget() is page.license_button
        assert page.license_button.focusPolicy() == Qt.FocusPolicy.StrongFocus
        assert ("licenseButton", "TabFocusReason") in focus_events
    finally:
        service.release.set()
        _wait_until(qapp, lambda: not page.is_busy())
        page.close()


@pytest.mark.parametrize("key", [Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_about_keyboard_update_focus_returns_to_action(qapp: QApplication, key: Qt.Key) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    service = FakeUpdateService(_result(UpdateCheckStatus.CHECK_FAILED))
    page = AboutPage(update_check_service=service)
    page.resize(960, 640)
    page.show()
    page.activateWindow()
    qapp.processEvents()
    page.check_update_button.setFocus(Qt.FocusReason.TabFocusReason)
    QTest.keyClick(page.check_update_button, key)
    assert service.started.wait(1)
    assert page.is_busy()
    assert qapp.focusWidget() is page.update_section
    _wait_until(qapp, lambda: not page.is_busy())
    assert qapp.focusWidget() is page.check_update_button
    page.close()


@pytest.mark.parametrize("key", [Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_about_keyboard_update_preserves_deliberate_license_focus(
    qapp: QApplication, key: Qt.Key,
) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    service = FakeUpdateService(_result(UpdateCheckStatus.CHECK_FAILED), block=True)
    page = AboutPage(update_check_service=service)
    page.resize(960, 640)
    page.show()
    qapp.processEvents()
    page.check_update_button.setFocus(Qt.FocusReason.TabFocusReason)
    assert qapp.focusWidget() is page.check_update_button

    try:
        QTest.keyClick(page.check_update_button, key)
        assert service.started.wait(1)
        assert page.is_busy()
        assert qapp.focusWidget() is page.update_section

        page.license_button.setFocus(Qt.FocusReason.TabFocusReason)
        qapp.processEvents()
        assert qapp.focusWidget() is page.license_button

        service.release.set()
        _wait_until(qapp, lambda: not page.is_busy())
        assert qapp.focusWidget() is page.license_button
        assert page.check_update_button.isEnabled()
        assert page.update_status_label.text() == "检查失败，请稍后重试"
        assert service.calls == 1
    finally:
        service.release.set()
        _wait_until(qapp, lambda: not page.is_busy())
        page.close()


def test_about_programmatic_update_does_not_restore_keyboard_focus(qapp: QApplication) -> None:
    service = FakeUpdateService(_result(UpdateCheckStatus.CHECK_FAILED), block=True)
    page = AboutPage(update_check_service=service)
    page.resize(960, 640)
    page.show()
    qapp.processEvents()
    page.check_update_button.setFocus(Qt.FocusReason.TabFocusReason)
    assert qapp.focusWidget() is page.check_update_button

    try:
        page.check_update_button.click()
        assert service.started.wait(1)
        assert page.is_busy()
        assert qapp.focusWidget() is page.update_section
        service.release.set()
        _wait_until(qapp, lambda: not page.is_busy())
        assert qapp.focusWidget() is page.update_section
    finally:
        service.release.set()
        _wait_until(qapp, lambda: not page.is_busy())
        page.close()


@pytest.mark.parametrize("key", [Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_about_keyboard_retry_respects_new_focus_choice(qapp: QApplication, key: Qt.Key) -> None:
    from PySide6.QtTest import QTest

    service = FakeUpdateService(
        _result(UpdateCheckStatus.CHECK_FAILED),
        _result(UpdateCheckStatus.CHECK_FAILED),
        block=True,
    )
    page = AboutPage(update_check_service=service)
    page.resize(960, 640)
    page.show()
    qapp.processEvents()
    page.check_update_button.setFocus(Qt.FocusReason.TabFocusReason)

    try:
        QTest.keyClick(page.check_update_button, key)
        assert service.started.wait(1)
        service.release.set()
        _wait_until(qapp, lambda: not page.is_busy())
        assert qapp.focusWidget() is page.check_update_button

        service.release.clear()
        page.check_update_button.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(page.check_update_button, key)
        _wait_until(qapp, lambda: service.calls == 2)
        assert page.is_busy()
        assert qapp.focusWidget() is page.update_section
        page.license_button.setFocus(Qt.FocusReason.TabFocusReason)
        qapp.processEvents()
        assert qapp.focusWidget() is page.license_button

        service.release.set()
        _wait_until(qapp, lambda: not page.is_busy())
        assert qapp.focusWidget() is page.license_button
        assert page.check_update_button.isEnabled()
        assert page.update_status_label.text() == "检查失败，请稍后重试"
        assert service.calls == 2
    finally:
        service.release.set()
        _wait_until(qapp, lambda: not page.is_busy())
        page.close()


@pytest.mark.parametrize("key", [Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_about_keyboard_update_does_not_restore_focus_after_navigation(
    qapp: QApplication, key: Qt.Key,
) -> None:
    from PySide6.QtTest import QTest
    from tests.services.test_lookup import FakeProvider

    from dlsite_organizer.services.cover import CoverService
    from dlsite_organizer.services.lookup import LookupService
    from dlsite_organizer.services.naming import NamingService
    from dlsite_organizer.ui.main_window import MainPage, MainWindow

    service = FakeUpdateService(_result(UpdateCheckStatus.CHECK_FAILED), block=True)
    window = MainWindow(
        LookupService(FakeProvider(), NamingService()),
        CoverService(),
        update_check_service=service,
    )
    window.resize(1100, 800)
    window.show()
    qapp.processEvents()
    window.navigate_to(MainPage.ABOUT)
    qapp.processEvents()
    page = window.about_page
    page.check_update_button.setFocus(Qt.FocusReason.TabFocusReason)
    assert qapp.focusWidget() is page.check_update_button

    try:
        QTest.keyClick(page.check_update_button, key)
        assert service.started.wait(1)
        assert page.is_busy()
        assert page.isVisible()

        window.navigate_to(MainPage.LOOKUP)
        assert window.pages.currentWidget() is window.lookup_page
        assert window.lookup_page.isVisible()
        assert not page.isVisible()
        window.settings_button.setFocus(Qt.FocusReason.OtherFocusReason)
        qapp.processEvents()
        assert qapp.focusWidget() is window.settings_button

        service.release.set()
        _wait_until(qapp, lambda: not page.is_busy())
        assert window.pages.currentWidget() is window.lookup_page
        assert window.lookup_page.isVisible()
        assert not page.isVisible()
        assert qapp.focusWidget() is window.settings_button
    finally:
        service.release.set()
        _wait_until(qapp, lambda: not page.is_busy())
        window.close()


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_about_return_activates_once_on_press_and_retry(qapp: QApplication, key: Qt.Key) -> None:
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtTest import QSignalSpy, QTest

    service = FakeUpdateService(
        _result(UpdateCheckStatus.CHECK_FAILED),
        _result(UpdateCheckStatus.CHECK_FAILED),
        _result(UpdateCheckStatus.UP_TO_DATE),
        block=True,
    )
    page = AboutPage(update_check_service=service)
    page.resize(960, 640)
    page.show()
    page.activateWindow()
    qapp.processEvents()
    button = page.check_update_button
    clicks = QSignalSpy(button.clicked)
    assert not button.isDefault()
    assert not button.autoDefault()
    try:
        for count in (1, 2):
            service.release.clear()
            button.setFocus(Qt.FocusReason.TabFocusReason)
            assert button.hasFocus() and button.isVisible() and button.isEnabled()
            if count == 2:
                assert button.text() == "重新检查"
            QTest.keyPress(button, key)
            assert clicks.count() == count
            assert service.started.wait(1)
            _wait_until(qapp, lambda expected=count: service.calls == expected)
            assert page.is_busy()
            assert not button.isEnabled()
            assert qapp.focusWidget() is page.update_section
            QTest.keyClick(button, key)
            button.click()
            page.start_update_check()
            assert clicks.count() == count
            assert service.calls == count

            service.release.set()
            _wait_until(qapp, lambda: not page.is_busy())
            assert qapp.focusWidget() is button
            assert page.update_status_label.text() == "检查失败，请稍后重试"
            # A held Return must not restart work after asynchronous re-enabling.
            repeat = QKeyEvent(
                QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier, "", True, 1,
            )
            QApplication.sendEvent(button, repeat)
            QTest.keyRelease(button, key)
            qapp.processEvents()
            assert clicks.count() == count
            assert service.calls == count
            assert not page.is_busy()

        # A later programmatic click must not inherit a rejected repeat's origin.
        service.release.clear()
        button.click()
        _wait_until(qapp, lambda: service.calls == 3)
        assert qapp.focusWidget() is page.update_section
        service.release.set()
        _wait_until(qapp, lambda: not page.is_busy())
        assert qapp.focusWidget() is page.update_section
        assert clicks.count() == service.calls == 3
    finally:
        service.release.set()
        _wait_until(qapp, lambda: not page.is_busy())
        page.close()


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter])
@pytest.mark.parametrize("state", ["disabled", "unfocused", "hidden", "auto_repeat"])
def test_about_return_rejects_ineligible_activation(
    qapp: QApplication, key: Qt.Key, state: str,
) -> None:
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtTest import QSignalSpy, QTest

    service = FakeUpdateService(_result(UpdateCheckStatus.CHECK_FAILED), block=True)
    page = AboutPage(update_check_service=service)
    page.resize(960, 640)
    page.show()
    page.activateWindow()
    qapp.processEvents()
    button = page.check_update_button
    button.setFocus(Qt.FocusReason.TabFocusReason)
    clicks = QSignalSpy(button.clicked)
    if state == "disabled":
        button.setEnabled(False)
    elif state == "unfocused":
        page.license_button.setFocus(Qt.FocusReason.TabFocusReason)
    elif state == "hidden":
        button.hide()
    try:
        event = QKeyEvent(
            QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier,
            "", state == "auto_repeat", 1,
        )
        QApplication.sendEvent(button, event)
        QTest.keyRelease(button, key)
        qapp.processEvents()
        assert clicks.count() == 0
        assert service.calls == 0
        assert not page.is_busy()
        assert button.consume_activation_source() == "other"
    finally:
        service.release.set()
        _wait_until(qapp, lambda: not page.is_busy())
        page.close()
