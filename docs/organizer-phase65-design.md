# Phase 6.5: Undo reconciliation design (before production edits)

Baseline: main, 3fb21bc72f32371dd93c60a9e941a5c36171a9a1, clean;
v1.3.1 is an ancestor. Native baseline verification is running.

## Current flow and result authority

OrganizerPage.undo_recent loads latest_undoable, confirms, and starts the normal
RenameActionWorker. _show_undo_result currently clears forward reconciliation
retry state and unconditionally marks FILESYSTEM_CHANGED, including failures
before mutation and transactions from a different root. No old preview can
authorize mutation after Undo; automatic refresh must replace that snapshot.

UndoResult contains status, transaction, operations, error. Each RenameOperation
contains source_path, target_path, forward status, undo_status, undone_at and
sequence. Successful Undo is target_path -> source_path with forward SUCCESS
and undo SUCCESS. The journal snapshot includes transaction.root. UNDONE has
undo_state SUCCESS; UNDO_PARTIAL has undo_state PARTIAL. Undo stops on the first
failed reverse operation, leaving later operations pending. Preflight rejection
returns FAILED without filesystem mutation. RECOVERY_REQUIRED or an unresolved
transaction blocks ordinary reconciliation, even if operation snapshots include
success. Undo acts on the most recent transaction with remaining undoable ops.
Result counts/snapshots are cumulative across resumed partial Undo attempts;
FAILED must never trigger reconciliation merely because earlier successes remain.

## Presentation transition

Accept deterministic UNDONE/UNDO_PARTIAL only when transaction and operation
snapshots agree and the transaction is resolved. Canonical committed-root identity
must equal transaction.root. Different-root Undo leaves the current preview,
scope, Completed and any pending retry unchanged. No preview/empty scope causes
no new scan. Same-root off-scope mutation still refreshes the remaining scope:
the planner reads real sibling occupancy independently of preview membership.

Build an indexed target identity -> original source map from confirmed successes.
Use the existing linear remap_review_scope to replace scoped identities in place.
Remove just those target identities from Completed, including off-scope targets;
retain all unrelated Completed identities. Never derive status from old rows.

Mark the old preview stale before returning from the Undo result callback, then
reuse reconciliation_root/pending and retry_refresh. The action-finished callback
starts RECONCILE through the existing request/revision/generation ownership path.
Record presentation-only reconciliation origin to give Undo-specific progress and
refresh-failure text, including deterministic partial completion. Retry uses the
already reversed scope. Manual scan/clear starts a new session as before.

Recovery remains journal-first and blocks mutation; same-root uncertainty keeps
the old preview stale without ordinary auto-refresh. Failed-before-mutation
preserves preview and any existing retry. A rejected/cancelled/failed refresh keeps
the old snapshot read-only and retryable. Settings invalidation and hidden-page
completion use the existing acceptance/lifetime rules. No domain, journal, schema,
UndoService, planner or executor semantics change.
