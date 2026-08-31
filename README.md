# dlsite-organizer

`dlsite-organizer` 是一个面向 Windows 的桌面工具，用于查询 DLsite 作品信息、扫描本地作品目录，并在用户确认后安全重命名目录。

> 当前源码版本仍为 `1.0.0`，但此前的 release candidate 已撤回；本分支是未发布的开发快照。
> 当前没有 `v1.0.0` tag，也没有公开发行版。

## What it is

应用围绕一个本地优先的工作流设计：

```text
Lookup / Scan → inspect → Preview → explicit confirmation → safe Execute → Undo if needed
```

主要能力包括：

- 通过 RJcode 查询并展示 DLsite 当前作品 metadata；
- 扫描所选根目录的直接子目录，生成可审阅的重命名 preview；
- 使用 `Preview → explicit confirmation → preflight → durable journal → filesystem mutation` 执行安全重命名；
- 从 durable journal 执行 Undo，并在 PENDING、PARTIAL 或 RECOVERY_REQUIRED 状态下阻止不安全的继续操作；
- 支持完整模式拖放预览，以及共享安全管线驱动的轻量模式即时重命名；
- 区分当前和历史 DLsite 明确翻译关系；
- 在本地保存 metadata observations、人工 review history 和 bonus observation evidence。

## Installation

### Windows distribution

此前候选版的 ZIP 文件名和构建说明仅作历史记录；当前没有可供下载的正式 ZIP。正式发布前请从源码验证并重新执行构建流程。

下载并解压 ZIP 后，首次启动会自动创建用户数据目录和 SQLite schema。

### Run from source

需要 Python 3.12+：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m dlsite_organizer
```

### Build the Windows distribution

在 Windows、canonical `.venv` 和网络可用的环境中运行：

```powershell
.\packaging\build-windows.ps1
```

脚本使用 PyInstaller onedir 构建，输出 `dist\dlsite-organizer\` 和带版本号的 zip。构建不会把 `research/`、测试、源码 checkout 或 `.venv` 复制到 artifact。

## Quick Start

1. 启动应用，在“查询”页输入例如 `RJ01609020`。
2. 查看当前 metadata、来源和关系说明；必要时使用“强制刷新”。
3. 在“设置”页配置命名模板、元数据语言和 cache；实时预览使用内置中性示例。
4. 在“整理”页选择本地根目录并扫描。
5. 逐项检查 preview，只保留确定要修改的 READY 项。
6. 明确确认后执行；需要时从最近一次 journaled transaction 执行 Undo。

也可以在“设置”中选择下次启动的完整模式或轻量模式。完整模式把拖入根目录或作品文件夹
解释为预览入口；轻量模式则只接受同一父目录下、名称包含唯一 RJcode 的作品文件夹，拖到
明确标注“拖入后将立即按当前设置重命名”的区域即视为本次 Quick Rename 确认。轻量模式仍
执行 lookup、当前命名设置、planner、executor preflight 和 durable journal，失败批次不会
偷偷只修改一部分目录。

应用启动时会自动创建用户数据目录和 SQLite schema，不需要预先创建数据库或配置文件。

## Rename safety

重命名只处理所选 root 下的直接子目录，不递归、不覆盖、不删除、不移动目录，也不处理 VJ/BJ Organizer。执行前会检查 root containment、目标碰撞、重复路径、symlink/junction、路径变化、依赖链、cycle 和 Windows 大小写不敏感冲突。

必须遵守：

```text
NO JOURNAL = NO MUTATION
```

事务 intent 在首次 filesystem mutation 前持久化；每次成功 mutation 后立即更新 journal。失败会停止后续操作并保留 partial 状态，不自动猜测或回滚。Undo 依赖 journal 中的路径和用户未手动替换目录这一前提；它不是 ACID 或身份证明系统。PENDING、RECOVERY_REQUIRED 和 Undo 冲突必须先人工处理。

## Drag & Drop and Lightweight Mode

完整模式：

```text
drop root                       → scan direct children → Preview
drop one RJ folder              → Preview that folder only
drop same-parent RJ folders     → Preview those folders only
```

拖放不会绕过完整模式的用户复核与执行确认。轻量模式：

```text
drop one or more work folders   → QuickRenameService → safe rename
```

轻量批次要求所有目录存在、是普通本地目录、来自同一父目录且各自只有一个 RJcode；任何
输入错误、metadata lookup 失败、目标冲突、规划失败或 journal 不可用都会在文件修改前
拒绝整批。已经是目标名的目录显示“无需重命名”；部分执行结果会显示“部分操作已完成”，
并沿用现有 journal/Undo/恢复边界。

## Explorer context menu (Phase D)

Windows 打包版可以在“设置 → 资源管理器集成”中主动注册当前程序的右键菜单。注册只写入
当前用户的 `HKCU\Software\Classes\Directory\shell\dlsite-organizer`，不需要管理员权限，
也不会自动注册。菜单项名称为 **使用 DLsite Organizer 重命名**，命令直接启动当前 frozen
executable：

```text
"<current packaged exe>" --quick-rename "%1"
```

每次 Explorer 调用只处理一个文件夹，并执行与轻量模式相同的
`QuickRenameService → LookupService → NamingService → RenamePlanner → Preflight → Journal →
RenameExecutor` 管线；Explorer 集成没有新的重命名实现。Quick Action 窗口会保留结果并继续
通过 `UndoService` 提供显式撤销。失败前置检查会明确显示未修改任何文件，部分执行或恢复状态
不会被伪装成无修改。

这是 portable ZIP 的 per-user 注册：如果程序目录移动，设置页会显示“路径已失效 / 需要更新”，
点击“注册 / 更新”即可覆盖为当前 exe；“移除”只删除本应用创建的 verb 和 `command` 子键，
删除不存在的注册也是安全的。开发源码运行时注册按钮会禁用，因为右键菜单只能可靠指向打包版。
删除 portable 程序目录前，建议先在设置中移除右键菜单。根据 Windows 版本和 Explorer 行为，
命令可能出现在“显示更多选项”菜单中。

## Data and storage

Windows 默认数据目录为 `%LOCALAPPDATA%\dlsite-organizer`：

| Data | Default path | Purpose |
| --- | --- | --- |
| Settings | `config.toml` | GUI 设置的 UTF-8 持久化文件，也支持高级手工编辑 |
| SQLite | `metadata.sqlite3` | Current cache, metadata history, manual reviews and rename journal |
| Logs | `logs\dlsite-organizer.log` | Bounded application diagnostics |
| Cover cache | none on disk | Successfully downloaded covers are kept only in the current process memory |

可以在 `config.toml` 中设置 `database_path`；相对路径相对于配置文件目录解析。旧 SQLite 数据库使用 additive upgrade：旧 observation、translation history、manual reviews 和 rename journal 不会被删除或重写。`metadata_observations.bonus_evidence_json = NULL` 表示 legacy / not captured / unknown，不等同于明确的 `bonuses = []`。

## Settings and naming

普通用户应使用“设置”页；页面和 TOML 共用同一个已校验的 `AppSettings` schema。保存前会先构造并校验完整设置，再通过临时文件和 `os.replace` 原子写入 `config.toml`。保存命名设置后，下一次 Organizer preview 立即使用新规则；已有 preview 会标记为 stale，必须重新扫描。

默认命名模板仍是 `[{maker_name}][{workno}] {title}`，所以不修改设置不会改变现有默认结果。canonical 变量是 `{rjcode}`、`{title}`、`{maker_name}`、`{maker_id}`、`{series}`、`{cv}`、`{tags}`、`{age}`、`{language}` 和 `{release_date}`。`{workno}`、`{series_name}`、`{cv_list}`、`{tags_list}`、`{age_category}` 等旧别名继续兼容。

`{cv}` 和 `{tags}` 使用设置页中的分隔符；CV 可配置前后缀，标签数量 `0` 表示不限且保持 DLsite source 顺序。缺失字段为空，`[ ... ]` 空段会被清理。命名器仍负责 Windows 非法字符、保留设备名、尾随点/空格的安全处理。

## Relation provenance

界面和文档严格区分四层：

1. **Current DLsite Confirmed**：当前响应中明确声明的翻译关系。
2. **Historical DLsite Confirmed**：本地历史 observation 中曾明确声明、当前响应可能已不再显示的关系。
3. **Manual User Review**：用户在本地做出的 append-only review；不是 DLsite 官方事实。
4. **Derived Candidate**：按 `same-maker-same-date v1` 从本地 metadata 派生的待审建议。

Candidate 不是 confirmed relation、不是 AI detection，也没有 score、probability、auto-confirm 或 auto-learning。Candidate review queue 不会批量联网；本地筛选、选中和人工标注不会调用 provider。

## Manual Review and evaluation

“候选审阅”是 advanced / research / calibration 工作流，不是首发主要卖点。它保存人工决定和 review-time evidence，支持历史复核；evaluation dataset 只用于描述 workflow 和 taxonomy 的人工使用情况。24-pair Pilot 只说明该 workflow / taxonomy 被人工使用过，不代表 accuracy 或 precision。

## Historical bonus wording

Historical bonus metadata observed by the application is retained from v0.10.3 onward.

Automatic recovery/detection of expired limited-bonus relations is not implemented in v1.0.0.

Existing observations suggest bonus metadata may be transient, but this is not claimed as a universal DLsite contract.

应用只保存成功 response 当时报告的 bonus observation；它不会自动检测、恢复或创建 `BONUS_OF` relation。

## Known limitations

- Windows 是主要目标平台；当前 Organizer 仅支持 root 下直接子目录和 RJcode。
- 默认 provider 使用显式配置的 `maniax` section；不会尝试多个 section 猜测。
- live Lookup / force refresh 会访问 DLsite；网络错误只影响该次 lookup，不应阻止应用启动。
- metadata locale、cache TTL 和 provider timeout 可在“设置”页修改；locale 变化不会静默重解释已有 cache，下一次 live/force refresh 使用新 locale。
- Review Queue 的本地浏览、筛选、选中和 review 不调用 provider。
- cover 没有持久化磁盘 cache；应用重启后需要重新通过 lookup 获取封面。
- Explorer 右键菜单是普通 Registry shell verb，不是 Windows 11 modern shell extension；首版
  仅支持每次一个文件夹。若无法进行实际 Explorer 菜单点击，仍可用打包版
  `--quick-rename <directory>` 直接验证同一调用边界。
- 不实现过期 limited-bonus relation 的自动恢复/检测、关系概率、候选评分、批量自动确认或 auto-learning。

## Privacy and network behavior

Manual Review notes 只保存在本地 SQLite，不写入日志。应用没有 telemetry、analytics 或 remote error reporting。网络访问仅来自 live DLsite metadata lookup/refresh，以及 lookup 流程中的可选 cover 下载；本地 Review Queue 不因刷新或选中而联网。

## Development

默认测试全部离线运行；live integration test 必须标记 `integration`，默认被 deselect。canonical checks：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest
ruff check .
pyright
python -m compileall src
python -m pip check
git diff --check
```

Release procedure 见 [`docs/release-checklist.md`](docs/release-checklist.md)。数据契约、candidate evidence、manual review 和 bonus preservation 的细节见 `docs/` 中对应文档。

## License

本项目使用 MIT License，详见 [`LICENSE`](LICENSE)。运行时第三方组件的许可证和来源见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)；它们不因本项目使用 MIT 而改变。
