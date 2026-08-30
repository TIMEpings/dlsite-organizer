# dlsite-organizer

`dlsite-organizer` 是一个处于早期开发阶段的 Python 桌面应用。v0.4 支持手动 RJcode
查询、已确认翻译关系、本地作品文件夹扫描，以及带确认、执行日志和撤销的安全重命名：扫描目录名中的
RJcode，读取 DLsite metadata，通过统一的 `NamingService` 生成目标目录名，并在 GUI 中
审查、选择并执行 `RenamePlan`。

> **v0.4 首次支持真实目录重命名。** 执行遵循 `Preview → Explicit confirmation →
> Preflight → Journal → Rename`。只重命名 Organizer root 下的直接子目录；不会移动、删除或覆盖目录。
> 每个事务写入 SQLite journal，并可按 journal 尝试撤销最近一次成功或部分成功的重命名。
> Undo 依赖重命名后的目录和原位置未被用户手工修改，不能证明目录身份，也不提供 ACID 保证。

扫描默认只读取用户选择根目录的直接子目录；无 RJcode 的目录会跳过并计数，包含多个不同
RJcode 的目录会作为 ambiguous 行显示。当前 Organizer 只处理 RJ，不递归扫描，也不实现
特典检测、启发式关系推断或 VJ/BJ Organizer 支持。

## Requirements

- Python 3.12+
- PySide6 Essentials（Qt Core/Gui/Widgets；不安装当前未使用的 Qt Addons）
- Windows 是当前主要目标平台；架构预留 PyInstaller 打包位置，但尚未发布安装包

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

## v0.9 manual review dataset

Manual review events can be analyzed with the derived
`EvaluationDatasetService` and exported as a deterministic, notes-free CSV.
The dataset uses the latest review per canonical pair and preserves review-time
evidence; it does not score candidates, learn from labels, or auto-confirm
relations. See [docs/evaluation-dataset.md](docs/evaluation-dataset.md).

默认测试全部离线运行。任何未来的真实 DLsite integration test 都必须标记为
`integration`，且不进入默认测试集。

## v0.4 Organizer flow

```text
ScanCandidate → WorkLookup → Work → NamingService → RenamePlan → Preview
                                                        ↓
                                      User confirmation → Preflight → Journal → Rename
```

同一轮扫描中的相同 RJcode 只查询一次；单个查询失败不会中断其他作品。Planner 会检查
当前目标是否已存在、批次内目标碰撞、Windows 大小写不敏感路径策略、根目录 containment
和保守的路径长度阈值。GUI 默认选中 READY 行，但只有用户明确确认后才会执行；执行器会再次进行整批
preflight。执行器只使用同一 root 下直接子目录的 `Path.rename`，拒绝非 READY、目标存在、
symlink/junction、重复路径、依赖链、cycle 和 case-only rename。执行中首次失败会停止后续项目，
不自动 rollback；每次 filesystem mutation 后立即持久化 journal。执行结束后旧 preview 失效，必须重新扫描。

最近一次可撤销事务显示在 Organizer 页面。Undo 完全读取 transaction journal，按成功操作的反向顺序
执行并再次 preflight；目标冲突、路径消失或目录被替换时会拒绝整批 Undo。事务状态和每个 operation
的执行/撤销结果都会保存在 SQLite 中。若 journal 不可用，执行按钮禁用且服务拒绝任何 filesystem mutation。

## Provider status and limitations

v0.4 的 provider 有两个受限的数据源：

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
再考虑关系历史分析。当前版本不实现关系历史分析、完整 transaction history browser、特典检测
或过期作品推断。

### v0.5 metadata cache

Lookup results use a persistent 24-hour metadata cache by default. The Lookup page offers a force-refresh action and indicates live, fresh-cache, or stale-cache fallback data. Successful live AJAX/HTML observations are appended to the SQLite historical observation store; cache hits and stale fallbacks do not create synthetic history. Historical relations are derived only from explicit translation references; no bonuses, expired-work inference, or heuristics are used.

### v0.4.1 safety

Real rename execution is Windows only. If an unfinished or recovery-required rename journal is detected, all new filesystem mutations are blocked.
# Historical confirmed translation relations

The application derives aggregated relations from persisted DLsite metadata observations. Historical evidence is shown separately from the current response and may reveal reverse links even when the queried work no longer exposes them.
### v0.7 explainable relation candidates

Lookup can show experimental **关联作品候选** derived from local metadata.
Candidates are non-confirmed suggestions for review, not DLsite-confirmed
relations, and the feature intentionally provides no score or probability.
See [`docs/candidate-evidence.md`](docs/candidate-evidence.md) for the evidence
contract and calibration limitations.

### 候选审阅队列

从本地已缓存或历史元数据中整理未审候选，便于人工标注。队列是派生的
本地视图，不执行批量网络访问、不评分、不计算概率，也不会自动确认关系。
候选审阅现在可在同一页面查看完整作品信息与本地封面；封面仅使用已有的
本地可用内容，不会因选中候选而自动查询网络。
