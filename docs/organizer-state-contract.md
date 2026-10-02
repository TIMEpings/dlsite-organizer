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
  Warning counts overlap the status totals and are not a partition.
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
