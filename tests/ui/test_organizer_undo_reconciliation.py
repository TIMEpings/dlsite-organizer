"""Offline Undo result characterization and scoped UI reconciliation contracts."""

from dataclasses import replace
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtWidgets import QMessageBox
from tests.services.test_undo_service import FailOnceFilesystem
from tests.ui.test_organizer_page import _wait_for, _wait_for_scan_idle
from tests.ui.test_organizer_reconciliation import checked_codes

from dlsite_organizer.domain.organizer import RenamePlanStatus
from dlsite_organizer.domain.rename_execution import TransactionStatus, UndoStatus
from dlsite_organizer.services.drop_input import normalized_path_identity
from dlsite_organizer.services.undo_service import UndoService
from dlsite_organizer.ui.state.organizer_state import PreviewStaleReason
from dlsite_organizer.ui.workers.organizer_worker import OrganizerWorkerResult

pytest_plugins = ["tests.ui.test_organizer_reconciliation"]


def test_undo_refreshes_preserved_scope_and_removes_completed(qapp, review, monkeypatch):
    page, service, executor, journal, root, _lookup = review
    original = tuple(page.state.review_scope.values())
    page.state.project_highlight([3], 3)
    page.remove_selected()
    result = executor.execute(root, page.selected_ready_plans()[:2], confirmed=True)
    page._show_execution_result(result)
    _wait_for_scan_idle(qapp, page)
    assert len(page.state.review_scope) == 3
    assert sum(page.state.completed(i) for i in range(3)) == 2
    request = page.state.request_id

    def no_discovery(*args, **kwargs):
        pytest.fail("Undo reconciliation must not rediscover root membership")

    monkeypatch.setattr(service, "preview", no_discovery)
    undone = UndoService(journal).undo(result.transaction_id, confirmed=True)
    assert undone.status is TransactionStatus.UNDONE
    assert undone.transaction is not None and not undone.transaction.unresolved
    assert undone.transaction.root == root
    assert undone.operations == undone.transaction.operations
    for forward, reverse in zip(result.operations, undone.operations, strict=True):
        assert reverse.source_path == forward.source_path
        assert reverse.target_path == forward.target_path
        assert reverse.undo_status is UndoStatus.SUCCESS and reverse.undone_at is not None
        assert reverse.source_path.is_dir() and not reverse.target_path.exists()
    page._show_undo_result(undone)
    _wait_for_scan_idle(qapp, page)
    assert not page.state.stale and page.state.request_id == request + 1
    assert tuple(page.state.review_scope.values()) == original[:3]
    assert tuple(p.source_path for p in page.state.plans) == original[:3]
    assert not page.state.completed_targets
    assert original[3] not in page.state.review_scope.values()
    assert page.state.reconciliation_root is None and not page.execute_button.isEnabled()


def test_different_root_undo_preserves_current_preview(qapp, review):
    page, service, executor, journal, root, _lookup = review
    other = root.parent / "other"
    other.mkdir()
    (other / "old RJ000006").mkdir()
    result = executor.execute(other, service.preview(other).plans, confirmed=True)
    undone = UndoService(journal).undo(result.transaction_id, confirmed=True)
    preview, scope, request = page.preview, dict(page.state.review_scope), page.state.request_id
    page._show_undo_result(undone)
    assert not page.state.stale and page.preview is preview
    assert page.state.review_scope == scope and page.state.request_id == request


def test_result_contract_partial_resume_and_preflight_rejection(qapp, review):
    page, _service, executor, journal, root, _lookup = review
    result = executor.execute(root, page.selected_ready_plans()[:2], confirmed=True)
    service = UndoService(
        journal, filesystem=FailOnceFilesystem(result.operations[0].target_path.name)
    )
    partial = service.undo(result.transaction_id, confirmed=True)
    assert partial.status is TransactionStatus.UNDO_PARTIAL
    assert partial.transaction is not None and not partial.transaction.unresolved
    assert partial.operations == partial.transaction.operations
    assert [op.undo_status for op in partial.operations] == [UndoStatus.FAILED, UndoStatus.SUCCESS]
    assert partial.success_count == partial.failed_count == partial.pending_count == 1
    # Earlier successes remain in a FAILED snapshot: counts aren't current-attempt mutation facts.
    rejected = service.undo(result.transaction_id, confirmed=False)
    assert rejected.status is TransactionStatus.FAILED and rejected.success_count == 1
    resumed = service.undo(result.transaction_id, confirmed=True)
    assert resumed.status is TransactionStatus.UNDONE and resumed.success_count == 2
    assert journal.latest_undoable() is None


def execute_and_refresh(qapp, review, codes):
    page, _service, executor, _journal, root, _lookup = review
    checked_codes(page, codes)
    result = executor.execute(root, page.selected_ready_plans(), confirmed=True)
    page._show_execution_result(result)
    _wait_for_scan_idle(qapp, page)
    return result


def test_partial_undo_maps_only_success_and_resume_is_idempotent(qapp, review):
    page, _service, _executor, journal, _root, _lookup = review
    result = execute_and_refresh(qapp, review, {"RJ000001", "RJ000002"})
    before = tuple(page.state.review_scope.values())
    undo = UndoService(
        journal, filesystem=FailOnceFilesystem(result.operations[0].target_path.name)
    )
    partial = undo.undo(result.transaction_id, confirmed=True)
    page._show_undo_result(partial)
    _wait_for_scan_idle(qapp, page)
    assert tuple(page.state.review_scope.values()) == (
        before[0],
        result.operations[1].source_path,
        *before[2:],
    )
    assert page.state.completed(0) and not page.state.completed(1)
    assert page.state.plans[1].status is RenamePlanStatus.READY
    assert not page.state.stale and page.undo_button.isEnabled()
    assert "剩余可撤销 1 项" in page.status_label.text()
    request, preview = page.state.request_id, page.preview
    rejected = undo.undo(result.transaction_id, confirmed=False)
    page._show_undo_result(rejected)
    assert page.state.request_id == request and page.preview is preview and not page.state.stale
    page._show_undo_result(undo.undo(result.transaction_id, confirmed=True))
    _wait_for_scan_idle(qapp, page)
    assert tuple(page.state.review_scope.values())[:2] == tuple(
        op.source_path for op in result.operations
    )
    assert not page.state.completed_targets


def test_latest_transaction_only_completed_and_hidden_page_input(qapp, review, monkeypatch):
    page, service, _executor, journal, root, _lookup = review
    first = execute_and_refresh(qapp, review, {"RJ000001"})
    second = execute_and_refresh(qapp, review, {"RJ000002"})
    assert journal.latest_undoable().transaction_id == second.transaction_id
    service.entered, service.release = Event(), Event()
    page.set_root_path(root.parent / "next-scan")
    monkeypatch.setattr(QMessageBox, "exec", lambda _dialog: QMessageBox.StandardButton.Yes)
    page.undo_button.click()  # real Undo action worker -> scan worker chain
    _wait_for(qapp, service.entered.is_set)
    assert page.is_busy() and not page.execute_button.isEnabled()
    assert not page.state.checks_editable
    assert page.status_banner.title_label.text() == "撤销完成，正在刷新预览…"
    assert "撤销前的预览" in page.status_banner.message_label.text()
    page.hide()
    request = page.state.request_id
    service.release.set()
    _wait_for_scan_idle(qapp, page)
    page.show()
    assert page.state.request_id == request and not page.state.stale
    assert page.state.completed(0) and not page.state.completed(1)
    assert page.state.completed_targets == {
        normalized_path_identity(first.operations[0].target_path)
    }
    assert page.root_input.text().endswith("next-scan")
    assert journal.latest_undoable().transaction_id == first.transaction_id
    assert "可撤销：1" in page.recent_transaction_label.text()


def test_off_scope_undo_refreshes_without_reintroducing_and_removes_overlay(
    qapp, review, monkeypatch
):
    page, service, _executor, journal, root, _lookup = review
    result = execute_and_refresh(qapp, review, {"RJ000001"})
    page.state.project_highlight([0], 0)
    page.remove_selected()
    scope = tuple(page.state.review_scope.values())
    calls = []
    refresh = service.refresh_preview_scope

    def scoped(root_path, paths, **kwargs):
        calls.append((Path(root_path), tuple(paths)))
        return refresh(root_path, paths, **kwargs)

    monkeypatch.setattr(service, "refresh_preview_scope", scoped)
    page._show_undo_result(UndoService(journal).undo(result.transaction_id, confirmed=True))
    _wait_for_scan_idle(qapp, page)
    assert calls == [(root, scope)]
    assert tuple(page.state.review_scope.values()) == scope
    assert tuple(p.source_path for p in page.state.plans) == scope
    assert not page.state.completed_targets and result.operations[0].source_path.is_dir()


@pytest.mark.parametrize("empty", ["clear", "remove"])
def test_no_preview_or_empty_scope_does_not_refresh(qapp, review, empty):
    page, _service, _executor, journal, _root, _lookup = review
    result = execute_and_refresh(qapp, review, {"RJ000001"})
    if empty == "clear":
        page.clear_preview()
    else:
        page.state.project_highlight(list(range(4)), 0)
        page.remove_selected()
    request = page.state.request_id
    page._show_undo_result(UndoService(journal).undo(result.transaction_id, confirmed=True))
    assert not page.state.review_scope and not page.state.completed_targets
    assert page.state.request_id == request and not page.is_busy()
    assert not page.undo_button.isEnabled()


@pytest.mark.parametrize("partial", [False, True])
def test_undo_refresh_failure_retry_preserves_truth_and_scope(qapp, review, partial):
    page, service, _executor, journal, root, _lookup = review
    page.state.project_highlight([3], 3)
    page.remove_selected()
    result = execute_and_refresh(qapp, review, {"RJ000001", "RJ000002"})
    original = page.preview
    undo = UndoService(
        journal,
        filesystem=FailOnceFilesystem(result.operations[0].target_path.name) if partial else None,
    )
    undone = undo.undo(result.transaction_id, confirmed=True)
    service.fail = True
    page._show_undo_result(undone)
    _wait_for_scan_idle(qapp, page)
    scope = tuple(page.state.review_scope.values())
    assert page.preview is original and page.state.last_undo_result is undone
    assert journal.get_transaction(result.transaction_id).operations == undone.operations
    assert page.state.stale_reason is PreviewStaleReason.REFRESH_FAILED
    assert not page.execute_button.isEnabled() and not page.state.checks_editable
    assert (
        page.retry_refresh_button.isEnabled()
        and "刷新失败" in page.status_banner.title_label.text()
    )
    assert (
        "撤销未全部完成" in page.status_banner.title_label.text()
        if partial
        else ("撤销已完成" in page.status_banner.title_label.text())
    )
    assert len(page.state.completed_targets) == int(partial)
    assert ("本轮已完成 1" in page.summary_label.text()) == partial
    service.fail = False
    page.retry_refresh_button.click()
    _wait_for_scan_idle(qapp, page)
    assert tuple(p.source_path for p in page.state.plans) == scope
    assert len(scope) == 3 and not page.state.stale
    assert page.state.completed(0) == partial
    page.set_root_path(root)
    page.scan_button.click()
    _wait_for_scan_idle(qapp, page)
    assert len(page.state.plans) == 4 and not page.state.completed_targets


def test_undo_preflight_failure_preserves_preview_and_forward_retry(qapp, review):
    page, _service, _executor, journal, root, _lookup = review
    result = execute_and_refresh(qapp, review, {"RJ000001"})
    result.operations[0].source_path.mkdir()  # Real occupancy rejects before Undo mutation.
    preview, request, scope = page.preview, page.state.request_id, dict(page.state.review_scope)
    completed = set(page.state.completed_targets)
    undone = UndoService(journal).undo(result.transaction_id, confirmed=True)
    assert undone.status is TransactionStatus.FAILED and undone.success_count == 0
    page._show_undo_result(undone)
    assert page.preview is preview and not page.state.stale and page.state.request_id == request
    assert page.state.review_scope == scope and page.state.completed_targets == completed
    # An unrelated failed attempt must not cancel an existing forward-refresh retry.
    page.state.reconciliation_root = root
    page.state.mark_stale(PreviewStaleReason.REFRESH_FAILED)
    page._show_undo_result(undone)
    assert page.state.reconciliation_root == root and page.retry_refresh_button.isEnabled()


def test_undo_recovery_remains_journal_first(qapp, review, monkeypatch):
    page, _service, _executor, journal, _root, _lookup = review
    result = execute_and_refresh(qapp, review, {"RJ000001", "RJ000002"})
    scope, completed, request = (
        dict(page.state.review_scope),
        set(page.state.completed_targets),
        page.state.request_id,
    )

    def fail(*args):
        raise OSError("controlled undo journal failure")

    monkeypatch.setattr(journal, "mark_undo_success", fail)
    undone = UndoService(journal).undo(result.transaction_id, confirmed=True)
    assert undone.status is TransactionStatus.RECOVERY_REQUIRED
    page._show_undo_result(undone)
    assert page.state.request_id == request and not page.is_busy()
    assert page.state.review_scope == scope and page.state.completed_targets == completed
    assert page.state.stale and page.state.unresolved_transaction_id == result.transaction_id
    assert page.status_banner.title_label.text() == "存在未解决的重命名事务"
    assert not page.execute_button.isEnabled() and page.retry_refresh_button.isHidden()


@pytest.mark.parametrize("obsolete", ["settings", "revision", "request", "cancel"])
def test_undo_refresh_ownership_and_cancel_reject_old_truth(qapp, review, obsolete):
    page, service, _executor, journal, root, _lookup = review
    result = execute_and_refresh(qapp, review, {"RJ000001"})
    preview = page.preview
    service.entered, service.release = Event(), Event()
    page._show_undo_result(UndoService(journal).undo(result.transaction_id, confirmed=True))
    _wait_for(qapp, service.entered.is_set)
    request, revision = page.state.request_id, page.state.preview_revision
    if obsolete == "settings":
        service.generation += 1
        page.invalidate_preview()
    elif obsolete == "revision":
        page.preview_model.mark_stale(PreviewStaleReason.FILESYSTEM_CHANGED)
    elif obsolete == "request":
        page.state.request_id += 1
    else:
        page.cancel_scan()
    service.release.set()
    _wait_for_scan_idle(qapp, page)
    assert page.preview is preview and page.state.stale
    assert page.state.reconciliation_root == root and page.retry_refresh_button.isEnabled()
    assert not page.state.completed_targets
    # Even a delayed result from the cancelled/obsolete request cannot commit later.
    fresh = service.refresh_preview_scope(root, tuple(page.state.review_scope.values()))
    page._show_preview(OrganizerWorkerResult(request, revision, fresh))
    assert page.preview is preview


def test_mismatched_undo_snapshot_is_not_reconciled(qapp, review):
    page, _service, _executor, journal, _root, _lookup = review
    result = execute_and_refresh(qapp, review, {"RJ000001"})
    undone = UndoService(journal).undo(result.transaction_id, confirmed=True)
    scope, request = dict(page.state.review_scope), page.state.request_id
    page._show_undo_result(replace(undone, operations=()))
    assert page.state.request_id == request and page.state.review_scope == scope


def test_same_root_off_scope_restoration_changes_real_sibling_conflict(qapp, review):
    page, service, executor, journal, root, _lookup = review
    a = page.state.plans[0]
    assert a.target_path is not None
    # D occupies A's desired target, but is intentionally outside the review.
    d = a.target_path
    d.mkdir()
    d_plan = replace(
        a,
        source_path=d,
        current_name=d.name,
        target_path=root / "moved RJ000006",
        proposed_name="moved RJ000006",
    )
    execution = executor.execute(root, (d_plan,), confirmed=True)
    assert execution.status is TransactionStatus.COMPLETED
    page.set_preview(service.preview_paths(root, (a.source_path,)))
    assert page.state.plans[0].status is RenamePlanStatus.READY
    request = page.state.request_id
    page._show_undo_result(UndoService(journal).undo(execution.transaction_id, confirmed=True))
    _wait_for_scan_idle(qapp, page)
    assert page.state.request_id == request + 1
    assert tuple(page.state.review_scope.values()) == (a.source_path,)
    assert page.state.plans[0].status is RenamePlanStatus.CONFLICT
    assert d.is_dir() and not page.state.completed_targets


def test_first_reverse_failure_without_mutation_preserves_preview(qapp, review):
    page, _service, _executor, journal, _root, _lookup = review
    execution = execute_and_refresh(qapp, review, {"RJ000001"})
    undo = UndoService(
        journal, filesystem=FailOnceFilesystem(execution.operations[0].target_path.name)
    )
    result = undo.undo(execution.transaction_id, confirmed=True)
    assert result.status is TransactionStatus.UNDO_PARTIAL and result.success_count == 0
    preview, request, completed = (
        page.preview,
        page.state.request_id,
        set(page.state.completed_targets),
    )
    page._show_undo_result(result)
    assert page.preview is preview and not page.state.stale and page.state.request_id == request
    assert page.state.completed_targets == completed


def test_resumed_partial_first_failure_does_not_refresh_old_successes(qapp, review, monkeypatch):
    page, _service, _executor, journal, _root, _lookup = review
    execution = execute_and_refresh(qapp, review, {"RJ000001", "RJ000002"})
    name = execution.operations[0].target_path.name
    first = UndoService(journal, filesystem=FailOnceFilesystem(name)).undo(
        execution.transaction_id, confirmed=True
    )
    page._show_undo_result(first)
    _wait_for_scan_idle(qapp, page)
    page._undo_service = UndoService(journal, filesystem=FailOnceFilesystem(name))
    monkeypatch.setattr(QMessageBox, "exec", lambda _dialog: QMessageBox.StandardButton.Yes)
    preview, request = page.preview, page.state.request_id
    completed, scope = set(page.state.completed_targets), dict(page.state.review_scope)
    page.undo_button.click()
    _wait_for_scan_idle(qapp, page)
    assert page.state.last_undo_result.success_count == 1  # Historical, not this attempt.
    assert page.preview is preview and not page.state.stale and page.state.request_id == request
    assert page.state.completed_targets == completed and page.state.review_scope == scope


def test_unrelated_undo_preserves_partial_undo_refresh_retry_context(qapp, review):
    page, service, executor, journal, root, _lookup = review
    execution = execute_and_refresh(qapp, review, {"RJ000001", "RJ000002"})
    partial = UndoService(
        journal, filesystem=FailOnceFilesystem(execution.operations[0].target_path.name)
    ).undo(execution.transaction_id, confirmed=True)
    service.fail = True
    page._show_undo_result(partial)
    _wait_for_scan_idle(qapp, page)
    title, scope, request = (
        page.status_banner.title_label.text(),
        dict(page.state.review_scope),
        page.state.request_id,
    )
    service.fail = False
    other = root.parent / "unrelated"
    other.mkdir()
    (other / "old RJ000006").mkdir()
    unrelated = executor.execute(other, service.preview(other).plans, confirmed=True)
    page._show_undo_result(UndoService(journal).undo(unrelated.transaction_id, confirmed=True))
    assert page.status_banner.title_label.text() == title
    assert page.state.review_scope == scope and page.state.request_id == request
    assert page.state.reconciliation_undo_result is partial
    page.retry_refresh()
    _wait_for_scan_idle(qapp, page)
    assert "剩余可撤销 1 项" in page.status_label.text()
