"""Typed command-line invocation modes for the desktop application."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import NoReturn

from dlsite_organizer.domain.quick_rename import (
    MAX_QUICK_RENAME_ITEMS,
    quick_rename_batch_limit_message,
)


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
    quick_rename_directories: tuple[Path, ...] = ()
    quick_rename_host: bool = False
    handoff_id: str | None = None

    @property
    def quick_rename_directory(self) -> Path | None:
        """Return the singular path for legacy callers of the old contract."""
        if len(self.quick_rename_directories) == 1:
            return self.quick_rename_directories[0]
        return None


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
        dest="quick_rename_directories",
        metavar="DIRECTORY",
        action=_StoreOnce,
        type=_directory_argument,
        nargs="+",
        help="立即对一个或多个已存在的同一父目录下 DLsite 作品文件夹执行 Quick Rename。",
    )
    parser.add_argument(
        "--quick-rename-host",
        dest="quick_rename_host",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--handoff-id", action=_StoreOnce, type=_handoff_id_argument, help=argparse.SUPPRESS
    )
    parsed = parser.parse_args(tuple(argv))
    directories = parsed.quick_rename_directories
    if parsed.quick_rename_host and directories is not None:
        parser.error("--quick-rename-host 不能与 --quick-rename 一起使用。")
    if parsed.handoff_id is not None and not parsed.quick_rename_host:
        parser.error("--handoff-id 需要 --quick-rename-host。")
    if directories is None:
        return ApplicationInvocation(
            mode=LaunchMode.NORMAL,
            quick_rename_host=parsed.quick_rename_host,
            handoff_id=parsed.handoff_id,
        )
    if len(directories) > MAX_QUICK_RENAME_ITEMS:
        parser.error(quick_rename_batch_limit_message())
    return ApplicationInvocation(
        mode=LaunchMode.QUICK_RENAME,
        quick_rename_directories=tuple(Path(directory).expanduser() for directory in directories),
    )


def _directory_argument(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("DIRECTORY 不能为空。")
    return value


def _handoff_id_argument(value: str) -> str:
    from dlsite_organizer.app.single_instance import ProtocolError, _validate_request_id

    try:
        return _validate_request_id(value)
    except ProtocolError as exc:
        raise argparse.ArgumentTypeError("无效的 handoff ID。") from exc
