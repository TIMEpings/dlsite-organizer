# Architecture

## Current architecture

The v0.2 lookup flow is deliberately small:

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

SQLite is initialized through a small SQLAlchemy `Database` object. Its only table reserves a
minimal shape for future historical observations. Lookups are not automatically persisted in
v0.2, and translation relations are not written to SQLite; the project does not yet pretend to
provide a cache or history repository.

Settings use `tomllib` and validated Pydantic models. Missing configuration is normal and uses
built-in defaults. Invalid present configuration is surfaced rather than silently ignored.

## Thread boundary

Each lookup gets one Qt `QThread` and one `QObject` worker. The synchronous HTTP provider executes
inside that worker thread. Metadata is emitted before the optional cover download, and cover
failure cannot turn metadata success into failure. No asyncio/Qt bridge is used.

## Future architecture

The next larger feature should add these application components only when implemented:

```text
FolderScanner → RenamePlanner → preview → RenameExecutor
                                      ↓
                               TransactionLog → undo

RelationAnalyzer → historical evidence-backed WorkRelation
HistoryStore     → timestamped observations
```

Renaming must always follow `scan → plan → preview → execute → transaction log → undo`; scanning
must never immediately mutate the filesystem. Relation analysis should accumulate explicit
evidence and confidence, not infer truth from adjacent RJ numbers. These future services can share
the existing `Work` model without importing the UI or DLsite-specific raw fields.
