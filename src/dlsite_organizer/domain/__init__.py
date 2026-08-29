"""Domain models and rules independent of UI and infrastructure."""

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
from dlsite_organizer.domain.work import Work
from dlsite_organizer.domain.work_code import WorkCode, WorkCodeError, extract_work_codes

__all__ = [
    "RenamePlan",
    "RenamePlanStatus",
    "ScanCandidate",
    "ScanCandidateStatus",
    "ScanResult",
    "ScanSkipReason",
    "ScanSkipped",
    "TranslationRole",
    "Work",
    "WorkCode",
    "WorkCodeError",
    "WorkRelation",
    "extract_work_codes",
]
