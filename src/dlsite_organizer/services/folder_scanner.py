"""Safe, non-recursive scanning of local work folders."""

from __future__ import annotations

import ctypes
import logging
import os
from enum import StrEnum
from pathlib import Path

from dlsite_organizer.domain.organizer import (
    ScanCandidate,
    ScanCandidateStatus,
    ScanResult,
    ScanSkipped,
    ScanSkipReason,
)
from dlsite_organizer.domain.work_code import extract_work_codes

logger = logging.getLogger(__name__)


class FolderScanFailureKind(StrEnum):
    """User-facing categories for a root scan that cannot start."""

    INVALID_ROOT = "invalid_root"
    ACCESS = "access"


class FolderScanFailure(Exception):
    """A root scan failure that is safe to show in the GUI."""

    def __init__(self, kind: FolderScanFailureKind, message: str) -> None:
        super().__init__(message)
        self.kind = kind
        self.user_message = message


class FolderScanner:
    """Scan only the direct, non-symlink child directories of a root."""

    def scan(self, root: Path | str) -> ScanResult:
        """Return valid and ambiguous candidates without touching their contents."""
        try:
            root_path = Path(root).expanduser().resolve(strict=False)
        except (OSError, RuntimeError, TypeError) as exc:
            raise FolderScanFailure(
                FolderScanFailureKind.INVALID_ROOT,
                "作品根目录无效，无法开始扫描。",
            ) from exc

        if _is_link_like(root_path) or not root_path.exists() or not root_path.is_dir():
            raise FolderScanFailure(
                FolderScanFailureKind.INVALID_ROOT,
                "请选择一个存在的文件夹作为作品根目录。",
            )

        try:
            children = sorted(
                root_path.iterdir(),
                key=lambda path: (path.name.casefold(), path.name),
            )
        except OSError as exc:
            logger.warning("Cannot read organizer root %s: %s", root_path, exc)
            raise FolderScanFailure(
                FolderScanFailureKind.ACCESS,
                "无法读取作品根目录，请检查权限后重试。",
            ) from exc

        candidates: list[ScanCandidate] = []
        skipped: list[ScanSkipped] = []
        for child in children:
            reason = self._skip_reason(child)
            if reason is not None:
                skipped.append(ScanSkipped(source_path=child, reason=reason))
                continue

            try:
                codes = extract_work_codes(child.name)
            except OSError as exc:
                logger.warning("Cannot inspect organizer child %s: %s", child, exc)
                skipped.append(
                    ScanSkipped(
                        source_path=child,
                        reason=ScanSkipReason.READ_ERROR,
                        detail="无法读取目录名称。",
                    )
                )
                continue

            if not codes:
                skipped.append(ScanSkipped(source_path=child, reason=ScanSkipReason.NO_WORK_CODE))
                continue
            if len(codes) > 1:
                candidates.append(
                    ScanCandidate(
                        source_path=child,
                        work_code=None,
                        work_codes=tuple(codes),
                        status=ScanCandidateStatus.AMBIGUOUS_WORK_CODE,
                        error=f"目录名包含多个不同 RJcode：{'、'.join(codes)}。",
                    )
                )
                continue
            candidates.append(
                ScanCandidate(
                    source_path=child,
                    work_code=codes[0],
                    work_codes=(codes[0],),
                    status=ScanCandidateStatus.VALID,
                )
            )

        logger.info(
            "Scanned organizer root %s: %d candidates, %d skipped",
            root_path,
            len(candidates),
            len(skipped),
        )
        return ScanResult(root_path=root_path, candidates=tuple(candidates), skipped=tuple(skipped))

    @staticmethod
    def _skip_reason(path: Path) -> ScanSkipReason | None:
        try:
            if _is_link_like(path):
                return ScanSkipReason.SYMLINK
            if _is_hidden_or_system(path):
                return ScanSkipReason.HIDDEN
            if not path.is_dir():
                return ScanSkipReason.NOT_DIRECTORY
        except OSError:
            return ScanSkipReason.READ_ERROR
        return None


def _is_hidden_or_system(path: Path) -> bool:
    """Recognize dot-hidden entries and Windows hidden/system attributes."""
    if path.name.startswith("."):
        return True
    if os.name != "nt":
        return False
    try:
        attributes = ctypes.windll.kernel32.GetFileAttributesW(str(path))
    except (AttributeError, OSError):
        return False
    if attributes == -1:
        return False
    return bool(attributes & (0x2 | 0x4))  # FILE_ATTRIBUTE_HIDDEN | SYSTEM


def _is_link_like(path: Path) -> bool:
    """Reject symlink-like directories, including Windows junctions."""
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    return bool(is_junction is not None and is_junction())
