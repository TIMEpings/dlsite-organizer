"""Create and validate rename previews without changing the filesystem."""

from __future__ import annotations

import logging
import ntpath
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path, PureWindowsPath
from typing import Any

from dlsite_organizer.domain.organizer import (
    RenamePlan,
    RenamePlanStatus,
    ScanCandidate,
    ScanCandidateStatus,
)
from dlsite_organizer.domain.work import Work
from dlsite_organizer.domain.work_code import WorkCodeError, normalize_rjcode
from dlsite_organizer.services.naming import NamingService

logger = logging.getLogger(__name__)

# Windows Explorer and many Win32 APIs become risky before MAX_PATH itself.
WINDOWS_PATH_WARNING_THRESHOLD = 240


class RenamePlanner:
    """Build safe, reviewable plans and never perform a filesystem mutation."""

    def __init__(
        self,
        naming: NamingService | None = None,
        *,
        case_insensitive: bool = True,
        path_warning_threshold: int = WINDOWS_PATH_WARNING_THRESHOLD,
    ) -> None:
        self._naming = naming or NamingService()
        self._case_insensitive = case_insensitive
        self._path_warning_threshold = path_warning_threshold

    def apply_settings(self, settings: Any) -> None:
        """Update the shared naming renderer for subsequent plans."""
        self._naming.apply_settings(settings)

    def plan(
        self,
        root_path: Path | str,
        source_path: Path | str,
        work_code: str,
        work: Work,
        *,
        formatted_name: str | None = None,
    ) -> RenamePlan:
        """Build one plan from a validated RJcode and normalized Work.

        ``formatted_name`` is accepted from ``LookupResult`` so the organizer
        reuses the existing NamingService pipeline.  Direct callers can omit it
        and let this planner apply its configured NamingService.
        """
        root = _resolved_path(root_path)
        source = Path(source_path)
        current_name = source.name
        try:
            normalized_code = normalize_rjcode(work_code)
        except WorkCodeError as exc:
            return RenamePlan(
                source_path=source,
                current_name=current_name,
                work_code=None,
                work=work,
                proposed_name=None,
                target_path=None,
                status=RenamePlanStatus.INVALID_CODE,
                error=str(exc),
            )

        if normalized_code != work.workno:
            return RenamePlan(
                source_path=source,
                current_name=current_name,
                work_code=normalized_code,
                work=work,
                proposed_name=None,
                target_path=None,
                status=RenamePlanStatus.INVALID_CODE,
                error="目录 RJcode 与查询到的作品编号不一致。",
                work_codes=(normalized_code,),
            )

        if not _is_within(source, root, case_insensitive=self._case_insensitive):
            return self._invalid_target_plan(
                source,
                normalized_code,
                work,
                "源目录不在所选作品根目录内。",
            )

        try:
            proposed = formatted_name if formatted_name is not None else self._naming.format(work)
        except Exception as exc:
            logger.warning("Could not format organizer name for %s: %s", normalized_code, exc)
            return self._invalid_target_plan(
                source,
                normalized_code,
                work,
                "无法生成安全的目标目录名。",
            )

        if not _is_safe_name_component(proposed):
            return self._invalid_target_plan(
                source,
                normalized_code,
                work,
                "生成的目标目录名不是安全的单一文件名。",
                proposed_name=proposed,
            )

        target = root / proposed
        if not _is_within(target, root, case_insensitive=self._case_insensitive):
            return self._invalid_target_plan(
                source,
                normalized_code,
                work,
                "目标目录不在所选作品根目录内。",
                proposed_name=proposed,
                target_path=target,
            )

        warnings = _path_warnings(target, self._path_warning_threshold)
        try:
            existing_children = tuple(root.iterdir())
        except OSError as exc:
            logger.warning("Cannot inspect rename target under %s: %s", root, exc)
            return RenamePlan(
                source_path=source,
                current_name=current_name,
                work_code=normalized_code,
                work=work,
                proposed_name=proposed,
                target_path=target,
                status=RenamePlanStatus.CONFLICT,
                warnings=(*warnings, "无法确认目标目录是否已存在，因此不会标记为 READY。"),
                error="无法检查目标目录是否已存在。",
                work_codes=(normalized_code,),
            )

        matching_children = [
            child
            for child in existing_children
            if _same_name(child.name, proposed, case_insensitive=self._case_insensitive)
        ]
        source_identity = any(
            _same_path(child, source, case_insensitive=self._case_insensitive)
            for child in matching_children
        )
        if matching_children and not source_identity:
            return RenamePlan(
                source_path=source,
                current_name=current_name,
                work_code=normalized_code,
                work=work,
                proposed_name=proposed,
                target_path=target,
                status=RenamePlanStatus.CONFLICT,
                warnings=warnings,
                error="目标目录已存在，未生成可执行的重命名计划。",
                work_codes=(normalized_code,),
            )

        status = (
            RenamePlanStatus.UNCHANGED
            if _same_name(current_name, proposed, case_insensitive=self._case_insensitive)
            else RenamePlanStatus.READY
        )
        return RenamePlan(
            source_path=source,
            current_name=current_name,
            work_code=normalized_code,
            work=work,
            proposed_name=proposed,
            target_path=target,
            status=status,
            warnings=warnings,
            work_codes=(normalized_code,),
        )

    def plan_scan_issue(self, root_path: Path | str, candidate: ScanCandidate) -> RenamePlan:
        """Represent a scanner warning as a visible, non-renameable plan row."""
        status = (
            RenamePlanStatus.AMBIGUOUS_CODE
            if candidate.status is ScanCandidateStatus.AMBIGUOUS_WORK_CODE
            else RenamePlanStatus.INVALID_CODE
        )
        return RenamePlan(
            source_path=candidate.source_path,
            current_name=candidate.current_name,
            work_code=candidate.work_code,
            work=None,
            proposed_name=None,
            target_path=None,
            status=status,
            error=candidate.error or "无法从目录名确定唯一的 RJcode。",
            work_codes=candidate.work_codes,
        )

    def finalize(self, plans: Iterable[RenamePlan]) -> tuple[RenamePlan, ...]:
        """Mark every plan involved in a same-target collision as CONFLICT."""
        ordered = list(plans)
        by_target: dict[str, list[int]] = {}
        for index, plan in enumerate(ordered):
            if plan.target_path is None or plan.status not in {
                RenamePlanStatus.READY,
                RenamePlanStatus.UNCHANGED,
            }:
                continue
            key = _path_key(plan.target_path, case_insensitive=self._case_insensitive)
            by_target.setdefault(key, []).append(index)

        for indexes in by_target.values():
            if len(indexes) < 2:
                continue
            for index in indexes:
                plan = ordered[index]
                warning = "本轮扫描中的多个目录计划使用同一个目标目录。"
                ordered[index] = replace(
                    plan,
                    status=RenamePlanStatus.CONFLICT,
                    warnings=(*plan.warnings, warning),
                    error=plan.error or "目标目录与本轮其他计划重复。",
                )
        return tuple(ordered)

    @staticmethod
    def _invalid_target_plan(
        source: Path,
        work_code: str,
        work: Work,
        error: str,
        *,
        proposed_name: str | None = None,
        target_path: Path | None = None,
    ) -> RenamePlan:
        return RenamePlan(
            source_path=source,
            current_name=source.name,
            work_code=work_code,
            work=work,
            proposed_name=proposed_name,
            target_path=target_path,
            status=RenamePlanStatus.INVALID_TARGET,
            error=error,
            work_codes=(work_code,),
        )


def _resolved_path(path: Path | str) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _path_key(path: Path, *, case_insensitive: bool) -> str:
    value = os_path_absolute(path)
    return value.casefold() if case_insensitive else value


def os_path_absolute(path: Path) -> str:
    """Return a normalized absolute string without touching filesystem state."""
    return ntpath.normpath(str(path.resolve(strict=False)))


def _is_within(path: Path, root: Path, *, case_insensitive: bool) -> bool:
    path_value = _path_key(path, case_insensitive=case_insensitive)
    root_value = _path_key(root, case_insensitive=case_insensitive)
    try:
        return ntpath.commonpath([path_value, root_value]) == root_value
    except ValueError:
        return False


def _same_path(left: Path, right: Path, *, case_insensitive: bool) -> bool:
    return _path_key(left, case_insensitive=case_insensitive) == _path_key(
        right,
        case_insensitive=case_insensitive,
    )


def _same_name(left: str, right: str, *, case_insensitive: bool) -> bool:
    return left.casefold() == right.casefold() if case_insensitive else left == right


def _is_safe_name_component(value: str) -> bool:
    if not isinstance(value, str) or not value or value in {".", ".."}:
        return False
    if "/" in value or "\\" in value:
        return False
    return not PureWindowsPath(value).is_absolute() and not ntpath.isabs(value)


def _path_warnings(target_path: Path, threshold: int) -> tuple[str, ...]:
    if len(str(target_path)) <= threshold:
        return ()
    return (f"目标路径长度为 {len(str(target_path))}，超过保守阈值 {threshold}。",)
