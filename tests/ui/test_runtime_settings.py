"""Settings races use provider Events, with Qt pumping only for completion delivery."""

from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication
from tests.services.test_runtime_settings import RecordingProvider, initial_settings, runtime

from dlsite_organizer.app.settings import ProviderSettings
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.services.cover import CoverService
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.ui.main_window import MainWindow
from dlsite_organizer.ui.pages.organizer_page import OrganizerPage
from dlsite_organizer.ui.state.organizer_state import ScanMode, merge_previews
from dlsite_organizer.ui.workers.organizer_worker import OrganizerWorkerResult


@pytest.fixture
def qapp() -> QApplication:
    application = QApplication.instance()
    return application if isinstance(application, QApplication) else QApplication([])


def wait_idle(page: OrganizerPage) -> None:
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(1)
    poll.timeout.connect(lambda: loop.quit() if not page.is_busy() else None)
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    poll.start()
    timeout.start(5000)
    if page.is_busy():
        loop.exec()
    poll.stop()
    timeout.stop()
    assert not page.is_busy(), "scan did not complete"


@pytest.mark.parametrize("mode", ["replace-empty", "replace-existing", "append"])
@pytest.mark.parametrize("cancel", [False, True], ids=["finish", "cancel"])
@pytest.mark.parametrize("changed", ["template", "locale"])
def test_settings_save_invalidates_inflight_result_and_next_scan_is_current(
    qapp: QApplication, tmp_path: Path, mode: str, cancel: bool, changed: str
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    first, second = (root / f"old {code}" for code in ("RJ000001", "RJ000002"))
    first.mkdir()
    second.mkdir()
    settings = initial_settings(tmp_path)
    provider = RecordingProvider()
    lookup, persistence = runtime(settings, provider, tmp_path)
    organizer = OrganizerService(lookup)
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    window = MainWindow(
        lookup,
        CoverService(),
        organizer_service=organizer,
        settings_service=persistence,
        rename_executor=RenameExecutor(TransactionJournal(database)),
    )
    page = window.organizer_page
    page.set_root_path(root)
    if mode != "replace-empty":
        page.set_preview(organizer.preview_paths(root, (first,)))
        assert page.execute_button.isEnabled()
    previous = page.preview
    provider.calls.clear()
    provider.pause.set()
    delivered: list[OrganizerWorkerResult] = []
    try:
        if mode == "append":
            page._handle_drop_paths((second,))
        else:
            page.start_scan()
        assert provider.entered.wait(5)
        assert page._worker is not None
        page._worker.result_ready.connect(delivered.append)
        generation = lookup.settings_generation
        for index in (1, 2):
            if changed == "template":
                saved = settings.model_copy(update={"naming_template": f"NEW{index}-{{workno}}"})
            else:
                saved = settings.model_copy(
                    update={
                        "provider": ProviderSettings(
                            metadata_locale="en_us" if index == 1 else "zh_cn"
                        )
                    }
                )
            persistence.save(saved)
            assert page.preview_stale
            assert not page.execute_button.isEnabled()
        assert lookup.settings_generation == generation + 2
        if cancel:
            page.cancel_scan()
        provider.release.set()
        wait_idle(page)
        assert delivered and delivered[0].preview.settings_generation == generation
        assert all(
            plan.proposed_name.startswith("OLD-")
            for plan in delivered[0].preview.plans
            if plan.proposed_name is not None
        )
        assert {call[1] for call in provider.calls} == {"ja_jp"}
        assert page.preview is previous
        assert page.preview_stale
        assert not page.execute_button.isEnabled()
        assert not page.selected_ready_plans()
        page.start_scan()
        wait_idle(page)
        assert page.preview is not None and page.preview.settings_generation == generation + 2
        assert not page.preview_stale and page.execute_button.isEnabled()
        expected = (
            ("NEW2-RJ000001", "NEW2-RJ000002")
            if changed == "template"
            else ("OLD-RJ000001-zh_cn", "OLD-RJ000002-zh_cn")
        )
        assert tuple(plan.proposed_name for plan in page.preview.plans) == expected
    finally:
        provider.release.set()
        wait_idle(page)
        window.close()
        database.dispose()


@pytest.mark.parametrize("change", ["request", "replacement", "invalidation"])
def test_late_delivery_cannot_replace_current_preview(
    qapp: QApplication, tmp_path: Path, change: str
) -> None:
    (tmp_path / "old RJ000001").mkdir()
    settings = initial_settings(tmp_path)
    lookup, persistence = runtime(settings, RecordingProvider(), tmp_path)
    organizer = OrganizerService(lookup)
    page = OrganizerPage(organizer)
    preview = organizer.preview(tmp_path)
    page.set_preview(preview)
    late = OrganizerWorkerResult(page.state.request_id, page.state.preview_revision, preview)
    try:
        if change == "request":
            page.state.request_id += 1
        elif change == "replacement":
            persistence.save(settings.model_copy(update={"naming_template": "NEW-{workno}"}))
            page.set_preview(organizer.preview(tmp_path))
            page.state.scan_mode = ScanMode.APPEND
            assert page.preview is not None and page.preview.settings_generation == 1
        else:
            page.invalidate_preview()
        current = page.preview
        revision = page.state.preview_revision
        stale = page.preview_stale
        page._show_preview(late)
        assert page.preview is current
        assert page.state.preview_revision == revision
        assert page.preview_stale == stale
    finally:
        page.close()


def test_queued_success_after_cancellation_is_non_executable(
    qapp: QApplication, tmp_path: Path
) -> None:
    (tmp_path / "old RJ000001").mkdir()
    settings = initial_settings(tmp_path)
    lookup, _ = runtime(settings, RecordingProvider(), tmp_path)
    organizer = OrganizerService(lookup)
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    page = OrganizerPage(organizer, execution_service=RenameExecutor(TransactionJournal(database)))
    result = OrganizerWorkerResult(0, 0, organizer.preview(tmp_path))
    assert not result.preview.cancelled
    try:
        # Completion was already emitted before Cancel was processed by Qt.
        page.state.cancel_requested = True
        page._show_preview(result)
        page._update_execute_button()
        assert page.preview is not None and page.preview.cancelled
        assert page.preview_stale
        assert not page.execute_button.isEnabled()
        assert not page.selected_ready_plans()
    finally:
        page.close()
        database.dispose()


def test_append_rejects_existing_rows_from_other_settings_generation(
    qapp: QApplication, tmp_path: Path
) -> None:
    first, second = (tmp_path / f"old {code}" for code in ("RJ000001", "RJ000002"))
    first.mkdir()
    second.mkdir()
    settings = initial_settings(tmp_path)
    lookup, persistence = runtime(settings, RecordingProvider(), tmp_path)
    organizer = OrganizerService(lookup)
    page = OrganizerPage(organizer)
    previous = organizer.preview_paths(tmp_path, (first,))
    page.set_preview(previous)
    persistence.save(settings.model_copy(update={"naming_template": "NEW-{workno}"}))
    incoming = organizer.preview_paths(tmp_path, (second,))
    page.state.scan_mode = ScanMode.APPEND
    try:
        page._show_preview(OrganizerWorkerResult(0, page.state.preview_revision, incoming))
        assert page.preview is previous
        assert page.preview_stale
        assert not page.selected_ready_plans()
        with pytest.raises(ValueError, match="different settings generations"):
            merge_previews(previous, incoming)
    finally:
        page.close()
