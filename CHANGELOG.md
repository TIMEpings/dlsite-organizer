# Changelog

## v1.4.0 — 待发布

### 新增功能

- 完整模式采用新的侧边导航和 Windows 集成标题栏，常用页面与底部的设置、关于入口更清晰。
- 整理预览新增批量设置执行勾选，以及 Delete 键和右键菜单移除预览项的快捷操作；移除仅影响预览，不删除磁盘目录。

### 功能改进

- 整理页改为表格与项目详情结合的审阅工作区，分别呈现高亮项目、执行勾选、状态摘要和操作进度。
- 重命名及撤销后按当前预览范围刷新结果；追加或移除项目后重新检查相关冲突，避免遗漏仍被占用的目标或保留过期冲突。
- 重命名历史改进事务列表、操作详情、状态图标和路径复制，并将当前文件系统观察与历史日志事实分开展示。
- 查询页突出作品编号、标题和格式化名称，改进封面、复制入口与窄窗口布局；轻量模式、设置和关于页统一视觉与焦点反馈。
- 优化批量重命名规划和本地关系查询，减少重复目录遍历与无关历史数据读取；候选关系仍为非确认信息，判断策略不变。

### 问题修复

- 修复运行中保存命名、语言、超时和缓存设置后的应用不一致；新操作使用新配置，已开始的批次保持同一份配置，过期整理预览必须重新生成。
- 修复数值设置在界面和保存过程中被截断或丢失精度，以及恢复默认设置时意外替换自定义数据库路径的问题。
- 撤销前先持久化撤销意图，并在每次反向改名前再次检查目录身份与目标占用；中断或日志失败时保留恢复状态并阻止后续不安全操作。
- 改进本地请求异常与连接清理，以及 Python 与原生 Helper 的持久化请求排序、重试和去重记录处理；不承诺所有 Explorer 场景均已验证。
- 修复完整模式未显示 Quick Rename 失败、轻量模式部分撤销结果提示不准确，以及控制器忙碌期间撤销资格显示不一致的问题。
- 修复关于页检查更新时的键盘焦点保持、回车激活和响应式布局；改进设置页中文标题字体及 Explorer 操作按钮的悬停、焦点反馈。
- 修复可选 greenlet 未安装时打包许可证审计错误拒绝构建的问题；实际包含该依赖时仍要求匹配的原始许可证。

### 兼容性与升级说明

- 支持沿用现有配置和 SQLite 数据；数据库初始化会补充撤销状态及起止时间三个可空字段，不要求重建资料库。升级前建议备份配置与数据库。
- 新版恢复保护依赖新增的撤销状态；存在未解决事务时，请保留新版与原数据进行检查，不要用旧版继续重命名或撤销。升级兼容性不代表支持降级。
- Explorer 多选、每批最多 32 项、整批拒绝超限、一次批处理对应一次撤销，以及当前用户注册约定均沿用旧版。便携目录移动后仍需在设置中显式更新注册。
- 更新检查仍由用户手动触发，不自动安装；本次没有更改项目依赖声明或引入新的运行时依赖。

### 已知限制

- 历史间歇性无响应风险尚未确认根因或彻底消除；已通过的启动与退出观察不代表所有场景均无此风险。
- Explorer/Helper 的全部冷启动、已有实例、多选边界与排队组合，以及所有拖放路径尚未完整验证。
- 不同显示缩放、多显示器、放大字体和屏幕阅读器尚未完成全面验证；小工作区的完整可用性仍有验证限制。
- 过期限定特典关系的自动恢复或识别仍未实现。

## v1.3.1 — 2026-09-26

### 改进

- 重命名历史改为固定行高的操作概览和完整选中项详情，长路径与错误不再扩展表格行。
- 事务列表显示状态图标、文字和本地秒级时间；详情保留数字 UTC 偏移，事务 ID 移至技术信息。
- 调整历史页分栏与窄宽度下的路径显示，保留复制完整路径的入口。

## v1.3.0 — 2026-09-26

### 新增

- 新增“重命名历史”页，可分页查看已保存的重命名事务、操作结果、撤销结果和恢复信息。
- Organizer 可从未解决事务提示直接打开对应的事务详情。

### 安全与可靠性

- 历史页只读；当前文件系统观察与持久化 journal 事实分开展示，不将路径存在解释为原目录身份，也不自动恢复或修改事务。
- 保持未解决事务对新的重命名和撤销操作的阻止。

## v1.2.1 — 2026-09-25

### 修复

- 修复应用启动期间通过资源管理器右键执行 Quick Rename 时，所选文件夹偶发丢失或操作失败的问题。

## v1.2.0 — 2026-09-03

### 新增

- Explorer 右键快捷重命名支持多文件夹选择。

### 改进

- 一次 Explorer 多选作为一个批处理事务处理，并沿用同一套 Quick Rename、journal 和 Undo 流程。
- portable package 包含独立原生 x64 Shell helper；helper 不在 `explorer.exe` 内加载 Python/PySide，且没有可见控制台窗口。

### 安全与可靠性

- 一次撤销可以恢复整个多文件夹批次；批次最多处理 32 个文件夹，超过上限时整批拒绝。
- Explorer 独立请求不会被时间窗口合并；helper 仅负责选择传递和本地 IPC，不是 daemon 或后台服务。

## v1.1.0 — 2026-09-01

- Added: manual update check from the About page using the public GitHub Releases API.
- Changed: Lightweight mode now uses a more compact unified drop/recent-operation surface.
- Changed: one active application instance per user/profile now owns normal activation and Explorer
  Quick Rename forwarding; rapid Quick Rename actions are serialized in the primary application.
- Fixed: the recent-operation presentation stays compact after Undo.

## v1.0.0 — 2026-08-31

首个 Windows standalone release。该版本冻结 feature behavior，并聚焦 packaging、
upgrade safety、documentation 和 launch validation。

- RJ lookup with normalized DLsite metadata and cover retrieval.
- Metadata cache and append-only observation/history storage.
- Current and historical confirmed translation relations with separate provenance.
- Folder scan and safe formatting of proposed names.
- Safe rename workflow with preview, explicit confirmation, preflight, durable
  journal, crash/recovery states, and Undo.
- Candidate relations using `same-maker-same-date v1`, kept non-confirmed and
  without score or probability.
- Local Manual Review and Review Queue history, including review-time evidence.
- Evaluation support for review history and derived datasets. The 24-pair Pilot
  documents workflow and taxonomy use; it is not an accuracy or precision evaluation.
- Bonus observation preservation from v0.10.3 onward, including the distinction
  between `NULL` unknown/legacy evidence and an explicitly empty bonus list.
- Transient bonus evidence preservation; no automatic recovery or detection of
  expired limited-bonus relations.

Candidate is derived/non-confirmed. The candidate policy remains
`same-maker-same-date v1`; it does not auto-confirm or learn from labels.
Automatic recovery/detection of expired limited-bonus relations is not implemented.

## 0.10.3 — 2026-08-30

- Preserve normalized bonus metadata observations in historical SQLite rows.
- Keep legacy `bonus_evidence_json = NULL` distinct from explicit `bonuses: []`.
- Do not infer or create bonus relations from transient observations.

## 0.10.2

- Add the local candidate Review Queue and expanded Manual Review taxonomy.
- Preserve candidate policy provenance and review-time evidence.

## 0.10.1

- Add the derived evaluation dataset workflow for manual review history.

## 0.10.0

- Add historical confirmed translation relation queries over metadata observations.

## 0.9.0

- Add explainable, non-scored relation candidates from local metadata.

## 0.5–0.8

- Add persistent metadata cache, append-only observations, and stale-cache
  fallback behavior.
- Harden provider parsing and explicit translation topology handling.
- Add safe Organizer preview, durable rename execution, crash recovery, and Undo.

## 0.1–0.4

- Bootstrap the desktop application, manual RJ lookup, DLsite metadata parsing,
  local folder scanning, naming, and the first safe rename preview flow.
