from pathlib import Path

import pytest

from dlsite_organizer.app.invocation import (
    InvocationParseError,
    LaunchMode,
    parse_invocation,
)
from dlsite_organizer.domain.quick_rename import MAX_QUICK_RENAME_ITEMS


def test_empty_invocation_keeps_normal_startup_mode() -> None:
    invocation = parse_invocation(())

    assert invocation.mode is LaunchMode.NORMAL
    assert invocation.quick_rename_directories == ()
    assert invocation.quick_rename_directory is None


@pytest.mark.parametrize(
    "work_paths",
    [
        (r"D:\DLsite\RJ01609020 old", r"D:\DLsite\RJ01636949 old"),
        (r"D:\DLsite\BJ00000001 old",),
        (r"D:\DLsite\VJ00000001 old",),
    ],
)
def test_quick_rename_accepts_one_or_more_directory_arguments(
    work_paths: tuple[str, ...],
) -> None:
    invocation = parse_invocation(
        ("--quick-rename", *work_paths)
    )

    assert invocation.mode is LaunchMode.QUICK_RENAME
    assert invocation.quick_rename_directories == tuple(Path(path) for path in work_paths)
    assert invocation.quick_rename_directory == (
        Path(work_paths[0]) if len(work_paths) == 1 else None
    )


def test_quick_rename_accepts_limit_and_rejects_oversized_batch() -> None:
    paths = tuple(rf"D:\DLsite\RJ{index:08d} old" for index in range(MAX_QUICK_RENAME_ITEMS))

    invocation = parse_invocation(("--quick-rename", *paths))

    assert len(invocation.quick_rename_directories) == MAX_QUICK_RENAME_ITEMS
    with pytest.raises(InvocationParseError, match="一次最多处理 32 个文件夹"):
        parse_invocation(("--quick-rename", *paths, r"D:\DLsite\RJ99999999 old"))


def test_internal_quick_rename_host_signal_has_no_paths() -> None:
    invocation = parse_invocation(("--quick-rename-host",))

    assert invocation.mode is LaunchMode.NORMAL
    assert invocation.quick_rename_host
    with pytest.raises(InvocationParseError):
        parse_invocation(("--quick-rename-host", "unexpected"))


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
