# Settings

The normal entry point is the GUI **设置** page. `config.toml` is the UTF-8 persistent format and
remains available for advanced manual edits. Both routes construct the same validated `AppSettings`
model.

## Naming

Default behavior is unchanged:

```text
[{maker_name}][{workno}] {title}
```

Canonical variables:

| Variable | Meaning |
| --- | --- |
| `{rjcode}` | Canonical RJ work number |
| `{title}` | Work title |
| `{maker_name}` | DLsite listing maker/circle name |
| `{maker_id}` | DLsite listing maker/circle ID |
| `{series}` | Series name, if present |
| `{cv}` | CV names using the configured separator and optional prefix/suffix |
| `{tags}` | Tags using the configured separator and max tag count |
| `{age}` | `全年龄`, `R15`, `R18`, or empty for unknown |
| `{language}` | Human-readable work language, with safe raw-code fallback |
| `{release_date}` | Release date using the configured `strftime` format |

The older aliases `{workno}`, `{work_name}`, `{series_name}`, `{cv_list}`, `{cv_list_str}`,
`{tags_list}`, `{tags_list_str}`, `{age_category}`, and `{language_code}` remain accepted. Legacy
list and age aliases retain their previous raw formatting. Templates only support simple named
placeholders. Empty templates, unknown fields, attribute/index access, conversions, and format
expressions are rejected immediately.

Missing optional fields render as empty strings. A bracketed empty segment is removed, so:

```text
[{maker_name}][{series}] {title}
```

does not produce `[][] Title` when maker and series are absent. The final name always goes through
the shared Windows sanitizer: illegal characters are replaced with the configured safe symbol,
reserved device names are prefixed, and trailing dots/spaces are removed. The Organizer's existing
long-path warning and rename safety checks remain unchanged.

## Metadata and cache

The GUI exposes the verified provider locales and stores their canonical codes:

```text
日本語       ja_jp
English      en_us
简体中文     zh_cn
繁體中文     zh_tw
한국어       ko_kr
```

The default is always `ja_jp`, independent of the UI language. Existing cache rows are not silently
reinterpreted after a locale change; fresh rows continue until TTL expiry, and live/force refreshes
use the new locale. Cache TTL is edited in hours, provider timeout in seconds, and both are applied
to subsequent requests without an application restart.

## Startup mode and drag-and-drop

The **启动模式** setting controls which window appears on the next launch:

```text
完整模式       full          (default)
轻量模式       lightweight
```

It does not change the behavior of a temporary runtime mode switch. Full mode accepts a dropped
root or explicitly selected same-parent work folders and generates a preview; it never executes
because of a drop. Lightweight mode accepts only one or more existing work folders from the same
parent. Each basename must contain exactly one valid RJcode; a missing or ambiguous RJcode is
rejected and no lookup or filesystem mutation starts.

In lightweight mode, actively dropping folders onto the clearly labelled **将 DLsite 作品文件夹拖到这里 / 拖入后将立即按当前设置重命名** zone is the confirmation for that Quick Rename action. The
operation still uses the shared NamingService, RenamePlanner, RenameExecutor, and durable
TransactionJournal. Any batch-wide input, metadata, plan, journal, recovery, or final preflight
failure is fail-closed. The **撤销最近一次** button uses the existing UndoService and asks for
explicit confirmation.

## Persistence and paths

Save performs this sequence:

```text
GUI values → AppSettings validation → UTF-8 temporary TOML → fsync → os.replace(config.toml)
```

Invalid values are never written. Existing configurations that omit new fields load with defaults;
the existing strict unknown-key policy remains in force. Saving regenerates the application-owned
TOML document, so comments and unknown keys are not preserved; this avoids adding a second TOML
editing engine. The page displays expected paths for the config file, SQLite database, and logs.
Folder buttons use Qt desktop services and open the parent directory when the file itself does not
exist.

Reset defaults loads values into the page after confirmation. It does not write the file until the
user presses **保存设置**.
