# v1.0.0 Release Readiness Checklist

这是 `dlsite-organizer` 的发布前核验清单。Phase E 完成后停止功能开发，先由用户验收，再决定是否创建 tag、发布 portable ZIP 或进行其他发行操作。本清单不会代替真实 Windows 桌面验收。

## Automated gates

- [x] Full offline tests: `QT_QPA_PLATFORM=offscreen python -m pytest`
- [x] Ruff: `ruff check .`
- [x] Pyright: `pyright`
- [x] Compile: `python -m compileall src`
- [x] Dependency health: `python -m pip check`
- [x] selectolax parser smoke
- [x] `git diff --check`
- [x] Version source is `1.0.0`
- [x] Candidate, manual-review, relation, bonus-observation and journal backend tests remain green
- [x] PyInstaller onedir development verification artifact builds from canonical `.venv`
- [x] Packaged fresh-profile startup/navigation smoke
- [x] Packaged artifact excludes source, tests, research and `.venv`
- [x] `LICENSE`, `THIRD_PARTY_NOTICES.md` and required runtime license files are packaged
- [x] ICU provenance audit passes with no foreign ICU DLL
- [x] Native PE audit passes for x64 `.exe`, `.dll` and `.pyd` files

## Current packaged artifact

The verification build is produced by:

```powershell
.\packaging\build-windows.ps1
```

It writes `dist\dlsite-organizer\` and a versioned ZIP under ignored build output. The build is a validation artifact only; it is not a release. The About page resolves `LICENSE` and `THIRD_PARTY_NOTICES.md` through the application resource helper, and the dependency license directory is verified before the ZIP is created.

## Manual gates pending user acceptance

```text
REAL EXPLORER DRAG/DROP:
MANUAL REQUIRED

REAL EXPLORER MENU CLICK:
MANUAL REQUIRED
```

The automated `--quick-rename` boundary and HKCU register/update/remove checks are covered; the two gates above require the user's real desktop Explorer because the connected automation environment does not share the host Explorer registry/session.

Before release, also perform the visible GUI walkthrough:

```text
启动 → 整理 → 查询 → 设置 → 关于
→ 完整模式 / 轻量模式 → clean exit → restart
```

Verify real lookup examples `RJ01609020`, `RJ01636949` and `RJ01637033`, preview-before-mutation in full mode, immediate safe rename in lightweight mode, Undo, About resources, and Explorer integration wording.

## Git and release status

- Branch: `codex/bootstrap-v0.1`
- Phase E must be an independent commit.
- `v1.0.0` tag: absent until explicitly authorized after user acceptance.
- Push, GitHub Release, upload and auto-updater: not performed.
