from pathlib import Path

import pytest

from dlsite_organizer.domain.organizer import RenamePlanStatus
from dlsite_organizer.domain.work import Work
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.rename_planner import (
    RenamePlanner,
    RenamePlanningRequest,
)


def make_work(workno: str = "RJ01609020", title: str = "Work Title") -> Work:
    return Work(workno=workno, title=title, maker_name="Circle Name")


def test_planner_creates_ready_plan_without_mutating_source(tmp_path: Path) -> None:
    source = tmp_path / "old title RJ01609020"
    source.mkdir()
    before = sorted(path.name for path in tmp_path.iterdir())

    plan = RenamePlanner().plan(tmp_path, source, "rj01609020", make_work())

    assert plan.status is RenamePlanStatus.READY
    assert plan.current_name == "old title RJ01609020"
    assert plan.proposed_name == "[RJ01609020][Circle Name]Work Title"
    assert plan.target_path == tmp_path / plan.proposed_name
    assert sorted(path.name for path in tmp_path.iterdir()) == before


def test_planner_marks_already_formatted_directory_unchanged(tmp_path: Path) -> None:
    work = make_work()
    source = tmp_path / "[RJ01609020][Circle Name]Work Title"
    source.mkdir()

    plan = RenamePlanner().plan(tmp_path, source, work.workno, work)

    assert plan.status is RenamePlanStatus.UNCHANGED


def test_planner_marks_existing_target_as_conflict(tmp_path: Path) -> None:
    source = tmp_path / "old RJ01609020"
    target = tmp_path / "[RJ01609020][Circle Name]Work Title"
    source.mkdir()
    target.mkdir()

    plan = RenamePlanner().plan(tmp_path, source, "RJ01609020", make_work())

    assert plan.status is RenamePlanStatus.CONFLICT
    assert plan.target_path == target
    assert plan.error is not None


def test_planner_marks_existing_file_target_as_conflict(tmp_path: Path) -> None:
    source = tmp_path / "old RJ01609020"
    target = tmp_path / "[RJ01609020][Circle Name]Work Title"
    source.mkdir()
    target.write_text("occupied", encoding="utf-8")

    plan = RenamePlanner().plan(tmp_path, source, "RJ01609020", make_work())

    assert plan.status is RenamePlanStatus.CONFLICT
    assert plan.target_path == target
    assert plan.error == "目标目录已存在，未生成可执行的重命名计划。"


@pytest.mark.parametrize("existing_name", ["Title", "title", "TITLE"])
def test_planner_matches_windows_case_insensitive_sibling_aliases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    existing_name: str,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    existing_alias = tmp_path / existing_name
    original_iterdir = Path.iterdir

    def fake_iterdir(path: Path):
        if path == tmp_path:
            return iter((source, existing_alias))
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", fake_iterdir)
    plan = RenamePlanner().plan(
        tmp_path,
        source,
        "RJ01609020",
        make_work(),
        formatted_name="tItLe",
    )

    assert plan.status is RenamePlanStatus.CONFLICT
    assert plan.error == "目标目录已存在，未生成可执行的重命名计划。"


def test_planner_treats_link_like_sibling_as_occupied_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    link_like = tmp_path / "target"
    link_referent = tmp_path / "other-target"
    original_iterdir = Path.iterdir
    original_resolve = Path.resolve

    def fake_iterdir(path: Path):
        if path == tmp_path:
            return iter((source, link_like))
        return original_iterdir(path)

    def fake_resolve(path: Path, strict: bool = False) -> Path:
        if path == link_like:
            return link_referent
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "iterdir", fake_iterdir)
    monkeypatch.setattr(Path, "resolve", fake_resolve)
    plan = RenamePlanner().plan(
        tmp_path,
        source,
        "RJ01609020",
        make_work(),
        formatted_name="target",
    )

    assert plan.status is RenamePlanStatus.CONFLICT
    assert plan.error == "目标目录已存在，未生成可执行的重命名计划。"


def test_planner_batch_preserves_directory_symlink_occupancy(tmp_path: Path) -> None:
    source = tmp_path / "source"
    referent = tmp_path / "other target"
    link_like = tmp_path / "target"
    source.mkdir()
    referent.mkdir()
    try:
        link_like.symlink_to(referent, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlinks are unavailable in this environment: {exc}")
    request = RenamePlanningRequest(source, "RJ01609020", make_work(), "target")
    planner = RenamePlanner()

    independent = planner.plan(
        tmp_path,
        request.source_path,
        request.work_code,
        request.work,
        formatted_name=request.formatted_name,
    )

    assert planner.plan_batch(tmp_path, (request,)) == (independent,)
    assert independent.status is RenamePlanStatus.CONFLICT


def test_planner_does_not_schedule_rename_into_another_batch_source(tmp_path: Path) -> None:
    occupied_source = tmp_path / "A"
    moving_source = tmp_path / "B"
    occupied_source.mkdir()
    moving_source.mkdir()
    planner = RenamePlanner()
    work_a = make_work("RJ01609020", "A title")
    work_b = make_work("RJ01636949", "B title")

    first = planner.plan(
        tmp_path, occupied_source, work_a.workno, work_a, formatted_name="renamed A"
    )
    second = planner.plan(
        tmp_path, moving_source, work_b.workno, work_b, formatted_name="A"
    )
    plans = planner.finalize((first, second))

    assert [plan.status for plan in plans] == [
        RenamePlanStatus.READY,
        RenamePlanStatus.CONFLICT,
    ]
    assert plans[1].error == "目标目录已存在，未生成可执行的重命名计划。"


def test_planner_reports_sibling_enumeration_failure_as_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()

    def fail_iterdir(_path: Path):
        raise PermissionError("fixture access denied")

    monkeypatch.setattr(Path, "iterdir", fail_iterdir)
    plan = RenamePlanner().plan(
        tmp_path,
        source,
        "RJ01609020",
        make_work(),
        formatted_name="target",
    )

    assert plan.status is RenamePlanStatus.CONFLICT
    assert plan.warnings == ("无法确认目标目录是否已存在，因此不会标记为 READY。",)
    assert plan.error == "无法检查目标目录是否已存在。"


def test_planner_marks_duplicate_planned_targets_as_conflict(tmp_path: Path) -> None:
    first = tmp_path / "first RJ01609020"
    second = tmp_path / "second RJ01609020"
    first.mkdir()
    second.mkdir()
    planner = RenamePlanner()
    work = make_work()

    plans = planner.finalize(
        [
            planner.plan(tmp_path, first, work.workno, work),
            planner.plan(tmp_path, second, work.workno, work),
        ]
    )

    assert [plan.status for plan in plans] == [
        RenamePlanStatus.CONFLICT,
        RenamePlanStatus.CONFLICT,
    ]
    assert all(plan.warnings for plan in plans)


def test_planner_uses_naming_service_sanitization(tmp_path: Path) -> None:
    work = make_work(title='Rain: "Night"?')
    source = tmp_path / "RJ01609020"
    source.mkdir()

    plan = RenamePlanner(NamingService()).plan(tmp_path, source, work.workno, work)

    assert plan.status is RenamePlanStatus.READY
    assert plan.proposed_name == "[RJ01609020][Circle Name]Rain_ _Night__"
    assert ":" not in (plan.proposed_name or "")
    assert "?" not in (plan.proposed_name or "")


def test_planner_rejects_source_outside_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside RJ01609020"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()

    plan = RenamePlanner().plan(root, outside, "RJ01609020", make_work())

    assert plan.status is RenamePlanStatus.INVALID_TARGET
    assert "根目录内" in (plan.error or "")


def test_planner_rejects_illegal_target_escape(tmp_path: Path) -> None:
    source = tmp_path / "RJ01609020"
    source.mkdir()

    plan = RenamePlanner().plan(
        tmp_path,
        source,
        "RJ01609020",
        make_work(),
        formatted_name="..\\outside",
    )

    assert plan.status is RenamePlanStatus.INVALID_TARGET
    assert plan.target_path is None


def test_planner_uses_windows_case_insensitive_identity_policy(tmp_path: Path) -> None:
    source = tmp_path / "Foo RJ01609020"
    source.mkdir()
    work = make_work(title="foo rj01609020")

    plan = RenamePlanner().plan(
        tmp_path,
        source,
        work.workno,
        work,
        formatted_name="foo rj01609020",
    )

    assert plan.status is RenamePlanStatus.UNCHANGED

    case_only_source = tmp_path / "Foo"
    case_only_source.mkdir()
    case_only_plan = RenamePlanner().plan(
        tmp_path,
        case_only_source,
        work.workno,
        work,
        formatted_name="foo",
    )
    assert case_only_plan.status is RenamePlanStatus.UNCHANGED


def test_planner_supports_unicode_metadata(tmp_path: Path) -> None:
    work = make_work(title="雨音の夜・中文标题")
    source = tmp_path / "RJ01609020"
    source.mkdir()

    plan = RenamePlanner().plan(tmp_path, source, work.workno, work)

    assert plan.proposed_name == "[RJ01609020][Circle Name]雨音の夜・中文标题"


def test_planner_warns_on_conservative_long_path_threshold(tmp_path: Path) -> None:
    source = tmp_path / "RJ01609020"
    source.mkdir()
    planner = RenamePlanner(path_warning_threshold=10)

    plan = planner.plan(tmp_path, source, "RJ01609020", make_work())

    assert plan.status is RenamePlanStatus.READY
    assert any("超过保守阈值" in warning for warning in plan.warnings)


def test_planner_marks_invalid_work_code(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()

    plan = RenamePlanner().plan(tmp_path, source, "not-a-code", make_work())

    assert plan.status is RenamePlanStatus.INVALID_CODE


def test_planner_batch_matches_independent_plans_for_static_filesystem(
    tmp_path: Path,
) -> None:
    ready_source = tmp_path / "ready source"
    unchanged_source = tmp_path / "Old Name"
    directory_conflict_source = tmp_path / "directory conflict source"
    file_conflict_source = tmp_path / "file conflict source"
    invalid_source = tmp_path / "invalid source"
    unicode_source = tmp_path / "unicode source"
    for source in (
        ready_source,
        unchanged_source,
        directory_conflict_source,
        file_conflict_source,
        invalid_source,
        unicode_source,
    ):
        source.mkdir()
    (tmp_path / "occupied directory").mkdir()
    (tmp_path / "occupied file").write_text("occupied", encoding="utf-8")
    requests = (
        RenamePlanningRequest(
            ready_source,
            "RJ01609020",
            make_work("RJ01609020"),
            "new ready name",
        ),
        RenamePlanningRequest(
            unchanged_source,
            "RJ01609021",
            make_work("RJ01609021"),
            "old name",
        ),
        RenamePlanningRequest(
            directory_conflict_source,
            "RJ01609022",
            make_work("RJ01609022"),
            "occupied directory",
        ),
        RenamePlanningRequest(
            file_conflict_source,
            "RJ01609023",
            make_work("RJ01609023"),
            "occupied file",
        ),
        RenamePlanningRequest(
            invalid_source,
            "RJ01609024",
            make_work("RJ01609024"),
            "..\\escape",
        ),
        RenamePlanningRequest(
            unicode_source,
            "RJ01609025",
            make_work("RJ01609025"),
            "混合 Mixed 音声タイトル",
        ),
    )
    planner = RenamePlanner(path_warning_threshold=10)

    independent = tuple(
        planner.plan(
            tmp_path,
            request.source_path,
            request.work_code,
            request.work,
            formatted_name=request.formatted_name,
        )
        for request in requests
    )
    batched = planner.plan_batch(tmp_path, requests)

    assert batched == independent
    assert [plan.status for plan in batched] == [
        RenamePlanStatus.READY,
        RenamePlanStatus.UNCHANGED,
        RenamePlanStatus.CONFLICT,
        RenamePlanStatus.CONFLICT,
        RenamePlanStatus.INVALID_TARGET,
        RenamePlanStatus.READY,
    ]
    assert all(plan.warnings for index, plan in enumerate(batched) if index != 4)


def test_planner_batch_preserves_case_alias_and_link_like_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    alias = tmp_path / "TITLE"
    link_like = tmp_path / "target"
    link_referent = tmp_path / "other-target"
    original_iterdir = Path.iterdir
    original_resolve = Path.resolve

    def fake_iterdir(path: Path):
        if path == tmp_path:
            return iter((source, alias, link_like))
        return original_iterdir(path)

    def fake_resolve(path: Path, strict: bool = False) -> Path:
        if path == link_like:
            return link_referent
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "iterdir", fake_iterdir)
    monkeypatch.setattr(Path, "resolve", fake_resolve)
    requests = (
        RenamePlanningRequest(source, "RJ01609020", make_work(), "title"),
        RenamePlanningRequest(source, "RJ01609020", make_work(), "target"),
    )
    planner = RenamePlanner()
    independent = tuple(
        planner.plan(
            tmp_path,
            request.source_path,
            request.work_code,
            request.work,
            formatted_name=request.formatted_name,
        )
        for request in requests
    )

    assert planner.plan_batch(tmp_path, requests) == independent
    assert all(plan.status is RenamePlanStatus.CONFLICT for plan in independent)


def test_planner_batch_enumerates_root_once_and_keeps_snapshot_call_local(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = tuple(tmp_path / f"source-{index}" for index in range(8))
    for source in sources:
        source.mkdir()
    requests = tuple(
        RenamePlanningRequest(
            source,
            f"RJ{index + 1609020:08d}",
            make_work(f"RJ{index + 1609020:08d}"),
            f"target-{index}",
        )
        for index, source in enumerate(sources)
    )
    original_iterdir = Path.iterdir
    root_enumerations = 0

    def count_root_iterdir(path: Path):
        nonlocal root_enumerations
        if path == tmp_path:
            root_enumerations += 1
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", count_root_iterdir)
    planner = RenamePlanner()

    invalid_only = planner.plan_batch(
        tmp_path,
        (RenamePlanningRequest(sources[0], "not-a-code", requests[0].work, "invalid"),),
    )
    assert invalid_only[0].status is RenamePlanStatus.INVALID_CODE
    assert root_enumerations == 0

    first_batch = planner.plan_batch(tmp_path, requests)
    assert len(first_batch) == len(requests)
    assert root_enumerations == 1

    for request in requests:
        planner.plan(
            tmp_path,
            request.source_path,
            request.work_code,
            request.work,
            formatted_name=request.formatted_name,
        )
    assert root_enumerations == 1 + len(requests)

    planner.plan_batch(tmp_path, requests[:1])
    assert root_enumerations == 2 + len(requests)

    planner.plan(tmp_path, requests[0].source_path, requests[0].work_code, requests[0].work)
    assert root_enumerations == 3 + len(requests)


def test_planner_batch_repeats_existing_failure_result_without_reenumerating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sources = tuple(tmp_path / f"source-{index}" for index in range(3))
    for source in sources:
        source.mkdir()
    requests = tuple(
        RenamePlanningRequest(
            source,
            f"RJ{index + 1609020:08d}",
            make_work(f"RJ{index + 1609020:08d}"),
            f"target-{index}",
        )
        for index, source in enumerate(sources)
    )
    root_enumerations = 0

    def fail_root_iterdir(path: Path):
        nonlocal root_enumerations
        if path == tmp_path:
            root_enumerations += 1
            raise PermissionError("fixture access denied")
        raise AssertionError(f"unexpected sibling enumeration for {path}")

    monkeypatch.setattr(Path, "iterdir", fail_root_iterdir)
    plans = RenamePlanner().plan_batch(tmp_path, requests)

    assert root_enumerations == 1
    assert [plan.status for plan in plans] == [RenamePlanStatus.CONFLICT] * 3
    assert all(
        plan.warnings == ("无法确认目标目录是否已存在，因此不会标记为 READY。",)
        for plan in plans
    )
    assert all(plan.error == "无法检查目标目录是否已存在。" for plan in plans)
