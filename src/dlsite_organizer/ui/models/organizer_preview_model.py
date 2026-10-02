"""Lazy, widget-free projection of OrganizerState into Qt's table API."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from enum import StrEnum
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPersistentModelIndex, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QStyle

from dlsite_organizer.domain.organizer import RenamePlan, RenamePlanStatus
from dlsite_organizer.services.organizer import OrganizerPreview
from dlsite_organizer.ui.state.organizer_state import (
    OrganizerState,
    PreviewStaleReason,
    merge_previews,
)

_ROOT_INDEX = QModelIndex()


class StatusPresentation(StrEnum):
    NORMAL = "normal"
    WARNING = "warning"
    BLOCKED = "blocked"


class OrganizerPreviewModel(QAbstractTableModel):
    """Render service plans and edit only execution check projection."""

    COLUMNS = ("执行", "状态", "作品", "当前名称", "目标名称")
    checked_changed = Signal()

    def __init__(self, state: OrganizerState, parent=None) -> None:
        super().__init__(parent)
        self.state = state
        self._status_font = QFont()
        self._status_font.setBold(True)
        self._warning_icon = QApplication.style().standardIcon(
            QStyle.StandardPixmap.SP_MessageBoxWarning
        )
        self._blocked_icon = QApplication.style().standardIcon(
            QStyle.StandardPixmap.SP_MessageBoxCritical
        )

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = _ROOT_INDEX) -> int:
        return 0 if parent.isValid() else len(self.state.plans)

    def columnCount(self, parent: QModelIndex | QPersistentModelIndex = _ROOT_INDEX) -> int:
        return 0 if parent.isValid() else len(self.COLUMNS)

    def headerData(self, section, orientation, role: int = Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.COLUMNS[section] if 0 <= section < len(self.COLUMNS) else None
        return super().headerData(section, orientation, role)

    def data(self, index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not self._valid(index):
            return None
        row, column = index.row(), index.column()
        plan = self.state.plans[row]
        if role == Qt.ItemDataRole.CheckStateRole and column == 0:
            if plan.status is RenamePlanStatus.READY:
                return Qt.CheckState.Checked if self.state.checked(row) else Qt.CheckState.Unchecked
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            if column == 0:
                return None
            if column == 1:
                return plan_status_label(plan.status)
            if column == 2:
                code = plan.work_code or "、".join(plan.work_codes) or "—"
                return f"{code} · {plan.work.title}" if plan.work is not None else code
            if column == 3:
                return plan.current_name
            return plan.proposed_name or "—"
        if role == Qt.ItemDataRole.ToolTipRole:
            if column in (0, 1):
                detail = plan_diagnostics(plan)
                return (
                    f"{plan_status_label(plan.status)}\n原因：{detail}\n\n悬停查看具体原因。"
                    if detail else plan_status_label(plan.status)
                )
            if column == 2:
                code = plan.work_code or "、".join(plan.work_codes) or "—"
                work = plan.work
                return (
                    f"{code}\n{work.title}\n社团：{work.maker_name or '—'}"
                    if work is not None else code
                )
            if column == 3:
                return str(plan.source_path)
            return str(plan.target_path) if plan.target_path is not None else plan.proposed_name
        if role == Qt.ItemDataRole.AccessibleTextRole and column in (0, 1):
            return f"{plan_status_label(plan.status)}。原因：{plan_diagnostics(plan)}"
        if role == Qt.ItemDataRole.TextAlignmentRole:
            if column == 0:
                return Qt.AlignmentFlag.AlignCenter
            return Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        if column == 1 and plan_presentation(plan) is not StatusPresentation.NORMAL:
            if role == Qt.ItemDataRole.FontRole:
                return self._status_font
            if role == Qt.ItemDataRole.DecorationRole:
                return (
                    self._warning_icon if plan_presentation(plan) is StatusPresentation.WARNING
                    else self._blocked_icon
                )
        return None

    def flags(self, index):
        if not self._valid(index):
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        if (
            index.column() == 0 and self.state.checks_editable
            and self.state.plans[index.row()].status is RenamePlanStatus.READY
        ):
            flags |= Qt.ItemFlag.ItemIsUserCheckable
        return flags

    def setData(self, index, value, role: int = Qt.ItemDataRole.EditRole) -> bool:
        if (
            not self._valid(index) or index.column() != 0
            or role != Qt.ItemDataRole.CheckStateRole
            or value not in (Qt.CheckState.Checked, Qt.CheckState.Unchecked, 0, 2)
        ):
            return False
        checked = value in (Qt.CheckState.Checked, 2)
        previous = self.state.checked(index.row())
        if not self.state.set_checked(index.row(), checked):
            return False
        if previous != checked:
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.CheckStateRole])
            self.checked_changed.emit()
        return True

    def replace_preview(self, preview: OrganizerPreview) -> None:
        self.beginResetModel()
        self.state.replace_preview(preview)
        self.endResetModel()

    def append_preview(self, incoming: OrganizerPreview) -> None:
        if self.state.preview is None:
            self.replace_preview(incoming)
            return
        if self.state.stale or incoming.cancelled:
            raise ValueError("Cannot append to stale preview or commit cancelled append")
        preview = merge_previews(self.state.preview, incoming)
        old_count = self.rowCount()
        new_count = len(preview.plans)
        if new_count > old_count:
            self.beginInsertRows(QModelIndex(), old_count, new_count - 1)
        self.state.append_preview(preview)
        if new_count > old_count:
            self.endInsertRows()
        if old_count:
            self.dataChanged.emit(self.index(0, 0), self.index(old_count - 1, 4), [])

    def remove_rows(self, rows: Iterable[int]) -> None:
        if self.state.busy is not None or self.state.stale:
            return
        selected = sorted({row for row in rows if 0 <= row < self.rowCount()})
        if not selected:
            return
        ranges: list[tuple[int, int]] = []
        first = last = selected[0]
        for row in selected[1:]:
            if row == last + 1:
                last = row
            else:
                ranges.append((first, last))
                first = last = row
        ranges.append((first, last))
        # Many sparse selections are cheaper and coherent as a single reset.
        if len(ranges) > 32:
            assert self.state.preview is not None
            removed = set(selected)
            preview = replace(self.state.preview, plans=tuple(
                plan for row, plan in enumerate(self.state.plans) if row not in removed
            ))
            self.beginResetModel()
            self.state.append_preview(preview)
            self.endResetModel()
            return
        for first, last in reversed(ranges):
            self.beginRemoveRows(QModelIndex(), first, last)
            self.state.remove_rows(first, last)
            self.endRemoveRows()
        self.state.preview_revision += 1

    def clear_preview(self) -> None:
        self.beginResetModel()
        self.state.clear_preview()
        self.endResetModel()

    def mark_stale(self, reason: PreviewStaleReason) -> None:
        self.state.mark_stale(reason)
        self.refresh_editability()

    def refresh_editability(self) -> None:
        if self.rowCount():
            self.dataChanged.emit(self.index(0, 0), self.index(self.rowCount() - 1, 0),
                                  [Qt.ItemDataRole.CheckStateRole])

    def _valid(self, index: QModelIndex | QPersistentModelIndex) -> bool:
        return (
            index.isValid() and index.model() is self
            and 0 <= index.row() < self.rowCount()
            and 0 <= index.column() < self.columnCount()
        )


def plan_status_label(status: RenamePlanStatus) -> str:
    return {
        RenamePlanStatus.READY: "可执行",
        RenamePlanStatus.UNCHANGED: "无需重命名",
        RenamePlanStatus.CONFLICT: "冲突",
        RenamePlanStatus.LOOKUP_FAILED: "查询失败",
        RenamePlanStatus.INVALID_CODE: "无效作品编号",
        RenamePlanStatus.AMBIGUOUS_CODE: "作品编号不唯一",
        RenamePlanStatus.INVALID_TARGET: "目标名称无效",
        RenamePlanStatus.CANCELLED: "已取消",
    }.get(status, "需检查")


def plan_diagnostics(plan: RenamePlan) -> str:
    messages = [plan.error] if plan.error else []
    messages.extend(plan.warnings)
    return "\n".join(dict.fromkeys(messages))


def plan_presentation(plan: RenamePlan) -> StatusPresentation:
    if plan.status in {RenamePlanStatus.READY, RenamePlanStatus.UNCHANGED}:
        return StatusPresentation.WARNING if plan.warnings else StatusPresentation.NORMAL
    if plan.status is RenamePlanStatus.CANCELLED:
        return StatusPresentation.WARNING
    return StatusPresentation.BLOCKED
