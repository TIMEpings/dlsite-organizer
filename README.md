# DLsite Organizer

DLsite Organizer 是一个面向 Windows 的桌面工具，用于查询 DLsite 作品元数据、整理本地作品文件夹，并在用户确认后安全重命名目录。

## 主要功能

- 通过 RJcode 查询标题、社团、系列、CV、标签、语言、年龄分级和发售日期；
- 在“查询”页显示 DLsite 明确提供的作品关系，以及本地历史中曾确认的关系；
- 扫描作品目录并生成可审阅的重命名预览；
- 自定义命名模板和元数据格式；
- 完整模式拖放预览，轻量模式拖放后立即按当前设置安全重命名；
- 使用持久化 journal 支持撤销最近一次重命名；
- 在 Windows 资源管理器右键菜单中调用轻量重命名。

作品关系只根据明确的 DLsite 信息或本地历史确认结果显示，不会把推测当作事实。应用会保存观察到的 bonus metadata 历史，但尚未实现过期限定特典关系的自动恢复或识别。

## 安装 / 运行

### Windows portable 版本

应用以 PyInstaller onedir ZIP 形式分发，不需要安装器。解压后启动 `dlsite-organizer.exe`；首次启动会自动创建本机数据目录和 SQLite schema。

当前源码版本为 `1.0.0`，仍需完成用户验收后再进行正式发布。

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

## 完整模式

完整模式是默认模式，主导航和 About footer 为：

```text
整理 → 查询 → 设置
关于 · v<version>
```

在“整理”页可选择根目录，也可将文件夹拖入窗口。扫描会查询 RJcode 并生成预览；完整模式拖入只生成预览，不会立即重命名。检查需要修改的项目后，点击“执行重命名”并明确确认。

## 轻量模式

轻量模式适合处理已经确定的作品文件夹：

```text
拖入作品文件夹 → 立即按当前设置安全重命名
```

拖入是轻量操作的明确确认，仍会执行 metadata lookup、命名规划、安全预检和 journal 记录。失败的前置检查不会修改文件；需要时可以使用“撤销最近一次”。轻量模式可通过“设置”返回完整模式或打开完整模式的设置页。

## Explorer 右键菜单

在“设置 → 资源管理器集成”中可以为当前 Windows 用户注册、更新或移除右键菜单。该功能只写入应用自己的 HKCU 注册表项，不需要管理员权限，也不会自动注册。Windows 11 中菜单可能位于“显示更多选项”。

右键菜单仅支持单个文件夹，调用与轻量模式相同的安全重命名流程。需要批量处理多个作品时，
请使用轻量模式拖放或完整模式。Explorer 多选不提供此 verb，避免静态 shell verb 启动多个
应用实例并发修改。若程序目录移动，设置页会显示路径已失效，需要重新“注册 / 更新”。

## 命名设置

普通用户应使用“设置”页配置命名模板、日期格式、CV/标签格式、元数据语言和 cache。可用变量包括：

```text
{workno} {title} {maker_name} {maker_id} {series_name}
{cv} {tags} {age} {language} {release_date}
```

旧配置仍兼容 `{rjcode}`、`{work_name}`、`{series}`、`{cv_list}`、`{cv_list_str}`、
`{tags_list}`、`{tags_list_str}`、`{age_category}` 和 `{language_code}`；这些 alias 不再作为
新模板按钮展示。

保存前会校验完整设置；已有 Organizer 预览会失效，必须重新扫描。缺失字段在界面中显示为 `—`，命名模板中的空分组会被清理。

## 数据与隐私

应用数据保存在本机，默认目录为 `%LOCALAPPDATA%\dlsite-organizer`：

| 数据 | 默认位置 | 用途 |
| --- | --- | --- |
| 设置 | `config.toml` | GUI 设置和高级持久化入口 |
| SQLite | `metadata.sqlite3` | metadata cache、历史观察和重命名 journal |
| 日志 | `logs\dlsite-organizer.log` | 有界诊断信息 |
| 封面 cache | 不落盘 | 仅保留在当前进程内存 |

应用没有 telemetry、analytics 或 remote error reporting。作品查询和可选封面下载会访问 DLsite；本地整理、预览、重命名和撤销不需要联网。

## 安全重命名 / Undo

重命名只处理所选 root 下的直接子目录，不递归、不覆盖、不删除、不移动目录。执行前会检查 root containment、目标碰撞、重复路径、symlink/junction、路径变化、依赖链和 Windows 大小写冲突。

安全边界是：

```text
Preview → 明确确认 → 预检 → journal → 文件系统修改
```

没有 journal 就不会修改文件。每次成功修改后立即更新 journal；异常或部分执行会保留状态，不会自动猜测回滚。Undo 依赖 journal 和文件未被用户替换这一前提。

## 已知限制

- Windows 是主要目标平台；Organizer 目前只支持 root 下直接子目录和 RJcode；
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

数据契约、历史关系、bonus observation、研究型关系候选和人工审阅实现细节位于 `docs/`，不属于普通用户主导航。正式发布前还需要完成真实 Explorer 拖放和右键菜单点击验收。

## License

本项目使用 MIT License，详见 [`LICENSE`](LICENSE)。运行时第三方组件的许可证和来源见 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)；它们不因本项目使用 MIT 而改变。
