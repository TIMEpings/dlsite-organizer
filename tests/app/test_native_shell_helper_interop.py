from __future__ import annotations

import json
import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from PySide6.QtCore import QCoreApplication, QEventLoop
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from dlsite_organizer.app.single_instance import (
    MAX_REQUEST_FRAME_SIZE,
    LocalCommand,
    LocalCommandName,
    LocalCommandServer,
    LocalReply,
    LocalReplyStatus,
    decode_reply,
    encode_frame_payload,
    instance_identity,
)
from dlsite_organizer.domain.quick_rename import MAX_QUICK_RENAME_ITEMS


def _native_client_path() -> Path:
    repository = Path(__file__).resolve().parents[2]
    return (
        repository
        / "build"
        / "native"
        / "shell_helper"
        / "bin"
        / "shell_helper_interop_client.exe"
    )


def _require_native_client() -> Path:
    path = _native_client_path()
    if os.name != "nt" or not path.is_file():
        pytest.skip("native x64 shell helper interop client is not built")
    return path


@pytest.fixture
def qcore() -> QCoreApplication:
    application = QCoreApplication.instance()
    if application is None:
        application = QCoreApplication([])
    return application


def _run_native(
    qcore: QCoreApplication,
    client: Path,
    profile: Path,
    paths: tuple[str, ...],
    *,
    timeout_seconds: float = 8.0,
) -> subprocess.CompletedProcess[str]:
    arguments = [str(client), "--profile-root", str(profile)]
    for path in paths:
        arguments.extend(("--path", path))
    process = subprocess.Popen(
        arguments,
        cwd=client.parents[4],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + timeout_seconds
    while process.poll() is None and time.monotonic() < deadline:
        qcore.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
        time.sleep(0.005)
    if process.poll() is None:
        process.kill()
    stdout, stderr = process.communicate(timeout=3)
    return subprocess.CompletedProcess(arguments, process.returncode, stdout, stderr)


def _start_server(
    profile: Path,
    handler: Callable[[LocalCommand], LocalReply],
) -> LocalCommandServer:
    server = LocalCommandServer(instance_identity(profile).server_name, handler)
    assert server.listen()
    return server


def _pump_until(
    application: QCoreApplication,
    predicate: Callable[[], bool],
    timeout_ms: int = 1_000,
) -> bool:
    deadline = time.monotonic() + timeout_ms / 1_000
    while time.monotonic() < deadline:
        application.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, 10)
        if predicate():
            return True
    return predicate()


def test_native_win32_client_reaches_qlocalserver_and_preserves_one_batch(
    qcore: QCoreApplication,
    tmp_path: Path,
) -> None:
    client = _require_native_client()
    profile = tmp_path / "profile"
    received: list[LocalCommand] = []

    def handler(command: LocalCommand) -> LocalReply:
        received.append(command)
        return LocalReply(1, command.request_id, LocalReplyStatus.ACCEPTED, "native-ok")

    server = _start_server(profile, handler)
    paths = (r"C:\测试 folder\A", r"C:\space path\B")
    try:
        result = _run_native(qcore, client, profile, paths)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "ACCEPTED"
        assert len(received) == 1
        assert received[0].command is LocalCommandName.QUICK_RENAME
        assert received[0].quick_rename_paths == paths
    finally:
        server.close()


def test_native_win32_client_accepts_exact_python_batch_limit(
    qcore: QCoreApplication,
    tmp_path: Path,
) -> None:
    client = _require_native_client()
    profile = tmp_path / "limit-profile"
    received: list[LocalCommand] = []

    def handler(command: LocalCommand) -> LocalReply:
        received.append(command)
        return LocalReply(1, command.request_id, LocalReplyStatus.ACCEPTED)

    server = _start_server(profile, handler)
    paths = tuple(rf"C:\batch\RJ{index:08d}" for index in range(MAX_QUICK_RENAME_ITEMS))
    try:
        result = _run_native(qcore, client, profile, paths)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "ACCEPTED"
        assert len(received) == 1
        assert len(received[0].quick_rename_paths) == MAX_QUICK_RENAME_ITEMS
    finally:
        server.close()


def test_native_win32_client_rejects_oversized_batch_before_ipc_write(
    qcore: QCoreApplication,
    tmp_path: Path,
) -> None:
    client = _require_native_client()
    profile = tmp_path / "oversized-profile"
    received: list[LocalCommand] = []
    server = _start_server(
        profile,
        lambda command: (
            received.append(command),
            LocalReply(1, command.request_id, LocalReplyStatus.ACCEPTED),
        )[1],
    )
    paths = tuple(
        rf"C:\batch\RJ{index:08d}" for index in range(MAX_QUICK_RENAME_ITEMS + 1)
    )
    try:
        result = _run_native(qcore, client, profile, paths)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "REJECTED"
        assert received == []
    finally:
        server.close()


def test_python_server_rejects_synthetic_oversized_batch_before_handler(
    qcore: QCoreApplication,
    tmp_path: Path,
) -> None:
    profile = tmp_path / "python-oversized-profile"
    received: list[LocalCommand] = []
    server = _start_server(
        profile,
        lambda command: (
            received.append(command),
            LocalReply(1, command.request_id, LocalReplyStatus.ACCEPTED),
        )[1],
    )
    request = {
        "version": 1,
        "request_id": "synthetic-oversized",
        "command": "QUICK_RENAME",
        "payload": {
            "paths": [rf"C:\batch\RJ{index:08d}" for index in range(MAX_QUICK_RENAME_ITEMS + 1)]
        },
    }
    socket = QLocalSocket()
    try:
        socket.connectToServer(instance_identity(profile).server_name)
        assert socket.waitForConnected(1_000)
        payload = json.dumps(request, separators=(",", ":")).encode("utf-8")
        socket.write(encode_frame_payload(payload, MAX_REQUEST_FRAME_SIZE))
        socket.flush()
        assert _pump_until(qcore, lambda: socket.bytesAvailable() > 0)
        reply = decode_reply(bytes(cast(bytes, socket.readAll())))
        assert reply.status is LocalReplyStatus.REJECTED
        assert reply.request_id == "synthetic-oversized"
        assert reply.detail == "一次最多处理 32 个文件夹。"
        assert received == []
    finally:
        socket.abort()
        server.close()


def test_native_win32_client_preserves_rejection_ack(
    qcore: QCoreApplication,
    tmp_path: Path,
) -> None:
    client = _require_native_client()
    profile = tmp_path / "rejected-profile"
    received: list[LocalCommand] = []

    def handler(command: LocalCommand) -> LocalReply:
        received.append(command)
        return LocalReply(1, command.request_id, LocalReplyStatus.REJECTED, "test-rejected")

    server = _start_server(profile, handler)
    try:
        result = _run_native(qcore, client, profile, (r"C:\作品\Rejected",))
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "REJECTED"
        assert len(received) == 1
    finally:
        server.close()


def test_native_win32_client_classifies_missing_ack_as_ambiguous(
    qcore: QCoreApplication,
    tmp_path: Path,
) -> None:
    client = _require_native_client()
    profile = tmp_path / "timeout-profile"
    server = QLocalServer()
    server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
    assert server.listen(instance_identity(profile).server_name)
    try:
        result = _run_native(qcore, client, profile, (r"C:\timeout\A",), timeout_seconds=6)
        assert result.returncode == 4, result.stderr
    finally:
        server.close()
