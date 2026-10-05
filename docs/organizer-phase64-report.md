# Organizer Phase 6.4 — scope preservation and interaction cleanup

Date: 2026-10-05 (Asia/Shanghai). Human Organizer acceptance remains pending.

## A. Baseline

- Branch: `main`.
- Starting HEAD: `caf4acfd6abbd9c18762771c6fbda2035f339b00` / `caf4acf`.
- Starting `git status --short`: empty; `v1.3.1` ancestry: exit 0.
- Native Windows baseline: **1097 passed, 0 skipped**, 97.70 seconds.
  Verified against a `git archive caf4acf` export under ignored `build`, with
  `PYTHONPATH` pinned to its original source and the existing native interop
  client copied into its expected build path. No checkout/reset/rebase occurred.
- An initial parallel Qt test attempt stalled and was stopped. An archive run
  before copying the interop client reported 1089 passed / 8 skipped; the complete
  serial baseline above supersedes it. Native UI test processes were serialized.

## B. Phase 6.3 retained behavior

Completed stays a session-local overlay on freshly planned UNCHANGED rows. Its
canonical successful-target matching and accumulation across executions remain.
Automatic reconciliation, deterministic partial success, refresh-failure stale
read-only behavior, Retry, truthful journal/Undo gates, warning/non-executable
exclusivity, and service-driven conflict re-evaluation remain.

Deliberate adjustment: automatic reconciliation and Retry now evaluate ordered
current review members instead of discovering root children. Scoped skipped
counts describe this new refresh, not a previous manual root scan. Clear remains
available when a refreshed preview has no visible rows but retained scope intent.

## C. Preview scope root cause

Phase 6.3 retained a committed root but dispatched RECONCILE with no selected
paths. The worker therefore called `OrganizerService.preview(root)`, rediscovering
all accepted root children. Removing rows only changed preview plans, so later
whole-root discovery silently reversed that review decision.

The committed root identifies the owning parent. Review scope identifies which
concrete work directories the user kept. Occupancy inspection still covers the
root; membership discovery during automatic refresh does not.

Before production edits, characterization tests reproduced removed-row return,
missing context/Delete removal, duplicate summary/work hover content, and the
integer-center checkmark mapping. Context/Delete contracts failed as expected;
the first test commit records those pending contracts with strict xfails. The
final tests contain no xfails and assert the corrected behavior.

## D. Review scope model

`OrganizerState.review_scope: dict[str, Path]` is an insertion-ordered canonical
identity-to-concrete-path mapping. It shares `preview_row_keys`' direct-child
batch normalization and fallback to `normalized_path_identity` for unusual
spellings. It does not use names, basenames, row numbers, or table widgets as
identity. Successful source-to-target replacement is a single indexed linear
pass. The state owns membership; the service owns plans/filesystem truth.

The root remains `state.preview.root_path`. Scope is session-local and survives
navigation, stale previews, and temporarily skipped members. No persistence or
second plan authority was added.

## E. Scope transition matrix

| Action | Scope effect |
|---|---|
| Manual Scan | Replace with accepted plan members in scanner order; clear Completed/retry |
| Append | Same-root canonical upsert; retain existing positions/checks; append new unchecked members |
| Remove | Remove highlighted identities; close gaps; preserve surviving order/checks |
| Successful rename | Replace only confirmed successful source identity/path with target in place |
| Failed rename | Retain source identity/path; likewise unattempted operations |
| Auto reconcile | Freshly evaluate only supplied members in scope order; checks reset |
| Manual Rescan | Explicit whole-root rediscovery creates a new scope and clears Completed |
| Clear Preview | Clear visible rows, scope, Completed and retry; journal/Undo unchanged |
| App restart | New empty state; no scope persistence |

## F. Canonical A/B/C/D/E acceptance fixture

Native sandbox: `research/phase64-native-93fb497d/ABCDE`.

```text
root initial: A RJ000001, B RJ000002, C RJ000003, D RJ000004, E RJ000005
manual scan:  A B C D E
remove D/E:  A B C
execute:     A -> [测试社团][RJ000001] 夜间书房・静かな時間 (= A')
automatic:   A' Completed, B READY, C READY
manual scan: A' B C D E (normal scanner name order)
```

Automatic refresh contains exactly three members. D/E remain directories on disk.
Manual rescan explicitly restores five rows and clears the Completed overlay.
Regression tests additionally exercise refresh failure/Retry, a second execution
of B, explicit re-append of D, navigation, independent next-input root, and Clear.

## G. Filesystem truth

`OrganizerWorker` captures immutable ordered members and an ordinary service
snapshot. `OrganizerService.refresh_preview_scope` reuses explicit-path scanning,
direct-child/root/link validation, metadata lookup, Planner batch evaluation and
fresh finalization. `FolderScanner.scan_paths(preserve_order=True)` avoids changing
the established ordering of ordinary drop/Quick Rename scans.

Planner still builds its real sibling index for occupancy. A real unscoped target
directory is tested to block a scoped source. Unrelated siblings never become
preview candidates. Cross-root members raise the existing scan failure. No
filesystem/planner evaluation was moved into OrganizerPage; no discovery fallback
or manual editing of old plan statuses was introduced.

Missing/invalid members follow existing scanner semantics: omitted visible rows
plus per-member `scan.skipped` diagnostics and a fresh skipped count. Their scope
intent is retained until explicit replacement/removal/Clear. They are never
replaced by unrelated root children. An empty refreshed list still allows Clear.

## H. Partial success, refresh failure and ownership

For A success / B failure / C unattempted, scope becomes A' B C in place. Only
confirmed SUCCESS operations are mapped. Existing deterministic failure fixtures
now assert exact scope paths as well as fresh statuses.

A refresh failure retains mapped scope, old stale/read-only rows, completion
facts, journal truth and Undo. Retry uses the same ordered mapped members and
committed root, regardless of editable next-input text. Failed/cancelled refresh,
late request/revision delivery and settings-generation changes retain existing
ownership rejection. Tests assert rejected results cannot change the scope.
Hidden-page results still commit through the normal gates; navigation preserves
the persistent state. Undo retains its previous invalidation-only contract.

## I. Preview removal UX

The bottom button, one-action context menu and table `QAction` for Delete invoke
the same `remove_selected` workflow, including existing service re-evaluation.
Right-clicking an unselected row makes it current and selects that row. Clicking
inside a selected group preserves the group, updating current/focus. Blank space
opens no menu and does not clear the previous selection.

Delete uses `WidgetShortcut` on the table. Root input, selectable source-path text
and unrelated controls cannot trigger removal. It acts on highlighted identities,
independent of execution checks. Wording is “从预览移除”, with the native Del hint,
without danger styling or filesystem deletion. Tests cover all three entries,
multi-selection, blank area, input/path focus, conflict reopening and directories
remaining on disk. Native SendInput coordinate right-click also opened the menu.

## J. Checkmark geometry and DPI

The outer indicator and hit area were already centered and remain unchanged.
Old painting reused `paint_status_mark` in a twice-inset integer `QRect`; its even
size center rounded toward upper-left. The shared tick points (-5,0), (-1,4),
(6,-4) also had horizontally asymmetric bounds. At the tested 16x16 indicator,
the old transform combined a one-pixel integer-center shift with a partial
horizontal compensation from the asymmetric glyph.

New painting uses `QRectF(indicator)` directly, normalized points (0.23,0.49),
(0.43,0.70), (0.77,0.30), and a 1.7 logical-pixel round-cap/round-join vector stroke.
Bounds center at (0.5,0.5); the round stroke expands bounds symmetrically. Native
indicator size: **16x16 logical pixels**; tick bounds before stroke: **8.64x6.4**.
No checkbox input semantics, painter bitmap, outer offsets, or per-row widgets changed.

Actual native environment: **Windows 11 10.0.26300, Qt windows platform,
system-default scaling 125%, DPR 1.25, logical DPI 96**, no QT_SCALE_FACTOR override.
Checked, unchecked, checked+selected/current/focus and disabled-checked screenshots
were inspected. Centering appears corrected; final human visual acceptance is
pending. **100%: NOT TESTED** in this phase. Phase 6.3's recorded native run was
100% / DPR 1.0; it is not relabeled as 125%.

## K. Tooltip policy

```text
summary duplicate tooltips: removed
visible status duplicate tooltips: removed
icon-only useful tooltips: retained
elided text full-value tooltips: retained
additional diagnostics: retained where useful
```

Name cells expose complete names only when the hovered cell's actual style text
rectangle elides them. Work cells check both painted lines, including bold code
font/padding, and expose code/title rather than a metadata dump. Root/detail paths
and secondary labels update hints for current available width. Diagnostics remain
concise in hover and complete in the detail pane. Clear/recovery button explanations
remain where they add meaning. Accessibility names/descriptions and status text
remain independent of hover hints.

## L. Tooltip audit

Audited summary, committed preview root, status/check columns, work/current/target
columns, bulk icon control, visible action buttons, detail title/metadata/paths,
diagnostics and recent-transaction region. Summary has no hover echo. Plain status
has no echo. Fully fitting work/name values return no delegate tooltip; elided
values do. Path selection/copy remains. The bulk icon retains both useful tooltip
and accessible name/description. Detail content and useful transaction diagnostics
remain. Hover only measures the current cell, with no whole-model pass.

## M. Performance

Offline native probe, median of 7 mapping samples, all members successfully mapped:

| Membership | Mapping time |
|---|---:|
| 1,000 | 2.961 ms |
| 5,000 | 14.878 ms |

This isolates `remap_review_scope`, including target identity generation, from
filesystem/network/planner work. Five times the members took about five times the
time. Real 1,000-sibling/999-retained-row remove plus service re-evaluate/finalize
and page updates: **320.931 ms**, median of 3 samples; Phase 6.3 reference 327.853 ms.
The probe is comparable, not a machine-independent performance guarantee.
No material table regression observed: QTableView, fixed columns, vector delegates,
identity sets, no per-row widgets and no resizeColumnsToContents remain.

## N. Native Windows UAT

Evidence: [native JSON](H:/GitProjects/dlsite-organizer/research/phase64-native-93fb497d/evidence.json).
All mutations used newly created ignored research directories and a sandbox journal.
The metadata provider was offline; the shared provider rejects live requests.

| Flow | Result |
|---|---|
| Checkmark centering | Inspected at native 125% / DPR 1.25; human acceptance pending |
| Right-click remove | PASS, native Qt menu action; actual mouse menu opening also inspected |
| Multi-select remove | PASS, selected group retained and removed together |
| Delete remove | PASS, highlighted preview rows only; disk member count unchanged |
| Scope preservation | PASS, A' B C exactly |
| Explicit rescan rediscovery | PASS, five members return |
| Tooltip audit | Policy/geometry checks PASS; native regions inspected; human hover acceptance pending |

Screenshots:

- [Checkbox states](H:/GitProjects/dlsite-organizer/research/phase64-native-93fb497d/01-checkbox-normal-selected-focus.png)
- [Disabled checks](H:/GitProjects/dlsite-organizer/research/phase64-native-93fb497d/02-checkbox-disabled-checked.png)
- [Multi-selection](H:/GitProjects/dlsite-organizer/research/phase64-native-93fb497d/04-context-multiple.png)
- [Context menu](H:/GitProjects/dlsite-organizer/research/phase64-native-93fb497d/menu.png)
- [Canonical scoped result](H:/GitProjects/dlsite-organizer/research/phase64-native-93fb497d/05-canonical-scoped-completed.png)
- [Manual rediscovery](H:/GitProjects/dlsite-organizer/research/phase64-native-93fb497d/06-explicit-rescan.png)

The first probe completed/saved evidence but stdout failed on a GBK-only Japanese
character. The tool now writes Unicode JSON to disk and ASCII-escaped stdout; its
final run exited 0. Physical Explorer dragging was not reopened in this phase.

## O. Every tracked file changed

| File | Purpose |
|---|---|
| src/dlsite_organizer/ui/state/organizer_state.py | Ordered scope ownership, lifecycle and successful identity replacement |
| src/dlsite_organizer/ui/models/organizer_preview_model.py | Sparse removal scope cleanup and concise tooltip candidate values |
| src/dlsite_organizer/ui/pages/organizer_page.py | Scoped worker coordination, shared removal action/menu, selection/focus behavior, redundant hint cleanup |
| src/dlsite_organizer/ui/workers/organizer_worker.py | Reconciliation dispatch through normal worker/snapshot/result ownership |
| src/dlsite_organizer/services/organizer.py | Purpose-specific refresh_preview_scope seam |
| src/dlsite_organizer/services/folder_scanner.py | Opt-in explicit-member input order without changing default scans |
| src/dlsite_organizer/ui/delegates/organizer_delegates.py | Floating-point checkmark and current-cell elision hover behavior |
| src/dlsite_organizer/ui/widgets/organizer_review.py | Summary, label, and selectable-path tooltip hygiene; accessibility retained |
| tests/ui/test_organizer_scope.py | 18 new offline scope/interaction/geometry/elision cases |
| tests/ui/test_organizer_reconciliation.py | Common scan-control seam, exact partial/ownership/missing-scope assertions |
| tests/ui/test_organizer_preview_model.py | Updated work/name tooltip candidate contract |
| tests/ui/test_organizer_visual_correction.py | Summary counts remain accessible without duplicate hover |
| tools/inspect_organizer_phase64.py | Reproducible native offline UAT, screenshots, actual DPI and performance probe |
| docs/organizer-state-contract.md | Phase 6.4 ownership, scoped refresh and interaction contract |
| docs/organizer-phase64-report.md | Acceptance evidence and limitations |

```text
database schema changed: no
planner status enum changed: no
RenameExecutor semantics changed: no
Undo semantics changed: no
multi-root implemented: no
frozen cross-app foundation changed: no
Completed persisted: no
```

## P. Tests

New cases cover removed-member exclusion, every removal entry point with conflict
re-evaluation and no disk deletion, normal status/summary accessibility, centered
tick bounds at 13/14/18/20 logical sizes, canonical successful/failed-refresh scope,
second execution/Completed accumulation, explicit append and manual rediscovery,
clear/restart, real unscoped occupancy, missing diagnostic, cross-root rejection,
successful position mapping and sparse resets, right-click selection/blank area,
root/path Delete focus safety, per-cell elision for three columns, and responsive
label/path tooltip policy. Existing partial-success, generation/revision/request,
cancel/retry, hidden-page, journal uncertainty and Undo tests were retained/strengthened.

## Q. Validation

Targeted native pytest (including cross-mode Undo synchronization): **193 passed,
0 skipped**, 19.33 seconds. The previous Organizer-only run passed 187 tests.

```powershell
.\.venv\Scripts\python.exe -m pytest tests/ui/test_mode_undo_sync.py tests/ui/test_organizer_scope.py tests/ui/test_organizer_reconciliation.py tests/ui/test_organizer_preview_model.py tests/ui/test_organizer_visual_correction.py tests/ui/test_organizer_final_polish.py tests/ui/test_organizer_page.py tests/ui/test_organizer_view.py tests/ui/test_organizer_state_characterization.py tests/services/test_organizer.py tests/services/test_folder_scanner.py -o faulthandler_timeout=30 --basetemp build/pytest-phase64-targeted-acceptance
```

Final full native pytest: **1115 passed, 0 skipped**, 102.47 seconds, Windows Python
3.12.10 / pytest 9.1.1, fresh ignored `build/pytest-phase64-final3`. Other checks:
Ruff `check .`: PASS; Pyright: 0 errors/0 warnings/0 informations; compileall `-q src`:
exit 0; pip check: no broken requirements; git diff --check: PASS.

```powershell
.\.venv\Scripts\python.exe -m pytest -o faulthandler_timeout=30 --basetemp build/pytest-phase64-final3
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\pyright.exe
.\.venv\Scripts\python.exe -m compileall -q src
.\.venv\Scripts\python.exe -m pip check
git diff --check
.\.venv\Scripts\python.exe tools/inspect_organizer_phase64.py
```

The first final full run found one regression (1114 passed / 1 failed): responsive
label elision updates cleared the useful recent-transaction ID tooltip. A small
Organizer-specific `set_extra_tooltip` now keeps additional diagnostics across
resize, independently of duplicate/elision hints; existing cross-mode Undo tests
and a new resize assertion verify it. No cross-app foundation files were changed.

The next full run passed all Organizer/Undo checks but hit the existing subprocess
fixture race in `test_durable_request_survives_startup_beyond_old_deadline` (1114
passed / 1 failed): `subprocess_helper.tracked_rename` writes the quick marker
before writing quick-count, and the assertion observed an empty count during its
write. Its isolated rerun passed (1 passed, 6.87 seconds). No frozen foundation
or unrelated fixture was changed. The final fresh full rerun below supersedes
that result; the observed fixture timing race remains a validation limitation.

## R. Final Organizer verdict

IMPLEMENTATION PASS — HUMAN ACCEPTANCE PENDING

## S. Commits

```text
71cc5a0bb6c47e58d33900cb6b4ea0becf08afc1 test: characterize organizer preview scope
1bb8b371493559453170e5228c8b0b43808ada40 fix: preserve organizer scope during reconciliation
HEAD feat: add organizer preview removal shortcuts
```

The report is included in the last interaction/acceptance commit; its exact SHA
and final repository output are delivered with the final chat response and the
local repository evidence linked below. Phase 6.3 was not amended.

## T. Recommended next phase

Await final human acceptance. Pending that, the one narrow follow-up is
**Phase 6.4.1 — Organizer human visual acceptance and confirmed visual corrections**.
If accepted, the next implementation phase is **Phase 7 — Rename History Visual
Migration**. Neither phase has been started.

## U. Final repository state

Final status, `git log -14 --oneline`, and `git diff HEAD^..HEAD --stat` are saved
after the final commit in
[repository evidence](H:/GitProjects/dlsite-organizer/research/phase64-final-repository.txt)
and linked in the final response. Final branch is main; `git status --short` is
empty after the three focused commits. No push, tag, publishing, release, Explorer registration,
or real-library mutation was performed.
