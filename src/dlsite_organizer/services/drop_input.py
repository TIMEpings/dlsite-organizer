"""Normalize and validate directories received from drag-and-drop entry points."""

from __future__ import annotations

import ntpath
import os
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from dlsite_organizer.domain.work_code import extract_work_codes


class DropInputKind(StrEnum):
    """The two deliberate meanings of a directory dropped on the app."""

    ROOT = "root"
    WORK_FOLDERS = "work_folders"


class DropInputError(ValueError):
    """A drop cannot be interpreted safely without touching the filesystem."""

    def __init__(self, message: str, *, kind: DropInputKind | None = None) -> None:
        super().__init__(message)
        self.user_message = message
        self.kind = kind


@dataclass(frozen=True, slots=True)
class DroppedDirectorySelection:
    """A validated root and, optionally, explicitly selected direct children."""

    root_path: Path
    selected_paths: tuple[Path, ...] | None
    work_codes: tuple[str, ...] = ()

    @property
    def is_selective(self) -> bool:
        return self.selected_paths is not None


class DropInputService:
    """Apply one shared, fail-closed interpretation to all directory drops."""

    def validate_full_drop(self, paths: Sequence[Path | str]) -> DroppedDirectorySelection:
        """Interpret a full-mode drop as either a root or selected work folders."""
        normalized = _normalize_unique_paths(paths)
        if len(normalized) == 1:
            path = normalized[0]
            _validate_directory(path, role="作品目录")
            codes = _codes_or_empty(path)
            if not codes:
                return DroppedDirectorySelection(root_path=path, selected_paths=None)
            if len(codes) > 1:
                raise DropInputError("检测到多个 RJ 编号，无法确定作品。")
            _validate_parent(path)
            return DroppedDirectorySelection(
                root_path=path.parent,
                selected_paths=(path,),
                work_codes=(codes[0],),
            )

        selection = self.validate_work_folders(normalized)
        return selection

    def validate_work_folders(
        self, paths: Sequence[Path | str]
    ) -> DroppedDirectorySelection:
        """Validate an explicit batch of work directories for Quick Rename."""
        normalized = _normalize_unique_paths(paths)
        if not normalized:
            raise DropInputError("请至少拖入一个作品文件夹。")
        for path in normalized:
            _validate_directory(path, role="轻量模式")
            _validate_parent(path)

        parent = normalized[0].parent
        parent_key = _path_key(parent)
        if any(_path_key(path.parent) != parent_key for path in normalized[1:]):
            raise DropInputError("轻量模式一次只能处理同一父目录下的作品文件夹。")

        codes: list[str] = []
        for path in normalized:
            found = _codes_or_empty(path)
            if not found:
                raise DropInputError(
                    f"目录“{path.name}”不包含 RJ 编号，无法执行轻量重命名。"
                )
            if len(found) > 1:
                raise DropInputError("检测到多个 RJ 编号，无法确定作品。")
            codes.append(found[0])
        return DroppedDirectorySelection(
            root_path=parent,
            selected_paths=tuple(normalized),
            work_codes=tuple(codes),
        )


def _normalize_unique_paths(paths: Sequence[Path | str]) -> tuple[Path, ...]:
    normalized = tuple(Path(path).expanduser().absolute() for path in paths)
    seen: set[str] = set()
    for path in normalized:
        key = _path_key(path)
        if key in seen:
            raise DropInputError("拖入的目录包含重复项目，已拒绝本次操作。")
        seen.add(key)
    return normalized


def _validate_directory(path: Path, *, role: str) -> None:
    if _link_like(path):
        raise DropInputError(f"{role}不支持符号链接、junction 或 reparse point 目录。")
    try:
        exists = path.exists()
        is_dir = path.is_dir()
    except OSError as exc:
        raise DropInputError(f"{role}无法读取，请检查目录后重试。") from exc
    if not exists or not is_dir:
        raise DropInputError(f"{role}必须是一个存在的文件夹。")


def _validate_parent(path: Path) -> None:
    parent = path.parent
    if _link_like(parent) or not parent.exists() or not parent.is_dir():
        raise DropInputError("作品文件夹的父目录无效，已拒绝本次操作。")


def _codes_or_empty(path: Path) -> list[str]:
    try:
        return extract_work_codes(path.name)
    except OSError as exc:
        raise DropInputError(f"无法读取目录名称：{path.name}") from exc


def _link_like(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        if is_junction is not None and is_junction():
            return True
        try:
            stat_result = path.stat(follow_symlinks=False)
        except FileNotFoundError:
            return False
        reparse_point = getattr(os, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        return bool(getattr(stat_result, "st_file_attributes", 0) & reparse_point)
    except (OSError, ValueError):
        return True


def _path_key(path: Path) -> str:
    try:
        value = str(path.resolve(strict=False))
    except OSError:
        value = str(path)
    return ntpath.normcase(value)
