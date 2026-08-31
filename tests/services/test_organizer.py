from dataclasses import dataclass, field
from pathlib import Path

from dlsite_organizer.domain.organizer import RenamePlanStatus
from dlsite_organizer.domain.work import AgeCategory, Work
from dlsite_organizer.services.lookup import LookupFailure, LookupFailureKind, LookupResult
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.organizer import (
    OrganizerService,
    WorkLookupStatus,
)
from dlsite_organizer.services.rename_planner import RenamePlanner


@dataclass
class FakeBatchLookupService:
    works: dict[str, Work]
    failures: dict[str, Exception] = field(default_factory=dict)
    requested: list[str] = field(default_factory=list)

    def lookup(self, raw_workno: str) -> LookupResult:
        self.requested.append(raw_workno)
        failure = self.failures.get(raw_workno)
        if failure is not None:
            if isinstance(failure, LookupFailure):
                raise failure
            raise failure
        work = self.works[raw_workno]
        return LookupResult(
            work=work,
            formatted_name=f"[{work.workno}] {work.title}",
        )


def test_organizer_maps_metadata_and_deduplicates_batch_requests(tmp_path: Path) -> None:
    (tmp_path / "A RJ01609020").mkdir()
    (tmp_path / "B RJ01609020 copy").mkdir()
    (tmp_path / "C RJ01636949").mkdir()
    provider = FakeBatchLookupService(
        works={
            "RJ01609020": Work(workno="RJ01609020", title="Original", maker_name="Circle A"),
            "RJ01636949": Work(workno="RJ01636949", title="Translation", maker_name="Circle B"),
        }
    )
    progress: list[tuple[int, int, str]] = []

    preview = OrganizerService(provider).preview(
        tmp_path,
        progress_callback=lambda completed, total, code: progress.append((completed, total, code)),
    )

    assert provider.requested == ["RJ01609020", "RJ01636949"]
    assert [lookup.status for lookup in preview.lookups] == [
        WorkLookupStatus.SUCCESS,
        WorkLookupStatus.SUCCESS,
    ]
    assert [plan.work_code for plan in preview.plans] == [
        "RJ01609020",
        "RJ01609020",
        "RJ01636949",
    ]
    assert progress == [
        (0, 2, "RJ01609020"),
        (1, 2, "RJ01636949"),
    ]


def test_organizer_preview_reuses_the_same_rich_work_and_naming_fields(tmp_path: Path) -> None:
    (tmp_path / "source RJ01609020").mkdir()
    rich_work = Work(
        workno="RJ01609020",
        title="中性测试作品",
        maker_id="RG12345",
        maker_name="测试社团",
        series_name="测试系列",
        cvs=["CV A"],
        tags=["ASMR"],
        language="CHI_HANS",
        age_category=AgeCategory.R15,
    )
    naming = NamingService(
        "{maker_name}|{series_name}|{cv_list}|{tags_list}|{age_category}|{language}"
    )
    provider = FakeBatchLookupService(works={rich_work.workno: rich_work})

    preview = OrganizerService(provider, planner=RenamePlanner(naming)).preview(tmp_path)

    assert preview.lookups[0].work == rich_work
    assert preview.plans[0].work == rich_work
    assert naming.format(rich_work) == "测试社团_测试系列_CV A_ASMR_r15_CHI_HANS"


def test_organizer_isolates_one_lookup_failure_and_keeps_result_order(tmp_path: Path) -> None:
    (tmp_path / "one RJ01609020").mkdir()
    (tmp_path / "two RJ01636949").mkdir()
    (tmp_path / "three RJ01637033").mkdir()
    provider = FakeBatchLookupService(
        works={
            "RJ01609020": Work(workno="RJ01609020", title="One"),
            "RJ01637033": Work(workno="RJ01637033", title="Three"),
        },
        failures={
            "RJ01636949": LookupFailure(
                LookupFailureKind.CONNECTION,
                "连接 DLsite 失败，请检查网络后稍后重试。",
            )
        },
    )

    preview = OrganizerService(provider).preview(tmp_path)

    assert provider.requested == ["RJ01609020", "RJ01637033", "RJ01636949"]
    assert [plan.status for plan in preview.plans] == [
        RenamePlanStatus.READY,
        RenamePlanStatus.READY,
        RenamePlanStatus.LOOKUP_FAILED,
    ]
    assert "连接 DLsite 失败" in (preview.plans[2].error or "")
    assert preview.plans[0].work is not None
    assert preview.plans[1].work is not None


def test_organizer_keeps_ambiguous_folder_visible_without_lookup(tmp_path: Path) -> None:
    (tmp_path / "ambiguous RJ01609020 + RJ01636949").mkdir()
    provider = FakeBatchLookupService(works={})

    preview = OrganizerService(provider).preview(tmp_path)

    assert provider.requested == []
    assert len(preview.plans) == 1
    assert preview.plans[0].status is RenamePlanStatus.AMBIGUOUS_CODE
    assert preview.plans[0].work_codes == ("RJ01609020", "RJ01636949")


def test_organizer_cancellation_stops_new_requests_and_keeps_completed_rows(tmp_path: Path) -> None:
    (tmp_path / "one RJ01609020").mkdir()
    (tmp_path / "two RJ01636949").mkdir()
    (tmp_path / "three RJ01637033").mkdir()
    provider = FakeBatchLookupService(
        works={
            "RJ01609020": Work(workno="RJ01609020", title="One"),
            "RJ01636949": Work(workno="RJ01636949", title="Two"),
            "RJ01637033": Work(workno="RJ01637033", title="Three"),
        }
    )
    cancelled = False

    def on_progress(completed: int, _total: int, _code: str) -> None:
        nonlocal cancelled
        if completed == 1:
            cancelled = True

    preview = OrganizerService(provider).preview(
        tmp_path,
        progress_callback=on_progress,
        cancel_check=lambda: cancelled,
    )

    assert provider.requested == ["RJ01609020"]
    assert preview.cancelled is True
    assert preview.plans[0].status is RenamePlanStatus.READY
    assert preview.plans[1].status is RenamePlanStatus.CANCELLED
    assert preview.plans[2].status is RenamePlanStatus.CANCELLED


def test_organizer_preview_does_not_modify_directory_tree(tmp_path: Path) -> None:
    (tmp_path / "RJ01609020").mkdir()
    (tmp_path / "misc").mkdir()
    before = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    provider = FakeBatchLookupService(
        works={"RJ01609020": Work(workno="RJ01609020", title="Title")}
    )

    OrganizerService(provider).preview(tmp_path)

    after = sorted(path.relative_to(tmp_path).as_posix() for path in tmp_path.rglob("*"))
    assert after == before


def test_organizer_selective_preview_does_not_scan_unselected_siblings(tmp_path: Path) -> None:
    selected = tmp_path / "selected RJ01609020"
    sibling = tmp_path / "sibling RJ01636949"
    selected.mkdir()
    sibling.mkdir()
    provider = FakeBatchLookupService(
        works={
            "RJ01609020": Work(workno="RJ01609020", title="Selected"),
            "RJ01636949": Work(workno="RJ01636949", title="Sibling"),
        }
    )

    preview = OrganizerService(provider).preview_paths(tmp_path, (selected,))

    assert provider.requested == ["RJ01609020"]
    assert [plan.source_path for plan in preview.plans] == [selected]
