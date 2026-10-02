from __future__ import annotations

import pytest
from PySide6.QtCore import Qt, qInstallMessageHandler
from PySide6.QtWidgets import QApplication, QLabel, QSplitter
from tests.services.test_lookup import FakeProvider

from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.lookup import LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.ui.main_window import MainPage, MainWindow
from dlsite_organizer.ui.theme.tokens import FONT_ROLES, FontRole, Geometry
from dlsite_organizer.ui.widgets.page_header import PageHeader


@pytest.fixture
def qapp() -> QApplication:
    application = QApplication.instance()
    if isinstance(application, QApplication):
        return application
    return QApplication([])


def _window() -> MainWindow:
    return MainWindow(LookupService(FakeProvider(), NamingService()), CoverService())


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
        assert window.about_page.check_update_button.property("visualRole") == "primary"
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


def test_form_and_read_pages_share_bounded_content_while_data_pages_remain_flexible(
    qapp: QApplication,
) -> None:
    from dlsite_organizer.ui.widgets.constrained_content import ConstrainedContent

    window = _window()
    window.show()
    qapp.processEvents()

    try:
        bounded = {
            MainPage.LOOKUP: window.lookup_page,
            MainPage.SETTINGS: window.settings_page,
            MainPage.ABOUT: window.about_page,
        }
        for width in (1160, 1460, 1700):
            window.resize(width, 1000)
            qapp.processEvents()
            for route, page in bounded.items():
                window.navigate_to(route)
                qapp.processEvents()
                container = page.findChild(ConstrainedContent, "constrainedContent")
                assert container is not None
                assert 800 <= container.maximumWidth() <= 1200
                assert container.width() <= page.width()
                if width >= 1460:
                    assert page.width() > container.width()

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
