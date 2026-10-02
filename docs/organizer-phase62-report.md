# Organizer Phase 6.2 — final UX polish

## A. Baseline

- Branch: `main`.
- Starting HEAD: `7dc7c00a5cb495b210604ef091dcd8fc7228d3cb` (`7dc7c00`).
- Starting working tree: clean; the requested recent commits matched.
- `git merge-base --is-ancestor v1.3.1 HEAD`: exit 0.
- Recorded native baseline: **1049 passed, 0 skipped**, from the Phase 6.1 report.
  This full baseline was not rerun before editing. The initial pre-edit Organizer
  subset returned 95 passed / 1 failed in the existing native clipboard test;
  that failure also reproduced during validation and passed in isolation.

## B. Scan hover

The global `QPushButton[visualRole="secondary"]` rule has greater selector
specificity than the generic `QPushButton:hover` rule, masking its surface change.
Scan already had the secondary role: object names, hover interception and dynamic
property repolishing were not the cause. Scan and Cancel are separate existing
buttons whose visibility changes during scanning.

Organizer-local secondary hover/pressed/focus rules reuse the existing theme's
surface-secondary, selection and focus colors. Both Scan and Cancel use them.
The global stylesheet is untouched and Execute remains primary.

| Native inspection | Result |
|---|---|
| Browse idle / hover | PASS |
| Scan idle / hover | PASS |
| Scan keyboard focus | PASS |
| Cancel hover | PASS — safely projected loading state, no scan cancellation |

Native cursor positioning confirmed `underMouse=true` for all hover captures.
The captured Browse, Scan and Cancel hover surfaces are visually consistent.

## C. Empty state

No committed preview shows one centered title, `尚未生成预览`, and one explanation:
`选择上方目录并扫描，或拖入作品目录以生成预览。`

Preview context/summary, table, detail, preview management, execution toolbar and
Execute are hidden. The permanent input drag hint is hidden in this mode as well.
A committed preview with no rows retains its existing no-items/diagnostic wording.
Recoverable Undo and unresolved journal information remain independently visible;
the no-transaction row is omitted only when no preview exists. A presentation-only
pending-Undo flag preserves the row even while its action is temporarily disabled.

Native empty capture: PASS; calm central composition, no duplicate visible empty
text or disabled zero-count execution controls.

## D. Drop target

One Organizer event filter owns drag-enter/move/leave/drop events on the page and
its existing widget descendants, including scroll viewports and detail inputs.
The table remains in `NoDragDrop` mode: Qt item transfer is not enabled. Events
are consumed by the page and routed to the unchanged `_handle_drop_paths` entry.
The former compact drop widget remains available as the public drop-zone hook.

Accepted regions: input, table viewport, empty surface, details, page background
and footer whitespace. Sidebar, title bar, Lookup and other pages have no new drop
handlers. Local-directory URL validation uses the same `local_directory_paths`;
backend root, duplicate, reparse and same-root validation remain unchanged.

A temporary translucent page overlay with the existing accent/focus border says
`松开以追加到当前预览`, or `松开以添加作品目录` for an empty preview. Leave/drop
clears it; page hiding clears it. Invalid file/remote URL drags are rejected.
The overlay allocates one QLabel and does not touch the model or column sizing.

Scan still replaces. Populated drops still append/merge; empty drops initialize
the preview using the existing handler's behavior. Existing checked states and
unchecked appended rows were verified through real offline worker scans.

## E. Bulk execution toolbar

Three 28 × 28 logical-pixel quiet buttons sit at the right of the existing summary
row, separate from Remove/Clear preview controls. App-owned cached line icons use
stacked checkbox shapes with checks, opposing arrows, or a neutral minus.
No emoji, third-party library or destructive icon is used.

| Action | Tooltip and accessible name |
|---|---|
| Select | 全选所有可执行项 |
| Invert | 反选所有可执行项 |
| Clear | 清除所有执行勾选 |

Each also describes that only execution checks change. StrongFocus, a visible
theme focus border, Tab traversal and Space activation are retained/tested.
Select/Clear remain idempotent when there is nothing to change.

## F. Bulk semantics

| Action | READY editable | Non-READY | Highlight selection |
|---|---|---|---|
| Select all | Checked | Unchanged | Unchanged |
| Invert | Checked ↔ unchecked | Unchanged | Unchanged |
| Clear | Unchecked | Unchanged | Unchanged |

Stale, busy, generation-invalid and no-executable cases disable the controls.
The model transition independently checks the existing state gate. It calls
`OrganizerState.set_checked`; there is no parallel check storage or simulated
cell clicking. A changed operation emits one bounded check-column `dataChanged`
and one `checked_changed`; an idempotent operation emits neither. No model reset.
Current index, detail, highlighted identities and preview revision are preserved.
Execute count updates synchronously through the existing page callback.

## G. Bulk performance

Native Windows 5,000-row measurements, including the themed page callback:

| Operation | Approximate elapsed |
|---|---|
| Select all 5k | 2.147 ms |
| Invert 5k | 2.132 ms |
| Clear 5k | 2.358 ms |

Clear was measured with all rows checked. No per-row widgets, resets or high-level
page callbacks were introduced. Measurements are evidence, not strict thresholds.

## H. Status selection bug

`OrganizerStatusDelegate.paint` explicitly replaced `mark_color` with
`QPalette.HighlightedText` when selected. This recolored the main semantic mark
to the dark selection text color. Removing that override preserves `_mark`'s
explicit success/blocked/neutral colors. The independent warning mark stays amber.
Background painting still precedes the semantic marks; selected text continues
using the existing readable selected-text palette.

The actual painting path was tested by capturing the colors passed to the mark
painter with selected/focus flags. Native screenshots inspected READY, CONFLICT,
warning-bearing READY and UNCHANGED before/after selection. Colors remain stable.

## I. State contract

Existing replace, append/upsert, default checks, checked-vs-highlight, stale,
request/revision/generation ownership, cancellation, Execute and Undo semantics
are unchanged. `OrganizerState` itself is unchanged. The model adds only the
dedicated bulk check transition allowed by this phase, through existing APIs.
No planner, service, journal gate, database or settings contract changed.

## J. Native Windows UAT

Host: **Windows 11 Pro**, Qt platform `windows`, native **DPR 1.25 / 125%**.
All captures and worker data use disposable ignored sandbox fixtures.

Evidence: [JSON and capture index](../research/phase62-native-c4aafdbf/evidence.json).

| Required inspection | Result |
|---|---|
| Empty state | PASS |
| Normal populated | PASS |
| Icon toolbar / hover | PASS |
| Selected status icons | PASS |
| Drag over empty state | PASS — native Qt events; physical Explorer drag NOT AVAILABLE |
| Drag over table | PASS — native Qt events; physical Explorer drag NOT AVAILABLE |
| Drag over detail | PASS — native Qt events; physical Explorer drag NOT AVAILABLE |
| Input / page / blank area drop | PASS — event tests plus real offline workers |
| Leave/cancel overlay cleanup | PASS — native leave events |
| Invalid drag | PASS — file and remote URL rejection |
| Sidebar / another page | PASS — native acceptance properties unchanged |
| Browse / Scan hover | PASS |

Physical Explorer dragging and final human visual acceptance have not been claimed.
No real user library was mutated. The separate Undo regression test renames one
pytest sandbox directory to establish a genuinely recoverable journal record.

## K. Files changed

| Tracked file | Purpose |
|---|---|
| `src/dlsite_organizer/ui/models/organizer_preview_model.py` | Coalesced bulk execution check transition |
| `src/dlsite_organizer/ui/theme/icons.py` | Three small app-owned execution icons |
| `src/dlsite_organizer/ui/pages/organizer_page.py` | Local Scan/Cancel interaction style, empty visibility, independent Undo presentation, toolbar, workspace drop routing/overlay |
| `src/dlsite_organizer/ui/delegates/organizer_delegates.py` | Preserve status mark semantic color when selected |
| `tests/ui/test_organizer_final_polish.py` | 24 offline regression cases for this phase |
| `tests/ui/test_organizer_view.py` | Replace compact-target assertions with workspace overlay/acceptance assertions |
| `tests/ui/test_organizer_visual_correction.py` | Show/focus the actual path controls before native clipboard shortcut checks |
| `tools/inspect_organizer_phase62.py` | Reproducible native capture and 5k measurements |
| `docs/organizer-phase62-report.md` | This report |

```text
backend/service files changed: none
database/schema changed: no
frozen cross-app foundation changed: none
```

The allowed icon additions extend the existing icon system without changing
navigation icons or global styling. Ignored research captures remain local.

## L. Tests

Coverage: empty/populated visibility; real Undo after no preview/clear; bulk
select/invert/clear over mixed states; stale/busy/generation/no-ready/empty gates;
accessible names/tooltips/size; keyboard activation/traversal; coalesced 5k model
notifications; highlight/current/detail/revision stability; immediate Execute
count; directory drop across six regions; append checked-state preservation;
leave cleanup; invalid drag rejection; actual selected/focus paint color inputs.
No brittle screenshot pixel comparisons or timing thresholds were added.

Native test processes were run serially for final validation because simultaneous
Qt processes contend for the Windows clipboard. The pre-existing copy shortcut
test now explicitly displays/focuses controls; no clipboard behavior was changed.

## M. Validation

Final commands use the repository `.venv` on native Windows:

```powershell
.venv/Scripts/python.exe -X utf8 -m pytest tests/ui/test_organizer_final_polish.py tests/ui/test_organizer_visual_correction.py tests/ui/test_organizer_view.py tests/ui/test_organizer_page.py tests/ui/test_organizer_preview_model.py tests/ui/test_organizer_state_characterization.py tests/ui/test_main_window.py -q --basetemp=.pytest_cache/phase62-targeted-serial
$env:PYTHONUTF8 = "1"
.venv/Scripts/python.exe -m pytest -q -o faulthandler_timeout=45 --basetemp=.pytest_cache/phase62-final-utf8-native-20261003
.venv/Scripts/python.exe -m ruff check .
.venv/Scripts/python.exe -m pyright
.venv/Scripts/python.exe -m compileall -q src
.venv/Scripts/python.exe -m pip check
git diff --check
```

| Check | Result |
|---|---|
| Targeted pytest | 130 passed, 0 skipped, 10.71 s |
| Native full pytest | 1073 passed, 0 skipped, 0 warnings, 91.55 s |
| Ruff | All checks passed |
| Pyright | 0 errors, 0 warnings, 0 informations |
| compileall | Exit 0 |
| pip check | No broken requirements found |
| git diff --check | Exit 0 |

An earlier completed full run returned 1073 passed in 91.65 s with four
subprocess decoding warnings: parent-only -X utf8 did not propagate to spawned
Python interpreters. A subsequent default-code-page diagnostic run stalled and
was stopped. The final run uses inherited PYTHONUTF8=1 so parent/child encodings
agree, plus faulthandler diagnostics. This changes only the validation process
environment; no subprocess/backend logic was edited.

No live DLsite request, Explorer registration modification, release, push, tag
or publication was performed.

## N. Visual verdict

```text
IMPLEMENTATION PASS — HUMAN VISUAL ACCEPTANCE PENDING
```

## O. Commits

- 0729c5f72744a46841c5b4ef7e9dce07b10168d6 — feat: add organizer bulk execution check transitions.
- style: finalize organizer empty and selected states — the commit containing
  page/delegate integration, tests, native tooling and this report; its exact SHA
  appears in the completion response and final repository snapshot.

Phase 6.1 commits were not amended.

## P. Recommended next phase

After final human Organizer acceptance: **Phase 7 — Rename History Visual Migration**.
No concrete remaining implementation defect was identified and no next phase was started.

## Q. Final repository state

Branch remains `main`; working tree clean after both local commits.
The outputs of `git status --short`, `git log -12 --oneline` and
`git diff HEAD^..HEAD --stat` are preserved in
[repository-final.txt](../research/phase62-native-c4aafdbf/repository-final.txt).
