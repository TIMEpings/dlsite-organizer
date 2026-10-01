# Architecture

## Current architecture

The lookup flow remains deliberately small:

```text
PySide6 LookupPage
        ↓ signal / QThread worker
LookupService ─────→ NamingService
        ↓
WorkProvider protocol
        ↓
DlsiteProvider → product_info_ajax parser → ProductInfoAjaxSource ─┐
        ↓ optional bounded enrichment                               │
product.json parser → ProductMetadataSource ───────────────────────┤
        ↓ (only when core is unusable)                              │
HTML JSON-LD parser → HtmlProductSource ────────────────────────────┤
                                                                     ↓
                                                    merged normalized Work
                                                                     ↓
                                                TranslationInfoSource (when available)
                                                                     ↓
                                                   TranslationRelationService
                                                                     ↓
                                             TranslationRole + WorkRelation[]
                                                                     ↓
                                                                   LookupResult
```

`Work`, work-code validation, safe naming primitives, and relation contracts live in the domain
package. The domain imports no Qt, HTTP, parser, or database implementation. `LookupService`
coordinates validation, provider access, error translation, and naming without knowing widgets.
The UI owns thread lifecycle and presentation only; `LookupWorker` invokes services and emits
values, and never manipulates widgets or implements parsing policy.

`DlsiteSite` builds every section-scoped URL in one place. `source_route_for()` maps the typed
`WorkCode` to a bounded source route: RJ keeps the configured section, BJ uses `books`, and VJ
resolves a public-page redirect across the fixed `soft`/`pro` pair before requesting structured
sources. `DlsiteProvider` first requests the candidate `product/info/ajax?product_id=<WORKNO>` source. A successful response is used only when it
validates as `ProductInfoAjaxSource`; it then makes one exact-listing
`api/=/product.json?workno=<WORKNO>&locale=<configured-locale>` enrichment request. A translation child may trigger
one additional rich request for its explicit original work; child lists are never crawled. If the core
source is unusable, the provider makes one HTML fallback request instead. There is no brute-force
section probing. The route seam remains a provider implementation detail and is the narrow boundary
where future source routing can be introduced.

`ProductInfoAjaxSource` and its nested `TranslationInfoSource`, together with
`ProductMetadataSource` and its nested rich DTOs, are provider-local Pydantic models with
`extra="allow"`. They retain typed reviewed metadata and translation evidence without polluting
`Work`. The validated AJAX envelope key establishes the queried `Work` identity; an optional nested
`product_id` is retained separately and must agree when present. The raw `regist_date` timestamp is
retained as `regist_datetime`, while `Work.release_date` receives its date component. Rich product JSON
adds maker pair, series, CV, tags, exact listing language, and normalized age. `HtmlProductSource` is a
separate provider-local dataclass for Schema.org Product JSON-LD. Each source is normalized into `Work`
only after extraction; the domain never imports HTTP, HTML, JSON, or DLsite DTOs.

`DlsiteProvider.fetch_work_lookup()` is the source-aware extension point. It returns normalized
`Work`, the already-validated AJAX DTO, separate core/rich provenance, and optional translation
attribution in one bounded request flow (or no AJAX DTO when HTML fallback was used). Core AJAX remains
authoritative for translation topology, bonus evidence, precise registration, and availability; rich
JSON fills descriptive metadata. A rich failure degrades to the validated core work. The application
exposes only its nested `translation_info` to `TranslationRelationService`, which maps explicit flags
and references into confirmed directional relations without re-requesting or reparsing raw responses.
`fetch_work()` remains the metadata-only API for simple callers.

`TranslationRelationService` is the only place that interprets DLsite translation topology. A true
`is_original`, `is_parent`, or `is_child` flag determines `TranslationRole`; concrete target work numbers
are required before creating edges. Every created edge has `Confidence.CONFIRMED` and
`EvidenceType.EXPLICIT_TRANSLATION_REFERENCE`. It never requests related works, persists relations,
or uses maker/date/RJ proximity heuristics. `WorkRelation` stores only source/target work identities,
so related nodes do not need incomplete `Work` objects.

The service treats absent or role-less translation metadata as `NO_INFORMATION`. Contradictory role
flags become `INVALID`; missing references and self-references become `INCOMPLETE` with no unsafe
edge for the invalid field. These outcomes remain data in `LookupResult`, so metadata lookup and
the GUI do not crash on a valid source payload with an invalid business topology.

The structured contract is backed by three reviewed, user-captured AJAX responses reduced into real
regression fixtures. It remains a small-sample contract rather than a claim about every production
response; see [DLsite AJAX data contract](dlsite-data-contract.md). HTML JSON-LD remains unverified
against current live markup. Optional values degrade to missing values; corrupt core source metadata
is a parse failure rather than invented metadata.

SQLite is initialized through a small SQLAlchemy `Database` object. It retains the early
`work_observations` placeholder for compatibility, but the production observation source in v0.5 is
`metadata_observations`; the placeholder is deprecated/unused and its unknown old rows are not
migrated. `work_metadata_cache` stores one current row per work number, while
`metadata_observations` appends one row for each successful live provider response. The cache and
observation schemas are initialized separately from the v0.4 `rename_transactions` and
`rename_operations` journal tables.
The nullable `metadata_observations.bonus_evidence_json` field is an additive,
versioned snapshot of bonus evidence reported by that response; `NULL` remains
distinct from an explicitly empty bonus list. Phase A adds nullable rich-field and provenance columns
to both current cache and historical observations, so old rows load as not captured/unknown. See
[historical bonus evidence](bonus-evidence-history.md) and the [DLsite metadata source contract](dlsite-data-contract.md).
Lookup results use the cache for current metadata and preserve provider provenance separately from
delivery freshness. Translation relations are reconstructed from the normalized cached
`translation_info`; no relation-history query or inference is implemented.

Settings use `tomllib` and validated Pydantic models. `SettingsPage` edits the same `AppSettings`
schema that TOML loading uses; it never writes TOML directly. `SettingsService` validates a complete
model, writes UTF-8 TOML through a same-directory temporary file and `os.replace`, then notifies the
runtime. Missing configuration is normal and uses built-in defaults. Invalid present configuration is
surfaced rather than silently ignored. Fresh profiles default to lightweight startup,
`[{workno}][{maker_name}]{title}`, and the system-mapped supported metadata locale with `ja_jp`
fallback. Changing locale affects subsequent live/force requests, while fresh existing cache entries
remain governed by the current TTL rather than being silently reinterpreted; explicit existing
settings are preserved.

`NamingService` is the one renderer for lookup names, Organizer plans, and Settings preview. Its
canonical placeholders are `{workno}`, `{title}`, `{maker_name}`, `{maker_id}`, `{series_name}`,
`{cv}`, `{tags}`, `{age}`, `{language}`, and `{release_date}`. The old aliases, including
`{rjcode}` and `{series}`, remain accepted but are not advertised for new templates. Missing
optional values render as empty strings, and the existing bracket empty-group cleanup avoids results
such as `[]`. Rendering is deterministic and does not evaluate code or general template expressions.
CV/tag separators, CV prefix/suffix, tag limit, date format, general-age visibility, and the Windows
illegal-character replacement symbol are settings. Reserved Windows device names and trailing
dot/space protection remain in the shared sanitizer.

When Settings saves successfully, `MainWindow` updates the existing NamingService, LookupService,
provider timeout/locale, and cache policy without restarting. Only changes to settings that can
alter generated directory names mark an existing Organizer preview stale; execution remains
disabled until a new scan creates a plan with the new naming configuration. Provider timeout and
cache-policy-only saves do not invalidate an otherwise unchanged preview.

## Organizer flow

The organizer is an application flow separate from the manual lookup page:

```text
OrganizerPage
        ↓ signal / QThread worker
OrganizerWorker
        ↓
OrganizerService
        ↓
FolderScanner → ScanResult → ScanCandidate[]
        ↓ direct RJ / BJ / VJ work-number batch, sequential and deduplicated
LookupService → WorkLookup[] → Work
        ↓
NamingService → RenamePlanner → RenamePlan[]
        ↓
OrganizerPage preview table
```

`FolderScanner` reads only direct child directories. It excludes regular files, hidden entries and
symlink directories, records no-code children as skipped, and keeps ambiguous multi-code folders
as visible warning candidates. `extract_work_codes()` lives beside `WorkCode` parsing, so folder
extraction and manual lookup validation share one normalization rule. It accepts the same RJ / BJ /
VJ contract; VJ/BJ do not create alternate Organizer pipelines.

The Organizer table is a projection of one authoritative in-memory `OrganizerPreview` collection.
The explicit **扫描并预览** action uses replace semantics: the new successful result replaces the
collection. A full-mode directory drop uses append/merge semantics: existing rows keep their order,
new unique source directories are appended, and a repeated source directory is upserted in place
using the shared normalized Windows path identity. Delete and clear only change this collection;
they never touch the filesystem or journal. Execution reads the collection's current plans, so a
removed row cannot be executed. A drop result is committed atomically only after successful worker
completion; cancellation or failure preserves the previous collection. A stale collection caused by
naming-setting changes rejects new append drops until it is rescanned or cleared. A filesystem
mutation from execution, undo, or the shared mutation-history signal uses a distinct filesystem
stale reason; mode switches and show events do not create settings staleness.

Plan status presentation is UI-only. Existing `RenamePlanStatus` values are grouped as normal
(`READY`, `UNCHANGED` without warnings), warning (plan warnings or `CANCELLED`), or blocked/error
(all other non-executable states). Warning and blocked/error status cells use a Qt standard icon,
emphasized text, and a tooltip containing the plan's actual reason; the status text also says that
the reason is available on hover. No separate details column is used.

## Phase C drag-and-drop and lightweight mode

The application has two runtime windows backed by one `ApplicationComponents` instance. Full mode
keeps the review boundary: a drop becomes a preview. A single dropped directory whose basename has
one RJ / BJ / VJ work number is treated as one explicitly selected work folder; a basename without a
work number is treated
as an organizer root and receives the ordinary direct-child scan. Multiple work folders must be
direct siblings, and selective preview never scans their other siblings. Files, non-local URLs,
symlink/junction/reparse-point entries, ambiguous work numbers, and mixed-parent drops are rejected.

Lightweight mode is a second UI entry point, not a second rename implementation:

```text
LightweightWindow
        ↓ one local user action
QuickActionController (one primary-owned FIFO)
        ↓ QtQuickActionRunner / QThread
QuickRenameWorker → QuickRenameService
                         ↓
DropInputService → OrganizerService.preview_paths
                         ↓
                 LookupService → NamingService → RenamePlanner
                         ↓
                    RenameExecutor → TransactionJournal → filesystem
                         ↓
                       UndoService
```

An explicit drop onto the labelled lightweight zone means the user's Quick Rename confirmation,
but journal availability, recovery health, batch-wide planner checks, executor preflight, and
durable journal intent remain mandatory. Quick Rename validates every input before lookup. Any
invalid input, lookup failure, or non-READY/non-UNCHANGED plan rejects the entire batch without
mutation. UNCHANGED rows are successful no-ops; READY rows execute as one journal transaction.
Partial executor results are surfaced as partial and remain undoable where the journal permits.

The full and lightweight windows share SettingsService, LookupService, metadata cache, NamingService,
OrganizerService, QuickRenameService, RenameExecutor, and UndoService. Settings saves therefore
apply to the next lightweight drop without restarting. `startup_mode` controls only the next launch;
runtime switching hides one window and shows the other in the same process. The UI contract does not
include single-instance IPC; the application-level integration is described below.

## Phase D Explorer context-menu invocation

Phase D adds one typed application boundary around the existing lightweight flow:

```text
Explorer shell verb (HKCU, Player, DelegateExecute)
        ↓ IExecuteCommand / IObjectWithSelection
native x64 helper → one bounded QUICK_RENAME request
        ↓
profile election → primary ApplicationComponents
        ↓ secondary: local IPC forwarding and exit
        ↓ primary: same config.toml, cache, SQLite DB, naming and locale
        ↓
LightweightWindow.start_quick_rename
        ↓
primary QuickActionController → QuickRenameWorker → QuickRenameService
                         ↓
                 LookupService → NamingService → RenamePlanner
                         ↓
                 RenameExecutor → TransactionJournal → filesystem
                         ↓
                       UndoService
```

`--quick-rename <directory> [<directory> ...]` accepts one or more directory arguments. Normal
launch with no arguments continues to select the configured full/lightweight startup window.
Unknown arguments and missing values are rejected by `argparse`; a packaged GUI reports the error
in a dialog and logs it rather than leaving an invisible process.

The production Explorer verb is created only under the current user's
`HKCU\Software\Classes\Directory\shell\dlsite-organizer` key. Its `command` child contains a
`DelegateExecute` `REG_SZ` value with the fixed native CLSID
`{031255AF-20D8-4EE9-AC4C-D8CE7D3E154B}`, and the verb explicitly sets `MultiSelectModel=Player`.
The matching `HKCU\Software\Classes\CLSID\{031255AF-20D8-4EE9-AC4C-D8CE7D3E154B}\LocalServer32`
default value is the quoted sibling `dlsite-shell-helper.exe` path. The static v1.1 command
`"<exe>" --quick-rename "%1"` is removed during migration and is never retained as a fallback.
No source path, Python interpreter, `cmd /c`, PowerShell, shell interpolation, HKLM entry, or
elevation is registered. Windows path comparison is case-insensitive and expands 8.3 spelling when
Windows can provide the long form, so portable relocation is reported as a typed stale state.
Non-Windows and source/development contexts report `UNSUPPORTED` and cannot register.

The native helper implements `IExecuteCommand` and `IObjectWithSelection` in a pure Win32 x64
`CLSCTX_LOCAL_SERVER` process. Explorer supplies one `IShellItemArray`; the helper validates and
snapshots it, then sends one bounded `QUICK_RENAME` request to the profile-scoped primary. If no
primary is running, it validates and launches the packaged sibling application with only the fixed
`--quick-rename-host` signal before sending that same request. The application remains authoritative
for the 32-item business limit, queue admission, validation, one journal transaction, and one Undo.
The CLI `--quick-rename <paths...>` path remains supported independently.

One Explorer selection is one application batch: one `SetSelection` snapshot produces one
`Execute`, one IPC request, one `QuickActionController` item, one `QuickRenameService` call, and
one journal transaction. The batch-wide rules are fail-closed, including the `MAX_QUICK_RENAME_ITEMS`
limit of 32 folders. The native helper limits its serialized request payload to 192 KiB; the
application's framed local IPC transport is capped at 256 KiB. Independent Explorer clicks are
independent requests and are never time-merged. The safety invariant remains:

```text
NO JOURNAL = NO MUTATION
```

`ExplorerIntegrationService` is UI-independent and uses a small registry backend protocol. The
Settings page renders its inspection result rather than guessing from button text or persisting a
registry state in TOML. It distinguishes `CURRENT`, `LEGACY`, `STALE`, and `ABSENT`; the UI renders
the first as **已注册**, legacy/stale as **需要更新**, and absent as **未注册**. Registration
validates the sibling helper before any write, writes the LocalServer32 entry before switching the
verb, writes `MultiSelectModel` last, and rolls back captured owned values on failure where possible.
Remove deletes only this verb tree and fixed CLSID tree and is idempotent. The packaged Explorer
selection path has been validated against this contract. Ordinary Windows 11 shell-verb behavior
may place the command under **显示更多选项**; first-level modern-menu placement is not guaranteed.

The native helper is built separately with the Release x64 MSVC/CMake toolchain and static MSVC
runtime (`/MT`), then copied as exactly one sibling of the PyInstaller onedir executable. Native
interoperability has been verified with PySide6 6.11.2 / Qt 6.11.2 against the Win32 named-pipe
client. Any future PySide6/Qt upgrade must re-run the native/PySide interoperability tests; no
arbitrary future Qt version is guaranteed.
The footer About entry is the only About navigation entry and derives its version from the package
version source; the main navigation is `整理`, `查询`, `设置`, `重命名历史`.

## Single-instance application lifecycle

The desktop entry point performs only the minimum work needed to identify the invocation before
business bootstrap:

```text
argv parse → QApplication/minimal Qt prerequisites → default profile root
        ↓
InstanceCoordinator election (profile-scoped QLockFile)
        ├─ secondary: LocalCommandClient → primary → bounded exit
        └─ primary: full logging → settings → components → windows/controller/router
                                      → QLocalServer listen → show mode → app.exec()
```

`InstanceIdentity` is derived from the canonical default application profile/data root, not from
the executable path. Portable copies therefore share one primary when they use the same profile.
`InstanceCoordinator` owns the `QLockFile`, stale-owner recovery, and the `QLocalServer` endpoint;
the server uses `UserAccessOption` and never exposes TCP or shell transport. The primary acquires
the lock before constructing the database or windows, but defers `listen()` until the application
command router is ready. A secondary retries connection only while no request has been connected or
written; an ambiguous post-write ACK failure is fail-closed and is never replayed locally.

The application router is outside the IPC core. `ACTIVATE` targets the actual current runtime window
(Full or Lightweight), restores it when minimized/hidden, and calls `raise()`/`activateWindow()`;
it does not create a window, switch mode, or change `startup_mode`. `QUICK_RENAME` submits to the
single primary-owned `QuickActionController`. Its queue admits one active action plus eight FIFO
pending actions, deduplicates every normalized path identity across active and pending requests, and
maps `ACCEPTED`, `DUPLICATE`, `QUEUE_FULL`, `SHUTTING_DOWN`, and `REJECTED` explicitly to local
admission replies. A multi-directory local/CLI action remains one controller item and is passed to
the existing `QuickRenameService` as one batch, preserving one planner/executor transaction and
one Undo record.

An admitted IPC Quick Rename surfaces the existing Lightweight window when the Full window is not
busy; this is a runtime presentation choice and never changes the stored `startup_mode`. If the
Full window is already doing work, the request remains queued in the primary and the shared
Lightweight surface receives its lifecycle and result signals when it is next shown.

`QuickActionRunnerHost` is the only primary Quick Rename worker factory. Lightweight local drops,
initial Quick Rename launch, and IPC requests all use that controller and the existing
`QuickActionController → QuickRenameWorker → QuickRenameService → DropInputService →
OrganizerService → RenameExecutor` pipeline. IPC admission ACK is returned immediately after queue
admission; lookup, planning, journaling, and filesystem work remain asynchronous. The Lightweight
surface subscribes to the controller lifecycle and existing `RuntimeSignals`, so forwarded work
uses the same progress, result, recent-operation, and Undo presentation as a local action.

Full Organizer execution remains its existing flow. `MutationGate` is shared by `RenameExecutor`
and `UndoService`, so Full execution, Quick Rename execution, and Undo cannot overlap mutation
critical sections. Quick input still passes through `DropInputService`; IPC structural validation
does not bypass work-code, directory, parent, reparse-point, or journal checks.

`ApplicationLifecycle` owns the current window and the explicit application exit path. With
`setQuitOnLastWindowClosed(False)`, a genuine close marks shutdown, stops the local server,
begins controller shutdown, drops pending-but-not-started Quick Actions, and lets the active worker
finish. It then closes windows, disposes the primary database, releases the profile lock last, and
quits the application. Close is idempotent, and no new queued mutation starts after shutdown begins.

### Durable ingress ordering and resource lifetime

Quick Rename CLI requests and native IPC fallback use the profile's `handoff/v1` inbox. Native
payloads remain non-executable in `prepared` until launch succeeds; launch failures stay in
`recoverable`. Publication means the fully written, flushed payload is atomically made visible in
`pending`, not the time it was prepared or a wall-clock timestamp. The primary moves it to `claimed`
and flushes a journal claim before controller submission. Capacity/startup/shutdown deferral records
`retryable` and restores the same payload to pending. Drain stops at the first `QUEUE_FULL` or
`SHUTTING_DOWN`, so later requests cannot overtake it. Each payload keeps its batch boundary.

New Python and native publishers serialize publication with an OS-owned byte-zero lock on
`publication.lock` (Windows CRT byte locking / Win32 `LockFileEx`; POSIX Python uses `flock`). The
lock remains held through pending visibility. Under that lock, `publication-sequence` reserves a
durable increasing unsigned 64-bit sequence, then `ordering/<request-id>.seq` records it. Both are
atomically replaced, flushed files containing 20 ASCII decimal digits and a newline. Counter gaps
after failed publication or a crash are harmless; a preparation/sidecar alone is never executable.
The counter persists through retention and restart, and a corrupt/exhausted allocator fails closed.
Lock acquisition has a five-second bound and process death releases the lock automatically. The
native promotion participates because Python cannot reconstruct the order of historical renames.

Distinct requests from participating publishers have a strict total publication order, including
concurrent processes; lock acquisition order itself has no fairness guarantee. Drain sorts by the
stored sequence and retry retains it, including claim recovery before admission. A fresh IPC request
that is first deferred to disk receives its sequence at that pending publication. A duplicate pending
ID keeps its original order. Payload JSON, protocol version, request-ID identity and canonical digest
are unchanged. Legacy files without a sidecar remain readable without migration: they precede all
sequenced files and use lexical request-ID order within the legacy group. Their original publication
order is unknowable; an older helper still publishing without sidecars receives this legacy policy.
Invalid payloads/ordering sidecars are quarantined, while corrupt admission journals block their ID.

Accepted tracked work progresses through queued/started/completed execution markers. Orphan journal
claims and accepted work without confirmed completion become `indeterminate` after restart and are
never replayed automatically. Safe terminal records retain the existing seven-day window. Pruning
under the publication lock removes their claimed payload, ordering sidecar, journal and in-memory
duplicate entry together; pending work, controller mappings and unresolved states prevent expiry.
After expiry the same ID can be admitted again, identically in a running process and after restart.

IPC normalizes invalid UTF-8, malformed JSON (including recursion-depth failures), duplicate keys,
nonstandard constants, wrong types/fields and frame-size violations to protocol rejection. The timer
stays active through decoding/admission and bounds reply flushing as well. A rejection is sent when
possible, then the connection closes; unexpected internal failures are logged and cleaned up. One
idempotent cleanup removes tracking first, stops the timer, disconnects connection signals, clears
the buffer, aborts a still-connected socket and schedules its Qt deferred deletion. Each timer is a
child of its socket and is destroyed with it. A stopped timer is not retained as a server child;
signal closures are released before deletion to avoid stale callbacks and ownership cycles.

The safety invariant remains prominent:

```text
NO JOURNAL = NO MUTATION
```

## Phase 4A update check and compact presentation

The About page's update action is a manual-only application flow:

```text
UpdateCheckService
        ↓
UpdateCheckWorker
        ↓
AboutPage
```

`UpdateCheckService` calls the fixed public GitHub endpoint
`https://api.github.com/repos/TIMEpings/dlsite-organizer/releases/latest` over anonymous HTTPS.
It compares the package version with the latest formal release, validates the canonical release
page URL, and fails soft on network or response errors. The flow has no authentication or token,
does not check at startup or poll in the background, and has no download or install pipeline. It is
independent from the DLsite provider.

Lightweight mode presents drag-and-drop and the latest recent operation through the
`UnifiedDropZone` presentation/layout surface. The recent-operation row is not authoritative
transaction history: SQLite and the journal remain authoritative for mutation and Undo state.

The journal transaction-creation check uses SQLite `BEGIN IMMEDIATE` to serialize the health check
and durable transaction intent across independent processes. SQLite WAL is not enabled as an
automatic side effect. Thus two near-simultaneous invocations cannot both commit overlapping
PENDING transactions; if the second reaches the filesystem after the first completed, the executor's
last-mile source/target checks fail closed without overwrite. The invariant remains:

```text
NO JOURNAL = NO MUTATION
```

`WorkLookup` is an in-memory batch record. `OrganizerService` deduplicates work numbers for one run,
calls the existing `LookupService` sequentially, isolates failures, and emits progress through the
worker boundary. It shares the persistent `LookupService` metadata cache with the manual lookup
page; fresh cache hits avoid provider calls, while stale fallback is surfaced as a plan warning.
Cooperative cancellation stops new requests; an already-running bounded HTTP request may finish,
and completed results remain in the preview.

`RenamePlanner` only computes `source_path`, `current_name`, `proposed_name`, and `target_path`.
It consumes the existing `NamingService` result and never calls a filesystem mutation API. It
marks READY, UNCHANGED, existing-target and planned-target conflicts separately, treats Windows
path comparison as case-insensitive by policy, checks that source and target remain below the
selected root, and records a warning for long target paths. It never mutates the filesystem.

The v0.4 execution boundary is intentionally separate from planning:

```text
RenamePlanner
      ↓
Preview
      ↓
User Confirmation
      ↓
RenameExecutionService
      ↓
RenameExecutor
      ↕
TransactionJournal
      ↓
Filesystem
```

`OrganizerPage` selects only READY plans and asks for explicit confirmation containing the number
of filesystem mutations. `RenameExecutor` is the application-facing execution service in this
small release; the Qt worker only runs it off the GUI thread. The executor does not call DLsite,
NamingService, RJ parsing, or a library scan. It receives already-generated plans, sorts them
deterministically by source path, performs a batch-wide preflight, and then uses only same-parent
`Path.rename`. It rejects non-READY plans, missing or changed sources, symlink/junction sources,
existing targets, paths outside the selected root, duplicate sources/targets, dependency chains/cycles,
and case-only renames. v0.4 supports direct child directory renames under one root only; it does
not move, delete, merge, overwrite, or add temporary-name graph handling.

There is no journal, no mutation: SQLite initialization or journal writes must be available before
the first filesystem operation. The transaction intent and all operation intents are committed
before mutation. After each successful rename, its operation status is committed before the next
rename starts. A failure stops all later operations and leaves earlier successful renames in a
PARTIAL transaction; it does not automatically roll them back. If a rename succeeded but its
success journal update or final transaction update failed, the transaction is marked
`RECOVERY_REQUIRED` when possible, later mutation stops, and the UI tells the user not to rerun
immediately. Journal schema changes use SQLAlchemy `create_all` for missing tables, so existing
databases gain the v0.4 tables without manual deletion or a heavyweight migration framework.

Undo is a separate journal-driven flow:

```text
TransactionJournal
      ↓
UndoService
      ↓
Preflight
      ↓
Durable Undo Intent
      ↓
Per-operation Revalidation
      ↓
Filesystem
```

Undo considers only successful, not-yet-undone operations from a COMPLETED, PARTIAL, or
UNDO_PARTIAL transaction. It validates the renamed path and original path under the original
root, rejects symlink/junctions, target conflicts and missing paths, and executes in reverse
sequence. It stops on the first filesystem or journal failure. A partial undo remains
`UNDO_PARTIAL` and can be retried after the external condition is resolved; journal failure after
a successful undo requires recovery. Existing databases receive the two journal tables
through SQLAlchemy `create_all` without manual deletion.

Batch preflight is advisory: Undo revalidates each reverse operation immediately before mutation,
after the operation's progress callback, while retaining the transaction mutation gate. It checks
root and current-entry existence, directory type, symlink/junction/reparse rejection, direct-child
path boundaries, distinct non-case-only paths, and destination conflicts (including case aliases
and dangling links). Non-following `Path.lstat()` captures ephemeral `(st_dev, st_ino,
st_birthtime_ns when available)` identities during preflight and compares root/current identities
at each operation. Missing/zero file identity fails closed. Ordinary content changes do not
invalidate identity. A known revalidation failure follows the same durable partial outcome as
a filesystem failure: the unsafe operation is untouched, earlier successes remain journaled,
and later operations do not execute.

These fingerprints are scoped to one Undo attempt; they cannot identify a replacement made before
its preflight or across restart. File-index reuse may also defeat a fingerprint (birth time reduces
that ambiguity where available). There remains an interval after the final userspace observation
and before the pathname-based rename syscall in which an external actor can replace the source or
redirect a parent. The mutation gate coordinates this application's operations only. Windows'
non-replacing rename still protects against a destination that appears at the syscall, but does
not bind the source to the checked identity. Undo is therefore a best-effort path-based recovery
aid, not an atomic identity-verified transaction. Eliminating this residual source/boundary race
would require platform-specific handle-based rename with retained, validated source and parent
handles and suitable sharing/reparse controls; that stronger mechanism is outside this hardening.

Before the first reverse mutation (including before progress callbacks), Undo commits a separate
transaction-level `undo_state = in_progress` and `undo_started_at`. This reservation uses
`BEGIN IMMEDIATE` and the same unresolved-state check as forward transaction creation. Each
operation's undo outcome is committed before the next reverse operation; these progress writes
do not clear the reservation. Terminal success atomically records `undo_state = success`,
`undo_completed_at`, and the existing `UNDONE` aggregate status. A known filesystem failure
atomically records the failed operation, `undo_state = partial`, `undo_completed_at`, and
`UNDO_PARTIAL`; the remaining operations can still be retried through ordinary confirmed Undo.
The original `completed_at` and forward operation facts are preserved throughout Undo.

An interrupted attempt remains `in_progress`, or `recovery_required` if a journal error can be
recorded. Both are unresolved and block forward creation and ordinary Undo. Even if progress,
terminal, and recovery-error writes all fail, the already-committed intent keeps mutation blocked.
The prior aggregate status (such as `COMPLETED`) is retained while Undo is unresolved: a successful
forward transaction and a later incomplete Undo are distinct facts. History presents this as
requiring inspection and also shows the prior status and recorded operation outcomes. Path
existence cannot resolve uncertainty or establish object identity. No automatic replay, resume,
or repair of an interrupted Undo is authorized by this contract.

Core schema initialization additively introduces nullable `undo_state`, `undo_started_at`, and
`undo_completed_at` columns in existing profiles. NULL means no separately recorded attempt
metadata; legacy terminal and recovery statuses retain their existing meaning. Historical rows
are not backfilled or reinterpreted. Fresh and upgraded profiles use the same mutation guard.

## Rename history and recovery inspection

The public Rename History page reads through `RenameHistoryService` and the journal repository; it
does not receive a database connection. `TransactionJournal.list_transactions(limit, offset)` returns
a bounded page of transaction summaries ordered by creation time descending, then transaction ID
descending for a stable tie-break. The UI displays 50 summaries and fetches one additional summary
to determine whether an older page exists. It loads operations in sequence order only for the
selected transaction. Completed, partial, failed, undone, and unresolved transaction and operation
states are shown from the persisted journal model.

For a PENDING or RECOVERY_REQUIRED transaction, or an unresolved Undo attempt, the page shows
the stored recovery stage, sequence,
error, and operation outcomes. Organizer keeps its existing mutation gate and offers a direct link
to that transaction's detail. The history page does not change journal state or initiate filesystem
mutations.

When an unresolved transaction is selected, the service may inspect the recorded source and target
paths with no-follow metadata reads. These are presented as **current filesystem observations** and
are kept separate from persisted journal facts. Directory, file, other, link-like, absent, and
inspection-error states describe only the path at inspection time. They do not establish that an
entry is the original directory; entries may have been removed, recreated, replaced, or changed
outside the application. A path inspection error does not prevent the journal transaction and its
recorded operations from being displayed. This inspector is read-only: it has no recovery, retry,
resume, rollback, journal edit, or journal deletion action. Manual resolution and repair remain out
of scope. Inspection itself does not change journal state.

## Thread boundary

### Runtime settings and operation ownership

Persisted `AppSettings` are validated and atomically saved before runtime publication. Lookup
owns a monotonically increasing settings generation and publishes naming, provider request
configuration, and cache policy together. Naming fields, provider timeout/metadata locale, and
cache policy are operation-semantic; startup mode, database path, and provider section/base URL
remain launch-time settings. Saving only launch-time fields does not invalidate a preview.

Each lookup, Organizer replace/append scan, and Quick Rename batch captures one configuration
at operation start (Qt workers capture it before dispatch). Running operations keep that snapshot
through every item and planning step. Quick Rename finishes its admitted batch, including execution,
under that snapshot; queued work captures settings when it actually starts.

Organizer completion carries a request ID, preview revision, and settings generation. Settings
changes invalidate existing previews and in-flight ownership, including when no rows exist yet.
Obsolete completions are discarded and cannot clear stale state or enable Execute. Append accepts
only matching generations. Cancellation retains completed rows only as non-executable diagnostic
state when no prior preview exists; it preserves a prior preview otherwise.

The database and MetadataStore remain profile-owned and are reused across cache toggles. Disabled
operations do not read/write metadata cache or observations, derive stored metadata relations, or
use stale fallback. Independent manual-review persistence and rename journals are unaffected.
Enabling applies immediately to new operations when database initialization succeeded. In-flight
operations retain their captured enabled/disabled policy; toggling never deletes stored data.

```text
validate -> atomic TOML save -> publish runtime generation N+1
                                  |                 |
                    new work snapshots N+1    invalidate old preview ownership
operation N finishes with N -> ownership comparison -> discard obsolete preview result
```

Each manual lookup and organizer batch gets one Qt `QThread` and one `QObject` worker. The
synchronous HTTP provider executes inside that worker thread. Metadata is emitted before the
optional cover download for manual lookup, and cover failure cannot turn metadata success into
failure. No asyncio/Qt bridge or unsafe thread termination is used.

## Future architecture

The next larger feature should add these application components only when implemented:

```text
RelationAnalyzer → historical evidence-backed WorkRelation
HistoryStore     → timestamped observations
```

Renaming must always follow `scan → plan → preview → explicit confirmation → preflight → journal →
execute → undo`; the scanner and planner must never mutate the filesystem. Relation analysis should accumulate explicit
evidence and confidence, not infer truth from adjacent RJ numbers. These future services can share
the existing `Work` model without importing the UI or DLsite-specific raw fields.

## Startup journal health

At startup and immediately before execution, the journal is checked for PENDING or RECOVERY_REQUIRED
transactions and for Undo attempts with `in_progress` or `recovery_required` state. Any unresolved
transaction blocks mutation. Execution facts (SUCCESS/FAILED/PENDING) remain separate from recovery
uncertainty metadata; no automatic crash recovery is attempted.
LookupService uses MetadataStore for a current cache and append-only observations. Cache freshness is independent from historical evidence: fresh hits replace neither observations nor provenance, while successful live provider responses update the current row and append one observation. Metadata persistence failures are logged and never disable live lookup; rename journal availability remains a separate safety boundary.
# Historical translation evidence

`metadata_observations` remains the source of truth for past DLsite responses. `HistoricalRelationService` reuses `TranslationRelationService` to derive explicit translation edges, then aggregates them by `(subject, relation_type, target)` with first/last observation times, count, and structured provenance. Queries return outgoing and incoming edges independently, so reverse relations remain visible offline and without target metadata.
