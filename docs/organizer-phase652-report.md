# Phase 6.5.2 — Append conflict reconciliation

## A. Baseline

- Branch `main`; starting HEAD `a7338b8b17b117c580882f766218ec624eebf0d1` (`a7338b8`).
- Initial `git status --short`: empty. Expected recent fourteen commits verified.
- `git merge-base --is-ancestor v1.3.1 HEAD`: exit 0.
- No repository or workspace-ancestor AGENTS.md found.
- Original native Windows tests, excluding the new regression file: 1145 passed,
  zero skipped, 151.18 seconds.

An initial original-suite attempt failed a clipboard assertion after 1008 passes
while another Qt test process was running. Concurrent Qt processes may interfere
with the shared desktop/clipboard; this is an inference, not an established root
cause. Serial baseline retry passed all 1145 tests without production edits.
Subsequent Qt tests and native UAT were run serially.

## B. Root cause

All Organizer workspace drop targets route through `eventFilter` or the input
panel's `paths_dropped` signal to `_handle_drop_paths`. DropInputService validates
the shared root and direct children. A nonempty existing review session selects
APPEND; the first drop selects REPLACE. OrganizerWorker snapshots the service and
calls `preview_paths` for the dropped subset (a root drop can scan the root).

`OrganizerService._preview_scan` performs deduplicated metadata lookup for that
subset, calls `RenamePlanner.plan_batch`, then `finalize`. `plan` and `plan_batch`
validate names and inspect actual sibling occupancy; `plan_batch` shares one lazy
sibling index. `finalize` marks eligible READY/UNCHANGED proposals sharing a
canonical target as CONFLICT, including Windows case aliases. It does not reopen
existing CONFLICT plans.

The accepted APPEND result previously went straight to
`OrganizerPreviewModel.append_preview`. Its `merge_previews` upserts source
identities in place, appends new sources, and merges lookup/scan data, but expressly
does not replan. OrganizerState then intersects old checks with the merged READY
identities. No combined finalization occurred. Thus each individually planned row
could be READY for the same absent target; their unchecked/checked presentation
was based on this inconsistent collection. Execute finally rejected the selected
duplicate batch and the existing execution result handler invalidated the preview.

Phase 6.3 Remove already calls `OrganizerService.reevaluate_preview` after removing
members. Its Planner seam rebuilds captured conflicts, then finalizes the whole
remaining batch without metadata lookup. Phase 6.4 review scope records ordered
explicit source identities independently of sibling occupancy, and scoped refresh
never rediscovers unrelated rows. These existing seams remain the authority.

## C. Old Append pipeline

```text
drop subset -> preview_paths -> lookup subset -> plan_batch subset -> finalize subset
-> ownership/generation checks -> merge unchanged existing plans with incoming plans
-> state/model commit (no complete-batch finalization)
```

## D. New Append reconciliation

```text
capture current immutable preview + service/settings snapshot + request/revision
-> worker plans/looks up only incoming subset
-> merge captured full preview by source identity
-> service.reevaluate_preview(recheck_ready=True)
-> planner.plan_batch using captured Work/code/proposed name + fresh full-root siblings
-> planner.finalize complete combined batch
-> owned result delivery + generation/cancellation/stale checks
-> one model/state commit + immediate table/summary/details/check projection
```

The existing service/Planner seam gained an optional `recheck_ready` flag. Append
rebuilds READY, UNCHANGED and CONFLICT proposals that have captured valid inputs.
Remove retains its existing default conflict-only re-evaluation. Unsupported
metadata/scan/invalid-target rows retain their actual original statuses.

Rechecking READY matters when an incoming directory has just been created at an
old proposed target: finalization alone cannot discover this source occupancy.
A dedicated failing regression was added before this adjustment. Append now
matches a fresh combined batch in that case without introducing dependency
scheduling or metadata relookup.

Ordinary Append performs this work in the existing worker, not on the GUI thread.
The result marks its combined reconciliation as complete, preventing a second
service re-evaluation on acceptance. Direct embedding/test result delivery uses
the same seam synchronously after ownership checks. The model's existing identity
merge of the complete reconciled result is presentation-only and preserves order.
No UI-owned target comparison or conflict diagnostic was introduced.

Worker errors and direct seam failures preserve the old preview atomically.
Cancelled, old-request, old-revision and old-generation results cannot commit.
An obsolete worker can only calculate against its own captured preview, never
re-finalize the newer live state. Hidden-page results commit once when still owned.

## E. Batch invariant

Every accepted current preview contains the complete review membership, with
Planner batch conflict semantics applied before execution can become available.

| Membership operation | Authority |
|---|---|
| Replace/manual scan | Service batch planning + finalize |
| Append | Captured complete merge + fresh proposal planning + finalize |
| Upsert | Same Append seam; existing identity position retained |
| Remove | Existing service conflict re-evaluation + complete finalize |
| Post-execution refresh | Fresh ordered review-scope planning + finalize |
| Post-Undo refresh | Fresh ordered remapped review-scope planning + finalize |

This is a preview invariant, not permission to mutate. External changes after a
completed preview still require execution-time preflight.

## F. Conflict matrix

| Scenario | Result |
|---|---|
| Two identical absent targets, either append order | Both CONFLICT before Execute |
| Three identical targets, appended separately | All CONFLICT; two remain blocked after one removal |
| Remove until one duplicate remains | Survivor READY, unchecked |
| Existing directory target | CONFLICT survives unrelated append/removal |
| Existing file target | Same conservative occupancy CONFLICT |
| Another source occupies target | CONFLICT; no dependency scheduling |
| Newly created appended source occupies old target | Old READY becomes CONFLICT, unchecked |
| Foo / foo / FOO proposed targets | Same outcome as normal full scan |
| Unsafe proposed component | INVALID_TARGET retained |
| Lookup failure / ambiguous code / unrelated unchanged | Actual status and raw diagnostics retained |

## G. Check-state matrix

| Transition | Check result |
|---|---|
| Existing READY remains READY | Prior checked/unchecked value preserved |
| Existing checked READY becomes CONFLICT | Unchecked, non-editable |
| New READY append | Unchecked |
| New non-executable append | Unchecked, non-editable |
| Existing CONFLICT becomes READY after upsert/removal | Unchecked |

Bulk Select All only checks editable READY rows. Metadata cautions remain raw
diagnostics on blocked rows; visible Warning category still applies only to READY.
The existing insertion and dataChanged notifications update old statuses, checks
and diagnostics immediately. No per-cell widgets or model architecture changes.
Surviving highlighted/current identities are retained, and current details are
refreshed from the authoritative new plan.

## H. Full-scan versus Append equivalence

Tests compare complete RenamePlan values (including warnings/errors/metadata)
after repeated individual drops with normal full-batch service output for identical
membership and a static filesystem. Cases include duplicate target, case alias,
directory/file occupancy, source occupancy, invalid target, unchanged, and stale
cache cautions. Reverse insertion order compares plans by stable source identity.
The newly created incoming-source case also compares against fresh combined output.

## I. Review scope / Completed

Append/upsert adds only explicit members; unrelated sibling occupancy does not
expand visible scope. Existing member positions remain, and no duplicate logical
row is created by an upsert. Removed sources remain on disk and still count for
occupancy. Completed targets and scoped execution/Undo reconciliation retain their
existing authority. Both tests and native UAT verify Completed survives a later
conflicting Append. The Phase 6.5.1 zero-mutation Undo contract is covered again by
the runtime-integrated focused tests and the full suite.

## J. Execution preflight

RenameExecutor and its preflight are unchanged. The original duplicate READY/READY
failure is characterized with independently planned proposals: FAILED, zero
mutations. The normal Organizer path now presents conflicts before Execute and
excludes them from selected plans. A separately checked valid row executes normally.

Tests and native UAT create actual target occupancy after a valid preview, then
execute: preflight rejects the entire selected batch, retains the source, and
the original execution-failure stale policy remains active. No generic suppression
of zero-mutation Execute failure invalidation was added.

## K. Performance

Final measurements from `research/phase652-perf-b1a1e8f5/evidence.json`:

| Scenario | Median |
|---|---:|
| 100 existing + append 1 | 124.892 ms |
| 1,000 existing + append 1 | 1229.598 ms |
| 5,000 existing + append 1 | 6078.991 ms |
| 100 existing + 20 individual appends, total | 2656.995 ms |

Benchmark component is
`merge_previews + OrganizerService.reevaluate_preview(recheck_ready=True)`;
five samples per case, median milliseconds. Captured plans are prepared outside
the timer over real ignored sandbox directories. The repeated workload performs
twenty complete individual reconciliations starting with 100 members.

The 5,000-row fresh filesystem check takes about six seconds on this machine;
the existing busy/cancellation gate remains active during it, while the UI stays
responsive. This cost is reported without a microsecond gate or throughput claim.

No metadata lookup occurs in this component (a lookup boundary raises if called).
Only the incoming subset is looked up by the worker. Work remains linear batch
planning/finalization per append; repeated appends necessarily revisit the growing
batch, without repeated expensive metadata lookup or a new incremental planner.
Reconciliation runs off the GUI thread; an Event-blocked regression proves the
old single-row preview stays readable and non-editable until one combined result
commits. No claim about live network latency is made.

## L. Native Windows UAT

Harness: `.\.venv\Scripts\python.exe tools/inspect_organizer_phase652.py`.
Windows-11-10.0.26300-SP0, native Qt `windows`, Windows scaling 125%, DPR 1.25,
logical DPI 96. Offline metadata, real OrganizerPage, native Qt URL-directory
drags, real worker, fresh SQLite journal and isolated ignored research sandbox.
Computer-use plugin supplied native inputs. No real library or DLsite requests.

The source list supplies Explorer-equivalent local-directory URL MIME data.
All drops enter Organizer's actual workspace drop event routing. The computer-use
drag helper needed a following target click to complete the native Qt drop; this
is tooling interaction, not a replacement test API. No resize/rescan/Execute was
needed to discover the primary conflicts. Final source-recheck implementation is
also revalidated in a fresh sandbox below.

Final implementation primary flow: `research/phase652-native-59c9c9f1`.
Append A, append conflicting B, Delete B and re-add B all PASS with the table,
summary and checks visible immediately. `evidence.json` and `state-00.png` through
`state-08.png` preserve this fresh run at the same 125% scaling / 1.25 DPR.
The earlier run supplies additional native execution, Completed and external-race
evidence; their final integrated equivalents also pass in the final test suite.
The final run's `audit.json` verifies these four ordered observed transitions,
125% / 1.25 DPR, and all three sandbox source directories unchanged.

| Flow | Result |
|---|---|
| Append A individually | READY / checked under established first-drop Replace semantics |
| Append conflicting B | Both 冲突 / unchecked; summary READY 0, blocked 2, Execute 0 |
| Remove B with Delete | A 可执行 / unchecked |
| Re-add B | Both 冲突 / unchecked immediately |
| Append independent valid row and Execute/Yes | One successful rename; automatic scoped refresh + Completed |
| Remove/re-add B during Completed session | Completed preserved; A/B conflict restored |
| Externally occupy accepted A target and Execute/Yes | Whole-batch preflight rejection; A retained; stale banner |

Initial evidence is retained under `research/phase652-native-3dce05a2`:
`evidence.json`, `audit.json` and native PNGs. Audit confirms original A/B source
directories retained, valid source renamed, and exactly one completed journal
transaction; the subsequent preflight rejection created no mutation transaction.
Native captures are agent-operated evidence; final human Organizer acceptance
remains pending. Harness windows are closed after use.

## M. Files changed / domain policy

| Tracked file | Purpose |
|---|---|
| src/dlsite_organizer/services/organizer.py | Expose optional full-proposal recheck through existing seam |
| src/dlsite_organizer/services/rename_planner.py | Reuse plan_batch/finalize for captured READY/UNCHANGED as needed |
| src/dlsite_organizer/ui/workers/organizer_worker.py | Reconcile captured Append batch off GUI thread, mark combined result |
| src/dlsite_organizer/ui/pages/organizer_page.py | Capture base preview; commit owned combined results; shared direct seam; refresh detail |
| src/dlsite_organizer/ui/state/organizer_state.py | Preserve surviving highlighted/current identities after append |
| tests/ui/test_organizer_append_conflicts.py | Human regression and complete Append conflict/ownership/check matrix |
| tests/ui/test_organizer_state_characterization.py | Back controlled upsert CONFLICT with real disk occupancy |
| tools/benchmark_organizer_append.py | Offline real-filesystem reconciliation timings |
| tools/inspect_organizer_phase652.py | Isolated interactive native drag/execute UAT and evidence |
| docs/organizer-phase652-report.md | Root cause, invariants and acceptance evidence |

```text
database schema changed: no
planner status enum changed: no
RenameExecutor semantics changed: no
Undo semantics changed: no
journal schema changed: no
multi-root implemented: no
frozen cross-app foundation changed: no
```

## N. Tests

The first exact human regression failed before any production edit: independently
drop A, check READY, drop B, then expect both CONFLICT before Execute. The initial
matrix produced 14 failures / 12 passes on the old production code. The later
new-source-occupancy regression also failed before its production correction.

The final new file contains 32 cases: canonical pair; manual scan then append;
atomic failed reconciliation; executor duplicate/race/valid execution;
three-way round trip in both orders via context/Delete; seven full-batch equivalence
cases; three-way Windows casefold; in-place upsert/unblocking/check retention;
three actual Qt drop surfaces; hidden/settings/revision/request ownership;
Completed and caution policy; four retained unrelated statuses; exactly-once
background reconciliation; stale-cache warning equivalence; newly created source
occupancy. The existing state characterization now uses a real occupied target
because a synthetic CONFLICT without an actual blocking reason is correctly
reopened by Planner reconciliation.

## O. Validation

Final targeted native command:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/services/test_organizer.py tests/services/test_organizer_cache.py tests/services/test_rename_planner.py tests/services/test_drop_input.py tests/services/test_rename_executor.py tests/services/test_undo_service.py tests/services/test_undo_revalidation.py tests/services/test_undo_durability.py tests/ui/test_organizer_append_conflicts.py tests/ui/test_organizer_page.py tests/ui/test_organizer_preview_model.py tests/ui/test_organizer_state_characterization.py tests/ui/test_organizer_scope.py tests/ui/test_organizer_reconciliation.py tests/ui/test_organizer_undo_reconciliation.py tests/ui/test_organizer_final_polish.py tests/ui/test_mode_undo_sync.py -q --tb=short --basetemp=research/phase652-targeted-occupancy-final
```

Result: 333 passed, zero skipped, 52.41 seconds, exit 0.

```powershell
.\.venv\Scripts\python.exe -m pytest -q -x --tb=short --basetemp=research/phase652-full-occupancy-final
.\.venv\Scripts\python.exe -m pytest -q -x --tb=short --basetemp=research/phase652-full-final-retry
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\pyright.exe
.\.venv\Scripts\python.exe -m compileall -q src
.\.venv\Scripts\python.exe -m pip check
git diff --check
```

Ruff passed. Pyright: 0 errors, 0 warnings, 0 informations (the installed CLI
prints a separate version-update notice; no upgrade performed). compileall passed;
pip check: No broken requirements found. git diff --check passed; Git's expected
LF-to-CRLF notices are not whitespace errors.

Earlier final runs before the source-occupancy adjustment: 332 targeted passes,
1176 full-suite passes. The new source-occupancy test first failed, then its focused
61-test run passed. The final full result below supersedes those intermediate runs.
An initial targeted command referred to nonexistent test_rename_revalidation.py
and collected no tests; the corrected actual test paths above were validated.

The first final occupancy-aware full run stopped after 180 passes on
`test_shutdown_gap_secondary_takes_lock_and_drains_request`: timeout waiting for
the new-primary.quick marker. Its sandbox retained old-primary quick markers and
new-primary startup markers. The exact cause is not established; no frozen
single-instance code was changed. An isolated fresh-basetemp run of that exact
test passed (1 passed, 2.50 seconds), followed by the complete fresh retry above.
The failed run is retained as a failed attempt, not counted as a passing suite.

Complete final retry: **1177 passed, zero skipped, 124.90 seconds, exit 0**.
No failure from the prior final attempt recurred in this complete run.

Default pytest configuration deselects live-network integration tests. Fresh
basetemp directories are ignored under research; no skip was added.
No dependencies were installed/upgraded. No release actions were taken.

## P. Verdict

PASS — APPEND CONFLICT RECONCILIATION COMPLETE

## Q. Commits

1. `3d00a0e54c3ca5dd05525718262ddffa888f41a0`
   `test: characterize organizer append conflicts` — first failing regressions.
2. `1c382a1d0f3c6c196e205f9a4bf648aa7649b50a`
   `fix: reconcile organizer conflicts after append` — implementation, complete tests and tools.
3. `docs: record organizer phase652 acceptance evidence` — this report.

Final SHAs are supplied in the final chat. No amend, push, tag or publication.

## R. Final repository state

Final chat contains verified `git status --short`, fourteen-commit log and
`git diff HEAD^..HEAD --stat`. Stop at Phase 6.5.2; do not start Phase 7 or add
Organizer features. Wait for final human acceptance.
