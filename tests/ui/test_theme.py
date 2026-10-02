import pytest
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from dlsite_organizer.ui.theme.palette import create_light_palette
from dlsite_organizer.ui.theme.stylesheet import light_stylesheet
from dlsite_organizer.ui.theme.tokens import (
    FONT_ROLES,
    LIGHT_COLORS,
    FontRole,
    Geometry,
    Shape,
    Spacing,
)


@pytest.fixture
def qapp() -> QApplication:
    application = QApplication.instance()
    if isinstance(application, QApplication):
        return application
    return QApplication([])


def test_theme_tokens_cover_the_shared_shell_roles() -> None:
    assert (Spacing.XS, Spacing.SM, Spacing.MD, Spacing.LG, Spacing.XL, Spacing.XXL) == (
        4,
        8,
        12,
        16,
        24,
        32,
    )
    assert Shape.CONTROL_RADIUS == 7
    assert Shape.SOFT_RADIUS == 8
    assert Shape.PANEL_RADIUS == 10
    assert Shape.SCROLLBAR_RADIUS == 4
    assert Geometry.CONTROL_HEIGHT == 34
    assert Geometry.NAVIGATION_ITEM_HEIGHT == 40
    assert Geometry.SIDEBAR_WIDTH == 184
    assert Geometry.PAGE_HEADER_HEIGHT == 52
    assert set(FONT_ROLES) == set(FontRole)
    assert {
        "background",
        "surface",
        "surface_secondary",
        "border",
        "border_strong",
        "text_primary",
        "text_secondary",
        "text_muted",
        "accent",
        "accent_hover",
        "accent_pressed",
        "selection",
        "focus",
        "success",
        "warning",
        "error",
        "blocked",
        "stale",
    } <= LIGHT_COLORS.__dataclass_fields__.keys()


def test_light_palette_sets_selection_disabled_and_surface_roles() -> None:
    palette = create_light_palette()

    assert palette.color(QPalette.ColorRole.Window).name().upper() == LIGHT_COLORS.background
    assert palette.color(QPalette.ColorRole.Base).name().upper() == LIGHT_COLORS.surface
    assert palette.color(QPalette.ColorRole.Highlight).name().upper() == LIGHT_COLORS.selection
    assert (
        palette.color(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text).name().upper()
        == LIGHT_COLORS.text_muted
    )


def test_shared_styles_keep_keyboard_focus_visible() -> None:
    stylesheet = light_stylesheet().lower()

    assert "qpushbutton:focus" in stylesheet
    assert "qlineedit:focus" in stylesheet
    assert "#navigationlist::item:hover" in stylesheet
    assert "outline: 0" not in stylesheet


def test_light_theme_can_be_applied_to_an_offscreen_application(qapp: QApplication) -> None:
    from dlsite_organizer.ui.theme.palette import apply_light_theme

    apply_light_theme(qapp)

    assert qapp.palette().color(QPalette.ColorRole.Window).name().upper() == LIGHT_COLORS.background
    assert qapp.font().pointSizeF() == 10
