from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


def _child_environment(profile_parent: Path, marker: Path) -> dict[str, str]:
    repository = Path(__file__).resolve().parents[2]
    environment = os.environ.copy()
    environment["LOCALAPPDATA"] = str(profile_parent)
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
