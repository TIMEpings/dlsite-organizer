# Phase 7 — Rename History visual migration

## A. Baseline

- Branch: `main`.
- Starting HEAD: `7ce82d9ace22dfcfe04c1a624ad4486ab0e3bec4` (`7ce82d9`).
- The initial worktree was clean; `v1.3.1` is an ancestor. The expected recent history was present.
- The requested baseline was 1,177 passed and zero skipped. Two baseline full-suite attempts each had one sporadic Windows `WinError 5` failure, in `test_pathname_rename_has_a_residual_source_replacement_window` and `test_completed_controller_action_releases_payload`; both tests passed in isolation on retry. The pathname-rename case also failed once in the first focused Organizer run and passed on isolated retry.

## B. Legacy History problems

History already had a master/detail page and bounded list loading, but the master pane was effectively fixed and its splitter handle was invisible. The detail view made technical transaction data compete with the user-facing outcome, used a path-heavy operation table, and showed current path observations only for unresolved transactions. This made it harder to distinguish a durable recorded result from a later filesystem change.

## C. Semantic model

| Category | Repository source | Meaning |
|---|---|---|
| Durable transaction fact | `RenameTransaction.status`, `undo_state`, timestamps, root, recovery fields | Values loaded from the journal and never rewritten by History observation. `display_status` is the existing presentation projection for unresolved transactions. |
| Durable operation fact | `RenameOperation.status`, `undo_status`, errors, timestamps, sequence and operation ID | Forward execution and Undo outcomes recorded in the journal. They remain independently visible. |
| Current filesystem observation | `RenameHistoryService.observe_paths()` and `CurrentPathObservation` | Fresh no-follow path checks, with directory, file, other, link-like, absent, and error outcomes. These are not journal facts. |
| Presentation selection | `_selected_id` and the current list item’s transaction ID | Which transaction is being inspected. Selection does not mutate the journal or filesystem. |

`RenameHistoryService.list_recent()` supplies bounded summaries in journal order. `get_transaction()` loads durable detail. `observe_paths()` checks the recorded source and target paths with `lstat`; it does not prove that a path still identifies the original directory.

## D. Master list

The master list is ordered newest first by journal creation time with transaction ID as the stable tie-breaker. Its identity is the transaction ID, not the row index. Rows show localized date/time, operation count, and concise mapped status; UUIDs and full paths stay out of the default list. Operation counts are supplied by a grouped count query for the bounded page, with no schema change.

The horizontal `QSplitter` defaults to 248 logical pixels for the master pane, constrained to 232–300 pixels, with a restrained six-pixel handle. The page keeps its splitter and selection in the long-lived History page instance.

## E. Detail structure

The detail column reads top to bottom as: selected transaction time/count/status; unresolved banner if applicable; **记录事实** and recorded root; **当前文件系统** with observation time and per-operation path checks; the compact operation table; selected-operation path/error details; and collapsed **技术详情**.

The no-history view contains one explanatory empty state in the detail pane. The empty master list is not rendered as a blank bordered list or a disabled detail form.

## F. Journal facts

The normal detail shows mapped transaction status, forward operation outcome counts, Undo outcome counts, recorded time, completion time when present, root, and recovery phase/sequence/diagnostic when present. Each operation row retains its sequence, forward outcome, Undo outcome, and source/target basenames. Selecting an operation reveals its complete paths and recorded errors. Full paths and root can be copied.

Current observation does not alter any of these facts.

## G. Current observation

Selecting a transaction and using Refresh both obtain a fresh observation for that transaction. The observation section is separate from journal facts and identifies its observation time, counts recorded source/target paths by current state, and shows source and target states per operation. The text explains that existence is not proof of identity. Observation errors are reported as **当前状态暂时无法确认** with a concise diagnostic; journal status remains unchanged.

## H. Operation presentation

Operations use scrollable `QTableWidget` tables with fixed compact row heights and no per-operation child widgets. The journal table shows sequence, recorded forward/Undo result, and source/target names. A separate table shows current source/target observations. Selecting an operation exposes copyable full paths and durable error details. This keeps current path state from looking like an execution result.

## I. Undo and recovery

An undone transaction is labeled **已撤销**, while its original forward successes remain in the operation table and its Undo results are shown separately. The current observation can still report that the original target is now absent and the source is present.

Unresolved transactions receive a persistent, readable recovery banner and recorded recovery details. Their path observations remain separate and do not resolve journal uncertainty. History adds no edit, retry, repair, rollback, or other mutation controls.

## J. Technical details

The disclosure is collapsed by default. It contains transaction ID, raw transaction/Undo states, precise transaction timestamps, and the selected operation’s journal ID, raw states, and precise execution/Undo times. The control has an accessible name and description; paths and diagnostic text are selectable, and copy buttons are labeled.

## K. Selection and refresh

- First entry selects the newest transaction.
- Selection follows transaction ID across list refresh, new transactions, navigation away/back, and journal notifications received while History is hidden.
- A `mutation_history_changed` notification refreshes journal presentation and the selected transaction’s current observation. It is not treated as proof of filesystem mutation.
- Explicit Refresh reloads the bounded journal page and re-observes the selected transaction. It does not rewrite history.

## L. Performance

Synthetic measurements were taken on the native Windows environment after UI construction was warmed. Values are representative local timings, not a strict performance gate.

| Case | Result |
|---|---:|
| 50-row master list population | 2.30 ms |
| 10-operation detail construction | 1.64 ms |
| 10-operation raw path observation / UI observation refresh | 0.34 / 0.87 ms |
| 100-operation detail construction | 2.66 ms |
| 100-operation raw path observation / UI observation refresh | 2.44 / 3.46 ms |
| 1,000-operation detail construction | 23.17 ms |
| 1,000-operation raw path observation / UI observation refresh | 23.80 / 32.56 ms |

The 1,000-operation tables remain scrollable and add no per-row `QWidget` objects. The measured local 1,000-operation observation path did not justify introducing asynchronous machinery.

## M. Native Windows UAT

Each capture used isolated temporary journal/library data and captured the actual Qt top-level HWND with `QScreen.grabWindow(window.winId())`. Captures were visually inspected at DPR 1.25 and logical DPI 96.0. The CUA session did not expose native-app controls, so interactions were verified by UI tests and the screenshots are native Qt captures rather than CUA-driven screenshots.

| State | Window | Result |
|---|---:|---|
| Empty History | 1160×800 | Reviewed; one intentional empty state. `empty.png` |
| Normal completed transaction | 1160×800 | Reviewed; compact outcome and separated sections. `completed.png` |
| Multi-operation transaction (20 operations) | 1160×800 | Reviewed; compact rows and scrollable tables. `multiple.png` |
| Undone transaction | 1160×800 | Reviewed; original rename and Undo facts remain distinct. `undone.png` |
| Missing current target | 1160×800 | Reviewed; journal success remains visible beside missing-path observation. `missing.png` |
| Unresolved/recovery | 1160×800 | Reviewed; persistent recovery banner and separate observations. `unresolved.png` |
| Technical details expanded | 1160×800 | Reviewed after scrolling to disclosure. `technical.png` |
| Wide window | 1540×900 | Reviewed; detail uses available width. `wide.png` |
| Supported minimum | 960×640 | Reviewed; master/detail remain usable, paths elide without overlap. `minimum.png` |

Screenshots are in the local ignored directory `.pytest_cache/phase7_visual/captures/`; no screenshot contains the user’s library or profile data.

Visual acceptance answers:

1. The page reads as History: time, operation count, status, journal facts, and current state lead; identifiers are disclosed only on demand.
2. Durable facts and current observations occupy separately labeled sections and tables.
3. The master remains near 248 logical pixels and is resizable within its bounds.
4. Technical IDs and raw state values are collapsed by default.
5. Twenty operations are presented in 32-logical-pixel scrollable rows rather than large cards.
6. A missing current path does not imply that the recorded rename failed.
7. At 960×640 the selected transaction’s time, count, status, journal facts, and current observation remain readable; long paths elide.

## N. Integration seams for Phase 10

- Transaction identity is the journal transaction ID; no deep-link or cross-page selection feature was added.
- History consumes `mutation_history_changed` as a journal presentation invalidation only.
- Undo visibility comes from persisted `undo_state` and per-operation `undo_status`.
- Recovery visibility comes from persisted transaction/recovery fields and the existing unresolved projection.
- Current path observation is recomputed when a transaction is selected or History is refreshed; it is not continuously watched or cached as truth.
- Selection is page-instance presentation state and survives navigation and refresh while the transaction remains available.

## O. Files changed

- `src/dlsite_organizer/domain/rename_execution.py` — add the operation count to the transaction summary projection.
- `src/dlsite_organizer/persistence/rename_journal.py` — include grouped operation counts in bounded transaction summaries.
- `src/dlsite_organizer/ui/pages/rename_history_page.py` — rebuild History around the read-only master/detail presentation.
- `tests/persistence/test_rename_journal.py` — assert summary operation counts.
- `tests/ui/test_main_window.py` — cover selection through navigation and hidden-page journal refresh.
- `tests/ui/test_rename_history_page.py` — cover fact/observation separation, selection, Undo/recovery, disclosure, empty state, and table scale.
- `docs/rename-history-phase7-report.md` — Phase 7 contract and acceptance evidence.

Scope confirmation:

| Area | Changed? |
|---|---|
| Organizer production files | No |
| RenameExecutor semantics | No |
| Undo service semantics | No |
| Journal schema | No |
| Database schema | No |
| Frozen cross-app foundation | No |

## P. Tests added or updated

History coverage includes current target disappearance without rewriting success; undone-history semantics; unresolved recovery presentation; observation failure; stable transaction identity; selection preservation on new journal entries, navigation, hidden-page refresh, and paging; first-entry newest selection; empty state; technical disclosure state/content; splitter constraints; and 10/100/1,000-operation table scaling. Persistence coverage asserts list summary counts. Existing Organizer/Undo tests remain unchanged.

## Q. Validation

| Check | Result |
|---|---|
| History/journal/Undo targeted suite | 145 passed |
| Frozen Organizer regression subset | 333 passed |
| Full native Windows suite | 1,186 passed, 0 failed, 3 warnings, 160.01 s |
| Ruff (`ruff check .`) | Passed |
| Pyright | 0 errors, 0 warnings, 0 informations |
| `compileall -q src` | Passed |
| `pip check` | No broken requirements |
| `git diff --check` | Passed |

The full-suite command was:

```powershell
.venv\Scripts\python.exe -X utf8 -m pytest -q --tb=short --basetemp=research/phase7-full-final-5
```

The targeted History/journal/Undo command was:

```powershell
.venv\Scripts\python.exe -X utf8 -m pytest tests/ui/test_rename_history_page.py tests/ui/test_main_window.py tests/services/test_rename_history.py tests/services/test_rename_executor.py tests/services/test_undo_service.py tests/services/test_undo_revalidation.py tests/services/test_undo_durability.py tests/persistence/test_rename_journal.py tests/persistence/test_undo_upgrade.py -q --tb=short --basetemp=research/phase7-history-undo-committed-tests
```

The frozen Organizer subset covered Organizer, planner, drop, executor, Undo,
revalidation, durability, append conflict, preview/model, scope, reconciliation,
final polish, and mode synchronization tests. Its final focused retry passed 333
tests.

The full suite’s three warnings came from existing single-instance subprocess tests whose reader threads reported a UTF-8 decode error on child output; all three tests passed. An earlier focused Organizer run hit the baseline pathname-rename `WinError 5`; the failing test passed in isolation, and the clean focused retry passed all 333 tests.

## R. History verdict

**PASS — RENAME HISTORY VISUAL MIGRATION COMPLETE**

Native captures were available and reviewed for all nine required states. This verdict covers Phase 7 only; user acceptance is still required before starting another phase.

## S. Commits

- `5a9b073f21637955539720e1bf5842bd126a634e` — `test: characterize rename history presentation contracts`
- History implementation and report: local commit on `main`; its SHA is included in the delivery summary because this file is part of that commit.
- No push or tag was created.

## T. Recommended next phase

`Phase 8 — Lookup Visual Migration & UX Completion` may begin only after the user accepts History. It was not started as part of Phase 7.

## U. Final repository state

The delivery summary records the final `git status --short`, `git log -16 --oneline`, and `git diff HEAD^..HEAD --stat`. Expected worktree state after the two local commits: clean.
