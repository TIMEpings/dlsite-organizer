from datetime import UTC, datetime, timedelta
from pathlib import Path

from dlsite_organizer.providers.dlsite.exceptions import DlsiteConnectionError
from dlsite_organizer.services.organizer import OrganizerService
from tests.services.test_lookup_cache import (
    MutableClock,
    SequencedProvider,
    make_lookup,
    make_work,
    open_service,
)


def test_organizer_uses_persistent_cache_across_previews(tmp_path: Path) -> None:
    for workno in ("RJ01609020", "RJ01636949", "RJ01637033"):
        (tmp_path / f"folder {workno}").mkdir()
    start = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
    clock = MutableClock(start)
    provider = SequencedProvider(
        [
            make_lookup(make_work(workno=workno, title=f"Title {workno}"))
            for workno in ("RJ01609020", "RJ01636949", "RJ01637033")
        ]
        + [
            make_lookup(make_work(workno=workno, title=f"Refreshed {workno}"))
            for workno in ("RJ01609020", "RJ01636949", "RJ01637033")
        ]
    )
    database, _store, lookup_service = open_service(tmp_path / "metadata.sqlite3", provider, clock)
    organizer = OrganizerService(lookup_service)

    first = organizer.preview(tmp_path)
    second = organizer.preview(tmp_path)
    clock.value = start + timedelta(hours=1)
    third = organizer.preview(tmp_path)

    assert len(first.plans) == len(second.plans) == len(third.plans) == 3
    assert provider.calls == [
        "RJ01609020",
        "RJ01636949",
        "RJ01637033",
        "RJ01609020",
        "RJ01636949",
        "RJ01637033",
    ]
    database.dispose()


def test_organizer_keeps_batch_dedupe_with_persistent_cache(tmp_path: Path) -> None:
    (tmp_path / "first RJ01609020").mkdir()
    (tmp_path / "duplicate RJ01609020").mkdir()
    (tmp_path / "second RJ01636949").mkdir()
    clock = MutableClock(datetime(2026, 8, 30, 10, 0, tzinfo=UTC))
    provider = SequencedProvider(
        [make_lookup(make_work(workno="RJ01609020")), make_lookup(make_work(workno="RJ01636949"))]
    )
    database, _store, lookup_service = open_service(tmp_path / "metadata.sqlite3", provider, clock)

    preview = OrganizerService(lookup_service).preview(tmp_path)
    second_preview = OrganizerService(lookup_service).preview(tmp_path)

    assert provider.calls == ["RJ01609020", "RJ01636949"]
    assert [plan.work_code for plan in preview.plans] == [
        "RJ01609020",
        "RJ01609020",
        "RJ01636949",
    ]
    assert len(second_preview.plans) == 3
    database.dispose()


def test_organizer_marks_stale_fallback_as_warning_but_fresh_cache_is_clean(tmp_path: Path) -> None:
    (tmp_path / "folder RJ01609020").mkdir()
    start = datetime(2026, 8, 30, 10, 0, tzinfo=UTC)
    clock = MutableClock(start)
    provider = SequencedProvider(
        [make_lookup(make_work(title="cached")), DlsiteConnectionError("offline")]
    )
    database, _store, lookup_service = open_service(tmp_path / "metadata.sqlite3", provider, clock)
    organizer = OrganizerService(lookup_service)

    fresh_preview = organizer.preview(tmp_path)
    assert not fresh_preview.plans[0].warnings

    clock.value = start + timedelta(hours=1)
    stale_preview = organizer.preview(tmp_path)

    assert any("使用旧缓存 metadata" in warning for warning in stale_preview.plans[0].warnings)
    assert "使用旧缓存 metadata" in (stale_preview.plans[0].warnings[0])
    assert provider.calls == ["RJ01609020", "RJ01609020"]
    database.dispose()
