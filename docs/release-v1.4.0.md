# v1.4.0 发布准备记录

2026-10-29，Asia/Singapore。

**V1.4.0 SOURCE READY — FINAL BUILD APPROVAL PENDING**

建议：**CONDITIONAL GO，仅进入一次新的本地候选构建与审计**。公开发布仍为 HOLD，须单独完成剩余支持范围与风险决定。本记录不授权构建、提交、注册、安装或发布。

## 实际范围与版本来源

- 基线 tag `v1.3.1`：`ac3d7ac56114f8ae1f1e2151351598f4f57e6d85`。
- 当前分支 `main`，HEAD `9976f86338d30b89869f971ab8fd1cf9cc0b0d64`；基线后有 66 个提交。
- 已核查实际 `git log v1.3.1..HEAD`、`git diff --stat/--name-status v1.3.1`、生产代码差异及六项本地修改；未以历史 UAT 代替源码比较。仓库（含隐藏目录）与 D:/、D:/GitProject 未发现适用 AGENTS.md。
- 权威版本为 `src/dlsite_organizer/version.py`；Hatch 动态读取该文件，应用、HTTP User-Agent、关于页、主窗口底部及构建脚本复用该版本。

| 实际变化 | 对用户的影响与源码依据 |
| --- | --- |
| 主窗口、主题与 Windows 标题栏 | 新侧边导航、设置/关于底部入口、集成标题栏；`ui/main_window.py`、`ui/windows/native_chrome.py`、`ui/widgets/title_bar.py`、`ui/theme/` |
| Organizer 审阅与快捷操作 | 表格/详情、独立高亮与执行勾选、批量勾选菜单、Delete/右键移除；`ui/pages/organizer_page.py`、`ui/state/organizer_state.py`、`ui/models/organizer_preview_model.py` |
| Organizer 结果一致性 | 重命名/撤销后保留会话范围刷新，零修改撤销保留预览，追加/移除后重查冲突；上述页面及 `services/organizer.py`、`services/rename_planner.py` |
| 历史、查询与轻量界面 | 历史事务/操作主从详情、状态与观察汇总、路径复制；查询身份/名称/封面层次；轻量界面与反馈统一；对应 `ui/pages/` 和 `ui/lightweight_window.py` |
| 设置实际应用与精度 | 新操作使用已保存配置快照；缓存可即时开关；旧预览失效；数值不被控件或序列化截断；恢复默认保留自定义 DB 路径；`app/bootstrap.py`、`app/settings.py`、`services/lookup.py`、worker 与设置页 |
| Undo 安全与恢复 | 先持久化意图、逐项重查身份/占用、独立撤销状态与时间、未解决撤销阻止新修改；`persistence/database.py`、`persistence/rename_journal.py`、`domain/rename_execution.py`、`services/undo_service.py` |
| 错误与键盘交互 | Full 模式显示 Quick Rename 失败、轻量部分撤销反馈和忙碌资格修正；关于页回车/焦点恢复及布局；`__main__.py`、两个窗口及关于页 |
| Explorer/Helper 内部可靠性 | IPC 异常/超时/断连清理；Python/native 共用发布锁与序号，重试保留顺序，终态清理保护在途记录；`app/single_instance.py`、`app/durable_handoff.py`、`native/shell_helper/src/durable_handoff.cpp` |
| 性能与内部重构 | 批量规划共用 sibling 索引，关系查询约束读取范围；策略仍为 same-maker-same-date v1；`services/rename_planner.py`、关系服务、`persistence/metadata_store.py`；模型/委托拆分及测试/检查脚本不作为用户新功能 |
| 打包与许可 | 可选 greenlet 按安装元数据和实际 loose/archive payload 审计，存在时严格比对原始许可；`packaging/windows_packaging.py`、`THIRD_PARTY_NOTICES.md`。依赖声明、PyInstaller spec、构建驱动及注册服务与 tag 相比未改 |

以下均已存在于 v1.3.1：作品查询、命名模板、缓存/观察与候选关系、完整/轻量模式、拖放、预览追加与移除按钮、重命名历史、journal/Undo/恢复保护、手动更新检查、单实例、Explorer 多选/32 项上限/一批一事务一撤销、原生 x64 Helper。此次只能宣称其实际改进。

## 本次新增编辑与中文公告

- `src/dlsite_organizer/version.py`：`1.3.1` → `1.4.0`。
- `tests/test_version.py`：权威版本与 User-Agent 断言同步，测试名改为 v140。
- `README.md`：当前源码版本同步，并纠正实际导航/底部入口描述。
- `CHANGELOG.md`：新增完整简体中文 v1.4.0 待发布公告，作为未来 GitHub Release 正文；保留历史条目。
- 本记录：集中记录范围、兼容性、验证、风险及最终构建申请。

六个既有修改文件保持字节不变，并与 candidate-03 的 source-identity.json 比对一致：THIRD_PARTY_NOTICES.md、packaging/windows_packaging.py、tests/packaging/test_windows_packaging.py、src/dlsite_organizer/ui/pages/settings_page.py、src/dlsite_organizer/ui/widgets/page_header.py、tests/ui/test_settings_form_controls.py。

旧 changelog、历史文档、v1.3.1 migration fixture 和焦点测试中的固定 UpdateCheckResult 样例均保留原版本。未新增测试基础设施、功能或稳定组件重构。

## 兼容性结论与证据边界

未发现阻止作为向后兼容 minor 升级的破坏性变化：

- SQLite 在 rename_transactions 增加 `undo_state`、`undo_started_at`、`undo_completed_at` 三个可空字段，按缺列添加，旧 NULL 不伪造撤销意图；既有 metadata/review schema 增补并非本次新功能。
- 配置键、默认值与既有严格未知键策略未改变。保存后对新任务生效，已开始批次保留快照；DB 路径、provider section/base URL 仍在启动时绑定。重置不再替换自定义数据库路径。
- Rename 的 journal/preflight 与整批安全约定保留；Undo 新增意图、身份重查及恢复门禁，属于收紧不安全路径。路径检查与 rename 仍非原子操作，不宣称消除外部竞争。
- COM CLSID、HKCU 所有权、注册/移除范围、Player/DelegateExecute、sibling Helper 路径及上限未改变。Helper 与 Python 均有排序代码变化，因此仅复用原证据覆盖范围，不宣称实际全路径已验收。
- 没有新增或升级依赖声明；构建将使用原 canonical 环境，并须核查漂移。资源与许可修正已获 candidate-03 接受。
- 这是旧数据向新版的升级兼容性，不是降级保证。旧程序忽略新的 undo_state，存在未解决撤销时不能用于继续操作；升级前建议备份，保留新版进行检查。

复用的实际证据：

- [candidate-02 acceptance](../research/release-convergence/candidate-02/evidence/acceptance.json)、[migration results](../research/release-convergence/candidate-02/evidence/migration-results.json)：fresh 与三种合成旧 profile，原记录值、sentinel、SQLite 完整性、forward/Undo 恢复提示与门禁、重启保持；不是注入崩溃或自动恢复认证。
- [candidate-03 最终报告](../research/release-convergence/candidate-03/FINAL_REPORT.md)：204 文件 ZIP/onedir/extraction 一致，81 x64 PE、单个 GUI Helper、许可/branding/CA 与 verified TLS smoke、隔离离线启动退出、有限 UI 观察。ZIP 56,624,427 bytes，SHA-256 `e9cab5eadc1af3fb968a71f4a5e74b9ed97a80820a68cce49d7cfbfce5ebd82b`；本次只读重算一致。
- 用户本次明确报告的五项 Pilot：正常启动与主 UI；设置字形/悬停；真正 Explorer 菜单可见；一次合成目录菜单调用且结果可接受；正常移除注册与退出。作为 user-reported 接受证据保留，不改写 Codex 先前菜单不可见或自动化失败，也不扩展为全部边界/成功重命名证明。

上述生产组件相对 candidate-03 未再修改；版本变更会改变包身份与字节，旧 ZIP 不可重命名冒充 v1.4.0。

## 本次聚焦验证

日志：`research/release-convergence/v1.4.0-source-preparation/`。

| 检查 | 实际结果 |
| --- | --- |
| 版本、packaging、About、Settings 控件四组 | 125 PASS / 1 FAIL；版本 1、packaging 38、About 63 全通过；Settings 为 23 PASS / 1 FAIL |
| update checker（MockTransport，无网络）与主窗口版本/底部检查 | 46 PASS |
| Settings 失败诊断 | 独立模块仍为 23 PASS / 1 FAIL；仅在进程内恢复原 1.3.1 runtime identity 后相同结果。既有 height 断言得到 43−36=7，要求 ≤6；与本次版本/文档编辑无因果关系，保留失败，不改生产代码/阈值 |
| Ruff `python -m ruff check .` | PASS |
| Pyright `python -m pyright` | 0 errors / 0 warnings |
| `git diff --check` | PASS；Git 有既有 LF→CRLF 提示，不是 whitespace error |
| 版本来源/消费者 | canonical/runtime 1.4.0；User-Agent dlsite-organizer/1.4.0；Hatch 配置引用 version.py；About/footer 断言通过；构建驱动直接 import __version__ |
| 环境安装元数据 | 既有 editable dist-info 仍为 1.3.1；非第二权威版本源，应用/构建不读取它作为身份。本次未重装依赖。环境无 hatchling，未运行 wheel metadata 构建；本地 portable 使用已有 PyInstaller recipe，不依赖 hatchling |
| 保护 | 六项既有修改与 candidate-03 字节一致；candidate-03 ZIP hash 一致；未写 candidate-01/02/03 |

未重跑完整 native UAT、Explorer/COM、已接受文件系统事务套件或 35 行矩阵。Settings 的现存布局测试失败保留为 UI/显示限制；未据此建立新的产品阻断缺陷。

## 剩余发布决定

| 项目 | 当前状态与必要决定 |
| --- | --- |
| H1 间歇性 hang | UNKNOWN，根因未解决；有限正常退出/Pilot 不等于修复。公开发布前须明确接受残余风险及支持边界；后续出现持续无响应仍为 STOP |
| Explorer/Helper | 用户菜单/单次调用 Pilot 接受；I1–I4 的全部 warm/cold、worker/journal 关联、32/33、FIFO/dedupe 等组合仍 PARTIAL/未验证；I5 全部真实拖放路径未验证。发布前须取得所缺证据或明确覆盖范围/风险决定；本次不授权注册或再次研究 |
| DPI/小工作区/字体 | D2/D3 仍 PARTIAL，D4–D6 的原 BLOCKED/未完成状态不变；不能以模拟 DPI 或单个字形测试代替物理显示可用性。建议仅声明已观察配置，其余需明确支持范围决定 |
| 辅助功能 | A1 PARTIAL、A2 BLOCKED，完整屏幕阅读器体验与人类归因未验收；不能宣称无障碍合规或全面支持 |
| 其他原强制门禁 | 以 [原矩阵](../research/phase11c-r10-candidate-com-integration/UPDATED_ORIGINAL_GATE_MATRIX.md) 的实际记录及后续窄范围证据为准；其他 W/UI/S 未完成部分保留，不能默认为 PASS。此处未复制/重建矩阵或豁免要求 |
| 已确认产品阻断 | 当前未建立新的 release-blocking 产品/升级兼容缺陷；candidate-01 greenlet 阻断已有接受修复。现存 offscreen 高度失败需如实披露 |
| 正式身份 | 版本选择已定 1.4.0；新 artifact 尚不存在，须批准一次构建。HEAD+dirty source manifest 可供本地构建；正式发布需另行授权固定源提交、tag 与 artifact 关系，禁止仅标注旧 HEAD 代表全部源码 |

建议维持本地构建 CONDITIONAL GO、公开发布 HOLD。剩余强制要求在得到明确支持/范围/风险决定前继续保持未完成，不被本建议豁免。

## 一次最终本地构建授权范围（尚未执行）

请求批准：基于本报告对应的 `source-identity.json` 与 `source.patch` 所固定的完整工作树，仅构建一个 v1.4.0 本地候选，并自主完成下述限定审计；不授权公开发布或环境集成变更。

新根目录：`D:\GitProject\dlsite-organizer\research\release-convergence\v1.4.0-candidate-01`，准备时确认不存在。唯一完整构建命令：

```powershell
.\packaging\build-windows.ps1 -Python '.venv\Scripts\python.exe' -OutputRoot 'research/release-convergence/v1.4.0-candidate-01/build-01' -StagingOnly
```

Staging 分支不生成 ZIP；所有 gate 通过后使用驱动中相同的归档操作，输出新根目录下的唯一 v1.4.0 ZIP：

```powershell
Compress-Archive -Path 'research/release-convergence/v1.4.0-candidate-01/build-01/dist/dlsite-organizer/*' -DestinationPath 'research/release-convergence/v1.4.0-candidate-01/dlsite-organizer-1.4.0-windows-x64.zip'
```

授权后的必要工作：

1. 重核 HEAD、完整源码/dirty patch、六项修改、canonical 环境/toolchain 与历史候选身份；确认目录全新、版本 1.4.0。记录 Python/PyInstaller/Qt/依赖与工具链版本，调查相对 candidate-03 的实质漂移。无需提交；不得把未经提交的六项修改漏出构建来源。
2. 进程级 APPDATA/LOCALAPPDATA/TEMP/TMP/PyInstaller cache 指向新候选根下；canonical PATH isolation；使用既有离线/loopback discard 配置，不发起 provider/update 操作；不是全机断网保证。
3. 唯一完整 build 包含 recipe 固有 native CTest、PE/Helper/ICU/branding/许可/certifi gate 与必要 frozen TLS 辅助 smoke；后者是既有同 hook 验证步骤，不是第二个完整候选。记录 warnings，不弱化 gate。
4. ZIP CRC、完整 sorted path/size/SHA-256 inventory 与 staged/extracted 一致；x64/GUI/static Helper、污染/源码/数据泄露、原许可与 branding/CA 字节比对；记录 ZIP 路径、大小、SHA-256、mtime、源身份。实际文件数记录，不将旧 204/81 数量伪作新观察。
5. 使用新目录下 disposable profile 进行 exact extracted exe 离线启动/正常退出、About/footer 1.4.0、资源存在及有限 Full/Lightweight 导航检查；复用现有 smoke，不新增 Harness，不激活 Helper/注册。
6. 复用 candidate-02 已验证合成 fixture 的只读原件，复制到新目录并安全重定向 root/source/target/config DB 路径后，有限核对 fresh/legacy 启动、原行与 sentinel、SQLite integrity、恢复提示/门禁与正常退出。此为新 artifact 兼容性 smoke，不重跑已接受事务/崩溃/UAT 套件。路径 containment 或 fixture 校验失败即停止。
7. 结束重核源、旧候选/保护输入及新 ZIP；正常关闭本候选进程，记录退出与残留检查。全程不访问真实资料库，不安装/替换应用，不改 COM/Explorer 注册，不提交/tag/push/upload/发布。

若源/依赖/toolchain/保护输入出现未解释漂移、输出非全新、构建/资源/许可/TLS/identity 失败、兼容性异常、hang/crash、越界访问或意外残留：停止并保留证据；没有明确失败与修复理由不生成第二候选，不自动完整重试。构建许可不豁免本报告的公开发布门禁。

完整可审核 source patch、HEAD/status、每个 tracked/新增发布文档的 hash 与汇总 identity 记录于 `research/release-convergence/v1.4.0-source-preparation/source-identity.json`；其外部文件避免本报告 hash 自引用。准备证据不进入产品包。
