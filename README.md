# dlsite-organizer

`dlsite-organizer` 是一个处于早期开发阶段的 Python 桌面应用。v0.1 提供单个
RJcode 查询流程：标准化编号、读取 DLsite 当前作品元数据、转换为统一的 `Work`
模型、生成 Windows 文件系统安全的名称，并在 GUI 中复制结果。

本版本不会扫描或重命名本地文件，不搜索特典，不推断作品关系，也不会批量遍历
RJcode。关系模型仅作为后续版本的领域契约存在。

## Requirements

- Python 3.12+
- PySide6 Essentials（Qt Core/Gui/Widgets；不安装 v0.1 未使用的 Qt Addons）
- Windows 是当前主要目标平台；架构预留 PyInstaller 打包位置，但 v0.1 尚未发布安装包

## Installation

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

## Run

```powershell
dlsite-organizer
```

也可以运行：

```powershell
python -m dlsite_organizer
```

首次启动不需要手工创建配置。可选配置文件位于
`%LOCALAPPDATA%\dlsite-organizer\config.toml`，例如：

```toml
naming_template = "[{maker_name}][{workno}] {title}"

[provider]
section = "maniax"
timeout_seconds = 15.0
```

## Quality checks

```powershell
pytest
ruff check .
pyright
```

默认测试全部离线运行。任何未来的真实 DLsite integration test 都必须标记为
`integration`，且不进入默认测试集。

## Provider status and limitations

v0.1 的 provider 只构造集中管理的公开作品页面 URL，并只尝试读取页面中的
Schema.org `Product` JSON-LD。它不依赖未经验证的 AJAX 私有字段，也不会把页面字段
直接暴露给 domain 或 UI。核心字段仅承诺 `workno` 与 `title`；maker 和发售日期只有在
语义数据可靠提供时才显示，其他字段均可缺失。

当前默认 section 是 `maniax`。v0.1 不会依次猜测请求多个 section，因此其他 DLsite
类别尚未自动识别。离线 parser fixture 是项目定义的语义契约样本，不是从线上保存的
页面快照；在实时网络无法访问 DLsite 的环境中，线上页面是否实际提供兼容 JSON-LD
必须视为尚未验证。页面语义标记变更时，provider/parser 需要相应更新。

## Roadmap

下一阶段将先验证并加固真实页面 metadata adapter，再考虑缓存。之后才会加入安全的
`scan → plan → preview → execute → transaction log → undo` 重命名流程及基于证据的
关系分析。
