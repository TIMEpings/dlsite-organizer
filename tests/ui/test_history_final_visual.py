"""Final visual contracts for the Rename History master-detail page."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtCore import QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetricsF, QImage, QPainter
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem

from dlsite_organizer.domain.rename_execution import RenameTransactionSummary, TransactionStatus
from dlsite_organizer.ui.pages.rename_history_page import (
    RenameHistoryPage,
    _master_status_mark_rect,
    _master_status_visual,
)


@pytest.fixture
def qapp():
    application = QApplication.instance()
    if isinstance(application, QApplication):
        return application
    return QApplication([])


@pytest.mark.parametrize(
    "status,label",
    [
        (TransactionStatus.PENDING, "进行中"),
        (TransactionStatus.COMPLETED, "已完成"),
        (TransactionStatus.PARTIAL, "部分完成"),
        (TransactionStatus.FAILED, "失败"),
        (TransactionStatus.UNDONE, "已撤销"),
        (TransactionStatus.UNDO_PARTIAL, "部分撤销"),
        (TransactionStatus.RECOVERY_REQUIRED, "需要检查"),
    ],
)
def test_status_mark_center_uses_actual_secondary_text_glyph_geometry(status, label):
    application = QApplication.instance()
    if not isinstance(application, QApplication):
        application = QApplication([])
    font = application.font()
    metrics = QFontMetricsF(font)
    secondary = QRectF(12, 30, 220, 22)

    mark = _master_status_mark_rect(secondary, metrics, label)
    baseline = secondary.top() + (secondary.height() - metrics.height()) / 2 + metrics.ascent()
    glyph_bounds = metrics.tightBoundingRect(label)
    expected_center = baseline + glyph_bounds.center().y()

    assert mark.center().y() == pytest.approx(expected_center)
    assert mark.width() == 14
    assert mark.height() == 14


@pytest.mark.parametrize(
    "status,label",
    [
        (TransactionStatus.PENDING, "进行中"),
        (TransactionStatus.COMPLETED, "已完成"),
        (TransactionStatus.PARTIAL, "部分完成"),
        (TransactionStatus.FAILED, "失败"),
        (TransactionStatus.UNDONE, "已撤销"),
        (TransactionStatus.UNDO_PARTIAL, "部分撤销"),
        (TransactionStatus.RECOVERY_REQUIRED, "需要检查"),
    ],
)
def test_rendered_status_mark_is_visually_centered_on_status_text(qapp, status, label):
    page = RenameHistoryPage(None)
    page._add_transaction_item(
        RenameTransactionSummary(
            "alignment",
            Path("library"),
            datetime(2026, 10, 6, tzinfo=UTC),
            status,
            operation_count=3,
        )
    )
    index = page.transaction_list.model().index(0, 0)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 248, 58)
    option.font = page.font()
    option.palette = page.palette()
    option.state = QStyle.StateFlag.State_Enabled
    image = QImage(248, 58, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    page.transaction_list.itemDelegate().paint(painter, option, index)
    painter.end()

    shape, color = _master_status_visual(status)
    assert shape
    secondary = QRectF(12, 30, 228, 22)
    metrics = QFontMetricsF(option.font)
    left = secondary.left() + metrics.horizontalAdvance("3 项 · ")
    icon_pixels = _colored_pixels(image, color, left, left + 14)
    text_pixels = _colored_pixels(image, color, left + 18, 240)
    icon_center = (min(y for _, y in icon_pixels) + max(y for _, y in icon_pixels)) / 2
    text_center = (min(y for _, y in text_pixels) + max(y for _, y in text_pixels)) / 2
    assert abs(icon_center - text_center) <= 1.1
    page.close()


def _colored_pixels(image: QImage, color: str, left: float, right: float) -> list[tuple[int, int]]:
    expected = QColor(color)
    return [
        (x, y)
        for x in range(max(0, int(left)), min(image.width(), int(right)))
        for y in range(30, 52)
        if (
            abs(image.pixelColor(x, y).red() - expected.red()) <= 24
            and abs(image.pixelColor(x, y).green() - expected.green()) <= 24
            and abs(image.pixelColor(x, y).blue() - expected.blue()) <= 24
        )
    ]


def test_detail_has_no_duplicate_transaction_header(qapp):
    page = RenameHistoryPage(None)

    assert not hasattr(page, "transaction_timestamp")
    assert not hasattr(page, "transaction_summary")
    assert page.detail_heading.text() == "事务详情"
    assert page.journal_facts_label is not None
    assert qapp is not None
    page.close()
