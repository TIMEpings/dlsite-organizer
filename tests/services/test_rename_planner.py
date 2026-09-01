from pathlib import Path

from dlsite_organizer.domain.organizer import RenamePlanStatus
from dlsite_organizer.domain.work import Work
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.rename_planner import RenamePlanner


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
