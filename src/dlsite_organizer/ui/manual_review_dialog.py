"""Shared manual review dialog used by lookup and queue entry points."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
)

from dlsite_organizer.domain.candidate import CandidateRelation
from dlsite_organizer.domain.manual_review import CandidateReviewOutcome, ManualRelationType


def open_manual_review_dialog(parent, service, candidate: CandidateRelation) -> bool:
    """Show the canonical review form and append through the existing service."""
    if service is None:
        QMessageBox.warning(parent, "无法保存", "人工 review 存储不可用。")
        return False
    dialog = QDialog(parent)
    dialog.setWindowTitle("人工标记候选关系")
    form = QFormLayout(dialog)
    outcome = QComboBox()
    for value, label in (
        (CandidateReviewOutcome.RELATED, "有关联"),
        (CandidateReviewOutcome.NOT_RELATED, "无关联"),
        (CandidateReviewOutcome.UNSURE, "不确定"),
    ):
        outcome.addItem(label, value)
    relation = QComboBox()
    for value, label in (
        ("unknown", "类型未知"),
        ("translation_of", "翻译"),
        ("bonus_of", "特典"),
        ("limited_bonus_of", "限时/限定特典"),
        ("child_of", "子作品"),
        ("bundled_with", "捆绑/套装"),
        ("other", "其他"),
        ("same_series", "同系列作品"),
        ("same_work_variant", "同一作品的不同版本"),
        ("same_work_language_variant", "同一作品的不同语言版本"),
        ("included_in", "收录于合集/套装"),
    ):
        relation.addItem(label, value)
    notes = QPlainTextEdit()
    subject = QComboBox()
    target = QComboBox()
    direction_hint = QLabel()
    direction_hint.setWordWrap(True)
    subject_label = QLabel("关系主体")
    target_label = QLabel("关系目标")
    direction_label = QLabel("方向说明")
    form.addRow("判断", outcome)
    form.addRow("关系类型", relation)
    form.addRow(subject_label, subject)
    form.addRow(target_label, target)
    form.addRow(direction_label, direction_hint)
    form.addRow("备注", notes)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
    )
    form.addRow(buttons)

    def update_direction_items(included: bool) -> None:
        subject.blockSignals(True)
        target.blockSignals(True)
        try:
            subject.clear()
            target.clear()
            worknos = (candidate.source_workno, candidate.target_workno)
            if included:
                subject.addItem(f"{worknos[0]}（独立作品）", worknos[0])
                subject.addItem(f"{worknos[1]}（独立作品）", worknos[1])
                target.addItem(f"{worknos[0]}（合集/套装）", worknos[0])
                target.addItem(f"{worknos[1]}（合集/套装）", worknos[1])
            else:
                for workno in worknos:
                    subject.addItem(workno, workno)
                    target.addItem(workno, workno)
            subject.setCurrentIndex(0)
            target.setCurrentIndex(1)
        finally:
            subject.blockSignals(False)
            target.blockSignals(False)

    def update_visibility(index: int = -1) -> None:
        related = outcome.currentData() == CandidateReviewOutcome.RELATED
        relation.setEnabled(related)
        relation_type = ManualRelationType(relation.currentData()) if related else None
        directional = related and relation_type is not None and relation_type.is_directional
        included = relation_type is ManualRelationType.INCLUDED_IN
        if included:
            direction_hint.setText(
                f"请选择明确方向：{candidate.source_workno} 收录于 {candidate.target_workno}，"
                f"或 {candidate.target_workno} 收录于 {candidate.source_workno}。"
            )
        elif directional:
            direction_hint.setText("请选择关系主体与关系目标；保存后会保留该方向。")
        else:
            direction_hint.clear()
        update_direction_items(included)
        subject.setEnabled(directional)
        target.setEnabled(directional)
        subject.setVisible(directional)
        target.setVisible(directional)
        subject_label.setVisible(directional)
        target_label.setVisible(directional)
        direction_label.setVisible(directional)
        direction_hint.setVisible(directional)

    relation.currentIndexChanged.connect(lambda _index: update_visibility(outcome.currentIndex()))
    outcome.currentIndexChanged.connect(update_visibility)
    update_visibility(0)

    def save() -> None:
        try:
            related = outcome.currentData() == CandidateReviewOutcome.RELATED
            relation_type = ManualRelationType(relation.currentData()) if related else None
            directional = related and relation_type is not None and relation_type.is_directional
            service.submit_review(
                candidate.source_workno,
                candidate.target_workno,
                CandidateReviewOutcome(outcome.currentData()),
                relation_type=relation_type,
                subject_workno=subject.currentData() if directional else None,
                target_workno=target.currentData() if directional else None,
                notes=notes.toPlainText() or None,
                candidate=candidate,
            )
        except Exception as exc:
            QMessageBox.critical(dialog, "保存失败", f"人工 review 保存失败：{exc}")
            return
        dialog.accept()

    buttons.accepted.connect(save)
    buttons.rejected.connect(dialog.reject)
    return dialog.exec() == QDialog.DialogCode.Accepted
