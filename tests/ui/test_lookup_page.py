import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Event

import httpx
import pytest
from PySide6.QtCore import QBuffer, QEvent, QEventLoop, QIODevice, QPoint, QPointF, Qt, QTimer
from PySide6.QtGui import QColor, QEnterEvent, QImage, QPainter, QTextDocument
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStyle,
    QStyleOptionButton,
    QToolButton,
)
from sqlalchemy import delete
from tests.services.test_lookup import FakeProvider
from tests.ui.test_navigation_characterization import _wait_until

from dlsite_organizer.domain.bonus import BonusMatch
from dlsite_organizer.domain.manual_review import CandidateReviewOutcome
from dlsite_organizer.domain.relation import (
    Confidence,
    EvidenceType,
    RelationEvidence,
    RelationType,
    TranslationRole,
    WorkRelation,
)
from dlsite_organizer.domain.work import AgeCategory, TranslationAttribution, Work
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.manual_reviews import ManualReviewRepository
from dlsite_organizer.persistence.metadata_store import MetadataStore, WorkMetadataCache
from dlsite_organizer.providers.dlsite.client import DlsiteProvider
from dlsite_organizer.services import bonus_search
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.historical_relations import (
    HistoricalRelation,
    HistoricalRelations,
)
from dlsite_organizer.services.lookup import LookupFreshness, LookupResult, LookupService
from dlsite_organizer.services.manual_reviews import ManualReviewService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.translation_discovery import TranslationListings
from dlsite_organizer.services.translation_relations import (
    TranslationAnalysis,
    TranslationAnalysisStatus,
)
from dlsite_organizer.ui.pages.lookup_page import LookupPage
from dlsite_organizer.ui.theme import light_stylesheet


def test_local_bonus_lookup_uses_the_normal_cover_pipeline(qapp, monkeypatch, tmp_path):
    main, bonus = "RJ01741384", "RJ01741449"
    images = {main: _png_bytes(24, 16), bonus: _png_bytes(32, 24)}
    cover_host = "https://example.test"
    client = httpx.Client
    requests = []
    offline = False
    missing = False
    now = datetime(2026, 11, 13, tzinfo=UTC)
    products = {
        main: {
            "work_name": "Main", "maker_id": "RG42470", "regist_date": "2026-11-06",
            "work_image": f"{cover_host}/{main}.png",
            "bonuses": [{"title": "期間限定特典", "end_date": "2026-11-19"}],
        },
        bonus: {
            "work_name": "期間限定特典", "maker_id": "RG42470", "regist_date": "2026-11-06",
            "price": 0, "is_sale": False, "is_free": True, "is_oly": True,
            "work_image": f"{cover_host}/{bonus}.png",
        },
    }

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if offline:
            raise httpx.ConnectError("offline", request=request)
        if request.url.host == "example.test":
            code = request.url.path.removeprefix("/").removesuffix(".png")
            return httpx.Response(200, content=images[code], headers={"Content-Type": "image/png"})
        if request.url.path.endswith("/ajax"):
            codes = request.url.params["product_id"].split(",")
            return httpx.Response(200, json={code: products[code] for code in codes
                                            if code in products and not missing})
        return httpx.Response(404)

    def mock_client(**kwargs):
        return client(transport=httpx.MockTransport(respond), **kwargs)

    monkeypatch.setattr(httpx, "Client", mock_client)
    monkeypatch.setattr(bonus_search, "BONUS_REQUEST_INTERVAL", 0.0)
    database = Database(tmp_path / "metadata.sqlite3")
    database.initialize()
    store = MetadataStore(database)
    lookup = LookupService(
        DlsiteProvider(client_factory=mock_client), NamingService(),
        metadata_store=store, clock=lambda: now,
        manual_review_service=ManualReviewService(ManualReviewRepository(database)),
    )
    page = LookupPage(lookup, CoverService())
    page.show()

    def query(code: str) -> None:
        page.code_input.setText(code)
        page.start_lookup()
        _wait_until(qapp, lambda: not page.is_busy())

    def freshness() -> LookupFreshness:
        result = page._current_result
        assert result is not None
        return result.freshness

    try:
        query(main)
        assert page._cover_content == images[main]
        match = lookup.bonus_search().search(
            bonus_search.BonusSearchRequest(main, "RG42470", date(2026, 11, 6), bonus),
            cancelled=lambda: False, progress=lambda _: None,
        ).matches
        assert len(match) == 1 and store.list_limited_bonus_matches(main) == match

        # Simulate reopening the app with a warm SQLite cache and cold cover memory.
        page.close()
        page = LookupPage(lookup, CoverService())
        page.show()
        requests.clear()
        query(main)
        assert page._cover_content == images[main]
        assert requests == [f"{cover_host}/{main}.png"]

        # A saved relation and observation can survive eviction of the work metadata.
        with database.session() as session:
            session.execute(delete(WorkMetadataCache).where(WorkMetadataCache.workno == bonus))
            session.commit()
        requests.clear()
        link = next(label for label in page.bonus_review_widget.findChildren(QLabel)
                    if f'href="{bonus}"' in label.text())
        link.linkActivated.emit(bonus)
        _wait_until(qapp, lambda: not page.is_busy())
        assert page._cover_content == images[bonus]
        assert freshness() is LookupFreshness.LIVE
        assert len(requests) == 3
        cached_bonus = store.get(bonus)
        assert cached_bonus and cached_bonus.work.cover_url == f"{cover_host}/{bonus}.png"

        requests.clear()
        page.back_button.click()
        assert page._cover_content == images[main]
        page.forward_button.click()
        assert page._cover_content == images[bonus]
        query(main)
        assert page._cover_content == images[main]
        assert requests == []

        now += timedelta(days=2)
        offline = True
        query(main)
        assert freshness() is LookupFreshness.CACHE_STALE_FALLBACK
        assert page._cover_content == images[main]
        assert len(requests) == 2 and all(url.startswith("https://www.dlsite.com/")
                                          for url in requests)
        assert store.list_limited_bonus_matches(main) == match
        assert store.get(bonus) == cached_bonus

        requests.clear()
        offline = False
        page.force_refresh()
        _wait_until(qapp, lambda: not page.is_busy())
        assert freshness() is LookupFreshness.LIVE
        assert page._cover_content == images[main]
        assert len(requests) == 3 and requests[-1] == f"{cover_host}/{main}.png"

        with database.session() as session:
            session.execute(delete(WorkMetadataCache).where(WorkMetadataCache.workno == main))
            session.commit()
        offline = True
        query(main)
        assert freshness() is LookupFreshness.LOCAL_BONUS_REVIEW
        assert page._cover_content == images[main]
        assert store.list_limited_bonus_matches(main) == match
        assert store.get(main) is None
        offline = False
        missing = True
        query(main)
        assert freshness() is LookupFreshness.LOCAL_BONUS_REVIEW
        assert page._cover_content == images[main]
        assert store.list_limited_bonus_matches(main) == match
    finally:
        _wait_until(qapp, lambda: not page.is_busy())
        page.close()
        database.dispose()


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


def test_history_ignores_abandoned_cover_translation_and_bonus_callbacks(
    qapp: QApplication, monkeypatch: pytest.MonkeyPatch,
) -> None:
    a, b = "RJ00000001", "RJ00000002"
    covers = {a: _png_bytes(24, 16), b: _png_bytes(32, 24)}
    started, release = Event(), Event()
    background_writes: list[str] = []

    class Provider:
        def fetch_work(self, workno: str) -> Work:
            return Work(workno=workno, title=workno, cover_url="https://example.test/cover.png")

    class Covers(CoverService):
        def fetch_for_work(self, workno: str, url: str) -> bytes:
            if workno == b:
                started.set()
                release.wait(timeout=5)
            self.remember_cached_cover(workno, covers[workno])
            return covers[workno]

    def discover(_service, result, *, publish, cancelled, allow_online):
        if result.work.workno == b:
            assert cancelled()
            background_writes.append(b)
        publish(TranslationListings(original=result.work.workno))

    def scan(_service, request, *, cancelled, progress):
        if request.local_only:
            return bonus_search.BonusSearchResult(request.main_workno)
        started.set()
        release.wait(timeout=5)
        assert cancelled()
        progress("旧页面进度")
        return bonus_search.BonusSearchResult(
            request.main_workno, notices=("旧页面结果",), cancelled=True,
            scan=bonus_search.BonusScanProgress(
                "RJ00000003", "RJ00000102", 100, 5, 1, 4, 0, 0, 1, (1, 100),
            ),
        )

    monkeypatch.setattr(LookupService, "discover_translations", discover)
    monkeypatch.setattr(bonus_search.BonusSearchService, "search", scan)
    page = LookupPage(LookupService(Provider(), NamingService()), Covers())
    page.show()
    try:
        page.code_input.setText(a)
        page.start_lookup()
        _wait_until(qapp, lambda: not page.is_busy())
        page.code_input.setText(b)
        page.start_lookup()
        assert started.wait(timeout=2)
        _wait_until(qapp, lambda: page.workno_value.text() == b)
        assert not page.cover_status_label.isHidden()
        assert page.cover_label.text() != "暂无封面"
        page.back_button.click()
        release.set()
        _wait_until(qapp, lambda: not page.is_busy())
        assert background_writes == [b]
        assert page.workno_value.text() == a
        assert page._cover_content == covers[a]
        assert page._translation_listings == TranslationListings(original=a)

        page.forward_button.click()
        assert page._cover_content == covers[b]
        started.clear()
        release.clear()
        page.bonus_search_button.click()
        assert started.wait(timeout=2)
        page.back_button.click()
        page.forward_button.click()
        page.back_button.click()
        release.set()
        _wait_until(qapp, lambda: not page.is_busy())
        assert page.workno_value.text() == a
        assert page.bonus_status.isHidden()
        assert "旧页面结果" not in page.bonus_output.text()
        assert page.bonus_search_button.isEnabled()
        page.forward_button.click()
        assert page.workno_value.text() == b
        assert "已核验 1 / 100 个编号" in rendered_text(page.bonus_output)
        assert page.bonus_search_button.text() == "继续搜索剩余范围"
    finally:
        release.set()
        _wait_until(qapp, lambda: not page.is_busy())
        page.close()


def _render_button_state(
    button: QPushButton, state: QStyle.StateFlag, *, enabled: bool = True,
) -> QImage:
    option = QStyleOptionButton()
    option.initFrom(button)
    option.rect = button.rect()
    option.text = button.text()
    option.state = (QStyle.StateFlag.State_Enabled | state) if enabled else state
    image = QImage(button.size(), QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    button.style().drawControl(QStyle.ControlElement.CE_PushButton, option, painter, button)
    painter.end()
    return image


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    application = QApplication.instance()
    if isinstance(application, QApplication):
        return application
    return QApplication([])



def rendered_text(label: QLabel) -> str:
    doc = QTextDocument()
    doc.setHtml(label.text())
    return doc.toPlainText()

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

    assert page.code_input.placeholderText() == "输入完整作品编号（RJ / BJ / VJ）"
    assert "RJ01609020" not in page.code_input.placeholderText()
    labels = "\n".join(label.text() for label in page.findChildren(QLabel))
    assert "读取当前作品信息并生成安全的格式化名称。" in labels
    assert "输入 RJcode" not in labels
    assert page.status_label.text() == "请输入作品编号开始查询。"
    assert page.status_label.property("state") != "error"
    assert page.identity_frame.isHidden()
    assert page.name_section.isHidden()
    page.close()


def test_lookup_page_idle_and_no_result_error_hide_result_sections(
    qapp: QApplication,
) -> None:
    page = LookupPage(LookupService(FakeProvider(), NamingService()), CoverService())

    page._show_error("未找到作品。")

    assert page.identity_frame.isHidden()
    assert page.name_section.isHidden()
    assert page.status_label.text() == "未找到作品。"
    assert not page.status_label.isHidden()
    page.close()


def test_lookup_page_verifies_known_bonus_after_regular_lookup(qapp: QApplication) -> None:
    main = "RJ01690645"
    bonus = "RJ01690654"
    calls: list[str] = []

    def response(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/ajax"):
            return httpx.Response(404)
        code = request.url.params["product_id"]
        calls.append(code)
        product = {
            "work_name": "Campaign bonus" if code == bonus else "Main",
            "regist_date": "2026-08-24",
            "maker_id": "RG01061132",
            "is_sale": code == main,
            "is_free": code == bonus,
            "is_oly": code == bonus,
            "price": 0,
            "bonuses": [{"title": "Campaign bonus", "end_date": "2026-09-06"}]
            if code == main else [],
        }
        return httpx.Response(200, json={code: product})

    provider = DlsiteProvider(client_factory=lambda: httpx.Client(
        transport=httpx.MockTransport(response)
    ))
    page = LookupPage(LookupService(provider, NamingService()), CoverService())
    page.show()
    qapp.processEvents()
    page.code_input.setText(main)
    assert page.bonus_panel.isHidden()
    page.start_lookup()
    thread = page._thread
    assert thread is not None
    loop = QEventLoop()
    thread.finished.connect(loop.quit)
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    qapp.processEvents()
    page.bonus_advanced.setChecked(True)
    page.bonus_candidate_input.setText(bonus.lower())
    page.bonus_verify_button.click()
    thread = page._thread
    assert thread is not None
    assert page.is_busy()
    assert page.bonus_search_button.text() == "取消搜索"
    loop = QEventLoop()
    thread.finished.connect(loop.quit)
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    qapp.processEvents()

    assert not page.is_busy()
    assert page.bonus_search_button.text() != "取消搜索"
    assert page.bonus_search_button.isEnabled()
    assert calls == [main, bonus]
    assert f"待确认候选 · 高置信标题匹配 · {bonus}" in rendered_text(page.bonus_output)
    assert any("2026-09-06" in label.text() for label in page.bonus_campaign.findChildren(QLabel))
    assert not page.identity_frame.isHidden()
    page.close()


def test_lookup_campaign_and_default_range_discovery(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(bonus_search, "BONUS_REQUEST_INTERVAL", 0.0)
    main, bonus = "RJ01741384", "RJ01741449"
    title = "期間限定4大特典" + "長い日本語の特典タイトル" * 18
    long_description = "長い特典の説明\n" * 200
    calls = []
    products = {
        main: {
            "work_name": "Main", "maker_id": "RG42470", "regist_date": "2026-11-06",
            "bonuses": [
                {"title": title, "end_date": "2099-11-19", "description": long_description},
                {"title": "過去の特典", "end_date": "2026-11-12", "description": "<b>短い説明</b>"},
                {"title": "期限未定", "description": "説明あり"},
                {"title": "説明なし"},
            ],
        },
        bonus: {
            "work_name": title, "maker_id": "RG42470", "regist_date": "2026-11-06",
            "price": 0, "is_sale": False, "is_free": True, "is_oly": True,
        },
    }

    def response(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/ajax"):
            calls.append([request.url.path])
            return httpx.Response(404)
        codes = request.url.params["product_id"].split(",")
        calls.append(codes)
        return httpx.Response(200, json={
            code: products[code] for code in codes if code in products
        })

    provider = DlsiteProvider(client_factory=lambda: httpx.Client(
        transport=httpx.MockTransport(response)
    ))
    database = Database(tmp_path / "metadata.sqlite3")
    database.initialize()
    now = datetime(2026, 11, 13, tzinfo=UTC)
    lookup = LookupService(
        provider, NamingService(), metadata_store=MetadataStore(database),
        clock=lambda: now,
        manual_review_service=ManualReviewService(ManualReviewRepository(database)),
    )
    page = LookupPage(lookup, CoverService())
    page.setStyleSheet(light_stylesheet())
    page.show()
    page.code_input.setText(main)

    def wait_for_page():
        thread = page._thread
        assert thread is not None
        loop = QEventLoop()
        thread.finished.connect(loop.quit)
        QTimer.singleShot(3000, loop.quit)
        loop.exec()
        qapp.processEvents()
        assert not page.is_busy()

    page.start_lookup()
    wait_for_page()
    assert calls == [[main], ["/maniax/api/=/product.json"]]
    campaign_text = "\n".join(label.text() for label in page.bonus_campaign.findChildren(QLabel))
    assert f"活动进行中 · {title} · 截止 2099-11-19" in campaign_text
    assert "已结束 · 過去の特典" in campaign_text
    assert "状态未知 · 期限未定" in campaign_text
    assert page._lookup_content_layout is not None
    assert (
        page._lookup_content_layout.indexOf(page.bonus_panel)
        > page._lookup_content_layout.indexOf(page.related_card)
    )
    assert "尚未发现独立编号" in campaign_text
    assert not page.bonus_campaign.findChildren(QToolButton)
    assert not page.bonus_campaign.findChildren(QPushButton)
    assert all(not label.toolTip() for label in page.bonus_campaign.findChildren(QLabel))
    links = page.bonus_campaign.findChildren(QLabel, "lookupCampaignStatus")
    assert len(links) == 3
    for link, key, description in zip(
        links,
        (Qt.Key.Key_Space, Qt.Key.Key_Return, None),
        (long_description.strip(), "<b>短い説明</b>", "説明あり"),
        strict=True,
    ):
        assert link.cursor().shape() == Qt.CursorShape.PointingHandCursor
        assert link.focusPolicy() == Qt.FocusPolicy.StrongFocus
        if key is not None:
            link.setFocus()
            assert link.hasFocus()
            QTest.keyClick(link, key)
        else:
            QTest.mouseClick(link, Qt.MouseButton.LeftButton, pos=QPoint(10, link.height() // 2))
        dialog = page.findChild(QDialog)
        assert dialog is not None and dialog.isVisible()
        details = dialog.findChild(QPlainTextEdit)
        assert details is not None and details.toPlainText() == description
        if key == Qt.Key.Key_Space:
            assert details.verticalScrollBar().maximum() > 0
            details.verticalScrollBar().setValue(details.verticalScrollBar().maximum())
            assert details.verticalScrollBar().value() > 0
        dialog.reject()
        qapp.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert not page.bonus_campaign.isHidden()
    assert page.bonus_end.text() == "+100"
    assert not page.bonus_panel.findChildren(QSpinBox)
    assert "494" not in page.bonus_coverage_label.text()
    assert "历史编号覆盖参考：约 30.8%" in page.bonus_coverage_label.text()
    page.bonus_range.setValue(page.bonus_range.value() + 3)
    assert page.bonus_end.text() == "+100"  # Near the recommended stop.
    QTest.keyClick(page.bonus_range, Qt.Key.Key_Right)
    assert page.bonus_end.text() == "+101"
    page.bonus_end.setText("+5001")
    page.bonus_end.editingFinished.emit()
    assert page._selected_bonus_offsets() == (1, 5001)
    assert "后方 5,001 个编号" in page.bonus_range_label.text()
    page.bonus_end.setText("+100")
    page.bonus_end.editingFinished.emit()

    calls.clear()
    page.start_lookup()
    wait_for_page()
    assert calls == []
    assert not page.bonus_campaign.isHidden()

    page.bonus_search_button.click()
    wait_for_page()
    assert len(calls) == 20
    assert any(bonus in batch for batch in calls)
    assert "目标范围尚未完成" in rendered_text(page.bonus_output)

    def review_link(code: str) -> QLabel:
        return next(label for label in page.bonus_review_widget.findChildren(QLabel)
                    if label.isVisible() and f'href="{code}"' in label.text())

    def review_button(text: str) -> QPushButton:
        return next(button for button in page.bonus_review_widget.findChildren(QPushButton)
                    if button.isVisible() and button.text() == text)

    assert "标题完全一致" in rendered_text(review_link(bonus))
    assert "自动关联 · 高置信" in rendered_text(review_link(bonus))
    assert page.bonus_search_button.text() == "继续搜索剩余范围"
    assert {button.text() for button in page.bonus_review_widget.findChildren(QPushButton)
            if button.isVisible()} == {"更改判断"}
    page.resize(480, 520)
    qapp.processEvents()
    scroll = page.findChild(QScrollArea, "lookupScrollArea")
    assert scroll is not None and scroll.verticalScrollBar().maximum() > 0
    assert scroll.horizontalScrollBar().maximum() == 0
    page.bonus_advanced.setChecked(True)
    assert page.bonus_start.isVisible() and page.bonus_range.isEnabled()
    page.bonus_start.setText("-50")
    page.bonus_end.setText("+100")
    page.bonus_end.editingFinished.emit()
    assert "可能需要多次手动继续" in page.bonus_budget_hint.text()
    assert "未计入负向贡献" in page.bonus_coverage_label.text()
    assert "重叠的已核验编号有 1 个" in page.bonus_range_progress.text()
    page.bonus_end.setText("-1")
    page.bonus_end.editingFinished.emit()
    assert "暂无可靠" in page.bonus_coverage_label.text()
    page.bonus_end.setText("+")
    page.bonus_end.editingFinished.emit()
    assert "有效" in page.bonus_range_label.text()
    page.bonus_end.setText("-51")
    page.bonus_end.editingFinished.emit()
    assert "起点须小于或等于终点" in page.bonus_range_label.text()
    page.bonus_end.setText("+40001")
    page.bonus_end.editingFinished.emit()
    assert "历史编号覆盖参考：约 91.3%" in page.bonus_coverage_label.text()
    assert page._selected_bonus_offsets() == (-50, 40001)
    assert page.bonus_range.value() == page.bonus_range.maximum()
    page.bonus_range.triggerAction(page.bonus_range.SliderAction.SliderPageStepSub)
    assert page.bonus_start.text() == "-50"
    assert 0 < page._selected_bonus_offsets()[1] < 20_000
    page.bonus_start.setText("+1")
    page.bonus_end.setText("+200000")
    page.bonus_end.editingFinished.emit()
    assert "历史编号覆盖参考：约 99.8%" in page.bonus_coverage_label.text()
    assert "已达到历史样本最大正向差值" in page.bonus_coverage_label.text()
    assert "仍可能发现样本外特典" in page.bonus_coverage_label.text()
    assert page._selected_bonus_offsets() == (1, 200000)
    page.bonus_start.setText("-18545")
    page.bonus_end.setText("+200000")
    page.bonus_end.editingFinished.emit()
    assert "历史样本覆盖：100%（已知样本）" in page.bonus_coverage_label.text()
    page.bonus_start.setText("+1")
    assert len(calls) == 20
    calls.clear()
    page.bonus_end.setText("+40000")
    page.bonus_end.editingFinished.emit()
    page.bonus_search_button.click()
    wait_for_page()
    assert len(calls) == 20
    assert calls[0] == list(bonus_search.bonus_range_worknos(main, (101, 105)))
    assert "历史编号覆盖参考：约 91.3%" in page.bonus_coverage_label.text()
    assert "已核验 1 / 40,000 个编号" in rendered_text(page.bonus_output)
    assert page.bonus_end.text() == "+40000"
    calls.clear()
    page.bonus_end.setText("+200000")
    page.bonus_end.editingFinished.emit()
    page.bonus_search_button.click()
    wait_for_page()
    assert page._selected_bonus_offsets() == (1, 200000)
    assert len(calls) == 20
    assert calls[0] == list(bonus_search.bonus_range_worknos(main, (201, 205)))
    page.bonus_end.setText("+100")
    page.bonus_end.editingFinished.emit()
    calls.clear()
    # Refresh expired metadata and keep using the saved association.
    now = datetime(2026, 11, 15, tzinfo=UTC)
    page.start_lookup()
    wait_for_page()
    assert calls == [[main], ["/maniax/api/=/product.json"]]
    calls.clear()
    assert page.bonus_search_button.text() == "查找更多特典"
    assert not any(button.text() == "查看待确认候选" for button in page.findChildren(QPushButton))
    scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum() // 2)
    saved_scroll = scroll.verticalScrollBar().value()
    review_link(bonus).linkActivated.emit(bonus)
    wait_for_page()
    assert "自动关联 · 高置信" in rendered_text(review_link(main))
    assert calls == [[bonus], ["/maniax/api/=/product.json"]]
    calls.clear()
    page.back_button.click()
    qapp.processEvents()
    assert page.code_input.text() == main
    assert "自动关联 · 高置信" in rendered_text(review_link(bonus))
    assert scroll.verticalScrollBar().value() == saved_scroll > 0
    page.forward_button.click()
    qapp.processEvents()
    assert page.code_input.text() == bonus
    assert calls == []
    review_link(main).linkActivated.emit(main)
    wait_for_page()
    assert "自动关联 · 高置信" in rendered_text(review_link(bonus))
    review_link(bonus).linkActivated.emit(bonus)
    wait_for_page()
    review_button("更改判断").click()
    qapp.processEvents()
    assert "待确认" in rendered_text(review_link(main))
    review_button("不是该作品的特典").click()
    qapp.processEvents()
    assert "已拒绝配对" in rendered_text(review_link(main))
    review_button("重新审阅").click()
    qapp.processEvents()
    assert "待确认" in rendered_text(review_link(main))
    review_button("确认匹配").click()
    qapp.processEvents()
    assert "用户人工确认" in rendered_text(review_link(main))
    review_link(main).linkActivated.emit(main)
    wait_for_page()
    assert calls == []
    assert "人工确认" in rendered_text(review_link(bonus))
    assert page.bonus_search_button.text() == "查找更多特典"
    review_button("重新审阅").click()
    qapp.processEvents()
    assert "待确认" in rendered_text(review_link(bonus))
    assert page.bonus_search_button.text() == "继续查找更多特典"
    review_link(bonus).linkActivated.emit(bonus)
    wait_for_page()
    assert "待确认" in rendered_text(review_link(main))
    assert calls == []
    review_link(main).linkActivated.emit(main)
    wait_for_page()
    assert calls == []
    page.bonus_search_button.click()
    wait_for_page()
    assert len(calls) == 20
    assert "目标范围尚未完成" in rendered_text(page.bonus_output)
    assert page.bonus_search_button.text() == "继续搜索剩余范围"

    page.bonus_search_button.click()
    assert page.bonus_search_button.text() == "取消搜索"
    assert not page.bonus_range.isEnabled()
    page.bonus_search_button.click()
    assert not page.bonus_search_button.isEnabled()
    wait_for_page()
    assert "搜索已取消" in rendered_text(page.bonus_output)
    assert page.bonus_search_button.isEnabled() and page.bonus_range.isEnabled()
    assert page.bonus_search_button.text() == "继续搜索剩余范围"

    for button in (
        page.bonus_search_button, page.bonus_verify_button,
        review_button("确认匹配"), review_button("不是该作品的特典"),
    ):
        normal = _render_button_state(button, QStyle.StateFlag.State_None)
        hover = _render_button_state(button, QStyle.StateFlag.State_MouseOver)
        assert hover != normal
        assert _render_button_state(button, QStyle.StateFlag.State_Sunken) != hover
        assert _render_button_state(button, QStyle.StateFlag.State_HasFocus) != normal
        assert _render_button_state(button, QStyle.StateFlag.State_None, enabled=False) != normal

    # A later direct observation must coexist with any local judgment.
    calls.clear()
    service = lookup.bonus_search()
    (pair,) = service.pairs_for_work(main)
    official = pair.match.model_copy(update={"relation": pair.match.relation.model_copy(update={
        "confidence": Confidence.CONFIRMED, "detection_source": "product_info_ajax",
    })})
    MetadataStore(database).save_limited_bonus_matches((official,))
    service.review_match(official, CandidateReviewOutcome.RELATED)
    page.start_lookup()
    wait_for_page()
    assert "人工确认 · DLsite 官方直接证据" in rendered_text(review_link(bonus))
    review_button("重新审阅").click()
    qapp.processEvents()
    assert "DLsite 官方直接证据 · 人工待确认" in rendered_text(review_link(bonus))
    review_button("不是该作品的特典").click()
    qapp.processEvents()
    assert "已拒绝配对 · DLsite 官方直接证据" in rendered_text(review_link(bonus))
    review_link(bonus).linkActivated.emit(bonus)
    wait_for_page()
    assert "已拒绝配对 · DLsite 官方直接证据" in rendered_text(review_link(main))
    assert calls == []
    page.close()
    database.dispose()


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

    assert "简体中文（CHI_HANS）" in rendered_text(page.relation_details_value)
    assert "原作 · RJ01609020" in rendered_text(page.relation_details_value)
    assert 'href="RJ01609020"' in page.relation_details_value.text()
    assert page.historical_relations_value.isHidden()
    assert "fixture" in page.historical_relations_value.text()
    assert "暂无历史" not in page.historical_relations_value.text()
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
            release_date=date(2026, 8, 24),
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
    assert page.release_value.text() == "2026-08-24"
    assert page.identity_frame.isAncestorOf(page.cvs_value)
    assert page.identity_frame.isAncestorOf(page.tags_value)
    assert not any(label.text() == "作品信息" for label in page.findChildren(QLabel))
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
    assert page.maker_id_value.isHidden()
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


def test_translation_links_navigate_original_parent_and_child(
    qapp: QApplication, tmp_path: Path,
) -> None:
    fixture = Path(__file__).parents[1] / "fixtures" / "dlsite" / "translation_RJ01619571.json"
    products = json.loads(fixture.read_text(encoding="utf-8"))
    calls: list[tuple[str, ...]] = []
    original, child = "RJ01619571", "RJ01628429"
    images = {original: _png_bytes(24, 16), child: _png_bytes(32, 24)}
    cover_requests = []
    for code in images:
        products[code]["work_image"] = f"https://example.test/{code}.png"

    class Covers(CoverService):
        def fetch_for_work(self, workno: str, url: str) -> bytes:
            cover_requests.append(workno)
            content = images[workno]
            self.remember_cached_cover(workno, content)
            return content

    def respond(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("/ajax"):
            return httpx.Response(404)
        codes = tuple(request.url.params["product_id"].split(","))
        calls.append(codes)
        return httpx.Response(200, json={
            code: products[code] for code in codes if code in products
        })

    provider = DlsiteProvider(client_factory=lambda: httpx.Client(
        transport=httpx.MockTransport(respond)
    ))
    database = Database(tmp_path / "metadata.sqlite3")
    database.initialize()
    store = MetadataStore(database)
    store.save_limited_bonus_matches((BonusMatch(
        relation=WorkRelation(
            source_workno=original, target_workno="RJ01619572",
            relation_type=RelationType.LIMITED_BONUS,
            confidence=Confidence.CONFIRMED, evidence=[], detection_source="fixture",
        ),
        title="Saved bonus", observed_at=datetime.now(UTC),
    ),))
    page = LookupPage(LookupService(provider, NamingService(), metadata_store=store), Covers())
    page.resize(620, 480)
    page.show()

    def wait_for_lookup() -> None:
        thread = page._thread
        assert thread is not None
        loop = QEventLoop()
        thread.finished.connect(loop.quit)
        QTimer.singleShot(3000, loop.quit)
        loop.exec()
        qapp.processEvents()
        assert not page.is_busy()

    page.code_input.setText("RJ01619571")
    page.start_lookup()
    wait_for_lookup()
    assert 'href="RJ01628429"' in page.relation_details_value.text()
    assert 'href="RJ01628430"' in page.relation_details_value.text()
    assert "むしぞる中尉" in rendered_text(page.relation_details_value)
    assert "暂无历史" not in page.historical_relations_value.text()
    assert calls == [("RJ01619571",), ("RJ01628427", "RJ01628429"), ("RJ01628430", "RJ01706442")]
    assert page._cover_content == images[original]

    page.relation_details_value.linkActivated.emit("RJ01628429")
    wait_for_lookup()
    assert page.workno_value.text() == "RJ01628429"
    assert 'href="RJ01619571"' in page.relation_details_value.text()
    assert page._cover_content == images[child]
    visited_calls = calls.copy()
    page.back_button.click()
    assert page.code_input.text() == "RJ01619571"
    assert "むしぞる中尉" in rendered_text(page.relation_details_value)
    assert page._cover_content == images[original]
    page.forward_button.click()
    assert page.code_input.text() == "RJ01628429"
    assert page._cover_content == images[child]
    assert calls == visited_calls
    page.start_lookup()
    wait_for_lookup()
    assert page._cover_content == images[child]
    assert cover_requests == [original, child]
    page.relation_details_value.linkActivated.emit("RJ01628430")
    wait_for_lookup()
    assert page.workno_value.text() == "RJ01628430"
    assert 'href="RJ01628429"' in page.relation_details_value.text()
    assert 'href="RJ01706442"' in page.relation_details_value.text()
    assert page.bonus_panel.isHidden()
    scroll = page.findChild(QScrollArea, "lookupScrollArea")
    assert scroll is not None and scroll.horizontalScrollBar().maximum() == 0
    page.close()
    database.dispose()


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

    assert "原作 · RJ01609020" in rendered_text(page.relation_details_value)
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
                cvs=["長い声優名" * 12, "もう一人の声優" * 8],
                tags=["長いタグ名" * 10, "音声作品" * 14],
            ),
            formatted_name="[RJ01609020] 标题",
        )
    )
    page.show()
    qapp.processEvents()

    captions = [
        caption for caption in page.identity_frame.findChildren(QLabel, "lookupFactCaption")
        if caption.isVisible()
    ]
    assert [caption.text() for caption in captions] == ["作品编号", "社团", "CV", "标签"]
    captions = captions[:2]
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
    for label in (page.cvs_value, page.tags_value):
        assert label.height() >= label.heightForWidth(label.width()) > label.fontMetrics().height()
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
