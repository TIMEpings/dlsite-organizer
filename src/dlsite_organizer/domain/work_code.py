"""Parsing and normalization for DLsite work identifiers."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import ClassVar


class WorkCodeError(ValueError):
    """Raised when a DLsite work identifier is invalid or unsupported."""


@dataclass(frozen=True, slots=True)
class WorkCode:
    """A normalized DLsite work identifier.

    Six through ten digits accommodates historical and future identifier widths
    without encoding the current width in every application layer.
    """

    prefix: str
    digits: str

    SUPPORTED_PREFIXES: ClassVar[frozenset[str]] = frozenset({"RJ", "VJ", "BJ"})
    MIN_DIGITS: ClassVar[int] = 6
    MAX_DIGITS: ClassVar[int] = 10
    _PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^([A-Za-z]{2})(\d+)$")

    @classmethod
    def parse(
        cls,
        value: str,
        *,
        allowed_prefixes: Iterable[str] | None = None,
    ) -> WorkCode:
        """Validate and normalize a work code."""
        if not isinstance(value, str):
            raise WorkCodeError("作品编号必须是文本。")

        candidate = value.strip()
        match = cls._PATTERN.fullmatch(candidate)
        if match is None:
            raise WorkCodeError("请输入 RJ 加数字组成的作品编号，例如 RJ01234567。")

        prefix, digits = match.groups()
        prefix = prefix.upper()
        permitted = (
            {item.upper() for item in allowed_prefixes}
            if allowed_prefixes is not None
            else cls.SUPPORTED_PREFIXES
        )
        if prefix not in permitted:
            expected = "、".join(sorted(permitted))
            raise WorkCodeError(f"暂不支持 {prefix} 编号；当前支持：{expected}。")
        if not cls.MIN_DIGITS <= len(digits) <= cls.MAX_DIGITS:
            raise WorkCodeError(f"作品编号的数字部分应为 {cls.MIN_DIGITS}–{cls.MAX_DIGITS} 位。")
        return cls(prefix=prefix, digits=digits)

    def __str__(self) -> str:
        return f"{self.prefix}{self.digits}"


def normalize_rjcode(value: str) -> str:
    """Normalize an RJ code for the v0.1 user interface."""
    return str(WorkCode.parse(value, allowed_prefixes={"RJ"}))
