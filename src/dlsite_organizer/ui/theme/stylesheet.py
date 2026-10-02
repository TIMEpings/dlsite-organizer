"""Limited shared QSS for the shell and named legacy page roles."""

from dlsite_organizer.ui.theme.tokens import (
    FONT_ROLES,
    LIGHT_COLORS,
    FontRole,
    Geometry,
    Shape,
    Spacing,
)


def light_stylesheet() -> str:
    """Return the shell stylesheet; standard control behavior remains palette-driven."""
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
#brandLabel {{ color: {colors.text_primary}; font-size: 11pt; font-weight: 600; }}
#navigationList {{ background: transparent; border: 0; }}
#navigationList::item {{
  min-height: {Geometry.NAVIGATION_ITEM_HEIGHT}px;
  border-radius: {Shape.CONTROL_RADIUS}px;
  padding: 0 {Spacing.MD}px;
  color: {colors.text_secondary};
}}
#navigationList::item:hover {{ background: {colors.sidebar_hover}; color: {colors.text_primary}; }}
#navigationList::item:selected {{
  background: {colors.selection};
  border-left: 3px solid {colors.accent};
  padding-left: {Spacing.MD - 3}px;
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
QPushButton:focus, QLineEdit:focus {{ border: 1px solid {colors.focus}; }}
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
QLineEdit {{
  background: {colors.surface};
  border: 1px solid {colors.border_strong};
  border-radius: {Shape.CONTROL_RADIUS}px;
  padding: 6px 9px;
  selection-background-color: {colors.accent};
  selection-color: {colors.surface};
}}
QLineEdit:read-only {{ background: {colors.surface_secondary}; }}
QPushButton {{
  min-height: {Geometry.CONTROL_HEIGHT}px;
  background: {colors.surface};
  border: 1px solid {colors.border_strong};
  border-radius: {Shape.CONTROL_RADIUS}px;
  padding: 5px {Spacing.MD}px;
}}
QPushButton:hover {{ background: {colors.surface_secondary}; }}
QPushButton:pressed {{ background: {colors.selection}; }}
QPushButton:disabled {{ color: {colors.text_muted}; background: {colors.surface_secondary}; }}
#primaryButton {{
  background: {colors.accent};
  color: {colors.surface};
  border: 1px solid {colors.accent};
  font-weight: 600;
}}
#primaryButton:hover {{ background: {colors.accent_hover}; border-color: {colors.accent_hover}; }}
#primaryButton:pressed {{
  background: {colors.accent_pressed};
  border-color: {colors.accent_pressed};
}}
#primaryButton:focus {{ border: 1px solid {colors.focus}; }}
#primaryButton:disabled {{ background: {colors.border_strong}; color: {colors.surface}; }}
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
