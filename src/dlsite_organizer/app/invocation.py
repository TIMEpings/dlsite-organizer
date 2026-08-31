"""Typed command-line invocation modes for the desktop application."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import NoReturn


class LaunchMode(StrEnum):
    """The user-facing application entry points."""

    NORMAL = "normal"
    QUICK_RENAME = "quick-rename"


class InvocationParseError(ValueError):
    """The application was launched with an unsupported argument shape."""


@dataclass(frozen=True, slots=True)
class ApplicationInvocation:
    """The validated, narrow contract passed from the CLI to the UI bootstrap."""

    mode: LaunchMode
    quick_rename_directory: Path | None = None


class _ArgumentParser(argparse.ArgumentParser):
    """Turn argparse's console-oriented errors into a UI-safe exception."""

    def error(self, message: str) -> NoReturn:
        usage = self.format_usage().strip()
        raise InvocationParseError(f"{message}\n用法：{usage}")


class _StoreOnce(argparse.Action):
    """Reject duplicate quick-action options instead of silently replacing one."""

    def __call__(
        self,
        parser: argparse.ArgumentParser,
        namespace: argparse.Namespace,
        values: object,
        option_string: str | None = None,
    ) -> None:
        if getattr(namespace, self.dest, None) is not None:
            parser.error(f"{option_string or self.dest} 只能指定一次。")
        setattr(namespace, self.dest, values)


def parse_invocation(argv: Sequence[str]) -> ApplicationInvocation:
    """Parse exactly the supported application invocation contract."""
    parser = _ArgumentParser(
        prog="dlsite-organizer",
        description="DLsite Organizer desktop application",
    )
    parser.add_argument(
        "--quick-rename",
        dest="quick_rename_directory",
        metavar="DIRECTORY",
        action=_StoreOnce,
        type=_directory_argument,
        help="立即对一个已存在的 DLsite 作品文件夹执行 Quick Rename。",
    )
    parsed = parser.parse_args(tuple(argv))
    directory = parsed.quick_rename_directory
    if directory is None:
        return ApplicationInvocation(mode=LaunchMode.NORMAL)
    return ApplicationInvocation(
        mode=LaunchMode.QUICK_RENAME,
        quick_rename_directory=Path(directory).expanduser(),
    )


def _directory_argument(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("DIRECTORY 不能为空。")
    return value
