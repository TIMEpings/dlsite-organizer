import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from dlsite_organizer.services import explorer_integration
from dlsite_organizer.services.explorer_integration import (
    EXPLORER_CLSID,
    EXPLORER_CLSID_KEY_PATH,
    EXPLORER_COMMAND_KEY_PATH,
    EXPLORER_DELEGATE_EXECUTE_VALUE,
    EXPLORER_HELPER_FILENAME,
    EXPLORER_ICON_VALUE,
    EXPLORER_KEY_PATH,
    EXPLORER_LEGACY_MULTI_SELECT_MODEL,
    EXPLORER_LOCAL_SERVER_KEY_PATH,
    EXPLORER_MENU_LABEL,
    EXPLORER_MULTI_SELECT_MODEL,
    EXPLORER_MULTI_SELECT_MODEL_VALUE,
    ExplorerIntegrationError,
    ExplorerIntegrationService,
    ExplorerRegistrationState,
    build_explorer_icon_value,
    build_local_server_command,
    build_quick_rename_command,
    notify_shell_association_changed,
)


@pytest.fixture(autouse=True)
def mock_shell_association_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep registry contract tests independent of the host Shell process."""
    monkeypatch.setattr(explorer_integration, "notify_shell_association_changed", lambda: None)


class MemoryRegistry:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.events: list[str] = []
        self.fail_after_writes: int | None = None

    def read_value(self, key_path: str, value_name: str = "") -> str | None:
        return self.values.get((key_path, value_name))

    def write_value(self, key_path: str, value_name: str, value: str) -> None:
        if self.fail_after_writes is not None:
            if self.fail_after_writes == 0:
                self.fail_after_writes = None
                raise PermissionError("simulated registry write failure")
            self.fail_after_writes -= 1
        self.values[(key_path, value_name)] = value
        self.events.append("write")

    def delete_value(self, key_path: str, value_name: str = "") -> None:
        self.values.pop((key_path, value_name), None)
        self.events.append("delete-value")

    def delete_key(self, key_path: str) -> None:
        prefix = key_path + "\\"
        matching = [
            key
            for key in self.values
            if key[0] == key_path or key[0].startswith(prefix)
        ]
        if not matching:
            raise FileNotFoundError(key_path)
        for key in matching:
            del self.values[key]
        self.events.append("delete-key")


def _package(tmp_path: Path, name: str = "DLsite Organizer 日本語") -> tuple[Path, Path]:
    root = tmp_path / name
    root.mkdir()
    executable = root / "dlsite-organizer.exe"
    helper = root / EXPLORER_HELPER_FILENAME
    executable.write_bytes(b"main")
    helper.write_bytes(b"helper")
    return executable, helper


def _service(registry: MemoryRegistry, executable: Path) -> ExplorerIntegrationService:
    return ExplorerIntegrationService(
        registry,
        executable_path_provider=lambda: executable,
    )


def _write_legacy(registry: MemoryRegistry, executable: Path) -> None:
    registry.write_value(EXPLORER_KEY_PATH, "", EXPLORER_MENU_LABEL)
    registry.write_value(
        EXPLORER_KEY_PATH,
        EXPLORER_MULTI_SELECT_MODEL_VALUE,
        EXPLORER_LEGACY_MULTI_SELECT_MODEL,
    )
    registry.write_value(
        EXPLORER_KEY_PATH,
        EXPLORER_ICON_VALUE,
        build_explorer_icon_value(executable),
    )
    registry.write_value(
        EXPLORER_COMMAND_KEY_PATH,
        "",
        build_quick_rename_command(executable),
    )


def test_command_and_local_server_use_direct_safe_windows_quoting() -> None:
    executable = r"C:\Program Files\DLsite Organizer\A&B (日本語)\dlsite-organizer.exe"
    helper = r"C:\Program Files\DLsite Organizer\A&B (日本語)\dlsite-shell-helper.exe"

    assert build_quick_rename_command(executable) == (
        r'"C:\Program Files\DLsite Organizer\A&B (日本語)\dlsite-organizer.exe" '
        r'--quick-rename "%1"'
    )
    assert build_local_server_command(helper) == (
        r'"C:\Program Files\DLsite Organizer\A&B (日本語)\dlsite-shell-helper.exe"'
    )
    for value in (build_quick_rename_command(executable), build_local_server_command(helper)):
        assert "cmd.exe" not in value.casefold()
        assert "powershell" not in value.casefold()


def test_empty_registry_is_absent_and_registration_writes_final_schema(tmp_path: Path) -> None:
    registry = MemoryRegistry()
    executable, helper = _package(tmp_path)
    service = _service(registry, executable)

    assert service.inspect().state is ExplorerRegistrationState.ABSENT

    registration = service.register_current_executable()

    assert registration.state is ExplorerRegistrationState.CURRENT
    assert registry.values[(EXPLORER_KEY_PATH, "")] == EXPLORER_MENU_LABEL
    assert registry.values[(EXPLORER_KEY_PATH, EXPLORER_MULTI_SELECT_MODEL_VALUE)] == "Player"
    assert EXPLORER_MULTI_SELECT_MODEL == "Player"
    assert registry.values[(EXPLORER_KEY_PATH, EXPLORER_ICON_VALUE)] == (
        build_explorer_icon_value(executable)
    )
    assert registry.values[
        (EXPLORER_COMMAND_KEY_PATH, EXPLORER_DELEGATE_EXECUTE_VALUE)
    ] == EXPLORER_CLSID
    assert (EXPLORER_COMMAND_KEY_PATH, "") not in registry.values
    assert registry.values[
        (EXPLORER_LOCAL_SERVER_KEY_PATH, "")
    ] == build_local_server_command(helper)
    assert registration.registered_executable == executable
    assert registration.registered_helper == helper


def test_legacy_migrates_to_delegate_execute_and_removes_static_command(tmp_path: Path) -> None:
    registry = MemoryRegistry()
    executable, helper = _package(tmp_path)
    _write_legacy(registry, executable)
    service = _service(registry, executable)

    assert service.inspect().state is ExplorerRegistrationState.LEGACY

    registration = service.register_current_executable()

    assert registration.state is ExplorerRegistrationState.CURRENT
    assert registry.values[(EXPLORER_KEY_PATH, EXPLORER_MULTI_SELECT_MODEL_VALUE)] == "Player"
    assert registry.values[
        (EXPLORER_COMMAND_KEY_PATH, EXPLORER_DELEGATE_EXECUTE_VALUE)
    ] == EXPLORER_CLSID
    assert (EXPLORER_COMMAND_KEY_PATH, "") not in registry.values
    assert registry.values[
        (EXPLORER_LOCAL_SERVER_KEY_PATH, "")
    ] == build_local_server_command(helper)


def test_registration_is_idempotent_at_same_portable_location(tmp_path: Path) -> None:
    registry = MemoryRegistry()
    executable, _helper = _package(tmp_path)
    service = _service(registry, executable)
    service.register_current_executable()
    first = dict(registry.values)

    second = service.register_current_executable()

    assert second.state is ExplorerRegistrationState.CURRENT
    assert registry.values == first


def test_moved_portable_package_is_stale_then_updates_all_owned_paths(tmp_path: Path) -> None:
    registry = MemoryRegistry()
    old_executable, old_helper = _package(tmp_path, "Old DLsite Organizer")
    new_executable, new_helper = _package(tmp_path, "New DLsite Organizer")
    old_service = _service(registry, old_executable)
    old_service.register_current_executable()

    moved_service = _service(registry, new_executable)
    stale = moved_service.inspect()
    assert stale.state is ExplorerRegistrationState.STALE
    assert stale.registered_executable == old_executable
    assert stale.registered_helper == old_helper

    updated = moved_service.register_current_executable()

    assert updated.state is ExplorerRegistrationState.CURRENT
    assert registry.values[(EXPLORER_KEY_PATH, EXPLORER_ICON_VALUE)] == build_explorer_icon_value(
        new_executable
    )
    assert registry.values[(EXPLORER_LOCAL_SERVER_KEY_PATH, "")] == build_local_server_command(
        new_helper
    )
    registered_local_server = registry.values[(EXPLORER_LOCAL_SERVER_KEY_PATH, "")]
    assert str(old_helper).casefold() not in registered_local_server.casefold()


def test_missing_helper_fails_before_mutating_an_existing_legacy_registration(
    tmp_path: Path,
) -> None:
    registry = MemoryRegistry()
    executable, helper = _package(tmp_path)
    _write_legacy(registry, executable)
    before = dict(registry.values)
    helper.unlink()

    with pytest.raises(ExplorerIntegrationError):
        _service(registry, executable).register_current_executable()

    assert registry.values == before
    assert _service(registry, executable).inspect().state is ExplorerRegistrationState.LEGACY


def test_registered_helper_removal_changes_current_to_stale(tmp_path: Path) -> None:
    registry = MemoryRegistry()
    executable, helper = _package(tmp_path)
    service = _service(registry, executable)
    service.register_current_executable()
    helper.unlink()

    assert service.inspect().state is ExplorerRegistrationState.STALE


def test_wrong_model_delegate_local_server_or_legacy_command_is_stale(tmp_path: Path) -> None:
    registry = MemoryRegistry()
    executable, _helper = _package(tmp_path)
    service = _service(registry, executable)
    service.register_current_executable()

    mutations = (
        lambda: registry.values.__setitem__(
            (EXPLORER_KEY_PATH, EXPLORER_MULTI_SELECT_MODEL_VALUE), "Single"
        ),
        lambda: registry.values.__setitem__(
            (EXPLORER_COMMAND_KEY_PATH, EXPLORER_DELEGATE_EXECUTE_VALUE), "{wrong}"
        ),
        lambda: registry.values.__setitem__(
            (EXPLORER_LOCAL_SERVER_KEY_PATH, ""), r'"C:\old\helper.exe"'
        ),
        lambda: registry.values.__setitem__(
            (EXPLORER_COMMAND_KEY_PATH, ""), r'"C:\old.exe" --quick-rename "%1"'
        ),
    )
    for mutate in mutations:
        clean = _service(registry, executable)
        clean.register_current_executable()
        mutate()
        assert clean.inspect().state is ExplorerRegistrationState.STALE


def test_missing_command_key_and_partial_clsid_are_stale(tmp_path: Path) -> None:
    registry = MemoryRegistry()
    executable, _helper = _package(tmp_path)
    service = _service(registry, executable)
    service.register_current_executable()

    registry.delete_key(EXPLORER_COMMAND_KEY_PATH)
    assert service.inspect().state is ExplorerRegistrationState.STALE

    registry.write_value(EXPLORER_COMMAND_KEY_PATH, EXPLORER_DELEGATE_EXECUTE_VALUE, EXPLORER_CLSID)
    registry.delete_key(EXPLORER_LOCAL_SERVER_KEY_PATH)
    assert service.inspect().state is ExplorerRegistrationState.STALE


def test_unregister_current_legacy_stale_and_partial_states_is_idempotent(tmp_path: Path) -> None:
    for mode in ("current", "legacy", "stale", "partial"):
        registry = MemoryRegistry()
        executable, helper = _package(tmp_path, f"package-{mode}")
        service = _service(registry, executable)
        if mode == "current":
            service.register_current_executable()
        elif mode == "legacy":
            _write_legacy(registry, executable)
        elif mode == "stale":
            service.register_current_executable()
            helper.unlink()
        else:
            registry.write_value(
                EXPLORER_COMMAND_KEY_PATH,
                EXPLORER_DELEGATE_EXECUTE_VALUE,
                "{wrong}",
            )
        assert service.unregister().state is ExplorerRegistrationState.ABSENT
        assert service.unregister().state is ExplorerRegistrationState.ABSENT
        assert registry.values == {}


def test_unregister_preserves_unrelated_verbs_and_clsids(tmp_path: Path) -> None:
    registry = MemoryRegistry()
    executable, _helper = _package(tmp_path)
    unrelated = {
        (r"Software\Classes\Directory\shell\other", ""): "other",
        (r"Software\Classes\CLSID\{other}\LocalServer32", ""): "other.exe",
    }
    registry.values.update(unrelated)
    _service(registry, executable).register_current_executable()

    _service(registry, executable).unregister()

    assert registry.values == unrelated
    assert EXPLORER_CLSID_KEY_PATH not in {key[0] for key in registry.values}


def test_registry_write_failure_rolls_back_without_misleading_current_state(tmp_path: Path) -> None:
    registry = MemoryRegistry()
    executable, _helper = _package(tmp_path)
    _write_legacy(registry, executable)
    before = dict(registry.values)
    registry.events.clear()
    registry.fail_after_writes = 1

    with pytest.raises(ExplorerIntegrationError):
        _service(registry, executable).register_current_executable()

    assert registry.values == before
    assert _service(registry, executable).inspect().state is ExplorerRegistrationState.LEGACY


def test_notification_happens_after_complete_registration_and_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[str] = []

    def notify() -> None:
        events.append("notify")

    monkeypatch.setattr(explorer_integration, "notify_shell_association_changed", notify)
    registry = MemoryRegistry()
    executable, _helper = _package(tmp_path)
    service = _service(registry, executable)

    service.register_current_executable()
    assert events == ["notify"]
    assert registry.events[:6] == [
        "write",
        "write",
        "write",
        "write",
        "delete-value",
        "write",
    ]
    events.clear()
    registry.events.clear()

    service.unregister()

    assert events == ["notify"]
    assert registry.events == ["delete-key", "delete-key", "delete-key"]


def test_notification_failure_rolls_back_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_notification() -> None:
        raise OSError("Shell32 unavailable")

    monkeypatch.setattr(explorer_integration, "notify_shell_association_changed", fail_notification)
    registry = MemoryRegistry()
    executable, _helper = _package(tmp_path)

    with pytest.raises(ExplorerIntegrationError):
        _service(registry, executable).register_current_executable()

    assert _service(registry, executable).inspect().state is ExplorerRegistrationState.ABSENT


def test_shell_notification_uses_safe_windows_api_signature(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeScalar:
        def __init__(self, value: int) -> None:
            self.value = value

    class FakeVoidPointer:
        pass

    class FakeFunction:
        def __init__(self) -> None:
            self.argtypes: object = None
            self.restype: object = "unset"
            self.calls: list[tuple[object, object, object, object]] = []

        def __call__(self, *args: object) -> None:
            assert len(args) == 4
            self.calls.append((args[0], args[1], args[2], args[3]))

    notify: FakeFunction = FakeFunction()
    dll_calls: list[tuple[str, bool]] = []

    def fake_windll(name: str, *, use_last_error: bool) -> SimpleNamespace:
        dll_calls.append((name, use_last_error))
        return SimpleNamespace(SHChangeNotify=notify)

    fake_ctypes = SimpleNamespace(
        WinDLL=fake_windll,
        c_long=FakeScalar,
        c_uint=FakeScalar,
        c_void_p=FakeVoidPointer,
    )
    monkeypatch.setattr(explorer_integration.os, "name", "nt")
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)

    notify_shell_association_changed()

    assert dll_calls == [("shell32", True)]
    assert notify.argtypes == [FakeScalar, FakeScalar, FakeVoidPointer, FakeVoidPointer]
    assert notify.restype is None
    assert len(notify.calls) == 1
    event, flags, item1, item2 = notify.calls[0]
    assert isinstance(event, FakeScalar) and event.value == 0x08000000
    assert isinstance(flags, FakeScalar) and flags.value == 0x1003
    assert item1 is None and item2 is None


def test_non_windows_or_source_context_is_unsupported() -> None:
    service = ExplorerIntegrationService(MemoryRegistry(), executable_path_provider=lambda: None)

    registration = service.inspect()

    assert registration.state is ExplorerRegistrationState.UNSUPPORTED
    assert not service.supported
