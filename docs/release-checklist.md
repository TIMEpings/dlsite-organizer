# v1.1.0 Release Readiness Checklist

这是 `dlsite-organizer` 的发布前核验清单。它记录 v1.1.0 candidate 的状态，不代替真实
Windows 桌面验收。完成所有 pending gates 并获得用户验收后，才决定是否创建 tag、发布
portable ZIP 或进行其他发行操作。

## Release status

- Last released: `v1.0.0` (`v1.0.0` tag)
- Current candidate: `v1.1.0`
- Development branch: `feature/v1.1.0`
- Current candidate HEAD: use `git rev-parse HEAD` after the Phase 4A commits
- Release tag: not created
- GitHub Release: not created
- v1.1.0 artifact: not built

## v1.1.0 scope

- [x] About 页面手动检查 GitHub public Releases 更新；无认证、token、启动检查、后台轮询、自动下载或安装
- [x] Lightweight 使用统一的 drag/drop 与 recent-operation visual surface
- [x] Lightweight 默认尺寸 `540×280`，最小尺寸 `460×280`
- [x] Undo 后只展示最新 presentation record；SQLite/journal 仍是 authoritative transaction history
- [x] Fresh profile 默认 `lightweight`，已有 startup mode 保持不变
- [x] 现有命名模板 canonical/legacy aliases 和 public navigation contract 保持不变

## Source-level verification

- [x] Full automated tests: `QT_QPA_PLATFORM=offscreen python -m pytest`
- [x] Update checker, About/MainWindow, Lightweight, Mode/Undo and Quick Rename regression tests
- [x] Project attribution, settings defaults, naming compatibility and public navigation tests
- [x] Ruff: `ruff check .`
- [x] Pyright: `pyright`
- [x] Compile: `python -m compileall src`
- [x] Dependency health: `python -m pip check`
- [x] selectolax parser smoke
- [x] Qt offscreen MainWindow/About/navigation/Lightweight/clean-exit smoke
- [x] `git diff --check`
- [x] Authoritative version source is `1.1.0`; packaging continues to use dynamic version wiring
- [x] One live update-check smoke was attempted; the environment returned no release response

## Pending release gates

- [ ] v1.1.0 Windows candidate build
- [ ] Packaged startup / clean exit
- [ ] Packaged manual Update Check
- [ ] Packaged Lightweight UAT
- [ ] Real Windows 100% DPI
- [ ] Real Windows 125% DPI
- [ ] Explorer integration regression
- [ ] Quick Rename / Undo regression
- [ ] Native binary audit
- [ ] ICU/Codex/Poppler contamination audit
- [ ] License/resource audit
- [ ] Final release readiness audit
- [ ] Merge, tag, push and GitHub Release

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

Before release, perform the visible GUI walkthrough:

```text
启动 → 整理 → 查询 → 设置 → 关于 footer
→ 完整模式 / 轻量模式 → clean exit → restart
```

Verify real `RJ`, `BJ`, and `VJ` lookup examples, the placeholder
`输入完整RJ|BJ|VJ号`, fresh lightweight startup, `[{workno}][{maker_name}]{title}`,
preview-before-mutation in full mode, immediate safe rename in lightweight mode, Undo, About
resources and manual Update Check behavior. The Explorer gates require the user's real desktop
session and must confirm that the static verb never launches one process per selected folder.

## Artifact and release safety

The existing `dist/dlsite-organizer-1.0.0-windows-x64.zip` is a **NON-AUTHORITATIVE LOCAL REBUILD**.
It must not be used as release input, uploaded, deleted, renamed, modified or rebuilt during Phase
4A. Phase 4B will build the new `dlsite-organizer-1.1.0-windows-x64.zip` candidate.

Phase 4A does not build, create a tag, push, merge `main`, create a GitHub Release or upload an
artifact.
