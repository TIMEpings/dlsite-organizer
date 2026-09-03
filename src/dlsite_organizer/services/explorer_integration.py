"""Per-user Windows Explorer context-menu integration."""

from __future__ import annotations

import logging
import ntpath
import os
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)

EXPLORER_VERB = "dlsite-organizer"
EXPLORER_MENU_LABEL = "使用 DLsite Organizer 重命名"
EXPLORER_KEY_PATH = rf"Software\Classes\Directory\shell\{EXPLORER_VERB}"
EXPLORER_COMMAND_KEY_PATH = rf"{EXPLORER_KEY_PATH}\command"
EXPLORER_MULTI_SELECT_MODEL_VALUE = "MultiSelectModel"
EXPLORER_MULTI_SELECT_MODEL = "Player"
EXPLORER_LEGACY_MULTI_SELECT_MODEL = "Single"
EXPLORER_ICON_VALUE = "Icon"
EXPLORER_DELEGATE_EXECUTE_VALUE = "DelegateExecute"
EXPLORER_CLSID = "{031255AF-20D8-4EE9-AC4C-D8CE7D3E154B}"
EXPLORER_DELEGATE_EXECUTE_CLSID = EXPLORER_CLSID
EXPLORER_CLSID_KEY_PATH = rf"Software\Classes\CLSID\{EXPLORER_CLSID}"
EXPLORER_LOCAL_SERVER_KEY_PATH = rf"{EXPLORER_CLSID_KEY_PATH}\LocalServer32"
EXPLORER_HELPER_FILENAME = "dlsite-shell-helper.exe"

# SHCNE_ASSOCCHANGED tells the Windows Shell that registration/association
# data changed. SHCNF_IDLIST is required for this event even though both
# item arguments are unused and must be NULL. SHCNF_DWORD | SHCNF_FLUSH is the
# documented association-registration pattern for this no-item event; the
# flush waits for delivery before returning, so a subsequent Explorer action
# can observe the new command without an arbitrary sleep or an Explorer
# restart.
_SHCNE_ASSOCCHANGED = 0x08000000
_SHCNF_DWORD = 0x0003
_SHCNF_FLUSH = 0x1000


class ExplorerRegistrationState(StrEnum):
    """Typed result of inspecting the application-owned Explorer verb."""

    CURRENT = "current"
    LEGACY = "legacy"
    STALE = "stale"
    ABSENT = "absent"
    UNSUPPORTED = "unsupported"
    ERROR = "error"

    # Compatibility aliases retained for callers from the v1.1 service API.
    REGISTERED_CURRENT = CURRENT
    REGISTERED_STALE = STALE
    NOT_REGISTERED = ABSENT


@dataclass(frozen=True, slots=True)
class ExplorerRegistration:
    """A UI-independent snapshot of the current registration state."""

    state: ExplorerRegistrationState
    current_executable: Path | None = None
    registered_executable: Path | None = None
    command: str | None = None
    multi_select_model: str | None = None
    icon: str | None = None
    error: str | None = None
    current_helper: Path | None = None
    registered_helper: Path | None = None
    delegate_execute: str | None = None
    local_server: str | None = None


class ExplorerIntegrationError(RuntimeError):
    """A registry update could not be completed safely."""

    user_message = "无法更新资源管理器右键菜单。"


class RegistryBackend(Protocol):
    """Minimal registry seam used by the service and its unit tests."""

    def read_value(self, key_path: str, value_name: str = "") -> str | None:
        """Read one string value from HKCU-relative ``key_path``."""
        ...

    def write_value(self, key_path: str, value_name: str, value: str) -> None:
        """Write one string value to HKCU-relative ``key_path``."""
        ...

    def delete_value(self, key_path: str, value_name: str = "") -> None:
        """Delete one value, remaining idempotent when it is absent."""
        ...

    def delete_key(self, key_path: str) -> None:
        """Delete one key tree, remaining idempotent when it is absent."""
        ...


class WinRegistryBackend:
    """Lazy ``winreg`` adapter so non-Windows startup never imports it."""

    def read_value(self, key_path: str, value_name: str = "") -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_READ) as key:
                value, _value_type = winreg.QueryValueEx(key, value_name)
        except FileNotFoundError:
            return None
        return str(value)

    def write_value(self, key_path: str, value_name: str, value: str) -> None:
        import winreg

        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            key_path,
            0,
            winreg.KEY_WRITE,
        ) as key:
            winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, value)

    def delete_value(self, key_path: str, value_name: str = "") -> None:
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                key_path,
                0,
                winreg.KEY_SET_VALUE,
            ) as key:
                try:
                    winreg.DeleteValue(key, value_name)
                except FileNotFoundError:
                    return
        except FileNotFoundError:
            return

    def delete_key(self, key_path: str) -> None:
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                key_path,
                0,
                winreg.KEY_READ | winreg.KEY_WRITE,
            ) as key:
                children: list[str] = []
                index = 0
                while True:
                    try:
                        children.append(winreg.EnumKey(key, index))
                    except OSError:
                        break
                    index += 1
            for child in children:
                self.delete_key(rf"{key_path}\{child}")
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
        except FileNotFoundError:
            return


ExecutablePathProvider = Callable[[], Path | None]
HelperPathProvider = Callable[[Path], Path | None]
RegularFileChecker = Callable[[Path], bool]


class ExplorerIntegrationService:
    """Inspect and update only this application's HKCU Explorer verb."""

    def __init__(
        self,
        backend: RegistryBackend | None = None,
        *,
        executable_path_provider: ExecutablePathProvider | None = None,
        helper_path_provider: HelperPathProvider | None = None,
        regular_file_checker: RegularFileChecker | None = None,
    ) -> None:
        self._backend = backend if backend is not None else _default_backend()
        self._executable_path_provider = executable_path_provider or current_executable_path
        self._helper_path_provider = helper_path_provider or sibling_helper_path
        self._regular_file_checker = regular_file_checker or Path.is_file

    @property
    def supported(self) -> bool:
        """Whether this process can register a packaged executable for Explorer."""
        return self._backend is not None and self._current_executable() is not None

    def inspect(self) -> ExplorerRegistration:
        """Read the owned keys and classify the registration schema and paths."""
        current = self._current_executable()
        if self._backend is None or current is None:
            return ExplorerRegistration(
                state=ExplorerRegistrationState.UNSUPPORTED,
                current_executable=current,
            )
        current_helper = self._current_helper(current)
        try:
            command = self._backend.read_value(EXPLORER_COMMAND_KEY_PATH)
            delegate_execute = self._backend.read_value(
                EXPLORER_COMMAND_KEY_PATH,
                EXPLORER_DELEGATE_EXECUTE_VALUE,
            )
            multi_select_model = self._backend.read_value(
                EXPLORER_KEY_PATH,
                EXPLORER_MULTI_SELECT_MODEL_VALUE,
            )
            icon = self._backend.read_value(EXPLORER_KEY_PATH, EXPLORER_ICON_VALUE)
            label = self._backend.read_value(EXPLORER_KEY_PATH)
            local_server = self._backend.read_value(EXPLORER_LOCAL_SERVER_KEY_PATH)
        except Exception:
            logger.exception("Could not inspect Explorer integration registry state")
            return ExplorerRegistration(
                state=ExplorerRegistrationState.ERROR,
                current_executable=current,
                current_helper=current_helper,
                error="无法读取资源管理器右键菜单。",
            )

        known_values = (
            label,
            icon,
            multi_select_model,
            command,
            delegate_execute,
            local_server,
        )
        if not any(value is not None for value in known_values):
            return ExplorerRegistration(
                state=ExplorerRegistrationState.ABSENT,
                current_executable=current,
                current_helper=current_helper,
            )

        registered_executable, arguments_valid = _parse_command(command)
        registered_icon_executable, icon_valid = _parse_icon_value(icon)
        registered_helper, local_server_valid = _parse_local_server(local_server)
        is_legacy = (
            arguments_valid
            and delegate_execute is None
            and multi_select_model in {None, EXPLORER_LEGACY_MULTI_SELECT_MODEL}
            and (label is None or label == EXPLORER_MENU_LABEL)
        )
        is_current = (
            label == EXPLORER_MENU_LABEL
            and icon_valid
            and registered_icon_executable is not None
            and _same_windows_path(registered_icon_executable, current)
            and multi_select_model == EXPLORER_MULTI_SELECT_MODEL
            and command is None
            and delegate_execute is not None
            and _same_text(delegate_execute, EXPLORER_CLSID)
            and local_server_valid
            and registered_helper is not None
            and current_helper is not None
            and _same_windows_path(registered_helper, current_helper)
            and self._is_regular_file(current_helper)
        )
        if is_current:
            state = ExplorerRegistrationState.CURRENT
        elif is_legacy:
            state = ExplorerRegistrationState.LEGACY
        else:
            state = ExplorerRegistrationState.STALE
        return ExplorerRegistration(
            state=state,
            current_executable=current,
            current_helper=current_helper,
            registered_executable=_path_or_none(
                registered_icon_executable or registered_executable
            ),
            registered_helper=_path_or_none(registered_helper),
            command=command,
            delegate_execute=delegate_execute,
            local_server=local_server,
            multi_select_model=multi_select_model,
            icon=icon,
        )

    def register_current_executable(self) -> ExplorerRegistration:
        """Create or migrate this application's HKCU COM-backed Explorer verb."""
        current = self._current_executable()
        if self._backend is None or current is None:
            return ExplorerRegistration(
                state=ExplorerRegistrationState.UNSUPPORTED,
                current_executable=current,
            )

        current_helper = self._current_helper(current)
        if current_helper is None or not self._is_regular_file(current_helper):
            raise ExplorerIntegrationError(
                f"{ExplorerIntegrationError.user_message} "
                f"找不到同目录的 {EXPLORER_HELPER_FILENAME}。"
            )

        previous: dict[tuple[str, str], str | None] | None = None
        try:
            previous = self._capture_owned_values()
            local_server = build_local_server_command(current_helper)
            # The COM server is made activatable before the verb is switched to
            # DelegateExecute.  MultiSelectModel is deliberately written last,
            # after every value needed for activation is complete.
            self._backend.write_value(EXPLORER_LOCAL_SERVER_KEY_PATH, "", local_server)
            self._backend.write_value(EXPLORER_KEY_PATH, "", EXPLORER_MENU_LABEL)
            self._backend.write_value(
                EXPLORER_KEY_PATH,
                EXPLORER_ICON_VALUE,
                build_explorer_icon_value(current),
            )
            self._backend.write_value(
                EXPLORER_COMMAND_KEY_PATH,
                EXPLORER_DELEGATE_EXECUTE_VALUE,
                EXPLORER_CLSID,
            )
            self._backend.delete_value(EXPLORER_COMMAND_KEY_PATH, "")
            self._backend.write_value(
                EXPLORER_KEY_PATH,
                EXPLORER_MULTI_SELECT_MODEL_VALUE,
                EXPLORER_MULTI_SELECT_MODEL,
            )
            registration = self.inspect()
            if registration.state is not ExplorerRegistrationState.CURRENT:
                raise ExplorerIntegrationError
            notify_shell_association_changed()
        except Exception as exc:
            logger.exception("Could not register Explorer integration")
            if previous is not None:
                self._restore_owned_values(previous)
            if isinstance(exc, ExplorerIntegrationError):
                raise
            raise ExplorerIntegrationError from exc
        return registration

    def register(self) -> ExplorerRegistration:
        """Short alias for the Settings-facing register/update operation."""
        return self.register_current_executable()

    def unregister(self) -> ExplorerRegistration:
        """Remove only this application's verb and COM class registration."""
        current = self._current_executable()
        if self._backend is None or current is None:
            return ExplorerRegistration(
                state=ExplorerRegistrationState.UNSUPPORTED,
                current_executable=current,
            )
        try:
            for key_path in (
                EXPLORER_COMMAND_KEY_PATH,
                EXPLORER_KEY_PATH,
                EXPLORER_LOCAL_SERVER_KEY_PATH,
                EXPLORER_CLSID_KEY_PATH,
            ):
                try:
                    self._backend.delete_key(key_path)
                except FileNotFoundError:
                    # Deleting an already absent application-owned key is
                    # idempotent; still attempt the parent verb key.
                    continue
            notify_shell_association_changed()
        except Exception as exc:
            logger.exception("Could not unregister Explorer integration")
            raise ExplorerIntegrationError from exc
        return self.inspect()

    def remove(self) -> ExplorerRegistration:
        """Short alias for the Settings-facing remove operation."""
        return self.unregister()

    def _current_executable(self) -> Path | None:
        try:
            return self._executable_path_provider()
        except Exception:
            logger.exception("Could not determine current packaged executable path")
            return None

    def _current_helper(self, executable: Path) -> Path | None:
        try:
            executable_path = Path(_absolute_executable_path(executable))
            helper = self._helper_path_provider(executable_path)
        except Exception:
            logger.exception("Could not determine the packaged Explorer helper path")
            return None
        if helper is None:
            return None
        return Path(_absolute_executable_path(helper))

    def _is_regular_file(self, path: Path) -> bool:
        try:
            return bool(self._regular_file_checker(path))
        except (OSError, ValueError):
            return False

    def _capture_owned_values(self) -> dict[tuple[str, str], str | None]:
        assert self._backend is not None
        keys = (
            (EXPLORER_KEY_PATH, ""),
            (EXPLORER_KEY_PATH, EXPLORER_ICON_VALUE),
            (EXPLORER_KEY_PATH, EXPLORER_MULTI_SELECT_MODEL_VALUE),
            (EXPLORER_COMMAND_KEY_PATH, ""),
            (EXPLORER_COMMAND_KEY_PATH, EXPLORER_DELEGATE_EXECUTE_VALUE),
            (EXPLORER_LOCAL_SERVER_KEY_PATH, ""),
        )
        return {key: self._backend.read_value(*key) for key in keys}

    def _restore_owned_values(self, previous: dict[tuple[str, str], str | None]) -> None:
        """Best-effort rollback; an incomplete result is classified STALE on reread."""
        assert self._backend is not None
        try:
            for (key_path, value_name), value in previous.items():
                if value is None:
                    self._backend.delete_value(key_path, value_name)
                else:
                    self._backend.write_value(key_path, value_name, value)
        except Exception:
            logger.exception("Could not fully roll back Explorer registration")


def is_frozen() -> bool:
    """Return whether the current process is a PyInstaller-style frozen app."""
    return bool(getattr(sys, "frozen", False))


def current_executable_path() -> Path | None:
    """Return ``sys.executable`` only for a packaged application."""
    if not is_frozen():
        return None
    executable = Path(sys.executable)
    if not executable.is_absolute():
        executable = Path.cwd() / executable
    return executable.absolute()


def build_quick_rename_command(executable_path: Path | str) -> str:
    """Build the direct single-selection Explorer command without a shell trampoline.

    ``%1`` is the Shell selection placeholder.  With ``MultiSelectModel`` set
    to ``Single`` on the owning verb, Explorer offers the command for one
    selected directory.  Keeping the placeholder quoted preserves paths
    containing spaces without introducing a command interpreter or an
    inter-process aggregation layer.
    """
    executable = _absolute_executable_path(executable_path)
    # Let the standard Windows argument formatter quote the executable path;
    # the final token is an Explorer placeholder and must remain visibly
    # quoted even though it has no spaces before Explorer expands it.
    executable_token = subprocess.list2cmdline([executable])
    if not executable_token.startswith('"'):
        executable_token = f'"{executable_token}"'
    return f'{executable_token} --quick-rename "%1"'


def build_local_server_command(helper_path: Path | str) -> str:
    """Build the exact quoted LocalServer32 command for the sibling helper."""
    return _quote_windows_executable(helper_path)


def sibling_helper_path(executable_path: Path | str) -> Path:
    """Return the production helper path beside the packaged application."""
    executable = Path(_absolute_executable_path(executable_path))
    return executable.parent / EXPLORER_HELPER_FILENAME


def build_explorer_icon_value(executable_path: Path | str) -> str:
    """Build the registry ``Icon`` value from the current executable path."""
    executable = _absolute_executable_path(executable_path)
    executable_token = subprocess.list2cmdline([executable])
    if not executable_token.startswith('"'):
        executable_token = f'"{executable_token}"'
    return f"{executable_token},0"


def _default_backend() -> RegistryBackend | None:
    if os.name != "nt":
        return None
    return WinRegistryBackend()


def notify_shell_association_changed() -> None:
    """Tell Windows Explorer to refresh its cached association state."""
    if os.name != "nt":
        return

    import ctypes

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    notify = shell32.SHChangeNotify
    notify.argtypes = [
        ctypes.c_long,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_void_p,
    ]
    notify.restype = None
    notify(
        ctypes.c_long(_SHCNE_ASSOCCHANGED),
        ctypes.c_uint(_SHCNF_DWORD | _SHCNF_FLUSH),
        None,
        None,
    )


def _absolute_executable_path(path: Path | str) -> str:
    raw = os.fspath(path)
    if ntpath.isabs(raw):
        return ntpath.normpath(raw)
    return str(Path(raw).absolute())


def _quote_windows_executable(path: Path | str) -> str:
    executable = _absolute_executable_path(path)
    executable_token = subprocess.list2cmdline([executable])
    if not executable_token.startswith('"'):
        executable_token = f'"{executable_token}"'
    return executable_token


def _path_or_none(path: Path | str | None) -> Path | None:
    return None if path is None else Path(path)


def _same_text(left: str, right: str) -> bool:
    return left.strip().casefold() == right.casefold()


def _same_windows_path(left: Path | str, right: Path | str) -> bool:
    return _windows_path_key(left) == _windows_path_key(right)


def _windows_path_key(path: Path | str) -> str:
    raw = os.fspath(path)
    if not ntpath.isabs(raw):
        raw = ntpath.abspath(raw)
    raw = ntpath.normpath(raw)
    if os.name == "nt":
        raw = _get_long_path_name(raw)
    return ntpath.normcase(raw).casefold()


def _get_long_path_name(path: str) -> str:
    """Expand 8.3 spelling on Windows without resolving symlinks."""
    try:
        import ctypes

        buffer_length = 32768
        buffer = ctypes.create_unicode_buffer(buffer_length)
        length = ctypes.windll.kernel32.GetLongPathNameW(path, buffer, buffer_length)
        if 0 < length < buffer_length:
            return buffer.value
    except Exception:
        logger.debug("Could not expand Windows long path spelling", exc_info=True)
    return path


def _parse_command(command: str | None) -> tuple[str | None, bool]:
    """Parse the command shape that this service writes, without shell evaluation."""
    if not command:
        return None, False
    value = command.strip()
    if not value.startswith('"'):
        return None, False
    closing_quote = value.find('"', 1)
    if closing_quote <= 1:
        return None, False
    executable = value[1:closing_quote]
    arguments = value[closing_quote + 1 :].strip()
    return executable, arguments == '--quick-rename "%1"'


def _parse_local_server(value: str | None) -> tuple[str | None, bool]:
    """Parse the quoted, argument-free LocalServer32 command this service writes."""
    if not value:
        return None, False
    candidate = value.strip()
    if not candidate.startswith('"'):
        return None, False
    closing_quote = candidate.find('"', 1)
    if closing_quote <= 1:
        return None, False
    executable = candidate[1:closing_quote]
    return executable, candidate[closing_quote + 1 :].strip() == ""


def _parse_icon_value(value: str | None) -> tuple[str | None, bool]:
    """Parse the exact quoted executable/index shape written by this service."""
    if not value:
        return None, False
    candidate = value.strip()
    if not candidate.startswith('"'):
        return None, False
    closing_quote = candidate.find('"', 1)
    if closing_quote <= 1:
        return None, False
    executable = candidate[1:closing_quote]
    return executable, candidate[closing_quote + 1 :].strip() == ",0"
