# dlsite-organizer

`dlsite-organizer` 是一个面向 Windows 的桌面工具，用于查询 DLsite 作品信息、扫描本地作品目录，并在用户确认后安全重命名目录。

> 当前源码版本是 `0.10.3`。v1.0.0 首发准备已完成可自动化部分，但在许可证选择和最终 Windows 人工 smoke 前不会宣称正式发布。

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
- 区分当前和历史 DLsite 明确翻译关系；
- 在本地保存 metadata observations、人工 review history 和 bonus observation evidence。

## Installation

### Windows distribution

正式发布时，普通用户只需解压 `dlsite-organizer-1.0.0-windows-x64.zip` 并运行其中的 `dlsite-organizer.exe`，不需要安装 Python 或项目依赖。当前仓库仍处于 release gate 阶段，最终 zip 尚未作为 v1.0.0 发布。

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
3. 在“整理”页选择本地根目录并扫描。
4. 逐项检查 preview，只保留确定要修改的 READY 项。
5. 明确确认后执行；需要时从最近一次 journaled transaction 执行 Undo。

应用启动时会自动创建用户数据目录和 SQLite schema，不需要预先创建数据库或配置文件。

## Rename safety

重命名只处理所选 root 下的直接子目录，不递归、不覆盖、不删除、不移动目录，也不处理 VJ/BJ Organizer。执行前会检查 root containment、目标碰撞、重复路径、symlink/junction、路径变化、依赖链、cycle 和 Windows 大小写不敏感冲突。

必须遵守：

```text
NO JOURNAL = NO MUTATION
```

事务 intent 在首次 filesystem mutation 前持久化；每次成功 mutation 后立即更新 journal。失败会停止后续操作并保留 partial 状态，不自动猜测或回滚。Undo 依赖 journal 中的路径和用户未手动替换目录这一前提；它不是 ACID 或身份证明系统。PENDING、RECOVERY_REQUIRED 和 Undo 冲突必须先人工处理。

## Data and storage

Windows 默认数据目录为 `%LOCALAPPDATA%\dlsite-organizer`：

| Data | Default path | Purpose |
| --- | --- | --- |
| Settings | `config.toml` | Optional TOML configuration |
| SQLite | `metadata.sqlite3` | Current cache, metadata history, manual reviews and rename journal |
| Logs | `logs\dlsite-organizer.log` | Bounded application diagnostics |
| Cover cache | none on disk | Successfully downloaded covers are kept only in the current process memory |

可以在 `config.toml` 中设置 `database_path`；相对路径相对于配置文件目录解析。旧 SQLite 数据库使用 additive upgrade：旧 observation、translation history、manual reviews 和 rename journal 不会被删除或重写。`metadata_observations.bonus_evidence_json = NULL` 表示 legacy / not captured / unknown，不等同于明确的 `bonuses = []`。

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
- Review Queue 的本地浏览、筛选、选中和 review 不调用 provider。
- cover 没有持久化磁盘 cache；应用重启后需要重新通过 lookup 获取封面。
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

本仓库目前没有 `LICENSE` 文件。许可证必须由项目所有者选择并加入后，才可以进行 public release；本项目不会在没有用户决定的情况下假设许可证。
