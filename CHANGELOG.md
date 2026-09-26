# Changelog

## v1.3.0 — 2026-09-26

### 新增

- 新增“重命名历史”页，可分页查看已保存的重命名事务、操作结果、撤销结果和恢复信息。
- Organizer 可从未解决事务提示直接打开对应的事务详情。

### 安全与可靠性

- 历史页只读；当前文件系统观察与持久化 journal 事实分开展示，不将路径存在解释为原目录身份，也不自动恢复或修改事务。
- 保持未解决事务对新的重命名和撤销操作的阻止。

## v1.2.1 — 2026-09-25

### 修复

- 修复应用启动期间通过资源管理器右键执行 Quick Rename 时，所选文件夹偶发丢失或操作失败的问题。

## v1.2.0 — 2026-09-03

### 新增

- Explorer 右键快捷重命名支持多文件夹选择。

### 改进

- 一次 Explorer 多选作为一个批处理事务处理，并沿用同一套 Quick Rename、journal 和 Undo 流程。
- portable package 包含独立原生 x64 Shell helper；helper 不在 `explorer.exe` 内加载 Python/PySide，且没有可见控制台窗口。

### 安全与可靠性

- 一次撤销可以恢复整个多文件夹批次；批次最多处理 32 个文件夹，超过上限时整批拒绝。
- Explorer 独立请求不会被时间窗口合并；helper 仅负责选择传递和本地 IPC，不是 daemon 或后台服务。

## v1.1.0 — 2026-09-01

- Added: manual update check from the About page using the public GitHub Releases API.
- Changed: Lightweight mode now uses a more compact unified drop/recent-operation surface.
- Changed: one active application instance per user/profile now owns normal activation and Explorer
  Quick Rename forwarding; rapid Quick Rename actions are serialized in the primary application.
- Fixed: the recent-operation presentation stays compact after Undo.

## v1.0.0 — 2026-08-31

首个 Windows standalone release。该版本冻结 feature behavior，并聚焦 packaging、
upgrade safety、documentation 和 launch validation。

- RJ lookup with normalized DLsite metadata and cover retrieval.
- Metadata cache and append-only observation/history storage.
- Current and historical confirmed translation relations with separate provenance.
- Folder scan and safe formatting of proposed names.
- Safe rename workflow with preview, explicit confirmation, preflight, durable
  journal, crash/recovery states, and Undo.
- Candidate relations using `same-maker-same-date v1`, kept non-confirmed and
  without score or probability.
- Local Manual Review and Review Queue history, including review-time evidence.
- Evaluation support for review history and derived datasets. The 24-pair Pilot
  documents workflow and taxonomy use; it is not an accuracy or precision evaluation.
- Bonus observation preservation from v0.10.3 onward, including the distinction
  between `NULL` unknown/legacy evidence and an explicitly empty bonus list.
- Transient bonus evidence preservation; no automatic recovery or detection of
  expired limited-bonus relations.

Candidate is derived/non-confirmed. The candidate policy remains
`same-maker-same-date v1`; it does not auto-confirm or learn from labels.
Automatic recovery/detection of expired limited-bonus relations is not implemented.

## 0.10.3 — 2026-08-30

- Preserve normalized bonus metadata observations in historical SQLite rows.
- Keep legacy `bonus_evidence_json = NULL` distinct from explicit `bonuses: []`.
- Do not infer or create bonus relations from transient observations.

## 0.10.2

- Add the local candidate Review Queue and expanded Manual Review taxonomy.
- Preserve candidate policy provenance and review-time evidence.

## 0.10.1

- Add the derived evaluation dataset workflow for manual review history.

## 0.10.0

- Add historical confirmed translation relation queries over metadata observations.

## 0.9.0

- Add explainable, non-scored relation candidates from local metadata.

## 0.5–0.8

- Add persistent metadata cache, append-only observations, and stale-cache
  fallback behavior.
- Harden provider parsing and explicit translation topology handling.
- Add safe Organizer preview, durable rename execution, crash recovery, and Undo.

## 0.1–0.4

- Bootstrap the desktop application, manual RJ lookup, DLsite metadata parsing,
  local folder scanning, naming, and the first safe rename preview flow.
