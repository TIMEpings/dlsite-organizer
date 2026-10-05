# Organizer Phase 6.5 acceptance evidence

## A. Baseline

- Branch: main; starting clean worktree.
- Full HEAD: 3fb21bc72f32371dd93c60a9e941a5c36171a9a1; short HEAD: 3fb21bc.
- git merge-base --is-ancestor v1.3.1 HEAD: exit 0.
- Fresh native Windows baseline: 1115 passed, 0 skipped, 99.18 s.
- Baseline command: `.\.venv\Scripts\python.exe -m pytest --basetemp=research/phase65-baseline -q`.

## B. Previous Undo behavior

OrganizerPage._show_undo_result cleared reconciliation_root/pending and called
_invalidate_preview(FILESYSTEM_CHANGED) unconditionally. That correctly prevented
old rows authorizing mutation after Undo, but also invalidated unrelated-root and
pre-mutation failed Undo. Successful Undo never scheduled a fresh scoped preview.
Characterization established these exact behaviors before production edits.

## C. Authoritative result contract

UndoResult supplies status, transaction, operations, error and derived cumulative
success_count/failed_count/pending_count. RenameOperation supplies transaction_id,
sequence, source_path, target_path, forward status, undo_status, undone_at and
errors. Forward SUCCESS plus undo SUCCESS confirms target_path -> source_path.
Failed/conflicting reversals remain FAILED/CONFLICT; unattempted reversals remain
PENDING. Undo stops at the first failure. The result operations match the journal
transaction snapshot. transaction.root provides root relevance.

UNDONE has transaction status UNDONE and undo_state SUCCESS. Deterministic partial
Undo has UNDO_PARTIAL/undo_state PARTIAL. RECOVERY_REQUIRED or unresolved undo_state
IN_PROGRESS/RECOVERY_REQUIRED blocks ordinary reconciliation. Preflight/confirmation
rejection returns FAILED without mutation. The existing Undo unit is the latest
transaction with remaining undoable operations, in reverse execution order.

Counts include earlier partial-attempt successes. The existing pre-attempt journal
snapshot supplies already successful sequences; only newly successful result facts
trigger reversal/refresh. A resumed attempt failing on its first remaining reverse
operation therefore does not refresh merely because historical successes remain.

## D. Reconciliation design

Undo result -> resolved matching snapshot -> canonical committed-root relevance ->
indexed target-to-source mapping -> linear ordered review_scope remap -> remove
only reversed Completed targets -> stale old rows -> existing RECONCILE worker ->
request/revision/generation acceptance -> fresh preview commit.

Undo-specific reconciliation origin/result are presentation-only. Keeping the
initiating snapshot separate from last_undo_result prevents an unrelated later
Undo from overwriting a failed refresh's Retry explanation. Completed summary is
updated immediately even when refresh subsequently fails. No old-row status flips.

## E. Root relevance matrix

| Undo relation to current Organizer | Behavior |
|---|---|
| Directly affects scoped member | Reverse identity in place; remove its Completed target; scoped refresh |
| Same root, off-scope member | Preserve membership; remove any reversed Completed target; refresh remaining scope |
| Different root | No current-preview invalidation/refresh; retain existing Retry context |
| No current preview | Update Undo/journal controls only |
| Empty scope | Preserve empty scope; no root rediscovery or new refresh |

## F. Scope identity matrix

| Before | Undo success | After scope |
|---|---|---|
| A' B C | A' -> A | A B C |
| A' B' C | A' -> A, B' failure | A B' C |
| A B C | D' -> D off-scope | A B C |

Positions are retained. Omitted members retain review intent; explicitly removed
members remain excluded. Manual Scan remains whole-root discovery/new session.

## G. Completed overlay

Only identities of confirmed newly reversed targets are removed, including
off-scope targets. Unrelated completed items survive later transactions and Undo.
Failed/unattempted reverse operations retain their target overlay, displayed as
Completed only if fresh planning yields matching UNCHANGED rows. Restored sources
receive real planner status. Overlay is session-local and survives hidden-page
completion; manual replacement scan/clear resets the review session.

## H. Success flow

Real sandbox Undo -> automatic scoped refresh -> fresh READY planner rows; removed
D stays absent. Feedback transitions from Undo progress to
`撤销完成，正在刷新预览…` and then `撤销已恢复 N 项，预览已刷新。`.
Partial Undo reports partial completion and remaining undoable count. The recent
transaction summary is refreshed from journal truth.

## I. Failure matrix

| Condition | Organizer result |
|---|---|
| Undo full success + refresh success | Fresh scoped preview; reversed Completed removed |
| Undo partial deterministic + refresh success | Map only actual successes; fresh statuses; report remaining Undo |
| Undo failure before mutation | Preserve preview/overlay/Retry; report failure; no new refresh |
| Undo unresolved/recovery required | Recovery banner and mutation block; no ordinary auto-refresh |
| Undo success + refresh failure | Journal success retained; old rows stale/read-only; Undo-specific persistent banner; Retry |
| Retry refresh success | Refresh already reversed preserved scope; removed members remain excluded |
| Different-root Undo | Current preview and existing reconciliation context unchanged |

Cancelled or obsolete refresh results also retain stale read-only inspection and
Retry. Settings invalidation remains authoritative. Global journal recovery still
blocks mutation even for a transaction outside the current root.

## J. Filesystem/planner truth

The existing refresh_preview_scope re-stats current paths, obtains offline test
metadata/cache through normal lookup, invokes RenamePlanner and finalizes batch
conflicts. Actual sibling occupancy sees the full root independently of review
membership. A real test restores an off-scope sibling into an in-scope row's target
and proves READY becomes CONFLICT without adding that sibling to preview.
Another deterministic spy rejects OrganizerService.preview during Undo refresh.

## K. Ownership/lifecycle

Existing request ID, preview revision, settings generation and late-result gates
remain in force. Tests reject obsolete request/revision/generation and cancelled
results. Real UndoActionWorker -> RECONCILE OrganizerWorker transition is tested.
Old preview is stale before action cleanup, so Execute/check editing cannot become
enabled against it. Hidden-page results commit once; editable next-scan input
remains independent of committed root. No foundation or signal wiring changed.

## L. Performance

Final native run: seven samples, median, full Undo identity-map construction,
ordered scope remap and Completed removal/matching, excluding state setup:

| Scope / Undo operations | Mapping + overlay |
|---|---:|
| 1,000 / 1,000 | 6.846 ms |
| 5,000 / 5,000 | 30.380 ms |

Algorithm is O(scope + operations) using dicts/sets. Canonical sandbox Undo and
refresh: 9834.493 ms end-to-end including confirmation timer, worker lifecycle,
navigation and native event settling; this is not a planner-only benchmark.
No live-network timing claims.

## M. Native Windows UAT

Windows-11-10.0.26300-SP0; native Qt platform windows; system default Qt scale;
actual Windows scaling 125%; DPR 1.25; logical DPI 96.

| Flow | Result |
|---|---|
| Successful Undo auto refresh | PASS |
| Removed-member preservation | PASS |
| Completed removal | PASS |
| Unrelated Completed preservation | PASS |
| Different-root behavior | PASS |
| Refresh-failure Retry | PASS |
| Hidden-page completion | PASS |
| Next-scan input independence | PASS |

Reproducible harness: `.\.venv\Scripts\python.exe tools/inspect_organizer_phase65.py`.
Real MainWindow, confirmation dialogs, native Qt workers, executor, UndoService,
filesystem and SQLite journal; offline metadata; new research sandbox only.
Native window accessibility/screenshots were additionally inspected through the
computer-use plugin; final captures were visually checked. Human acceptance is
pending, not replaced by scripted native evidence.

Final local evidence: `research/phase65-native-e6da7174/evidence.json`; six PNGs
cover Completed before Undo, scoped READY after Undo, unrelated Completed,
different-root, truthful refresh failure, and Retry success. Research is ignored.
No user-library mutation, DLsite requests, Explorer registration or release action.

## N. Tracked files changed

| File | Purpose |
|---|---|
| docs/organizer-phase65-design.md | Pre-production authority and transition design |
| src/dlsite_organizer/ui/state/organizer_state.py | Root relevance, indexed reversal, selective overlay, reconciliation origin/result |
| src/dlsite_organizer/ui/pages/organizer_page.py | Undo worker integration, historical-success boundary, refresh feedback/summary |
| tests/ui/test_organizer_undo_reconciliation.py | Characterization then 21 final Undo contracts |
| tests/ui/test_organizer_reconciliation.py | Undo after failed forward refresh now retries reversed scope |
| tests/ui/test_organizer_page.py | Existing Undo test expects fresh automatic reconciliation |
| tools/inspect_organizer_phase65.py | Reproducible native offline UAT and linear mapping timings |
| docs/organizer-phase65-report.md | Acceptance evidence and scope closure record |

```text
database schema changed: no
planner status enum changed: no
RenameExecutor semantics changed: no
Undo semantics changed: no
Undo journal schema changed: no
multi-root implemented: no
frozen cross-app foundation changed: no
Completed persisted: no
```

## O. Coverage

Characterization: old Undo stale path, review scope/Completed before Undo,
different-root over-invalidation, actual successful/partial/resumed/rejected
journal results. Final integration: canonical removed-member flow, partial and
resumed Undo, historical successes without new mutation, latest-transaction-only
Completed, real action worker, hidden page, independent input, off-scope refresh,
real sibling conflict, no preview, empty scope, refresh failure/Retry/full manual
rescan, failed preflight, recovery journal failure, obsolete ownership/cancel,
mismatched snapshot, unrelated Undo preserving existing partial Retry context.
Existing Phase 6.4 removal/tooltip/checkbox/conflict/forward-refresh tests pass.

## P. Validation

Final targeted command (native Windows, no live integration marker):

```powershell
.\.venv\Scripts\python.exe -m pytest tests/services/test_undo_service.py tests/services/test_undo_revalidation.py tests/services/test_undo_durability.py tests/persistence/test_rename_journal.py tests/persistence/test_undo_upgrade.py tests/ui/test_organizer_undo_reconciliation.py tests/ui/test_organizer_reconciliation.py tests/ui/test_organizer_scope.py tests/ui/test_organizer_page.py tests/ui/test_organizer_state_characterization.py tests/ui/test_organizer_preview_model.py tests/ui/test_mode_undo_sync.py -q --basetemp=research/phase65-targeted-verified
```

Result: 215 passed, 0 skipped, 34.47 s. Characterization run: 33 passed.
Final Undo-specific run: 21 passed, 5.90 s.

```powershell
.\.venv\Scripts\python.exe -m pytest --basetemp=research/phase65-full-verified -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\pyright.exe
.\.venv\Scripts\python.exe -m compileall -q src
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

Full-suite result: 1136 passed, 0 skipped, 110.30 s. Ruff passed; Pyright 0 errors,
0 warnings, 0 informations; compileall exit 0; pip check: No broken requirements
found; git diff --check exit 0. No dependencies were installed or upgraded.

## Q. Organizer verdict

IMPLEMENTATION PASS — HUMAN ACCEPTANCE PENDING

## R. Commits

41c012d780c2da8a4e5568674dab6623fd9560b9
test: characterize organizer undo reconciliation

97cbb26ec4e02c67981bafe9869cc0628b9f6411
feat: reconcile organizer preview after undo

The final report-only commit SHA is reported in the final chat after commit.
No amend, push, tag or publication.

## S. Recommended next phase

Phase 6.5.1 — Native Windows human acceptance follow-up, limited to corrections
identified during acceptance. No new correction is currently evidenced. After
explicit acceptance: Phase 7 — Rename History Visual Migration. Neither is begun.

## T. Final repository state

Post-commit git status --short, git log -14 --oneline and
git diff HEAD^..HEAD --stat are reported in the final chat. Worktree must be clean.
