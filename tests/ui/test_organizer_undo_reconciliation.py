"""Offline Undo result characterization and scoped UI reconciliation contracts."""

import pytest
from tests.ui.test_organizer_page import _wait_for_scan_idle

from dlsite_organizer.domain.rename_execution import TransactionStatus, UndoStatus
from dlsite_organizer.services.undo_service import UndoService

pytest_plugins = ["tests.ui.test_organizer_reconciliation"]


def test_current_undo_invalidates_preserved_scope_and_completed(qapp, review):
    page, service, executor, journal, root, _lookup = review
    original = tuple(page.state.review_scope.values())
    page.state.project_highlight([3], 3)
    page.remove_selected()
    result = executor.execute(root, page.selected_ready_plans()[:2], confirmed=True)
    page._show_execution_result(result)
    _wait_for_scan_idle(qapp, page)
    assert len(page.state.review_scope) == 3
    assert sum(page.state.completed(i) for i in range(3)) == 2
    scope, completed = dict(page.state.review_scope), set(page.state.completed_targets)
    request = page.state.request_id
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
    assert page.state.stale and page.state.request_id == request
    assert page.state.review_scope == scope and page.state.completed_targets == completed
    assert original[3] not in page.state.review_scope.values()
    assert page.state.reconciliation_root is None and not page.execute_button.isEnabled()


def test_current_different_root_undo_over_invalidates(qapp, review):
    page, service, executor, journal, root, _lookup = review
    other = root.parent / "other"
    other.mkdir()
    (other / "old RJ000006").mkdir()
    result = executor.execute(other, service.preview(other).plans, confirmed=True)
    undone = UndoService(journal).undo(result.transaction_id, confirmed=True)
    preview, scope, request = page.preview, dict(page.state.review_scope), page.state.request_id
    page._show_undo_result(undone)
    assert page.state.stale and page.preview is preview
    assert page.state.review_scope == scope and page.state.request_id == request


def test_result_contract_partial_resume_and_preflight_rejection(qapp, review):
    from tests.services.test_undo_service import FailOnceFilesystem

    page, _service, executor, journal, root, _lookup = review
    result = executor.execute(root, page.selected_ready_plans()[:2], confirmed=True)
    service = UndoService(journal, filesystem=FailOnceFilesystem(result.operations[0].target_path.name))
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
