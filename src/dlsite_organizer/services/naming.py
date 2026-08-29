"""Simple, filesystem-conscious work naming service."""

from __future__ import annotations

import re
import string
from datetime import date

from dlsite_organizer.domain.naming import sanitize_windows_name
from dlsite_organizer.domain.work import Work

DEFAULT_NAMING_TEMPLATE = "[{maker_name}][{workno}] {title}"
_EMPTY_GROUP = re.compile(r"\[\s*\]")
_SPACES = re.compile(r"\s{2,}")


class NamingTemplateError(ValueError):
    """The naming template contains an unsupported field or invalid syntax."""


class NamingService:
    """Render a small set of Work fields and sanitize the result for Windows."""

    _SUPPORTED_FIELDS = frozenset(
        {
            "workno",
            "title",
            "maker_id",
            "maker_name",
            "release_date",
            "series_name",
            "cv",
            "tags",
        }
    )

    def __init__(self, template: str = DEFAULT_NAMING_TEMPLATE) -> None:
        self._template = template
        self._validate_template(template)

    def format(self, work: Work) -> str:
        """Return a formatted name with absent values omitted predictably."""
        fields = {
            "workno": work.workno,
            "title": work.title,
            "maker_id": work.maker_id or "",
            "maker_name": work.maker_name or "",
            "release_date": _date_text(work.release_date),
            "series_name": work.series_name or "",
            "cv": ", ".join(work.cvs),
            "tags": ", ".join(work.tags),
        }
        try:
            rendered = self._template.format_map(fields)
        except (KeyError, ValueError) as exc:
            raise NamingTemplateError("无法应用命名模板。") from exc
        rendered = _EMPTY_GROUP.sub("", rendered)
        rendered = _SPACES.sub(" ", rendered).strip()
        return sanitize_windows_name(rendered)

    @classmethod
    def _validate_template(cls, template: str) -> None:
        if not template.strip():
            raise NamingTemplateError("命名模板不能为空。")
        try:
            fields = {
                field_name
                for _, field_name, _, _ in string.Formatter().parse(template)
                if field_name is not None
            }
        except ValueError as exc:
            raise NamingTemplateError("命名模板格式不正确。") from exc
        unsupported = fields - cls._SUPPORTED_FIELDS
        if unsupported:
            raise NamingTemplateError(f"不支持的命名字段：{', '.join(sorted(unsupported))}")


def _date_text(value: date | None) -> str:
    return value.isoformat() if value else ""
