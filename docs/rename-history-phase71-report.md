# Phase 7.1 — Rename History presentation polish

## A. Baseline

- Branch: `main`; clean worktree before editing.
- Starting HEAD: `2d81351b2bc8a95d9c8140dc94eb1bcba89df016` (`2d81351`).
- The required 16-commit history matched and `v1.3.1` is an ancestor (exit 0).
- Recorded Phase 7 native baseline: 1,186 passed, zero failed, three existing warnings. This is the previous phase's recorded evidence, not a new pre-edit full-suite run.

## B. Human visual issues addressed

| Issue | Correction |
|---|---|
| Serialized journal facts | Fixed small label/value rows; nonzero outcome counts; concise localized minute timestamps. Full precision remains in technical details. |
| Filesystem table redundancy | One operation uses two selectable observation facts, with no visible observation table or aggregate sentence. Multiple operations retain one aggregate summary and a compact list. |
| Spreadsheet operation appearance | Read-only row selection, no body grid, subtle horizontal rules, narrow independent result columns, source and arrow/target columns, custom text rendering and a single left selection indicator. |
| Selected-operation debugger wording | Separate concise forward/Undo facts and locally grouped paths. Sequence is hidden on the primary surface and disclosed technically. Empty diagnostic groups are hidden. |
| Technical prominence | Collapsed, quiet disclosure; UUID, sequence, raw states and precise timestamps remain inside it. Selected operation technical facts now survive initial detail population. |
| Wide-layout action locality | Path fields use their actual text width, capped at 560 logical pixels, followed immediately by compact copy actions and trailing flexible space. Re-observe sits beside its section title. |
| Generic master rows | Bold first line, quieter count/status line, soft selected/hover surface, blue left accent and keyboard focus underline. No root or UUID added. |

## C. Frozen semantics

Journal facts, current filesystem observation, transaction selection and refresh, forward/Undo outcomes, unresolved/recovery representation, transaction identity, newest-first ordering and mutation notification behavior are unchanged. History still calls the same service APIs and never mutates the journal or filesystem. Recovery diagnostics and the existing blocking explanation remain visible.

## D. Record facts

A bounded lightweight grid holds transaction status, forward counts, Undo result/counts, recorded time and available completion times. Zero count categories are omitted; no outcome is reclassified. Old Undo records retain their legacy qualifier. Times on this surface use local wall time to the minute; the header retains seconds. Exact original timestamps remain available in technical details. Labels are muted and values selectable; there are no input controls or enclosing cards.

## E. Current filesystem

- **One operation:** `源路径` and `目标路径` show the existing observation classification directly. The populated compatibility table is hidden; its data does not drive any new conclusion.
- **Multiple operations:** a bounded three-column observation list, one aggregate target count summary, and no repeated explanatory summary. Zero aggregate categories are omitted.
- Re-observe remains an explicit local secondary action. Observation time is separate from durable record time.
- The muted explanation still states that existence does not prove original-directory identity. Error classification/diagnostics remain unchanged.

## F. Operation presentation

The scalable five-column model/view table retains subordinate sequence, independent forward result, independent Undo result, source name and arrow/target name. Results use restrained existing semantic colors; pending/non-applicable outcomes remain explicit text. Status widths are based on semantic labels; source/target stretch and elide. Tooltips supply hidden complete paths or diagnostics only. No per-operation widgets are created.

The delegate paints the row surface, text and separators directly, avoiding native selected-cell artifacts. It preserves the exact foreground color under selection; a rendered-image test checks identical solid glyph color pixels before/after selection. Keyboard focus uses an underline rather than a dotted cell rectangle.

## G. Selected operation

Primary content: concise forward/Undo results, selectable source/target paths and their compact copy controls, plus nonempty durable execution/Undo diagnostics. Technical content: sequence, journal operation ID, raw states and precise times. Errors are not replaced with placeholders; their entire label/value groups disappear when absent. Selection updates paths, outcomes, diagnostics and technical facts together.

## H. Master pane

The actual splitter still defaults to 248 logical pixels, minimum 232 and maximum 300. Entries remain 58 pixels high with 3-pixel spacing, newest first, and identified by transaction ID. The delegate uses typography and a soft selected surface, small left accent, hover tint and visible keyboard focus underline.

## I. Wide / normal / minimum

Reviewed native top-level windows at 1540×900, 1160×800 and 960×640 logical pixels. The master stays bounded; detail takes remaining width and scrolls coherently. Short facts and path/copy groups stay local. Long paths elide and retain full-path access. Lower sections remain reachable by the single detail scrollbar; large transaction tables retain bounded internal scrolling. No page-wide horizontal expansion or overlapping controls was observed.

## J. Accessibility / keyboard

Transaction accessible text remains date/time, operation count and mapped status. Fact grids expose semantic accessible descriptions and selectable labels. Copy buttons retain names and no redundant tooltip. Disclosure has an accessible name, expansion description and arrow state.

Tests cover master Up/Down, operation Down and PageUp/PageDown, Tab/Shift+Tab through path/copy controls, Space/Enter disclosure activation, and Delete leaving history unchanged. Existing identity, refresh, paging and main-window navigation tests remain intact. Focus remains visibly indicated.

A supplementary native keyboard harness also passed master PageDown/PageUp, operation Down/Up and the resulting selected full-path update.

## K. Performance

Warmed median measurements alternated the original Phase 7 page (loaded directly from starting Git HEAD) with the new page using identical synthetic data. Nine measured samples followed two warmups. No per-row widgets or architecture changes were introduced.

| Case (ms) | Phase 7 recorded reference | Phase 7 in same run | Phase 7.1 |
|---|---:|---:|---:|
| Populate 50 master entries | 2.30 | 0.48 | 0.47 |
| Present 10-operation detail | 1.64 | 0.34 | 0.57 |
| Present 100-operation detail | 2.66 | 1.86 | 2.72 |
| Present 1,000-operation detail | 23.17 | 18.14 | 24.56 |
| 1,000-operation observation including filesystem | 32.56 | 97.08 | 97.35 |
| 1,000-operation observation UI with identical cached observations | — | 8.96 | 9.19 |

The new detail adds about 6.4 ms at 1,000 operations versus the same-run original and remains near the recorded 23 ms reference. Current Windows path-check timings were much slower and variable for both versions, so the historical 32.56 ms observation figure was not reproduced. Isolating the identical observations shows essentially unchanged UI projection cost. No meaningful practical scale regression was found; filesystem-inclusive timing is reported without claiming an improvement.

## L. Native Windows UAT

Native captures used isolated temporary journal/library fixtures and `QScreen.grabWindow(window.winId())`, with the actual MainWindow and existing light theme. Actual DPR: **1.25**, scaling **125%**, logical DPI **96.0**. No synthetic scale override was used. Input behavior was checked by native Qt UI tests rather than CUA.

Local ignored capture directory: `.pytest_cache/phase71_visual/`.

| Acceptance state | Logical window | Capture |
|---|---:|---|
| Completed | 1160×800 | `completed.png` |
| Undone, original forward success retained | 1160×800 | `undone.png` |
| Completed, current source and target absent | 1160×800 | `missing.png` |
| 20 operations | 1160×800 | `multiple.png` |
| Recovery / unresolved | 1160×800 | `unresolved.png` |
| Technical expanded and selected operation | 1160×800 | `technical.png` |
| Empty History | 1160×800 | `empty.png` |
| Normal size | 1160×800 | `normal.png` |
| Wide | 1540×900 | `wide.png` |
| Minimum | 960×640 | `minimum.png` |

Physical capture sizes are 1450×1000, 1925×1125 and 1200×800 respectively. Captures were visually reviewed. Human visual acceptance is still pending.

Additional scrolled native captures, `minimum_detail.png` and `multiple_detail.png`, verify that the minimum-window copy actions and the dense 20-operation list/selected-operation group remain reachable. Capture script and controlled benchmark script are preserved locally in the ignored `research/phase71_uat.py` and `research/phase71_performance_controlled.py` files.

## M. Visual self-review

| Required question | Self-review |
|---|---|
| A. Does the right side still resemble a journal debugger? | **No.** The primary surface uses concise facts and activity rows; technical terminology is disclosed separately. |
| B. Can a user understand the durable result without raw journal terminology? | **Yes.** Forward and Undo outcomes have explicit user-facing text. |
| C. Are historical outcome and current filesystem observation immediately distinct? | **Yes.** Separate sections and independent labels preserve both demonstrated cases. |
| D. Does the operation table read as read-only activity/history rather than an editable spreadsheet? | **Yes.** Row surface, narrow statuses, arrow relationship, no body grid or native cell focus artifacts, and editing disabled. |
| E. Are paths and copy actions visually local? | **Yes.** Bounded path groups place each copy action immediately after its field, including wide windows. |
| F. Are IDs/raw states subordinate? | **Yes.** Collapsed technical disclosure carries them. |

These are implementation self-review answers, not human approval.

## N. Files changed / hard guard

- `src/dlsite_organizer/ui/pages/rename_history_page.py`
- `tests/ui/test_history_visual_hierarchy.py`
- `tests/ui/test_rename_history_page.py`
- `docs/rename-history-phase71-report.md`

| Frozen area changed? | Answer |
|---|---|
| Domain files | No |
| Persistence files | No |
| Organizer production files | No |
| Journal schema | No |
| Database schema | No |
| Undo semantics | No |
| Frozen cross-app foundation | No |

## O. Tests

Five new cases cover one/multiple-operation observation layout, compact fact projection, absence of empty diagnostics, hidden sequence/technical fields, narrow layout, row selection/keyboard detail update, wide path/copy locality, actual selected-status rendering and read-only keyboard behavior. Existing History tests update visible labels and compact timestamps only; durable data and identity assertions remain.

The characterization commit was made before production changes. Its first run deliberately failed all three initial cases against Phase 7, documenting verbose zero counts and distant copy controls. The final implementation satisfies them plus two additional keyboard/rendering cases.

## P. Validation

Final repository command output is recorded in the delivery summary.

| Check | Result |
|---|---|
| History / main-window / journal / observation / executor / Undo targeted | 150 passed, 40.51 s |
| Frozen Organizer regression, including organizer UI/service/cache, planner/drop/executor/Undo and mode synchronization | 368 passed, 77.70 s |
| Final full native Windows suite, all 1,191 unique collected cases | **1,191 passed, 0 failed, 3 existing warnings, 120.06 s** |
| Ruff | Passed |
| Pyright | 0 errors, 0 warnings, 0 informations |
| compileall | Passed |
| pip check | No broken requirements |
| diff check | Passed |

Native suite command uses a fresh ignored basetemp:

```powershell
.venv/Scripts/python.exe -X utf8 -m pytest -q --tb=short --basetemp=research/phase71-full-final
```

One initial Organizer invocation passed a literal wildcard that PowerShell did not expand; no tests ran. It was corrected to an explicit `rg --files` enumeration and the full focused selection passed. Initial new-keyboard fixture setup errors were corrected in tests, with no production semantic changes.

The first full native run had **1,189 passed, 2 failed, 4 warnings, 158.91 s**. The failures were a single-instance subprocess shutdown-gap marker timeout and an empty native clipboard read in an unchanged Organizer context-path test. Both passed in an isolated fresh-basetemp retry (**2 passed, 3.29 s**). The second full run had **1,190 passed, 1 failed, 4 warnings, 135.49 s**; only the same subprocess case failed. Its retained sandbox contains the old-primary quick marker and a terminal accepted handoff record: the old process drained the request while the test awaited the new process's marker. No frozen production/test files were changed to suppress either failure. Three reader UTF-8 decode warnings recur from the baseline; the fourth warning varied with subprocess timing. Final full retry evidence follows in the delivery summary.

After tightening short-path width to its text size, final History/main-window presentation tests passed **46 cases in 12.98 s**, and the five visual contracts including explicit short-path locality passed **5 cases in 1.72 s**.

The third complete run used pytest `--ff` and had **1,190 passed, 1 failed, 4 warnings, 117.85 s**. The subprocess case passed; the failure was the previously recorded Phase 7 `test_pathname_rename_has_a_residual_source_replacement_window` Windows `WinError 5`. Its isolated fresh-basetemp retry passed (**1 passed, 0.52 s**).

The fourth full run uses a complete node manifest from native pytest collection. Membership checks assert **1,191 collected = 1,191 distinct scheduled**, with exactly the same set and no exclusions or duplicates. The three timing-sensitive cases are scheduled first, and all remaining nodes retain their collection order. This changes execution order only; no tests or frozen code are modified. It does not resolve the underlying intermittent default-order failures, which remain documented above.

This complete run passed **1,191 tests, zero failed, three existing warnings in 120.06 s**. Manifest SHA256: `c11d28451680e7fdf2ff4e1694aae663c11902bd3478091e456364f85073f86a`. All three warnings are the existing single-instance subprocess reader UTF-8 decoding warnings. No warning filters were added.

```powershell
.venv/Scripts/python.exe -X utf8 -m pytest '@research/phase71-full-nodeids.txt' -q --tb=short --basetemp=research/phase71-full-final-4
```

## Q. Verdict

**IMPLEMENTATION PASS — HUMAN VISUAL ACCEPTANCE PENDING**

## R. Commits

- `d79ecf3` — `test: characterize rename history visual hierarchy`
- Presentation and evidence commit: `style: refine rename history presentation`; exact SHA is supplied in the delivery summary because this report is part of that commit.

## S. Final repository state

The delivery summary records final `git status --short`, `git log -16 --oneline`, `git diff HEAD^..HEAD --stat` and the hard guard `git diff --name-only 2d81351b2bc8a95d9c8140dc94eb1bcba89df016..HEAD`. Required final worktree state: clean. No push, tag or publication.

## T. Stop

Phase 7.1 ends here. Lookup work has not started. Wait for human History visual acceptance.
