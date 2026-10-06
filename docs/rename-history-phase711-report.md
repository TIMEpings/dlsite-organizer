# Phase 7.1.1 — History master status scannability

## A. Baseline

- Branch `main`, clean worktree; starting HEAD `d99b0960bec51035fa16d1f87ed5abfd41f59414`.
- All requested Git baseline checks matched; `v1.3.1` ancestor check exited 0.
- Previous phase's recorded native baseline: 1,191 passed, zero failed, three warnings. This was not a new pre-edit full-suite run.

## B. Actual status model

The master uses `RenameTransactionSummary.display_status`, with exactly seven possible `TransactionStatus` values. Unresolved Undo (`IN_PROGRESS` or `RECOVERY_REQUIRED`) projects to transaction `RECOVERY_REQUIRED`. Forward and legacy transaction statuses otherwise retain their original mapping. Precondition failure, rejection and individual Undo failure are operation outcomes, not additional master categories. No new durable distinctions were introduced.

This mapping was documented in the implementation update before production edits.

## C. Visual mapping

| Durable state | Icon/shape | Color category | Visible text |
|---|---|---|---|
| `pending` | Outlined clock | `text_secondary` | 进行中 |
| `completed` | Check | `success` | 已完成 |
| `partial` | Half-filled circle | `warning` | 部分完成 |
| `failed` | Cross | `error` | 失败 |
| `undone` | Return arrow | `text_secondary` (readable slate) | 已撤销 |
| `undo_partial` | Half-filled circle | `warning` | 部分撤销 |
| `recovery_required` | Warning triangle | `warning` | 需要检查 |

All marks are 14 logical pixels, with 4 pixels before the text, using rounded 1.5-pixel vector strokes consistent with the existing app-owned icon painter. Existing assets cover navigation and execution selection, not this set of semantic status marks, so the small painter is History-specific. No emoji, fallback-font symbols, external icon library or palette additions. Only status ink is colored; the count stays secondary and the timestamp stays primary/bold. No pills, extra row data, search, filters or legend.

## D. Selection / hover / focus

The delegate paints status color independently of the selected palette. Normal, hovered and selected/focused render tests preserve the same solid semantic stroke/text interiors. Selection retains its existing soft surface and blue left stripe; hover retains its quieter tint; keyboard focus retains its bottom underline. The stripe still means selection only. Undone uses the readable `text_secondary` token, never disabled text.

## E. Accessibility

Display and accessible labels are unchanged. Example native fixture row: `2026-10-06 09:10:00，1 项，已撤销`. Both AccessibleTextRole and AccessibleDescriptionRole retain full timestamp, count and precise mapped status. Graphic names are not announced. No redundant tooltip was added; visible status text may elide only if the available width is insufficient, while accessible text remains complete.

## F. DPI / native UAT

Actual native Windows scaling **125%**, DPR **1.25**, logical DPI **96.0**. No scale override. 100% was not tested. Captures use the actual themed MainWindow and `QScreen.grabWindow(window.winId())`.

| Window | Logical size | Physical capture | Master width | Row height |
|---|---|---|---|---|
| Normal | 1160×800 | 1450×1000 | 248 | 58 |
| Wide | 1540×900 | 1925×1125 | 248 | 58 |
| Minimum | 960×640 | 1200×800 | 248 | 58 |

All five captures were visually inspected: complete status marks and text, aligned secondary lines, no master clipping, unchanged density, clear selection/focus. Status marks do not scale with pane width. Detail remains the previous implementation.

## G. Mixed-status screenshot

Native evidence is preserved locally under ignored `.pytest_cache/phase711_visual/`:

- `normal_completed.png`: completed selected; recovery, completed, undone, partial and failed simultaneously visible.
- `normal_undone.png`: undone selected, completed immediately above it.
- `normal_recovery.png`: recovery selected.
- `wide.png` and `minimum.png`: mixed list at the required sizes.
- `metrics.json`: actual display and geometry measurements.

Reproduce with `.venv/Scripts/python.exe -X utf8 -m tools.inspect_history_phase711`.

The fixture uses isolated SQLite and real supported journal transitions, with deterministic recorded times: operation success/completion, successful Undo, one success then failure/partial, failure, and recovery intent. It creates five actual durable categories safely. Pending and partial Undo are covered by semantic/render tests; they are not claimed as captured native states. Creating another transaction after pending/recovery would violate the journal's unresolved-work guard, which is preserved.

## H. Performance

Warmed median 50-row population: **0.4988 ms** (nine measured samples after three warmups), against Phase 7.1's approximately **0.47 ms** reference. No strict benchmark gate or claim of speed improvement. Existing list/model/delegate architecture remains; no QWidget per master row.

## I. Frozen contracts

History detail architecture, journal facts, current filesystem observation, transaction identity, selection persistence, refresh/hidden-page updates, Undo/recovery semantics and Organizer are unchanged. Status metadata derives solely from the existing durable summary projection. Filesystem observations never drive master status. Existing History/service/journal/Undo/executor/main-window regression coverage passed.

## J. Files changed / hard guard

- `src/dlsite_organizer/ui/pages/rename_history_page.py`
- `tests/ui/test_history_master_status.py`
- `tools/inspect_history_phase711.py`
- `docs/rename-history-phase711-report.md`

| Frozen area changed? | Answer |
|---|---|
| Domain files | no |
| Persistence files | no |
| Organizer production files | no |
| Right-side History architecture | no |
| Journal schema | no |
| Database schema | no |
| Undo semantics | no |

Production diff is limited to master item metadata and its delegate mapping/vector painting. Final delivery verifies `git diff --name-only d99b0960bec51035fa16d1f87ed5abfd41f59414..HEAD`.

## K. Tests

Sixteen new cases: seven status labels/categories/accessible labels/58px density/no widgets/no tooltip plus actual normal-hover-selected-focus rendering, seven DPR 1.25 mark-boundary cases, and two unresolved Undo projection cases. Rendering tests check semantic ink separately in icon and text regions; no whole-window pixel snapshots.

The first test commit preceded production edits and failed nine cases on the baseline due to missing visual metadata/mapping. Existing textual/accessibility contracts already passed those assertions. A new boundary test initially treated fractional pixel coverage as out-of-bounds; correcting its device-pixel boundary rounding made the test match actual antialiasing without changing production geometry. Initial Pyright test QApplication inference was fixed by using the widget palette.

## L. Validation

| Check | Result |
|---|---|
| Targeted History presentation (after formatting) | 51 passed, 10.40 s |
| History semantic regression | 145 passed, 31.24 s |
| Frozen Organizer regression | 374 passed, 76.80 s |
| Full final native suite | **1,207 passed, zero failed, three existing warnings, 121.35 s** |
| Ruff `check .` | passed |
| Pyright | 0 errors, 0 warnings, 0 informations |
| `compileall -q src` | passed |
| `pip check` | No broken requirements found |
| `git diff --check` | passed |

History semantic selection: History UI/service, MainWindow, rename journal, Undo upgrade, executor and all three Undo service/revalidation/durability modules. Organizer selection enumerates all `test_organizer*`, planner/drop/executor/Undo/mode-sync modules using `rg --files`; no literal wildcard is passed to pytest.

The first full native run passed **1,200 cases, zero failures, two existing subprocess UTF-8 reader warnings in 138.96 s**. This run collected before the seven additional boundary tests were added, so it is not the final suite result. The initial concurrent Organizer run printed Windows RPC exception `0x800706be` during the unchanged native clipboard case, but continued and passed all 374 tests. Its isolated serial retry passed **1 case in 1.00 s**, with no exception. This infrastructure behavior is recorded; frozen code/tests were not changed. Subsequent native suite runs use fresh ignored basetemp and default collection order, without warning filters or exclusions.

Final full command: `.venv/Scripts/python.exe -X utf8 -m pytest -q --tb=short --basetemp=research/phase711-full-final`.

The final suite includes all 1,191 baseline cases plus 16 new cases and passed in default order. Its three warnings are the existing subprocess reader UTF-8 decoding warnings. No clipboard RPC exception appeared in this complete run.

## M. Verdict / human acceptance

IMPLEMENTATION PASS — HUMAN VISUAL ACCEPTANCE PENDING

Please review the native mixed-status evidence:

1. Can completed and undone be distinguished before reading the full label?
2. Does the master remain calm rather than colorful/noisy?
3. Is selected state clearly separate from transaction status?
4. Is 已撤销 visually meaningful rather than disabled-looking?
5. Are warning/error/recovery states discoverable without dominating normal history?
6. Does 58px density still feel correct?

Implementation review is not human visual approval.

## N. Commits

- `92e8df3` — `test: characterize history master status visuals`
- `style: improve history status scannability` — exact SHA supplied in final delivery (this report is part of that commit).

## O. Final repository state

Final delivery records `git status --short`, `git log -16 --oneline`, `git diff HEAD^..HEAD --stat` and the start-to-HEAD file guard. Required final state is clean. No push, tag or publication. Stop after Phase 7.1.1; Phase 8 and further History detail work have not started.
