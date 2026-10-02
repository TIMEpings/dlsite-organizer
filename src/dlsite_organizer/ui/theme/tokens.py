"""Small shared spacing, shape, typography, and color-role tokens."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Spacing:
    """Logical pixel spacing used by the application shell."""

    XS = 4
    SM = 8
    MD = 12
    LG = 16
    XL = 24
    XXL = 32


class Shape:
    """Shared corner radii."""

    CONTROL_RADIUS = 7
    PANEL_RADIUS = 10


class Geometry:
    """Shared control and navigation dimensions in logical pixels."""

    CONTROL_HEIGHT = 34
    NAVIGATION_ITEM_HEIGHT = 40
    SIDEBAR_WIDTH = 184


class FontRole(StrEnum):
    PAGE_TITLE = "page_title"
    PAGE_DESCRIPTION = "page_description"
    SECTION_TITLE = "section_title"
    BODY = "body"
    BODY_STRONG = "body_strong"
    SECONDARY = "secondary"
    CAPTION = "caption"
    TABLE = "table"
    STATUS = "status"


@dataclass(frozen=True, slots=True)
class FontSpec:
    point_size: float
    weight: int = 400


FONT_ROLES: dict[FontRole, FontSpec] = {
    FontRole.PAGE_TITLE: FontSpec(20, 600),
    FontRole.PAGE_DESCRIPTION: FontSpec(10),
    FontRole.SECTION_TITLE: FontSpec(10, 600),
    FontRole.BODY: FontSpec(10),
    FontRole.BODY_STRONG: FontSpec(10, 600),
    FontRole.SECONDARY: FontSpec(9.5),
    FontRole.CAPTION: FontSpec(9),
    FontRole.TABLE: FontSpec(9.5),
    FontRole.STATUS: FontSpec(9.5),
}


@dataclass(frozen=True, slots=True)
class SemanticColors:
    background: str
    surface: str
    surface_secondary: str
    sidebar: str
    sidebar_hover: str
    border: str
    border_strong: str
    text_primary: str
    text_secondary: str
    text_muted: str
    accent: str
    accent_hover: str
    accent_pressed: str
    selection: str
    focus: str
    success: str
    warning: str
    error: str
    blocked: str
    stale: str


LIGHT_COLORS = SemanticColors(
    background="#F5F7FA",
    surface="#FFFFFF",
    surface_secondary="#EEF2F7",
    sidebar="#F1F4F8",
    sidebar_hover="#E8EDF4",
    border="#E1E6EE",
    border_strong="#C9D2DF",
    text_primary="#202A38",
    text_secondary="#536174",
    text_muted="#7A8797",
    accent="#315FDB",
    accent_hover="#284FBF",
    accent_pressed="#2245A9",
    selection="#E6EDFF",
    focus="#6D91F2",
    success="#16805B",
    warning="#A65C00",
    error="#BC3543",
    blocked="#A52D3C",
    stale="#8B5A16",
)
