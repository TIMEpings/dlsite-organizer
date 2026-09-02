import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from dlsite_organizer.services import explorer_integration
from dlsite_organizer.services.explorer_integration import (
    EXPLORER_COMMAND_KEY_PATH,
    EXPLORER_ICON_VALUE,
    EXPLORER_KEY_PATH,
    EXPLORER_MENU_LABEL,
    EXPLORER_MULTI_SELECT_MODEL,
    EXPLORER_MULTI_SELECT_MODEL_VALUE,
    ExplorerIntegrationError,
    ExplorerIntegrationService,
    ExplorerRegistrationState,
    build_explorer_icon_value,
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

    def read_value(self, key_path: str, value_name: str = "") -> str | None:
        return self.values.get((key_path, value_name))

    def write_value(self, key_path: str, value_name: str, value: str) -> None:
        self.values[(key_path, value_name)] = value

    def delete_key(self, key_path: str) -> None:
        matching = [key for key in self.values if key[0] == key_path]
        if not matching:
            raise FileNotFoundError(key_path)
        for key in matching:
            del self.values[key]


def _service(registry: MemoryRegistry, executable: str) -> ExplorerIntegrationService:
    return ExplorerIntegrationService(
        registry,
        executable_path_provider=lambda: Path(executable),
    )


def test_command_uses_direct_exe_and_safe_windows_quoting() -> None:
    command = build_quick_rename_command(
        r"C:\Program Files\DLsite Organizer\A&B (日本語)\dlsite-organizer.exe"
    )

    assert command == (
        r'"C:\Program Files\DLsite Organizer\A&B (日本語)\dlsite-organizer.exe" '
        r'--quick-rename "%1"'
    )
    assert "cmd.exe" not in command
    assert "powershell" not in command.casefold()


def test_register_inspect_stale_update_and_idempotent_remove() -> None:
    registry = MemoryRegistry()
    current = r"C:\Apps\DLsite Organizer\dlsite-organizer.exe"
    service = _service(registry, current)

    assert service.inspect().state is ExplorerRegistrationState.NOT_REGISTERED

    registered = service.register_current_executable()
    assert registered.state is ExplorerRegistrationState.REGISTERED_CURRENT
    assert EXPLORER_MULTI_SELECT_MODEL == "Single"
    assert registered.multi_select_model == "Single"
    assert registry.values[(EXPLORER_KEY_PATH, "")] == EXPLORER_MENU_LABEL
    assert (
        registry.values[(EXPLORER_KEY_PATH, EXPLORER_MULTI_SELECT_MODEL_VALUE)]
        == EXPLORER_MULTI_SELECT_MODEL
    )
    assert registry.values[(EXPLORER_KEY_PATH, EXPLORER_ICON_VALUE)] == (
        build_explorer_icon_value(current)
    )
    assert registry.values[(EXPLORER_COMMAND_KEY_PATH, "")] == build_quick_rename_command(current)

    moved_service = _service(registry, r"D:\Apps\DLsite Organizer\dlsite-organizer.exe")
    stale = moved_service.inspect()
    assert stale.state is ExplorerRegistrationState.REGISTERED_STALE
    assert stale.registered_executable == Path(current)

    updated = moved_service.register_current_executable()
    assert updated.state is ExplorerRegistrationState.REGISTERED_CURRENT
    assert updated.registered_executable == Path(r"D:\Apps\DLsite Organizer\dlsite-organizer.exe")

    removed = moved_service.unregister()
    assert removed.state is ExplorerRegistrationState.NOT_REGISTERED
    assert registry.values == {}
    assert moved_service.unregister().state is ExplorerRegistrationState.NOT_REGISTERED


def test_register_notifies_shell_once_after_complete_registry_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[None] = []
    monkeypatch.setattr(
        explorer_integration,
        "notify_shell_association_changed",
        lambda: calls.append(None),
    )

    registry = MemoryRegistry()
    _service(registry, r"C:\Apps\dlsite-organizer.exe").register_current_executable()

    assert len(calls) == 1


def test_update_notifies_shell_once_for_the_reregistration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[None] = []
    monkeypatch.setattr(
        explorer_integration,
        "notify_shell_association_changed",
        lambda: calls.append(None),
    )
    registry = MemoryRegistry()
    _service(registry, r"C:\Apps\A\dlsite-organizer.exe").register_current_executable()
    calls.clear()

    _service(registry, r"C:\Apps\B\dlsite-organizer.exe").register_current_executable()

    assert len(calls) == 1


def test_unregister_notifies_shell_once_after_registry_removal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[None] = []
    monkeypatch.setattr(
        explorer_integration,
        "notify_shell_association_changed",
        lambda: calls.append(None),
    )
    registry = MemoryRegistry()
    service = _service(registry, r"C:\Apps\dlsite-organizer.exe")
    service.register_current_executable()
    calls.clear()

    service.unregister()

    assert len(calls) == 1


def test_status_check_does_not_notify_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[None] = []
    monkeypatch.setattr(
        explorer_integration,
        "notify_shell_association_changed",
        lambda: calls.append(None),
    )

    _service(MemoryRegistry(), r"C:\Apps\dlsite-organizer.exe").inspect()

    assert calls == []


def test_registry_failure_does_not_notify_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[None] = []
    monkeypatch.setattr(
        explorer_integration,
        "notify_shell_association_changed",
        lambda: calls.append(None),
    )

    class DeniedRegistry(MemoryRegistry):
        def write_value(self, key_path: str, value_name: str, value: str) -> None:
            raise PermissionError("denied")

    with pytest.raises(ExplorerIntegrationError):
        _service(DeniedRegistry(), r"C:\Apps\dlsite-organizer.exe").register_current_executable()

    assert calls == []


def test_registry_write_and_removal_complete_before_shell_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class RecordingRegistry(MemoryRegistry):
        def write_value(self, key_path: str, value_name: str, value: str) -> None:
            super().write_value(key_path, value_name, value)
            events.append("write")

        def delete_key(self, key_path: str) -> None:
            super().delete_key(key_path)
            events.append("delete")

    monkeypatch.setattr(
        explorer_integration,
        "notify_shell_association_changed",
        lambda: events.append("notify"),
    )
    registry = RecordingRegistry()
    service = _service(registry, r"C:\Apps\dlsite-organizer.exe")

    service.register_current_executable()
    assert events == ["write", "write", "write", "write", "notify"]

    events.clear()
    service.unregister()
    assert events == ["delete", "delete", "notify"]


def test_shell_notification_failure_is_reported_after_registry_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_notification() -> None:
        raise OSError("Shell32 unavailable")

    monkeypatch.setattr(
        explorer_integration,
        "notify_shell_association_changed",
        fail_notification,
    )
    registry = MemoryRegistry()

    with pytest.raises(ExplorerIntegrationError):
        _service(registry, r"C:\Apps\dlsite-organizer.exe").register_current_executable()

    assert registry.values[(EXPLORER_COMMAND_KEY_PATH, "")] == (
        build_quick_rename_command(r"C:\Apps\dlsite-organizer.exe")
    )


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

    notify = FakeFunction()
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
    call = notify.calls[0]
    event = call[0]
    flags = call[1]
    item1 = call[2]
    item2 = call[3]
    assert isinstance(event, FakeScalar)
    assert event.value == 0x08000000
    assert isinstance(flags, FakeScalar)
    assert flags.value == 0x1003
    assert item1 is None
    assert item2 is None


def test_path_comparison_accepts_case_variation() -> None:
    registry = MemoryRegistry()
    current = r"C:\Program Files\DLsite Organizer\dlsite-organizer.exe"
    registry.write_value(
        EXPLORER_COMMAND_KEY_PATH,
        "",
        r'"c:\PROGRAM FILES\DLsite Organizer\dlsite-organizer.exe" --quick-rename "%1"',
    )
    registry.write_value(
        EXPLORER_KEY_PATH,
        EXPLORER_MULTI_SELECT_MODEL_VALUE,
        EXPLORER_MULTI_SELECT_MODEL,
    )
    registry.write_value(EXPLORER_KEY_PATH, EXPLORER_ICON_VALUE, build_explorer_icon_value(current))

    assert (
        _service(registry, current).inspect().state
        is ExplorerRegistrationState.REGISTERED_CURRENT
    )


def test_remove_cleans_parent_verb_when_command_key_is_already_missing() -> None:
    registry = MemoryRegistry()
    registry.write_value(EXPLORER_KEY_PATH, "", EXPLORER_MENU_LABEL)

    registration = _service(registry, r"C:\Apps\dlsite-organizer.exe").unregister()

    assert registration.state is ExplorerRegistrationState.NOT_REGISTERED
    assert registry.values == {}


def test_malformed_owned_command_is_stale() -> None:
    registry = MemoryRegistry()
    registry.write_value(EXPLORER_COMMAND_KEY_PATH, "", r'"C:\old.exe" --wrong "%1"')

    assert (
        _service(registry, r"C:\old.exe").inspect().state
        is ExplorerRegistrationState.REGISTERED_STALE
    )


def test_missing_or_wrong_multi_select_model_is_stale() -> None:
    registry = MemoryRegistry()
    registry.write_value(
        EXPLORER_COMMAND_KEY_PATH,
        "",
        r'"C:\old.exe" --quick-rename "%1"',
    )

    assert (
        _service(registry, r"C:\old.exe").inspect().state
        is ExplorerRegistrationState.REGISTERED_STALE
    )

    registry.write_value(EXPLORER_KEY_PATH, EXPLORER_MULTI_SELECT_MODEL_VALUE, "Player")
    assert (
        _service(registry, r"C:\old.exe").inspect().state
        is ExplorerRegistrationState.REGISTERED_STALE
    )

    registry.write_value(EXPLORER_KEY_PATH, EXPLORER_MULTI_SELECT_MODEL_VALUE, "Document")
    assert (
        _service(registry, r"C:\old.exe").inspect().state
        is ExplorerRegistrationState.REGISTERED_STALE
    )


def test_missing_or_stale_icon_value_is_stale() -> None:
    registry = MemoryRegistry()
    current = r"C:\Apps\dlsite-organizer.exe"
    registry.write_value(
        EXPLORER_COMMAND_KEY_PATH,
        "",
        build_quick_rename_command(current),
    )
    registry.write_value(
        EXPLORER_KEY_PATH,
        EXPLORER_MULTI_SELECT_MODEL_VALUE,
        EXPLORER_MULTI_SELECT_MODEL,
    )

    assert _service(registry, current).inspect().state is ExplorerRegistrationState.REGISTERED_STALE

    registry.write_value(
        EXPLORER_KEY_PATH,
        EXPLORER_ICON_VALUE,
        build_explorer_icon_value(current),
    )
    assert (
        _service(registry, current).inspect().state
        is ExplorerRegistrationState.REGISTERED_CURRENT
    )


def test_registry_read_failure_is_typed_error() -> None:
    class BrokenRegistry(MemoryRegistry):
        def read_value(self, key_path: str, value_name: str = "") -> str | None:
            raise OSError("registry unavailable")

    registration = _service(
        BrokenRegistry(), r"C:\Apps\dlsite-organizer.exe"
    ).inspect()

    assert registration.state is ExplorerRegistrationState.ERROR
    assert registration.error == "无法读取资源管理器右键菜单。"


def test_permission_failure_is_reported_as_user_safe_error() -> None:
    class DeniedRegistry(MemoryRegistry):
        def write_value(self, key_path: str, value_name: str, value: str) -> None:
            raise PermissionError("denied")

    with pytest.raises(ExplorerIntegrationError) as caught:
        _service(DeniedRegistry(), r"C:\Apps\dlsite-organizer.exe").register_current_executable()

    assert caught.value.user_message == "无法更新资源管理器右键菜单。"


def test_missing_executable_context_is_unsupported() -> None:
    service = ExplorerIntegrationService(MemoryRegistry(), executable_path_provider=lambda: None)

    registration = service.inspect()

    assert registration.state is ExplorerRegistrationState.UNSUPPORTED
    assert not service.supported
