from pathlib import Path

import pytest

from dlsite_organizer.services.drop_input import DropInputError, DropInputService


@pytest.mark.parametrize("workno", ["RJ01609020", "BJ00000001", "VJ00000001"])
def test_full_single_work_folder_is_selective(tmp_path: Path, workno: str) -> None:
    folder = tmp_path / f"[Circle][{workno}] old"
    folder.mkdir()

    selection = DropInputService().validate_full_drop((folder,))

    assert selection.root_path == tmp_path
    assert selection.selected_paths == (folder.absolute(),)
    assert selection.work_codes == (workno,)


def test_full_single_no_rj_folder_is_root_drop(tmp_path: Path) -> None:
    selection = DropInputService().validate_full_drop((tmp_path,))

    assert selection.root_path == tmp_path.absolute()
    assert selection.selected_paths is None


def test_quick_rejects_no_work_code_and_ambiguous_names(tmp_path: Path) -> None:
    no_code = tmp_path / "random folder"
    no_code.mkdir()
    ambiguous = tmp_path / "RJ01609020 RJ01636949"
    ambiguous.mkdir()
    service = DropInputService()

    with pytest.raises(DropInputError, match="不包含作品编号"):
        service.validate_work_folders((no_code,))
    with pytest.raises(DropInputError, match="多个不同作品编号"):
        service.validate_work_folders((ambiguous,))


@pytest.mark.parametrize("workno", ["BJ00000001", "VJ00000001"])
def test_quick_accepts_non_rj_work_folders(tmp_path: Path, workno: str) -> None:
    folder = tmp_path / f"old {workno}"
    folder.mkdir()

    selection = DropInputService().validate_work_folders((folder,))

    assert selection.work_codes == (workno,)
    assert selection.selected_paths == (folder.absolute(),)


def test_quick_rejects_mixed_parent_batch_before_lookup(tmp_path: Path) -> None:
    first = tmp_path / "one" / "RJ01609020 old"
    second = tmp_path / "two" / "RJ01636949 old"
    first.mkdir(parents=True)
    second.mkdir(parents=True)

    with pytest.raises(DropInputError, match="同一父目录"):
        DropInputService().validate_work_folders((first, second))


def test_quick_rejects_files_and_nonexistent_directories(tmp_path: Path) -> None:
    file_path = tmp_path / "RJ01609020.txt"
    file_path.write_text("not a directory", encoding="utf-8")
    missing = tmp_path / "RJ01609020 missing"

    with pytest.raises(DropInputError, match="存在的文件夹"):
        DropInputService().validate_work_folders((file_path,))
    with pytest.raises(DropInputError):
        DropInputService().validate_work_folders((missing,))
