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

v0.1.1 的 provider 有两个受限的数据源，且它们都只在 provider 内转换为 `Work`：

```text
product/info/ajax JSON → ProductInfoAjaxSource DTO → Work
HTML Schema.org Product JSON-LD → HtmlProductSource → Work
```

当 AJAX 响应成功且符合显式 DTO 契约时，它是首选 metadata source，提供 `workno`、
`work_name`、`maker_id`、`maker_name`、`regist_date` 和 `work_image`。`work_type`、
`age_category` 也会保留在 DTO 中，但目前没有对应的 domain 字段。若存在，
`translation_info` 被保存为 provider-local `TranslationInfoSource`（
`original_workno`、`parent_workno`、`child_worknos`、`lang`）；本版本绝不会据此创建或猜测
`WorkRelation`。未知 JSON 字段可被忽略，但缺少或类型错误的核心 `workno` / `work_name`
会令这个 source 无效。

HTML JSON-LD 仍是 fallback/supplementary source：当 AJAX 不可用、返回非成功状态或不符合
DTO 时，provider 只再请求一次 HTML 页面。它可提供 title，以及页面确实声明时的 maker、
发售日期、系列、声优、tags 和封面；它不提供 translation contract。不会为同一个 RJcode
轮询多个 section。

当前默认 section 仍是 `maniax`，但 URL 构造集中在 provider 的 `DlsiteSite` 中；UI、domain
和 service 都不知道 `maniax/home/books/...`。AJAX endpoint 是否能跨 section 解析当前尚未
验证，因此本版本仍使用一个显式配置的 section，未来可在这一处替换为最小 section resolver。

本开发环境对候选 URL
`https://www.dlsite.com/maniax/product/info/ajax?product_id=RJ01609020` 的连接探测超时
（HTTP 000），浏览器连接器同样无法打开它。因此没有声称已验证线上 AJAX 结构，也没有
加入 live integration fixture。`tests/fixtures/product_info_ajax_contract.json` 是明确标记为
`product_info_ajax` 的**合成 provider contract fixture**，不是保存的真实 DLsite response；
它只验证本项目对成功响应的处理边界。原有 `product_semantic.html` 同样是合成 HTML
JSON-LD fixture。未来在可连接环境取得、并经审查的真实 response 后，应以最小语义子集
替换或补充这两个 fixture。

## Roadmap

下一阶段应先在可访问 DLsite 的环境中验证并固定真实 `product/info/ajax` 响应契约，再考虑
缓存。之后才会加入安全的
`scan → plan → preview → execute → transaction log → undo` 重命名流程及基于证据的
关系分析。
