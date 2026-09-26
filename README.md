# DLsite Organizer

DLsite Organizer 是一个面向 Windows 的桌面工具，用于查询 DLsite 作品元数据、整理本地作品文件夹，并在用户确认后安全重命名目录。

## 主要功能

- 通过 RJ / BJ / VJ 作品编号查询标题、社团、系列、CV、标签、语言、年龄分级和发售日期；
- 在“查询”页显示 DLsite 明确提供的作品关系，以及本地历史中曾确认的关系；
- 扫描作品目录并生成可审阅的重命名预览；
- 自定义命名模板和元数据格式；
- 完整模式拖放预览，轻量模式拖放后立即按当前设置安全重命名；
- 使用持久化 journal 查看重命名历史和操作结果，并支持撤销最近一次重命名；
- 在 About 页面手动检查 GitHub Releases 更新，并在有新版本时打开发布页；
- 在 Windows 资源管理器右键菜单中调用轻量重命名；
- 同一 Windows 用户/profile 只运行一个长期应用实例，后续启动会激活或转发到已有实例。

作品关系只根据明确的 DLsite 信息或本地历史确认结果显示，不会把推测当作事实。应用会保存观察到的 bonus metadata 历史，但尚未实现过期限定特典关系的自动恢复或识别。

## 安装 / 运行

### Windows portable 版本

应用以 PyInstaller onedir ZIP 形式分发，不需要安装器。解压后启动 `dlsite-organizer.exe`；首次启动会自动创建本机数据目录和 SQLite schema。

当前源码版本为 `1.3.0`；Windows portable 版本支持下文所述的 Explorer Quick Rename 约定。

### 从源码运行

需要 Python 3.12+：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m dlsite_organizer
```

### 构建 Windows portable ZIP

在 Windows、canonical `.venv` 和网络可用的环境中运行：

```powershell
.\packaging\build-windows.ps1
```

构建输出 `dist\dlsite-organizer\` 和带版本号的 ZIP，并审计应用许可证、第三方许可证及 ICU/native 依赖。构建不会把 `research/`、测试、源码 checkout 或 `.venv` 复制到 artifact。

## 启动模式与完整模式

全新 profile 默认启动轻量模式；已有配置中的 `startup_mode` 会原样保留。完整模式可从设置或运行时切换进入，主导航和 About footer 为：

```text
整理 → 查询 → 设置 → 重命名历史
关于 · v<version>
```

在“整理”页可选择根目录，也可将文件夹拖入窗口。点击“扫描并预览”会查询 RJ / BJ / VJ 作品编号并替换当前预览；完整模式拖入会将新的唯一项目追加/合并到当前预览，不会立即重命名。可用“删除选中项”移除一项或多项，也可用“清空”清除当前预览；这两项都不会删除或改名磁盘上的目录。检查需要修改的项目后，点击“执行重命名”并明确确认。

扫描、拖放、删除和清空本身都只改变当前内存中的 Preview；只有“执行重命名”才会在 journal 记录后执行文件系统修改。重新拖入同一个 source directory 会按规范化 Windows 路径 upsert，保留原有行位置并刷新预览状态。命名设置变化会使现有 Preview 失效，必须重新扫描或清空后再拖放。

## 单实例运行

同一 Windows 用户和应用 profile 同时只保留一个活动的 DLsite Organizer。再次正常启动时，
已有应用会被激活，新的启动进程随后退出；不会创建第二个窗口、数据库连接或服务图。

从资源管理器发起的 Quick Rename 会交给已有应用的轻量模式处理，发起进程完成转发后退出。
连续发起的多个 Quick Rename 会在活动应用内按提交顺序串行处理，保持一次用户操作的 batch、
journal 和 Undo 语义。应用退出时会先停止接收新操作，完成当前安全操作后再释放本地数据资源。

## 轻量模式

轻量模式适合处理已经确定的作品文件夹：

```text
拖入作品文件夹 → 立即按当前设置安全重命名
```

拖入是轻量操作的明确确认，仍会执行 metadata lookup、命名规划、安全预检和 journal 记录。失败的前置检查不会修改文件；需要时可以使用“撤销最近一次”。轻量模式可通过“设置”返回完整模式或打开完整模式的设置页。

## 版本更新

About 页面提供“检查更新”。只有用户主动点击时，应用才会通过匿名 HTTPS 请求公开的
GitHub Releases API，并将当前版本与最新正式 Release 比较。发现新版本时可打开对应的
GitHub Release 页面；应用不会自动下载或安装，也不需要 GitHub 登录或 token。应用没有
启动时检查或后台轮询。

## Explorer 右键菜单

在“设置 → 资源管理器集成”中可以为当前 Windows 用户注册、更新或移除右键菜单。该功能只写入应用自己的 HKCU 注册表项，不需要管理员权限，也不会自动注册。Windows 11 经典菜单中的入口可能位于“显示更多选项”；不保证出现在 Windows 11 的第一层菜单。

Explorer 右键 Quick Rename 支持同时选择多个文件夹。一次 Explorer 选择作为一个批处理请求处理，最多 32 个文件夹；它使用一次 journal transaction，成功后可用一次 Undo 恢复整个批次。无效的混合选择会整体拒绝，不会只处理其中一部分。独立的 Explorer 点击保持独立事务，不会按时间窗口合并。

portable package 已包含原生 x64 Shell helper。helper 只负责接收 Explorer 选择并通过本地 IPC 转发给已有应用；它是短生命周期的 transient helper，不是 daemon 或后台服务。若程序目录移动，设置页会显示路径已失效，需要重新“注册 / 更新”。

## 命名设置

普通用户应使用“设置”页配置命名模板、日期格式、CV/标签格式、元数据语言和 cache。可用变量包括：

```text
{workno} {title} {maker_name} {maker_id} {series_name}
{cv} {tags} {age} {language} {release_date}
```

全新 profile 的命名默认值是：

```text
[{workno}][{maker_name}]{title}
```

全新 profile 的 metadata locale 会匹配支持的系统语言（日本語 `ja_jp`、English `en_us`、
简体中文 `zh_cn`、繁體中文 `zh_tw`、한국어 `ko_kr`），无法匹配时回退到 `ja_jp`。
已有配置和显式保存的字段不会因系统语言或本轮默认值变化而覆盖。

旧配置仍兼容 `{rjcode}`、`{work_name}`、`{series}`、`{cv_list}`、`{cv_list_str}`、
`{tags_list}`、`{tags_list_str}`、`{age_category}` 和 `{language_code}`；这些 alias 不再作为
新模板按钮展示。

保存前会校验完整设置；只有会影响目标目录名的命名设置变化才会使已有 Organizer 预览失效，必须重新扫描。缺失字段在界面中显示为 `—`，命名模板中的空分组会被清理。

## 数据与隐私

应用数据保存在本机，默认目录为 `%LOCALAPPDATA%\dlsite-organizer`：

| 数据 | 默认位置 | 用途 |
| --- | --- | --- |
| 设置 | `config.toml` | GUI 设置和高级持久化入口 |
| SQLite | `metadata.sqlite3` | metadata cache、历史观察和重命名 journal |
| 日志 | `logs\dlsite-organizer.log` | 有界诊断信息 |
| 封面 cache | 不落盘 | 仅保留在当前进程内存 |

应用没有 telemetry、analytics 或 remote error reporting。用户主动请求 metadata 或封面操作时
才会访问 DLsite；只有用户主动点击“检查更新”时才会以匿名 HTTPS 访问 GitHub 公开 Releases。
Explorer helper 仅使用本机 COM / IPC，不进行 metadata lookup。应用没有启动时检查、后台轮询、
自动更新下载或后台 daemon；本地整理、预览、重命名和撤销不需要联网。

## 安全重命名 / Undo

重命名只处理所选 root 下的直接子目录，不递归、不覆盖、不删除、不移动目录。执行前会检查 root containment、目标碰撞、重复路径、symlink/junction、路径变化、依赖链和 Windows 大小写冲突。

安全边界是：

```text
Preview → 明确确认 → 预检 → journal → 文件系统修改
```

没有 journal 就不会修改文件。每次成功修改后立即更新 journal；异常或部分执行会保留状态，不会自动猜测回滚。Undo 依赖 journal 和文件未被用户替换这一前提。

## 重命名历史与恢复检查

“重命名历史”页按时间分页显示本机 SQLite 中持久化的重命名事务。选择事务可查看记录的根目录、创建和完成时间、事务状态，以及按顺序排列的 source → target 操作、执行结果、撤销结果和已保存的错误。成功、部分完成、失败、已撤销和需要检查等状态按 journal 实际记录展示。

对于仍未解决的事务，页面会显示恢复阶段、恢复序号、恢复错误和相关操作结果。Organizer 继续阻止新的重命名和普通撤销，并提供“查看事务详情”入口。

未解决事务的“当前文件系统观察”会单独显示 source 和 target 路径在查看时是否存在、条目类型或无法检查。观察只读取当前路径元数据，不证明当前条目就是原先参与重命名的目录；路径可能已被删除、重建或外部修改。重命名历史页是只读检查工具，不提供恢复、修复、重试、强制撤销、编辑或删除 journal 的操作；恢复和修复仍需在 v1.3.0 范围之外人工处理。

## 已知限制

- Windows 是主要目标平台；Organizer 目前只支持 root 下直接子目录和 RJ / BJ / VJ 作品编号；
- live Lookup / 强制刷新需要访问 DLsite，网络错误不会阻止应用启动；
- 右键菜单是普通 Registry shell verb，不是 Windows 11 modern shell extension；
- 过期 limited-bonus relation 的自动恢复/识别、关系概率、候选评分和批量自动确认尚未实现；
- 封面没有持久化磁盘 cache，应用重启后需要重新获取。

## 开发

默认测试全部离线运行；live integration test 必须标记 `integration`。canonical checks：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest
ruff check .
pyright
python -m compileall src
python -m pip check
git diff --check
```

数据契约、历史关系、bonus observation、研究型关系候选和人工审阅实现细节位于 `docs/`，不属于普通用户主导航。v1.2.0 的 Explorer 集成契约和安全边界见上文，架构细节位于 `docs/`。

## 项目署名

- Developer & Maintainer: TIMEpings
- Copyright © 2026 TIMEpings
- License: MIT

## License

本项目使用 MIT License，详见 [`LICENSE`](LICENSE)。运行时第三方组件的许可证和来源见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)；它们不因本项目使用 MIT 而改变。
