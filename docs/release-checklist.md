# v1.0.0 Release Checklist

This checklist is for the first public Windows release. A checked automated item
must have a reproducible command or recorded artifact. Do not count an
unavailable manual gate as PASS.

## Release gates

- [x] Full tests: `QT_QPA_PLATFORM=offscreen python -m pytest` — 296 passed
- [x] Ruff: `ruff check .`
- [x] Pyright: `pyright`
- [x] Compile: `python -m compileall src`
- [x] Dependency health: `python -m pip check`
- [x] selectolax import/parser smoke
- [x] `git diff --check`
- [ ] Version source is exactly `1.0.0` after blockers are resolved
- [ ] Intended release tree is clean; `research/` remains local and untracked
- [ ] User-selected `LICENSE` is present
- [x] Fresh-profile startup: no DB, cache, or config; schema initializes; MainWindow opens and exits
- [x] Fresh-profile restart succeeds
- [x] Legacy SQLite upgrade preserves metadata observations, translation history,
      manual reviews, bonus evidence state, and rename journal rows
- [x] `bonus_evidence_json = NULL` remains unknown/legacy, not `bonuses = []`
- [x] Offline startup succeeds without a provider call (source smoke; 0 startup provider requests)
- [ ] PyInstaller artifact build completes from canonical `.venv`
- [ ] Artifact imports/runs PySide6, SQLAlchemy, selectolax, and packaged dependencies
- [ ] Packaged artifact does not depend on `src/`, `tests/`, `research/`, or `.venv/`
- [ ] Packaged fresh-profile startup and restart
- [ ] Packaged Lookup page, Review Queue, Relations, and Settings navigation smoke
- [x] Manual Windows rename smoke: scan → preview → execute → verify → journal (temporary directory)
- [x] Manual Windows Undo smoke: undo → verify restoration (temporary directory)
- [ ] Network lookup smoke against an approved live test work, if network is available
- [x] README, CHANGELOG, and this checklist updated
- [x] Security/repository hygiene audit completed: no tracked secret patterns or local DB/log/private data
- [ ] SHA-256 recorded for the final artifact
- [ ] Local annotated tag `v1.0.0` created only after every gate is checked
- [ ] No push and no remote GitHub release from this procedure

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

At the start of v1.0.0 preparation the source version is `0.10.3`. The remaining
known decision gate is the missing user-selected `LICENSE`; the version must not
be bumped or tagged while that blocker remains unresolved.

## Recorded skips / manual gates for this preparation run

- `MANUAL WINDOWS SMOKE REQUIRED`: real filesystem scan → preview → execute →
  journal → Undo was not performed in this environment.
- `PACKAGED ARTIFACT VERIFICATION REQUIRED`: PyInstaller could not be installed
  because the configured package index returned repeated SSL EOF errors; no
  artifact or checksum was claimed.
- Live network lookup smoke was not run; default automated tests remain offline.
