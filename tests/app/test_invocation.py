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
    assert invocation.quick_rename_directories == ()
    assert invocation.quick_rename_directory is None


def test_quick_rename_accepts_one_or_more_directory_arguments() -> None:
    invocation = parse_invocation(
        ("--quick-rename", r"D:\DLsite\RJ01609020 old", r"D:\DLsite\RJ01636949 old")
    )

    assert invocation.mode is LaunchMode.QUICK_RENAME
    assert invocation.quick_rename_directories == (
        Path(r"D:\DLsite\RJ01609020 old"),
        Path(r"D:\DLsite\RJ01636949 old"),
    )
    assert invocation.quick_rename_directory is None


@pytest.mark.parametrize(
    "arguments",
    [
        ("--quick-rename",),
        ("--unknown",),
        ("--quick-rename", "A", "--quick-rename", "B"),
    ],
)
def test_invalid_invocation_is_rejected_without_guessing(arguments: tuple[str, ...]) -> None:
    with pytest.raises(InvocationParseError):
        parse_invocation(arguments)
