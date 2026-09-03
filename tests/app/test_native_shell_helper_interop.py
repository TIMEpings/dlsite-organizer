from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEventLoop
from PySide6.QtNetwork import QLocalServer

from dlsite_organizer.app.single_instance import (
    LocalCommand,
    LocalCommandName,
    LocalCommandServer,
    LocalReply,
    LocalReplyStatus,
    instance_identity,
)


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
