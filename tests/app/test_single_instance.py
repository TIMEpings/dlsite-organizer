from __future__ import annotations

import json
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import QCoreApplication, QEventLoop, QLockFile
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from dlsite_organizer.app.single_instance import (
    CLIENT_ACK_DEADLINE_MS,
    CLIENT_TOTAL_DEADLINE_MS,
    MAX_QUICK_RENAME_PATH_LENGTH,
    MAX_REQUEST_FRAME_SIZE,
    MAX_RESPONSE_FRAME_SIZE,
    CoordinatorRole,
    CoordinatorState,
    CoordinatorStateError,
    InstanceCoordinator,
    LocalClientError,
    LocalCommand,
    LocalCommandClient,
    LocalCommandName,
    LocalCommandServer,
    LocalReply,
    LocalReplyStatus,
    LockOwner,
    OwnerLiveness,
    ProtocolError,
    _LockLike,
    assess_lock_owner,
    decode_reply,
    decode_request,
    encode_frame_payload,
    encode_reply,
    encode_request,
    instance_identity,
    new_request_id,
    read_lock_owner,
)


def _activate(request_id: str | None = None) -> LocalCommand:
    return LocalCommand(1, request_id or new_request_id(), LocalCommandName.ACTIVATE, {})


def _quick_rename(path: str = r"C:\Works\RJ00000001") -> LocalCommand:
    return LocalCommand(1, new_request_id(), LocalCommandName.QUICK_RENAME, {"path": path})


def _raw_request(value: object) -> bytes:
    return encode_frame_payload(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        MAX_REQUEST_FRAME_SIZE,
    )


def _raw_reply(value: object) -> bytes:
    return encode_frame_payload(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        MAX_RESPONSE_FRAME_SIZE,
    )


def _pump_until(
    application: QCoreApplication,
    predicate: Callable[[], bool],
    *,
    timeout_ms: int = 1_000,
) -> bool:
    deadline = time.monotonic() + timeout_ms / 1_000
    while time.monotonic() < deadline:
        application.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
        if predicate():
            return True
    return predicate()


def _socket_bytes(socket: QLocalSocket) -> bytes:
    return bytes(cast(bytes, socket.readAll()))


def test_identity_is_stable_and_does_not_depend_on_executable(tmp_path: Path) -> None:
    profile = tmp_path / "profile"
    equivalent = profile.parent / profile.name / ".." / profile.name
    first = instance_identity(profile)
    second = instance_identity(equivalent)

    assert first == second
    assert first.server_name == f"dlsite-organizer-{first.profile_hash}"
    assert len(first.profile_hash) == 32
    assert first.lock_path == first.profile_root / "instance.lock"
    assert instance_identity(tmp_path / "other").server_name != first.server_name
    assert "python" not in first.server_name


def test_identity_normalizes_windows_case_when_running_on_windows(tmp_path: Path) -> None:
    if __import__("os").name != "nt":
        pytest.skip("Windows case normalization")
    profile = tmp_path / "Profile"
    assert (
        instance_identity(profile).server_name
        == instance_identity(str(profile).lower()).server_name
    )


def test_identity_does_not_require_profile_directory_to_exist(tmp_path: Path) -> None:
    profile = tmp_path / "not-created-yet"
    identity = instance_identity(profile)
    assert not profile.exists()
    assert identity.canonical_profile_root


def test_protocol_accepts_activate_and_quick_rename() -> None:
    activate = _activate("a1")
    decoded_activate = decode_request(encode_request(activate))
    assert decoded_activate == activate

    quick_rename = LocalCommand(
        1, "b2", LocalCommandName.QUICK_RENAME, {"path": r"C:\作品\RJ00000001"}
    )
    assert decode_request(encode_request(quick_rename)) == quick_rename

    batch = LocalCommand(
        1,
        "batch1",
        LocalCommandName.QUICK_RENAME,
        {"paths": [r"C:\作品\RJ00000001", r"C:\作品\BJ00000002"]},
    )
    decoded_batch = decode_request(encode_request(batch))
    assert decoded_batch == batch
    assert decoded_batch.quick_rename_paths == (
        r"C:\作品\RJ00000001",
        r"C:\作品\BJ00000002",
    )


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (
            '{"version":1,"request_id":"a1","command":"ACTIVATE","payload":{},"extra":1}',
            "unexpected_field",
        ),
        ('{"version":1,"request_id":"a1","command":"NOPE","payload":{}}', "unknown_command"),
        (
            '{"version":2,"request_id":"a1","command":"ACTIVATE","payload":{}}',
            "unsupported_version",
        ),
        ("[1,2,3]", "protocol_error"),
        (
            '{"version":1,"request_id":"a1","command":"ACTIVATE","payload":{"path":"C:\\\\x"}}',
            "unexpected_field",
        ),
    ],
)
def test_protocol_rejects_invalid_messages(value: str, code: str) -> None:
    with pytest.raises(ProtocolError) as error:
        decode_request(_raw_request(json.loads(value)))
    assert error.value.code == code


def test_protocol_rejects_duplicate_keys_invalid_utf8_oversize_truncation_and_trailing() -> None:
    duplicate = (
        b'{"version":1,"request_id":"a1","request_id":"a2","command":"ACTIVATE","payload":{}}'
    )
    with pytest.raises(ProtocolError, match="duplicate"):
        decode_request(encode_frame_payload(duplicate, MAX_REQUEST_FRAME_SIZE))

    with pytest.raises(ProtocolError) as invalid_utf8:
        decode_request(encode_frame_payload(b"\xff", MAX_REQUEST_FRAME_SIZE))
    assert invalid_utf8.value.code == "invalid_utf8"

    oversized = (MAX_REQUEST_FRAME_SIZE).to_bytes(4, "big")
    with pytest.raises(ProtocolError) as too_large:
        decode_request(oversized)
    assert too_large.value.code == "oversized_frame"

    with pytest.raises(ProtocolError) as truncated:
        decode_request(b"\x00\x00\x00\x20{}")
    assert truncated.value.code == "truncated_frame"

    with pytest.raises(ProtocolError) as trailing:
        decode_request(encode_request(_activate()) + b"x")
    assert trailing.value.code == "trailing_bytes"


@pytest.mark.parametrize(
    "path",
    [
        "",
        "relative\\folder",
        r"C:folder",
        "/unix/root",
        "C:\\bad\x00path",
        "C:\\bad\x1fpath",
    ],
)
def test_protocol_rejects_bad_quick_rename_paths(path: str) -> None:
    with pytest.raises(ProtocolError, match="path"):
        encode_request(LocalCommand(1, "path1", LocalCommandName.QUICK_RENAME, {"path": path}))


def test_protocol_rejects_quick_rename_path_length() -> None:
    path = "C:\\" + "x" * MAX_QUICK_RENAME_PATH_LENGTH
    with pytest.raises(ProtocolError, match="path"):
        encode_request(LocalCommand(1, "path1", LocalCommandName.QUICK_RENAME, {"path": path}))


def test_protocol_rejects_wrong_types_and_response_bounds() -> None:
    with pytest.raises(ProtocolError):
        decode_request(
            _raw_request({"version": 1, "request_id": 1, "command": "ACTIVATE", "payload": {}})
        )
    with pytest.raises(ProtocolError):
        decode_request(
            _raw_request({"version": 1, "request_id": "a1", "command": "ACTIVATE", "payload": []})
        )
    with pytest.raises(ProtocolError):
        decode_request(
            _raw_request({"version": 1, "request_id": "a1", "command": "ACTIVATE", "payload": {}})[
                :-1
            ]
        )

    reply = LocalReply(1, "a1", LocalReplyStatus.ACCEPTED, "ok")
    assert decode_reply(encode_reply(reply)) == reply
    with pytest.raises(ProtocolError):
        encode_reply(LocalReply(1, "a1", LocalReplyStatus.REJECTED, "x" * 513))
    with pytest.raises(ProtocolError):
        decode_reply(_raw_reply({"version": 1, "request_id": "a1", "status": "NOPE"}))
    with pytest.raises(ProtocolError):
        decode_reply(
            _raw_reply({"version": 1, "request_id": "a1", "status": "ACCEPTED", "detail": None})
        )


def test_protocol_rejects_missing_fields_and_oversized_request_id() -> None:
    with pytest.raises(ProtocolError, match="field"):
        decode_request(_raw_request({"version": 1, "request_id": "a1", "payload": {}}))
    with pytest.raises(ProtocolError, match="request id"):
        decode_request(
            _raw_request(
                {
                    "version": 1,
                    "request_id": "x" * 65,
                    "command": "ACTIVATE",
                    "payload": {},
                }
            )
        )


class _FakeLock:
    def __init__(self, acquired: bool, info: tuple[object, ...] = (123, "host", "app")) -> None:
        self.acquired = acquired
        self.info = info
        self.error_value = (
            QLockFile.LockError.NoError if acquired else QLockFile.LockError.LockFailedError
        )
        self.stale_time: int | None = None
        self.remove_calls = 0
        self.unlock_calls = 0
        self.try_calls = 0

    def setStaleLockTime(self, stale_lock_time: int) -> None:
        self.stale_time = stale_lock_time

    def tryLock(self, timeout: int = 0) -> bool:
        self.try_calls += 1
        self.error_value = (
            QLockFile.LockError.NoError if self.acquired else QLockFile.LockError.LockFailedError
        )
        return self.acquired

    def error(self) -> QLockFile.LockError:
        return self.error_value

    def getLockInfo(self) -> tuple[int, str, str]:
        return cast(tuple[int, str, str], self.info)

    def removeStaleLockFile(self) -> bool:
        self.remove_calls += 1
        self.acquired = True
        return True

    def isLocked(self) -> bool:
        return self.acquired

    def unlock(self) -> None:
        self.unlock_calls += 1
        self.acquired = False


class _FakeServer:
    def __init__(self, listen_results: list[bool]) -> None:
        self.listen_results = listen_results
        self.listen_calls = 0
        self.close_calls = 0

    def listen(self) -> bool:
        self.listen_calls += 1
        return self.listen_results.pop(0)

    def close(self) -> None:
        self.close_calls += 1


class _BrokenInfoLock(_FakeLock):
    def getLockInfo(self) -> tuple[int, str, str]:
        raise RuntimeError("metadata unavailable")


def test_lock_owner_tuple_is_validated_from_get_lock_info() -> None:
    lock = _FakeLock(True, (77, "HOST", "app"))
    assert read_lock_owner(lock) is not None
    assert read_lock_owner(lock).pid == 77  # type: ignore[union-attr]
    assert read_lock_owner(_FakeLock(False, (77, "HOST"))) is None


def test_real_qt_get_lock_info_returns_the_approved_tuple(tmp_path: Path) -> None:
    lock = QLockFile(str(tmp_path / "instance.lock"))
    lock.setStaleLockTime(0)
    assert lock.tryLock(0)
    try:
        owner = read_lock_owner(cast(_LockLike, lock))
        assert owner is not None
        assert isinstance(owner.pid, int)
        assert isinstance(owner.hostname, str)
        assert isinstance(owner.appname, str)
        expected_liveness = (
            OwnerLiveness.ALIVE if sys.platform == "win32" else OwnerLiveness.UNKNOWN
        )
        assert assess_lock_owner(owner) is expected_liveness
    finally:
        lock.unlock()


def test_primary_election_sets_stale_time_and_listens(tmp_path: Path) -> None:
    lock = _FakeLock(True)
    server = _FakeServer([True])
    removed: list[str] = []
    coordinator = InstanceCoordinator(
        tmp_path / "profile",
        lock_factory=lambda _path: lock,
        server_factory=lambda _name, _handler: server,
        remove_server=lambda name: removed.append(name) is None,
    )

    assert coordinator.start() is CoordinatorRole.PRIMARY
    assert coordinator.state is CoordinatorState.PRIMARY_LISTENING
    assert lock.stale_time == 0
    assert removed == []
    coordinator.close()
    coordinator.close()
    assert lock.unlock_calls == 1
    assert coordinator.state is CoordinatorState.CLOSED


def test_primary_election_can_hold_lock_before_server_listen(tmp_path: Path) -> None:
    lock = _FakeLock(True)
    server = _FakeServer([True])
    coordinator = InstanceCoordinator(
        tmp_path / "profile",
        lock_factory=lambda _path: lock,
        server_factory=lambda _name, _handler: server,
    )

    assert coordinator.start(listen=False) is CoordinatorRole.PRIMARY
    assert coordinator.state is CoordinatorState.PRIMARY_LOCKED
    assert server.listen_calls == 0
    assert coordinator.listen()
    assert coordinator.state is CoordinatorState.PRIMARY_LISTENING
    coordinator.stop_server()
    assert lock.isLocked()
    coordinator.release_lock()
    assert not lock.isLocked()
    assert coordinator.state is CoordinatorState.CLOSED


def test_secondary_never_creates_or_removes_server(tmp_path: Path) -> None:
    lock = _FakeLock(False)
    created = 0
    removed: list[str] = []
    coordinator = InstanceCoordinator(
        tmp_path / "profile",
        lock_factory=lambda _path: lock,
        server_factory=lambda _name, _handler: (_ for _ in ()).throw(
            AssertionError("server created")
        ),
        remove_server=lambda name: removed.append(name) is True,
        liveness_checker=lambda _owner: OwnerLiveness.ALIVE,
    )
    assert coordinator.start() is CoordinatorRole.SECONDARY
    assert coordinator.state is CoordinatorState.SECONDARY
    assert created == 0
    assert removed == []


@pytest.mark.parametrize("liveness", [OwnerLiveness.ALIVE, OwnerLiveness.UNKNOWN])
def test_live_or_unknown_owner_is_never_removed(tmp_path: Path, liveness: OwnerLiveness) -> None:
    lock = _FakeLock(False)
    coordinator = InstanceCoordinator(
        tmp_path / "profile",
        lock_factory=lambda _path: lock,
        liveness_checker=lambda _owner: liveness,
    )
    assert coordinator.start() is CoordinatorRole.SECONDARY
    assert lock.remove_calls == 0


def test_lock_metadata_failure_and_pid_reuse_ambiguity_fail_closed(tmp_path: Path) -> None:
    broken = _BrokenInfoLock(False)
    coordinator = InstanceCoordinator(tmp_path / "broken", lock_factory=lambda _path: broken)
    assert coordinator.start() is CoordinatorRole.SECONDARY
    assert broken.remove_calls == 0

    assert assess_lock_owner(LockOwner(-1, "host", "app")) is OwnerLiveness.UNKNOWN
    assert assess_lock_owner(LockOwner(123, "different-host", "app")) is OwnerLiveness.UNKNOWN


def test_dead_owner_may_be_removed_and_retried(tmp_path: Path) -> None:
    lock = _FakeLock(False)
    server = _FakeServer([True])
    coordinator = InstanceCoordinator(
        tmp_path / "profile",
        lock_factory=lambda _path: lock,
        server_factory=lambda _name, _handler: server,
        liveness_checker=lambda _owner: OwnerLiveness.DEAD,
    )
    assert coordinator.start() is CoordinatorRole.PRIMARY
    assert lock.remove_calls == 1
    assert lock.try_calls == 2
    coordinator.close()


def test_stale_endpoint_cleanup_is_primary_only_and_retried(tmp_path: Path) -> None:
    lock = _FakeLock(True)
    server = _FakeServer([False, True])
    removed: list[str] = []
    coordinator = InstanceCoordinator(
        tmp_path / "profile",
        lock_factory=lambda _path: lock,
        server_factory=lambda _name, _handler: server,
        remove_server=lambda name: removed.append(name) is None,
    )
    assert coordinator.start() is CoordinatorRole.PRIMARY
    assert server.listen_calls == 2
    assert removed == [coordinator.identity.server_name]
    coordinator.close()


def test_coordinator_rejects_double_start_and_preserves_error_state(tmp_path: Path) -> None:
    coordinator = InstanceCoordinator(tmp_path / "profile")
    coordinator.close()
    with pytest.raises(CoordinatorStateError):
        coordinator.start()


@pytest.fixture
def qcore() -> QCoreApplication:
    application = QCoreApplication.instance()
    if application is None:
        application = QCoreApplication([])
    return application


def test_real_local_server_client_round_trip_and_two_connections(
    qcore: QCoreApplication, tmp_path: Path
) -> None:
    identity = instance_identity(tmp_path / f"profile-{uuid.uuid4().hex}")
    received: list[LocalCommand] = []

    def handler(command: LocalCommand) -> LocalReply:
        received.append(command)
        return LocalReply(1, command.request_id, LocalReplyStatus.ACCEPTED, "admitted")

    server = LocalCommandServer(identity.server_name, handler)
    assert server.socket_options & QLocalServer.SocketOption.UserAccessOption
    assert server.listen()
    try:
        client = LocalCommandClient(identity.server_name)
        first = _activate()
        second = _quick_rename()
        assert client.send(first).status is LocalReplyStatus.ACCEPTED
        assert client.send(second).status is LocalReplyStatus.ACCEPTED
        assert received == [first, second]
    finally:
        server.close()


def test_real_local_server_accepts_partial_frame_arrival(
    qcore: QCoreApplication, tmp_path: Path
) -> None:
    identity = instance_identity(tmp_path / f"partial-{uuid.uuid4().hex}")
    received: list[LocalCommand] = []
    server = LocalCommandServer(
        identity.server_name,
        lambda command: (
            received.append(command),
            LocalReply(1, command.request_id, LocalReplyStatus.ACCEPTED),
        )[1],
    )
    assert server.listen()
    socket = QLocalSocket()
    command = _activate()
    frame = encode_request(command)
    try:
        socket.connectToServer(identity.server_name)
        assert socket.waitForConnected(1_000)
        socket.write(frame[:4])
        socket.flush()
        _pump_until(qcore, lambda: False, timeout_ms=20)
        assert received == []
        socket.write(frame[4:15])
        socket.flush()
        _pump_until(qcore, lambda: False, timeout_ms=20)
        assert received == []
        socket.write(frame[15:])
        socket.flush()
        assert _pump_until(qcore, lambda: bool(received))
        assert _pump_until(qcore, lambda: socket.bytesAvailable() > 0)
        assert decode_reply(_socket_bytes(socket)).status is LocalReplyStatus.ACCEPTED
    finally:
        socket.abort()
        server.close()


def test_real_local_server_disconnect_mid_frame_does_not_dispatch(
    qcore: QCoreApplication, tmp_path: Path
) -> None:
    identity = instance_identity(tmp_path / f"disconnect-{uuid.uuid4().hex}")
    received: list[LocalCommand] = []
    server = LocalCommandServer(
        identity.server_name,
        lambda command: (
            received.append(command),
            LocalReply(1, command.request_id, LocalReplyStatus.ACCEPTED),
        )[1],
    )
    assert server.listen()
    socket = QLocalSocket()
    try:
        socket.connectToServer(identity.server_name)
        assert socket.waitForConnected(1_000)
        socket.write(encode_request(_activate())[:8])
        socket.flush()
        _pump_until(qcore, lambda: False, timeout_ms=20)
        socket.abort()
        _pump_until(qcore, lambda: not received, timeout_ms=100)
        assert received == []
    finally:
        socket.abort()
        server.close()


def test_real_local_server_times_out_incomplete_frame_and_cleans_up(
    qcore: QCoreApplication, tmp_path: Path
) -> None:
    identity = instance_identity(tmp_path / f"timeout-{uuid.uuid4().hex}")
    server = LocalCommandServer(
        identity.server_name,
        lambda command: LocalReply(1, command.request_id, LocalReplyStatus.ACCEPTED),
        incomplete_timeout_ms=20,
    )
    assert server.listen()
    socket = QLocalSocket()
    try:
        socket.connectToServer(identity.server_name)
        assert socket.waitForConnected(1_000)
        socket.write(b"\x00\x00\x00\x10")
        socket.flush()
        assert _pump_until(qcore, lambda: socket.bytesAvailable() > 0, timeout_ms=500)
        reply = decode_reply(_socket_bytes(socket))
        assert reply.status is LocalReplyStatus.REJECTED
        assert reply.detail == "truncated_frame"
    finally:
        socket.abort()
        server.close()


def test_client_ack_timeout_is_ambiguous_after_request_write(
    qcore: QCoreApplication, tmp_path: Path
) -> None:
    identity = instance_identity(tmp_path / f"ack-timeout-{uuid.uuid4().hex}")
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    assert server.listen(identity.server_name)
    try:
        with pytest.raises(LocalClientError) as error:
            LocalCommandClient(identity.server_name, ack_deadline_ms=50).send(_activate())
        assert error.value.is_ambiguous
    finally:
        server.close()


def test_real_startup_race_has_exactly_one_primary(tmp_path: Path) -> None:
    profile = tmp_path / "race-profile"
    first = InstanceCoordinator(profile)
    second = InstanceCoordinator(profile)
    try:
        assert first.start() is CoordinatorRole.PRIMARY
        assert second.start() is CoordinatorRole.SECONDARY
        assert first.state is CoordinatorState.PRIMARY_LISTENING
        assert second.state is CoordinatorState.SECONDARY
        assert second.server is None
    finally:
        second.close()
        first.close()


def test_client_connect_deadline_is_bounded_and_not_ambiguous_before_connect(
    qcore: QCoreApplication, tmp_path: Path
) -> None:
    missing = instance_identity(tmp_path / f"missing-{uuid.uuid4().hex}")
    client = LocalCommandClient(
        missing.server_name,
        connect_deadline_ms=50,
        ack_deadline_ms=CLIENT_ACK_DEADLINE_MS,
    )
    with pytest.raises(LocalClientError) as error:
        client.send(_activate())
    assert not error.value.is_ambiguous
    assert error.value.classification.value == "CONNECTION_FAILED"


def test_client_malformed_reply_is_ambiguous(qcore: QCoreApplication, tmp_path: Path) -> None:
    identity = instance_identity(tmp_path / f"malformed-{uuid.uuid4().hex}")

    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)

    def send_malformed_reply() -> None:
        socket = server.nextPendingConnection()
        if socket is None:
            return
        socket.readyRead.connect(
            lambda socket=socket: (
                socket.readAll(),
                socket.write(
                    _raw_reply({"version": 1, "request_id": "wrong", "status": "ACCEPTED"})
                ),
                socket.flush(),
            )
        )

    server.newConnection.connect(send_malformed_reply)
    assert server.listen(identity.server_name)
    try:
        with pytest.raises(LocalClientError) as error:
            LocalCommandClient(
                identity.server_name, connect_deadline_ms=CLIENT_TOTAL_DEADLINE_MS
            ).send(_activate())
        assert error.value.is_ambiguous
    finally:
        server.close()
