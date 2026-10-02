"""Limited shared QSS for the shell and named legacy page roles."""

from __future__ import annotations

from PySide6.QtWidgets import QPushButton

from dlsite_organizer.ui.theme.tokens import (
    FONT_ROLES,
    LIGHT_COLORS,
    ButtonRole,
    FontRole,
    Geometry,
    Shape,
    Spacing,
)


def set_button_role(button: QPushButton, role: ButtonRole) -> None:
    """Set a presentation role without affecting enabled state or eligibility."""
    button.setProperty("visualRole", role.value)
    style = button.style()
    style.unpolish(button)
    style.polish(button)


def light_stylesheet() -> str:
    """Return shared shell and control styles while leaving native behavior intact."""
    colors = LIGHT_COLORS
    page_title = FONT_ROLES[FontRole.PAGE_TITLE]
    page_description = FONT_ROLES[FontRole.PAGE_DESCRIPTION]
    section_title = FONT_ROLES[FontRole.SECTION_TITLE]

    return f"""
QMainWindow, QStackedWidget {{ background: {colors.background}; }}
#navigation {{
  background: {colors.sidebar};
  border-right: 1px solid {colors.border};
}}
#windowTitle {{ color: {colors.text_secondary}; font-size: 10pt; font-weight: 400; }}
#systemMenuIcon {{ background: transparent; border: 0; padding: 0; }}
#systemMenuIcon:hover {{ background: {colors.surface_secondary}; }}
#navigationList {{ background: transparent; border: 0; }}
#navigationList::item {{
  min-height: {Geometry.NAVIGATION_ITEM_HEIGHT}px;
  border-radius: {Shape.SOFT_RADIUS}px;
  padding: 0 {Spacing.MD}px;
  color: {colors.text_secondary};
}}
#navigationList::item:hover {{ background: {colors.sidebar_hover}; color: {colors.text_primary}; }}
#navigationList::item:selected {{
  background: {colors.selection};
  border-left: 2px solid {colors.accent};
  padding-left: {Spacing.MD - 2}px;
  color: {colors.accent};
  font-weight: 600;
}}
#settingsButton, #aboutButton, #modeButton {{
  min-height: {Geometry.CONTROL_HEIGHT}px;
  border-radius: {Shape.CONTROL_RADIUS}px;
  padding: 5px {Spacing.MD}px;
  text-align: left;
}}
#settingsButton, #aboutButton {{
  color: {colors.text_secondary};
  background: transparent;
  border: 1px solid transparent;
}}
#settingsButton:hover, #aboutButton:hover {{
  color: {colors.text_primary};
  background: {colors.sidebar_hover};
}}
#settingsButton:checked, #aboutButton:checked {{
  color: {colors.accent};
  background: {colors.selection};
  font-weight: 600;
}}
#settingsButton:focus, #aboutButton:focus, #modeButton:focus,
QPushButton:focus, QLineEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
  border: 1px solid {colors.focus};
}}
#modeButton {{
  color: {colors.text_secondary};
  background: {colors.surface};
  border: 1px solid {colors.border};
}}
#modeButton:hover {{ color: {colors.accent}; background: {colors.selection}; }}
#modeButton:pressed {{ background: {colors.surface_secondary}; }}
#pageTitle {{
  color: {colors.text_primary};
  font-size: {page_title.point_size}pt;
  font-weight: {page_title.weight};
}}
#pageDescription {{
  color: {colors.text_secondary};
  font-size: {page_description.point_size}pt;
  font-weight: {page_description.weight};
}}
#sectionLabel {{
  color: {colors.text_primary};
  font-size: {section_title.point_size}pt;
  font-weight: {section_title.weight};
}}
#resultCard, #relationCard {{
  background: {colors.surface};
  border: 1px solid {colors.border};
  border-radius: {Shape.PANEL_RADIUS}px;
}}
#coverPlaceholder {{
  background: {colors.surface_secondary};
  border: 1px solid {colors.border};
  border-radius: {Shape.CONTROL_RADIUS}px;
  color: {colors.text_muted};
}}
QGroupBox {{
  margin-top: {Spacing.MD}px;
  border: 1px solid {colors.border};
  border-radius: {Shape.SOFT_RADIUS}px;
  padding: {Spacing.SM}px {Spacing.MD}px {Spacing.SM}px {Spacing.MD}px;
  color: {colors.text_primary};
}}
QGroupBox::title {{
  subcontrol-origin: margin;
  subcontrol-position: top left;
  left: {Spacing.SM}px;
  padding: 0 {Spacing.XS}px;
  color: {colors.text_secondary};
  background: {colors.background};
  font-weight: 600;
}}
#quickAccessBox {{ border: 0; padding: {Spacing.XS}px 0 0 0; }}
#quickAccessBox::title {{ left: 0; padding: 0 {Spacing.XS}px; color: {colors.text_primary}; }}
QScrollArea {{ border: 0; background: transparent; }}
QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
  background: {colors.surface};
  border: 1px solid {colors.border_strong};
  border-radius: {Shape.CONTROL_RADIUS}px;
  padding: 5px 8px;
  min-height: {Geometry.CONTROL_HEIGHT}px;
  selection-background-color: {colors.accent};
  selection-color: {colors.surface};
}}
QLineEdit:read-only {{ background: {colors.surface_secondary}; }}
QPushButton {{
  min-height: {Geometry.CONTROL_HEIGHT}px;
  background: {colors.surface};
  border: 1px solid {colors.border};
  border-radius: {Shape.CONTROL_RADIUS}px;
  padding: 5px {Spacing.MD}px;
}}
QPushButton:hover {{ background: {colors.surface_secondary}; }}
QPushButton:pressed {{ background: {colors.selection}; }}
QPushButton:disabled {{ color: {colors.text_muted}; background: {colors.surface_secondary}; }}
QPushButton[visualRole="primary"], #primaryButton {{
  background: {colors.accent};
  color: {colors.surface};
  border: 1px solid {colors.accent};
  font-weight: 600;
}}
QPushButton[visualRole="primary"]:hover, #primaryButton:hover {{
  background: {colors.accent_hover};
  border-color: {colors.accent_hover};
}}
QPushButton[visualRole="primary"]:pressed, #primaryButton:pressed {{
  background: {colors.accent_pressed};
  border-color: {colors.accent_pressed};
}}
QPushButton[visualRole="primary"]:focus, #primaryButton:focus {{
  border: 1px solid {colors.focus};
}}
QPushButton[visualRole="primary"]:disabled, #primaryButton:disabled {{
  background: {colors.border_strong};
  color: {colors.surface};
}}
QPushButton[visualRole="secondary"] {{
  background: {colors.surface};
  color: {colors.text_primary};
  border: 1px solid {colors.border};
}}
QPushButton[visualRole="quiet"] {{
  background: transparent;
  color: {colors.text_secondary};
  border-color: transparent;
}}
QPushButton[visualRole="quiet"]:hover {{
  background: {colors.sidebar_hover};
  color: {colors.text_primary};
}}
QPushButton[visualRole="danger"] {{
  background: {colors.surface};
  color: {colors.error};
  border: 1px solid {colors.border};
}}
QPushButton[visualRole="danger"]:hover {{
  background: {colors.surface_secondary};
  border-color: {colors.error};
}}
QPushButton[visualRole="danger"]:focus {{ border: 1px solid {colors.focus}; }}
QScrollBar:vertical {{
  width: 9px;
  margin: 0;
  border: 0;
  background: transparent;
}}
QScrollBar:horizontal {{
  height: 9px;
  margin: 0;
  border: 0;
  background: transparent;
}}
QScrollBar::handle:vertical {{
  min-height: 28px;
  margin: 0;
  border-radius: {Shape.SCROLLBAR_RADIUS}px;
  background: {colors.border_strong};
}}
QScrollBar::handle:horizontal {{
  min-width: 28px;
  margin: 0;
  border-radius: {Shape.SCROLLBAR_RADIUS}px;
  background: {colors.border_strong};
}}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{
  background: {colors.text_muted};
}}
QScrollBar::handle:vertical:pressed, QScrollBar::handle:horizontal:pressed {{
  background: {colors.accent};
}}
QScrollBar::add-line, QScrollBar::sub-line {{
  width: 0;
  height: 0;
  border: 0;
  background: transparent;
}}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
#statusLabel {{ color: {colors.text_secondary}; min-height: 22px; }}
#statusLabel[state="loading"] {{ color: {colors.accent}; }}
#statusLabel[state="success"] {{ color: {colors.success}; }}
#statusLabel[state="error"] {{ color: {colors.error}; }}
#statusLabel[state="blocked"] {{ color: {colors.blocked}; }}
#statusLabel[state="stale"] {{ color: {colors.stale}; }}
#dropZone {{ background: transparent; border: 0; }}
#dropZoneTitle {{ color: {colors.accent}; font-size: 12pt; font-weight: 600; }}
#dropZoneDescription {{ color: {colors.text_secondary}; }}
"""
