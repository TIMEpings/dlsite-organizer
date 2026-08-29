# Architecture

## Current architecture

The v0.1 lookup flow is deliberately small:

```text
PySide6 LookupPage
        ↓ signal / QThread worker
LookupService ─────→ NamingService
        ↓
WorkProvider protocol
        ↓
DlsiteProvider → page parser → public DLsite product page
        ↓
      Work
```

`Work`, work-code validation, safe naming primitives, and relation contracts live in the domain
package. The domain imports no Qt, HTTP, parser, or database implementation. `LookupService`
coordinates validation, provider access, error translation, and naming without knowing widgets.
The UI owns thread lifecycle and presentation only; `LookupWorker` invokes services and emits
values, and never manipulates widgets or implements parsing policy.

The DLsite adapter builds section URLs in one place and converts provider-local raw HTML into the
domain `Work`. v0.1 conservatively reads only Schema.org `Product` JSON-LD. The offline fixture is a
contract sample rather than a captured live page. Because this environment could not establish a
DLsite connection during development, compatibility with the current live markup remains an
explicit integration limitation. Optional fields degrade to missing values; a missing trustworthy
title is a parse failure rather than invented metadata.

SQLite is initialized through a small SQLAlchemy `Database` object. Its only table reserves a
minimal shape for future historical observations. Lookups are not automatically persisted in
v0.1, so the project does not yet pretend to provide a cache or history repository.

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

RelationAnalyzer → evidence-backed WorkRelation
HistoryStore     → timestamped observations
```

Renaming must always follow `scan → plan → preview → execute → transaction log → undo`; scanning
must never immediately mutate the filesystem. Relation analysis should accumulate explicit
evidence and confidence, not infer truth from adjacent RJ numbers. These future services can share
the existing `Work` model without importing the UI or DLsite-specific raw fields.
