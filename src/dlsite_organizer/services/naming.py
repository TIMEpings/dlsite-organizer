"""Simple, filesystem-conscious work naming service.

The template language intentionally stays small: it is made up of named
placeholders and the existing ``[...]`` empty-group cleanup.  It never
evaluates Python expressions or delegates rendering to a general-purpose
template engine.
"""

from __future__ import annotations

import re
import string
from datetime import date
from typing import Any

from dlsite_organizer.domain.naming import sanitize_windows_name
from dlsite_organizer.domain.work import Work

DEFAULT_NAMING_TEMPLATE = "[{maker_name}][{workno}] {title}"

# These are the tokens the Settings UI recommends for new templates.  The
# names follow the normalized Work contract where possible; ``cv``, ``tags``,
# ``age``, and ``language`` are deliberately user-facing formatter values.
CANONICAL_TEMPLATE_VARIABLES = (
    ("RJ编号", "{workno}"),
    ("标题", "{title}"),
    ("社团", "{maker_name}"),
    ("社团ID", "{maker_id}"),
    ("系列", "{series_name}"),
    ("CV", "{cv}"),
    ("标签", "{tags}"),
    ("年龄", "{age}"),
    ("语言", "{language}"),
    ("发售日期", "{release_date}"),
)

# Public in v0.x/v1.0 configurations.  Keep accepting these tokens even
# though the Settings UI intentionally does not advertise them anymore.
LEGACY_TEMPLATE_ALIASES = (
    "rjcode",
    "work_name",
    "series",
    "cv_list",
    "cv_list_str",
    "tags_list",
    "tags_list_str",
    "age_category",
    "language_code",
)
_EMPTY_GROUP = re.compile(r"\[\s*\]")
_SPACES = re.compile(r"\s{2,}")
_WINDOWS_DATE_DIRECTIVES = frozenset("aAbBcdHIjmMpSUwWxXyYZ%")
_LANGUAGE_NAMES = {
    "JPN": "日语",
    "CHI_HANS": "简体中文",
    "CHI_HANT": "繁体中文",
    "ENG": "英语",
    "KOR": "韩语",
    "KO_KR": "韩语",
    "SPA": "西班牙语",
    "GER": "德语",
    "FRE": "法语",
    "IND": "印尼语",
    "ITA": "意大利语",
    "POR": "葡萄牙语",
    "SWE": "瑞典语",
    "THA": "泰语",
    "VIE": "越南语",
}


class NamingTemplateError(ValueError):
    """The naming template contains an unsupported field or invalid syntax."""


class NamingService:
    """Render a small set of Work fields and sanitize the result for Windows."""

    _SUPPORTED_FIELDS = frozenset(
        field[1:-1] for _, field in CANONICAL_TEMPLATE_VARIABLES
    ) | frozenset(LEGACY_TEMPLATE_ALIASES) | frozenset({"language_name"})
    _LEGACY_FIELDS = frozenset(
        {
            "work_name",
            "cv_list",
            "cv_list_str",
            "tags_list",
            "tags_list_str",
            "age_category",
        }
    )

    def __init__(
        self,
        template: str = DEFAULT_NAMING_TEMPLATE,
        *,
        cv_separator: str = ", ",
        cv_prefix: str = "",
        cv_suffix: str = "",
        tag_separator: str = ", ",
        max_tags: int = 0,
        age_unknown: str = "",
        hide_general_age: bool = False,
        date_format: str = "%Y-%m-%d",
        illegal_char_replacement: str = "_",
    ) -> None:
        self.configure(
            template=template,
            cv_separator=cv_separator,
            cv_prefix=cv_prefix,
            cv_suffix=cv_suffix,
            tag_separator=tag_separator,
            max_tags=max_tags,
            age_unknown=age_unknown,
            hide_general_age=hide_general_age,
            date_format=date_format,
            illegal_char_replacement=illegal_char_replacement,
        )

    @property
    def template(self) -> str:
        return self._template

    def configure(
        self,
        *,
        template: str,
        cv_separator: str = ", ",
        cv_prefix: str = "",
        cv_suffix: str = "",
        tag_separator: str = ", ",
        max_tags: int = 0,
        age_unknown: str = "",
        hide_general_age: bool = False,
        date_format: str = "%Y-%m-%d",
        illegal_char_replacement: str = "_",
    ) -> None:
        """Validate all naming options, then replace the live configuration.

        Validation happens before any instance attribute is changed so a bad
        Settings edit cannot leave a partially updated renderer in memory.
        """
        self._validate_template(template)
        if max_tags < 0:
            raise NamingTemplateError("标签数量不能为负数。")
        if len(illegal_char_replacement) != 1 or _is_windows_illegal(
            illegal_char_replacement
        ):
            raise NamingTemplateError("非法字符替换符必须是一个安全字符。")
        _validate_date_format(date_format)
        self._template = template
        self._cv_separator = cv_separator
        self._cv_prefix = cv_prefix
        self._cv_suffix = cv_suffix
        self._tag_separator = tag_separator
        self._max_tags = max_tags
        self._age_unknown = age_unknown
        self._hide_general_age = hide_general_age
        self._date_format = date_format
        self._illegal_char_replacement = illegal_char_replacement
        self._fields = _template_fields(template)

    def apply_settings(self, settings: Any) -> None:
        """Apply the naming portion of an AppSettings-like object."""
        self.configure(
            template=settings.naming_template,
            cv_separator=settings.cv_separator,
            cv_prefix=settings.cv_prefix,
            cv_suffix=settings.cv_suffix,
            tag_separator=settings.tag_separator,
            max_tags=settings.max_tags,
            hide_general_age=settings.hide_general_age,
            date_format=settings.date_format,
            illegal_char_replacement=settings.illegal_char_replacement,
        )

    def format(self, work: Work) -> str:
        """Return a formatted name with absent values omitted predictably."""
        cv_names = tuple(work.cvs)
        cv_value = self._cv_separator.join(cv_names)
        if cv_value:
            cv_value = f"{self._cv_prefix}{cv_value}{self._cv_suffix}"
        tags = tuple(work.tags)
        if self._max_tags:
            tags = tags[: self._max_tags]
        age = _age_text(work.age_category, hide_general=self._hide_general_age)
        if age is None:
            age = self._age_unknown
        language_code = _language_code(work.language)
        language_name = _LANGUAGE_NAMES.get(language_code, language_code)
        # ``language`` is the canonical human-facing value.  Templates that
        # use the pre-Phase-B alias family retain the old raw-code behavior.
        language_value = (
            language_code if self._fields & self._LEGACY_FIELDS else language_name
        )
        fields = {
            "workno": work.workno,
            "rjcode": work.workno,
            "title": work.title,
            "work_name": work.title,
            "maker_id": work.maker_id or "",
            "maker_name": work.maker_name or "",
            "release_date": _date_text(work.release_date, self._date_format),
            "series": work.series_name or "",
            "series_name": work.series_name or "",
            "cv": cv_value,
            "cv_list": ", ".join(cv_names),
            "cv_list_str": ", ".join(cv_names),
            "tags": self._tag_separator.join(tags),
            "tags_list": ", ".join(work.tags),
            "tags_list_str": ", ".join(work.tags),
            "age": age,
            "age_category": work.age_category.value,
            "language": language_value,
            "language_name": language_name,
            "language_code": language_code,
        }
        try:
            rendered = self._template.format_map(fields)
        except (KeyError, ValueError) as exc:
            raise NamingTemplateError("无法应用命名模板。") from exc
        rendered = _EMPTY_GROUP.sub("", rendered)
        rendered = _SPACES.sub(" ", rendered).strip()
        return sanitize_windows_name(rendered, replacement=self._illegal_char_replacement)

    @classmethod
    def _validate_template(cls, template: str) -> None:
        if not template.strip():
            raise NamingTemplateError("命名模板不能为空。")
        try:
            parts = tuple(string.Formatter().parse(template))
        except ValueError as exc:
            raise NamingTemplateError("命名模板格式不正确。") from exc
        fields = {
            field_name
            for _, field_name, format_spec, conversion in parts
            if field_name is not None
        }
        if any(
            not field_name
            or any(character in field_name for character in ".[]")
            or format_spec
            or conversion
            for _, field_name, format_spec, conversion in parts
            if field_name is not None
        ):
            raise NamingTemplateError("模板只支持简单的 {变量名} 占位符。")
        unsupported = fields - cls._SUPPORTED_FIELDS
        if unsupported:
            raise NamingTemplateError(f"不支持的命名字段：{', '.join(sorted(unsupported))}")


def _date_text(value: date | None, date_format: str = "%Y-%m-%d") -> str:
    return value.strftime(date_format) if value else ""


def _template_fields(template: str) -> frozenset[str]:
    return frozenset(
        field_name
        for _, field_name, _, _ in string.Formatter().parse(template)
        if field_name is not None
    )


def _age_text(value: Any, *, hide_general: bool) -> str | None:
    normalized = getattr(value, "value", value)
    if normalized == "general":
        return "" if hide_general else "全年龄"
    if normalized == "r15":
        return "R15"
    if normalized == "r18":
        return "R18"
    return None


def _language_code(value: Any) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value))


def _is_windows_illegal(value: str) -> bool:
    return bool(re.search(r'[<>:"/\\|?*\x00-\x1f]', value))


def _validate_date_format(value: str) -> None:
    if not value.strip():
        raise NamingTemplateError("日期格式不能为空。")
    for match in re.finditer(r"%(.)", value):
        if match.group(1) not in _WINDOWS_DATE_DIRECTIVES:
            raise NamingTemplateError(f"不支持的日期格式指令：%{match.group(1)}")
