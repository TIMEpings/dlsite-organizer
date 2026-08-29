# Architecture

## Current architecture

The v0.4 lookup flow remains deliberately small:

```text
PySide6 LookupPage
        ↓ signal / QThread worker
LookupService ─────→ NamingService
        ↓
WorkProvider protocol
        ↓
DlsiteProvider → product_info_ajax parser → ProductInfoAjaxSource ─┐
        ↓ (only when unusable)                                      │
HTML JSON-LD parser → HtmlProductSource ────────────────────────────┤
                                                                     ↓
                                                                   Work
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

`DlsiteSite` builds every section-scoped URL in one place. `DlsiteProvider` first requests the
candidate `product/info/ajax?product_id=<WORKNO>` source. A successful response is used only when it
validates as `ProductInfoAjaxSource`; otherwise the provider makes one HTML fallback request. There is
no brute-force section probing. The configured section remains a provider implementation detail and
is the narrow boundary where a future resolver can be introduced.

`ProductInfoAjaxSource` and its nested `TranslationInfoSource` are provider-local Pydantic DTOs with
`extra="allow"`. They retain a reviewed metadata subset and translation evidence without polluting
`Work`. The validated AJAX envelope key establishes the queried `Work` identity;
an optional nested `product_id` is retained separately and must agree when present. The raw
`regist_date` timestamp is retained as `regist_datetime`, while `Work.release_date` receives only its
date component for the current UI. `HtmlProductSource` is a separate provider-local dataclass for
Schema.org Product JSON-LD. Each source is normalized into `Work` only after extraction; the domain
never imports HTTP, HTML, JSON, or DLsite DTOs.

`DlsiteProvider.fetch_work_lookup()` is the source-aware extension point. It returns normalized
`Work` plus the already-validated AJAX DTO in one request flow (or no AJAX DTO when HTML fallback
was used). `LookupService` consumes only the narrow `translation_info` view and passes it to
`TranslationRelationService`; the existing `WorkProvider.fetch_work()` protocol remains
metadata-only for simple callers.

`TranslationRelationService` is the only place that interprets DLsite translation topology. A true
`is_original`, `is_parent`, or `is_child` flag determines `TranslationRole`; concrete target RJcodes
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

SQLite is initialized through a small SQLAlchemy `Database` object. It contains the historical
observation placeholder plus the v0.4 `rename_transactions` and `rename_operations` journal tables.
Lookups and translation relations are still not automatically persisted; the project does not
pretend to provide a metadata cache or a relation history repository.

Settings use `tomllib` and validated Pydantic models. Missing configuration is normal and uses
built-in defaults. Invalid present configuration is surfaced rather than silently ignored.

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
        ↓ direct RJcode batch, sequential and deduplicated
LookupService → WorkLookup[] → Work
        ↓
NamingService → RenamePlanner → RenamePlan[]
        ↓
OrganizerPage preview table
```

`FolderScanner` reads only direct child directories. It excludes regular files, hidden entries and
symlink directories, records no-code children as skipped, and keeps ambiguous multi-code folders
as visible warning candidates. `extract_work_codes()` lives beside `WorkCode` parsing, so folder
extraction and manual RJ validation share one normalization rule. V0.3 intentionally does not
recursively inspect work contents or support VJ/BJ Organizer candidates.

`WorkLookup` is an in-memory batch record. `OrganizerService` deduplicates RJcodes for one run,
calls the existing `LookupService` sequentially, isolates failures, and emits progress through the
worker boundary. There is no persistent metadata cache. Cooperative cancellation stops new
requests; an already-running bounded HTTP request may finish, and completed results remain in the
preview.

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
