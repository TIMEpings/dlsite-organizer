"""Parsing and normalization for DLsite work identifiers."""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import ClassVar


class WorkCodeError(ValueError):
    """Raised when a DLsite work identifier is invalid or unsupported."""


SUPPORTED_WORK_CODE_PREFIXES = ("RJ", "BJ", "VJ")
INVALID_WORK_CODE_MESSAGE = "请输入完整的 RJ、BJ 或 VJ 编号。"


@dataclass(frozen=True, slots=True)
class WorkCode:
    """A normalized DLsite work identifier.

    Six through ten digits accommodates historical and future identifier widths
    without encoding the current width in every application layer.
    """

    prefix: str
    digits: str

    SUPPORTED_PREFIXES: ClassVar[frozenset[str]] = frozenset(SUPPORTED_WORK_CODE_PREFIXES)
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
            raise WorkCodeError(INVALID_WORK_CODE_MESSAGE)

        prefix, digits = match.groups()
        prefix = prefix.upper()
        permitted = (
            {item.upper() for item in allowed_prefixes}
            if allowed_prefixes is not None
            else cls.SUPPORTED_PREFIXES
        )
        if prefix not in permitted:
            raise WorkCodeError(_invalid_message(permitted))
        if not cls.MIN_DIGITS <= len(digits) <= cls.MAX_DIGITS:
            raise WorkCodeError(_invalid_message(permitted))
        return cls(prefix=prefix, digits=digits)

    def __str__(self) -> str:
        return f"{self.prefix}{self.digits}"


def normalize_workno(value: str) -> str:
    """Normalize any supported DLsite work number."""
    return str(WorkCode.parse(value))


def normalize_rjcode(value: str) -> str:
    """Compatibility alias for callers that used the old RJ-only name.

    The alias intentionally follows the general WorkCode contract now, so a
    legacy naming/template or service call cannot make BJ/VJ a second pipeline.
    """
    return normalize_workno(value)


def extract_work_codes(text: str) -> list[str]:
    """Extract distinct, normalized work codes from a folder name.

    Folder names are tokenized first and every token is validated by the same
    :class:`WorkCode` parser used by manual lookup.  This keeps extraction from
    introducing a second, subtly different code-validation rule.
    """
    if not isinstance(text, str):
        return []

    codes: list[str] = []
    for match in _EMBEDDED_PATTERN.finditer(text):
        try:
            normalized = normalize_workno(match.group(0))
        except WorkCodeError:
            continue
        if normalized not in codes:
            codes.append(normalized)
    return codes


_EMBEDDED_PATTERN = re.compile(r"(?:RJ|BJ|VJ)\d+", re.IGNORECASE)


def _invalid_message(permitted: Iterable[str]) -> str:
    """Use one concise validation message for the public input boundary."""
    permitted_set = set(permitted)
    if permitted_set == set(SUPPORTED_WORK_CODE_PREFIXES):
        return INVALID_WORK_CODE_MESSAGE
    expected = "、".join(
        prefix for prefix in SUPPORTED_WORK_CODE_PREFIXES if prefix in permitted_set
    )
    return f"请输入完整的 {expected} 编号。"
