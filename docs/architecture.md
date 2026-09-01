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
        ↓ QThread
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
runtime switching hides one window and shows the other in the same process. Phase C itself did not
include a CLI, registry, single-instance IPC, Explorer integration, About page, or navigation cleanup.

## Phase D Explorer context-menu invocation

Phase D adds one typed application boundary around the existing lightweight flow:

```text
Explorer shell verb (HKCU, Single, one %1 selection)
        ↓ direct packaged exe command
argparse → ApplicationInvocation(QUICK_RENAME)
        ↓
ApplicationComponents (same config.toml, cache, SQLite DB, naming and locale)
        ↓
LightweightWindow.start_quick_rename
        ↓
QuickRenameWorker → QuickRenameService
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

The Explorer verb is created only under the current user's
`HKCU\Software\Classes\Directory\shell\dlsite-organizer` key. It explicitly sets
`MultiSelectModel=Single`; its command is generated from `sys.executable` in a frozen build and
contains a quoted executable path plus the literal quoted Explorer `%1` placeholder. No source
path, Python interpreter, `cmd /c`, PowerShell, or shell interpolation is registered. Windows path
comparison is case-insensitive and expands 8.3 spelling when Windows can provide the long form,
so portable relocation is reported as a typed stale state.
Non-Windows and source/development contexts report `UNSUPPORTED` and cannot register.

The Explorer contract supports one selected directory only. Multi-select is intentionally
unsupported for this static verb because the target Windows environment cannot reliably aggregate
multiple selections into one application call; limiting the verb to `Single` prevents Explorer from
launching multiple application processes. Batch Quick Rename remains available through lightweight
drag-and-drop and the Full Organizer. The CLI continues to accept multiple directory arguments for
internal tests and future integration.

`ExplorerIntegrationService` is UI-independent and uses a small registry backend protocol. The
Settings page renders its inspection result rather than guessing from button text or persisting a
registry state in TOML. Register/update and remove are explicit; remove deletes only this verb and
its command child and is idempotent. Ordinary Windows 11 shell-verb behavior may place the command
under **显示更多选项**; no modern COM or MSIX shell extension is part of this hardening round.
The footer About entry is the only About navigation entry and derives its version from the package
version source; the main navigation remains `整理`, `查询`, `设置`.

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
Filesystem
```

Undo considers only successful, not-yet-undone operations from a COMPLETED, PARTIAL, or
UNDO_PARTIAL transaction. It validates the renamed path and original path under the original
root, rejects symlink/junctions, target conflicts and missing paths, and executes in reverse
sequence. It stops on the first filesystem or journal failure. A partial undo remains
`UNDO_PARTIAL` and can be retried after the external condition is resolved; journal failure after
a successful undo is `RECOVERY_REQUIRED`. The journal stores paths and cannot prove that a
renamed directory was not manually replaced, so Undo is a best-effort path-based recovery aid,
not an ACID or identity-verified transaction. Existing databases receive the two journal tables
through SQLAlchemy `create_all` without manual deletion.

## Thread boundary

Each manual lookup and organizer batch gets one Qt `QThread` and one `QObject` worker. The
synchronous HTTP provider executes inside that worker thread. Metadata is emitted before the
optional cover download for manual lookup, and cover failure cannot turn metadata success into
failure. No asyncio/Qt bridge or unsafe thread termination is used.

## Future architecture

The next larger feature should add these application components only when implemented:

```text
RenamePlan[] → preview → RenameExecutor
                           ↓
                    TransactionLog → undo

RelationAnalyzer → historical evidence-backed WorkRelation
HistoryStore     → timestamped observations
```

Renaming must always follow `scan → plan → preview → explicit confirmation → preflight → journal →
execute → undo`; the scanner and planner must never mutate the filesystem. Relation analysis should accumulate explicit
evidence and confidence, not infer truth from adjacent RJ numbers. These future services can share
the existing `Work` model without importing the UI or DLsite-specific raw fields.

## Startup journal health

At startup and immediately before execution, the journal is checked for PENDING or RECOVERY_REQUIRED transactions. Any unresolved transaction blocks mutation. Execution facts (SUCCESS/FAILED/PENDING) remain separate from recovery uncertainty metadata; no automatic crash recovery is attempted.
LookupService uses MetadataStore for a current cache and append-only observations. Cache freshness is independent from historical evidence: fresh hits replace neither observations nor provenance, while successful live provider responses update the current row and append one observation. Metadata persistence failures are logged and never disable live lookup; rename journal availability remains a separate safety boundary.
# Historical translation evidence

`metadata_observations` remains the source of truth for past DLsite responses. `HistoricalRelationService` reuses `TranslationRelationService` to derive explicit translation edges, then aggregates them by `(subject, relation_type, target)` with first/last observation times, count, and structured provenance. Queries return outgoing and incoming edges independently, so reverse relations remain visible offline and without target metadata.
