# Settings

The normal entry point is the GUI **设置** page. `config.toml` is the UTF-8 persistent format and
remains available for advanced manual edits. Both routes construct the same validated `AppSettings`
model.

## Naming

Fresh profiles use this naming default:

```text
[{workno}][{maker_name}]{title}
```

Canonical variables:

| Variable | Meaning |
| --- | --- |
| `{workno}` | Canonical RJ / BJ / VJ work number from the normalized `Work` |
| `{title}` | Work title |
| `{maker_name}` | DLsite listing maker/circle name |
| `{maker_id}` | DLsite listing maker/circle ID |
| `{series_name}` | Series name, if present |
| `{cv}` | CV names using the configured separator and optional prefix/suffix |
| `{tags}` | Tags using the configured separator and max tag count |
| `{age}` | `全年龄`, `R15`, `R18`, or empty for unknown |
| `{language}` | Human-readable work language, with safe raw-code fallback |
| `{release_date}` | Release date using the configured `strftime` format |

The legacy/compatibility aliases `{rjcode}`, `{work_name}`, `{series}`, `{cv_list}`,
`{cv_list_str}`, `{tags_list}`, `{tags_list_str}`, `{age_category}`, and `{language_code}` remain
accepted. They are intentionally not shown as insertion buttons for new templates. Legacy list and
age aliases retain their previous raw formatting. Templates only support simple named placeholders.
Empty templates, unknown fields, attribute/index access, conversions, and format expressions are
rejected immediately.

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

For a fresh profile or a missing config field, the default is derived from Qt's system locale:
Japanese maps to `ja_jp`, English to `en_us`, Simplified Chinese to `zh_cn`, Traditional Chinese
to `zh_tw`, and Korean to `ko_kr`. Unsupported languages fall back to `ja_jp`. This is the
metadata display/request locale, not `Work.language`. Existing explicit values are never replaced
when the system locale changes. Existing cache rows are not silently reinterpreted after a locale
change; fresh rows continue until TTL expiry, and live/force refreshes use the new locale. Cache TTL
is edited in hours, provider timeout in seconds, and both are applied to subsequent requests without
an application restart.

Qt's Chinese script is preferred over a language-only prefix: `zh_CN` and `zh_SG` map to `zh_cn`,
while `zh_TW`, `zh_HK`, and `zh_MO` map to `zh_tw`. This keeps Traditional Chinese from being
mistaken for Simplified Chinese on systems whose locale name is not sufficient by itself.

## Startup mode and drag-and-drop

The **启动模式** setting controls which window appears on the next launch:

```text
完整模式       full
轻量模式       lightweight
```

Fresh profiles default to `lightweight`. An existing explicit `startup_mode = "full"` remains
`full`; loading a legacy file with the field omitted uses the new default and does not rewrite the
file until the user saves it.

It does not change the behavior of a temporary runtime mode switch. Full mode accepts a dropped
root or explicitly selected same-parent work folders and generates a preview; it never executes
because of a drop. Lightweight mode accepts only one or more existing work folders from the same
parent. Each basename must contain exactly one valid RJ / BJ / VJ work number; a missing or ambiguous work number is
rejected and no lookup or filesystem mutation starts.

In lightweight mode, actively dropping folders onto the clearly labelled **将 DLsite 作品文件夹拖到这里 / 拖入后将立即按当前设置重命名** zone is the confirmation for that Quick Rename action. The
operation still uses the shared NamingService, RenamePlanner, RenameExecutor, and durable
TransactionJournal. Multiple selected folders must be direct siblings. Any batch-wide input,
metadata, plan, journal, recovery, or final preflight failure is fail-closed; READY folders are
committed as one transaction and the **撤销最近一次** button uses the existing UndoService to
restore the whole transaction with one confirmation.

## Explorer integration

The **资源管理器集成** group is a live inspection of the application-owned Windows Registry verb;
its state is not stored in `config.toml`. It shows one of:

```text
未注册
已注册
路径已失效 / 需要更新
不支持
读取失败
```

On a packaged Windows build, **注册 / 更新** writes only the current user's
`HKCU\Software\Classes\Directory\shell\dlsite-organizer` key and its `command` child. It also
sets the verb's `MultiSelectModel` value to `Single`, and the command is generated as a direct
executable invocation:

```text
"<current packaged exe>" --quick-rename "%1"
```

The executable path is quoted by the standard Windows argument formatter, so spaces and non-ASCII
paths are preserved; no `cmd.exe`, PowerShell, or shell trampoline is used. The Explorer contract is
single selected directory only. Multi-select is intentionally unsupported by this static verb;
batch processing remains available through lightweight drag-and-drop or Full Organizer. The CLI
still accepts one or more directory arguments for internal tests and future integration. **移除**
deletes only this verb and its `command` child, is idempotent, and never removes the parent
`Directory\shell` key or unrelated verbs. Registration is explicit, per-user, and needs no
administrator rights.

The source/development run does not register a Python interpreter or source entry point; its buttons
are disabled with a message that Explorer integration is available only in the packaged version.
Because the distribution is a portable ZIP, moving the application makes an existing absolute path
stale. Start the application from its new location and use **注册 / 更新**. Before deleting the
portable application directory, use **移除** first. On Windows 11 and some Explorer configurations,
the ordinary shell verb may appear under **显示更多选项**.

The Explorer action opens one lightweight Quick Action window and calls the same
`QuickRenameService` as drag-and-drop. It does not show a second ordinary confirmation dialog,
but it still performs input validation, lookup, naming, planning, final preflight, durable journal
creation, and executor mutation. A fresh metadata cache is reused, and an unresolved journal
blocks the action. Independent concurrent invocations remain serialized at journal transaction
creation as defense-in-depth. This single-selection Explorer restriction is intentional: Windows
Explorer's static right-click verb cannot reliably aggregate multi-select into one application
call in the target environment, so v1.0 avoids the unsafe multiple-process behavior; future
versions may revisit it.

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
user presses **保存设置**. The reset values are the current fresh-profile defaults: lightweight
startup, `[{workno}][{maker_name}]{title}`, and the current supported system locale or `ja_jp`
fallback.
