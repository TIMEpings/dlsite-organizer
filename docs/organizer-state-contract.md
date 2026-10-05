# Organizer presentation state contract (Phase 5)

Baseline: main, f5bcb97cabeed55f267c23df2b398e0f553e8f21.
This note precedes the migration; the shell, settings schema and services stay frozen.

## Legacy state machine

- `root_input` owns editable input. `_preview.root_path` owns the committed root;
  editing input does not commit it. Button scans replace; validated same-root drops
  append when there are existing rows, otherwise replace.
- `_preview` owns immutable service plans, scan diagnostics and lookup provenance,
  but QTableWidget items own execution check states. Removal and append reread
  these cells, then rebuild every item. Highlight/current index lives in Qt's
  selection model, independently of execution checks.
- Replace keeps service order and checks every READY row. Other statuses are not
  checkable. Append upserts by `normalized_path_identity(source_path)`, keeps the
  old position and checks, appends new sources in incoming order, and leaves new
  READY rows unchecked. An upsert to non-READY discards executable checks.
- Removal preserves remaining order/checks and only changes preview plans (scan
  and lookup provenance remain). Legacy replace, append and removal rebuild the
  table and clear highlight/current indexes. Clear retains input, discards preview,
  and clears stale state. Startup creates an empty page; navigation never resets it.
- Page fields own request ID, revision, scan mode, cancellation, stale reason and
  execution/Undo summaries. Worker owns an immutable service snapshot and Event.
  Result acceptance checks request ID and revision, then service settings generation
  before committing. Settings invalidation increments revision even without rows.
- Failed scans retain previous preview/root/checks. Cancel is cooperative. Cancelled
  append or replace with a prior preview retains it; first cancelled replace shows
  diagnostic/completed rows but becomes stale and non-executable. A queued success
  after Cancel is treated as cancelled. Thread/worker references live until finished;
  results can commit while busy and hidden, with editability restored only at finish.
- Settings invalidation retains rows/check projection and disables editing. Execute,
  Undo or escaped action errors invalidate existing preview as filesystem-changed.
  With no rows and no active scan, invalidation does not fabricate a stale preview.
- Execute uses checked READY plans from a current, non-cancelled generation, the
  committed root, idle state, executor availability and unresolved journal gate.
  Services revalidate mutations at execution time. Undo uses durable journal state,
  not preview rows. Unresolved transactions disable ordinary mutations and link to
  history; journal failures fail closed. The page refreshes gates on navigation and
  shared mutation signals. Persistent MainWindow pages retain active workers.

## Future ownership and API

`OrganizerState` is the one Organizer-specific presentation authority. It stores:
input root; immutable committed preview (including ordered plans and provenance);
cached row identities and checked identities; highlighted identities and current
identity; request ID, preview revision and current settings generation; scan mode,
busy operation and cancel request; stale reason; a service-supplied mutation-gate
snapshot; existing last execution/Undo summaries and unresolved transaction ID.
No generic application state framework or speculative summary fields are needed.

Row identity is exactly `normalized_path_identity(source_path)`: resolved local
path with Windows case normalization, matching existing drop/upsert semantics.
For service-produced direct non-link children, canonicalization resolves the shared
parent once and joins each normalized child name. Scanner already excludes child
links/reparse points; possible DOS short-name aliases, trailing dot/space names and
non-direct paths fall back to the original resolver. This prevents repeated Windows
path probes during bulk presentation construction without new planner/safety rules.
Indexes locate rows but never identify them. Plans remain domain results, including
structured statuses, warnings, errors, Work and lookup provenance.

Transitions: `replace_preview`, `append_preview`, `remove_rows`, `set_checked`,
`mark_stale`, `clear_preview`, `begin_scan`, `finish_operation`, and selection
projection. Current-generation/validity and execution eligibility are derived in
state. Revisions advance at collection commits/clear/invalidation/removal; checks
and selection do not invalidate request ownership. Page checks acceptance before
calling model collection transitions. Generation mismatch rejects and invalidates;
append additionally requires the existing generation and validity to match.

`OrganizerPreviewModel` references that state and stores no second plan/check
collection. It exposes five columns: 执行 / 状态 / 作品 / 当前名称 / 目标名称.
Standard roles provide check state, text, complete tooltips and accessible status
diagnostics. READY checks are editable only while valid and idle; stale checks stay
visible. Full replace/clear use reset, append uses insertion/update notifications,
removal uses row notifications, and one checkbox emits one bounded dataChanged.
Bulk initialization sets checks coherently. Sorting remains disabled. Standard Qt
delegate painting creates no per-cell widgets. Interim widths are Organizer-only.

Page/controller retains worker lifetime, service calls, immutable settings snapshot
dispatch, drop validation, dialogs, status/summary copy and gate refresh. Selection
signals project highlighted/current identities into state; removal consumes those
identities, never execution checks. Successful collection edits clear
highlight/current state explicitly. Navigation and stale/busy transitions retain
them. No QModelIndex is stored in state.

Model/state do not query providers, settings persistence, journal health or filesystem
mutation safety. They consume service results and gate snapshots. Execute eligibility
requires an existing current non-cancelled preview, committed root, idle state,
checked READY rows, available executor and no unresolved/failed gate. Backend remains
the final mutation authority.

## Phase 6 presentation contract

- The compact input field is the next scan root. Editing it never changes
  `OrganizerState.committed_preview_root` and never marks the existing preview stale.
- The current preview root and summary are rendered from `OrganizerState.preview`.
  Warning counts are a subset of executable READY rows and exclude blocked,
  ordinary UNCHANGED and completed rows. Raw diagnostics remain in the plans.
- The production table remains `QTableView + OrganizerPreviewModel`, with five
  unsorted columns. Structured delegates paint work identity/title and status marks;
  no per-row or per-cell widgets are created.
- A collapsible bottom detail pane follows the state current-row identity and reads
  the corresponding structured plan. It exposes full title, maker, codes, source
  and target paths, and diagnostics. Path fields remain selectable and copyable.
- A single persistent banner prioritizes recovery-required state, journal
  unavailability, and stale preview. Stale rows remain enabled for selection and
  inspection; the model blocks checkbox edits and execution. Rescan or clear recovers
  a stale preview. Unresolved transactions link to History for inspection.
- Scan and rename progress use real worker callbacks. Local completion feedback is
  transient and cannot replace the persistent banner.
- Remove and clear operate only on the in-memory preview. Execute is the only primary
  mutation action, and its count comes from checked READY identities in state.

## Phase 6.3 reconciliation contract

Remove first uses the existing model row notifications. The page then calls
`OrganizerService.reevaluate_preview`: the planner rebuilds affected CONFLICT rows
from their captured Work/code/proposed name using one fresh, lazy sibling index,
then finalizes all surviving proposals. It performs no metadata lookup. READY,
UNCHANGED and other diagnostic rows retain their captured inputs; execution still
revalidates the entire checked batch against current filesystem truth. Model/state
only accept the service output, preserving previously checked READY identities.
Newly unblocked rows start unchecked. An unexpected replanning failure leaves the
remaining preview stale rather than executable.

Finalized forward results may request reconciliation only with a matching committed
root, a non-unresolved transaction and matching finalized operation snapshots.
COMPLETED/PARTIAL/FAILED results qualify; preflight/no-transaction rejection and
pending/recovery-required operations do not. Journal health/unresolved gates are
checked before dispatch. Only confirmed SUCCESS operation target paths enter the
session-local `completed_targets` set, using `normalized_path_identity` (resolved
local path plus Windows case normalization). It is never a domain status or DB fact.

After the action thread finishes, RECONCILE mode uses the same OrganizerWorker and
service snapshot/scanner/lookup/planner pipeline as manual scanning, with a new
request ID and the current preview revision. Results still require matching
request/revision and settings generation. Worker failures carry request/revision
to a GUI-thread slot too; obsolete failure delivery cannot invalidate a newer
preview. Busy policy prevents starting a second scan while the worker is alive.
The root is the executed committed preview root, independent of editable input.

Accepted reconciliation replaces the preview from actual filesystem results and
clears all execution checks. A row presents as Completed only if its canonical
current source identity belongs to the success-target set AND its new domain plan
is UNCHANGED. A changed READY or blocked plan retains its real planner presentation.
Unmatched targets are logged, never materialized into rows. Matching and summary
derivation use set membership and linear passes, with no per-row widgets or cache.

The overlay survives navigation, same-root append and subsequent executions. Manual
replacement commits and Clear Preview reset it and the retry context. A new state
starts empty. Ordinary Replace defaults (READY checked) and Append defaults (new
READY unchecked, old checks retained) remain unchanged. Clear leaves journal/Undo
truth and last mutation summaries intact.

Failed/cancelled/rejected reconciliation retains the old preview read-only, keeps
the mutation result, and exposes Retry Refresh on the original committed root.
Settings-generation rejection retains its stale reason and follows the same retry
ownership rules. Recovery and journal failure take precedence over refresh banners
and retries. Undo remains journal-driven and keeps the previous invalidation-only
behavior; an Undo result clears any failed forward-refresh retry context and does
not schedule a new scan. No executor/Undo/journal safety semantics are changed.

## Phase 6.4 review scope and interaction contract

The committed root owns the session; it does not define membership. State owns
`review_scope`, an insertion-ordered dictionary of canonical source identities to
concrete direct-child paths. It uses the same batch canonicalization as row keys
and successful target matching. This session-only intent survives stale preview
rows, navigation and skipped members; it is neither a second plan authority nor
filesystem truth.

Manual replacement creates scope from accepted plans and clears Completed/retry
state. Append upserts paths without moving existing identities and appends new
members unchecked. Removal deletes highlighted identities from scope (including
the sparse model-reset path). Confirmed successful operations replace source
identities with target identities at the same position; failed and unattempted
members remain. Clear resets scope/overlay/retry, retaining journal and Undo facts.

Automatic reconciliation and Retry capture `tuple(review_scope.values())` into
the normal worker. `OrganizerService.refresh_preview_scope` uses explicit-path
scanner validation in preserved order, fresh metadata lookup and Planner batch
evaluation/finalization. The Planner still indexes actual root siblings for target
occupancy; only supplied members become candidates. There is no root-discovery
fallback. Missing/invalid members are omitted with fresh scoped `scan.skipped`
diagnostics/counts; their scope intent remains until explicit removal/replacement
or Clear. Empty refreshed previews still allow Clear. A scope with no visible
rows continues enforcing same-root append ownership. Request/revision/settings
generation gates and failed-refresh read-only/Undo behavior are unchanged.

Bottom removal, the concise table context menu and the table-only Delete action
call `remove_selected`. Right-click outside the selection selects that row and
makes it current; inside a selection it preserves the group and updates current.
Blank space opens no menu. Delete is a `WidgetShortcut` attached to the table,
so root/path/text focus cannot remove rows. Removal remains preview-only and
reuses service conflict re-evaluation.

Organizer hover hints expose only elided text, icon semantics, or useful extra
diagnostics/explanations. Summary count tooltips and plain status echoes are absent;
accessible descriptions and full detail remain. Work elision checks both painted
lines with their actual font weights/padding. Name columns use the style's text
rectangle for the hovered cell. Labels/path fields adapt to available width.
No whole-model hover measurement or per-row widget is introduced.

Checkbox outer/hit geometry is unchanged. The checkmark now uses indicator-local
floating-point points `(0.23, 0.49)`, `(0.43, 0.70)`, `(0.77, 0.30)` and a round
1.7 logical-pixel stroke. Its visual bounds center on the floating-point box center,
avoiding the shared status glyph's asymmetric bounds and integer-center rounding.
