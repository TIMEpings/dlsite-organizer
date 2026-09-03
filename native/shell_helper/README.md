# Native Explorer selection adapter spike

This directory contains the v1.2.0 Phase 1 x64-only native foundation.  The
source is part of the project MIT-licensed work:

`Copyright (c) 2026 TIMEpings`

It is intentionally not part of the v1.1.0 Explorer registration or the
PyInstaller/ZIP packaging yet.  Build output belongs under the ignored
repository `build/` directory.

## Build

From a Visual Studio Developer Command Prompt configured for x64:

```text
cmake -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_TESTING=ON -S native/shell_helper -B build/native/shell_helper
cmake --build build/native/shell_helper --parallel
ctest --test-dir build/native/shell_helper --output-on-failure
```

The development helper is:

`build/native/shell_helper/bin/dlsite-shell-helper.exe`

`shell_helper_interop_client.exe` is test-only and is not a production
entrypoint.  It accepts a test profile root so Python can select an isolated
server; the production helper accepts only COM's optional `-Embedding` switch
and derives its profile from the current user's `%LOCALAPPDATA%`.

## Fixed COM identity

The Phase 1 class identity is:

`{031255AF-20D8-4EE9-AC4C-D8CE7D3E154B}`

The same object implements `IExecuteCommand` and `IObjectWithSelection`.
There is no permanent registry entry in this phase.

## Selection contract

`ShellSelectionAdapter` enumerates `IShellItemArray` with `GetCount` and
`GetItemAt`, requires both `SFGAO_FILESYSTEM` and `SFGAO_FOLDER`, then extracts
`SIGDN_FILESYSPATH`.  Any null, empty, non-filesystem, non-folder, malformed,
control-character, overlong, or duplicate-normalized item rejects the whole
selection.  The provisional hard limit is 32 items.  The original Shell array
order is retained; the primary application remains authoritative for domain
ordering.

`ShellCommand::Execute` snapshots the validated paths and can dispatch at most
one batch for that command object.  A missing or invalid selection performs no
IPC, process launch, or filesystem action.

## IPC contract

The native client mirrors `src/dlsite_organizer/app/single_instance.py`:

* profile identity is `normcase(normpath(abspath(profile_root)))` using UTF-8
  SHA-256, truncated to the first 32 hexadecimal characters;
* server name is `dlsite-organizer-<profile-hash>`;
* the Windows endpoint is the named pipe
  `\\.\pipe\dlsite-organizer-<profile-hash>`;
* each message is one 4-byte big-endian payload length followed by bounded
  UTF-8 JSON;
* outgoing native requests use protocol v1 and the existing
  `QUICK_RENAME`/`payload.paths` shape, with no protocol-version bump;
* the client waits only for the admission ACK and recognizes
  `ACCEPTED`, `DUPLICATE`, `QUEUE_FULL`, `SHUTTING_DOWN`, `REJECTED`, and
  `UNSUPPORTED_VERSION`;
* after a connection has been opened, any write, timeout, disconnect, or
  malformed ACK is ambiguous and is never replayed.

The request ID is a GUID rendered as a bounded ASCII hyphenated ID.  The
native JSON implementation is deliberately limited to this fixed request and
ACK shape; it is not a general JSON library.

## QLocalServer transport audit

The production server uses a bare name such as
`dlsite-organizer-<hash>`.  The Qt 6 public documentation states that
`QLocalSocket` is a Windows named pipe and that `QLocalServer::listen()` can
accept a Windows pipe path such as `\\.\pipe\foo`.  The Qt 6.8 source used by
the audit maps a bare name to that path before calling `CreateNamedPipeW`,
uses `PIPE_TYPE_BYTE | PIPE_READMODE_BYTE`, and adds no application framing.
The matching client source opens the same full pipe path with `CreateFileW`.

References: [QLocalServer](https://doc.qt.io/qt-6/qlocalserver.html),
[QLocalSocket](https://doc.qt.io/qt-6/qlocalsocket.html), and the official
[Qt 6.8 Windows server source](https://raw.githubusercontent.com/qt/qtbase/6.8/src/network/socket/qlocalserver_win.cpp).

This was verified against the installed PySide6/Qt **6.11.2** endpoint by the
Python tests in `tests/app/test_native_shell_helper_interop.py`: native Win32
connects to the real `QLocalServer`, sends byte-exact UTF-8 framed JSON with
Unicode and spaces, receives both acceptance and rejection ACKs, and treats a
missing ACK as ambiguous.  The test server uses
`UserAccessOption`, preserving same-user/local-only access.

The supported dependency decision for this spike is therefore pure Win32;
Qt is not linked into the helper.  The pipe prefix is an explicitly tested
Qt 6 Windows transport contract for this repository's current Qt line, not a
user-supplied endpoint.  If a future Qt line changes this documented Windows
name behavior, the interop gate must fail before production packaging.

## COM server lifetime

The process initializes COM as `COINIT_MULTITHREADED`, calls
`CoInitializeSecurity`, and registers one `CLSCTX_LOCAL_SERVER` class object
with `REGCLS_MULTIPLEUSE | REGCLS_SUSPENDED` followed by
`CoResumeClassObjects`.  The class object remains available while command
objects, active executions, or `LockServer` locks exist.  Once all are idle,
the helper revokes the class object and exits after a bounded 10-second idle
window; it is not a daemon, tray process, or autostart task.

No COM registration, production Explorer verb, application bootstrap change,
or packaging change is made here.  A real Explorer probe is consequently
deferred to human UAT on the normal interactive desktop.

## Human Explorer UAT probe (temporary HKCU registration only)

This is a manual probe plan, not a Phase 1 automated registration step.  Use
an isolated development checkout and the x64 Release helper.  In `regedit`,
create only these temporary per-user values:

1. `HKCU\Software\Classes\CLSID\{031255AF-20D8-4EE9-AC4C-D8CE7D3E154B}\LocalServer32`
   with the default value set to the absolute path of
   `build/native/shell_helper/bin/dlsite-shell-helper.exe`.
2. `HKCU\Software\Classes\Directory\shell\dlsite-v12-com-probe` with a
   display label such as `DLsite COM probe`, then its `command` subkey with a
   `DelegateExecute` `REG_SZ` value containing the same CLSID.

Close and reopen Explorer (or otherwise refresh its shell view), select three
folders whose names include Unicode and spaces, and invoke the temporary
`DLsite COM probe` verb.  Confirm that the running application receives one
`QUICK_RENAME` admission for one `payload.paths` array of length three, with
the Shell selection order preserved.  Repeat the probe as two independent
clicks—select A/B and invoke it, then shortly select C/D and invoke it again—
and confirm the batches remain separate with no selection state bleed.  Also
try a mixed or unsupported item; the helper must fail closed without
launching the application or sending IPC.  If the verb does not activate,
record the exact HRESULT/event details and leave the implementation unchanged
for diagnosis.

After the probe, delete only the temporary `dlsite-v12-com-probe` key and
the temporary CLSID key under `HKCU\Software\Classes\CLSID`.  Verify that
the existing production `dlsite-organizer` verb and all application files
are unchanged.  Do not create these values as part of a package or commit
them to the repository.
