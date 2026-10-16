from __future__ import annotations

import os
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt, qInstallMessageHandler
from PySide6.QtGui import QFontDatabase, QFontInfo, QFontMetrics
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QSplitter
from tests.services.test_lookup import FakeProvider

from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.ui.main_window import MainPage, MainWindow
from dlsite_organizer.ui.theme.tokens import FONT_ROLES, FontRole, Geometry
from dlsite_organizer.ui.widgets.constrained_content import (
    ConstrainedContent,
    ContentAlignment,
)
from dlsite_organizer.ui.widgets.page_header import PageHeader


@pytest.fixture
def qapp() -> QApplication:
    application = QApplication.instance()
    if isinstance(application, QApplication):
        return application
    return QApplication([])


def _window() -> MainWindow:
    return MainWindow(LookupService(FakeProvider(), NamingService()), CoverService())


@pytest.fixture
def geometry_font_environment(qapp: QApplication):
    """Load installed Windows fonts only for this geometry characterization."""
    original_font = qapp.font()
    registered_ids: list[int] = []
    if qapp.platformName().casefold() == "offscreen":
        font_directory = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
        font_files = (
            (font_directory / "segoeui.ttf", "Segoe UI"),
            (font_directory / "segoeuib.ttf", "Segoe UI"),
            (font_directory / "msyh.ttc", "Microsoft YaHei"),
        )
        for path, family in font_files:
            assert path.is_file(), f"Required installed system font is unavailable: {path}"
            font_id = QFontDatabase.addApplicationFont(str(path))
            assert font_id >= 0, f"Qt could not register installed system font: {path}"
            registered_ids.append(font_id)
            assert family in QFontDatabase.applicationFontFamilies(font_id)

    families = set(QFontDatabase.families())
    assert "Segoe UI" in families, "Segoe UI must be available in the geometry font environment"
    assert "Microsoft YaHei" in families, (
        "Microsoft YaHei must be available in the geometry font environment"
    )

    try:
        yield
    finally:
        # MainWindow applies its theme to the shared QApplication. Restore the
        # pre-test font and registrations so later tests keep their own setup.
        qapp.setFont(original_font)
        for font_id in reversed(registered_ids):
            assert QFontDatabase.removeApplicationFont(font_id)


def test_sidebar_top_breathing_space_aligns_first_item_with_page_header(
    qapp: QApplication,
) -> None:
    window = _window()
    window.show()
    qapp.processEvents()

    try:
        navigation_layout = window.navigation.layout()
        assert navigation_layout is not None
        top_inset = navigation_layout.contentsMargins().top()
        assert 24 <= top_inset <= 32

        header = window.organizer_page.findChild(PageHeader, "pageHeader")
        assert header is not None
        first_item = window.navigation_list.item(0)
        assert first_item is not None
        item_center = window.navigation_list.visualItemRect(first_item).center()
        item_center_in_window = window.navigation_list.mapTo(window, item_center)
        header_center = header.mapTo(window, header.rect().center())
        assert abs(item_center_in_window.y() - header_center.y()) <= 12
    finally:
        window.close()


def test_navigation_items_keep_labels_icons_and_a_distinct_focus_route(
    qapp: QApplication,
) -> None:
    window = _window()
    window.show()
    qapp.processEvents()

    try:
        assert [
            window.navigation_list.item(index).text()
            for index in range(window.navigation_list.count())
        ] == ["整理", "查询", "重命名历史"]
        assert all(
            not window.navigation_list.item(index).icon().isNull()
            for index in range(window.navigation_list.count())
        )
        assert window.lookup_page.query_button.property("visualRole") == "primary"
        assert window.settings_page.save_button.property("visualRole") == "primary"
        assert window.settings_page.reset_button.property("visualRole") == "quiet"
        assert window.settings_page.remove_explorer_button.property("visualRole") == "danger"
        assert window.about_page.check_update_button.property("visualRole") == "secondary"
        about_names = [
            label.text()
            for label in window.about_page.findChildren(QLabel)
        ]
        assert about_names.count("DLsite Organizer") == 1

        window.show_about_page()
        window.settings_button.setFocus(Qt.FocusReason.TabFocusReason)
        qapp.processEvents()
        assert window.current_page.value == "about"
        assert window.about_button.isChecked()
        assert not window.settings_button.isChecked()
        assert window.settings_button.hasFocus()
    finally:
        window.close()


def test_page_header_uses_compact_centralized_typography(qapp: QApplication) -> None:
    header = PageHeader("标题", "说明")
    assert QApplication.instance() is qapp
    title_spec = FONT_ROLES[FontRole.PAGE_TITLE]
    description_spec = FONT_ROLES[FontRole.PAGE_DESCRIPTION]

    assert header.minimumHeight() <= 56
    assert header.title_label.font().pointSizeF() == title_spec.point_size
    assert header.title_label.font().weight() == title_spec.weight
    assert header.description_label is not None
    assert header.description_label.font().pointSizeF() == description_spec.point_size
    header.close()


def test_page_content_alignment_policy_and_page_header_axes(
    qapp: QApplication, geometry_font_environment,
) -> None:
    expected = {
        MainPage.LOOKUP: (
            1120,
            ContentAlignment.LEFT_BOUNDED_WORKSPACE,
        ),
        MainPage.SETTINGS: (
            1080,
            ContentAlignment.LEFT_BOUNDED_WORKSPACE,
        ),
        MainPage.ABOUT: (
            880,
            ContentAlignment.CENTERED_INFORMATIONAL,
        ),
    }

    window = _window()
    window.show()
    qapp.processEvents()

    try:
        for width in (1024, 1160, 1460, 1600):
            window.resize(width, 1000)
            qapp.processEvents()
            for route, (max_width, alignment) in expected.items():
                window.navigate_to(route)
                qapp.processEvents()
                page = window._pages_by_identity[route]
                container = page.findChild(ConstrainedContent, "constrainedContent")
                assert container is not None
                assert container.maximumWidth() == max_width
                assert container.alignment is alignment
                assert container.width() <= page.width()

                scroll = page.findChild(QScrollArea)
                assert scroll is not None
                viewport = scroll.viewport()
                assert container.width() <= viewport.width()
                assert scroll.horizontalScrollBar().maximum() == 0
                content_origin = container.mapTo(viewport, QPoint(0, 0))
                if alignment is ContentAlignment.LEFT_BOUNDED_WORKSPACE:
                    assert abs(content_origin.x()) <= 1
                    if width >= 1460:
                        assert container.width() < viewport.width()
                    header = page.findChild(PageHeader, "pageHeader")
                    assert header is not None
                    header_left = header.mapTo(page, QPoint(0, 0)).x()
                    body = (
                        window.lookup_page.code_input
                        if route is MainPage.LOOKUP
                        else window.settings_page.quick_access_box
                    )
                    body_left = body.mapTo(page, QPoint(0, 0)).x()
                    assert abs(header_left - body_left) <= 2
                    if route is MainPage.SETTINGS:
                        resolved_font = QFontInfo(qapp.font())
                        metrics = QFontMetrics(qapp.font())
                        assert resolved_font.family() == "Segoe UI"
                        assert metrics.height() > 0
                        assert container.minimumSizeHint().width() <= max_width
                        if width >= 1460:
                            assert container.width() == max_width
                else:
                    assert abs(
                        2 * content_origin.x() + container.width() - viewport.width()
                    ) <= 1
                    if width >= 1460:
                        assert container.width() == max_width

        assert window.organizer_page.findChild(ConstrainedContent) is None
        assert window.rename_history_page.findChild(ConstrainedContent) is None
        assert Geometry.SIDEBAR_WIDTH == 184
        window.navigate_to(MainPage.ORGANIZER)
        assert window.organizer_page.table.width() > 650
        window.navigate_to(MainPage.HISTORY)
        splitter = window.rename_history_page.findChild(QSplitter, "renameHistorySplitter")
        assert splitter is not None and splitter.width() > 650
    finally:
        window.close()


def test_constrained_content_alignment_layout_is_safe_in_isolation(
    qapp: QApplication,
) -> None:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    left_content = ConstrainedContent(
        360,
        alignment=ContentAlignment.LEFT_BOUNDED_WORKSPACE,
    )
    centered_content = ConstrainedContent(
        360,
        alignment=ContentAlignment.CENTERED_INFORMATIONAL,
    )
    assert left_content.content_layout is not None
    assert centered_content.content_layout is not None

    scroll.resize(600, 320)
    scroll.setAlignment(left_content.alignment.value | Qt.AlignmentFlag.AlignTop)
    scroll.setWidget(left_content)
    scroll.show()
    qapp.processEvents()
    assert left_content.width() == 360
    assert left_content.mapTo(scroll.viewport(), QPoint(0, 0)).x() == 0

    scroll.takeWidget()
    scroll.setAlignment(centered_content.alignment.value | Qt.AlignmentFlag.AlignTop)
    scroll.setWidget(centered_content)
    qapp.processEvents()
    centered_origin = centered_content.mapTo(scroll.viewport(), QPoint(0, 0)).x()
    assert abs(2 * centered_origin + centered_content.width() - scroll.viewport().width()) <= 1
    scroll.close()


def test_theme_styles_scrollbars_group_boxes_and_button_roles(qapp: QApplication) -> None:
    from dlsite_organizer.ui.theme.stylesheet import light_stylesheet

    stylesheet = light_stylesheet()
    assert "QScrollBar::add-line" in stylesheet
    assert "QScrollBar::handle:vertical:hover" in stylesheet
    assert 'QPushButton[visualRole="primary"]' in stylesheet
    assert 'QPushButton[visualRole="secondary"]' in stylesheet
    assert 'QPushButton[visualRole="quiet"]' in stylesheet
    assert 'QPushButton[visualRole="danger"]' in stylesheet
    assert "QGroupBox::title" in stylesheet
    assert "outline: 0" not in stylesheet.lower()


def test_shared_buttons_render_one_visible_keyboard_focus_border(qapp: QApplication) -> None:
    from PySide6.QtGui import QColor
    from PySide6.QtTest import QTest

    from dlsite_organizer.ui.theme.palette import apply_light_theme
    from dlsite_organizer.ui.theme.tokens import LIGHT_COLORS

    window = _window()
    shared_style = qapp.style()
    apply_light_theme(qapp)
    assert qapp.style() is shared_style
    window.resize(1160, 800)
    window.show()
    qapp.processEvents()
    window.show_about_page()
    page = window.about_page

    def assert_single_focus_change(button, enter_focus) -> None:
        before = button.grab().toImage()
        enter_focus()
        qapp.processEvents()
        assert button.hasFocus()
        after = button.grab().toImage()
        focus_color = QColor(LIGHT_COLORS.focus)
        assert any(
            after.pixelColor(x, y) == focus_color
            for y in range(after.height())
            for x in range(after.width())
        ), button.objectName()
        inner_changes = [
            (x, y)
            for y in range(3, after.height() - 3)
            for x in range(3, after.width() - 3)
            if before.pixelColor(x, y) != after.pixelColor(x, y)
        ]
        assert not inner_changes, (button.objectName(), inner_changes[:8])

    try:
        check = page.check_update_button
        license_button = page.license_button
        check.setFocus(Qt.FocusReason.OtherFocusReason)
        qapp.processEvents()
        assert_single_focus_change(
            license_button,
            lambda: QTest.keyClick(check, Qt.Key.Key_Tab),
        )

        assert_single_focus_change(
            check,
            lambda: QTest.keyClick(license_button, Qt.Key.Key_Backtab),
        )
        check.setEnabled(False)
        qapp.processEvents()
        assert not check.hasFocus()
        check.setEnabled(True)
        from dlsite_organizer.services.update_checker import UpdateCheckResult, UpdateCheckStatus

        page._render_update_result(
            UpdateCheckResult(
                UpdateCheckStatus.UPDATE_AVAILABLE,
                "1.3.1",
                "9.9.9",
                "https://github.com/TIMEpings/dlsite-organizer/releases/tag/v9.9.9",
            )
        )
        qapp.processEvents()
        release_button = page.release_page_button

        def tab_to_release() -> None:
            release_button.setFocus(Qt.FocusReason.OtherFocusReason)
            QTest.keyClick(release_button, Qt.Key.Key_Tab)
            next_widget = qapp.focusWidget()
            assert next_widget is not None
            QTest.keyClick(next_widget, Qt.Key.Key_Backtab)

        assert_single_focus_change(release_button, tab_to_release)

        window.show_settings_page()
        save = window.settings_page.save_button

        def tab_to_save() -> None:
            save.setFocus(Qt.FocusReason.OtherFocusReason)
            QTest.keyClick(save, Qt.Key.Key_Tab)
            next_widget = qapp.focusWidget()
            assert next_widget is not None
            QTest.keyClick(next_widget, Qt.Key.Key_Backtab)

        assert_single_focus_change(save, tab_to_save)

        sidebar = window.about_button

        def tab_to_sidebar() -> None:
            sidebar.setFocus(Qt.FocusReason.OtherFocusReason)
            QTest.keyClick(sidebar, Qt.Key.Key_Tab)
            next_widget = qapp.focusWidget()
            assert next_widget is not None
            QTest.keyClick(next_widget, Qt.Key.Key_Backtab)

        assert_single_focus_change(sidebar, tab_to_sidebar)

        window.navigate_to(MainPage.HISTORY)
        history = window.rename_history_page
        # Synthetic presentation only: expose deterministic empty details without
        # opening a journal or observing the filesystem.
        history.empty_state.hide()
        history.history_detail.show()
        history.operation_detail_section.show()
        history.technical_section.show()
        for button in (
            history.observe_button,
            history.copy_root_path_button,
            history.copy_source_path_button,
            history.copy_target_path_button,
            history.copy_transaction_id_button,
        ):
            button.setEnabled(True)
            history.detail_scroll.ensureWidgetVisible(button)
            button.setFocus(Qt.FocusReason.OtherFocusReason)
            QTest.keyClick(button, Qt.Key.Key_Tab)
            next_widget = qapp.focusWidget()
            assert next_widget is not None and next_widget is not button
            qapp.processEvents()
            assert_single_focus_change(
                button,
                lambda next_widget=next_widget: QTest.keyClick(next_widget, Qt.Key.Key_Backtab),
            )
            # Verify keyboard activation independently of observation/copy slots.
            from PySide6.QtTest import QSignalSpy

            button.clicked.disconnect()
            clicked = QSignalSpy(button.clicked)
            QTest.keyClick(button, Qt.Key.Key_Space)
            assert clicked.count() == 1

        window.navigate_to(MainPage.ORGANIZER)
        organizer = window.organizer_page
        organizer.detail_panel.show()
        organizer.detail_content.show()
        for display in (organizer.detail_source_path, organizer.detail_target_path):
            display.set_path("C:/synthetic/focus-test")
            button = display.copy_button
            assert button is not None
            assert button.property("customFocusBorder") is None
            display.path_input.setFocus(Qt.FocusReason.OtherFocusReason)
            qapp.processEvents()
            before = button.grab().toImage()
            QTest.keyClick(display.path_input, Qt.Key.Key_Tab)
            qapp.processEvents()
            assert button.hasFocus()
            after = button.grab().toImage()
            assert before != after, "Organizer copy must retain native keyboard focus"
            assert not any(
                after.pixelColor(x, y) == QColor(LIGHT_COLORS.focus)
                for y in range(after.height())
                for x in range(after.width())
            ), "Native copy focus must not acquire an extra blue border"
            button.clicked.disconnect()
            clicked = QSignalSpy(button.clicked)
            QTest.keyClick(button, Qt.Key.Key_Space)
            assert clicked.count() == 1

        from typing import cast

        from dlsite_organizer.app.settings import AppSettings, SettingsService
        from dlsite_organizer.services.quick_rename import QuickRenameService
        from dlsite_organizer.services.undo_service import UndoService
        from dlsite_organizer.ui.lightweight_window import LightweightWindow

        class EmptyUndoService:
            def unresolved_transaction(self):
                return None

            def latest_transaction(self):
                return None

        lightweight = LightweightWindow(
            cast(QuickRenameService, object()),
            cast(UndoService, EmptyUndoService()),
            SettingsService(AppSettings()),
        )
        lightweight.show()
        qapp.processEvents()
        icon_button = lightweight.settings_button

        def tab_to_icon_button() -> None:
            icon_button.setFocus(Qt.FocusReason.OtherFocusReason)
            QTest.keyClick(icon_button, Qt.Key.Key_Tab)
            next_widget = qapp.focusWidget()
            assert next_widget is not None
            QTest.keyClick(next_widget, Qt.Key.Key_Backtab)

        assert_single_focus_change(icon_button, tab_to_icon_button)
        lightweight.close()
    finally:
        window.close()


def test_every_shell_opt_in_renders_a_single_custom_focus_border(qapp: QApplication) -> None:
    from PySide6.QtGui import QColor
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QPushButton, QWidget

    from dlsite_organizer.ui.theme.tokens import LIGHT_COLORS

    window = _window()
    window.show()
    try:
        for route, page in window._pages_by_identity.items():
            window.navigate_to(route)
            for button in page.findChildren(QPushButton):
                if button.property("customFocusBorder") is not True:
                    continue
                # Reveal hidden/disabled presentation states without calling
                # services, file operations, workers, or action handlers.
                parent: QWidget | None = button
                while parent is not None and parent is not page:
                    parent.show()
                    parent.setEnabled(True)
                    parent = parent.parentWidget()
                button.setEnabled(True)
                button.setFocus(Qt.FocusReason.OtherFocusReason)
                qapp.processEvents()
                QTest.keyClick(button, Qt.Key.Key_Backtab)
                other = qapp.focusWidget()
                assert other is not None and other is not button
                qapp.processEvents()
                normal = button.grab().toImage()
                QTest.keyClick(other, Qt.Key.Key_Tab)
                qapp.processEvents()
                assert button.hasFocus(), (route, button.text())
                focused = button.grab().toImage()
                assert any(
                    focused.pixelColor(x, y) == QColor(LIGHT_COLORS.focus)
                    for y in range(focused.height())
                    for x in range(focused.width())
                ), (route, button.text())
                assert all(
                    normal.pixelColor(x, y) == focused.pixelColor(x, y)
                    for y in range(3, focused.height() - 3)
                    for x in range(3, focused.width() - 3)
                ), (route, button.text(), "duplicate inner focus frame")
    finally:
        window.close()


def test_native_focus_is_default_and_opt_in_is_read_at_paint_time(qapp: QApplication) -> None:
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QPushButton, QStyle, QStyleOptionFocusRect

    from dlsite_organizer.ui.theme.palette import apply_light_theme, use_custom_focus_border

    apply_light_theme(qapp)
    style = qapp.style()
    for _ in range(3):
        apply_light_theme(qapp)
        assert qapp.style() is style
    button = QPushButton("Unmarked native action")
    button.ensurePolished()
    option = QStyleOptionFocusRect()
    option.initFrom(button)
    option.rect = button.rect().adjusted(3, 3, -3, -3)
    option.state |= QStyle.StateFlag.State_HasFocus | QStyle.StateFlag.State_KeyboardFocusChange

    def paint():
        image = QImage(button.size(), QImage.Format.Format_ARGB32)
        image.fill(QColor("white"))
        painter = QPainter(image)
        style.drawPrimitive(QStyle.PrimitiveElement.PE_FrameFocusRect, option, painter, button)
        painter.end()
        return image

    try:
        native = paint()
        use_custom_focus_border(button)
        suppressed = paint()
        assert native != suppressed
        assert all(
            suppressed.pixelColor(x, y) == QColor("white")
            for y in range(suppressed.height())
            for x in range(suppressed.width())
        )
        button.setProperty("customFocusBorder", False)
        assert paint() == native  # No stylesheet cache/repolish dependency.
    finally:
        button.close()


def test_shared_stylesheet_applies_without_qt_style_warnings(qapp: QApplication) -> None:
    from PySide6.QtWidgets import QGroupBox, QLineEdit, QPushButton, QVBoxLayout, QWidget

    from dlsite_organizer.ui.theme.stylesheet import light_stylesheet

    messages: list[str] = []
    previous_handler = qInstallMessageHandler(
        lambda _message_type, _context, message: messages.append(message)
    )
    root = QWidget()
    layout = QVBoxLayout(root)
    group = QGroupBox("Section")
    group_layout = QVBoxLayout(group)
    group_layout.addWidget(QLineEdit())
    group_layout.addWidget(QPushButton("Action"))
    layout.addWidget(group)
    try:
        root.setStyleSheet(light_stylesheet())
        root.show()
        qapp.processEvents()
    finally:
        root.close()
        qInstallMessageHandler(previous_handler)

    assert not [message for message in messages if "stylesheet" in message.casefold()]
