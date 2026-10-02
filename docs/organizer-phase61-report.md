# Organizer Phase 6.1 — correction report

Date: 2026-10-03 (Asia/Shanghai). Scope: Organizer presentation and interaction only.

## A. Baseline

- Branch: `main`.
- Starting HEAD: `6afe063d5b16068cb9d472ac2ad21cf838812460` (`6afe063`).
- Starting working tree: clean; `git status --short` returned no output.
- `git merge-base --is-ancestor v1.3.1 HEAD`: exit 0.
- First native full run: **1020 passed / 1 failed / 0 skipped**, 87.02 s,
  basetemp `.pytest_cache/phase61-baseline-20261003`.
- Failure: `tests/app/test_durable_handoff.py::test_completed_controller_action_releases_payload`.
  `os.replace()` of the journal temporary file returned `PermissionError: [WinError 5]`;
  the claimed payload subsequently remained present. No implementation edits preceded this run.
- Isolated durable-handoff module rerun: **52 passed**, 4.06 s,
  basetemp `.pytest_cache/phase61-baseline-handoff-20261003`.
- The user explicitly authorized a narrowly scoped transient Windows filesystem baseline exception,
  with one further fresh-basetemp full run before production edits.
- Second native full baseline: **1021 passed / 0 failed / 0 skipped**, 84.82 s,
  basetemp `.pytest_cache/phase61-baseline-second-20261003`.
  The transient failure **did not recur**. Rule A cleared the baseline.
- Final native full run: **1049 passed / 0 failed / 0 skipped**, 87.51 s.
  The transient failure **did not recur** in final validation either.
- No handoff production code or tests were changed, weakened, skipped, or xfailed.
  No reset or checkout of another commit was performed.

## B. First-preview width root cause

The old width calculation ran at the end of `_build_ui()` and synchronously in the page's
`resizeEvent()`. All five header sections were `Interactive`; last-section stretching was off.
There was no model-reset/result-acceptance or viewport-geometry hook to distribute widths.

The empty state hides the table. A native standalone-page characterization at 1160 x 800 measured:

| Stage | Table visible | Viewport width | Column-width sum |
|---|---|---:|---:|
| Empty page, layout settled | No | 678 | 678 |
| `modelReset`, synchronous | No | 678 | 678 |
| First result accepted | Yes | 1118 | 678 |
| Events processed, no resize | Yes | 1118 | 678 |
| Page resized one pixel | Yes | 1119 | 1119 |

Showing the table gives it its final geometry without resizing the page. Thus the previous
calculation used hidden-table geometry, leaving 440 px blank in this reproduction. Model reset
did not recalculate widths. Resizing the page later read the real viewport and corrected them.

The fix uses a coalesced, zero-delay, single-shot column-layout timer. Model structure signals
schedule it; a viewport event filter schedules it on `Show` and `Resize`. The latter also catches
scrollbar visibility changes and navigation to a previously hidden page. Qt may post layout,
scrollbar and viewport events in separate phases; only an actual geometry event schedules another
one-shot. There is no repeating or polling resize timer, content scan or checkbox-update hook.

## C. Column policy

All sections now use `Fixed` resize mode, with a deterministic Organizer distribution:

| Column | Policy |
|---|---|
| 执行 | 44 logical px |
| 状态 | 15% of viewport, bounded to 144–164 logical px |
| 作品 | 22% of viewport, bounded to 180–250 logical px |
| 当前名称 | Rounded 46% of width remaining after the first three columns |
| 目标名称 | The rest of that remaining width; absorbs rounding |

Triggers: model reset, rows inserted/removed, viewport show/resize, page resize,
detail expand/collapse. Append/upsert retains the same policy; an upsert that changes neither
row count nor viewport geometry needs no recalculation. Cell updates/check toggles do not
schedule layout. No `resizeColumnsToContents()` or iteration through plan data is used.

Native fresh first scan, without resize: viewport **925 px**, widths
**44 / 144 / 204 / 245 / 288**, sum **925 px**. The second scan had identical widths.
At 960 x 680: viewport **725 px**, widths **44 / 144 / 180 / 164 / 193**, sum **725 px**.
Wider/narrower resizing and maximize/restore passed.

## D. Execution checkbox

`OrganizerCheckDelegate` reads `CheckStateRole` and model flags. It owns painting and click
geometry, while `OrganizerPreviewModel.setData()` remains the sole edit path.

- Checked: accent-filled rounded box with a vector white checkmark.
- Unchecked: empty light box with a neutral border; hover strengthens that border.
- Disabled checked: muted filled box retaining its white checkmark.
- Disabled unchecked/non-READY: quiet neutral box, without suggesting execution is enabled.
- Geometry: native `PM_IndicatorWidth/Height`, centered on the cell's center in both axes.
- Keyboard focus: a small accent halo around the indicator, distinct from row highlight.
- Mouse release in the indicator/hit margin and Space toggle through the model.
- Selecting an unchecked row does not check it; checked rows can remain unselected.
- Enter/Return do not activate Execute, including when Execute is enabled.

No per-row checkbox widget or other cell widget was introduced.

## E. Focus defect

Previously the execution cell used Qt's default styled-item checkbox painting and item focus
primitive, together with the Organizer `::item:focus` border rule. Default item-view checkbox
geometry placed the indicator at the left and left a separate item/text focus region to its right;
that cell-level focus treatment produced the reported oversized rectangle.

The dedicated delegate strips `State_HasFocus` and `HasCheckIndicator` from its background
style option, paints its own centered indicator and local focus halo, and removes the old
Organizer item-focus stylesheet border. Work/status delegates use the same clean background
and a restrained bottom line for their current cell. Keyboard focus remains visible.
Native focused-checkbox captures contain no large black focus rectangle.

## F. Status / warning vocabulary

Every row retains its real domain status and one primary painted mark. Marks are deterministic
QPainter paths (check, equal lines, circled exclamation, warning triangle), without emoji or
font-dependent status glyphs. The leading mark reserves 18 px; text baselines and spacing are
stable and long labels elide. Additional triangles sit immediately after the status label.

| Row condition | Primary presentation | Additional warning presentation |
|---|---|---|
| READY, no warning | Green check + 可执行 | None |
| READY + warning | Green check + 可执行 | Caution triangle; real diagnostic tooltip/detail |
| UNCHANGED, no warning | Neutral equal + 无需重命名 | None |
| UNCHANGED + warning | Neutral equal + 无需重命名 | Caution triangle; real diagnostic tooltip/detail |
| CONFLICT | Blocked exclamation + 冲突 | No stacked triangle; tooltip/detail; 另有 N 条警告 in detail header |
| AMBIGUOUS_CODE | Blocked exclamation + 作品编号不唯一 | Same independent diagnostic treatment |
| LOOKUP_FAILED | Blocked exclamation + 查询失败 | Same independent diagnostic treatment |
| INVALID_TARGET | Blocked exclamation + 目标名称无效 | Same independent diagnostic treatment |

Warnings are independent information, not a stronger severity tier or a new domain status.
Existing model decoration/accessibility semantics remain unchanged; the Organizer delegate
controls the visible vocabulary.

## G. Tooltip

Removed `悬停查看具体原因。` and equivalent self-instruction filler. Execution/status tooltips
return no value if there is no diagnostic. Otherwise they show the first three diagnostic lines,
bounded to 300 characters; the detail pane retains complete content.

Examples: READY without warnings → no tooltip; warning → `独立警告`;
conflict → `目标目录已存在，不能覆盖。` plus any real independent warning.
Full title/path tooltips continue to provide useful long-content access.

## H. Current preview context

Current preview shows a strong directory basename next to a borderless, selectable full path.
Both derive exclusively from `state.committed_preview_root`. Next-scan input remains an editable
QLineEdit with normal input chrome. Editing it neither changes committed context nor ownership.
Full current-root path remains accessible through selection, Ctrl+C, horizontal text access,
tooltip and accessibility description.

## I. Summary

`OrganizerSummary` paints small single-line count marks in priority order: total, executable,
non-executable, overlapping warnings, then quieter unchanged/skipped counts. No cards or colored
pills. At reduced width it paints only complete marks that fit, never a wrapped telemetry block.
All counts and the warning-overlap explanation remain in accessible text and the summary tooltip.
Native 960 x 680 captures still show all six small counts.

## J. Detail

Organizer-only `OrganizerPathDisplay` keeps the existing read-only selection/copy behavior,
explicit Copy buttons and complete long paths, while replacing input chrome with transparent
context text and a subtle focus underline. Shared `PathDisplay` is unchanged.

Title/metadata/detail header use one-line elision with full accessible/tooltip text. Diagnostic
text is read-only and borderless with zero document margin: 24 px for the quiet no-warning line,
56 px and scrolling for actual multiline diagnostics. The full error/warning content is preserved.
The detail surface has a single top divider rather than another enclosing rounded border.

## K. Action area

Remove(highlighted count) and Clear remain quiet actions at the left. Execute(checked executable
count) is the dominant action at the right. Duplicate highlighted-count chrome is hidden; the
state projection and public count label remain intact. The single-line helper sits above the row,
with shorter confirmation/path-check wording in compact mode. Mutation safety is visible in
the helper and existing confirmation flow, not solely in tooltips.

A divider separates the compact recent-transaction/Undo row. Undo retains quiet emphasis and
all existing gates. Recent context elides instead of creating another multiline block.

One Organizer decision considers page width below 880 px or height below 780 px compact. On
entering compact geometry it collapses detail; users can expand it explicitly without periodic
auto-collapse. At the native default 1160 x 800 window, table viewport height is **303 px**,
compared with about **180 px** in the first expanded-pane correction capture. At 960 x 680 it is
**183 px**, with an unwrapped helper, separate footer and clear Execute. Row height stays 40 px.

## L. State contract

Unchanged: replace, append, upsert, check ownership/defaults/projection, highlighted selection,
current identity, stale rules, request/revision/generation, scan cancel, Execute and Undo.
`OrganizerState` was not edited. `OrganizerPreviewModel` changed only status/execution tooltip
presentation; no flags, data edits, signals, state transitions or structural semantics changed.
No new state authority, model, planner/rename rule or mutation gate was introduced.

## M. Performance

Native Windows Qt, production light theme, median of five offline samples:

| Operation (ms) | 1k | 5k |
|---|---:|---:|
| Accepted preview, including state/model and page updates | 6.623 | 19.684 |
| First presentation, deferred layout/events plus visible viewport paint | 15.073 | 16.000 |
| Checkbox, including page signal handling | 0.064 | 0.071 |
| Current-row detail update | 0.299 | 0.343 |

For the retained bare-table benchmark, 5k model/state reset was 14.678 ms and first presentation
26.688 ms. The page and bare-table timings have different scopes; these are representative
current observations, not timing assertions or identical-method historical speedup claims.

**Per-row widgets: none.** Table child-widget count remains constant at 1k/5k. Column distribution
is constant work across five columns. Single checkbox/current-row changes do not scan all rows;
delegates paint visible cells and use simple vector marks rather than per-paint icon allocation.
Reproduce with `python tools/benchmark_organizer_preview.py`.
Raw measurements: [benchmark.json](../research/phase61-native-57271903/benchmark.json).

## N. Native Windows UAT

Environment: Microsoft Windows 11 Pro, build 26300, Qt platform `windows`, 125% scale,
window DPR **1.25**, screen logical DPI 96. Captures are native Qt MainWindow grabs, not
offscreen/linux renders. All library/journal fixtures were under ignored `.pytest_cache`;
the evidence confirmed all eight original sandbox directories remained unchanged.

These PASS entries record implementation checks and agent inspection of native captures;
human visual approval is still pending.

| Scenario | Result | Evidence |
|---|---|---|
| Fresh first real worker scan, no resize | PASS | [First scan](../research/phase61-native-57271903/01-first-scan.png); sum=viewport=925 px |
| Second accepted scan | PASS | Same widths as first scan; no intervening resize |
| Normal populated | PASS | [Normal/focus](../research/phase61-native-57271903/02-normal-checkbox-focus.png) |
| Checkbox checked/unchecked/current/selected | PASS | [Normal/focus](../research/phase61-native-57271903/02-normal-checkbox-focus.png) |
| Disabled checked and unchecked projection | PASS | [Stale projection](../research/phase61-native-57271903/04-disabled-check-projection.png) |
| Mixed READY/warning/conflict/ambiguous/lookup/invalid | PASS | [Mixed statuses](../research/phase61-native-57271903/03-mixed-status-warning-detail.png) |
| Expanded error plus independent warning | PASS | [Expanded diagnostics](../research/phase61-native-57271903/03b-expanded-diagnostics.png) |
| Near minimum, 960 x 680 | PASS | [Near minimum](../research/phase61-native-57271903/05-near-minimum.png) |
| Wider/narrower/maximize/restore | PASS | Native harness geometry assertions |

Reproduce with `python tools/inspect_organizer_phase61.py`.
[Native evidence JSON](../research/phase61-native-57271903/evidence.json) includes dimensions,
column widths and capture paths. Review copies are preserved under ignored `research/`.

## O. Files changed

| Tracked file | Purpose |
|---|---|
| `src/dlsite_organizer/ui/delegates/organizer_delegates.py` | Centered check painting/hit handling, restrained focus and vector status vocabulary |
| `src/dlsite_organizer/ui/models/organizer_preview_model.py` | Concise diagnostics-only tooltip presentation |
| `src/dlsite_organizer/ui/pages/organizer_page.py` | Deferred viewport column layout and local review/footer/compact presentation |
| `src/dlsite_organizer/ui/widgets/organizer_review.py` | Organizer-only contextual paths, elided labels and painted summary |
| `tests/ui/test_organizer_visual_correction.py` | 28 added structural and interaction regression cases |
| `tests/ui/test_organizer_view.py` | Click hit testing now uses the installed check delegate geometry |
| `tests/ui/test_organizer_page.py` | Updated tooltip expectations: no filler/no empty diagnostic tooltip |
| `tools/benchmark_organizer_preview.py` | Retains bare-table measurements and adds actual themed Organizer page/detail timings |
| `tools/inspect_organizer_phase61.py` | Reproducible offline native first/second scan, capture and geometry UAT |
| `docs/organizer-phase61-report.md` | This A–U evidence/report |

Backend files changed: **none**. Frozen cross-app foundation changed: **none**.
No shared PathDisplay, StatusBanner, shell, sidebar, typography, scrollbar, content-width,
About or Lightweight implementation changes.

## P. Tests

Added coverage: hidden-to-first-preview distribution at 7/1k/5k rows, actual first worker scan
without resize, second scan consistency, append/upsert distribution, window/detail geometry,
checked/unchecked and stale/busy/non-READY projection, centered indicator/hit geometry,
Space/mouse behavior, independent highlighted/checked/current identities, action counts,
Enter/Return with an enabled Execute button, status/warning combinations, diagnostic tooltip
absence/cleanup, committed-root ownership, full-path Ctrl+C/Copy, compact table/footer and
single-line summary data/accessibility. No pixel-perfect or brittle timing assertions added.

## Q. Validation

All final commands used the repository `.venv` on native Windows:

| Check | Result |
|---|---|
| Targeted pytest (command below) | **106 passed**, 8.55 s, 0 skipped |
| Full `python -m pytest --basetemp=.pytest_cache/phase61-final-native-20261003` | **1049 passed**, 87.51 s, 0 skipped |
| `ruff check .` | All checks passed; exit 0 |
| `pyright` | 0 errors, 0 warnings, 0 informations; exit 0 |
| `python -m compileall -q src` | Exit 0 |
| `python -m pip check` | No broken requirements found; exit 0 |
| `git diff --check` | Exit 0 |

Targeted command:

```powershell
.venv/Scripts/python.exe -m pytest tests/ui/test_organizer_visual_correction.py tests/ui/test_organizer_view.py tests/ui/test_organizer_page.py tests/ui/test_organizer_preview_model.py tests/ui/test_organizer_state_characterization.py tests/ui/test_main_window.py -q --basetemp=.pytest_cache/phase61-targeted-release-20261003
```

No live DLsite request, Explorer registration change, user-library rename, release action,
push, tag or publication was performed.

## R. Visual verdict

```text
IMPLEMENTATION PASS — HUMAN VISUAL ACCEPTANCE PENDING
```

## S. Commits

- `90de301` — `fix: correct organizer table interaction visuals`.
- `style: refine organizer review presentation` — the commit containing this report;
  its exact SHA is recorded in the completion response and
  [final repository snapshot](../research/phase61-native-57271903/repository-final.txt).

Phase 6 commit was not amended. Commits remain local on `main`.

## T. Recommended next phase

Wait for human visual acceptance. Only after approval/freeze:
**Phase 7 — Rename History Visual Migration**. No next phase was started.
No additional Phase 6.1.x correction is proposed before reviewing the native captures.

## U. Final repository state

Working tree: **clean** after the two focused commits. Branch remains `main`.
The post-commit outputs of `git status --short`, `git log -12 --oneline` and
`git diff HEAD^..HEAD --stat` are preserved verbatim in
[repository-final.txt](../research/phase61-native-57271903/repository-final.txt).
