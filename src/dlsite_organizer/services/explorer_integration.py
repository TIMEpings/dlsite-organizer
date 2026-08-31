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
EXPLORER_MULTI_SELECT_MODEL = "Single"


class ExplorerRegistrationState(StrEnum):
    """Typed result of inspecting the application-owned Explorer verb."""

    NOT_REGISTERED = "not_registered"
    REGISTERED_CURRENT = "registered_current"
    REGISTERED_STALE = "registered_stale"
    UNSUPPORTED = "unsupported"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ExplorerRegistration:
    """A UI-independent snapshot of the current registration state."""

    state: ExplorerRegistrationState
    current_executable: Path | None = None
    registered_executable: Path | None = None
    command: str | None = None
    multi_select_model: str | None = None
    error: str | None = None


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

    def delete_key(self, key_path: str) -> None:
        """Delete one key, raising ``FileNotFoundError`` when it is absent."""
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

    def delete_key(self, key_path: str) -> None:
        import winreg

        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key_path)
        except FileNotFoundError:
            return


ExecutablePathProvider = Callable[[], Path | None]


class ExplorerIntegrationService:
    """Inspect and update only this application's HKCU Explorer verb."""

    def __init__(
        self,
        backend: RegistryBackend | None = None,
        *,
        executable_path_provider: ExecutablePathProvider | None = None,
    ) -> None:
        self._backend = backend if backend is not None else _default_backend()
        self._executable_path_provider = executable_path_provider or current_executable_path

    @property
    def supported(self) -> bool:
        """Whether this process can register a packaged executable for Explorer."""
        return self._backend is not None and self._current_executable() is not None

    def inspect(self) -> ExplorerRegistration:
        """Read the application-owned command and classify its path."""
        current = self._current_executable()
        if self._backend is None or current is None:
            return ExplorerRegistration(
                state=ExplorerRegistrationState.UNSUPPORTED,
                current_executable=current,
            )
        try:
            command = self._backend.read_value(EXPLORER_COMMAND_KEY_PATH)
            multi_select_model = self._backend.read_value(
                EXPLORER_KEY_PATH,
                EXPLORER_MULTI_SELECT_MODEL_VALUE,
            )
        except Exception:
            logger.exception("Could not inspect Explorer integration registry state")
            return ExplorerRegistration(
                state=ExplorerRegistrationState.ERROR,
                current_executable=current,
                error="无法读取资源管理器右键菜单。",
            )
        if not command:
            return ExplorerRegistration(
                state=ExplorerRegistrationState.NOT_REGISTERED,
                current_executable=current,
            )

        registered_executable, arguments_valid = _parse_command(command)
        is_current = (
            arguments_valid
            and multi_select_model == EXPLORER_MULTI_SELECT_MODEL
            and registered_executable is not None
            and _same_windows_path(registered_executable, current)
        )
        return ExplorerRegistration(
            state=(
                ExplorerRegistrationState.REGISTERED_CURRENT
                if is_current
                else ExplorerRegistrationState.REGISTERED_STALE
            ),
            current_executable=current,
            registered_executable=(
                Path(registered_executable) if registered_executable is not None else None
            ),
            command=command,
            multi_select_model=multi_select_model,
        )

    def register_current_executable(self) -> ExplorerRegistration:
        """Create or replace this application's HKCU verb with the frozen exe path."""
        current = self._current_executable()
        if self._backend is None or current is None:
            return ExplorerRegistration(
                state=ExplorerRegistrationState.UNSUPPORTED,
                current_executable=current,
            )
        command = build_quick_rename_command(current)
        try:
            self._backend.write_value(EXPLORER_KEY_PATH, "", EXPLORER_MENU_LABEL)
            self._backend.write_value(
                EXPLORER_KEY_PATH,
                EXPLORER_MULTI_SELECT_MODEL_VALUE,
                EXPLORER_MULTI_SELECT_MODEL,
            )
            self._backend.write_value(EXPLORER_COMMAND_KEY_PATH, "", command)
        except Exception as exc:
            logger.exception("Could not register Explorer integration")
            raise ExplorerIntegrationError from exc
        return self.inspect()

    def unregister(self) -> ExplorerRegistration:
        """Remove only this verb and its command subkey; parent shell keys remain."""
        current = self._current_executable()
        if self._backend is None or current is None:
            return ExplorerRegistration(
                state=ExplorerRegistrationState.UNSUPPORTED,
                current_executable=current,
            )
        try:
            for key_path in (EXPLORER_COMMAND_KEY_PATH, EXPLORER_KEY_PATH):
                try:
                    self._backend.delete_key(key_path)
                except FileNotFoundError:
                    # Deleting an already absent application-owned key is
                    # idempotent; still attempt the parent verb key.
                    continue
        except Exception as exc:
            logger.exception("Could not unregister Explorer integration")
            raise ExplorerIntegrationError from exc
        return self.inspect()

    def _current_executable(self) -> Path | None:
        try:
            return self._executable_path_provider()
        except Exception:
            logger.exception("Could not determine current packaged executable path")
            return None


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


def _default_backend() -> RegistryBackend | None:
    if os.name != "nt":
        return None
    return WinRegistryBackend()


def _absolute_executable_path(path: Path | str) -> str:
    raw = os.fspath(path)
    if ntpath.isabs(raw):
        return ntpath.normpath(raw)
    return str(Path(raw).absolute())


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


def _parse_command(command: str) -> tuple[str | None, bool]:
    """Parse the command shape that this service writes, without shell evaluation."""
    value = command.strip()
    if not value.startswith('"'):
        return None, False
    closing_quote = value.find('"', 1)
    if closing_quote <= 1:
        return None, False
    executable = value[1:closing_quote]
    arguments = value[closing_quote + 1 :].strip()
    return executable, arguments == '--quick-rename "%1"'
