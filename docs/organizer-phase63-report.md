# Organizer Phase 6.3 — reconciliation and final review controls

## A. Baseline

- Branch: `main`; clean working tree before edits.
- Starting HEAD: `0d528c2b6c42c07b3c29f404bdf865cd8609bafb` (`0d528c2`).
- Requested recent commits matched. `git merge-base --is-ancestor v1.3.1 HEAD`: exit 0.
- Native pre-edit suite: **1073 passed, 0 skipped**, 87.30 s.
- Command: `.venv\Scripts\python.exe -m pytest --basetemp=.pytest_tmp_phase63_baseline`.
  The baseline artifacts were subsequently moved, with checked absolute paths, to
  ignored `research/phase63-baseline`; final validation uses fresh ignored basetemps.

## B. Label / preview context

The input row allowed QLabel to compress horizontally beside its expanding input
and action buttons. The label now uses Fixed horizontal size policy and an explicit
minimum based on its full size hint plus 8 logical pixels. The font is unchanged.
Structural/native checks verify available width against the full Chinese text.

Current preview now has one hierarchy: `当前预览` followed by the committed full
path. The redundant basename widget was removed. The existing read-only PathDisplay
retains selection, scrolling, Ctrl+C and full-path tooltip access. The uncommitted
next-scan input remains separate and survives refresh unchanged when nonempty.

## C. Bulk check menu

One quiet 30×30 logical-pixel button uses the existing app-painted stacked checklist
icon plus dropdown affordance. The old three buttons are gone. Its menu contains
exactly:

1. 全选所有可执行项
2. 反选所有可执行项
3. 清除所有执行勾选

Actions call the existing model bulk transition. Tooltip/accessibility name is
`批量设置执行勾选`; StrongFocus, Space, Enter/Return and menu navigation are tested.
Enter is handled explicitly because native QPushButton outside a dialog did not
open its menu on Return. Busy/stale/no-preview/no-READY/unresolved/journal gates
disable the control and actions. No check logic moved into QAction callbacks.

## D. Remove-conflict root cause

Previously removal only sliced the plans. `RenamePlanner.finalize` is one-way: it
marks READY/UNCHANGED duplicate targets CONFLICT, but ignores already-CONFLICT
rows. Calling finalize on one survivor therefore cannot recover its earlier READY
classification.

The service now calls `RenamePlanner.reevaluate` after removal. Affected CONFLICT
rows with captured Work/code/proposed name are rebuilt with `plan_batch` against
one fresh sibling index, then the whole surviving batch is finalized. Other rows
retain their inputs. Only obsolete planner-generated batch/inspection diagnostics
are replaced; metadata cautions remain. No network lookup, second preview store,
UI planning rule, or manual CONFLICT→READY assignment was introduced.

## E. Remove-conflict matrix

| Scenario | Before remove | After remove |
|---|---|---|
| Duplicate target only, target absent | A/B CONFLICT | Surviving A READY, unchecked |
| Real target file or directory | CONFLICT | CONFLICT |
| Target occupied by another source | CONFLICT | CONFLICT; removing preview rows does not remove disk occupants |
| Windows case alias occupies target | CONFLICT | CONFLICT |
| Unrelated conflict / invalid target | Blocked | Real occupancy/diagnostic state preserved |
| Unrelated READY | READY, checked or unchecked | Same check state preserved |

If sibling inspection fails the planner returns CONFLICT. An unexpected service
failure marks the surviving preview stale. Execution retains independent complete
batch revalidation.

## F. Final user-facing status categories

| Category | Meaning |
|---|---|
| Completed / 本轮已完成 | Confirmed successful target identity found as UNCHANGED in the refreshed preview |
| Executable / 可执行 | Current unfinished READY plans |
| Warning / 警告 | Subset of executable READY rows containing cautions |
| Unchanged / 无需重命名 | UNCHANGED plans without the session completion overlay |
| Non-executable / 不可执行 | All other planner statuses |

Completed, Executable, ordinary Unchanged and Non-executable partition the total.
Warning overlaps Executable only; it is mutually exclusive with Non-executable,
ordinary Unchanged and Completed. Scan-skipped count remains available separately.
Completed rows paint a restrained green circle/check and are not checkable.

## G. Raw diagnostic preservation

Category/count/badge filtering does not delete domain warnings or errors. All raw
diagnostics remain available in concise tooltip/accessibility text and the full
current-row diagnostic pane. Replanning replaces obsolete conflict diagnostics
with current planner facts, preserving extra metadata cautions.

## H. Post-execution flow

Confirmed Execute → real executor/journal result → retain authoritative result and
invalidate old rows → check finalized transaction and journal gates → capture
successful canonical target identities → release action worker/thread → start a
normal scan request in RECONCILE mode on the executed committed root → fresh
filesystem scan, metadata and planning → ordinary ownership/generation acceptance
→ replace preview → project Completed overlay → leave every READY check empty.

During refresh the old table remains visible but disabled, with an explicit banner
identifying it as the execution-before preview. Success feedback is transient,
with failure/unattempted counts retained for deterministic partial/failed batches.
The current-row detail reads refreshed source/current paths, not obsolete sources.

## I. Completed overlay

Identity is `normalized_path_identity(success_operation.target_path)`: resolved
local path with Windows case normalization, matching normal preview row identity.
Matching is by a set of canonical paths, never names or row indexes. A matching
row must also be UNCHANGED; new READY/blocked planner facts take precedence.
Missing targets are logged and never fabricated as rows.

The overlay survives auto-refresh, hidden-page completion, returning to Organizer,
same-root append and second execution. New successful targets accumulate. Manual
Scan replacement acceptance, Clear Preview, different-root replacement and new
application state reset it. Failed manual replacement retains the existing review.
No DB persistence or planner/domain enum change was made.

## J. Partial / failure matrix

| Execution / refresh condition | Result |
|---|---|
| All success + refresh succeeds | Real refreshed rows; only successful matching UNCHANGED rows Completed; READY unchecked |
| Deterministic partial success + refresh succeeds | Successful targets Completed; failed/unattempted rows get new planner facts; counts remain in transient feedback/journal |
| Finalized FAILED transaction, no unresolved operations | May reconcile, without inventing successful identities |
| Confirmation cancelled | No action, scan, mutation or review reset |
| Executor rejection/error before mutation | No normal auto-refresh; escaped exception invalidates preview; journal result remains truthful |
| Unresolved / recovery required / journal uncertainty | No reconciliation; recovery/journal banner and History take priority |
| Mutation succeeds + refresh fails/cancels/is rejected | Result and journal retained; old preview stale/read-only; Retry on original root |
| Retry succeeds | Normal accepted filesystem preview; overlay retained where matchable; READY unchecked |
| Empty refreshed filesystem | Normal empty preview; no fabricated Completed rows; journal and transient success summary retained |
| Undo after accepted or failed refresh | Existing journal-driven Undo; stale preview, no automatic scan; failed forward retry context cleared |

## K. Request ownership

- RECONCILE increments the same request ID and carries the current preview revision.
- Worker snapshots pin settings; results still require current settings generation.
- Invalidation, newer request/revision and obsolete delivery reject results.
- Worker failure additionally carries request/revision through a decorated GUI slot;
  a late obsolete error cannot invalidate a newer accepted preview.
- The existing busy policy prevents R2 dispatch while R1's worker is alive; tested
  explicitly by attempting a scan during blocked reconciliation.
- Hidden-page completion commits persistent state once. Native UAT navigates to
  Lookup while the refresh is blocked and returns after accepted completion.
- Root A is taken from the committed preview/transaction, checked for canonical
  equality. Editing input to B does not redirect refresh or overwrite B.
- Worker creation failure/cancellation follows the same stale/read-only retry path.

## L. Multi-root

The review/executor/journal/Undo contract remains one committed parent root per
batch. No multi-root transaction architecture was added. Cross-root drop feedback
states the actual current root, explains that another root cannot join this batch,
and directs users to Clear Preview or scan the corresponding root. This is a
product constraint, not a fabricated filesystem safety failure. Multi-root remains
deferred product work outside this phase.

## M. Performance

Native Windows, effective DPR 1.25, offline data; medians of five samples for
presentation, three for removal:

| Measurement | 1k | 5k |
|---|---:|---:|
| Completion identity matching/classification | 0.107 ms | 0.549 ms |
| Summary derivation and widget text update | 0.213 ms | 1.000 ms |

Removing one duplicate from 1000 actual sibling directories, rechecking the
affected survivor and finalizing 999 remaining plans: **327.853 ms** median.
This includes actual planner/filesystem path work and is not a model-only removal
comparison. Completion matching is O(N) with constant-time set membership;
delegates still create zero cell widgets. Existing 5k model/bulk tests pass.

The captured first execution took 9754.008 ms including confirmation, intentionally
blocked refresh, navigation and screenshot capture. It is UAT instrumentation
time, not a production scan benchmark. No network performance claim is made.

## N. Native Windows UAT

Host: Windows 11 Pro, build 26300; Qt `windows` platform. Desktop system scaling
was **100%, DPR 1.0**. Native captures used **QT_SCALE_FACTOR=1.25**, yielding
effective window DPR 1.25 with logical DPI 96. This is native Windows rendering
and real sandbox behavior, but not evidence of system-level 125% scaling.

| Flow | Native Windows / app-scaled DPR 1.25 | System 125% / human acceptance |
|---|---|---|
| Full label at normal/minimum width | PASS | NOT AVAILABLE / pending |
| Current preview path hierarchy | PASS | NOT AVAILABLE / pending |
| Single menu and keyboard navigation | PASS | NOT AVAILABLE / pending |
| Duplicate conflict removal | PASS | NOT AVAILABLE / pending |
| Warning exclusivity | PASS | NOT AVAILABLE / pending |
| Execute → automatic refresh | PASS | NOT AVAILABLE / pending |
| Completed rows | PASS | NOT AVAILABLE / pending |
| Second batch accumulation | PASS | NOT AVAILABLE / pending |
| Manual rescan reset | PASS | NOT AVAILABLE / pending |
| Injected refresh failure and retry | PASS | NOT AVAILABLE / pending |
| Cross-root rejection | PASS (existing drop-handler input) | Physical Explorer drag NOT AVAILABLE / pending |

All mutations used the ignored disposable sandbox. Real native confirmation,
executor, journal, scan workers and navigation were exercised. The injected refresh
exception is intentional failure-path evidence. No user library, live DLsite,
Explorer registration, release or persistent runtime settings were touched.

Evidence: `research/phase63-native-f5f4ce27/evidence.json` and the 12 PNGs listed
there, including normal/minimum label, menu, removal, busy refresh, first/second
Completed batches, manual reset, failed refresh, retry and cross-root feedback.
Screenshots were visually inspected; final human acceptance remains pending.

## O. Files changed

| Tracked file | Purpose |
|---|---|
| src/dlsite_organizer/services/organizer.py | Service-owned conflict re-evaluation seam |
| src/dlsite_organizer/services/rename_planner.py | Rebuild affected conflicts with fresh siblings and finalize survivors |
| src/dlsite_organizer/ui/state/organizer_state.py | Session completion identities and reconciliation lifecycle/check defaults |
| src/dlsite_organizer/ui/models/organizer_preview_model.py | Overlay projection and acceptance of service replanning |
| src/dlsite_organizer/ui/pages/organizer_page.py | Shared request dispatch, reconciliation/retry, summary/menu/context/drop UX |
| src/dlsite_organizer/ui/workers/organizer_worker.py | Request-owned GUI failure delivery; legacy error signal retained |
| src/dlsite_organizer/ui/delegates/organizer_delegates.py | Completed circle/check and exclusive warning marker |
| src/dlsite_organizer/ui/widgets/organizer_review.py | Enter-capable menu button and completed/exclusive summary text |
| tests/ui/test_organizer_reconciliation.py | 24 offline characterization/integration cases |
| tests/ui/test_organizer_page.py | Accepted reconciliation/Undo and exclusive warning expectations |
| tests/ui/test_organizer_visual_correction.py | Removed basename and revised badge/summary expectations |
| tests/ui/test_organizer_final_polish.py | Menu transitions/gates/keyboard behavior and warning painting expectations |
| tools/inspect_organizer_phase63.py | Reproducible native sandbox captures and performance measurements |
| docs/organizer-state-contract.md | Updated reconciliation/status/state authority contract |
| docs/organizer-phase63-report.md | Phase evidence and limitations |

- Database schema changed: **no**.
- Planner domain status enum changed: **no**.
- RenameExecutor semantics changed: **no**.
- Undo semantics changed: **no** (only Organizer retry-context integration).
- Frozen cross-app foundation changed: **no**.

## P. Tests

Before production edits, three passing characterization tests established that
finalize alone cannot reverse a conflict, finalized operation/journal target data
matches real post-rename filesystem rows, and replace/append/remove/check/request
boundaries behave as expected. Existing executor/durability suites supplied the
pre-existing partial/recovery semantics.

Added integration coverage includes real action workers and two execution batches;
committed root A / input B; hidden-page acceptance; manual reset; failure and retry;
partial SUCCESS/FAILED/NOT_EXECUTED; busy scan prevention; settings/revision/request
rejection; confirmation cancellation and action error; real journal uncertainty;
disk files/directories/source occupancy/Windows aliases; ordinary UNCHANGED and
unmatched completions; append/root/new-state/clear lifetime boundaries; empty
refresh; diagnostic preservation; native-label structure and single menu; cancelled
refresh and obsolete errors; failed-refresh Undo retaining invalidation behavior.

## Q. Validation

Final commands run with the project's native Windows `.venv`:

```powershell
.venv\Scripts\python.exe -m pytest tests/ui/test_organizer_reconciliation.py tests/ui/test_organizer_page.py tests/ui/test_organizer_visual_correction.py tests/ui/test_organizer_final_polish.py tests/ui/test_organizer_preview_model.py tests/ui/test_organizer_view.py tests/ui/test_organizer_state_characterization.py tests/services/test_rename_planner.py tests/services/test_organizer.py --basetemp=research/phase63-targeted-accepted
.venv\Scripts\python.exe -m pytest --basetemp=research/phase63-full-native-accepted
.venv\Scripts\ruff.exe check .
.venv\Scripts\pyright.exe
.venv\Scripts\python.exe -m compileall -q src
.venv\Scripts\python.exe -m pip check
git diff --check
```

| Check | Final result |
|---|---|
| Targeted pytest | **178 passed**, 10.00 s |
| Full native Windows pytest | **1097 passed, 0 skipped**, 88.63 s |
| Ruff | All checks passed |
| Pyright | 0 errors, 0 warnings, 0 informations |
| compileall | Exit 0 |
| pip check | No broken requirements found |
| git diff --check | Exit 0 |

The earlier complete run passed 1096 tests, then one focused Undo integration test
and a corresponding banner/context fix were added and verified by the final suite.
Fresh basetemps remain ignored. Default pytest excludes live-network integration
tests; no test was explicitly skipped. Pyright emitted an informational available
version notice; no dependency upgrade was performed.

## R. Final Organizer verdict

**IMPLEMENTATION PASS — HUMAN VISUAL/BEHAVIOR ACCEPTANCE PENDING**

Native automated sandbox evidence exists. System-level 125% scaling and final human
visual/behavior acceptance have not been certified.

## S. Commits

1. `0c566f4` — `test: characterize organizer reconciliation boundaries`
2. `e7e30fe` — `feat: reconcile organizer preview after rename`
3. Evidence/contract commit — `docs: record organizer phase63 acceptance evidence`

The final evidence commit SHA is in the delivery message / final Git log. Phase 6.2
commits were not amended. Nothing was pushed, tagged or published.

## T. Recommended next phase

After acceptance: **Phase 7 — Rename History Visual Migration**. If human review
finds a remaining issue, do one narrowly scoped Phase 6.3.x correction. Neither
phase was started automatically.

## U. Final repository state

The delivery records `git status --short`, `git log -14 --oneline` and
`git diff HEAD^..HEAD --stat` after the final commit. Ignored sandbox/UAT artifacts
remain available locally; no untracked baseline directory remains at repository root.
