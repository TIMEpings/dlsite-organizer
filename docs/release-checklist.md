# v1.1.0 Release Readiness Checklist

这是 `dlsite-organizer` 的发布前核验清单。它定义 v1.1.0 release line 的验收证据、刷新规则和最终发布门禁，不代替真实 Windows 桌面验收。发布动作只能在最终审计通过后执行。

## Release identity and policy

- Last published release: `v1.0.0` (`v1.0.0` tag)
- Current release line: `v1.1.0`
- Authoritative version source: `1.1.0`; packaging continues to use dynamic version wiring
- Release preparation branch/policy: `feature/v1.1.0`; release HEAD must be identified by the final audit
- Release tag and GitHub Release are created only after the final release-readiness audit
- Exact artifact filename, size, SHA256, and build-HEAD identity are established during the final audit

## Completed v1.1.0 packaged acceptance baseline

Packaged Windows acceptance for the v1.1.0 release line was completed and accepted as RC #3 manual-UAT evidence. The accepted baseline covers:

- [x] Packaged startup and clean exit
- [x] DLsite live behavior and manual Update Check
- [x] Explorer same-user integration, including real drag/drop and classic-menu click
- [x] Quick Rename, Undo, and cross-mode Undo
- [x] Lightweight visual behavior
- [x] Real Windows 100% DPI
- [x] Real Windows 125% DPI
- [x] Native, resource, license, and security audits
- [x] Frozen TLS/certifi smoke

RC #3 is historical evidence of the accepted manual-UAT baseline. Its exact artifact identity is recorded in the Phase 4D.1 audit evidence; it is not itself the release HEAD after any later tracked change.

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
- [x] One live update-check smoke was attempted; the environment returned no release response

## Post-tracked-change refresh rule

If any tracked release-preparation change occurs after an accepted packaged-UAT baseline, a fresh artifact must be built from the new release HEAD. Manual UAT may be inherited by the refreshed artifact only when the final audit explicitly proves all of the following:

- same application version
- runtime and package inputs unchanged
- identical extracted regular-file path set and file count
- every extracted file has identical bytes, size, and SHA256
- native/resource/license/security audits still pass
- final release audit approves the payload equivalence

ZIP container metadata, archive timestamps, and extraction timestamps do not determine payload equivalence. Differences inside the EXE, DLLs, PYZ, Qt resources, certifi, licenses, icons, or `_internal` payload are content differences. If equivalence is not proven, affected or full packaged UAT must be repeated.

## Final release gates

Before release, the final audit must confirm:

- exact release HEAD and clean working tree
- exact artifact filename, size, and SHA256
- artifact built from the exact release HEAD
- payload correspondence to the accepted packaged-UAT baseline, or completion of required new UAT
- remote tag absence/presence as appropriate to the release step
- native, resource, license, TLS, and security state
- release execution readiness

The final audit must also preserve the evidence needed to explain any refreshed-candidate decision. No tag, push, merge, GitHub Release, or artifact upload is implied until those gates pass and the release action is explicitly authorized.

## Accepted packaged UAT evidence (RC #3)

The accepted RC #3 walkthrough covered the real same-user Windows desktop session:

```text
REAL HKCU REGISTER/UPDATE/REMOVE IN THE USER DESKTOP SESSION:
PASS — accepted in RC #3 baseline

REAL EXPLORER SINGLE-SELECTION MENU VISIBILITY:
PASS — accepted in RC #3 baseline

REAL EXPLORER SINGLE INVOCATION + ONE WINDOW/TRANSACTION/UNDO:
PASS — accepted in RC #3 baseline

REAL EXPLORER MULTI-SELECT VERB UNAVAILABLE:
PASS — accepted in RC #3 baseline

REAL EXPLORER DRAG/DROP:
PASS — accepted in RC #3 baseline

REAL EXPLORER MENU CLICK:
PASS — accepted in RC #3 baseline
```

The visible GUI walkthrough was:

```text
启动 → 整理 → 查询 → 设置 → 关于 footer
→ 完整模式 / 轻量模式 → clean exit → restart
```

It included real `RJ`, `BJ`, and `VJ` lookup examples, the placeholder
`输入完整RJ|BJ|VJ号`, fresh lightweight startup, `[{workno}][{maker_name}]{title}`,
preview-before-mutation in full mode, immediate safe rename in lightweight mode, Undo, About
resources, manual Update Check, and the Explorer single-selection contract.

The Explorer forensic conclusion for the observed environment was `ENVIRONMENT / USER-CONTEXT REGISTRY-HIVE MISMATCH`; the real same-user `TIMEpings` context passed.

## Artifact and release safety

Any artifact not produced from the exact release HEAD by the authoritative packaging workflow is non-authoritative release input. The final candidate must be freshly built after any tracked release-preparation change and must satisfy the post-tracked-change refresh rule above.
