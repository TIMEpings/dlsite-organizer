from pathlib import Path

import pytest

from dlsite_organizer.app.invocation import (
    InvocationParseError,
    LaunchMode,
    parse_invocation,
)


def test_empty_invocation_keeps_normal_startup_mode() -> None:
    invocation = parse_invocation(())

    assert invocation.mode is LaunchMode.NORMAL
    assert invocation.quick_rename_directory is None


def test_quick_rename_accepts_exactly_one_directory_argument() -> None:
    invocation = parse_invocation(("--quick-rename", r"D:\DLsite\RJ01609020 old"))

    assert invocation.mode is LaunchMode.QUICK_RENAME
    assert invocation.quick_rename_directory == Path(r"D:\DLsite\RJ01609020 old")


@pytest.mark.parametrize(
    "arguments",
    [
        ("--quick-rename",),
        ("--quick-rename", "A", "B"),
        ("--unknown",),
        ("--quick-rename", "A", "--quick-rename", "B"),
    ],
)
def test_invalid_invocation_is_rejected_without_guessing(arguments: tuple[str, ...]) -> None:
    with pytest.raises(InvocationParseError):
        parse_invocation(arguments)
