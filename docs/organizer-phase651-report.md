# Phase 6.5.1 — Undo pre-mutation failure invalidation fix

## A. Baseline

- Branch: `main`; starting worktree clean.
- HEAD: `85bf19c21b32bc0058fb82d811c72e7cad723f23`.
- Subject: `docs: record organizer phase65 acceptance evidence`.
- Phase 6.5 implementation: `97cbb26`.
- `git merge-base --is-ancestor v1.3.1 85bf19c`: exit 0.
- No AGENTS.md found in the repository or its workspace ancestors.

## B. Exact root cause

`UndoService._undo` persists `mark_undo_conflict` for preflight-rejected operations
and calls `_notify_mutation_history_changed`. This notification truthfully means
journal state changed; it does not establish that a directory was renamed.
`RuntimeSignals.notify_mutation_history_changed` emits the shared Qt signal.
`MainWindow.__init__` connects it to `OrganizerPage.mark_filesystem_changed`, which
previously unconditionally invalidated any existing preview with
`PreviewStaleReason.FILESYSTEM_CHANGED`.

Thus real application wiring invalidated the preview before the queued FAILED
UndoResult arrived. `_reconcile_undo_result` correctly scheduled no refresh for
this result, but did not undo the stale state already introduced by the generic
notification. `_clear_feedback` only clears the transient text; its 6500 ms timer
exposed the persistent stale banner, rather than creating the invalidation.

Cause: a generic journal-change signal incorrectly treated as filesystem mutation.
The authoritative result classification itself was sufficient. Existing Phase 6.5
direct-result tests and native harness omitted the runtime notification wiring,
so they could not exercise this path.

The narrow fix keeps shared journal notifications updating controls while this
Organizer owns an active Undo. Its existing authoritative result handler decides
invalidation/reconciliation. No stale-state restoration or message-string inference
is used. Escaped action exceptions retain the existing conservative stale behavior.
Other shared-mutation handling and cross-window foundations retain their contract.

## C. Undo result classification used

The existing contract supplies result status, transaction root, journal operation
snapshot, per-operation forward status and undo status, and unresolved transaction
state. The initiating snapshot's successful sequence set excludes earlier partial
attempts from the current attempt's mutation facts.

- Zero new mutations: FAILED preflight/confirmation/intent rejection, or a resolved
  UNDO_PARTIAL with no newly successful reversals. Cancellation of the confirmation
  dialog starts no worker and changes no preview.
- Deterministic mutations: resolved UNDONE/UNDO_PARTIAL, matching result and journal
  snapshots, with forward SUCCESS + undo SUCCESS sequences absent from the
  pre-attempt successful sequence set. Existing code maps only those target paths
  back to source paths and removes only their Completed identities.
- Uncertain/unresolved: RECOVERY_REQUIRED or unresolved transaction state,
  including undo IN_PROGRESS/RECOVERY_REQUIRED. Recovery controls remain highest
  priority, and ordinary reconciliation is blocked.
- Relevance: canonical transaction root must equal committed preview root.

Cumulative success_count alone is not a current-attempt mutation classification.
No UndoService, durability, preflight, schema, planner or executor edits were needed.

## D. Before/after state-transition matrix

All rows assume an initially CURRENT preview and Organizer-owned Undo with actual
shared runtime notifications. Preserved Phase 6.5 result semantics are included.

| Outcome | Before this fix | After this fix |
|---|---|---|
| A. Preflight rejection, zero mutations | Journal conflict signal -> FILESYSTEM_CHANGED stale; no refresh; stale survives feedback expiry | CURRENT; unchanged rows/checks/scope/eligibility; truthful transient failure; no refresh/invalidation |
| B. Cancel before mutation | CURRENT | CURRENT; no action worker |
| C. Full success | Generic invalidation, then scoped reconciliation | Result-confirmed reversal/Completed update -> scoped reconciliation -> CURRENT |
| D. Partial with new successful operation(s) | Generic invalidation, then successful-only reconciliation | Same successful-only mapping and scoped refresh; failed/unattempted identities retained |
| Partial with zero new successes, including resumed attempt | Generic journal signal -> stale despite zero new changes | CURRENT; historical successes do not trigger a refresh |
| E. Mutation succeeds, refresh fails | Stale/read-only, preserved scope, Retry Refresh | Same; truthful Undo result retained, Retry Refresh available |
| F. Recovery/unresolved | Recovery gate/banner, no ordinary refresh | Same highest-priority gate/banner; uncertain mutation remains protected |
| G. Different-root Undo | Generic shared signal could stale unrelated current preview | CURRENT; no refresh or invalidation from the owned Undo |

## E. Regression tests

Added nine parametrized native Qt integration cases to `test_mode_undo_sync.py`.
They construct MainWindow and LightweightWindow with actual RuntimeSignals and
real callback-bearing RenameExecutor/UndoService instances, SQLite journal and
fresh sandbox directories. Undo is invoked through the Organizer button and real
RenameActionWorker; only dialog responses and deterministic failure seams are
controlled.

Preflight rejection produces real source occupancy before mutation, verifies
FAILED + zero undo successes + CONFLICT journal facts and truthful failure text,
then processes Qt events through the actual feedback timer expiry (up to 8 seconds).
It compares preview object, plans, row keys, mixed checked/unchecked state, scope,
request/revision and Execute eligibility before and after. Invalidation and scoped
refresh spies must remain empty, and StatusBanner must remain hidden.

Other cases: cancellation; first reverse failure with zero success; resumed partial
attempt with historical success but zero new success; full success; deterministic
partial success; refresh failure; journal failure after filesystem mutation requiring
recovery; different-root successful Undo. Existing Undo reconciliation tests cover
Completed updates, preserved removed members, Retry success, ownership and lifecycle.

Before the production edit, the runtime-integrated preflight, first-failure,
resumed-failure and different-root assertions reproduced the erroneous stale state.
The final runtime file run passed all 15 tests (9 new + 6 existing) in 13.27 seconds.

## F. Native Windows reproduction

Harness: `.\.venv\Scripts\python.exe tools/inspect_organizer_phase651.py`.
Environment: Windows-11-10.0.26300-SP0; Qt native `windows` platform.
Uses offline metadata and a fresh ignored `research/phase651-native-675e35f4` sandbox.
Real MainWindow, runtime signals, Undo worker, filesystem and SQLite journal.
No user library or network service was involved.

Using the computer-use plugin, clicked Undo and its Yes confirmation with a valid
three-row preview visible and a historical source-path occupancy conflict. Observed
`撤销前验证失败：整批未执行，请处理冲突后重试。` while Execute remained enabled.
Waited for the actual timer to clear feedback: 6.468 seconds measured after worker
completion (Qt coarse timer; configured interval remains 6500 ms). Rows, checks,
preview validity and interactivity were unchanged. The captured failure frame and
post-expiry frame contain no filesystem-change stale banner. Instrumentation recorded
zero invalidations and zero refreshes throughout this failed attempt.

Resolved only the controlled empty sandbox conflict, clicked Undo/Yes again and
verified UNDONE, exactly one automatic scoped refresh, the target -> source reversal,
CURRENT fresh READY rows and continued exclusion of the fourth member.

Results: preflight reproduction PASS; successful Undo reconciliation PASS.
This is agent-operated native evidence, not a claim of subsequent human acceptance.

Local evidence: `research/phase651-native-675e35f4/evidence.json` and four PNGs:

1. `01-current-before-undo.png`
2. `02-preflight-failure-current.png`
3. `03-feedback-cleared-current.png`
4. `04-success-scoped-reconciliation.png`

The harness window was closed after completion; sandbox evidence is retained.

## G. Validation

Targeted native Windows command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/services/test_undo_service.py tests/services/test_undo_revalidation.py tests/services/test_undo_durability.py tests/persistence/test_rename_journal.py tests/persistence/test_undo_upgrade.py tests/ui/test_organizer_undo_reconciliation.py tests/ui/test_organizer_reconciliation.py tests/ui/test_organizer_scope.py tests/ui/test_organizer_page.py tests/ui/test_organizer_state_characterization.py tests/ui/test_organizer_preview_model.py tests/ui/test_mode_undo_sync.py -q --basetemp=research/phase651-targeted-final
```

Result: 224 passed, zero skipped, 58.38 seconds.

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=research/phase651-full-final
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\pyright.exe
.\.venv\Scripts\python.exe -m compileall -q src tests tools
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

The initial full-suite process aborted near runtime-settings tests with Windows
heap exception `0xc0000374` after an earlier failure indicator; it produced no
complete failure summary. The Organizer/page and runtime-settings files then passed
in isolation: 49 passed in 5.80 seconds. No production edits were made to those areas.
The complete retry used a new ignored basetemp:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -x --tb=short --basetemp=research/phase651-full-retry
```

Result: 1145 passed, zero skipped, 118.21 seconds, process exit 0. The first native
process failure was not reproduced on the complete retry; its underlying cause
has not been established. It is retained here rather than counted as a passing run.
All basetemp directories were fresh and ignored. Default pytest configuration
excludes live-network integration
tests; no tests were otherwise skipped by this change.
Ruff: passed. Pyright: 0 errors, 0 warnings, 0 informations.
compileall: exit 0. pip check: No broken requirements found.
git diff --check: exit 0. No dependencies installed or upgraded.

## H. Files changed

| File | Purpose |
|---|---|
| src/dlsite_organizer/ui/pages/organizer_page.py | Defer owned Undo journal notifications to authoritative result; refresh gates normally |
| tests/ui/test_mode_undo_sync.py | Runtime-integrated matrix and real timer regression |
| tools/inspect_organizer_phase651.py | Interactive, isolated offline native reproduction and evidence |
| docs/organizer-phase651-report.md | Root cause, contract, state matrix and acceptance record |

Undo durability/preflight, journal/database schema, RenameExecutor, planner statuses,
review-scope and Completed semantics, Organizer layout and History UI are unchanged.

## I. Commit

Subject: `fix: preserve organizer preview on zero-mutation undo`.
The complete commit SHA is returned in the final chat, since this report is part
of that same commit. No amend, push, tag or publication.

## J. Final repository state

Work committed on `main`; post-commit status and SHA are verified and returned in
the final chat. Ignored native UAT and pytest evidence remains under `research`.
STOP at Phase 6.5.1; Phase 7 is not started.

## Verdict

PASS — UNDO INVALIDATION FIX COMPLETE
