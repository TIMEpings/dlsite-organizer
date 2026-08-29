# dlsite-organizer

`dlsite-organizer` 是一个处于早期开发阶段的 Python 桌面应用。v0.3 支持手动 RJcode
查询、已确认翻译关系、本地作品文件夹扫描，以及安全的重命名预览：扫描目录名中的
RJcode，读取 DLsite metadata，通过统一的 `NamingService` 生成目标目录名，并在 GUI 中
审查 `RenamePlan`。

> **v0.3 Organizer is preview-only.** 当前不会重命名、移动或删除本地文件，也不会创建
> 快捷方式。关系只来自 DLsite structured metadata 明确声明的字段。

扫描默认只读取用户选择根目录的直接子目录；无 RJcode 的目录会跳过并计数，包含多个不同
RJcode 的目录会作为 ambiguous 行显示。当前 Organizer 只处理 RJ，不递归扫描，也不实现
特典检测、启发式关系推断或 VJ/BJ Organizer 支持。

## Requirements

- Python 3.12+
- PySide6 Essentials（Qt Core/Gui/Widgets；不安装当前未使用的 Qt Addons）
- Windows 是当前主要目标平台；架构预留 PyInstaller 打包位置，但 v0.3 尚未发布安装包

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

## v0.3 Organizer flow

```text
ScanCandidate → WorkLookup → Work → NamingService → RenamePlan → Preview
```

同一轮扫描中的相同 RJcode 只查询一次；单个查询失败不会中断其他作品。Planner 会检查
当前目标是否已存在、批次内目标碰撞、Windows 大小写不敏感路径策略、根目录 containment
和保守的路径长度阈值。没有执行按钮，流程在 Preview 结束。

## Provider status and limitations

v0.3 的 provider 有两个受限的数据源：

```text
product/info/ajax JSON → ProductInfoAjaxSource DTO → Work
HTML Schema.org Product JSON-LD → HtmlProductSource → Work
```

当 AJAX 响应成功且符合真实 regression fixture 校准过的 DTO 契约时，它是首选 metadata
source。作品身份来自请求所对应的顶层 RJ key；`product_id` 若存在必须与该 key 一致，不能用
图片路径或其他字段替代。DTO 保留 `work_name`、`maker_id`、`maker_name`、`work_image`、
`work_type`、`age_category` 和精确的 `regist_datetime`。当前 `Work.release_date` 仅使用
`regist_datetime` 的日期部分，并不宣称 `regist_date` 就是公开发布日期。若存在，
`translation_info` 被保存为 provider-local `TranslationInfoSource`（包括 original/parent/child
flags 与 workno 引用）。`TranslationRelationService` 再把这些明确字段转换为带
`Confirmed` confidence 和结构化 evidence 的 `WorkRelation`；GUI 只消费
application 层的 `TranslationRole + relations`，不会读取 provider raw fields。未知 JSON
字段可被忽略，但缺少或类型错误的核心 metadata 会令这个 source 无效。

当前关系解释仅支持：原作品、翻译 Parent、翻译 Child，以及 `translation_of`、
`has_translation_child`、`child_of_translation` 三种明确方向。缺少 `translation_info` 或
缺少明确 translation state 时显示“未发现 DLsite 明确的翻译关系信息”，不把它解释成
“已证明没有翻译作品”。矛盾或不完整 topology 会保留安全的已知事实并报告 contract issue，
不会生成自引用或伪造 target。

HTML JSON-LD 仍是 fallback/supplementary source：当 AJAX 不可用、返回非成功状态或不符合
DTO 时，provider 只再请求一次 HTML 页面。它可提供 title，以及页面确实声明时的 maker、
发售日期、系列、声优、tags 和封面；它不提供 translation contract。不会为同一个 RJcode
轮询多个 section。

当前默认 section 仍是 `maniax`，但 URL 构造集中在 provider 的 `DlsiteSite` 中；UI、domain
和 service 都不知道 `maniax/home/books/...`。AJAX endpoint 是否能跨 section 解析当前尚未
验证，因此本版本仍使用一个显式配置的 section，未来可在这一处替换为最小 section resolver。

本轮使用了三份经审查的用户保存 AJAX 响应，并将它们的最小相关子集放入
`tests/fixtures/dlsite/` 作为真实 regression fixture；没有执行实时请求。原有
`product_semantic.html` 仍是合成 HTML JSON-LD fallback fixture。详细观察、已知契约和不确定性
见 [docs/dlsite-data-contract.md](docs/dlsite-data-contract.md)。

## Roadmap

下一阶段应在可访问 DLsite 的环境中针对性验证更多已知 translation 引用和 `regist_date` 语义，
再考虑缓存。之后才会在单独版本中加入安全的
`scan → plan → preview → execute → transaction log → undo` 重命名流程及基于证据的
关系历史分析；当前 v0.3 明确终止于 Preview。
