"""Deterministic service-level settings publication and operation snapshots."""

from concurrent.futures import ThreadPoolExecutor
from copy import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from unittest.mock import patch

import httpx
import pytest

from dlsite_organizer.app.bootstrap import build_components
from dlsite_organizer.app.settings import (
    AppSettings,
    CacheSettings,
    ProviderSettings,
    SettingsService,
    StartupMode,
    load_settings,
)
from dlsite_organizer.domain.work import Work
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.metadata_store import MetadataStore
from dlsite_organizer.persistence.rename_journal import TransactionJournal
from dlsite_organizer.providers.dlsite.client import DlsiteProvider
from dlsite_organizer.providers.dlsite.exceptions import DlsiteConnectionError
from dlsite_organizer.services.lookup import LookupFailure, LookupFreshness, LookupService
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.services.quick_rename import QuickRenameService, QuickRenameStatus
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.ui.workers.lookup_worker import LookupWorker
from dlsite_organizer.ui.workers.quick_rename_worker import QuickRenameWorker


class RecordingProvider:
    """Copy request configuration, share only test synchronization and recorded calls."""

    def __init__(self) -> None:
        self.locale = "ja_jp"
        self.timeout = 15.0
        self.pause = Event()
        self.entered = Event()
        self.release = Event()
        self.calls: list[tuple[str, str, float]] = []
        self.failure: Exception | None = None

    def snapshot(self):
        return copy(self)

    def apply_settings(self, *, timeout_seconds: float, metadata_locale: str) -> None:
        self.timeout = timeout_seconds
        self.locale = metadata_locale

    def fetch_work(self, workno: str) -> Work:
        self.calls.append((workno, self.locale, self.timeout))
        if self.pause.is_set() and not self.entered.is_set():
            self.entered.set()
            assert self.release.wait(5), "test did not release provider"
        if self.failure is not None:
            raise self.failure
        return Work(workno=workno, title=self.locale)


def initial_settings(tmp_path: Path, *, enabled: bool = False) -> AppSettings:
    return AppSettings(
        naming_template="OLD-{workno}-{title}",
        provider=ProviderSettings(metadata_locale="ja_jp"),
        cache=CacheSettings(enabled=enabled),
        database_path=tmp_path / "metadata.sqlite3",
    )


def runtime(settings: AppSettings, provider: RecordingProvider, tmp_path: Path):
    lookup = LookupService(provider, NamingService(settings.naming_template), settings=settings)
    persistence = SettingsService(settings, config_path=tmp_path / "config.toml")
    persistence.subscribe(lookup.apply_settings)
    return lookup, persistence


@pytest.mark.parametrize("selected", [False, True], ids=["root", "selected-paths"])
@pytest.mark.parametrize("changed", ["template", "locale-timeout"])
def test_scan_pins_configuration_across_multiple_persisted_saves(
    tmp_path: Path, selected: bool, changed: str
) -> None:
    paths = tuple(tmp_path / f"old {code}" for code in ("RJ000001", "RJ000002"))
    for path in paths:
        path.mkdir()
    settings = initial_settings(tmp_path)
    provider = RecordingProvider()
    provider.pause.set()
    lookup, persistence = runtime(settings, provider, tmp_path)
    organizer = OrganizerService(lookup)
    original_generation = lookup.settings_generation

    def preview():
        return organizer.preview_paths(tmp_path, paths) if selected else organizer.preview(tmp_path)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(preview)
        try:
            assert provider.entered.wait(5)
            if changed == "template":
                persistence.save(settings.model_copy(update={"naming_template": "MID-{workno}"}))
                saved = persistence.save(
                    settings.model_copy(update={"naming_template": "NEW-{workno}"})
                )
            else:
                persistence.save(
                    settings.model_copy(
                        update={
                            "provider": ProviderSettings(
                                metadata_locale="en_us", timeout_seconds=20
                            )
                        }
                    )
                )
                saved = persistence.save(
                    settings.model_copy(
                        update={
                            "provider": ProviderSettings(
                                metadata_locale="zh_cn", timeout_seconds=30
                            )
                        }
                    )
                )
            assert lookup.settings_generation == original_generation + 2
        finally:
            provider.release.set()
        result = future.result(5)
    assert result.settings_generation == original_generation
    assert [plan.proposed_name for plan in result.plans] == [
        "OLD-RJ000001-ja_jp",
        "OLD-RJ000002-ja_jp",
    ]
    assert {call[1:] for call in provider.calls} == {("ja_jp", 15.0)}
    provider.calls.clear()
    current = preview()
    assert current.settings_generation == original_generation + 2
    expected = (
        ["NEW-RJ000001", "NEW-RJ000002"]
        if changed == "template"
        else ["OLD-RJ000001-zh_cn", "OLD-RJ000002-zh_cn"]
    )
    assert [plan.proposed_name for plan in current.plans] == expected
    assert load_settings(persistence.config_path) == saved


def test_failed_atomic_save_does_not_publish_runtime(tmp_path: Path) -> None:
    settings = initial_settings(tmp_path)
    lookup, persistence = runtime(settings, RecordingProvider(), tmp_path)
    persistence.save(settings)
    snapshot = lookup.snapshot()
    with (
        patch("dlsite_organizer.app.settings.os.replace", side_effect=OSError("save failed")),
        pytest.raises(OSError, match="save failed"),
    ):
        persistence.save(settings.model_copy(update={"naming_template": "NEW-{workno}"}))
    assert persistence.settings == settings
    assert load_settings(persistence.config_path) == settings
    assert lookup.settings_generation == snapshot.settings_generation
    assert lookup.lookup("RJ000001").formatted_name == "OLD-RJ000001-ja_jp"


def test_launch_only_save_keeps_generation_and_preview_configuration(tmp_path: Path) -> None:
    settings = initial_settings(tmp_path)
    lookup, persistence = runtime(settings, RecordingProvider(), tmp_path)
    persistence.save(
        settings.model_copy(
            update={
                "startup_mode": StartupMode.FULL,
                "database_path": tmp_path / "other.sqlite3",
                "provider": settings.provider.model_copy(
                    update={"section": "home", "base_url": "https://example.test"}
                ),
            }
        )
    )
    assert lookup.settings_generation == 0
    assert lookup.lookup("RJ000001").formatted_name == "OLD-RJ000001-ja_jp"


@pytest.mark.parametrize("age_hours", [0, 48], ids=["fresh", "stale"])
def test_disabling_bypasses_all_metadata_reads_and_writes(tmp_path: Path, age_hours: int) -> None:
    database = Database(tmp_path / "cache.sqlite3")
    database.initialize()
    store = MetadataStore(database)
    now = datetime.now(UTC)
    cached = Work(workno="RJ000001", title="stored")
    store.save(cached, None, source="fixture", fetched_at=now - timedelta(hours=age_hours))
    provider = RecordingProvider()
    settings = initial_settings(tmp_path, enabled=True)
    lookup = LookupService(provider, NamingService(), metadata_store=store, settings=settings)
    try:
        lookup.apply_settings(settings.model_copy(update={"cache": CacheSettings(enabled=False)}))
        with (
            patch.object(store, "get", wraps=store.get) as reads,
            patch.object(store, "save", wraps=store.save) as writes,
            patch.object(
                store, "append_observation", wraps=store.append_observation
            ) as observations,
            patch.object(store, "list_observations", wraps=store.list_observations) as history,
        ):
            result = lookup.lookup("RJ000001")
            new = lookup.lookup("RJ000002")
            assert result.freshness is LookupFreshness.LIVE
            assert new.freshness is LookupFreshness.LIVE
            assert result.work.title == "ja_jp"
            assert not reads.called and not writes.called and not observations.called
            assert not history.called
            assert result.candidate_relations is None
        assert store.get("RJ000001").work == cached  # type: ignore[union-attr]
        assert store.get("RJ000002") is None
    finally:
        database.dispose()


def test_disabled_cache_does_not_fall_back_after_network_failure(tmp_path: Path) -> None:
    database = Database(tmp_path / "cache.sqlite3")
    database.initialize()
    store = MetadataStore(database)
    store.save(
        Work(workno="RJ000001", title="stale"),
        None,
        source="fixture",
        fetched_at=datetime.now(UTC) - timedelta(days=2),
    )
    provider = RecordingProvider()
    provider.failure = DlsiteConnectionError("offline")
    settings = initial_settings(tmp_path, enabled=True)
    lookup = LookupService(provider, NamingService(), metadata_store=store, settings=settings)
    try:
        assert lookup.lookup("RJ000001").freshness is LookupFreshness.CACHE_STALE_FALLBACK
        lookup.apply_settings(settings.model_copy(update={"cache": CacheSettings(enabled=False)}))
        with patch.object(store, "get", wraps=store.get) as reads:
            with pytest.raises(LookupFailure):
                lookup.lookup("RJ000001")
            assert not reads.called
    finally:
        database.dispose()


@pytest.mark.parametrize("enabled", [False, True], ids=["enable", "disable"])
def test_inflight_cache_toggle_pins_both_read_and_write_policy(
    tmp_path: Path, enabled: bool
) -> None:
    database = Database(tmp_path / "cache.sqlite3")
    database.initialize()
    store = MetadataStore(database)
    store.save(
        Work(workno="RJ000002", title="cached"),
        None,
        source="fixture",
        fetched_at=datetime.now(UTC),
    )
    settings = initial_settings(tmp_path, enabled=enabled)
    provider = RecordingProvider()
    provider.pause.set()
    lookup = LookupService(provider, NamingService(), metadata_store=store, settings=settings)
    old = lookup.snapshot()
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(old.lookup, "RJ000001")
            try:
                assert provider.entered.wait(5)
                lookup.apply_settings(
                    settings.model_copy(update={"cache": CacheSettings(enabled=not enabled)})
                )
            finally:
                provider.release.set()
            assert future.result(5).freshness is LookupFreshness.LIVE
        assert (store.get("RJ000001") is not None) == enabled
        expected_old = LookupFreshness.CACHE_FRESH if enabled else LookupFreshness.LIVE
        assert old.lookup("RJ000002").freshness is expected_old
        expected_new = LookupFreshness.LIVE if enabled else LookupFreshness.CACHE_FRESH
        assert lookup.lookup("RJ000002").freshness is expected_new
        lookup.lookup("RJ000003")
        assert (store.get("RJ000003") is not None) == (not enabled)
    finally:
        provider.release.set()
        database.dispose()


@pytest.mark.parametrize("enabled", [False, True], ids=["started-disabled", "started-enabled"])
def test_bootstrap_cache_toggle_is_live_and_persists_on_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    settings = initial_settings(tmp_path, enabled=enabled)
    components = build_components(settings)
    try:
        store = MetadataStore(components.database)
        store.save(
            Work(workno="RJ000001", title="persisted"),
            None,
            source="fixture",
            fetched_at=datetime.now(UTC),
        )
        saved = components.settings_service.save(
            settings.model_copy(update={"cache": CacheSettings(enabled=not enabled)})
        )
        # Disabled behavior is proved above; this guard forbids live DLsite requests here.
        with patch.object(DlsiteProvider, "fetch_work_lookup", side_effect=DlsiteConnectionError()):
            if not enabled:
                assert components.lookup_service.lookup("RJ000001").freshness is (
                    LookupFreshness.CACHE_FRESH
                )
            else:
                with pytest.raises(LookupFailure):
                    components.lookup_service.lookup("RJ000001")
        persisted = load_settings(components.settings_service.config_path)
        assert persisted == saved
    finally:
        components.database.dispose()
    restarted = build_components(persisted)
    try:
        with patch.object(DlsiteProvider, "fetch_work_lookup", side_effect=DlsiteConnectionError()):
            if persisted.cache.enabled:
                assert restarted.lookup_service.lookup("RJ000001").freshness is (
                    LookupFreshness.CACHE_FRESH
                )
            else:
                with pytest.raises(LookupFailure):
                    restarted.lookup_service.lookup("RJ000001")
    finally:
        restarted.database.dispose()


def test_quick_rename_finishes_captured_batch_and_next_batch_uses_new_settings(
    tmp_path: Path,
) -> None:
    settings = initial_settings(tmp_path)
    provider = RecordingProvider()
    lookup, persistence = runtime(settings, provider, tmp_path)
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    service = QuickRenameService(
        OrganizerService(lookup), RenameExecutor(TransactionJournal(database))
    )
    paths = tuple(tmp_path / f"old {code}" for code in ("RJ000001", "RJ000002"))
    for path in paths:
        path.mkdir()
    provider.pause.set()
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(service.rename, paths)
            try:
                assert provider.entered.wait(5)
                persistence.save(settings.model_copy(update={"naming_template": "NEW-{workno}"}))
            finally:
                provider.release.set()
            result = future.result(5)
        assert result.status is QuickRenameStatus.SUCCESS
        assert result.preview is not None and result.preview.settings_generation == 0
        assert all(
            plan.proposed_name.startswith("OLD-")
            for plan in result.plans
            if plan.proposed_name is not None
        )
        assert all(
            plan.target_path.is_dir() for plan in result.plans if plan.target_path is not None
        )
        next_path = tmp_path / "old RJ000003"
        next_path.mkdir()
        current = service.rename((next_path,))
        assert current.status is QuickRenameStatus.SUCCESS
        assert (tmp_path / "NEW-RJ000003").is_dir()
    finally:
        provider.release.set()
        database.dispose()


def test_workers_capture_before_dispatch(tmp_path: Path) -> None:
    from dlsite_organizer.services.cover import CoverService

    settings = initial_settings(tmp_path)
    lookup, persistence = runtime(settings, RecordingProvider(), tmp_path)
    lookup_worker = LookupWorker(lookup, CoverService(), "RJ000001")
    names: list[str] = []
    lookup_worker.result_ready.connect(lambda result: names.append(result.formatted_name))
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    source = tmp_path / "old RJ000002"
    source.mkdir()
    quick = QuickRenameService(
        OrganizerService(lookup), RenameExecutor(TransactionJournal(database))
    )
    quick_worker = QuickRenameWorker(quick, (source,))
    try:
        persistence.save(settings.model_copy(update={"naming_template": "NEW-{workno}"}))
        lookup_worker.run()
        quick_worker.run()
        assert names == ["OLD-RJ000001-ja_jp"]
        assert (tmp_path / "OLD-RJ000002-ja_jp").is_dir()
    finally:
        database.dispose()


def test_snapshot_dispatch_does_not_reenter_public_service_wrappers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The existing subprocess harness instruments public methods on service instances."""
    settings = initial_settings(tmp_path)
    lookup, _ = runtime(settings, RecordingProvider(), tmp_path)
    organizer = OrganizerService(lookup)
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    source = tmp_path / "old RJ000001"
    source.mkdir()
    quick = QuickRenameService(organizer, RenameExecutor(TransactionJournal(database)))
    original_rename = quick.rename
    original_preview = organizer.preview
    original_paths = organizer.preview_paths
    calls: list[str] = []

    def tracked_rename(*args, **kwargs):
        calls.append("rename")
        assert calls.count("rename") == 1
        return original_rename(*args, **kwargs)

    def tracked_preview(*args, **kwargs):
        calls.append("preview")
        assert calls.count("preview") == 1
        return original_preview(*args, **kwargs)

    def tracked_paths(*args, **kwargs):
        calls.append("paths")
        assert calls.count("paths") == 1
        return original_paths(*args, **kwargs)

    monkeypatch.setattr(quick, "rename", tracked_rename)
    monkeypatch.setattr(organizer, "preview", tracked_preview)
    monkeypatch.setattr(organizer, "preview_paths", tracked_paths)
    try:
        assert organizer.preview(tmp_path).plans
        assert organizer.preview_paths(tmp_path, (source,)).plans
        calls.clear()
        result = quick.rename((source,))
        assert result.status is QuickRenameStatus.SUCCESS
        assert calls == ["rename", "paths"]
    finally:
        database.dispose()


def test_provider_snapshot_keeps_locale_between_core_and_metadata_requests(tmp_path: Path) -> None:
    fixtures = Path(__file__).parents[1] / "fixtures" / "dlsite"
    settings = initial_settings(tmp_path)
    locales: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/product/info/ajax"):
            lookup.apply_settings(
                settings.model_copy(
                    update={
                        "provider": ProviderSettings(metadata_locale="en_us", timeout_seconds=30)
                    }
                )
            )
            name = "product_info_RJ01609020.json"
        else:
            locales.append(request.url.params["locale"])
            name = "product_metadata_RJ01609020.json"
        return httpx.Response(200, text=(fixtures / name).read_text(encoding="utf-8"))

    provider = DlsiteProvider(
        metadata_locale="ja_jp",
        client_factory=lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    lookup = LookupService(provider, NamingService(), settings=settings)
    lookup.lookup("RJ01609020")
    assert locales == ["ja_jp"]
    lookup.lookup("RJ01609020")
    assert locales == ["ja_jp", "en_us"]


def test_default_client_factory_binds_to_snapshot_timeout(tmp_path: Path) -> None:
    settings = initial_settings(tmp_path)
    lookup = LookupService(DlsiteProvider(), NamingService(), settings=settings)
    old = lookup.snapshot()
    lookup.apply_settings(
        settings.model_copy(
            update={"provider": ProviderSettings(metadata_locale="en_us", timeout_seconds=30)}
        )
    )
    old_provider = old._configuration.provider
    current_provider = lookup._configuration.provider
    assert isinstance(old_provider, DlsiteProvider)
    assert isinstance(current_provider, DlsiteProvider)
    with old_provider._new_client() as old_client, current_provider._new_client() as current_client:
        assert old_client.timeout.read == 15
        assert current_client.timeout.read == 30
