from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from threading import Barrier

from dlsite_organizer.domain.rename_execution import TransactionStatus
from dlsite_organizer.domain.work import Work
from dlsite_organizer.persistence.database import Database
from dlsite_organizer.persistence.metadata_store import MetadataStore
from dlsite_organizer.persistence.rename_journal import TransactionJournal, UnavailableRenameJournal
from dlsite_organizer.services.lookup import (
    LookupFailure,
    LookupFailureKind,
    LookupResult,
    LookupService,
)
from dlsite_organizer.services.naming import NamingService
from dlsite_organizer.services.organizer import OrganizerService
from dlsite_organizer.services.quick_rename import QuickRenameService, QuickRenameStatus
from dlsite_organizer.services.rename_executor import RenameExecutor
from dlsite_organizer.services.undo_service import UndoService


@dataclass
class FakeLookup:
    works: dict[str, Work]
    failures: dict[str, Exception] = field(default_factory=dict)
    requested: list[str] = field(default_factory=list)

    def lookup(self, raw_workno: str) -> LookupResult:
        self.requested.append(raw_workno)
        if raw_workno in self.failures:
            raise self.failures[raw_workno]
        work = self.works[raw_workno]
        return LookupResult(work=work, formatted_name=f"[{work.workno}] {work.title}")


class BarrierLookup:
    def __init__(self, work: Work, barrier: Barrier) -> None:
        self._work = work
        self._barrier = barrier

    def lookup(self, raw_workno: str) -> LookupResult:
        self._barrier.wait(timeout=5)
        return LookupResult(work=self._work, formatted_name=f"[{raw_workno}] {self._work.title}")


class _ConcurrentFilesystem:
    def rename(self, source: Path, target: Path) -> None:
        if target.exists():
            raise FileExistsError(str(target))
        source.rename(target)


def _quick(tmp_path: Path, lookup: FakeLookup) -> tuple[QuickRenameService, TransactionJournal]:
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    service = QuickRenameService(OrganizerService(lookup), RenameExecutor(journal))
    return service, journal


def test_quick_rename_happy_path_and_undo(tmp_path: Path) -> None:
    source = tmp_path / "RJ01609020 old"
    source.mkdir()
    lookup = FakeLookup({"RJ01609020": Work(workno="RJ01609020", title="Target")})
    service, journal = _quick(tmp_path, lookup)
    undo = UndoService(journal)

    result = service.rename((source,))

    assert result.status is QuickRenameStatus.SUCCESS
    assert result.execution is not None
    assert result.execution.status is TransactionStatus.COMPLETED
    target = tmp_path / "[RJ01609020] Target"
    assert not source.exists()
    assert target.is_dir()
    assert (
        journal.get_transaction(result.transaction_id or "").status
        is TransactionStatus.COMPLETED
    )

    undone = undo.undo(result.transaction_id, confirmed=True)
    assert undone.status is TransactionStatus.UNDONE
    assert source.is_dir()
    assert not target.exists()


def test_quick_rename_batch_uses_one_transaction_and_one_undo(tmp_path: Path) -> None:
    paths = tuple(
        tmp_path / f"RJ0160902{index} old"
        for index in range(0, 3)
    )
    for path in paths:
        path.mkdir()
    works = {
        "RJ01609020": Work(workno="RJ01609020", title="Zero"),
        "RJ01609021": Work(workno="RJ01609021", title="One"),
        "RJ01609022": Work(workno="RJ01609022", title="Two"),
    }
    lookup = FakeLookup(works)
    service, journal = _quick(tmp_path, lookup)
    undo = UndoService(journal)

    result = service.rename(paths)

    assert result.status is QuickRenameStatus.SUCCESS
    assert result.execution is not None
    assert result.execution.success_count == 3
    transaction = journal.get_transaction(result.transaction_id or "")
    assert transaction is not None
    assert len(transaction.operations) == 3
    assert len({operation.transaction_id for operation in transaction.operations}) == 1
    assert all(not path.exists() for path in paths)

    undone = undo.undo(result.transaction_id, confirmed=True)

    assert undone.status is TransactionStatus.UNDONE
    assert undone.success_count == 3
    assert all(path.is_dir() for path in paths)
    assert all(
        not (tmp_path / f"[{code}] {works[code].title}").exists()
        for code in works
    )


def test_quick_invalid_input_has_no_lookup_or_journal(tmp_path: Path) -> None:
    source = tmp_path / "random folder"
    source.mkdir()
    lookup = FakeLookup({})
    service, journal = _quick(tmp_path, lookup)

    result = service.rename((source,))

    assert result.status is QuickRenameStatus.INVALID_INPUT
    assert lookup.requested == []
    assert journal.latest_undoable() is None
    assert source.exists()


def test_quick_lookup_failure_rejects_entire_batch(tmp_path: Path) -> None:
    first = tmp_path / "RJ01609020 first"
    second = tmp_path / "RJ01636949 second"
    first.mkdir()
    second.mkdir()
    lookup = FakeLookup(
        {"RJ01609020": Work(workno="RJ01609020", title="One")},
        failures={
            "RJ01636949": LookupFailure(LookupFailureKind.NOT_FOUND, "not found"),
        },
    )
    service, journal = _quick(tmp_path, lookup)

    result = service.rename((first, second))

    assert result.status is QuickRenameStatus.LOOKUP_FAILED
    assert first.exists() and second.exists()
    assert journal.latest_undoable() is None


def test_quick_plan_conflict_rejects_entire_batch(tmp_path: Path) -> None:
    first = tmp_path / "RJ01609020 first"
    second = tmp_path / "RJ01636949 second"
    first.mkdir()
    second.mkdir()
    (tmp_path / "[RJ01636949] Two").mkdir()
    lookup = FakeLookup(
        {
            "RJ01609020": Work(workno="RJ01609020", title="One"),
            "RJ01636949": Work(workno="RJ01636949", title="Two"),
        }
    )
    service, journal = _quick(tmp_path, lookup)

    result = service.rename((first, second))

    assert result.status is QuickRenameStatus.PLAN_REJECTED
    assert first.exists() and second.exists()
    assert journal.latest_undoable() is None


def test_quick_journal_unavailable_has_no_mutation(tmp_path: Path) -> None:
    source = tmp_path / "RJ01609020 old"
    source.mkdir()
    lookup = FakeLookup({"RJ01609020": Work(workno="RJ01609020", title="Target")})
    organizer = OrganizerService(lookup)
    service = QuickRenameService(organizer, RenameExecutor(UnavailableRenameJournal()))

    result = service.rename((source,))

    assert result.status is QuickRenameStatus.EXECUTION_FAILED
    assert source.exists()
    assert lookup.requested == []


def test_concurrent_same_folder_quick_invocations_fail_closed(tmp_path: Path) -> None:
    source = tmp_path / "RJ01609020 old"
    source.mkdir()
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    journal = TransactionJournal(database)
    barrier = Barrier(2)
    lookup = BarrierLookup(Work(workno="RJ01609020", title="Target"), barrier)
    services = [
        QuickRenameService(
            OrganizerService(lookup),
            RenameExecutor(journal, filesystem=_ConcurrentFilesystem()),
        )
        for _ in range(2)
    ]

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda service: service.rename((source,)), services))

        assert sum(result.status is QuickRenameStatus.SUCCESS for result in results) == 1
        assert sum(result.status is QuickRenameStatus.EXECUTION_FAILED for result in results) == 1
        assert (tmp_path / "[RJ01609020] Target").is_dir()
        assert not source.exists()
        assert journal.find_unresolved_transaction() is None
    finally:
        database.dispose()


def test_quick_rename_uses_the_shared_naming_service_settings(tmp_path: Path) -> None:
    source = tmp_path / "RJ01609020 old"
    source.mkdir()

    @dataclass
    class Provider:
        calls: int = 0

        def fetch_work(self, workno: str) -> Work:
            self.calls += 1
            return Work(workno=workno, title="Target", maker_name="Circle")

    provider = Provider()
    database = Database(tmp_path / "journal.sqlite3")
    database.initialize()
    lookup = LookupService(
        provider,
        NamingService("[{maker_name}][{rjcode}] {title}"),
        metadata_store=MetadataStore(database),
    )
    journal = TransactionJournal(database)
    service = QuickRenameService(
        OrganizerService(lookup),
        RenameExecutor(journal, filesystem=_ConcurrentFilesystem()),
    )

    try:
        lookup.lookup("RJ01609020")
        result = service.rename((source,))

        assert result.status is QuickRenameStatus.SUCCESS
        assert (tmp_path / "[Circle][RJ01609020] Target").is_dir()
        assert provider.calls == 1
    finally:
        database.dispose()
