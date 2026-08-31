# v1.0.0 Release Checklist

This checklist is for the first public Windows release. A checked automated item
must have a reproducible command or recorded artifact. Do not count an
unavailable manual gate as PASS.

## Release gates

- [x] Full tests: `QT_QPA_PLATFORM=offscreen python -m pytest` — 301 passed
- [x] Ruff: `ruff check .`
- [x] Pyright: `pyright`
- [x] Compile: `python -m compileall src`
- [x] Dependency health: `python -m pip check`
- [x] selectolax import/parser smoke
- [x] `git diff --check`
- [x] Version source is exactly `1.0.0`
- [x] Intended release tree is clean after the release commit; `research/`
      remains local and ignored
- [x] User-selected `LICENSE` is present (MIT; holder: `dlsite-organizer contributors`)
- [x] Fresh-profile startup: no DB, cache, or config; schema initializes; MainWindow opens and exits
- [x] Fresh-profile restart succeeds
- [x] Legacy SQLite upgrade preserves metadata observations, translation history,
      manual reviews, bonus evidence state, and rename journal rows
- [x] `bonus_evidence_json = NULL` remains unknown/legacy, not `bonuses = []`
- [x] Offline startup succeeds without a provider call (source smoke; 0 startup provider requests)
- [x] Packaged offline startup under blocked HTTP(S)/ALL_PROXY: fresh profile and restart exit cleanly
- [x] PyInstaller artifact build completes from canonical `.venv` (`1.0.0`)
- [x] Artifact imports/runs PySide6, SQLAlchemy, selectolax, and packaged dependencies
- [x] Packaged artifact does not depend on `src/`, `tests/`, `research/`, or `.venv/`
- [x] Packaged fresh-profile startup and restart
- [x] Packaged Lookup page, Review Queue, Relations, and Settings navigation smoke
- [x] PyInstaller foreign ICU contamination regression guard: build-time PATH
      isolation, provenance rejection, and final dist audit
- [x] Manual Windows rename smoke: scan → preview → execute → verify → journal (temporary directory)
- [x] Manual Windows Undo smoke: undo → verify restoration (temporary directory)
- [x] Network lookup smoke against approved live test work `RJ01609020`
- [x] README, CHANGELOG, and this checklist updated
- [x] Security/repository hygiene audit completed: no tracked secret patterns or local DB/log/private data
- [x] SHA-256 recorded for the final artifact: `dlsite-organizer-1.0.0-windows-x64.zip`,
      57,598,016 bytes, `C089454D147BABC3034768DA42B32B844FA594A1ED18695265AFE54782F51164`
- [x] Local annotated tag `v1.0.0` created after every gate is checked
- [x] No push and no remote GitHub release from this procedure

## Required manual report when a gate cannot run

Use an explicit status such as:

```text
MANUAL WINDOWS SMOKE REQUIRED
```

Do not convert skipped manual work into an automated PASS. The release report
must separate automated evidence from manual evidence and list every skip.

## Artifact procedure

From a Windows checkout with the canonical environment:

```powershell
.\.venv\Scripts\Activate.ps1
.\packaging\build-windows.ps1
Get-ChildItem .\dist\dlsite-organizer-*-windows-x64.zip | Get-FileHash -Algorithm SHA256
```

The output zip is a validation/release candidate only until the LICENSE and all
manual gates are complete. Keep build output under ignored `build/` and `dist/`;
never include `research/`, local databases, logs, private spreadsheets, or
development caches.

## Current audit status

The source version is now `1.0.0`. The MIT `LICENSE` and runtime third-party
notices are present. The Qt frozen-DLL blocker is fixed: the minimal sample and
the full application build without bundled ICU DLLs and pass clean-PATH startup
smoke. Release builds isolate build-time PATH from unrelated tool runtimes.

## Recorded release evidence

- Qt frozen-DLL root cause: PyInstaller was resolving `icuuc.dll` from the
  foreign Poppler runtime on the inherited build PATH. The release build now
  isolates PATH for the canonical Python child process, rejects foreign ICU
  provenance during Analysis, and audits the final dist before creating ZIP.
- Minimal Qt sample: build PASS; normal PATH run PASS; clean system PATH run
  PASS; no `icu*.dll` bundled.
- 0.10.3 verification artifact: build PASS; fresh-profile MainWindow/navigation
  smoke PASS; normal PATH launch PASS; clean system PATH restart PASS; no
  `icu*.dll`, Codex, Poppler, source, tests, research, or `.venv` paths in the
  artifact. Validation ZIP: `dist/dlsite-organizer-0.10.3-windows-x64.zip`.
- Approved live lookup smoke `RJ01609020`: PASS. Default automated tests remain
  offline.
- Packaged legacy DB gate: PASS in an isolated temporary profile; metadata
  observations, translation history, manual review, and rename journal rows
  survived additive upgrade; `bonus_evidence_json = NULL` remained unknown.
- Packaged rename gate: PASS in an isolated temporary works directory; GUI scan,
  preview, explicit confirmation, execute, journal verification, Undo, and full
  filename/content restoration all completed.
- Final v1.0.0 artifact: fresh-profile startup, MainWindow/navigation, blocked-
  network startup, restart, and clean exit all PASS. Startup log reported
  `v1.0.0`; final ZIP checksum is recorded above.
- Release scope stops at `1.0.0`; no v1.1 work is included.
