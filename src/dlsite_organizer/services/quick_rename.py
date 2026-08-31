"""Quick Rename orchestration using the existing safe organizer pipeline."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from dlsite_organizer.domain.organizer import RenamePlan, RenamePlanStatus
from dlsite_organizer.domain.rename_execution import RenameExecutionResult, TransactionStatus
from dlsite_organizer.services.drop_input import DropInputError, DropInputService
from dlsite_organizer.services.folder_scanner import FolderScanFailure
from dlsite_organizer.services.organizer import (
    OrganizerPreview,
    OrganizerService,
    WorkLookupStatus,
)
from dlsite_organizer.services.rename_executor import ExecutionProgressCallback, RenameExecutor

logger = logging.getLogger(__name__)


class QuickRenameStatus(StrEnum):
    """Stable result categories for the lightweight UI and callers."""

    SUCCESS = "success"
    NO_CHANGE = "no_change"
    INVALID_INPUT = "invalid_input"
    LOOKUP_FAILED = "lookup_failed"
    PLAN_REJECTED = "plan_rejected"
    EXECUTION_FAILED = "execution_failed"
    RECOVERY_REQUIRED = "recovery_required"


QuickProgressCallback = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class QuickRenameResult:
    """Typed, UI-independent outcome of one all-or-nothing quick batch."""

    status: QuickRenameStatus
    root_path: Path | None = None
    input_paths: tuple[Path, ...] = ()
    preview: OrganizerPreview | None = None
    execution: RenameExecutionResult | None = None
    error: str | None = None

    @property
    def plans(self) -> tuple[RenamePlan, ...]:
        return self.preview.plans if self.preview is not None else ()

    @property
    def transaction_id(self) -> str | None:
        return self.execution.transaction_id if self.execution is not None else None

    @property
    def mutated(self) -> bool:
        return self.execution is not None and self.execution.success_count > 0

    @property
    def summary(self) -> str:
        if self.status is QuickRenameStatus.SUCCESS:
            return "✓ 已重命名"
        if self.status is QuickRenameStatus.NO_CHANGE:
            return "无需重命名"
        if self.status is QuickRenameStatus.RECOVERY_REQUIRED:
            return "需要恢复"
        if self.mutated:
            return "部分操作已完成"
        return "未执行任何文件修改"


class QuickRenameService:
    """Validate, preview, and execute a drop through shared services only."""

    def __init__(
        self,
        organizer_service: OrganizerService,
        rename_executor: RenameExecutor,
        *,
        input_service: DropInputService | None = None,
    ) -> None:
        self._organizer_service = organizer_service
        self._rename_executor = rename_executor
        self._input_service = input_service or DropInputService()

    def rename(
        self,
        paths: Sequence[Path | str],
        *,
        progress_callback: QuickProgressCallback | None = None,
        execution_progress_callback: ExecutionProgressCallback | None = None,
    ) -> QuickRenameResult:
        """Run one validated batch; no mutation occurs before executor preflight/journal."""
        input_paths = tuple(Path(path).expanduser().absolute() for path in paths)
        logger.info("Quick Rename start work_count=%d", len(input_paths))
        try:
            selection = self._input_service.validate_work_folders(input_paths)
        except DropInputError as exc:
            logger.info("Quick Rename result=%s error=%s", QuickRenameStatus.INVALID_INPUT, exc)
            return QuickRenameResult(
                status=QuickRenameStatus.INVALID_INPUT,
                input_paths=input_paths,
                error=exc.user_message,
            )

        try:
            unresolved = self._rename_executor.unresolved_transaction()
        except Exception:
            logger.exception("Quick Rename could not inspect journal health")
            return QuickRenameResult(
                status=QuickRenameStatus.RECOVERY_REQUIRED,
                root_path=selection.root_path,
                input_paths=input_paths,
                error="最近一次重命名需要恢复，请先处理该事务。",
            )
        if unresolved is not None:
            return QuickRenameResult(
                status=QuickRenameStatus.RECOVERY_REQUIRED,
                root_path=selection.root_path,
                input_paths=input_paths,
                error="最近一次重命名需要恢复，请先处理该事务。",
            )
        if not self._rename_executor.available:
            return QuickRenameResult(
                status=QuickRenameStatus.EXECUTION_FAILED,
                root_path=selection.root_path,
                input_paths=input_paths,
                error="事务日志不可用，已禁止文件系统重命名。",
            )

        if progress_callback is not None:
            progress_callback(f"正在查询 {len(input_paths)} 个作品…")
        try:
            preview = self._organizer_service.preview_paths(
                selection.root_path,
                selection.selected_paths or (),
                progress_callback=_preview_progress(progress_callback),
            )
        except FolderScanFailure as exc:
            return QuickRenameResult(
                status=QuickRenameStatus.INVALID_INPUT,
                root_path=selection.root_path,
                input_paths=input_paths,
                error=exc.user_message,
            )
        if len(preview.plans) != len(input_paths):
            return QuickRenameResult(
                status=QuickRenameStatus.INVALID_INPUT,
                root_path=selection.root_path,
                input_paths=input_paths,
                preview=preview,
                error="输入目录在处理期间发生变化，未执行任何文件修改。",
            )
        if any(lookup.status is not WorkLookupStatus.SUCCESS for lookup in preview.lookups):
            result = QuickRenameResult(
                status=QuickRenameStatus.LOOKUP_FAILED,
                root_path=selection.root_path,
                input_paths=input_paths,
                preview=preview,
                error="至少一个作品的 metadata 查询失败，未执行任何文件修改。",
            )
            logger.info("Quick Rename result=%s", result.status)
            return result

        invalid_plans = tuple(
            plan
            for plan in preview.plans
            if plan.status not in {RenamePlanStatus.READY, RenamePlanStatus.UNCHANGED}
        )
        if invalid_plans:
            result = QuickRenameResult(
                status=QuickRenameStatus.PLAN_REJECTED,
                root_path=selection.root_path,
                input_paths=input_paths,
                preview=preview,
                error="至少一个重命名计划未通过验证，未执行任何文件修改。",
            )
            logger.info("Quick Rename result=%s", result.status)
            return result

        ready = tuple(plan for plan in preview.plans if plan.status is RenamePlanStatus.READY)
        if not ready:
            result = QuickRenameResult(
                status=QuickRenameStatus.NO_CHANGE,
                root_path=selection.root_path,
                input_paths=input_paths,
                preview=preview,
            )
            logger.info("Quick Rename result=%s", result.status)
            return result

        execution = self._rename_executor.execute(
            selection.root_path,
            ready,
            confirmed=True,
            progress_callback=execution_progress_callback,
        )
        status = (
            QuickRenameStatus.SUCCESS
            if execution.status is TransactionStatus.COMPLETED
            else QuickRenameStatus.RECOVERY_REQUIRED
            if execution.status is TransactionStatus.RECOVERY_REQUIRED
            else QuickRenameStatus.EXECUTION_FAILED
        )
        error = execution.error
        result = QuickRenameResult(
            status=status,
            root_path=selection.root_path,
            input_paths=input_paths,
            preview=preview,
            execution=execution,
            error=error,
        )
        logger.info(
            "Quick Rename result=%s transaction_id=%s success=%d",
            result.status,
            result.transaction_id,
            execution.success_count,
        )
        return result

    def quick_rename(
        self,
        paths: Sequence[Path | str],
        *,
        progress_callback: QuickProgressCallback | None = None,
        execution_progress_callback: ExecutionProgressCallback | None = None,
    ) -> QuickRenameResult:
        """Descriptive alias for callers that prefer the product operation name."""
        return self.rename(
            paths,
            progress_callback=progress_callback,
            execution_progress_callback=execution_progress_callback,
        )


def _preview_progress(callback: QuickProgressCallback | None):
    if callback is None:
        return None

    def report(completed: int, total: int, work_code: str) -> None:
        callback(f"正在查询 {completed + 1} / {total}：{work_code}")

    return report
