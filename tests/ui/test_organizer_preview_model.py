"""Direct model contracts, without network or filesystem planning."""

from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex, Qt
from PySide6.QtTest import QAbstractItemModelTester, QSignalSpy
from PySide6.QtWidgets import QApplication

from dlsite_organizer.domain.organizer import RenamePlan, RenamePlanStatus, ScanResult
from dlsite_organizer.domain.work import Work
from dlsite_organizer.services.drop_input import normalized_path_identity
from dlsite_organizer.services.organizer import OrganizerPreview
from dlsite_organizer.ui.models.organizer_preview_model import OrganizerPreviewModel
from dlsite_organizer.ui.state.organizer_state import (
    BusyOperation,
    OrganizerState,
    PreviewStaleReason,
    ScanMode,
)


@pytest.fixture
def qapp() -> QApplication:
    app = QApplication.instance()
    return app if isinstance(app, QApplication) else QApplication([])


def synthetic_preview(root: Path, count: int) -> OrganizerPreview:
    work = Work(workno="RJ000001", title="中文作品・日本語 title", maker_name="社团")
    plans = tuple(RenamePlan(
        source_path=root / f"old-{i}", current_name=f"old-{i}",
        work_code=work.workno, work=work, proposed_name=f"新名称-{i}",
        target_path=root / f"新名称-{i}", status=RenamePlanStatus.READY,
    ) for i in range(count))
    return OrganizerPreview(root, ScanResult(root, (), ()), (), plans)


def test_roles_headers_unicode_full_paths_and_diagnostics(qapp: QApplication, tmp_path: Path):
    preview = synthetic_preview(tmp_path, 1)
    plan = replace(preview.plans[0], current_name="目录" * 200,
                   warnings=("路径警告",), error="结构化原因")
    state = OrganizerState()
    model = OrganizerPreviewModel(state)
    model.replace_preview(replace(preview, plans=(plan,)))
    assert model.rowCount() == 1 and model.columnCount() == 5
    assert model.rowCount(model.index(0, 0)) == 0
    assert model.columnCount(model.index(0, 0)) == 0
    assert [model.headerData(i, Qt.Orientation.Horizontal) for i in range(5)] == [
        "执行", "状态", "作品", "当前名称", "目标名称"
    ]
    assert model.data(QModelIndex()) is None
    assert model.data(model.index(0, 0)) is None
    assert model.data(model.index(0, 1)) == "可执行"
    assert "中文作品・日本語" in model.data(model.index(0, 2))
    assert "社团" in model.data(model.index(0, 2), Qt.ItemDataRole.ToolTipRole)
    assert model.data(model.index(0, 3)) == "目录" * 200
    assert model.data(model.index(0, 3), Qt.ItemDataRole.ToolTipRole) == str(plan.source_path)
    assert model.data(model.index(0, 4), Qt.ItemDataRole.ToolTipRole) == str(plan.target_path)
    assert "结构化原因\n路径警告" in model.data(model.index(0, 1), Qt.ItemDataRole.ToolTipRole)
    assert model.data(model.index(0, 1), Qt.ItemDataRole.FontRole).bold()
    assert not model.data(model.index(0, 1), Qt.ItemDataRole.DecorationRole).isNull()
    assert "路径警告" in model.data(model.index(0, 1), Qt.ItemDataRole.AccessibleTextRole)
    assert model.data(model.index(0, 0), Qt.ItemDataRole.TextAlignmentRole) == (
        Qt.AlignmentFlag.AlignCenter
    )
    assert state.plans[0] is plan


@pytest.mark.parametrize("status", list(RenamePlanStatus))
def test_check_flags_and_default_projection_for_every_domain_status(
    qapp: QApplication, tmp_path: Path, status: RenamePlanStatus
):
    preview = synthetic_preview(tmp_path, 1)
    model = OrganizerPreviewModel(OrganizerState())
    model.replace_preview(replace(preview, plans=(replace(preview.plans[0], status=status),)))
    index = model.index(0, 0)
    ready = status is RenamePlanStatus.READY
    assert bool(model.flags(index) & Qt.ItemFlag.ItemIsUserCheckable) is ready
    assert model.data(index, Qt.ItemDataRole.CheckStateRole) == (
        Qt.CheckState.Checked if ready else None
    )
    assert model.setData(index, Qt.CheckState.Unchecked, Qt.ItemDataRole.CheckStateRole) is ready
    assert not model.flags(model.index(0, 2)) & Qt.ItemFlag.ItemIsEditable
    assert not model.setData(index, "edit")
    assert not model.setData(index, Qt.CheckState.PartiallyChecked, Qt.ItemDataRole.CheckStateRole)


@pytest.mark.parametrize("busy", [BusyOperation.SCAN, BusyOperation.EXECUTE, BusyOperation.UNDO])
def test_busy_checks_visible_but_not_editable(qapp: QApplication, tmp_path: Path, busy):
    state = OrganizerState()
    model = OrganizerPreviewModel(state)
    model.replace_preview(synthetic_preview(tmp_path, 2))
    state.busy = busy
    model.refresh_editability()
    index = model.index(0, 0)
    assert model.data(index, Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
    assert not model.flags(index) & Qt.ItemFlag.ItemIsUserCheckable
    assert not model.setData(index, 0, Qt.ItemDataRole.CheckStateRole)


def test_one_check_change_is_bounded_with_no_reset_or_bulk_callbacks(
    qapp: QApplication, tmp_path: Path
):
    state = OrganizerState(executor_available=True, mutation_blocked=False)
    model = OrganizerPreviewModel(state)
    reset = QSignalSpy(model.modelReset)
    changed = QSignalSpy(model.dataChanged)
    checked = QSignalSpy(model.checked_changed)
    model.replace_preview(synthetic_preview(tmp_path, 5000))
    assert reset.count() == 1 and checked.count() == 0 and changed.count() == 0
    assert state.checked_ready_count == 5000 and state.can_execute
    index = model.index(2500, 0)
    assert model.setData(index, 0, Qt.ItemDataRole.CheckStateRole)
    assert reset.count() == 1 and changed.count() == 1 and checked.count() == 1
    assert changed.at(0)[:2] == [index, index]
    assert state.checked_ready_count == 4999
    assert model.setData(index, 0, Qt.ItemDataRole.CheckStateRole)
    assert changed.count() == 1


def test_append_upsert_remove_reset_and_identity_synchronization(qapp, tmp_path):
    state = OrganizerState()
    model = OrganizerPreviewModel(state)
    tester = QAbstractItemModelTester(model, QAbstractItemModelTester.FailureReportingMode.Fatal)
    preview = synthetic_preview(tmp_path, 4)
    model.replace_preview(replace(preview, plans=preview.plans[:2]))
    keys = state.row_keys
    assert model.setData(model.index(1, 0), 0, Qt.ItemDataRole.CheckStateRole)
    upsert = replace(preview.plans[0], source_path=Path(str(preview.plans[0].source_path).upper()),
                     current_name="更新后的名称")
    reset = QSignalSpy(model.modelReset)
    insert = QSignalSpy(model.rowsInserted)
    model.append_preview(replace(preview, plans=(upsert, preview.plans[2], preview.plans[3])))
    assert reset.count() == 0 and insert.count() == 1
    assert state.row_keys[:2] == keys
    assert [state.checked(row) for row in range(4)] == [True, False, False, False]
    assert model.data(model.index(0, 3)) == "更新后的名称"
    model.remove_rows([3, 0, 0])
    assert state.plans == (preview.plans[1], preview.plans[2])
    assert [state.checked(row) for row in range(2)] == [False, False]
    model.clear_preview()
    assert reset.count() == 1 and model.rowCount() == 0
    assert state.preview is None and state.current_key is None
    assert tester.model() is model


def test_stale_projection_and_request_ownership(qapp, tmp_path):
    state = OrganizerState(executor_available=True, mutation_blocked=False)
    model = OrganizerPreviewModel(state)
    preview = synthetic_preview(tmp_path, 2)
    model.replace_preview(preview)
    state.begin_scan(ScanMode.APPEND)
    request, revision = state.request_id, state.preview_revision
    assert state.owns_result(request, revision)
    model.mark_stale(PreviewStaleReason.SETTINGS_CHANGED)
    state.finish_operation()
    assert not state.owns_result(request, revision)
    assert state.preview is preview and state.stale
    assert not state.can_execute and not state.checked_ready_plans()
    assert model.data(model.index(0, 0), Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked
    assert not model.setData(model.index(0, 0), 0, Qt.ItemDataRole.CheckStateRole)
    assert not model.flags(model.index(0, 0)) & Qt.ItemFlag.ItemIsUserCheckable
    model.clear_preview()
    assert not state.stale


@pytest.mark.parametrize("gate", ["busy", "unavailable", "unresolved", "generation", "cancelled"])
def test_execution_derivation_fails_closed_for_all_gates(qapp, tmp_path, gate):
    state = OrganizerState(executor_available=True, mutation_blocked=False)
    model = OrganizerPreviewModel(state)
    model.replace_preview(synthetic_preview(tmp_path, 1))
    assert state.can_execute
    if gate == "busy":
        state.busy = BusyOperation.EXECUTE
    elif gate == "unavailable":
        state.executor_available = False
    elif gate == "unresolved":
        state.mutation_blocked = True
    elif gate == "generation":
        state.settings_generation += 1
    else:
        assert state.preview is not None
        model.replace_preview(replace(state.preview, cancelled=True))
    assert not state.can_execute


def test_sparse_bulk_removal_is_one_coherent_reset(qapp, tmp_path):
    state = OrganizerState()
    model = OrganizerPreviewModel(state)
    model.replace_preview(synthetic_preview(tmp_path, 5000))
    reset = QSignalSpy(model.modelReset)
    model.remove_rows(range(0, 5000, 2))
    assert reset.count() == 1 and model.rowCount() == 2500
    assert state.plans[0].current_name == "old-1"
    assert state.plans[-1].current_name == "old-4999"
    assert state.checked_ready_count == 2500


def test_row_identity_matches_canonical_resolver_for_service_paths_and_fallbacks(qapp, tmp_path):
    preview = synthetic_preview(tmp_path, 1)
    ordinary = [tmp_path / name for name in ("作品中文", "Unicode 日本語", "OLD~1", "MixedCase")]
    other = tmp_path / "other" / "source"
    other.mkdir(parents=True)
    for path in ordinary:
        path.mkdir()
    paths = [*ordinary, other, Path(str(ordinary[-1]).upper())]
    plans = tuple(replace(preview.plans[0], source_path=path) for path in paths)
    state = OrganizerState()
    model = OrganizerPreviewModel(state)
    model.replace_preview(replace(preview, plans=plans))
    assert state.row_keys == tuple(normalized_path_identity(path) for path in paths)


def test_append_generation_and_root_mismatch_leave_state_untouched(qapp, tmp_path):
    state = OrganizerState()
    model = OrganizerPreviewModel(state)
    preview = synthetic_preview(tmp_path, 2)
    model.replace_preview(preview)
    for incoming in (replace(preview, settings_generation=1),
                     replace(preview, root_path=tmp_path / "other")):
        with pytest.raises(ValueError):
            model.append_preview(incoming)
        assert state.preview is preview and state.checked_ready_count == 2
