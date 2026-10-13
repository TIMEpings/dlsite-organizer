from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from dlsite_organizer.app.durable_handoff import publish
from dlsite_organizer.app.single_instance import PROTOCOL_VERSION, LocalCommand, LocalCommandName


def _child_environment(profile_parent: Path, marker: Path) -> dict[str, str]:
    repository = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["LOCALAPPDATA"] = str(profile_parent)
    environment["APPDATA"] = str(profile_parent)
    environment["PYTHONUTF8"] = "1"
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["DLSITE_ORGANIZER_TEST_MODE"] = "1"
    environment["DLSITE_ORGANIZER_TEST_MARKER"] = str(marker)
    source_root = str(repository / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        item for item in (source_root, str(repository), environment.get("PYTHONPATH", "")) if item
    )
    return environment


def _start_primary(profile_parent: Path, marker: Path) -> subprocess.Popen[str]:
    process = subprocess.Popen(
        [sys.executable, "-m", "tests.app.subprocess_helper"],
        cwd=Path(__file__).resolve().parents[2],
        env=_child_environment(profile_parent, marker),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    _wait_for_marker(process, marker, "ready")
    assert process.poll() is None
    return process


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


def _run_native_client(profile: Path, paths: tuple[Path, ...]) -> subprocess.CompletedProcess[str]:
    client = _native_client_path()
    if os.name != "nt" or not client.is_file():
        pytest.skip("native x64 shell helper interop client is not built")
    arguments = [str(client), "--profile-root", str(profile)]
    for path in paths:
        arguments.extend(("--path", str(path)))
    return subprocess.run(
        arguments,
        cwd=client.parents[4],
        capture_output=True,
        text=True,
        timeout=10,
    )


def _run_secondary(
    profile_parent: Path,
    marker: Path,
    *arguments: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "tests.app.subprocess_helper", *arguments],
        cwd=Path(__file__).resolve().parents[2],
        env=_child_environment(profile_parent, marker),
        capture_output=True,
        text=True,
        timeout=10,
    )


def _wait_for_marker(
    process: subprocess.Popen[str],
    marker: Path,
    suffix: str,
    timeout: float = 10.0,
) -> None:
    target = marker.with_name(f"{marker.name}.{suffix}")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if target.is_file():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"subprocess exited before {suffix}: code={process.returncode}\n"
                f"stdout={stdout}\nstderr={stderr}"
            )
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {target}")


def _wait_for_journal_state(path: Path, state: str, timeout: float = 10.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    last_record: dict[str, object] | None = None
    while time.monotonic() < deadline:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            time.sleep(0.05)
            continue
        if isinstance(record, dict):
            last_record = record
            if record.get("state") == state:
                return record
        time.sleep(0.05)
    raise AssertionError(
        f"timed out waiting for journal state {state!r}; last record: {last_record!r}"
    )


def _stop_process(process: subprocess.Popen[str]) -> tuple[str, str]:
    if process.poll() is None:
        process.terminate()
    try:
        return process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        return process.communicate(timeout=10)


def test_normal_secondary_activates_primary_without_components_or_database(
    tmp_path: Path,
) -> None:
    primary_marker = tmp_path / "primary"
    secondary_marker = tmp_path / "secondary"
    primary = _start_primary(tmp_path, primary_marker)
    try:
        result = _run_secondary(tmp_path, secondary_marker)
        assert result.returncode == 0, result.stderr
        assert primary.poll() is None
        assert primary_marker.with_name("primary.components").is_file()
        assert primary_marker.with_name("primary.database").is_file()
        assert not secondary_marker.with_name("secondary.components").exists()
        assert not secondary_marker.with_name("secondary.database").exists()
    finally:
        _stop_process(primary)


def test_quick_rename_secondary_forwards_once_without_local_runtime(
    tmp_path: Path,
) -> None:
    primary_marker = tmp_path / "primary"
    secondary_marker = tmp_path / "secondary"
    quick_paths = [tmp_path / "not-a-work-folder", tmp_path / "also-not-a-work-folder"]
    for quick_path in quick_paths:
        quick_path.mkdir()
    primary = _start_primary(tmp_path, primary_marker)
    try:
        result = _run_secondary(
            tmp_path,
            secondary_marker,
            "--quick-rename",
            *(str(path) for path in quick_paths),
        )
        assert result.returncode == 0, result.stderr
        assert primary.poll() is None
        assert not secondary_marker.with_name("secondary.components").exists()
        assert not secondary_marker.with_name("secondary.database").exists()
        _wait_for_marker(primary, primary_marker, "quick")
    finally:
        _stop_process(primary)


def test_rapid_secondary_quick_requests_keep_one_primary_and_no_duplicate_bootstrap(
    tmp_path: Path,
) -> None:
    primary_marker = tmp_path / "primary"
    primary = _start_primary(tmp_path, primary_marker)
    clients: list[tuple[subprocess.Popen[str], Path]] = []
    try:
        for index in range(3):
            path = tmp_path / f"invalid-{index}"
            path.mkdir()
            marker = tmp_path / f"secondary-{index}"
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "tests.app.subprocess_helper",
                    "--quick-rename",
                    str(path),
                ],
                cwd=Path(__file__).resolve().parents[2],
                env=_child_environment(tmp_path, marker),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            clients.append((process, marker))

        for process, marker in clients:
            stdout, stderr = process.communicate(timeout=10)
            assert process.returncode == 0, f"stdout={stdout}\nstderr={stderr}"
            assert not marker.with_name(f"{marker.name}.components").exists()
            assert not marker.with_name(f"{marker.name}.database").exists()
        assert primary.poll() is None
    finally:
        for process, _marker in clients:
            if process.poll() is None:
                _stop_process(process)
        _stop_process(primary)


def test_no_primary_quick_launch_becomes_persistent_primary_without_changing_startup_mode(
    tmp_path: Path,
) -> None:
    application_data = tmp_path / "dlsite-organizer"
    application_data.mkdir()
    config = application_data / "config.toml"
    config.write_text('startup_mode = "full"\n', encoding="utf-8")
    before = config.read_text(encoding="utf-8")
    marker = tmp_path / "initial"
    quick_path = tmp_path / "not-a-work-folder"
    quick_path.mkdir()

    process = subprocess.Popen(
        [sys.executable, "-m", "tests.app.subprocess_helper", "--quick-rename", str(quick_path)],
        cwd=Path(__file__).resolve().parents[2],
        env=_child_environment(tmp_path, marker),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_marker(process, marker, "ready")
        _wait_for_marker(process, marker, "quick")
        assert process.poll() is None
        assert config.read_text(encoding="utf-8") == before
    finally:
        _stop_process(process)


def test_no_primary_helper_host_starts_lightweight_without_creating_a_quick_action(
    tmp_path: Path,
) -> None:
    application_data = tmp_path / "dlsite-organizer"
    application_data.mkdir()
    config = application_data / "config.toml"
    config.write_text('startup_mode = "full"\n', encoding="utf-8")
    before = config.read_text(encoding="utf-8")
    marker = tmp_path / "host"

    process = subprocess.Popen(
        [sys.executable, "-m", "tests.app.subprocess_helper", "--quick-rename-host"],
        cwd=Path(__file__).resolve().parents[2],
        env=_child_environment(tmp_path, marker),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_marker(process, marker, "ready")
        _wait_for_marker(process, marker, "lightweight")
        assert process.poll() is None
        assert not marker.with_name("host.quick").exists()
        assert config.read_text(encoding="utf-8") == before
    finally:
        _stop_process(process)


def test_native_helper_reaches_production_primary_once_as_one_three_path_batch(
    tmp_path: Path,
) -> None:
    application_data = tmp_path / "dlsite-organizer"
    application_data.mkdir()
    (application_data / "config.toml").write_text('startup_mode = "full"\n', encoding="utf-8")
    marker = tmp_path / "native-primary"
    primary = _start_primary(tmp_path, marker)
    paths = tuple(tmp_path / f"RJ{index:08d} invalid" for index in range(3))
    for path in paths:
        path.mkdir()

    try:
        result = _run_native_client(application_data, paths)
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "ACCEPTED"
        _wait_for_marker(primary, marker, "full")
        _wait_for_marker(primary, marker, "quick")
        assert (
            marker.with_name("native-primary.controller-submit-count")
            .read_text(encoding="ascii")
            == "1"
        )
        assert marker.with_name("native-primary.quick-count").read_text(encoding="utf-8") == "3"
        assert primary.poll() is None
    finally:
        _stop_process(primary)


def test_durable_request_survives_startup_beyond_old_deadline(tmp_path: Path) -> None:
    marker = tmp_path / "delayed-primary"
    barrier = tmp_path / "release-build"
    profile = tmp_path / "dlsite-organizer"
    paths = [tmp_path / "RJ00000001 invalid", tmp_path / "RJ00000002 invalid"]
    for path in paths:
        path.mkdir()
    request = LocalCommand(
        PROTOCOL_VERSION,
        "delayed-original-id",
        LocalCommandName.QUICK_RENAME,
        {"paths": [str(path) for path in paths]},
    )
    publish(profile, request)
    environment = _child_environment(tmp_path, marker)
    environment["DLSITE_ORGANIZER_TEST_BUILD_BARRIER"] = str(barrier)
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "tests.app.subprocess_helper",
            "--quick-rename-host",
            "--handoff-id",
            request.request_id,
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_marker(process, marker, "startup-paused")
        time.sleep(5.2)
        assert not marker.with_name("delayed-primary.ready").exists()
        barrier.write_text("release", encoding="ascii")
        _wait_for_marker(process, marker, "quick")
        assert marker.with_name("delayed-primary.controller-submit-count").read_text() == "1"
        assert marker.with_name("delayed-primary.quick-count").read_text() == "2"
        journal = profile / "handoff" / "v1" / "journal" / f"{request.request_id}.json"
        assert _wait_for_journal_state(journal, "terminal")["state"] == "terminal"
    finally:
        barrier.touch()
        _stop_process(process)


def test_shutdown_gap_secondary_takes_lock_and_drains_request(tmp_path: Path) -> None:
    barrier = tmp_path / "finish-old-shutdown"
    new_shutdown = tmp_path / "shutdown-new-primary"
    old_marker = tmp_path / "old-primary"
    new_marker = tmp_path / "new-primary"
    environment = _child_environment(tmp_path, old_marker)
    environment["DLSITE_ORGANIZER_TEST_SHUTDOWN_GAP_BARRIER"] = str(barrier)
    old = subprocess.Popen(
        [sys.executable, "-m", "tests.app.subprocess_helper"],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    new: subprocess.Popen[str] | None = None
    try:
        _wait_for_marker(old, old_marker, "shutdown-gap")
        # Exercise real Quick Rename completion without incidental metadata I/O.
        path = tmp_path / "not-a-work-folder"
        path.mkdir()
        request = LocalCommand(
            PROTOCOL_VERSION,
            "shutdown-gap-id",
            LocalCommandName.QUICK_RENAME,
            {"paths": [str(path)]},
        )
        profile = tmp_path / "dlsite-organizer"
        pending = publish(profile, request)
        published_payload = pending.read_bytes()
        journal = profile / "handoff" / "v1" / "journal" / f"{request.request_id}.json"
        new_environment = _child_environment(tmp_path, new_marker)
        new_environment["DLSITE_ORGANIZER_TEST_SHUTDOWN_FILE"] = str(new_shutdown)
        new = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tests.app.subprocess_helper",
                "--quick-rename-host",
                "--handoff-id",
                request.request_id,
            ],
            cwd=Path(__file__).resolve().parents[2],
            env=new_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        # A failed production election proves contention, not just a process
        # that has been spawned but has yet to attempt ownership.
        _wait_for_marker(new, new_marker, "secondary-contending")
        assert old.poll() is None
        assert new.poll() is None
        for suffix in ("components", "database", "primary-owned", "ready", "quick"):
            assert not new_marker.with_name(f"new-primary.{suffix}").exists()
        for suffix in ("database-disposed", "lock-released", "quick", "controller-submit-count"):
            assert not old_marker.with_name(f"old-primary.{suffix}").exists()
        assert pending.read_bytes() == published_payload
        assert not journal.exists()

        barrier.write_text("release", encoding="ascii")
        old_stdout, old_stderr = old.communicate(timeout=10)
        assert old.returncode == 0, f"stdout={old_stdout}\nstderr={old_stderr}"
        assert "Traceback" not in old_stderr
        assert old_marker.with_name("old-primary.shutdown-completed").is_file()
        assert old_marker.with_name("old-primary.lock-released").is_file()
        _wait_for_marker(new, new_marker, "ready")
        _wait_for_marker(new, new_marker, "quick")
        _wait_for_marker(new, new_marker, "controller-finished")
        record = json.loads(journal.read_text(encoding="utf-8"))
        new_shutdown.write_text("shutdown", encoding="ascii")
        new_stdout, new_stderr = new.communicate(timeout=10)
        assert new.returncode == 0, f"stdout={new_stdout}\nstderr={new_stderr}"
        assert "Traceback" not in new_stderr
        assert new_marker.with_name("new-primary.shutdown-completed").is_file()
        assert new_marker.with_name("new-primary.lock-released").is_file()
        assert new_marker.with_name("new-primary.controller-submit-count").read_text() == "1"
        assert new_marker.with_name("new-primary.quick-count").read_text() == "1"
        processed_paths = json.loads(new_marker.with_name("new-primary.quick-paths").read_text())
        assert processed_paths == [str(path)]
        assert not old_marker.with_name("old-primary.controller-submit-count").exists()
        assert not old_marker.with_name("old-primary.quick").exists()
        assert record["id"] == request.request_id
        assert record["status"] == "ACCEPTED"
        assert record["operation"] == "QUICK_RENAME"
        assert record["path_count"] == 1
        assert record["tracked"] is True
        assert record["state"] == "terminal"
        assert record["execution"] == "completed"
        assert json.loads(journal.read_text()) == record
        for directory in ("prepared", "pending", "claimed", "recoverable", "quarantine"):
            assert not list((profile / "handoff" / "v1" / directory).glob("*.json"))

        def event_time(marker: Path, name: str) -> int:
            events = [
                json.loads(line)
                for line in Path(f"{marker}.events.jsonl").read_text().splitlines()
            ]
            return min(event["time_ns"] for event in events if event["event"] == name)

        assert (
            event_time(old_marker, "primary-owned")
            < event_time(old_marker, "shutdown-requested")
            < event_time(old_marker, "admission-stopped-lock-held")
            < event_time(new_marker, "secondary-contending")
            < event_time(old_marker, "shutdown-gate-open")
            < event_time(old_marker, "database-disposed")
            < event_time(old_marker, "lock-release")
            < event_time(new_marker, "primary-owned")
        )
        assert not old_marker.with_name("old-primary.shutdown-gate-timeout").exists()
    finally:
        barrier.touch()
        new_shutdown.touch()
        if new is not None:
            _stop_process(new)
        _stop_process(old)


@pytest.mark.parametrize("launch_arguments", [(), ("--quick-rename",)])
def test_terminated_primary_can_be_replaced_in_isolated_profile(
    tmp_path: Path,
    launch_arguments: tuple[str, ...],
) -> None:
    first_marker = tmp_path / "first"
    first = _start_primary(tmp_path, first_marker)
    _stop_process(first)

    second_marker = tmp_path / "second"
    if launch_arguments:
        quick_path = tmp_path / "not-a-work-folder"
        quick_path.mkdir()
        second = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tests.app.subprocess_helper",
                *launch_arguments,
                str(quick_path),
            ],
            cwd=Path(__file__).resolve().parents[2],
            env=_child_environment(tmp_path, second_marker),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    else:
        second = subprocess.Popen(
            [sys.executable, "-m", "tests.app.subprocess_helper"],
            cwd=Path(__file__).resolve().parents[2],
            env=_child_environment(tmp_path, second_marker),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    try:
        _wait_for_marker(second, second_marker, "ready")
        assert second.poll() is None
    finally:
        _stop_process(second)
