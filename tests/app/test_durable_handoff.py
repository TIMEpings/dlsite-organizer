from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from dlsite_organizer.app import settings as settings_module
from dlsite_organizer.app.durable_handoff import (
    TERMINAL_RETENTION_SECONDS,
    DurableHandoff,
    _canonical,
    publish,
)
from dlsite_organizer.app.invocation import InvocationParseError, parse_invocation
from dlsite_organizer.app.quick_action_controller import QuickActionController
from dlsite_organizer.app.single_instance import (
    PROTOCOL_VERSION,
    LocalCommand,
    LocalCommandName,
    LocalReply,
    LocalReplyStatus,
    ProtocolError,
    decode_request,
    encode_request,
    instance_identity,
)


def command(request_id: str = "request-1", paths: list[str] | None = None) -> LocalCommand:
    return LocalCommand(
        PROTOCOL_VERSION,
        request_id,
        LocalCommandName.QUICK_RENAME,
        {"paths": paths or ["C:\\作品\\A", "C:\\作品\\B"]},
    )


@pytest.mark.parametrize("local_app_data", [None, ""])
def test_cold_handoff_under_known_folder_profile_is_visible_to_gui(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    local_app_data: str | None,
) -> None:
    known_folder = tmp_path / "Windows" / "LocalAppData"
    environment = {} if local_app_data is None else {"LOCALAPPDATA": local_app_data}
    monkeypatch.setattr(
        settings_module,
        "os",
        SimpleNamespace(name="nt", environ=environment),
    )
    monkeypatch.setattr(settings_module, "_known_folder_local_app_data", lambda: known_folder)

    # identity.cpp appends the same application directory to the Known Folder
    # returned by SHGetKnownFolderPath(FOLDERID_LocalAppData).
    helper_profile_root = known_folder / "dlsite-organizer"
    gui_profile_root = settings_module.default_data_dir()
    assert gui_profile_root == helper_profile_root
    assert instance_identity().profile_root == helper_profile_root

    request = command("cold-known-folder-request")
    publish(helper_profile_root, request)
    submitted: list[str] = []

    def submit(value: LocalCommand) -> LocalReply:
        submitted.append(value.request_id)
        return LocalReply(PROTOCOL_VERSION, value.request_id, LocalReplyStatus.ACCEPTED)

    inbox = DurableHandoff(gui_profile_root, submit)
    inbox.start()

    assert submitted == [request.request_id]
    assert (inbox.journal / f"{request.request_id}.json").is_file()
    inbox.stop()


def test_startup_and_runtime_drain_share_id_admission(tmp_path: Path) -> None:
    calls: list[str] = []

    def submit(value: LocalCommand) -> LocalReply:
        marker = tmp_path / "handoff" / "v1" / "journal" / f"{value.request_id}.json"
        assert json.loads(marker.read_text())["state"] == "claimed"
        calls.append(value.request_id)
        return LocalReply(PROTOCOL_VERSION, value.request_id, LocalReplyStatus.ACCEPTED)

    first = command()
    publish(tmp_path, first)
    inbox = DurableHandoff(tmp_path, submit)
    inbox.start()
    assert calls == [first.request_id]
    assert inbox.admit(first).status is LocalReplyStatus.DUPLICATE
    publish(tmp_path, first)
    inbox.drain()
    assert calls == [first.request_id]
    second = command("request-2")
    publish(tmp_path, second)
    inbox.drain()
    assert calls == [first.request_id, second.request_id]
    assert inbox.admit(command("request-1", ["C:\\different"])).status is LocalReplyStatus.REJECTED
    inbox.stop()


def test_different_ids_same_payload_are_distinct(tmp_path: Path) -> None:
    calls: list[str] = []

    def submit(value: LocalCommand) -> LocalReply:
        calls.append(value.request_id)
        return LocalReply(PROTOCOL_VERSION, value.request_id, LocalReplyStatus.ACCEPTED)

    inbox = DurableHandoff(tmp_path, submit)
    assert inbox.admit(command("A")).status is LocalReplyStatus.ACCEPTED
    assert inbox.admit(command("B")).status is LocalReplyStatus.ACCEPTED
    assert calls == ["A", "B"]


def test_claim_crash_is_indeterminate_and_never_replayed(tmp_path: Path) -> None:
    def crash(_value: LocalCommand) -> LocalReply:
        raise RuntimeError("simulated crash window")

    publish(tmp_path, command())
    first = DurableHandoff(tmp_path, crash)
    first.drain()
    marker = tmp_path / "handoff" / "v1" / "journal" / "request-1.json"
    assert json.loads(marker.read_text())["state"] == "indeterminate"
    calls: list[str] = []

    def submit(value: LocalCommand) -> LocalReply:
        calls.append(value.request_id)
        return LocalReply(PROTOCOL_VERSION, value.request_id, LocalReplyStatus.ACCEPTED)

    recovered = DurableHandoff(tmp_path, submit)
    recovered.drain()
    assert calls == []
    assert recovered.admit(command()).status is LocalReplyStatus.REJECTED
    assert json.loads(marker.read_text())["state"] == "indeterminate"


def test_file_claim_without_journal_is_safely_replayed(tmp_path: Path) -> None:
    request = command()
    pending = publish(tmp_path, request)
    claimed = pending.parent.parent / "claimed"
    claimed.mkdir()
    pending.rename(claimed / pending.name)
    calls: list[str] = []

    def submit(value: LocalCommand) -> LocalReply:
        calls.append(value.request_id)
        return LocalReply(PROTOCOL_VERSION, value.request_id, LocalReplyStatus.ACCEPTED)

    recovered = DurableHandoff(tmp_path, submit)
    assert (recovered.pending / pending.name).exists()
    recovered.drain()
    assert calls == [request.request_id]


def test_retention_expires_terminal_only(tmp_path: Path) -> None:
    now = [100.0]

    def submit(value: LocalCommand) -> LocalReply:
        return LocalReply(PROTOCOL_VERSION, value.request_id, LocalReplyStatus.ACCEPTED)

    first = DurableHandoff(tmp_path, submit, clock=lambda: now[0])
    first.admit(command("old-terminal"))
    journal = tmp_path / "handoff" / "v1" / "journal"
    (journal / "old-claim.json").write_text(
        json.dumps(
            {
                "id": "old-claim",
                "state": "claimed",
                "digest": "a" * 64,
                "path_count": 1,
                "operation": "QUICK_RENAME",
                "time": 100.0,
            }
        ),
        encoding="utf-8",
    )
    now[0] += TERMINAL_RETENTION_SECONDS + 1
    DurableHandoff(tmp_path, submit, clock=lambda: now[0])
    assert not (journal / "old-terminal.json").exists()
    assert json.loads((journal / "old-claim.json").read_text())["state"] == "indeterminate"


def test_malformed_and_oversized_records_are_quarantined(tmp_path: Path) -> None:
    calls: list[str] = []

    def submit(value: LocalCommand) -> LocalReply:
        calls.append(value.request_id)
        return LocalReply(PROTOCOL_VERSION, value.request_id, LocalReplyStatus.ACCEPTED)

    inbox = DurableHandoff(
        tmp_path,
        submit,
    )
    (inbox.pending / "bad.json").write_text("not json", encoding="utf-8")
    (inbox.pending / "huge.json").write_bytes(b"x" * (256 * 1024))
    publish(tmp_path, command("valid-after-malformed"))
    inbox.drain()
    assert (inbox.quarantine / "bad.json").exists()
    assert (inbox.quarantine / "huge.json").exists()
    assert calls == ["valid-after-malformed"]


def test_recoverable_launch_failure_is_not_drained(tmp_path: Path) -> None:
    request = command("recoverable")
    pending = publish(tmp_path, request)
    recoverable = pending.parent.parent / "recoverable"
    recoverable.mkdir()
    pending.rename(recoverable / pending.name)
    calls: list[str] = []

    def submit(value: LocalCommand) -> LocalReply:
        calls.append(value.request_id)
        return LocalReply(PROTOCOL_VERSION, value.request_id, LocalReplyStatus.ACCEPTED)

    inbox = DurableHandoff(tmp_path, submit)
    inbox.drain()
    assert calls == []
    assert (recoverable / pending.name).exists()


def test_failed_launch_transition_stays_prepared_and_does_not_poison_inbox(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    request_a = command("launch-failed-transition")
    prepared = tmp_path / "handoff" / "v1" / "prepared"
    prepared.mkdir(parents=True)
    prepared_path = prepared / f"{request_a.request_id}.json"
    prepared_path.write_bytes(_canonical(request_a))
    calls: list[str] = []

    def submit(value: LocalCommand) -> LocalReply:
        calls.append(value.request_id)
        return LocalReply(PROTOCOL_VERSION, value.request_id, LocalReplyStatus.ACCEPTED)

    # This is a later, unrelated normal application startup after the native
    # failure-state transition could not move A to recoverable/.
    inbox = DurableHandoff(tmp_path, submit)
    inbox.start()
    assert calls == []
    assert prepared_path.exists()
    assert "launch handoff remains non-executable: launch-failed-transition" in caplog.text

    request_b = command("new-valid-request")
    publish(tmp_path, request_b)
    inbox.drain()
    assert calls == [request_b.request_id]
    assert prepared_path.exists()
    inbox.stop()


def test_quarantine_and_indeterminate_work_are_not_replayed(tmp_path: Path) -> None:
    calls: list[str] = []

    def submit(value: LocalCommand) -> LocalReply:
        calls.append(value.request_id)
        return LocalReply(PROTOCOL_VERSION, value.request_id, LocalReplyStatus.ACCEPTED)

    inbox = DurableHandoff(tmp_path, submit)
    quarantined = command("quarantined")
    quarantine_path = inbox.quarantine / f"{quarantined.request_id}.json"
    quarantine_path.write_bytes(_canonical(quarantined))

    indeterminate = command("indeterminate")
    inbox._write_journal(
        indeterminate.request_id,
        {
            "id": indeterminate.request_id,
            "state": "indeterminate",
            "digest": hashlib.sha256(_canonical(indeterminate)).hexdigest(),
            "path_count": len(indeterminate.quick_rename_paths),
            "operation": "QUICK_RENAME",
            "time": 1.0,
        },
    )
    publish(tmp_path, indeterminate)
    inbox.drain()
    assert calls == []
    assert quarantine_path.exists()
    assert json.loads(
        (inbox.journal / f"{indeterminate.request_id}.json").read_text(encoding="utf-8")
    )["state"] == "indeterminate"


def test_timer_observes_requests_published_after_startup_scan(tmp_path: Path) -> None:
    application = QApplication.instance()
    if application is None:
        application = QApplication([])
    calls: list[str] = []

    def submit(value: LocalCommand) -> LocalReply:
        calls.append(value.request_id)
        return LocalReply(PROTOCOL_VERSION, value.request_id, LocalReplyStatus.ACCEPTED)

    inbox = DurableHandoff(tmp_path, submit)
    request = command("promoted-after-startup-scan")
    prepared = inbox.prepared / f"{request.request_id}.json"
    prepared.write_bytes(_canonical(request))
    inbox.start()
    assert calls == []
    prepared.rename(inbox.pending / prepared.name)
    deadline = time.monotonic() + 2.5
    while not calls and time.monotonic() < deadline:
        application.processEvents()
        time.sleep(0.01)
    inbox.stop()
    assert calls == [request.request_id]


def test_corrupt_journal_blocks_same_id_without_overwrite(tmp_path: Path) -> None:
    inbox = DurableHandoff(
        tmp_path,
        lambda value: LocalReply(PROTOCOL_VERSION, value.request_id, LocalReplyStatus.ACCEPTED),
    )
    marker = inbox.journal / "request-1.json"
    marker.write_text("{broken", encoding="utf-8")
    restarted = DurableHandoff(tmp_path, inbox._submit)
    assert restarted.admit(command()).status is LocalReplyStatus.REJECTED
    assert marker.read_text(encoding="utf-8") == "{broken"


def test_unfinished_controller_action_is_indeterminate_after_crash(tmp_path: Path) -> None:
    completions = []

    class Runner:
        def __init__(self, completion):
            self.completion = completion

        def start(self) -> None:
            pass

    def make_runner(_request, completion):
        completions.append(completion)
        return Runner(completion)

    controller = QuickActionController(make_runner)
    inbox: DurableHandoff

    def submit(value: LocalCommand) -> LocalReply:
        admission = controller.submit(value.quick_rename_paths)
        assert admission.request is not None
        inbox.note_controller_request(value.request_id, admission.request)
        return LocalReply(PROTOCOL_VERSION, value.request_id, LocalReplyStatus.ACCEPTED)

    inbox = DurableHandoff(tmp_path, submit, controller=controller)
    assert inbox.admit(command()).status is LocalReplyStatus.ACCEPTED
    payload = inbox.claimed / "request-1.json"
    assert payload.exists()
    recovered = DurableHandoff(tmp_path, submit)
    marker = recovered.journal / "request-1.json"
    assert json.loads(marker.read_text())["state"] == "indeterminate"
    assert payload.exists()
    assert recovered.admit(command()).status is LocalReplyStatus.REJECTED
    assert len(completions) == 1


def test_completed_controller_action_releases_payload(tmp_path: Path) -> None:
    completions = []

    class Runner:
        def start(self) -> None:
            pass

    def make_runner(_request, completion):
        completions.append(completion)
        return Runner()

    controller = QuickActionController(make_runner)
    inbox: DurableHandoff

    def submit(value: LocalCommand) -> LocalReply:
        admission = controller.submit(value.quick_rename_paths)
        assert admission.request is not None
        inbox.note_controller_request(value.request_id, admission.request)
        return LocalReply(PROTOCOL_VERSION, value.request_id, LocalReplyStatus.ACCEPTED)

    inbox = DurableHandoff(tmp_path, submit, controller=controller)
    inbox.admit(command())
    completions[0](None, None)
    assert not (inbox.claimed / "request-1.json").exists()
    recovered = DurableHandoff(tmp_path, submit)
    assert recovered.admit(command()).status is LocalReplyStatus.DUPLICATE


def test_publish_preserves_same_id_and_rejects_conflict(tmp_path: Path) -> None:
    first = command()
    path = publish(tmp_path, first)
    assert publish(tmp_path, first) == path
    with pytest.raises(ProtocolError):
        publish(tmp_path, command(paths=["C:\\different"]))


def test_json_formatting_does_not_change_digest_or_duplicate_admission(tmp_path: Path) -> None:
    calls: list[str] = []

    def submit(value: LocalCommand) -> LocalReply:
        calls.append(value.request_id)
        return LocalReply(PROTOCOL_VERSION, value.request_id, LocalReplyStatus.ACCEPTED)

    request = command("canonical-format")
    inbox = DurableHandoff(tmp_path, submit)
    assert inbox.admit(request).status is LocalReplyStatus.ACCEPTED
    canonical = _canonical(request)
    decoded = json.loads(canonical)
    reordered = json.dumps(
        {
            "payload": decoded["payload"],
            "request_id": decoded["request_id"],
            "command": decoded["command"],
            "version": decoded["version"],
        },
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    framed = len(reordered).to_bytes(4, "big") + reordered
    equivalent = decode_request(framed)
    assert canonical == encode_request(request)[4:]
    assert _canonical(equivalent) == canonical

    (inbox.pending / f"{request.request_id}.json").write_bytes(reordered)
    inbox.drain()
    assert calls == [request.request_id]
    assert json.loads((inbox.journal / f"{request.request_id}.json").read_text())["digest"] == (
        hashlib.sha256(canonical).hexdigest()
    )


def test_terminal_retention_keeps_journal_while_pending_replay_exists(tmp_path: Path) -> None:
    now = [100.0]
    calls: list[str] = []

    def submit(value: LocalCommand) -> LocalReply:
        calls.append(value.request_id)
        return LocalReply(PROTOCOL_VERSION, value.request_id, LocalReplyStatus.ACCEPTED)

    request = command("retained-terminal")
    first = DurableHandoff(tmp_path, submit, clock=lambda: now[0])
    assert first.admit(request).status is LocalReplyStatus.ACCEPTED
    pending = publish(tmp_path, request)
    now[0] += TERMINAL_RETENTION_SECONDS + 1

    recovered = DurableHandoff(tmp_path, submit, clock=lambda: now[0])
    marker = recovered.journal / f"{request.request_id}.json"
    assert marker.exists()
    recovered.drain()
    assert calls == [request.request_id]
    assert not pending.exists()
    assert marker.exists()


@pytest.mark.parametrize("timestamp", [-1.0, float("nan"), True, 10**1000])
def test_malformed_timestamp_never_expires_terminal_marker(
    tmp_path: Path,
    timestamp: object,
) -> None:
    request = command("bad-timestamp")
    inbox = DurableHandoff(
        tmp_path,
        lambda value: LocalReply(PROTOCOL_VERSION, value.request_id, LocalReplyStatus.ACCEPTED),
    )
    marker = inbox.journal / f"{request.request_id}.json"
    content = json.dumps(
        {
            "id": request.request_id,
            "state": "terminal",
            "digest": "a" * 64,
            "path_count": len(request.quick_rename_paths),
            "operation": "QUICK_RENAME",
            "time": timestamp,
            "status": LocalReplyStatus.ACCEPTED.value,
        }
    )
    marker.write_text(content, encoding="utf-8")

    recovered = DurableHandoff(
        tmp_path,
        lambda value: LocalReply(PROTOCOL_VERSION, value.request_id, LocalReplyStatus.ACCEPTED),
    )
    assert marker.read_text(encoding="utf-8") == content
    assert recovered.admit(request).status is LocalReplyStatus.REJECTED


def test_handoff_id_requires_host_and_has_strict_syntax() -> None:
    assert (
        parse_invocation(["--quick-rename-host", "--handoff-id", "abc_123"]).handoff_id == "abc_123"
    )
    assert parse_invocation(["--quick-rename-host"]).handoff_id is None
    with pytest.raises(InvocationParseError):
        parse_invocation(["--handoff-id", "abc_123"])
    with pytest.raises(InvocationParseError):
        parse_invocation(["--quick-rename-host", "--handoff-id", "../bad"])


def test_delayed_startup_and_ambiguous_replay(tmp_path: Path) -> None:
    now = [0.0]
    calls: list[str] = []

    def submit(value: LocalCommand) -> LocalReply:
        calls.append(value.request_id)
        return LocalReply(PROTOCOL_VERSION, value.request_id, LocalReplyStatus.ACCEPTED)

    first = command("delayed")
    publish(tmp_path, first)
    now[0] = 6.0  # Beyond the former post-launch five-second deadline.
    inbox = DurableHandoff(tmp_path, submit, clock=lambda: now[0])
    inbox.drain()
    assert calls == ["delayed"]
    # The primary accepted a frame, but the sender lost its ACK and published
    # the same logical request into the durable inbox.
    ambiguous = command("ambiguous")
    assert inbox.admit(ambiguous).status is LocalReplyStatus.ACCEPTED
    publish(tmp_path, ambiguous)
    inbox.drain()
    assert calls == ["delayed", "ambiguous"]


def test_concurrent_cold_ids_and_retryable_queue(tmp_path: Path) -> None:
    calls: list[str] = []
    attempts: dict[str, int] = {}

    def submit(value: LocalCommand) -> LocalReply:
        attempts[value.request_id] = attempts.get(value.request_id, 0) + 1
        status = (
            LocalReplyStatus.QUEUE_FULL
            if value.request_id == "B" and attempts[value.request_id] == 1
            else LocalReplyStatus.ACCEPTED
        )
        if status is LocalReplyStatus.ACCEPTED:
            calls.append(value.request_id)
        return LocalReply(PROTOCOL_VERSION, value.request_id, status)

    inbox = DurableHandoff(tmp_path, submit)
    for request_id in ("A", "B", "C"):
        publish(tmp_path, command(request_id))
    inbox.drain()
    assert calls == ["A", "C"]
    assert (inbox.pending / "B.json").exists()
    inbox.drain()
    assert calls == ["A", "C", "B"]
    assert attempts == {"A": 1, "B": 2, "C": 1}
