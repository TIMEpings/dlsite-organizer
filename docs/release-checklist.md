# v1.0.0 Release Readiness Checklist

这是 `dlsite-organizer` 的发布前核验清单。Phase E 完成后停止功能开发，先由用户验收，再决定是否创建 tag、发布 portable ZIP 或进行其他发行操作。本清单不会代替真实 Windows 桌面验收。

本轮 `Work Code + Final Defaults Update` 只完成兼容性、默认值和公开 UI 文案调整，仍停留在
Final Release Readiness 之前：不会创建 `v1.0.0` tag、push、上传或发布，也不会把本轮验证
误记为正式 Release Readiness。

## Compatibility update before readiness

- [ ] RJ / BJ / VJ 都通过同一个 typed `WorkCode`、Lookup、Naming、Organizer 和 Quick Rename pipeline
- [ ] BJ 使用 `books`，VJ 使用有限的 `soft` / `pro` public-page resolution；不进行全 section brute-force
- [ ] 新 profile 默认 `lightweight`
- [ ] 新 profile 默认 `[{workno}][{maker_name}]{title}`
- [ ] 新 profile metadata locale 按支持的系统语言映射，无法匹配时回退 `ja_jp`
- [ ] 已有 `startup_mode`、`metadata_locale` 和 naming template 原样保留
- [ ] Explorer 仍为单文件夹 `--quick-rename`；不宣传或恢复 multi-select verb

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
REAL HKCU REGISTER/UPDATE/REMOVE IN THE USER DESKTOP SESSION:
MANUAL REQUIRED

REAL EXPLORER SINGLE-SELECTION MENU VISIBILITY:
MANUAL REQUIRED

REAL EXPLORER SINGLE INVOCATION + ONE WINDOW/TRANSACTION/UNDO:
MANUAL REQUIRED

REAL EXPLORER MULTI-SELECT VERB UNAVAILABLE:
MANUAL REQUIRED

REAL EXPLORER DRAG/DROP:
MANUAL REQUIRED

REAL EXPLORER MENU CLICK:
MANUAL REQUIRED
```

The automated `--quick-rename` batch boundary and registry-backend register/update/remove checks
are covered. The real HKCU and single-selection invocation gates require the user's real desktop
Explorer because the connected automation environment does not share the host Explorer registry
session. The multi-select gate must confirm that the verb is unavailable or not executable; it must
never launch one process per selected folder.

Before release, also perform the visible GUI walkthrough:

```text
启动 → 整理 → 查询 → 设置 → 关于 footer
→ 完整模式 / 轻量模式 → clean exit → restart
```

Verify real lookup examples for `RJ`, `BJ`, and `VJ`, the placeholder `输入完整RJ|BJ|VJ号`,
the simplified Lookup subtitle, no duplicate Organizer subtitle, fresh lightweight startup,
`[{workno}][{maker_name}]{title}`, system-mapped metadata locale or `ja_jp` fallback,
preview-before-mutation in full mode, immediate safe rename in lightweight mode, Undo, About
resources, and Explorer single-folder integration wording.

## Git and release status

- Branch: `codex/bootstrap-v0.1`
- Phase E must be an independent commit.
- `v1.0.0` tag: absent until explicitly authorized after user acceptance.
- Push, GitHub Release, upload and auto-updater: not performed.
