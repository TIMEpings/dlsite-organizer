"""Domain models and rules independent of UI and infrastructure."""

from dlsite_organizer.domain.candidate import (
    CandidateEvidence,
    CandidateEvidenceKind,
    CandidateEvidencePolarity,
    CandidateRelation,
    CandidateSearchResult,
    CandidateSearchState,
    CandidateSnapshotSource,
    CandidateType,
    KnownWorkSnapshot,
)
from dlsite_organizer.domain.organizer import (
    RenamePlan,
    RenamePlanStatus,
    ScanCandidate,
    ScanCandidateStatus,
    ScanResult,
    ScanSkipped,
    ScanSkipReason,
)
from dlsite_organizer.domain.relation import TranslationRole, WorkRelation
from dlsite_organizer.domain.rename_execution import (
    ExecutionResult,
    ExecutionStatus,
    RenameExecutionResult,
    RenameOperation,
    RenameTransaction,
    TransactionStatus,
    UndoResult,
    UndoStatus,
)
from dlsite_organizer.domain.work import Work
from dlsite_organizer.domain.work_code import WorkCode, WorkCodeError, extract_work_codes

__all__ = [
    "CandidateEvidence",
    "CandidateEvidenceKind",
    "CandidateEvidencePolarity",
    "CandidateRelation",
    "CandidateSearchResult",
    "CandidateSearchState",
    "CandidateSnapshotSource",
    "CandidateType",
    "ExecutionResult",
    "ExecutionStatus",
    "KnownWorkSnapshot",
    "RenameExecutionResult",
    "RenameOperation",
    "RenamePlan",
    "RenamePlanStatus",
    "RenameTransaction",
    "ScanCandidate",
    "ScanCandidateStatus",
    "ScanResult",
    "ScanSkipReason",
    "ScanSkipped",
    "TransactionStatus",
    "TranslationRole",
    "UndoResult",
    "UndoStatus",
    "Work",
    "WorkCode",
    "WorkCodeError",
    "WorkRelation",
    "extract_work_codes",
]
