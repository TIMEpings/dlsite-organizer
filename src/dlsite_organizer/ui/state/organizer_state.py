"""Explicit Organizer presentation state; services retain all business authority."""

from __future__ import annotations

import ntpath
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from dlsite_organizer.domain.organizer import RenamePlan, RenamePlanStatus
from dlsite_organizer.domain.rename_execution import (
    ExecutionStatus,
    RenameExecutionResult,
    TransactionStatus,
    UndoResult,
)
from dlsite_organizer.services.drop_input import normalized_path_identity
from dlsite_organizer.services.organizer import OrganizerPreview, WorkLookup


class ScanMode(StrEnum):
    REPLACE = "replace"
    APPEND = "append"
    RECONCILE = "reconcile"


class BusyOperation(StrEnum):
    SCAN = "scan"
    EXECUTE = "execute"
    UNDO = "undo"


class PreviewStaleReason(StrEnum):
    SETTINGS_CHANGED = "settings_changed"
    FILESYSTEM_CHANGED = "filesystem_changed"
    CANCELLED = "cancelled"
    REFRESH_FAILED = "refresh_failed"


@dataclass
class OrganizerState:
    """One ordered service result plus identity-based presentation projections.

    Only the model's transitions should change collections/checks while attached
    to a view, so Qt notifications bracket changes. Page/controller owns lifecycle
    fields and supplies current service generation and mutation-gate snapshots.
    """

    input_root: str = ""
    preview: OrganizerPreview | None = field(default=None, init=False)
    row_keys: tuple[str, ...] = field(default=(), init=False)
    # Ordered canonical identity -> concrete path. Membership survives stale rows
    # and omitted/skipped members; only explicit scan/append/remove changes intent.
    review_scope: dict[str, Path] = field(default_factory=dict, init=False)
    _checked: set[str] = field(default_factory=set, init=False, repr=False)
    highlighted_keys: frozenset[str] = field(default_factory=frozenset, init=False)
    current_key: str | None = field(default=None, init=False)
    request_id: int = 0
    preview_revision: int = 0
    settings_generation: int = 0
    scan_mode: ScanMode = ScanMode.REPLACE
    busy: BusyOperation | None = None
    cancel_requested: bool = False
    stale_reason: PreviewStaleReason | None = field(default=None, init=False)
    executor_available: bool = False
    mutation_blocked: bool = True
    unresolved_transaction_id: str | None = None
    last_execution_result: RenameExecutionResult | None = None
    last_undo_result: UndoResult | None = None
    completed_targets: set[str] = field(default_factory=set, init=False)
    reconciliation_root: Path | None = field(default=None, init=False)
    reconciliation_pending: bool = field(default=False, init=False)

    def completed(self, row: int) -> bool:
        return (self.plans[row].status is RenamePlanStatus.UNCHANGED
                and self.row_keys[row] in self.completed_targets)

    def prepare_reconciliation(self, result: RenameExecutionResult) -> bool:
        """Capture only finalized, confirmed mutation facts in this review root."""
        transaction = result.transaction
        root = self.committed_preview_root
        if (transaction is None or transaction.unresolved or root is None
                or result.status not in {TransactionStatus.COMPLETED, TransactionStatus.PARTIAL,
                                         TransactionStatus.FAILED}
                or transaction.status is not result.status
                or transaction.operations != result.operations
                or normalized_path_identity(root) != normalized_path_identity(transaction.root)
                or any(op.status in {ExecutionStatus.PENDING, ExecutionStatus.RECOVERY_REQUIRED}
                       for op in result.operations)):
            return False
        root_key = normalized_path_identity(root)
        successful = {
            _source_identity(root, root_key, op.source_path): op.target_path
            for op in result.operations if op.status is ExecutionStatus.SUCCESS
        }
        self.review_scope = remap_review_scope(self.review_scope, root, root_key, successful)
        self.completed_targets.update(_source_identity(root, root_key, target)
                                      for target in successful.values())
        self.reconciliation_root = root
        self.reconciliation_pending = True
        return True

    @property
    def plans(self) -> tuple[RenamePlan, ...]:
        return self.preview.plans if self.preview is not None else ()

    @property
    def committed_preview_root(self) -> Path | None:
        return self.preview.root_path if self.preview is not None else None

    @property
    def stale(self) -> bool:
        return self.stale_reason is not None

    @property
    def preview_current(self) -> bool:
        return (
            self.preview is not None
            and not self.stale
            and not self.preview.cancelled
            and self.preview.settings_generation == self.settings_generation
        )

    @property
    def checks_editable(self) -> bool:
        return (self.preview_current and self.busy is None
                and self.unresolved_transaction_id is None)

    @property
    def checked_ready_count(self) -> int:
        # Collection transitions ensure only READY identities can be checked.
        return len(self._checked) if self.preview_current else 0

    @property
    def can_execute(self) -> bool:
        return (
            self.checks_editable
            and bool(self.committed_preview_root)
            and self.checked_ready_count > 0
            and self.executor_available
            and not self.mutation_blocked
        )

    def checked(self, row: int) -> bool:
        return self.row_keys[row] in self._checked

    def checked_ready_plans(self) -> tuple[RenamePlan, ...]:
        if not self.preview_current:
            return ()
        return tuple(
            plan for key, plan in zip(self.row_keys, self.plans, strict=True)
            if key in self._checked
        )

    def begin_scan(self, mode: ScanMode) -> None:
        self.request_id += 1
        self.scan_mode = mode
        self.cancel_requested = False
        self.busy = BusyOperation.SCAN

    def owns_result(self, request_id: int, preview_revision: int) -> bool:
        return request_id == self.request_id and preview_revision == self.preview_revision

    def finish_operation(self) -> None:
        self.busy = None
        self.scan_mode = ScanMode.REPLACE

    def replace_preview(self, preview: OrganizerPreview, *, reconcile: bool = False) -> None:
        if not reconcile:
            self._clear_review_session()
        keys = preview_row_keys(preview)
        self.preview = preview
        self.row_keys = keys
        if not reconcile:
            self.review_scope = dict(zip(keys, (p.source_path for p in preview.plans), strict=True))
        self._checked = {
            key for key, plan in zip(keys, preview.plans, strict=True)
            if plan.status is RenamePlanStatus.READY and not reconcile
        }
        self._commit_validity()
        self.clear_highlight()
        if reconcile:
            self.reconciliation_pending = False
            self.reconciliation_root = None

    def append_preview(self, preview: OrganizerPreview) -> None:
        """Commit a pre-merged ordered preview, preserving old READY checks only."""
        keys = preview_row_keys(preview)
        self.preview = preview
        self.row_keys = keys
        self.review_scope.update(zip(keys, (p.source_path for p in preview.plans), strict=True))
        self._checked.intersection_update(
            key for key, plan in zip(keys, preview.plans, strict=True)
            if plan.status is RenamePlanStatus.READY
        )
        self._commit_validity()
        self.clear_highlight()

    def _commit_validity(self) -> None:
        self.preview_revision += 1
        self.stale_reason = None
        assert self.preview is not None
        if self.preview.settings_generation != self.settings_generation:
            self.mark_stale(PreviewStaleReason.SETTINGS_CHANGED)
        elif self.preview.cancelled:
            self.mark_stale(PreviewStaleReason.CANCELLED)

    def set_checked(self, row: int, checked: bool) -> bool:
        if not self.checks_editable or self.plans[row].status is not RenamePlanStatus.READY:
            return False
        key = self.row_keys[row]
        if checked:
            self._checked.add(key)
        else:
            self._checked.discard(key)
        return True

    def project_highlight(self, rows: Sequence[int], current: int | None) -> None:
        self.highlighted_keys = frozenset(
            self.row_keys[row] for row in rows if 0 <= row < len(self.row_keys)
        )
        self.current_key = (
            self.row_keys[current] if current is not None and 0 <= current < len(self.row_keys)
            else None
        )

    def clear_highlight(self) -> None:
        self.highlighted_keys = frozenset()
        self.current_key = None

    def remove_rows(self, first: int, last: int) -> None:
        assert self.preview is not None
        self._checked.difference_update(self.row_keys[first:last + 1])
        for key in self.row_keys[first:last + 1]:
            self.review_scope.pop(key, None)
        self.row_keys = self.row_keys[:first] + self.row_keys[last + 1:]
        self.preview = replace(
            self.preview, plans=self.plans[:first] + self.plans[last + 1:]
        )
        self.clear_highlight()

    def mark_stale(self, reason: PreviewStaleReason) -> None:
        self.preview_revision += 1
        self.stale_reason = (
            reason if self.plans or self.busy is BusyOperation.SCAN
            or self.reconciliation_root is not None else None)

    def clear_preview(self) -> None:
        self.preview_revision += 1
        self.preview = None
        self.row_keys = ()
        self._checked.clear()
        self.stale_reason = None
        self.clear_highlight()
        self._clear_review_session()

    def _clear_review_session(self) -> None:
        self.review_scope.clear()
        self.completed_targets.clear()
        self.reconciliation_root = None
        self.reconciliation_pending = False


class _PathItem(Protocol):
    @property
    def source_path(self) -> Path: ...


def remap_review_scope(scope: dict[str, Path], root: Path, root_key: str,
                       successful: dict[str, Path]) -> dict[str, Path]:
    """Replace only confirmed successful members in place in a single linear pass."""
    mapped: dict[str, Path] = {}
    for key, path in scope.items():
        target = successful.get(key)
        mapped[_source_identity(root, root_key, target) if target is not None else key] = (
            target if target is not None else path)
    return mapped


def _source_identity(root: Path, root_key: str, source: Path) -> str:
    """Batch canonicalization for scanner-produced direct, non-link children.

    OrganizerService plans/candidates already exclude child links/reparse points.
    Resolve the shared parent once, then normalize the single child component.
    Unusual paths and possible DOS short-name aliases retain the legacy resolver.
    This identifies presentation rows; it does not validate mutation safety.
    """
    if (
        root.is_absolute() and source.parent == root
        and source.name not in ("", ".", "..")
        and "~" not in source.name and not source.name.endswith((".", " "))
    ):
        return ntpath.join(root_key, ntpath.normcase(source.name))
    return normalized_path_identity(source)


def preview_row_keys(preview: OrganizerPreview) -> tuple[str, ...]:
    root_key = normalized_path_identity(preview.root_path)
    return tuple(
        _source_identity(preview.root_path, root_key, plan.source_path) for plan in preview.plans
    )


def merge_previews(current: OrganizerPreview, incoming: OrganizerPreview) -> OrganizerPreview:
    """Merge service outputs without re-planning or changing service semantics."""
    if current.settings_generation != incoming.settings_generation:
        raise ValueError("Cannot merge previews from different settings generations")
    root_key = normalized_path_identity(current.root_path)
    if root_key != normalized_path_identity(incoming.root_path):
        raise ValueError("Cannot merge previews from different roots")
    # Both spellings of the parent have the scanner's non-link child contract.
    def identity(source: Path) -> str:
        root = incoming.root_path if source.parent == incoming.root_path else current.root_path
        return _source_identity(root, root_key, source)

    return replace(
        current,
        scan=replace(
            current.scan,
            candidates=_merge_path_items(
                current.scan.candidates, incoming.scan.candidates, identity=identity
            ),
            skipped=_merge_path_items(current.scan.skipped, incoming.scan.skipped),
            cancelled=False,
        ),
        lookups=_merge_lookup_items(current.lookups, incoming.lookups),
        plans=_merge_path_items(current.plans, incoming.plans, identity=identity),
        cancelled=False,
    )


def _merge_path_items[PathItemT: _PathItem](
    existing: Sequence[PathItemT], incoming: Sequence[PathItemT],
    *, identity: Callable[[Path], str] = normalized_path_identity,
) -> tuple[PathItemT, ...]:
    merged = list(existing)
    positions = {identity(item.source_path): i for i, item in enumerate(merged)}
    for item in incoming:
        key = identity(item.source_path)
        index = positions.get(key)
        if index is None:
            positions[key] = len(merged)
            merged.append(item)
        else:
            merged[index] = item
    return tuple(merged)


def _merge_lookup_items(
    existing: Sequence[WorkLookup], incoming: Sequence[WorkLookup]
) -> tuple[WorkLookup, ...]:
    merged = list(existing)
    positions = {item.work_code: i for i, item in enumerate(merged)}
    for item in incoming:
        index = positions.get(item.work_code)
        if index is None:
            positions[item.work_code] = len(merged)
            merged.append(item)
        else:
            merged[index] = item
    return tuple(merged)
