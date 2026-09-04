# v1.2.0 Release Readiness Checklist

这是 `dlsite-organizer` v1.2.0 的 durable 发布核验清单。它记录必须持续成立的 source、package、
native、安全和运行时门禁；它不代替针对所选 exact release candidate 的 Windows UAT，也不授权
tag、push、merge、GitHub Release 或 artifact upload。

当前 v1.2.0 状态：已接受的 Windows portable 包已完成最终 Windows UAT；本清单中的复选项仍用于
后续候选版本的逐项记录。

## Release identity and immutability

- Release line: `v1.2.0`
- Authoritative version source: `src/dlsite_organizer/version.py`; `pyproject.toml` continues to consume it dynamically
- Release preparation branch: `feature/v1.2.0`
- The selected exact release candidate's filename, absolute path, size, SHA256, mtime, and build HEAD are
  recorded in release evidence
- `v1.1.0` remains released and immutable; its official ZIP must remain `59,437,033` bytes with SHA256
  `6E060328A87F363DFBB2128EAA6D7C371C0E31FD0578DC0E172BF59EB2331684`
- The official v1.1.0 ZIP is rehashed before and after every v1.2.0 release-preparation/build audit
- No tag, push, merge, GitHub Release, or release upload is part of this checklist

## Public v1.2.0 contract

- [ ] Explorer right-click Quick Rename supports multiple selected folders
- [ ] One Explorer selection is one batch, one controller item, one journal transaction, and one Undo
- [ ] A Quick Rename batch accepts at most 32 folders and rejects 33 as a whole batch
- [ ] Unicode and spaces are preserved through selection, IPC, and path handling
- [ ] Explorer integration is current-user HKCU only and requires no administrator rights
- [ ] Windows 11 users may find the classic shell verb under **显示更多选项**
- [ ] A first-level Windows 11 modern-menu placement is not promised
- [ ] The portable package contains the native x64 Shell helper
- [ ] The helper is transient and local-only, not a daemon or background service

## Documentation and release messaging

- [ ] README documents the public multi-select, one-batch/one-Undo, 32-item, current-user, Windows 11,
  portable-helper, and transient-helper contract without unnecessary COM internals
- [ ] CHANGELOG contains v1.2.0 Added / Changed / Safety / Reliability notes and does not mention failed candidates
- [ ] Architecture documents Explorer → DelegateExecute → native x64 COM LocalServer →
  `IExecuteCommand`/`IObjectWithSelection`/`IShellItemArray` → `QUICK_RENAME paths[]` → QLocalServer →
  QuickActionController → QuickRenameService → RenameExecutor/journal
- [ ] Architecture records CLSID `{031255AF-20D8-4EE9-AC4C-D8CE7D3E154B}`, `MAX_QUICK_RENAME_ITEMS=32`,
  192 KiB helper payload cap, 256 KiB IPC frame cap, one-selection/one-batch, independent-click separation,
  and `NO JOURNAL = NO MUTATION`
- [ ] Architecture records verified PySide6 / Qt 6.11.2 native IPC interoperability and requires the
  interop tests after any future Qt/PySide6 upgrade; no arbitrary future Qt version is guaranteed
- [ ] Settings documentation covers 未注册 / 需要更新 / 已注册, legacy v1.1 migration, portable move,
  注册 / 更新, 移除, owned verb/CLSID cleanup, and no-admin behavior
- [ ] Privacy documentation states DLsite is contacted only during user-requested metadata operations,
  GitHub only for manual Update Check, and the Explorer helper is local-only
- [ ] Privacy documentation states there is no telemetry, analytics, startup polling, automatic updater,
  background network polling, or daemon
- [ ] If no tracked release-note convention exists, final Chinese v1.2.0 release notes are retained as
  release-execution metadata rather than invented as tracked release metadata

## Version and source verification

- [ ] `__version__ == "1.2.0"`
- [ ] `application_user_agent()` returns `dlsite-organizer/1.2.0`
- [ ] About/footer displays `v1.2.0`
- [ ] The authoritative package output is `dlsite-organizer-1.2.0-windows-x64.zip`
- [ ] No second authoritative version source or stale runtime `1.1.0` exists outside legitimate historical
  release/changelog/test fixtures
- [ ] Release-preparation changes are logically committed without amending historical Phase 1/2/3/3R commits
- [ ] After the final tracked release-preparation commit, source freeze records `FINAL_V1_2_SOURCE_HEAD`
  and `git status --short` is empty

## Durable native and registration gates

- [ ] Production helper is packaged exactly once at the distribution root beside `dlsite-organizer.exe`
- [ ] Helper is x64 PE (`0x8664`), Windows GUI subsystem (`2`), and has no console window during Explorer activation
- [ ] Helper uses the intended static runtime (`/MT`) and introduces no third-party dependency/license
- [ ] Explorer schema uses `MultiSelectModel=Player` and `command\DelegateExecute`
  `{031255AF-20D8-4EE9-AC4C-D8CE7D3E154B}`
- [ ] `CLSID\{031255AF-20D8-4EE9-AC4C-D8CE7D3E154B}\LocalServer32` points to the exact packaged sibling helper
- [ ] Legacy v1.1 static command is inactive/absent after 注册 / 更新
- [ ] Registration state transitions and stale/move semantics are verified for portable copies A/B
- [ ] Helper dependency audit contains only intentional dependencies; Python, PySide/Qt, WinHTTP, WinINet,
  Winsock, PowerShell, cmd, and dynamic MSVC runtime are absent
- [ ] Helper orphan count is zero after controlled runs
- [ ] Native Release x64 build uses `/W4 /WX`; native tests and CTest pass
- [ ] Native helper ↔ PySide6/Qt 6.11.2 interop passes against the real QLocalServer

## Behavioral and packaged smoke gates

- [ ] Packaged single-select Explorer path passes
- [ ] Packaged multi-select Explorer path passes
- [ ] One selection produces one IPC request, one Quick Action, one service invocation, one transaction,
  N filesystem operations, and one Undo
- [ ] Mixed invalid selection is rejected as a whole; no subset processing or mutation occurs
- [ ] No-primary helper route launches the sibling `dlsite-organizer.exe` with only `--quick-rename-host`,
  sends one batch, and exits
- [ ] Existing-primary helper route forwards one request to the primary without a second component graph
- [ ] 32-item path is eligible; 33-item path is rejected by local, IPC, native, and application gates
- [ ] Independent `[A,B]` then `[C]` and `X/Y/Z` actions remain separate transactions and are never time-merged
- [ ] Existing-primary Full and Lightweight modes, no-primary, Unicode/spaces, journal failure, and conflict
  paths preserve fail-closed behavior
- [ ] `NO JOURNAL = NO MUTATION` passes for all controlled batch paths
- [ ] Packaged normal secondary launch activates the primary, exits, and leaves one long-lived primary
- [ ] Isolated offline startup, profile/database creation, single-instance election, and clean shutdown pass
- [ ] Controlled packaged COM activation passes CoCreateInstance, SetSelection, Execute, and cleanup
- [ ] Controlled registration schema produces Player, DelegateExecute, exact LocalServer32, and no legacy command
- [ ] Packaged exact-RC extraction is used for audit; build tree is not treated as the release payload

## Package, security, TLS, license, and resource gates

- [ ] Extracted audit inventory is deterministic: relative path, size, SHA256, sorted for every regular file
- [ ] Package has no source, tests, `.git`, CMake/Ninja files, PDB/OBJ/LIB/EXP, caches, audit/UAT folders,
  logs, user DB/config, tokens, `.env`, crash dumps, Codex files, or native test executables
- [ ] All packaged PE files are x64; x86, ARM, and unknown architecture counts are zero
- [ ] Foreign ICU, Codex, Poppler, compiler/linker binaries, CMake/Ninja, and unintended runtime contamination
  counts are zero
- [ ] `_internal\certifi\cacert.pem` exists, `_internal\cacert.pem` is absent, and the bundle byte-matches
  the source certifi bundle
- [ ] Frozen `certifi.where()`, `ssl.create_default_context()`, and `httpx.Client` smoke passes
- [ ] Source/package contain no `verify=False`, `CERT_NONE`, or `check_hostname=False`
- [ ] Project MIT license, TIMEpings identity, and `THIRD_PARTY_NOTICES` are present
- [ ] Dependency license files byte-match the authoritative build environment; counts are recorded
- [ ] Resource/branding audit passes with the actual RC count and no branding loss
- [ ] Build logs contain no blocking QtNetwork, certifi, missing-helper, wrong-subsystem, native-build,
  foreign-DLL, ICU, Poppler, or missing-module warning; benign warnings are classified

## Automated source gates

- [ ] `QT_QPA_PLATFORM=offscreen python -m pytest` passes with at least 618 tests and zero failures
- [ ] `ruff check .` passes
- [ ] `pyright` passes
- [ ] `python -m compileall src` passes
- [ ] `python -m pip check` passes
- [ ] selectolax parser smoke passes
- [ ] Qt offscreen startup and Quick Rename smoke pass
- [ ] native Release x64 build, CTest, native tests, and native/PySide interop pass
- [ ] `git diff --check` passes
- [ ] Manual Update Check remains manual-only, has no startup polling or auto install, and a 1.2.0-versus-v1.1.0
  check is classified according to the implemented release-comparison semantics
- [ ] One safe live DLsite metadata/cover smoke passes; the final report omits the work title

## Build, freeze, and exact release-candidate audit policy

- [ ] Build environment records Python, PyInstaller, PySide6, Qt, httpx, certifi, Visual Studio, MSVC,
  Windows SDK, CMake, and Ninja; material unexpected changes are investigated
- [ ] Authoritative `packaging\build-windows.ps1` is used with PATH isolation; Codex runtime, Poppler,
  foreign ICU, unrelated Python, and developer-only native binaries do not participate
- [ ] The exact release candidate is built from a recorded source HEAD as
  `dist\dlsite-organizer-1.2.0-windows-x64.zip`
- [ ] The selected release candidate is rehashed after all audits; size and SHA256 are unchanged from its
  recorded designation
- [ ] No tracked files change during build or audit; if any do, invalidate the candidate and explicitly
  reject the source freeze before tracked repair
- [ ] Final Windows UAT is performed against the exact release candidate, and the final status is recorded
  only when every applicable gate above passes
- [ ] Documentation-only changes made after an accepted candidate are checked against the actual build and
  package dependency graph; if binary impact is uncertain, a new candidate build is required

## Release actions explicitly out of scope

Do not tag, push, merge, publish, upload, overwrite an accepted artifact, or rebuild an exact release
candidate after its final audit. If a tracked change affects package bytes, stop and perform a controlled
new candidate build; otherwise record the docs-only provenance and binary-impact result.
