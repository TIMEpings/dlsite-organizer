"""Per-profile Quick Rename inbox and crash-conservative admission journal.

The native helper keeps unlaunched or failed-launch payloads in prepared/ or
recoverable/. Only pending/ is eligible for ordinary admission. Inbox files
then move through claimed -> terminal. A journal claim is flushed before
controller submission. An orphan claim is indeterminate and is never replayed
automatically. Terminal markers are retained for seven days.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from dlsite_organizer.app.quick_action_controller import (
    QuickActionController,
    QuickActionFinished,
    QuickActionRequest,
)
from dlsite_organizer.app.single_instance import (
    MAX_REQUEST_FRAME_SIZE,
    PROTOCOL_VERSION,
    LocalCommand,
    LocalCommandName,
    LocalReply,
    LocalReplyStatus,
    ProtocolError,
    _validate_request_id,
    decode_request,
    encode_request,
)

logger = logging.getLogger(__name__)
TERMINAL_RETENTION_SECONDS = 7 * 24 * 60 * 60
MAX_RECORD_SIZE = MAX_REQUEST_FRAME_SIZE - 4
MAX_JOURNAL_SIZE = 4096


def _atomic_write(path: Path, content: bytes) -> None:
    _safe_directory(path.parent)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _canonical(command: LocalCommand) -> bytes:
    if command.command is not LocalCommandName.QUICK_RENAME:
        raise ProtocolError("durable handoff requires QUICK_RENAME")
    return encode_request(command)[4:]


def _safe_directory(path: Path) -> None:
    """Reject visible redirects; path checks cannot prevent concurrent replacement."""
    for component in (path, *path.parents):
        if component.is_symlink() or (
            hasattr(component, "is_junction") and component.is_junction()
        ):
            raise OSError(f"reparse point in handoff directory: {component}")
    path.mkdir(parents=True, exist_ok=True)


def _read_command(path: Path) -> LocalCommand:
    if path.is_symlink() or path.stat().st_size > MAX_RECORD_SIZE:
        raise ProtocolError("unsafe or oversized handoff record")
    raw = path.read_bytes()
    if len(raw) > MAX_RECORD_SIZE:
        raise ProtocolError("oversized handoff record")
    command = decode_request(len(raw).to_bytes(4, "big") + raw)
    if command.command is not LocalCommandName.QUICK_RENAME:
        raise ProtocolError("invalid handoff operation")
    if path.stem != command.request_id:
        raise ProtocolError("handoff filename and request ID differ")
    return command


def _same_payload(path: Path, canonical: bytes) -> bool:
    """Compare request meaning after protocol validation, not JSON formatting."""
    try:
        return _canonical(_read_command(path)) == canonical
    except (OSError, ProtocolError):
        return False


def _valid_timestamp(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        return False


class DurableHandoff(QObject):
    """Primary-owned admission and periodic inbox drain."""

    def __init__(
        self,
        profile_root: Path,
        submit: Callable[[LocalCommand], LocalReply],
        *,
        parent: QObject | None = None,
        clock: Callable[[], float] = time.time,
        controller: QuickActionController | None = None,
    ) -> None:
        super().__init__(parent)
        self.root = profile_root / "handoff" / "v1"
        self.prepared = self.root / "prepared"
        self.pending = self.root / "pending"
        self.claimed = self.root / "claimed"
        self.journal = self.root / "journal"
        self.quarantine = self.root / "quarantine"
        self.recoverable = self.root / "recoverable"
        self._submit = submit
        self._clock = clock
        self._seen: dict[str, dict[str, object]] = {}
        self._blocked_ids: set[str] = set()
        self._controller_to_external: dict[int, str] = {}
        self._admitting_external_id: str | None = None
        self._next_prune = clock() + 3600
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.drain)
        if controller is not None:
            controller.request_started.connect(self._controller_started)
            controller.request_finished.connect(self._controller_finished)
        for directory in (
            self.prepared,
            self.pending,
            self.claimed,
            self.journal,
            self.quarantine,
            self.recoverable,
        ):
            _safe_directory(directory)
        self._recover()
        for path in self.prepared.glob("*.json"):
            logger.error("Quick Rename launch handoff remains non-executable: %s", path.stem)
        for path in self.recoverable.glob("*.json"):
            logger.error("Quick Rename launch failed; recoverable request: %s", path.stem)

    def start(self) -> None:
        self.drain()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _journal_path(self, request_id: str) -> Path:
        return self.journal / f"{_validate_request_id(request_id)}.json"

    def _write_journal(self, request_id: str, record: dict[str, object]) -> None:
        content = json.dumps(record, separators=(",", ":")).encode()
        if len(content) > MAX_JOURNAL_SIZE:
            raise ValueError("oversized admission marker")
        _atomic_write(self._journal_path(request_id), content)
        self._seen[request_id] = record

    def _recover(self) -> None:
        for path in sorted(self.journal.glob("*.json")):
            try:
                if path.is_symlink():
                    raise ValueError("reparse admission marker")
                if path.stat().st_size > MAX_JOURNAL_SIZE:
                    raise ValueError("oversized admission marker")
                _validate_request_id(path.stem)
                data = json.loads(path.read_text(encoding="utf-8"))
                if (
                    not isinstance(data, dict)
                    or data.get("id") != path.stem
                    or data.get("state")
                    not in {"claimed", "terminal", "indeterminate", "retryable"}
                    or data.get("operation") != "QUICK_RENAME"
                    or not isinstance(data.get("digest"), str)
                    or re.fullmatch(r"[0-9a-f]{64}", data["digest"]) is None
                    or type(data.get("path_count")) is not int
                    or not 1 <= data["path_count"] <= 32
                    or not _valid_timestamp(data.get("time"))
                    or (
                        data["state"] == "terminal"
                        and data.get("status") not in {status.value for status in LocalReplyStatus}
                    )
                ):
                    raise ValueError("invalid admission marker")
                if data["state"] == "claimed":
                    data["state"] = "indeterminate"
                    self._write_journal(path.stem, data)
                    logger.error(
                        "indeterminate Quick Rename id=%s digest=%s paths=%s",
                        path.stem,
                        data.get("digest"),
                        data.get("path_count"),
                    )
                elif (
                    data["state"] == "terminal"
                    and data.get("status") == LocalReplyStatus.ACCEPTED.value
                    and data.get("tracked") is True
                    and data.get("execution") != "completed"
                ):
                    data["state"] = "indeterminate"
                    self._write_journal(path.stem, data)
                    logger.error(
                        "unfinished Quick Rename after crash id=%s digest=%s paths=%s execution=%s",
                        path.stem,
                        data.get("digest"),
                        data.get("path_count"),
                        data.get("execution"),
                    )
                elif (
                    data["state"] == "terminal"
                    and self._clock() - float(data["time"]) > TERMINAL_RETENTION_SECONDS
                ):
                    if (
                        self._can_release_payload(data)
                        and not (self.pending / path.name).exists()
                    ):
                        (self.claimed / path.name).unlink(missing_ok=True)
                        path.unlink()
                    else:
                        self._seen[path.stem] = data
                else:
                    self._seen[path.stem] = data
            except (OSError, ValueError, KeyError, TypeError):
                logger.exception("invalid admission marker: %s", path.name)
                self._blocked_ids.add(path.stem)
        for path in sorted(self.claimed.glob("*.json")):
            if path.stem in self._blocked_ids:
                logger.error("claimed Quick Rename has corrupt journal: %s", path.stem)
            elif path.stem not in self._seen or self._seen[path.stem]["state"] == "retryable":
                # The file rename preceded the durable admission claim; no
                # controller call was possible, so replay is safe.
                self._restore_pending(path.stem)
            elif self._can_release_payload(self._seen[path.stem]):
                path.unlink(missing_ok=True)
            else:
                logger.error("orphan claimed Quick Rename record: %s", path.name)

    def admit(self, command: LocalCommand) -> LocalReply:
        try:
            canonical = _canonical(command)
        except ProtocolError:
            return LocalReply(PROTOCOL_VERSION, command.request_id, LocalReplyStatus.REJECTED)
        digest = hashlib.sha256(canonical).hexdigest()
        if command.request_id in self._blocked_ids:
            return LocalReply(
                PROTOCOL_VERSION,
                command.request_id,
                LocalReplyStatus.REJECTED,
                "corrupt admission marker",
            )
        existing = self._seen.get(command.request_id)
        if existing is not None:
            if existing.get("digest") != digest:
                return LocalReply(
                    PROTOCOL_VERSION,
                    command.request_id,
                    LocalReplyStatus.REJECTED,
                    "request ID payload mismatch",
                )
            if existing["state"] == "terminal":
                return LocalReply(PROTOCOL_VERSION, command.request_id, LocalReplyStatus.DUPLICATE)
            if existing["state"] != "retryable":
                return LocalReply(
                    PROTOCOL_VERSION,
                    command.request_id,
                    LocalReplyStatus.REJECTED,
                    "indeterminate admission; inspect logs",
                )
        claim: dict[str, object] = {
            "id": command.request_id,
            "state": "claimed",
            "digest": digest,
            "path_count": len(command.quick_rename_paths),
            "operation": "QUICK_RENAME",
            "time": self._clock(),
        }
        try:
            _publish_record(self.claimed, command, canonical)
            self._write_journal(command.request_id, claim)
        except (OSError, ProtocolError):
            logger.exception("Quick Rename admission claim failed: %s", command.request_id)
            return LocalReply(
                PROTOCOL_VERSION,
                command.request_id,
                LocalReplyStatus.REJECTED,
                "admission journal unavailable",
            )
        self._admitting_external_id = command.request_id
        try:
            reply = self._submit(command)
        except Exception:
            logger.exception("Quick Rename submission indeterminate: %s", command.request_id)
            self._mark_indeterminate(command.request_id)
            return LocalReply(
                PROTOCOL_VERSION,
                command.request_id,
                LocalReplyStatus.REJECTED,
                "indeterminate admission",
            )
        finally:
            self._admitting_external_id = None
        final_state = (
            "retryable"
            if reply.status in {LocalReplyStatus.QUEUE_FULL, LocalReplyStatus.SHUTTING_DOWN}
            else "terminal"
        )
        terminal = {
            **self._seen[command.request_id],
            "state": final_state,
            "status": reply.status.value,
            "time": self._clock(),
        }
        try:
            self._write_journal(command.request_id, terminal)
        except OSError:
            logger.exception("Quick Rename outcome persistence failed: %s", command.request_id)
            self._mark_indeterminate(command.request_id)
            return LocalReply(
                PROTOCOL_VERSION,
                command.request_id,
                LocalReplyStatus.REJECTED,
                "indeterminate admission",
            )
        if final_state == "retryable":
            self._restore_pending(command.request_id)
        elif self._can_release_payload(terminal):
            (self.claimed / f"{command.request_id}.json").unlink(missing_ok=True)
        return reply

    def _mark_indeterminate(self, request_id: str) -> None:
        record = self._seen.get(request_id)
        if record is None:
            return
        try:
            self._write_journal(
                request_id,
                {**record, "state": "indeterminate", "time": self._clock()},
            )
        except Exception:
            logger.exception("Quick Rename indeterminate marker failed: %s", request_id)

    @staticmethod
    def _can_release_payload(record: dict[str, object]) -> bool:
        return record.get("state") == "terminal" and (
            record.get("status") != LocalReplyStatus.ACCEPTED.value
            or record.get("tracked") is not True
            or record.get("execution") == "completed"
        )

    def _restore_pending(self, request_id: str) -> None:
        claimed = self.claimed / f"{request_id}.json"
        pending = self.pending / claimed.name
        try:
            if pending.exists():
                canonical = _canonical(_read_command(claimed))
                if not _same_payload(pending, canonical):
                    raise ProtocolError("retryable request payload conflict")
                claimed.unlink()
            else:
                claimed.rename(pending)
        except (OSError, ProtocolError):
            logger.exception(
                "retryable Quick Rename record could not return to pending: %s", request_id
            )

    def note_controller_request(self, external_id: str, request: QuickActionRequest) -> None:
        """Persist whether accepted controller work is queued, running, or done."""
        internal_id = request.request_id
        self._controller_to_external[internal_id] = external_id
        execution = self._seen[external_id].get("execution", "queued")
        record = {**self._seen[external_id], "tracked": True, "execution": execution}
        self._write_journal(external_id, record)
        if execution == "completed":
            self._controller_to_external.pop(internal_id, None)

    def _controller_started(self, request: QuickActionRequest) -> None:
        if request.request_id not in self._controller_to_external and self._admitting_external_id:
            self._controller_to_external[request.request_id] = self._admitting_external_id
        self._update_execution(request.request_id, "started")

    def _controller_finished(self, outcome: QuickActionFinished) -> None:
        internal_id = outcome.request.request_id
        if internal_id not in self._controller_to_external and self._admitting_external_id:
            self._controller_to_external[internal_id] = self._admitting_external_id
        self._update_execution(internal_id, "completed")
        self._controller_to_external.pop(internal_id, None)

    def _update_execution(self, internal_id: int, execution: str) -> None:
        external_id = self._controller_to_external.get(internal_id)
        if external_id is None:
            return
        try:
            record = {**self._seen[external_id], "execution": execution}
            self._write_journal(external_id, record)
            if self._can_release_payload(record):
                (self.claimed / f"{external_id}.json").unlink(missing_ok=True)
        except OSError:
            logger.exception("Quick Rename execution marker failed: %s", external_id)

    def drain(self) -> None:
        if self._clock() >= self._next_prune:
            self._prune_terminal()
            self._next_prune = self._clock() + 3600
        for path in sorted(self.pending.glob("*.json")):
            try:
                command = _read_command(path)
                target = self.claimed / path.name
                if target.exists():
                    if not _same_payload(target, _canonical(command)):
                        raise ProtocolError("claimed request payload mismatch")
                    path.unlink()
                    self.admit(command)
                    continue
                path.rename(target)
                reply = self.admit(command)
                if self._can_release_payload(self._seen.get(command.request_id, {})):
                    target.unlink(missing_ok=True)
                elif reply.status is LocalReplyStatus.REJECTED:
                    logger.error(
                        "Quick Rename pending record requires review: %s", command.request_id
                    )
            except (OSError, ProtocolError, ValueError):
                logger.exception("invalid Quick Rename inbox record: %s", path.name)
                try:
                    path.rename(self.quarantine / path.name)
                except OSError:
                    logger.exception("could not quarantine inbox record: %s", path.name)

    def _prune_terminal(self) -> None:
        for request_id, record in tuple(self._seen.items()):
            timestamp = record.get("time")
            if (
                record["state"] == "terminal"
                and not (
                    record.get("status") == LocalReplyStatus.ACCEPTED.value
                    and record.get("tracked") is True
                    and record.get("execution") != "completed"
                )
                and isinstance(timestamp, (int, float))
                and _valid_timestamp(timestamp)
                and self._clock() - timestamp > TERMINAL_RETENTION_SECONDS
                and not (self.pending / f"{request_id}.json").exists()
            ):
                try:
                    (self.claimed / f"{request_id}.json").unlink(missing_ok=True)
                    self._journal_path(request_id).unlink(missing_ok=True)
                except OSError:
                    logger.exception("terminal marker cleanup failed: %s", request_id)


def publish(profile_root: Path, command: LocalCommand) -> Path:
    """Publish a CLI request without replacing another committed ID."""
    content = _canonical(command)
    directory = profile_root / "handoff" / "v1" / "pending"
    return _publish_record(directory, command, content)


def _publish_record(directory: Path, command: LocalCommand, content: bytes) -> Path:
    """Commit one canonical payload without replacing a published record."""
    _safe_directory(directory)
    target = directory / f"{command.request_id}.json"
    if target.is_symlink():
        raise ProtocolError("reparse pending record")
    if target.exists():
        if not _same_payload(target, content):
            raise ProtocolError("request ID payload mismatch")
        return target
    temporary = directory / f".{command.request_id}.{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, target)
        except FileExistsError as exc:
            if not _same_payload(target, content):
                raise ProtocolError("request ID payload mismatch") from exc
    finally:
        temporary.unlink(missing_ok=True)
    return target
