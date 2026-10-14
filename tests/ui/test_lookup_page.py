from datetime import UTC, datetime

import pytest
from PySide6.QtCore import QBuffer, QEvent, QEventLoop, QIODevice, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QEnterEvent, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QScrollArea, QStyle, QStyleOptionButton
from tests.services.test_lookup import FakeProvider

from dlsite_organizer.domain.relation import (
    Confidence,
    EvidenceType,
    RelationEvidence,
    RelationType,
    TranslationRole,
    WorkRelation,
)
from dlsite_organizer.domain.work import AgeCategory, TranslationAttribution, Work
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.historical_relations import (
    HistoricalRelation,
    HistoricalRelations,
)
from dlsite_organizer.services.lookup import LookupFreshness, LookupResult, LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.translation_relations import (
    TranslationAnalysis,
    TranslationAnalysisStatus,
)
from dlsite_organizer.ui.pages.lookup_page import LookupPage
from dlsite_organizer.ui.theme import light_stylesheet


def _png_bytes(width: int, height: int) -> bytes:
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor("#dce7f8"))
    painter = QPainter(image)
    painter.setPen(QColor("#365ea8"))
    painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, "TEST COVER")
    painter.end()
    buffer = QBuffer()
    assert buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, "PNG")  # pyright: ignore[reportCallIssue, reportArgumentType]
    content = bytes(buffer.data())  # pyright: ignore[reportArgumentType]
    buffer.close()
    return content


def _render_button_state(button, state: QStyle.StateFlag) -> QImage:
    option = QStyleOptionButton()
    option.initFrom(button)
    option.rect = button.rect()
    option.text = button.text()
    option.state = QStyle.StateFlag.State_Enabled | state
    image = QImage(button.size(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    button.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, button)
    painter.end()
    return image


@pytest.fixture
def qapp() -> QApplication:
    application = QApplication.instance()
    if isinstance(application, QApplication):
        return application
    return QApplication([])


def historical_relation(target: str) -> HistoricalRelation:
    when = datetime(2026, 8, 30, 9, 0, tzinfo=UTC)
    return HistoricalRelation(
        subject_workno="RJ01636949",
        relation_type=RelationType.TRANSLATION_OF,
        target_workno=target,
        confidence=Confidence.CONFIRMED,
        first_seen=when,
        last_seen=when,
        observation_count=1,
        evidence=(),
    )


def test_lookup_page_uses_generic_work_code_placeholder_and_subtitle(
    qapp: QApplication,
) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())

    assert page.code_input.placeholderText() == "输入完整RJ|BJ|VJ号"
    assert "RJ01609020" not in page.code_input.placeholderText()
    labels = "\n".join(label.text() for label in page.findChildren(QLabel))
    assert "读取当前作品信息并生成安全的格式化名称。" in labels
    assert "输入 RJcode" not in labels
    assert page.status_label.text() == "请输入作品编号开始查询。"
    assert page.status_label.property("state") != "error"
    assert page.identity_frame.isHidden()
    assert page.name_section.isHidden()
    assert page.metadata_frame.isHidden()
    page.close()


def test_lookup_page_idle_and_no_result_error_hide_result_sections(
    qapp: QApplication,
) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())

    page._show_error("未找到作品。")

    assert page.identity_frame.isHidden()
    assert page.name_section.isHidden()
    assert page.metadata_frame.isHidden()
    assert page.status_label.text() == "未找到作品。"
    assert not page.status_label.isHidden()
    page.close()


@pytest.mark.parametrize("workno", ["RJ00000001", "BJ00000001", "VJ00000001"])
def test_lookup_page_starts_lookup_for_every_supported_work_code(
    qapp: QApplication, workno: str
) -> None:
    page = LookupPage(
        LookupService(
            FakeProvider(work=Work(workno=workno, title="Fixture title")),
            NamingService(),
        ),
        CoverService(),
    )
    page.code_input.setText(workno.lower())

    page.start_lookup()
    thread = page._thread
    assert thread is not None

    loop = QEventLoop()
    thread.finished.connect(loop.quit)
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    qapp.processEvents()

    assert page.status_label.text().startswith("查询完成")
    assert page.workno_value.text() == workno
    page.close()


def test_lookup_page_renders_application_relation_result(qapp: QApplication) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    relation = WorkRelation(
        source_workno="RJ01636949",
        target_workno="RJ01609020",
        relation_type=RelationType.TRANSLATION_OF,
        confidence=Confidence.CONFIRMED,
        evidence=[
            RelationEvidence(
                evidence_type=EvidenceType.EXPLICIT_TRANSLATION_REFERENCE,
                description="fixture",
            )
        ],
        detection_source="fixture",
    )
    result = LookupResult(
        work=Work(workno="RJ01636949", title="翻译作品"),
        formatted_name="[RJ01636949] 翻译作品",
        translation=TranslationAnalysis(
            role=TranslationRole.TRANSLATION_PARENT,
            relations=(relation,),
            language="CHI_HANS",
            status=TranslationAnalysisStatus.CONFIRMED,
        ),
    )

    page._show_result(result)

    assert page.relation_role_value.text() == "翻译父条目"
    assert page.relation_source_value.text() == "DLsite 明确提供"
    assert page.relation_confidence_value.text() == "已确认"
    assert page.relation_language_value.text() == "CHI_HANS"
    assert page.relation_details_value.text() == "翻译原作：RJ01609020"
    assert not page.relation_frame.isHidden()
    page.close()


def test_lookup_page_renders_rich_work_fields_and_separate_translation_attribution(
    qapp: QApplication,
) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    result = LookupResult(
        work=Work(
            workno="RJ01637033",
            title="中性测试作品",
            maker_id="RG01058997",
            maker_name="原作社团",
            series_name="测试系列",
            cvs=["CV A", "CV B"],
            tags=["ASMR", "标签"],
            language="CHI_HANS",
            age_category=AgeCategory.R18,
        ),
        formatted_name="[原作社团][RJ01637033] 中性测试作品",
        translation_attribution=TranslationAttribution(
            maker_id="RG01001331",
            maker_name="翻译署名",
        ),
    )

    page._show_result(result)

    assert page.maker_value.text() == "原作社团"
    assert page.maker_id_value.text() == "RG01058997"
    assert page.series_value.text() == "测试系列"
    assert page.cvs_value.text() == "CV A、CV B"
    assert page.tags_value.text() == "ASMR、标签"
    assert page.language_value.text() == "简体中文（CHI_HANS）"
    assert page.age_value.text() == "R18"
    assert page.relation_attribution_value.text() == "翻译署名 (RG01001331)"
    assert page.maker_value.text() != page.relation_attribution_value.text()
    assert not page.relation_frame.isHidden()
    page.close()


def test_lookup_page_renders_missing_rich_fields_as_dashes(qapp: QApplication) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())

    page._show_result(
        LookupResult(
            work=Work(workno="RJ01609020", title="只有标题"),
            formatted_name="[RJ01609020] 只有标题",
        )
    )

    assert page.maker_value.text() == "—"
    assert page.maker_id_value.text() == "—"
    assert page.series_value.text() == "—"
    assert page.cvs_value.text() == "—"
    assert page.tags_value.text() == "—"
    assert page.language_value.text() == "—"
    assert page.age_value.text() == "未知"
    assert page.relation_attribution_value.text() == "—"
    assert page.relation_frame.isHidden()
    page.close()


@pytest.mark.parametrize(
    ("freshness", "label"),
    [
        (LookupFreshness.LIVE, "实时"),
        (LookupFreshness.CACHE_FRESH, "缓存"),
        (LookupFreshness.CACHE_STALE_FALLBACK, "旧缓存"),
    ],
)
def test_lookup_page_shows_delivery_freshness(
    qapp: QApplication, freshness: LookupFreshness, label: str
) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())

    page._show_result(
        LookupResult(
            work=Work(workno="RJ01609020", title="标题"),
            formatted_name="[RJ01609020] 标题",
            freshness=freshness,
            source="DLSITE_PRODUCT_INFO_AJAX",
        )
    )

    assert label in page.status_label.text()
    page.close()


def test_copy_acknowledgement_does_not_replace_freshness(qapp: QApplication) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    page._show_result(
        LookupResult(
            work=Work(workno="RJ01609020", title="标题"),
            formatted_name="[RJ01609020] 标题",
            freshness=LookupFreshness.CACHE_STALE_FALLBACK,
        )
    )

    page.copy_name()

    assert page.status_label.text() == "查询完成 · 旧缓存"
    assert page.copy_feedback.text() == "已复制"
    assert QApplication.clipboard().text() == "[RJ01609020] 标题"
    QTest.qWait(page._copy_feedback_timer.interval() + 100)
    assert page.copy_feedback.text() == ""
    page.close()


def test_lookup_page_provider_text_is_plain_and_name_is_selectable(qapp: QApplication) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    title = "<b>不应加粗</b> " + "很长的タイトル" * 15
    name = "[RJ00000001] " + "Folder/" * 35
    page._show_result(
        LookupResult(
            work=Work(workno="RJ00000001", title=title),
            formatted_name=name,
        )
    )

    assert page.title_value.text() == title
    assert page.title_value.textFormat() == Qt.TextFormat.PlainText
    assert page.name_output.text() == name
    assert page.name_output.textFormat() == Qt.TextFormat.PlainText
    assert page.name_output.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByMouse
    page.close()


def test_lookup_primary_controls_tab_order_and_formatted_name_keyboard_activation(
    qapp: QApplication,
) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    work = Work(workno="RJ01609020", title="键盘测试")
    formatted_name = NamingService().format(work)
    page._show_result(
        LookupResult(
            work=work,
            formatted_name=formatted_name,
        )
    )
    page.show()
    page.code_input.setFocus()
    qapp.processEvents()

    QTest.keyClick(page.code_input, Qt.Key.Key_Tab)
    assert qapp.focusWidget() is page.query_button
    QTest.keyClick(page.query_button, Qt.Key.Key_Tab)
    assert qapp.focusWidget() is page.refresh_button
    QTest.keyClick(page.refresh_button, Qt.Key.Key_Backtab)
    assert qapp.focusWidget() is page.query_button
    QTest.keyClick(page.query_button, Qt.Key.Key_Backtab)
    assert qapp.focusWidget() is page.code_input

    page.name_output.setFocus()
    qapp.processEvents()
    assert qapp.focusWidget() is page.name_output
    QTest.keyClick(page.name_output, Qt.Key.Key_Space)

    assert QApplication.clipboard().text() == formatted_name
    assert page.copy_feedback.text() == "已复制"
    assert page.status_label.text() == "查询完成 · 实时"
    page.close()


def test_formatted_name_enter_activates_complete_copy(qapp: QApplication) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    expected = "[RJ00000001] [complete] 名称/完整"
    page._show_result(
        LookupResult(work=Work(workno="RJ00000001", title="标题"), formatted_name=expected)
    )
    page.show()
    page.name_output.setFocus()
    qapp.processEvents()
    QTest.keyClick(page.name_output, Qt.Key.Key_Return)

    assert QApplication.clipboard().text() == expected
    assert page.copy_feedback.text() == "已复制"
    page.close()


def test_lookup_page_force_refresh_uses_bypass_cache_path_and_is_disabled_while_busy(
    qapp: QApplication,
) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    page.code_input.setText("RJ01609020")
    calls: list[bool] = []
    page._run_lookup = lambda *, force_refresh: calls.append(force_refresh)

    page.force_refresh()
    assert calls == [True]

    page._thread = object()  # type: ignore[assignment]
    page.force_refresh()
    assert calls == [True]
    page._thread = None
    page._set_loading(True)
    assert not page.refresh_button.isEnabled()
    page._set_loading(False)
    assert page.refresh_button.isEnabled()
    page.close()


@pytest.mark.parametrize("width,height", [(960, 640), (1160, 800), (1540, 900)])
def test_lookup_query_buttons_share_geometry_and_keep_distinct_roles(
    qapp: QApplication, width: int, height: int
) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    page.resize(width - 184, height - 40)
    page.setStyleSheet(light_stylesheet())
    page.show()
    qapp.processEvents()

    assert page.query_button.size() == page.refresh_button.size()
    assert page.query_button.height() == page.refresh_button.height() >= 40
    assert page.query_button.geometry().right() < page.refresh_button.geometry().left()
    assert page.query_button.property("visualRole") == "primary"
    assert page.refresh_button.property("visualRole") == "secondary"
    page._set_loading(True)
    assert not page.query_button.isEnabled()
    assert not page.refresh_button.isEnabled()
    page._set_loading(False)
    assert page.query_button.isEnabled()
    assert page.refresh_button.isEnabled()
    page.close()


def test_lookup_query_buttons_show_hover_pressed_and_focus_feedback(qapp: QApplication) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    page.setStyleSheet(light_stylesheet())
    page.show()
    qapp.processEvents()

    for button in (page.query_button, page.refresh_button):
        normal = _render_button_state(button, QStyle.StateFlag.State_None)
        hover = _render_button_state(button, QStyle.StateFlag.State_MouseOver)
        assert hover != normal
        pressed = _render_button_state(button, QStyle.StateFlag.State_Sunken)
        assert pressed != hover
        focused = _render_button_state(button, QStyle.StateFlag.State_HasFocus)
        assert focused != normal

    page.close()


@pytest.mark.parametrize(
    ("width", "height", "source_size", "expected_range"),
    [
        (960, 640, (1600, 900), (250, 270)),
        (1160, 800, (1600, 900), (300, 320)),
        (1540, 900, (1600, 900), (340, 380)),
        (960, 640, (600, 900), (200, 230)),
        (960, 640, (800, 800), (250, 270)),
    ],
)
def test_lookup_cover_uses_available_width_and_preserves_source_aspect(
    qapp: QApplication,
    width: int,
    height: int,
    source_size: tuple[int, int],
    expected_range: tuple[int, int],
) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    # MainWindow reserves 184 logical px for navigation/chrome around this page.
    page.resize(width - 184, height - 40)
    page._show_result(
        LookupResult(work=Work(workno="RJ00000001", title="景色标题"), formatted_name="name")
    )
    page.show()
    qapp.processEvents()
    page._show_cover(_png_bytes(*source_size))
    qapp.processEvents()

    displayed = page.cover_label.pixmap().size()
    ratio_error = abs(displayed.width() / displayed.height() - source_size[0] / source_size[1])
    assert expected_range[0] <= displayed.width() <= expected_range[1]
    assert ratio_error < 0.01
    assert page.cover_label.size() == displayed
    scroll = page.findChild(QScrollArea)
    assert scroll is not None
    assert scroll.horizontalScrollBar().maximum() == 0

    page.resize(
        {960: 1160, 1160: 1540, 1540: 960}[width] - 184,
        {960: 800, 1160: 900, 1540: 640}[width] - 40,
    )
    qapp.processEvents()
    resized = page.cover_label.pixmap().size()
    assert abs(resized.width() / resized.height() - source_size[0] / source_size[1]) < 0.01
    page.close()


def test_lookup_cover_missing_and_invalid_images_keep_restrained_placeholder(
    qapp: QApplication,
) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    page._show_result(
        LookupResult(work=Work(workno="RJ00000001", title="封面缺失"), formatted_name="name")
    )
    assert page.cover_label.text() == "暂无封面"
    initial_size = page.cover_label.size()
    page._show_cover(b"not an image")
    assert page.cover_label.text() == "暂无封面"
    assert page.cover_label.size() == initial_size
    page.close()


def test_lookup_page_renders_current_and_historical_confirmed_relations(
    qapp: QApplication,
) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    current = WorkRelation(
        source_workno="RJ01636949",
        target_workno="RJ01609020",
        relation_type=RelationType.TRANSLATION_OF,
        confidence=Confidence.CONFIRMED,
        evidence=[],
        detection_source="fixture",
    )

    page._show_result(
        LookupResult(
            work=Work(workno="RJ01636949", title="源作品"),
            formatted_name="[RJ01636949] 源作品",
            translation=TranslationAnalysis(
                role=TranslationRole.TRANSLATION_PARENT,
                relations=(current,),
                status=TranslationAnalysisStatus.CONFIRMED,
            ),
            historical_relations=HistoricalRelations(
                outgoing=(historical_relation("RJ01636950"),)
            ),
        )
    )

    assert "翻译原作：RJ01609020" in page.relation_details_value.text()
    assert "RJ01636950" in page.historical_relations_value.text()
    labels = "\n".join(label.text() for label in page.findChildren(QLabel))
    assert all(term not in labels for term in ("候选", "人工", "Candidate", "Review"))
    page.close()


@pytest.mark.parametrize(("width", "height"), [(960, 640), (1160, 800), (1540, 900)])
def test_formatted_name_is_focusable_copy_target_and_wraps(
    qapp: QApplication, width: int, height: int
) -> None:
    formatted_name = "[RJ01609020][测试社团]" + "静かな夜の物語与中文故事" * 90
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    page.resize(width, height)
    page._show_result(
        LookupResult(
            work=Work(workno="RJ01609020", title="静かな夜の物語", maker_name="测试社团"),
            formatted_name=formatted_name,
        )
    )
    page.show()
    qapp.processEvents()

    heading = next(
        label
        for label in page.name_section.findChildren(QLabel)
        if label.text() == "格式化名称"
    )
    heading_right = heading.mapTo(page.name_section, heading.rect().topRight()).x()
    content_width = page.name_section.width()

    assert heading_right < content_width
    assert not hasattr(page, "copy_button")
    assert page.name_output.text() == formatted_name
    assert page.name_output.wordWrap()
    assert page.name_output.height() > page.name_output.fontMetrics().height()
    assert page.name_output.textInteractionFlags() & Qt.TextInteractionFlag.TextSelectableByKeyboard
    assert page.name_output.focusPolicy() != Qt.FocusPolicy.NoFocus
    page.name_output.setFocus()
    qapp.processEvents()
    icon = page.name_output.copy_affordance
    assert icon.isVisible()
    assert icon.geometry().right() < page.name_output.width()
    assert "padding: 4px 28px 4px 6px" in page.name_output.styleSheet()
    QTest.keyClick(page.name_output, Qt.Key.Key_Space)
    assert QApplication.clipboard().text() == formatted_name
    assert page.copy_feedback.text() == "已复制"
    assert page.status_label.text() == "查询完成 · 实时"

    scroll = page.findChild(QScrollArea)
    assert scroll is not None
    assert scroll.horizontalScrollBar().maximum() == 0
    page.close()


def _wait_for_click_copy(qapp: QApplication) -> None:
    QTest.qWait(QApplication.doubleClickInterval() + 30)
    qapp.processEvents()


def test_formatted_name_single_click_copies_exact_authoritative_value_once(
    qapp: QApplication,
) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    expected = "[RJ00000001] " + "完整格式化名称/" * 12
    page._show_result(
        LookupResult(work=Work(workno="RJ00000001", title="标题"), formatted_name=expected)
    )
    page.show()
    qapp.processEvents()
    copies: list[str] = []
    clipboard = QApplication.clipboard()
    clipboard.dataChanged.connect(lambda: copies.append(clipboard.text()))
    QTest.mouseClick(page.name_output, Qt.MouseButton.LeftButton, pos=QPoint(20, 16))
    _wait_for_click_copy(qapp)

    assert copies == [expected]
    assert QApplication.clipboard().text() == expected
    assert page.copy_feedback.text() == "已复制"
    page.close()


def test_formatted_name_drag_and_double_click_selection_do_not_copy_whole_value(
    qapp: QApplication,
) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    expected = "[RJ00000001] selection should survive"
    page._show_result(
        LookupResult(work=Work(workno="RJ00000001", title="标题"), formatted_name=expected)
    )
    page.show()
    qapp.processEvents()
    clipboard = QApplication.clipboard()
    clipboard.setText("sentinel")
    start = QPoint(4, page.name_output.height() // 2)
    end = QPoint(min(page.name_output.width() - 4, 100), page.name_output.height() // 2)
    QTest.mousePress(page.name_output, Qt.MouseButton.LeftButton, pos=start)
    QTest.mouseMove(page.name_output, end, delay=50)
    QTest.mouseRelease(page.name_output, Qt.MouseButton.LeftButton, pos=end)
    _wait_for_click_copy(qapp)
    assert clipboard.text() == "sentinel"
    assert page.name_output.hasSelectedText()

    page.name_output.setSelection(0, 0)
    QTest.mouseDClick(
        page.name_output,
        Qt.MouseButton.LeftButton,
        pos=QPoint(10, page.name_output.height() // 2),
    )
    _wait_for_click_copy(qapp)
    assert clipboard.text() == "sentinel"
    assert page.name_output.hasSelectedText()
    assert page.name_output.selectedText() != expected
    QTest.keyClick(page.name_output, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
    assert clipboard.text() == page.name_output.selectedText()

    clipboard.setText("right click sentinel")
    QTest.mouseClick(
        page.name_output,
        Qt.MouseButton.RightButton,
        pos=QPoint(20, page.name_output.height() // 2),
    )
    assert clipboard.text() == "right click sentinel"
    page.close()


def test_formatted_name_does_not_copy_while_empty_or_after_query_failure(
    qapp: QApplication,
) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    QApplication.clipboard().setText("sentinel")
    page.show()
    QTest.mouseClick(page.name_output, Qt.MouseButton.LeftButton)
    _wait_for_click_copy(qapp)
    assert QApplication.clipboard().text() == "sentinel"

    page._show_result(
        LookupResult(work=Work(workno="RJ00000001", title="标题"), formatted_name="fresh name")
    )
    page._clear_result()
    QTest.mouseClick(page.name_output, Qt.MouseButton.LeftButton)
    _wait_for_click_copy(qapp)
    assert QApplication.clipboard().text() == "sentinel"
    page.close()


def test_pending_single_click_never_copies_a_replaced_result(qapp: QApplication) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    page._show_result(
        LookupResult(work=Work(workno="RJ00000001", title="first"), formatted_name="first result")
    )
    page.show()
    qapp.processEvents()
    QApplication.clipboard().setText("sentinel")
    QTest.mouseClick(page.name_output, Qt.MouseButton.LeftButton, pos=QPoint(20, 16))
    page._show_result(
        LookupResult(work=Work(workno="RJ00000002", title="second"), formatted_name="second result")
    )
    _wait_for_click_copy(qapp)

    assert QApplication.clipboard().text() == "sentinel"
    assert page.copy_feedback.text() == ""
    page.close()


def test_formatted_name_copy_affordance_is_contextual_and_mouse_transparent(
    qapp: QApplication,
) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    expected = "[RJ00000001] contextual copy"
    page._show_result(
        LookupResult(work=Work(workno="RJ00000001", title="标题"), formatted_name=expected)
    )
    page.show()
    qapp.processEvents()

    icon = page.name_output.copy_affordance
    assert icon.isHidden()

    QApplication.sendEvent(
        page.name_output,
        QEnterEvent(QPointF(24, 20), QPointF(24, 20), QPointF(0, 0)),
    )
    qapp.processEvents()
    assert not icon.isHidden()
    assert icon.text() == "⧉"
    assert icon.focusPolicy() == Qt.FocusPolicy.NoFocus
    assert icon.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    QApplication.sendEvent(page.name_output, QEvent(QEvent.Type.Leave))
    qapp.processEvents()
    assert icon.isHidden()

    page.name_output.setFocus()
    qapp.processEvents()
    assert not icon.isHidden()
    page.name_output.clearFocus()
    qapp.processEvents()
    assert icon.isHidden()

    # The overlay remains part of the name region's original click target.
    icon_center = icon.geometry().center()
    QTest.mouseClick(page.name_output, Qt.MouseButton.LeftButton, pos=icon_center)
    _wait_for_click_copy(qapp)
    assert QApplication.clipboard().text() == expected
    assert icon.text() == "✓"
    assert page.status_label.text() == "查询完成 · 实时"
    page.close()


def test_formatted_name_copy_acknowledgement_expires_to_contextual_icon(
    qapp: QApplication,
) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    page._show_result(
        LookupResult(
            work=Work(workno="RJ00000001", title="标题"),
            formatted_name="[RJ00000001] acknowledged",
        )
    )
    page.show()
    qapp.processEvents()
    page.name_output.setFocus()
    page.copy_name()
    assert page.name_output.copy_affordance.text() == "✓"

    QTest.qWait(page._copy_feedback_timer.interval() + 100)
    qapp.processEvents()
    assert page.copy_feedback.text() == ""
    assert page.name_output.copy_affordance.text() == "⧉"
    page.name_output.clearFocus()
    assert page.name_output.copy_affordance.isHidden()
    page.close()


def test_result_replacement_clears_copy_acknowledgement(qapp: QApplication) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    page._show_result(
        LookupResult(work=Work(workno="RJ00000001", title="first"), formatted_name="first name")
    )
    page.show()
    page.name_output.setFocus()
    page.copy_name()
    assert page.name_output.copy_affordance.text() == "✓"

    page._show_result(
        LookupResult(work=Work(workno="RJ00000002", title="second"), formatted_name="second name")
    )
    assert page.name_output.copy_affordance.text() == "⧉"
    assert page.copy_feedback.text() == ""
    QApplication.clipboard().setText("sentinel")
    QTest.qWait(page._copy_feedback_timer.interval() + 100)
    assert QApplication.clipboard().text() == "sentinel"
    assert page.name_output.copy_affordance.text() == "⧉"
    assert page.status_label.text() == "查询完成 · 实时"
    page.close()


@pytest.mark.parametrize("title", ["普通作品标题", "長い日本語タイトルと中文作品名称・" * 30])
def test_identity_fact_columns_align_with_title_and_long_maker(
    qapp: QApplication, title: str
) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())
    page.resize(480, 640)
    page._show_result(
        LookupResult(
            work=Work(
                workno="RJ01609020",
                title=title,
                maker_name="社团名称很长" * 30,
            ),
            formatted_name="[RJ01609020] 标题",
        )
    )
    page.show()
    qapp.processEvents()

    captions = page.identity_frame.findChildren(QLabel, "lookupFactCaption")
    assert [caption.text() for caption in captions] == ["作品编号", "社团"]
    caption_x = [
        caption.mapTo(page.identity_frame, caption.rect().topLeft()).x()
        for caption in captions
    ]
    value_x = [
        value.mapTo(page.identity_frame, value.rect().topLeft()).x()
        for value in (page.workno_value, page.maker_value)
    ]
    row_top_deltas = [
        abs(
            caption.mapTo(page.identity_frame, caption.rect().topLeft()).y()
            - value.mapTo(page.identity_frame, value.rect().topLeft()).y()
        )
        for caption, value in zip(captions, (page.workno_value, page.maker_value), strict=True)
    ]

    assert caption_x[0] == caption_x[1]
    assert value_x[0] == value_x[1]
    assert row_top_deltas == [0, 0]
    assert page.workno_value.text() == "RJ01609020"
    assert page.maker_value.text() == "社团名称很长" * 30
    assert page.maker_value.wordWrap()
    assert page.maker_value.height() > page.maker_value.fontMetrics().height()
    assert page.title_value.text() == title

    title_bottom = page.title_value.mapTo(
        page.identity_frame, page.title_value.rect().bottomLeft()
    ).y()
    workno_top = page.workno_value.mapTo(
        page.identity_frame, page.workno_value.rect().topLeft()
    ).y()
    workno_bottom = page.workno_value.mapTo(
        page.identity_frame, page.workno_value.rect().bottomLeft()
    ).y()
    maker_top = page.maker_value.mapTo(page.identity_frame, page.maker_value.rect().topLeft()).y()
    assert 0 <= workno_top - title_bottom <= 16
    assert 0 <= maker_top - workno_bottom <= 12

    scroll = page.findChild(QScrollArea)
    assert scroll is not None
    assert scroll.horizontalScrollBar().maximum() == 0
    page.close()
