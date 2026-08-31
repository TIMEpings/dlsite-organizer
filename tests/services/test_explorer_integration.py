from pathlib import Path

import pytest

from dlsite_organizer.services.explorer_integration import (
    EXPLORER_COMMAND_KEY_PATH,
    EXPLORER_KEY_PATH,
    EXPLORER_MENU_LABEL,
    EXPLORER_MULTI_SELECT_MODEL,
    EXPLORER_MULTI_SELECT_MODEL_VALUE,
    ExplorerIntegrationError,
    ExplorerIntegrationService,
    ExplorerRegistrationState,
    build_quick_rename_command,
)


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
