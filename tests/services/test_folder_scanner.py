from pathlib import Path

import pytest

from dlsite_organizer.domain.organizer import ScanCandidateStatus, ScanSkipReason
from dlsite_organizer.services.folder_scanner import (
    FolderScanFailure,
    FolderScanFailureKind,
    FolderScanner,
)


def test_scanner_returns_empty_result_for_empty_root(tmp_path: Path) -> None:
    result = FolderScanner().scan(tmp_path)

    assert result.root_path == tmp_path.resolve()
    assert result.candidates == ()
    assert result.skipped == ()


def test_scanner_finds_valid_lowercase_and_embedded_codes(tmp_path: Path) -> None:
    (tmp_path / "[RJ01609020] old title").mkdir()
    (tmp_path / "Circle RJ01636949").mkdir()

    result = FolderScanner().scan(tmp_path)

    assert [candidate.work_code for candidate in result.candidates] == [
        "RJ01609020",
        "RJ01636949",
    ]
    assert all(candidate.status is ScanCandidateStatus.VALID for candidate in result.candidates)


def test_scanner_skips_no_code_directories_and_counts_them(tmp_path: Path) -> None:
    (tmp_path / "Misc").mkdir()

    result = FolderScanner().scan(tmp_path)

    assert result.candidates == ()
    assert result.skipped_count == 1
    assert result.skipped[0].reason is ScanSkipReason.NO_WORK_CODE


def test_scanner_deduplicates_repeated_code_and_reports_ambiguity(tmp_path: Path) -> None:
    (tmp_path / "RJ01609020 [RJ01609020]").mkdir()
    (tmp_path / "RJ01609020 + RJ01636949").mkdir()

    result = FolderScanner().scan(tmp_path)

    valid = next(
        candidate
        for candidate in result.candidates
        if candidate.status is ScanCandidateStatus.VALID
    )
    ambiguous = next(
        candidate
        for candidate in result.candidates
        if candidate.status is ScanCandidateStatus.AMBIGUOUS_WORK_CODE
    )
    assert valid.work_codes == ("RJ01609020",)
    assert ambiguous.work_code is None
    assert ambiguous.work_codes == ("RJ01609020", "RJ01636949")
    assert "多个不同 RJcode" in (ambiguous.error or "")


def test_scanner_ignores_files_and_hidden_directories(tmp_path: Path) -> None:
    (tmp_path / "RJ01609020").mkdir()
    (tmp_path / "RJ01636949.txt").write_text("not a folder", encoding="utf-8")
    (tmp_path / ".hidden RJ01637033").mkdir()

    result = FolderScanner().scan(tmp_path)

    assert len(result.candidates) == 1
    assert {item.reason for item in result.skipped} == {
        ScanSkipReason.NOT_DIRECTORY,
        ScanSkipReason.HIDDEN,
    }


def test_scanner_does_not_follow_directory_symlink(tmp_path: Path) -> None:
    target = tmp_path / "target RJ01609020"
    target.mkdir()
    link = tmp_path / "link RJ01636949"
    try:
        link.symlink_to(target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks are unavailable in this environment")

    result = FolderScanner().scan(tmp_path)

    assert [candidate.work_code for candidate in result.candidates] == ["RJ01609020"]
    assert any(item.reason is ScanSkipReason.SYMLINK for item in result.skipped)


def test_scanner_preserves_unicode_directory_names(tmp_path: Path) -> None:
    folder = tmp_path / "雨音の夜 RJ01609020"
    folder.mkdir()

    result = FolderScanner().scan(tmp_path)

    assert result.candidates[0].source_path == folder


@pytest.mark.parametrize("root", ["missing", "a-file"])
def test_scanner_rejects_invalid_or_non_directory_root(tmp_path: Path, root: str) -> None:
    root_path = tmp_path / root
    if root == "a-file":
        root_path.write_text("file", encoding="utf-8")

    with pytest.raises(FolderScanFailure) as caught:
        FolderScanner().scan(root_path)

    assert caught.value.kind is FolderScanFailureKind.INVALID_ROOT


def test_scanner_maps_root_read_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fail_iterdir(_path: Path):
        raise PermissionError("permission denied")

    monkeypatch.setattr(Path, "iterdir", fail_iterdir)

    with pytest.raises(FolderScanFailure) as caught:
        FolderScanner().scan(tmp_path)

    assert caught.value.kind is FolderScanFailureKind.ACCESS
