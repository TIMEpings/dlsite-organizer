"""Single-instance election and local command IPC primitives.

This module deliberately stops at the application boundary.  It does not
know how an admitted command is executed and it never creates or activates a
window.  The next application phase supplies the command handler.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import ntpath
import os
import re
import socket as socket_module
import sys
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast

from PySide6.QtCore import QCoreApplication, QEventLoop, QLockFile, QObject, QThread, QTimer, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from dlsite_organizer.app.settings import default_data_dir
from dlsite_organizer.domain.quick_rename import (
    MAX_QUICK_RENAME_ITEMS,
    quick_rename_batch_limit_message,
)

logger = logging.getLogger(__name__)
_USER_ACCESS_OPTION = QLocalServer.SocketOption.UserAccessOption

PROTOCOL_VERSION = 1
FRAME_HEADER_SIZE = 4
MAX_REQUEST_FRAME_SIZE = 256 * 1024
MAX_RESPONSE_FRAME_SIZE = 4 * 1024
MAX_REQUEST_ID_LENGTH = 64
MAX_QUICK_RENAME_PATH_LENGTH = 32767
MAX_REPLY_DETAIL_LENGTH = 512
INCOMPLETE_FRAME_TIMEOUT_MS = 2_000
CLIENT_TOTAL_DEADLINE_MS = 5_000
CLIENT_ACK_DEADLINE_MS = 2_000
CONNECT_RETRY_INTERVAL_MS = 25

_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_UNKNOWN_REQUEST_ID = "unknown"


class LocalCommandName(StrEnum):
    """Commands that may cross the local IPC boundary."""

    ACTIVATE = "ACTIVATE"
    QUICK_RENAME = "QUICK_RENAME"


class LocalReplyStatus(StrEnum):
    """Bounded admission outcomes returned by the primary instance."""

    ACCEPTED = "ACCEPTED"
    DUPLICATE = "DUPLICATE"
    REJECTED = "REJECTED"
    QUEUE_FULL = "QUEUE_FULL"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"


class ProtocolError(ValueError):
    """A strict local IPC frame or message failed validation."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "protocol_error",
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.request_id = request_id


class IncompleteFrameError(ProtocolError):
    """A frame contains too few bytes to decode yet."""


class ClientFailureClassification(StrEnum):
    """Failure classes relevant to higher-level startup policy."""

    CONNECTION_FAILED = "CONNECTION_FAILED"
    AMBIGUOUS_IPC_FAILURE = "AMBIGUOUS_IPC_FAILURE"


class LocalClientError(RuntimeError):
    """A client operation failed without providing a safe local fallback."""

    def __init__(self, classification: ClientFailureClassification, reason: str) -> None:
        super().__init__(reason)
        self.classification = classification

    @property
    def is_ambiguous(self) -> bool:
        return self.classification is ClientFailureClassification.AMBIGUOUS_IPC_FAILURE


class CoordinatorState(StrEnum):
    """Explicit lifecycle states for an instance coordinator."""

    NEW = "NEW"
    PRIMARY_LOCKED = "PRIMARY_LOCKED"
    PRIMARY_LISTENING = "PRIMARY_LISTENING"
    SECONDARY = "SECONDARY"
    SHUTTING_DOWN = "SHUTTING_DOWN"
    CLOSED = "CLOSED"
    ERROR = "ERROR"


class CoordinatorRole(StrEnum):
    """The result of primary election."""

    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"


class OwnerLiveness(StrEnum):
    """Conservative process-owner assessment."""

    ALIVE = "ALIVE"
    DEAD = "DEAD"
    UNKNOWN = "UNKNOWN"


class CoordinatorError(RuntimeError):
    """The primary coordinator could not establish its local server."""


class CoordinatorStateError(RuntimeError):
    """An operation was attempted in an invalid coordinator state."""


@dataclass(frozen=True, slots=True)
class LocalCommand:
    """A validated command admitted by the protocol decoder."""

    version: int
    request_id: str
    command: LocalCommandName
    payload: dict[str, object]

    @property
    def quick_rename_paths(self) -> tuple[str, ...]:
        """Return the validated path batch carried by QUICK_RENAME."""
        if self.command is not LocalCommandName.QUICK_RENAME:
            return ()
        if "path" in self.payload:
            return (cast(str, self.payload["path"]),)
        return tuple(cast(list[str], self.payload["paths"]))


@dataclass(frozen=True, slots=True)
class LocalReply:
    """A bounded reply sent once on a local socket connection."""

    version: int
    request_id: str
    status: LocalReplyStatus
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class InstanceIdentity:
    """Stable names derived only from the canonical application profile."""

    profile_root: Path
    canonical_profile_root: str
    profile_hash: str
    server_name: str
    lock_path: Path

    @classmethod
    def for_profile(cls, profile_root: Path | str | None = None) -> InstanceIdentity:
        canonical = canonicalize_profile_root(profile_root)
        root = Path(canonical)
        profile_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
        return cls(
            profile_root=root,
            canonical_profile_root=canonical,
            profile_hash=profile_hash,
            server_name=f"dlsite-organizer-{profile_hash}",
            lock_path=root / "instance.lock",
        )


@dataclass(frozen=True, slots=True)
class LockOwner:
    """The tuple returned by PySide6's QLockFile.getLockInfo()."""

    pid: int
    hostname: str
    appname: str


def canonicalize_profile_root(profile_root: Path | str | None = None) -> str:
    """Return a stable, filesystem-light profile path representation.

    ``abspath`` and ``normpath`` normalize relative paths, separators, and
    dot segments without resolving symlinks or requiring the directory to
    exist.  ``normcase`` supplies Windows case-insensitivity while retaining
    native case semantics on other platforms.
    """

    raw_path = os.fspath(profile_root if profile_root is not None else default_data_dir())
    normalized = os.path.normcase(os.path.normpath(os.path.abspath(os.path.expanduser(raw_path))))
    if os.name == "nt":
        normalized = normalized.replace("/", "\\")
    return normalized


def instance_identity(profile_root: Path | str | None = None) -> InstanceIdentity:
    """Build the stable identity for one application profile."""

    return InstanceIdentity.for_profile(profile_root)


def new_request_id() -> str:
    """Create the bounded opaque request id used by secondary invocations."""

    return uuid.uuid4().hex


def validate_windows_absolute_path(value: object) -> str:
    """Validate only the structural path contract required by local IPC."""

    if not isinstance(value, str):
        raise ProtocolError("path must be a string", code="bad_path")
    if not value:
        raise ProtocolError("path must not be empty", code="bad_path")
    if len(value) > MAX_QUICK_RENAME_PATH_LENGTH:
        raise ProtocolError("path is too long", code="bad_path")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ProtocolError("path contains a control character", code="bad_path")

    drive, tail = ntpath.splitdrive(value)
    is_drive_absolute = (
        len(drive) == 2 and drive[0].isalpha() and drive[1] == ":" and tail.startswith(("\\", "/"))
    )
    is_unc_absolute = (value.startswith("\\\\") or value.startswith("//")) and ntpath.isabs(value)
    if not (is_drive_absolute or is_unc_absolute):
        raise ProtocolError("path must be an absolute Windows path", code="bad_path")
    return value


def _validate_request_id(value: object) -> str:
    if not isinstance(value, str) or not _REQUEST_ID_RE.fullmatch(value):
        raise ProtocolError("invalid request id", code="bad_request_id")
    return value


def _validate_json_object(value: object, *, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ProtocolError(f"{label} must be an object")
    return cast(dict[str, object], value)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("duplicate JSON object key", code="duplicate_key")
        result[key] = value
    return result


def _reject_nonstandard_json_constant(value: str) -> None:
    raise ProtocolError(f"unsupported JSON constant: {value}", code="invalid_json")


def _encode_json(value: Mapping[str, object], maximum_frame_size: int) -> bytes:
    try:
        payload = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise ProtocolError("message cannot be encoded", code="invalid_message") from exc
    return encode_frame_payload(payload, maximum_frame_size)


def encode_frame_payload(payload: bytes, maximum_frame_size: int) -> bytes:
    """Add the four-byte network-order length prefix to one payload."""

    if len(payload) + FRAME_HEADER_SIZE > maximum_frame_size:
        raise ProtocolError("frame is oversized", code="oversized_frame")
    return len(payload).to_bytes(FRAME_HEADER_SIZE, "big") + payload


def _extract_frame(frame: bytes, maximum_frame_size: int) -> bytes:
    if len(frame) < FRAME_HEADER_SIZE:
        raise IncompleteFrameError("frame header is truncated", code="truncated_frame")
    payload_length = int.from_bytes(frame[:FRAME_HEADER_SIZE], "big")
    if payload_length + FRAME_HEADER_SIZE > maximum_frame_size:
        raise ProtocolError("frame is oversized", code="oversized_frame")
    total_length = FRAME_HEADER_SIZE + payload_length
    if len(frame) < total_length:
        raise IncompleteFrameError("frame payload is truncated", code="truncated_frame")
    if len(frame) > total_length:
        raise ProtocolError("trailing bytes after frame", code="trailing_bytes")
    return frame[FRAME_HEADER_SIZE:total_length]


def _decode_json_object(frame: bytes, maximum_frame_size: int) -> dict[str, object]:
    payload = _extract_frame(frame, maximum_frame_size)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError("payload is not valid UTF-8", code="invalid_utf8") from exc
    try:
        value = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except ProtocolError:
        raise
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProtocolError("payload is not valid JSON", code="invalid_json") from exc
    return _validate_json_object(value, label="top-level payload")


def encode_request(command: LocalCommand) -> bytes:
    """Encode one strict command frame."""

    if command.version != PROTOCOL_VERSION or isinstance(command.version, bool):
        raise ProtocolError("unsupported protocol version", code="unsupported_version")
    request_id = _validate_request_id(command.request_id)
    if not isinstance(command.command, LocalCommandName):
        raise ProtocolError("unknown command", code="unknown_command")
    payload = _validate_json_object(command.payload, label="command payload")
    if command.command is LocalCommandName.ACTIVATE:
        if payload:
            raise ProtocolError("ACTIVATE payload must be empty", code="unexpected_field")
    elif command.command is LocalCommandName.QUICK_RENAME:
        _validate_quick_rename_payload(payload)
    else:
        raise ProtocolError("unknown command", code="unknown_command")
    return _encode_json(
        {
            "version": PROTOCOL_VERSION,
            "request_id": request_id,
            "command": command.command.value,
            "payload": payload,
        },
        MAX_REQUEST_FRAME_SIZE,
    )


def decode_request(frame: bytes) -> LocalCommand:
    """Decode and strictly validate one command frame."""

    value = _decode_json_object(frame, MAX_REQUEST_FRAME_SIZE)
    if set(value) != {"version", "request_id", "command", "payload"}:
        raise ProtocolError("unknown or missing request field", code="unexpected_field")
    version = value["version"]
    if not isinstance(version, int) or isinstance(version, bool):
        raise ProtocolError("version must be an integer", code="bad_version")
    request_id = value["request_id"]
    safe_request_id = (
        request_id
        if isinstance(request_id, str) and len(request_id) <= MAX_REQUEST_ID_LENGTH
        else None
    )
    if version != PROTOCOL_VERSION:
        raise ProtocolError(
            "unsupported protocol version",
            code="unsupported_version",
            request_id=safe_request_id,
        )
    request_id = _validate_request_id(request_id)
    command_value = value["command"]
    if not isinstance(command_value, str):
        raise ProtocolError("command must be a string", code="bad_command", request_id=request_id)
    try:
        command = LocalCommandName(command_value)
    except ValueError as exc:
        raise ProtocolError(
            "unknown command",
            code="unknown_command",
            request_id=request_id,
        ) from exc

    payload = _validate_json_object(value["payload"], label="command payload")
    if command is LocalCommandName.ACTIVATE:
        if payload:
            raise ProtocolError(
                "ACTIVATE payload must be empty",
                code="unexpected_field",
                request_id=request_id,
            )
    else:
        try:
            _validate_quick_rename_payload(payload)
        except ProtocolError as exc:
            raise ProtocolError(str(exc), code=exc.code, request_id=request_id) from exc
    return LocalCommand(PROTOCOL_VERSION, request_id, command, payload)


def _validate_quick_rename_payload(payload: dict[str, object]) -> tuple[str, ...]:
    """Validate the single-path and compatibility batch payload shapes."""
    fields = set(payload)
    if fields == {"path"}:
        return (validate_windows_absolute_path(payload["path"]),)
    if fields != {"paths"}:
        raise ProtocolError(
            "QUICK_RENAME payload must contain path or paths",
            code="unexpected_field",
        )
    values = payload["paths"]
    if not isinstance(values, list) or not values:
        raise ProtocolError("QUICK_RENAME paths must be a non-empty array", code="bad_path")
    if len(values) > MAX_QUICK_RENAME_ITEMS:
        raise ProtocolError("QUICK_RENAME paths exceed the batch limit", code="batch_limit")
    if any(not isinstance(value, str) for value in values):
        raise ProtocolError("QUICK_RENAME paths must contain strings", code="bad_path")
    return tuple(validate_windows_absolute_path(value) for value in values)


def _validate_reply_detail(value: object) -> str:
    if not isinstance(value, str) or len(value) > MAX_REPLY_DETAIL_LENGTH:
        raise ProtocolError("reply detail is invalid", code="bad_detail")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ProtocolError("reply detail contains a control character", code="bad_detail")
    return value


def encode_reply(reply: LocalReply) -> bytes:
    """Encode one bounded strict admission reply frame."""

    if reply.version != PROTOCOL_VERSION or isinstance(reply.version, bool):
        raise ProtocolError("unsupported protocol version", code="unsupported_version")
    request_id = _validate_request_id(reply.request_id)
    if not isinstance(reply.status, LocalReplyStatus):
        raise ProtocolError("unknown reply status", code="unknown_status")
    detail = _validate_reply_detail(reply.detail) if reply.detail is not None else None
    message: dict[str, object] = {
        "version": PROTOCOL_VERSION,
        "request_id": request_id,
        "status": reply.status.value,
    }
    if detail is not None:
        message["detail"] = detail
    return _encode_json(message, MAX_RESPONSE_FRAME_SIZE)


def decode_reply(frame: bytes) -> LocalReply:
    """Decode and strictly validate one reply frame."""

    value = _decode_json_object(frame, MAX_RESPONSE_FRAME_SIZE)
    fields = set(value)
    if not fields <= {"version", "request_id", "status", "detail"}:
        raise ProtocolError("unknown reply field", code="unexpected_field")
    if fields != {"version", "request_id", "status"} and fields != {
        "version",
        "request_id",
        "status",
        "detail",
    }:
        raise ProtocolError("missing reply field", code="missing_field")
    version = value.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ProtocolError("reply version must be an integer", code="bad_version")
    if version != PROTOCOL_VERSION:
        raise ProtocolError("unsupported reply version", code="unsupported_version")
    request_id = _validate_request_id(value.get("request_id"))
    status_value = value.get("status")
    if not isinstance(status_value, str):
        raise ProtocolError("reply status must be a string", code="bad_status")
    try:
        status = LocalReplyStatus(status_value)
    except ValueError as exc:
        raise ProtocolError("unknown reply status", code="unknown_status") from exc
    detail = _validate_reply_detail(value["detail"]) if "detail" in value else None
    return LocalReply(PROTOCOL_VERSION, request_id, status, detail)


LocalCommandHandler = Callable[[LocalCommand], LocalReply]
LivenessChecker = Callable[[LockOwner], OwnerLiveness]


@dataclass(slots=True)
class _ConnectionState:
    socket: QLocalSocket
    buffer: bytearray
    timer: QTimer
    response_sent: bool = False
    waiting_for_bytes: bool = False


class LocalCommandServer(QObject):
    """Own a strict, one-request-per-connection QLocalServer."""

    command_received = Signal(object)
    protocol_rejected = Signal(str)

    def __init__(
        self,
        server_name: str,
        handler: LocalCommandHandler,
        *,
        parent: QObject | None = None,
        incomplete_timeout_ms: int = INCOMPLETE_FRAME_TIMEOUT_MS,
    ) -> None:
        super().__init__(parent)
        self._server_name = server_name
        self._handler = handler
        self._incomplete_timeout_ms = incomplete_timeout_ms
        self._server = QLocalServer(self)
        self._server.setSocketOptions(_USER_ACCESS_OPTION)
        self._server.newConnection.connect(self._on_new_connection)
        self._connections: dict[int, _ConnectionState] = {}
        self._closed = False

    @property
    def server_name(self) -> str:
        return self._server_name

    @property
    def is_listening(self) -> bool:
        return self._server.isListening()

    @property
    def socket_options(self) -> QLocalServer.SocketOption:
        return self._server.socketOptions()

    @property
    def error_string(self) -> str:
        return self._server.errorString()

    @property
    def server_error(self) -> object:
        return self._server.serverError()

    def listen(self) -> bool:
        """Listen once; endpoint removal is deliberately coordinator-owned."""

        if self._closed or self._server.isListening():
            return False
        return self._server.listen(self._server_name)

    def close(self) -> None:
        """Stop accepting and close every active local socket safely."""

        if self._closed:
            return
        self._closed = True
        self._server.close()
        for state in tuple(self._connections.values()):
            state.timer.stop()
            state.socket.abort()
            state.socket.deleteLater()
        self._connections.clear()

    def _on_new_connection(self) -> None:
        while self._server.hasPendingConnections():
            socket = self._server.nextPendingConnection()
            if socket is None:
                return
            timer = QTimer(self)
            timer.setSingleShot(True)
            state = _ConnectionState(socket, bytearray(), timer)
            key = id(socket)
            self._connections[key] = state
            socket.readyRead.connect(lambda socket=socket: self._on_ready_read(socket))
            socket.disconnected.connect(lambda socket=socket: self._on_disconnected(socket))
            socket.bytesWritten.connect(
                lambda _count, socket=socket: self._on_bytes_written(socket)
            )
            timer.timeout.connect(lambda socket=socket: self._on_frame_timeout(socket))
            timer.start(self._incomplete_timeout_ms)
            self._on_ready_read(socket)

    def _state_for(self, socket: QLocalSocket) -> _ConnectionState | None:
        return self._connections.get(id(socket))

    def _on_ready_read(self, socket: QLocalSocket) -> None:
        state = self._state_for(socket)
        if state is None or state.response_sent:
            return
        state.buffer.extend(_read_socket_bytes(socket))
        if len(state.buffer) > MAX_REQUEST_FRAME_SIZE:
            self._reject(socket, ProtocolError("frame is oversized", code="oversized_frame"))
            return
        try:
            _extract_frame(bytes(state.buffer), MAX_REQUEST_FRAME_SIZE)
        except IncompleteFrameError:
            return
        except ProtocolError as exc:
            self._reject(socket, exc)
            return

        state.timer.stop()
        try:
            command = decode_request(bytes(state.buffer))
        except ProtocolError as exc:
            self._reject(socket, exc)
            return
        self.command_received.emit(command)
        try:
            reply = self._handler(command)
        except Exception:
            logger.error("local IPC command handler failed")
            reply = LocalReply(
                PROTOCOL_VERSION,
                command.request_id,
                LocalReplyStatus.REJECTED,
                "handler rejected",
            )
        if not isinstance(reply, LocalReply) or reply.request_id != command.request_id:
            reply = LocalReply(
                PROTOCOL_VERSION,
                command.request_id,
                LocalReplyStatus.REJECTED,
                "invalid handler reply",
            )
        self._send_reply(socket, reply)

    def _reject(self, socket: QLocalSocket, error: ProtocolError) -> None:
        state = self._state_for(socket)
        if state is None or state.response_sent:
            return
        state.timer.stop()
        self.protocol_rejected.emit(error.code)
        status = (
            LocalReplyStatus.UNSUPPORTED_VERSION
            if error.code == "unsupported_version"
            else LocalReplyStatus.REJECTED
        )
        request_id = (
            error.request_id
            if error.request_id and _REQUEST_ID_RE.fullmatch(error.request_id)
            else _UNKNOWN_REQUEST_ID
        )
        detail = (
            quick_rename_batch_limit_message()
            if error.code == "batch_limit"
            else error.code
        )
        self._send_reply(socket, LocalReply(PROTOCOL_VERSION, request_id, status, detail))

    def _on_frame_timeout(self, socket: QLocalSocket) -> None:
        self._reject(socket, ProtocolError("incomplete frame timed out", code="truncated_frame"))

    def _send_reply(self, socket: QLocalSocket, reply: LocalReply) -> None:
        state = self._state_for(socket)
        if state is None or state.response_sent:
            return
        try:
            frame = encode_reply(reply)
        except ProtocolError:
            frame = encode_reply(
                LocalReply(
                    PROTOCOL_VERSION,
                    (
                        reply.request_id
                        if _REQUEST_ID_RE.fullmatch(reply.request_id)
                        else _UNKNOWN_REQUEST_ID
                    ),
                    LocalReplyStatus.REJECTED,
                    "invalid reply",
                )
            )
        state.response_sent = True
        socket.write(frame)
        socket.flush()
        if socket.bytesToWrite() == 0:
            socket.disconnectFromServer()
        else:
            state.waiting_for_bytes = True

    def _on_bytes_written(self, socket: QLocalSocket) -> None:
        state = self._state_for(socket)
        if state is not None and state.waiting_for_bytes and socket.bytesToWrite() == 0:
            state.waiting_for_bytes = False
            socket.disconnectFromServer()

    def _on_disconnected(self, socket: QLocalSocket) -> None:
        state = self._connections.pop(id(socket), None)
        if state is not None:
            state.timer.stop()
            socket.deleteLater()


class LocalCommandClient:
    """Send one command and await one bounded admission reply."""

    def __init__(
        self,
        server_name: str,
        *,
        connect_deadline_ms: int = CLIENT_TOTAL_DEADLINE_MS,
        ack_deadline_ms: int = CLIENT_ACK_DEADLINE_MS,
    ) -> None:
        self._server_name = server_name
        self._connect_deadline_ms = connect_deadline_ms
        self._ack_deadline_ms = ack_deadline_ms

    def send(self, command: LocalCommand) -> LocalReply:
        frame = encode_request(command)
        socket: QLocalSocket | None = None
        try:
            socket = self._connect()
            if socket is None:
                raise LocalClientError(
                    ClientFailureClassification.CONNECTION_FAILED,
                    "primary local IPC endpoint did not connect",
                )
            ack_deadline = time.monotonic() + self._ack_deadline_ms / 1000
            self._write_request(socket, frame, ack_deadline)
            reply_frame = self._read_reply(socket, ack_deadline)
            reply = decode_reply(reply_frame)
            if reply.request_id != command.request_id:
                raise ProtocolError("reply request id mismatch", code="request_id_mismatch")
            return reply
        except LocalClientError:
            raise
        except (ProtocolError, OSError, RuntimeError) as exc:
            raise LocalClientError(
                ClientFailureClassification.AMBIGUOUS_IPC_FAILURE,
                "ambiguous local IPC failure",
            ) from exc
        finally:
            if socket is not None:
                socket.abort()
                socket.deleteLater()

    def _connect(self) -> QLocalSocket | None:
        deadline = time.monotonic() + self._connect_deadline_ms / 1000
        while True:
            remaining_ms = _remaining_milliseconds(deadline)
            if remaining_ms <= 0:
                return None
            socket = QLocalSocket()
            socket.connectToServer(self._server_name)
            if socket.waitForConnected(min(100, remaining_ms)):
                logger.debug("local IPC client connected")
                return socket
            socket.abort()
            _process_qt_events()
            if _remaining_milliseconds(deadline) <= 0:
                return None
            QThread.msleep(min(CONNECT_RETRY_INTERVAL_MS, _remaining_milliseconds(deadline)))

    def _write_request(self, socket: QLocalSocket, frame: bytes, deadline: float) -> None:
        offset = 0
        while offset < len(frame):
            written = socket.write(frame[offset:])
            if written <= 0:
                raise LocalClientError(
                    ClientFailureClassification.AMBIGUOUS_IPC_FAILURE,
                    "ambiguous local IPC failure",
                )
            offset += int(written)
            if offset == len(frame):
                socket.flush()
                return
            remaining_ms = _remaining_milliseconds(deadline)
            if remaining_ms <= 0:
                raise LocalClientError(
                    ClientFailureClassification.AMBIGUOUS_IPC_FAILURE,
                    "ambiguous local IPC failure",
                )
            if not socket.waitForBytesWritten(max(1, remaining_ms)):
                raise LocalClientError(
                    ClientFailureClassification.AMBIGUOUS_IPC_FAILURE,
                    "ambiguous local IPC failure",
                )
        socket.flush()

    def _read_reply(self, socket: QLocalSocket, deadline: float) -> bytes:
        buffer = bytearray()
        while True:
            buffer.extend(_read_socket_bytes(socket))
            if len(buffer) > MAX_RESPONSE_FRAME_SIZE:
                raise ProtocolError("frame is oversized", code="oversized_frame")
            if len(buffer) >= FRAME_HEADER_SIZE:
                payload_length = int.from_bytes(buffer[:FRAME_HEADER_SIZE], "big")
                if payload_length + FRAME_HEADER_SIZE > MAX_RESPONSE_FRAME_SIZE:
                    raise ProtocolError("frame is oversized", code="oversized_frame")
                if len(buffer) >= FRAME_HEADER_SIZE + payload_length:
                    return bytes(buffer)
            remaining_ms = _remaining_milliseconds(deadline)
            if remaining_ms <= 0:
                raise LocalClientError(
                    ClientFailureClassification.AMBIGUOUS_IPC_FAILURE,
                    "ambiguous local IPC failure",
                )
            if not socket.waitForReadyRead(min(100, remaining_ms)):
                buffer.extend(_read_socket_bytes(socket))
                if socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
                    raise LocalClientError(
                        ClientFailureClassification.AMBIGUOUS_IPC_FAILURE,
                        "ambiguous local IPC failure",
                    )
                _process_qt_events()


class _LockLike(Protocol):
    def setStaleLockTime(self, stale_lock_time: int) -> None: ...

    def tryLock(self, timeout: int = 0) -> bool: ...

    def error(self) -> QLockFile.LockError: ...

    def getLockInfo(self) -> tuple[int, str, str]: ...

    def removeStaleLockFile(self) -> bool: ...

    def unlock(self) -> None: ...

    def isLocked(self) -> bool: ...


class _ServerLike(Protocol):
    def listen(self) -> bool: ...

    def close(self) -> None: ...


ServerFactory = Callable[[str, LocalCommandHandler], _ServerLike]
LockFactory = Callable[[str], _LockLike]
RemoveServer = Callable[[str], bool]


def read_lock_owner(lock: _LockLike) -> LockOwner | None:
    """Defensively validate the actual PySide6 getLockInfo() tuple."""

    try:
        info = lock.getLockInfo()
    except Exception:
        return None
    if not isinstance(info, tuple) or len(info) != 3:
        return None
    pid, hostname, appname = info
    if type(pid) is not int or not isinstance(hostname, str) or not isinstance(appname, str):
        return None
    return LockOwner(pid, hostname, appname)


def assess_lock_owner(owner: LockOwner) -> OwnerLiveness:
    """Use the conservative Win32 fallback only on Windows."""

    if sys.platform != "win32":
        return OwnerLiveness.UNKNOWN
    return _assess_windows_process(owner)


def _assess_windows_process(owner: LockOwner) -> OwnerLiveness:
    if owner.pid <= 0 or not owner.hostname.strip() or not owner.appname.strip():
        return OwnerLiveness.UNKNOWN
    try:
        local_hostname = socket_module.gethostname()
    except OSError:
        return OwnerLiveness.UNKNOWN
    if owner.hostname.casefold() != local_hostname.casefold():
        return OwnerLiveness.UNKNOWN

    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_uint]
        open_process.restype = ctypes.c_void_p
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        query_image = kernel32.QueryFullProcessImageNameW
        query_image.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_uint),
        ]
        query_image.restype = ctypes.c_int
    except (AttributeError, OSError):
        return OwnerLiveness.UNKNOWN

    process_query_limited_information = 0x1000
    error_invalid_parameter = 87
    handle = open_process(process_query_limited_information, 0, owner.pid)
    if not handle:
        if ctypes.get_last_error() == error_invalid_parameter:
            return OwnerLiveness.DEAD
        return OwnerLiveness.UNKNOWN
    try:
        image_buffer = ctypes.create_unicode_buffer(32768)
        image_length = ctypes.c_uint(len(image_buffer))
        if not query_image(handle, 0, image_buffer, ctypes.byref(image_length)):
            return OwnerLiveness.UNKNOWN
        actual_name = ntpath.splitext(ntpath.basename(image_buffer.value))[0].casefold()
        expected_name = ntpath.splitext(ntpath.basename(owner.appname))[0].casefold()
        if not actual_name or not expected_name or actual_name != expected_name:
            return OwnerLiveness.UNKNOWN
        return OwnerLiveness.ALIVE
    finally:
        close_handle(handle)


def _default_command_handler(command: LocalCommand) -> LocalReply:
    return LocalReply(PROTOCOL_VERSION, command.request_id, LocalReplyStatus.ACCEPTED)


class InstanceCoordinator:
    """Elect one primary per profile and own its local IPC server."""

    def __init__(
        self,
        profile_root: Path | str | None = None,
        *,
        lock_factory: LockFactory | None = None,
        server_factory: ServerFactory | None = None,
        remove_server: RemoveServer | None = None,
        liveness_checker: LivenessChecker = assess_lock_owner,
    ) -> None:
        self.identity = instance_identity(profile_root)
        self._lock_factory = lock_factory or cast(LockFactory, QLockFile)
        self._server_factory = server_factory or (
            lambda name, handler: LocalCommandServer(name, handler)
        )
        self._remove_server = remove_server or QLocalServer.removeServer
        self._liveness_checker = liveness_checker
        self._lock: _LockLike | None = None
        self._server: _ServerLike | None = None
        self._state = CoordinatorState.NEW
        self._lock_error: QLockFile.LockError | None = None

    @property
    def state(self) -> CoordinatorState:
        return self._state

    @property
    def role(self) -> CoordinatorRole | None:
        if self._state in {CoordinatorState.PRIMARY_LOCKED, CoordinatorState.PRIMARY_LISTENING}:
            return CoordinatorRole.PRIMARY
        if self._state is CoordinatorState.SECONDARY:
            return CoordinatorRole.SECONDARY
        return None

    @property
    def lock_error(self) -> QLockFile.LockError | None:
        return self._lock_error

    @property
    def server(self) -> _ServerLike | None:
        return self._server

    def start(
        self,
        handler: LocalCommandHandler | None = None,
        *,
        listen: bool = True,
    ) -> CoordinatorRole:
        """Elect and prepare the primary local server.

        ``listen=False`` is the application bootstrap seam: the winning
        process owns the profile lock while components and the command router
        are built, then calls :meth:`listen`.  The default remains the
        historical one-call election/listen behavior used by core callers.
        """

        if self._state is not CoordinatorState.NEW:
            raise CoordinatorStateError(f"cannot start from {self._state}")
        command_handler = handler or _default_command_handler
        try:
            self.identity.profile_root.mkdir(parents=True, exist_ok=True)
            lock = self._lock_factory(str(self.identity.lock_path))
            lock.setStaleLockTime(0)
            self._lock = lock
            acquired = lock.tryLock(0)
            if not acquired and lock.error() is QLockFile.LockError.LockFailedError:
                acquired = self._recover_dead_owner(lock)
            if not acquired:
                self._lock_error = lock.error()
                self._state = CoordinatorState.SECONDARY
                logger.debug("secondary instance detected")
                return CoordinatorRole.SECONDARY

            self._state = CoordinatorState.PRIMARY_LOCKED
            server = self._server_factory(self.identity.server_name, command_handler)
            self._server = server
            if listen:
                self.listen()
            return CoordinatorRole.PRIMARY
        except CoordinatorError:
            self._release_resources()
            self._state = CoordinatorState.ERROR
            raise
        except (OSError, RuntimeError) as exc:
            self._release_resources()
            self._state = CoordinatorState.ERROR
            raise CoordinatorError("primary instance could not initialize") from exc

    def listen(self) -> bool:
        """Start the prepared primary server after application routing is ready."""
        if self._state is CoordinatorState.PRIMARY_LISTENING:
            return True
        if self._state is not CoordinatorState.PRIMARY_LOCKED or self._server is None:
            raise CoordinatorStateError(f"cannot listen from {self._state}")
        try:
            if not self._server.listen():
                # The lock proves that no live primary can own this endpoint.
                # Endpoint cleanup is therefore restricted to this branch.
                self._remove_server(self.identity.server_name)
                if not self._server.listen():
                    raise CoordinatorError("primary local IPC server could not listen")
            self._state = CoordinatorState.PRIMARY_LISTENING
            logger.debug("primary local IPC server listening")
            return True
        except CoordinatorError:
            self._release_resources()
            self._state = CoordinatorState.ERROR
            raise
        except (OSError, RuntimeError) as exc:
            self._release_resources()
            self._state = CoordinatorState.ERROR
            raise CoordinatorError("primary local IPC server could not listen") from exc

    def _recover_dead_owner(self, lock: _LockLike) -> bool:
        owner = read_lock_owner(lock)
        if owner is None:
            return False
        liveness = self._liveness_checker(owner)
        if liveness is not OwnerLiveness.DEAD:
            return False
        if not lock.removeStaleLockFile():
            return False
        return lock.tryLock(0)

    def close(self) -> None:
        """Close server/sockets first and release the lock last; idempotent."""

        if self._state is CoordinatorState.CLOSED:
            return
        self._state = CoordinatorState.SHUTTING_DOWN
        self._release_resources()
        self._state = CoordinatorState.CLOSED

    def stop_server(self) -> None:
        """Stop accepting commands while retaining the profile lock."""
        if self._state in {CoordinatorState.CLOSED, CoordinatorState.ERROR}:
            return
        if self._state not in {
            CoordinatorState.PRIMARY_LOCKED,
            CoordinatorState.PRIMARY_LISTENING,
            CoordinatorState.SHUTTING_DOWN,
        }:
            return
        self._state = CoordinatorState.SHUTTING_DOWN
        if self._server is not None:
            self._server.close()
            self._server = None

    def release_lock(self) -> None:
        """Release the profile lock after application resources are finished."""
        if self._state is CoordinatorState.CLOSED:
            return
        self._release_resources()
        self._state = CoordinatorState.CLOSED

    def _release_resources(self) -> None:
        if self._server is not None:
            self._server.close()
            self._server = None
        if self._lock is not None:
            try:
                if self._lock.isLocked():
                    self._lock.unlock()
            finally:
                self._lock = None


def _remaining_milliseconds(deadline: float) -> int:
    return max(0, int((deadline - time.monotonic()) * 1000))


def _read_socket_bytes(socket: QLocalSocket) -> bytes:
    return bytes(cast(bytes, socket.readAll()))


def _process_qt_events() -> None:
    application = QCoreApplication.instance()
    if application is not None:
        application.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 0)
