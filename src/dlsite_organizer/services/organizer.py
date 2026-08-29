"""Application orchestration for scan → lookup → rename preview."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from dlsite_organizer.domain.organizer import (
    RenamePlan,
    RenamePlanStatus,
    ScanCandidateStatus,
    ScanResult,
)
from dlsite_organizer.domain.work import Work
from dlsite_organizer.services.folder_scanner import FolderScanner
from dlsite_organizer.services.lookup import LookupFailure, LookupFreshness, LookupResult
from dlsite_organizer.services.rename_planner import RenamePlanner

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, str], None]
CancelCheck = Callable[[], bool]


class OrganizerLookupService(Protocol):
    """The small lookup boundary needed by the organizer batch."""

    def lookup(self, raw_workno: str) -> LookupResult:
        """Return one normalized lookup result or raise LookupFailure."""
        ...


class WorkLookupStatus(StrEnum):
    """Outcome for one deduplicated metadata request in a batch."""

    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class WorkLookup:
    """The metadata result for one RJcode, or a safe failure description."""

    work_code: str
    status: WorkLookupStatus
    result: LookupResult | None = None
    error: str | None = None

    @property
    def work(self) -> Work | None:
        return self.result.work if self.result is not None else None

    @property
    def formatted_name(self) -> str | None:
        return self.result.formatted_name if self.result is not None else None


@dataclass(frozen=True, slots=True)
class OrganizerPreview:
    """Complete read-only output for one organizer run."""

    root_path: Path
    scan: ScanResult
    lookups: tuple[WorkLookup, ...]
    plans: tuple[RenamePlan, ...]
    cancelled: bool = False

    @property
    def skipped_count(self) -> int:
        return self.scan.skipped_count

    def count(self, status: RenamePlanStatus) -> int:
        """Count plan rows by status for the GUI summary."""
        return sum(plan.status is status for plan in self.plans)


class OrganizerService:
    """Coordinate a bounded, sequential metadata lookup batch."""

    def __init__(
        self,
        lookup_service: OrganizerLookupService,
        scanner: FolderScanner | None = None,
        planner: RenamePlanner | None = None,
    ) -> None:
        self._lookup_service = lookup_service
        self._scanner = scanner or FolderScanner()
        self._planner = planner or RenamePlanner()

    def preview(
        self,
        root_path: Path | str,
        *,
        progress_callback: ProgressCallback | None = None,
        cancel_check: CancelCheck | None = None,
    ) -> OrganizerPreview:
        """Scan a root and build plans while preserving every source row.

        Work codes are looked up sequentially and only once per run.  A failed
        lookup becomes a single failed row; it does not abort other works.
        """
        scan = self._scanner.scan(root_path)
        cancel_check = cancel_check or (lambda: False)
        valid_candidates = [
            candidate
            for candidate in scan.candidates
            if candidate.status is ScanCandidateStatus.VALID and candidate.work_code is not None
        ]
        work_codes: list[str] = []
        for candidate in valid_candidates:
            work_code = candidate.work_code
            if work_code is not None and work_code not in work_codes:
                work_codes.append(work_code)
        lookup_by_code: dict[str, WorkLookup] = {}
        cancelled = False
        completed = 0

        logger.info(
            "Preparing organizer preview for %s: %d unique RJcodes",
            scan.root_path,
            len(work_codes),
        )
        for work_code in work_codes:
            if cancel_check():
                cancelled = True
                break
            if progress_callback is not None:
                progress_callback(completed, len(work_codes), work_code)
            if cancel_check():
                cancelled = True
                break
            try:
                result = self._lookup_service.lookup(work_code)
            except LookupFailure as exc:
                lookup_by_code[work_code] = WorkLookup(
                    work_code=work_code,
                    status=WorkLookupStatus.FAILED,
                    error=exc.user_message,
                )
                logger.warning("Organizer lookup failed for %s: %s", work_code, exc.user_message)
            except Exception:
                logger.exception("Unexpected organizer lookup failure for %s", work_code)
                lookup_by_code[work_code] = WorkLookup(
                    work_code=work_code,
                    status=WorkLookupStatus.FAILED,
                    error="查询时发生意外错误，详细信息已写入日志。",
                )
            else:
                lookup_by_code[work_code] = WorkLookup(
                    work_code=work_code,
                    status=WorkLookupStatus.SUCCESS,
                    result=result,
                )
            completed += 1

        if completed < len(work_codes):
            cancelled = True
            for work_code in work_codes[completed:]:
                lookup_by_code[work_code] = WorkLookup(
                    work_code=work_code,
                    status=WorkLookupStatus.CANCELLED,
                    error="已取消，尚未开始查询。",
                )

        lookups = tuple(lookup_by_code[work_code] for work_code in work_codes)
        plans: list[RenamePlan] = []
        for candidate in scan.candidates:
            if candidate.status is ScanCandidateStatus.AMBIGUOUS_WORK_CODE:
                plans.append(self._planner.plan_scan_issue(scan.root_path, candidate))
                continue
            if candidate.work_code is None:
                plans.append(self._planner.plan_scan_issue(scan.root_path, candidate))
                continue

            work_code = candidate.work_code
            assert work_code is not None
            lookup = lookup_by_code[work_code]
            if lookup.status is WorkLookupStatus.SUCCESS and lookup.result is not None:
                plan = self._planner.plan(
                    scan.root_path,
                    candidate.source_path,
                    work_code,
                    lookup.result.work,
                    formatted_name=lookup.result.formatted_name,
                )
                if lookup.result.freshness is LookupFreshness.CACHE_STALE_FALLBACK:
                    plan = replace(
                        plan,
                        warnings=(
                            *plan.warnings,
                            "使用旧缓存 metadata：本次未能取得新的 DLsite 响应。",
                        ),
                    )
                plans.append(plan)
            else:
                status = (
                    RenamePlanStatus.CANCELLED
                    if lookup.status is WorkLookupStatus.CANCELLED
                    else RenamePlanStatus.LOOKUP_FAILED
                )
                plans.append(
                    RenamePlan(
                        source_path=candidate.source_path,
                        current_name=candidate.current_name,
                        work_code=work_code,
                        work=None,
                        proposed_name=None,
                        target_path=None,
                        status=status,
                        error=lookup.error,
                        work_codes=(work_code,),
                    )
                )

        finalized = self._planner.finalize(plans)
        logger.info(
            "Organizer preview ready for %s: %d plans, %d skipped, %d lookup failures",
            scan.root_path,
            len(finalized),
            scan.skipped_count,
            sum(plan.status is RenamePlanStatus.LOOKUP_FAILED for plan in finalized),
        )
        return OrganizerPreview(
            root_path=scan.root_path,
            scan=scan,
            lookups=lookups,
            plans=finalized,
            cancelled=cancelled,
        )
