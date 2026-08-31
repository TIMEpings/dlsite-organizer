"""UI-independent models for the folder organizer preview flow."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from dlsite_organizer.domain.work import Work


class ScanCandidateStatus(StrEnum):
    """Classification of a directory that contains a usable work code."""

    VALID = "valid"
    AMBIGUOUS_WORK_CODE = "ambiguous_work_code"


class ScanSkipReason(StrEnum):
    """Why a direct child was not included as a scan candidate."""

    NO_WORK_CODE = "no_work_code"
    NOT_DIRECTORY = "not_directory"
    SYMLINK = "symlink"
    HIDDEN = "hidden"
    READ_ERROR = "read_error"


class RenamePlanStatus(StrEnum):
    """Safety state of a proposed directory-name change."""

    READY = "ready"
    UNCHANGED = "unchanged"
    CONFLICT = "conflict"
    LOOKUP_FAILED = "lookup_failed"
    INVALID_CODE = "invalid_code"
    AMBIGUOUS_CODE = "ambiguous_code"
    INVALID_TARGET = "invalid_target"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class ScanCandidate:
    """One direct child directory selected for organizer processing."""

    source_path: Path
    work_code: str | None
    status: ScanCandidateStatus
    work_codes: tuple[str, ...] = ()
    error: str | None = None

    @property
    def current_name(self) -> str:
        """Return the source directory's display name."""
        return self.source_path.name


@dataclass(frozen=True, slots=True)
class ScanSkipped:
    """A direct child intentionally omitted from the candidate list."""

    source_path: Path
    reason: ScanSkipReason
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Deterministic output of a single root-directory scan."""

    root_path: Path
    candidates: tuple[ScanCandidate, ...]
    skipped: tuple[ScanSkipped, ...]
    cancelled: bool = False

    @property
    def skipped_count(self) -> int:
        return len(self.skipped)


@dataclass(frozen=True, slots=True)
class RenamePlan:
    """A reviewable source-to-target proposal; it never performs the change."""

    source_path: Path
    current_name: str
    work_code: str | None
    work: Work | None
    proposed_name: str | None
    target_path: Path | None
    status: RenamePlanStatus
    warnings: tuple[str, ...] = ()
    error: str | None = None
    work_codes: tuple[str, ...] = ()
