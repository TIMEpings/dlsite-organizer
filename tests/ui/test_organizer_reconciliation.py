"""Offline characterization of planning, mutation facts and review ownership."""

from dataclasses import replace
from threading import Event

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton
from tests.ui.test_organizer_page import FakeLookupService, _wait_for, _wait_for_scan_idle
from tests.ui.test_organizer_preview_model import synthetic_preview

from dlsite_organizer.domain.organizer import RenamePlanStatus
from dlsite_organizer.domain.rename_execution import (
    ExecutionStatus,
    RenameExecutionResult,
    TransactionStatus,
)
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.drop_input import normalized_path_identity
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.services.rename_executor import LocalRenameFilesystem, RenameExecutor
from dlsite_organizer.services.rename_planner import RenamePlanner
from dlsite_organizer.services.undo_service import UndoService
from dlsite_organizer.ui.models.organizer_preview_model import OrganizerPreviewModel
from dlsite_organizer.ui.pages.organizer_page import OrganizerPage
from dlsite_organizer.ui.state.organizer_state import OrganizerState, ScanMode
from dlsite_organizer.ui.workers.organizer_worker import OrganizerWorkerResult


@pytest.fixture
def qapp():
    app = QApplication.instance()
    return app if isinstance(app, QApplication) else QApplication([])


class ControlledOrganizer(OrganizerService):
    generation = 0
    fail = False
    entered: Event | None = None
    release: Event | None = None

    @property
    def settings_generation(self):
        return self.generation

    def _preview(self, root_path, **kwargs):
        if self.entered is not None and self.release is not None:
            self.entered.set()
            assert self.release.wait(3)
        if self.fail:
            raise RuntimeError("controlled refresh failure")
        return super()._preview(root_path, **kwargs)


@pytest.fixture
def review(qapp, tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    for i in range(1, 4):
        (root / f"old RJ{i:06}").mkdir()
    (root / "RJ000004 RJ000005").mkdir()
    lookup = FakeLookupService()
    service = ControlledOrganizer(lookup)
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    executor = RenameExecutor(journal)
    page = OrganizerPage(service, execution_service=executor, undo_service=UndoService(journal))
    page.set_preview(service.preview(root))
    try:
        yield page, service, executor, journal, root, lookup
    finally:
        if service.release is not None:
            service.release.set()
        _wait_for_scan_idle(qapp, page)
        page.close()
        database.dispose()


def checked_codes(page, codes):
    for row, plan in enumerate(page.state.plans):
        if plan.status is RenamePlanStatus.READY:
            page.preview_model.setData(page.preview_model.index(row, 0),
                                       Qt.CheckState.Checked if plan.work_code in codes
                                       else Qt.CheckState.Unchecked,
                                       Qt.ItemDataRole.CheckStateRole)


def test_finalized_conflicts_are_not_reversible_by_finalize_alone(tmp_path):
    for name in ("a RJ000001", "b RJ000001"):
        (tmp_path / name).mkdir()
    preview = OrganizerService(FakeLookupService()).preview(tmp_path)
    assert all(plan.status is RenamePlanStatus.CONFLICT for plan in preview.plans)
    survivor = RenamePlanner().finalize(preview.plans[:1])
    assert survivor[0].status is RenamePlanStatus.CONFLICT
    # The reusable metadata/proposed name is present; no new lookup is necessary.
    plan = survivor[0]
    assert plan.work is not None and plan.work_code and plan.proposed_name
    replanned = RenamePlanner().plan(tmp_path, plan.source_path, plan.work_code,
                                    plan.work, formatted_name=plan.proposed_name)
    assert replanned.status is RenamePlanStatus.READY


def test_execution_exposes_final_journal_target_identity(tmp_path):
    (tmp_path / "old RJ000001").mkdir()
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    try:
        service = OrganizerService(FakeLookupService())
        preview = service.preview(tmp_path)
        result = RenameExecutor(TransactionJournal(database)).execute(
            tmp_path, preview.plans, confirmed=True)
        assert result.status is TransactionStatus.COMPLETED
        assert result.transaction is not None and not result.transaction.unresolved
        assert result.operations == result.transaction.operations
        operation = result.operations[0]
        assert operation.status is ExecutionStatus.SUCCESS
        assert operation.target_path.is_dir() and not operation.source_path.exists()
        refreshed = service.preview(tmp_path)
        assert refreshed.plans[0].source_path == operation.target_path
        assert refreshed.plans[0].status is RenamePlanStatus.UNCHANGED
    finally:
        database.dispose()


def test_replace_append_remove_and_request_boundaries(qapp, tmp_path):
    (tmp_path / "a RJ000001").mkdir()
    (tmp_path / "b RJ000002").mkdir()
    preview = OrganizerService(FakeLookupService()).preview(tmp_path)
    state = OrganizerState()
    model = OrganizerPreviewModel(state)
    model.replace_preview(preview)
    model.setData(model.index(0, 0), Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole)
    keys = state.row_keys
    model.remove_rows([1])
    assert state.row_keys == keys[:1] and not state.checked(0)
    model.append_preview(preview)
    assert state.row_keys == keys and state.checked_ready_count == 0
    state.begin_scan(ScanMode.REPLACE)
    request, revision = state.request_id, state.preview_revision
    assert state.owns_result(request, revision)
    state.begin_scan(ScanMode.REPLACE)
    assert not state.owns_result(request, revision)
    model.replace_preview(replace(preview, settings_generation=1))
    assert state.stale and not state.checks_editable
    model.replace_preview(preview)
    state.finish_operation()
    assert state.checked_ready_count == 2


def test_real_execute_refresh_two_batches_navigation_and_manual_reset(qapp, review, monkeypatch):
    page, _service, _executor, _journal, root, _lookup = review
    next_root = root.parent / "next-input"
    page.set_root_path(next_root)
    checked_codes(page, {"RJ000001", "RJ000002"})
    monkeypatch.setattr(QMessageBox, "exec", lambda _: QMessageBox.StandardButton.Yes)
    old_request = page.state.request_id
    page.execute_rename()
    page.hide()  # The persistent page/state accepts results while another page is visible.
    _wait_for_scan_idle(qapp, page)
    page.show()
    result = page.state.last_execution_result
    assert result is not None and result.success_count == 2
    assert page.state.request_id == old_request + 1
    assert page.state.committed_preview_root == root
    assert page.root_input.text() == str(next_root)
    assert not page.state.stale and page.state.checked_ready_count == 0
    assert sum(page.state.completed(i) for i in range(len(page.state.plans))) == 2
    assert "本轮已完成 2" in page.summary_label.text()
    assert "可执行 1" in page.summary_label.text() and "不可执行 1" in page.summary_label.text()
    for row, plan in enumerate(page.state.plans):
        if page.state.completed(row):
            assert plan.status is RenamePlanStatus.UNCHANGED
            assert page.preview_model.status_label(row) == "已完成"
            assert not (page.preview_model.flags(page.preview_model.index(row, 0))
                        & Qt.ItemFlag.ItemIsUserCheckable)
    assert page.undo_button.isEnabled()
    checked_codes(page, {"RJ000003"})
    page.execute_rename()
    _wait_for_scan_idle(qapp, page)
    assert sum(page.state.completed(i) for i in range(len(page.state.plans))) == 3
    assert page.state.checked_ready_count == 0
    page.set_root_path(root)
    page.scan_button.click()
    _wait_for_scan_idle(qapp, page)
    assert not page.state.completed_targets
    assert all(not page.state.completed(i) for i in range(len(page.state.plans)))
    assert page.undo_button.isEnabled()


def test_refresh_failure_truth_retry_and_clear(qapp, review):
    page, service, executor, journal, root, _lookup = review
    result = executor.execute(root, page.selected_ready_plans()[:1], confirmed=True)
    service.fail = True
    page._show_execution_result(result)
    _wait_for_scan_idle(qapp, page)
    assert page.state.last_execution_result is result
    assert journal.get_transaction(result.transaction_id).status is TransactionStatus.COMPLETED
    assert page.state.stale and page.state.checked_ready_count == 0
    assert page.state.reconciliation_root == root
    assert page.status_banner.title_label.text() == "重命名已完成，但预览刷新失败。"
    assert "当前列表可能已过期" in page.status_banner.message_label.text()
    assert not page.retry_refresh_button.isHidden()
    assert page.undo_button.isEnabled() and not page.execute_button.isEnabled()
    assert all(not (page.preview_model.flags(page.preview_model.index(i, 0))
                    & Qt.ItemFlag.ItemIsUserCheckable) for i in range(len(page.state.plans)))
    page.set_root_path(root.parent / "different-next-input")
    service.fail = False
    page.retry_refresh_button.click()
    _wait_for_scan_idle(qapp, page)
    assert not page.state.stale and page.state.reconciliation_root is None
    assert page.state.checked_ready_count == 0
    assert sum(page.state.completed(i) for i in range(len(page.state.plans))) == 1
    assert page.retry_refresh_button.isHidden() and page.undo_button.isEnabled()
    page.clear_preview()
    assert not page.state.completed_targets and page.state.reconciliation_root is None
    assert page.state.last_execution_result is result and page.undo_button.isEnabled()


def test_deterministic_partial_success_replans_failed_and_unattempted(qapp, review):
    page, _service, _executor, journal, root, _lookup = review
    class FailSecond(LocalRenameFilesystem):
        count = 0

        def rename(self, source, target):
            self.count += 1
            if self.count == 2:
                raise OSError("controlled second operation failure")
            super().rename(source, target)
    result = RenameExecutor(journal, filesystem=FailSecond()).execute(
        root, page.selected_ready_plans(), confirmed=True)
    assert result.status is TransactionStatus.PARTIAL
    assert (result.success_count, result.failed_count, result.not_executed_count) == (1, 1, 1)
    page._show_execution_result(result)
    _wait_for_scan_idle(qapp, page)
    assert not page.state.stale and page.state.checked_ready_count == 0
    assert sum(page.state.completed(i) for i in range(len(page.state.plans))) == 1
    assert sum(plan.status is RenamePlanStatus.READY for plan in page.state.plans) == 2
    assert all(plan.error is None for plan in page.state.plans
               if plan.status is RenamePlanStatus.READY)
    assert page.undo_button.isEnabled()


@pytest.mark.parametrize("condition", ["settings", "revision", "late-request"])
def test_refresh_ownership_rejection_and_busy_scan_policy(qapp, review, condition):
    page, service, executor, _journal, root, _lookup = review
    result = executor.execute(root, page.selected_ready_plans()[:1], confirmed=True)
    old_preview = page.preview
    service.entered, service.release = Event(), Event()
    page._show_execution_result(result)
    _wait_for(qapp, service.entered.is_set)
    request, revision = page.state.request_id, page.state.preview_revision
    assert not page.scan_button.isEnabled()
    page.start_scan()
    assert page.state.request_id == request  # R2 cannot start while R1 owns the worker.
    if condition == "settings":
        service.generation += 1
        page.invalidate_preview()
    elif condition == "revision":
        page.mark_filesystem_changed()
    else:
        page.state.request_id += 1
    service.release.set()
    _wait_for_scan_idle(qapp, page)
    assert page.preview is old_preview and page.state.stale
    assert page.state.checked_ready_count == 0
    assert page.state.last_execution_result is result
    # Explicit late delivery cannot bypass ownership, even when hidden.
    page.hide()
    page._show_preview(OrganizerWorkerResult(request, revision, service.preview(root)))
    assert page.preview is old_preview
    service.entered = service.release = None
    page.retry_refresh()
    _wait_for_scan_idle(qapp, page)
    assert not page.state.stale and page.state.completed_targets
    assert page.state.checked_ready_count == 0


def test_unresolved_result_and_pre_mutation_error_do_not_refresh(qapp, review):
    page, service, executor, _journal, root, _lookup = review
    cancelled = executor.execute(root, page.selected_ready_plans(), confirmed=False)
    assert cancelled.success_count == 0 and cancelled.transaction is None
    request = page.state.request_id
    page._show_execution_result(cancelled)
    assert page.state.request_id == request and page.state.stale
    page.set_preview(service.preview(root))
    page._show_action_error("controlled error before mutation")
    assert page.state.request_id == request and page.state.last_execution_result is cancelled
    page.set_preview(service.preview(root))
    ready = page.selected_ready_plans()
    result = executor.execute(root, ready[:1], confirmed=True)
    assert result.transaction is not None
    unresolved = replace(result.transaction, status=TransactionStatus.RECOVERY_REQUIRED)
    uncertain = RenameExecutionResult(TransactionStatus.RECOVERY_REQUIRED, unresolved,
                                      result.operations)
    page._show_execution_result(uncertain)
    assert page.state.request_id == request and not page.state.completed_targets
    assert page.state.stale and page.state.last_execution_result is uncertain


def test_confirmation_cancel_keeps_review_and_filesystem(qapp, review, monkeypatch):
    page, _service, _executor, _journal, _root, _lookup = review
    preview = page.preview
    monkeypatch.setattr(QMessageBox, "exec", lambda _: QMessageBox.StandardButton.Cancel)
    page.execute_rename()
    assert page.preview is preview and not page.state.stale
    assert page.state.last_execution_result is None and not page.is_busy()
    assert all(plan.source_path.is_dir() for plan in page.state.plans)


@pytest.mark.parametrize("occupancy", ["absent", "file", "directory", "source", "case-alias"])
def test_remove_reevaluates_only_planner_approved_survivors(qapp, tmp_path, occupancy):
    a, b = tmp_path / "a RJ000001", tmp_path / "b RJ000001"
    a.mkdir()
    b.mkdir()
    (tmp_path / "unrelated RJ000002").mkdir()
    lookup = FakeLookupService()
    service = OrganizerService(lookup)
    preview = service.preview(tmp_path)
    target = preview.plans[0].target_path
    assert target is not None
    if occupancy == "file":
        target.write_text("occupancy")
    elif occupancy in ("directory", "case-alias"):
        (target if occupancy == "directory" else target.with_name(target.name.lower())).mkdir()
    elif occupancy == "source":
        target.mkdir()
        # Occupancy by another current source remains real occupancy after preview removal.
        preview = service.preview(tmp_path)
    page = OrganizerPage(service)
    page.set_preview(preview)
    removed_row = next(i for i, plan in enumerate(page.state.plans) if plan.source_path == b)
    page.state.project_highlight([removed_row], removed_row)
    calls = len(lookup.calls)
    survivor_key = page.state.row_keys[next(i for i, plan in enumerate(page.state.plans)
                                          if plan.source_path == a)]
    page.remove_selected()
    row = page.state.row_keys.index(survivor_key)
    expected = RenamePlanStatus.READY if occupancy == "absent" else RenamePlanStatus.CONFLICT
    assert page.state.plans[row].status is expected
    assert not page.state.checked(row)
    assert len(lookup.calls) == calls
    unrelated = next(i for i, plan in enumerate(page.state.plans)
                     if plan.work_code == "RJ000002")
    assert page.state.checked(unrelated)
    page.close()


def test_completion_overlay_append_resets_unmatched_and_ordinary_unchanged(qapp, tmp_path):
    preview = synthetic_preview(tmp_path, 3)
    preview = replace(preview, plans=tuple(replace(plan, status=RenamePlanStatus.UNCHANGED)
                                           for plan in preview.plans))
    state = OrganizerState()
    model = OrganizerPreviewModel(state)
    model.replace_preview(preview)
    completed_key = normalized_path_identity(preview.plans[0].source_path)
    state.completed_targets.update({completed_key, normalized_path_identity(tmp_path / "missing")})
    model.replace_preview(preview, reconcile=True)
    assert state.completed(0) and not state.completed(1)
    assert model.status_label(0) == "已完成" and model.status_label(1) == "无需重命名"
    assert len(state.plans) == 3  # Unmatched completion never fabricates a row.
    incoming = synthetic_preview(tmp_path, 4)
    incoming = replace(incoming, plans=(*preview.plans, incoming.plans[-1]))
    model.append_preview(incoming)
    assert state.completed(0) and not state.checked(3)
    model.replace_preview(replace(preview, root_path=tmp_path / "other"))
    assert not state.completed_targets
    assert not OrganizerState().completed_targets
    state.completed_targets.add(completed_key)
    model.clear_preview()
    assert not state.completed_targets


def test_warning_categories_label_and_single_bulk_menu(qapp, tmp_path):
    page = OrganizerPage(OrganizerService(FakeLookupService()))
    preview = synthetic_preview(tmp_path, 4)
    statuses = (RenamePlanStatus.READY, RenamePlanStatus.CONFLICT,
                RenamePlanStatus.AMBIGUOUS_CODE, RenamePlanStatus.UNCHANGED)
    page.set_preview(replace(preview, plans=tuple(
        replace(plan, status=status, warnings=("raw diagnostic",))
        for plan, status in zip(preview.plans, statuses, strict=True))))
    page.resize(760, 640)
    page.show()
    qapp.processEvents()
    try:
        assert page.root_label.text() == "下次扫描目录"
        assert page.root_label.width() >= page.root_label.fontMetrics().horizontalAdvance(
            page.root_label.text())
        assert not hasattr(page, "preview_root_name")
        assert page.preview_root_display.text() == str(tmp_path)
        assert len(page.bulk_toolbar.findChildren(QPushButton)) == 1
        assert [action.text() for action in page.bulk_menu.actions()] == [
            "全选所有可执行项", "反选所有可执行项", "清除所有执行勾选"]
        assert "含警告 1" in page.summary_label.text()
        assert "不可执行 2" in page.summary_label.text()
        assert "无需重命名 1" in page.summary_label.text()
        for row in range(4):
            assert "raw diagnostic" in page.preview_model.index(row, 1).data(
                Qt.ItemDataRole.ToolTipRole)
    finally:
        page.close()


def test_real_journal_uncertainty_takes_priority(qapp, review, monkeypatch):
    page, _service, executor, journal, root, _lookup = review
    def fail_journal(*args):
        raise OSError("controlled success-journal uncertainty")
    monkeypatch.setattr(journal, "mark_operation_success", fail_journal)
    result = executor.execute(root, page.selected_ready_plans()[:1], confirmed=True)
    assert result.status is TransactionStatus.RECOVERY_REQUIRED
    page._show_execution_result(result)
    assert not page.is_busy() and page.state.request_id == 0
    assert page.state.unresolved_transaction_id == result.transaction_id
    assert page.status_banner.title_label.text() == "存在未解决的重命名事务"
    assert not page.undo_button.isEnabled() and not page.bulk_menu_button.isEnabled()
    assert not page.state.completed_targets
    assert page.retry_refresh_button.isHidden()


def test_empty_reconciliation_and_unmatched_targets_are_not_fabricated(qapp, review, caplog):
    page, _service, executor, _journal, root, _lookup = review
    result = executor.execute(root, page.selected_ready_plans(), confirmed=True)
    # Controlled external change after confirmed mutation, before refresh.
    for child in root.iterdir():
        child.rmdir()
    page._show_execution_result(result)
    _wait_for_scan_idle(qapp, page)
    assert not page.state.plans and not page.state.stale
    assert page.state.last_execution_result is result
    assert "absent from refreshed preview" in caplog.text
    assert page.empty_title_label.text() == "当前预览没有项目"
    assert "已完成 3 项，预览已刷新" in page.status_label.text()


def test_append_during_completed_session_and_cross_root_rejection(qapp, review):
    page, _service, executor, _journal, root, _lookup = review
    result = executor.execute(root, page.selected_ready_plans()[:1], confirmed=True)
    page._show_execution_result(result)
    _wait_for_scan_idle(qapp, page)
    appended = root / "new RJ000006"
    appended.mkdir()
    page._handle_drop_paths([appended])
    _wait_for_scan_idle(qapp, page)
    assert sum(page.state.completed(i) for i in range(len(page.state.plans))) == 1
    assert page.state.checked_ready_count == 0
    outside = root.parent / "other-root" / "other RJ000007"
    outside.mkdir(parents=True)
    preview, request = page.preview, page.state.request_id
    page._handle_drop_paths([outside])
    assert page.preview is preview and page.state.request_id == request
    assert str(root) in page.status_label.text() and "无法将其他根目录" in page.status_label.text()
    assert "清空预览" in page.status_label.text()


def test_refresh_cancel_failure_clear_and_obsolete_error(qapp, review):
    page, service, executor, _journal, root, _lookup = review
    result = executor.execute(root, page.selected_ready_plans()[:1], confirmed=True)
    service.entered, service.release = Event(), Event()
    page._show_execution_result(result)
    _wait_for(qapp, service.entered.is_set)
    request, revision = page.state.request_id, page.state.preview_revision
    assert "正在刷新预览" in page.status_banner.title_label.text()
    assert "执行前的预览" in page.status_banner.message_label.text()
    page.cancel_scan()
    service.release.set()
    _wait_for_scan_idle(qapp, page)
    assert page.state.stale and page.state.reconciliation_root == root
    page.clear_preview()
    assert not page.state.completed_targets and page.state.reconciliation_root is None
    service.entered = service.release = None
    page.set_preview(service.preview(root))
    current = page.preview
    page._show_scan_error(request, revision, "obsolete error")
    assert page.preview is current and not page.state.stale
    assert page.retry_refresh_button.isHidden()


def test_replanning_preserves_nonplanning_status_and_fails_closed_on_occupancy(qapp, tmp_path):
    (tmp_path / "a RJ000001").mkdir()
    (tmp_path / "b RJ000001").mkdir()
    service = OrganizerService(FakeLookupService())
    preview = service.preview(tmp_path)
    diagnostic = replace(preview.plans[0], status=RenamePlanStatus.INVALID_TARGET,
                         error="captured invalid target", warnings=("captured diagnostic",))
    preview = replace(preview, plans=(diagnostic, preview.plans[1]))
    replanned = service.reevaluate_preview(preview)
    assert replanned.plans[0] is diagnostic
    assert replanned.plans[1].status is RenamePlanStatus.READY
    for child in tmp_path.iterdir():
        child.rmdir()
    tmp_path.rmdir()
    replanned = service.reevaluate_preview(replace(preview, plans=preview.plans[1:]))
    assert replanned.plans[0].status is RenamePlanStatus.CONFLICT
    assert "无法检查" in (replanned.plans[0].error or "")


def test_undo_after_failed_forward_refresh_preserves_existing_invalidation(qapp, review):
    page, service, executor, journal, root, _lookup = review
    result = executor.execute(root, page.selected_ready_plans()[:1], confirmed=True)
    service.fail = True
    page._show_execution_result(result)
    _wait_for_scan_idle(qapp, page)
    assert page.state.reconciliation_root == root and page.undo_button.isEnabled()
    request = page.state.request_id
    undone = UndoService(journal).undo(result.transaction_id, confirmed=True)
    assert undone.status is TransactionStatus.UNDONE
    page._show_undo_result(undone)
    assert page.state.stale and page.state.reconciliation_root is None
    assert not page.state.reconciliation_pending and page.retry_refresh_button.isHidden()
    assert page.state.request_id == request and not page.is_busy()
    assert page.state.last_execution_result is result and page.state.last_undo_result is undone
    assert result.operations[0].source_path.is_dir()
