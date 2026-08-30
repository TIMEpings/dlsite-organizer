"""Shared manual review dialog used by lookup and queue entry points."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
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
    ):
        relation.addItem(label, value)
    notes = QPlainTextEdit()
    subject = QComboBox()
    subject.addItems([candidate.source_workno, candidate.target_workno])
    target = QComboBox()
    target.addItems([candidate.source_workno, candidate.target_workno])
    target.setCurrentIndex(1)
    fields = (
        ("判断", outcome),
        ("关系类型", relation),
        ("关系主体", subject),
        ("关系目标", target),
        ("备注", notes),
    )
    for label, widget in fields:
        form.addRow(label, widget)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
    )
    form.addRow(buttons)

    def update_visibility(index: int) -> None:
        related = index == 0
        relation.setEnabled(related)
        directional = related and relation.currentData() not in {"bundled_with", "other", "unknown"}
        subject.setVisible(directional)
        target.setVisible(directional)

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
                subject_workno=subject.currentText() if directional else None,
                target_workno=target.currentText() if directional else None,
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
