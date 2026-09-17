# Career Nova22 — 数据库说明

> 本地单用户，**SQLite 单文件**为主存储；`sqlite-vec` 只做向量（留给 Chatter RAG）。
> 本文件是库结构的单一真相源，改了就回来更新。

## 连接与地基
- 连接串：`settings.DATABASE_URL`，默认 `sqlite+aiosqlite:///./data/career_nova.db`（测试用 `:memory:`）。
- 引擎：`app/repositories/base.py` —— SQLModel + aiosqlite **真异步** engine/session。
- 建表：`init_db()` 用 `SQLModel.metadata.create_all`（对空库建表；已有表不动）。**不引 Alembic**——本地单用户、暂无迁移需求，真需要时再加。
- Repository 是全应用**唯一碰 DB 的层**，不知道 LLM 存在（分层红线）。

## 表清单

| 表 | 用途 | 状态 |
|---|---|---|
| `settings` | 全局设置（单行 JSON blob） | 已实现 |
| `user_facts` | 用户信息事实库（简历模块地基，全局共享） | 已实现 |
| `resume_documents` | 版本化简历文档（成品，单文档流） | 已实现 |
| `resume_snapshots` | 事实库整表快照（与文档版本同代，回滚用） | 已实现 |
| `chat_sessions` / `chat_messages` | 会话存储（每版本一 session，1.2/1.3 共享底座） | 已实现 |
| `optimization_pending` | 1.2 优化建议落库（状态机 pending/confirmed/rejected/discussing + 终态 applied/archived 软标记全留存，三栏面板读它） | 已实现 |
| `preferences` | 用户判定偏好（拒绝项 + 自定义标准，含"拒掉这一类"） | 已实现 |
| `jobs` | 岗位聚合（阶段二 v1：源岗位，同源幂等 upsert，跨源不去重） | 已实现 |
| `job_followup_events` | 投递跟进状态时间线（单一状态线，时间线即真相；取代已作废的 `job_screening`） | 已实现（2026-08-30 定稿） |
| `interviews` | 面试场次（一个岗位 0..N 场；日历数据源，编年可追溯） | 已实现（2026-08-31 定稿） |
| `crawl_state` | 抓取进度（续页游标 + 单平台三态） | 已实现（2026-08-31 定稿） |
| `analysis_reports` | 投递页分析报告缓存（batch/daily/interview 三 scope，算一次存一次） | 已实现（2026-09-14 定稿） |

### `settings`
后端**用户偏好**单行存储（key 固定 `'app'`），整份 `AppSettings` 序列化为 JSON 放 `value`。
单行 blob 而非逐字段列：设置项随功能边做边加，不用改表结构。
> 边界：只放普通用户偏好（语言/模型/base_url）+ **API key**（本地单用户，存本机 SQLite）。
> API key 的敏感点在于**不读回**——只写不回读：GET/PATCH 响应一律掩码。
> 端口/关闭行为/数据目录在 Electron 本地 `settings.json`——都不在这张表。
> **data_dir 已废弃**（2026-08-08 挪到 Electron 线）：SQLite 表里历史残留字段不删，
> 但 `AppSettings`/`AppSettingsPatch` 已移除该字段，GET/PATCH 不再返回/接受它。

| 列 | 类型 | 说明 |
|---|---|---|
| `key` | TEXT PK | 固定 `'app'` |
| `value` | TEXT | 整份设置 JSON（language/llm_model/llm_base_url/**llm_api_key**/**apply_mode**/**crawl_quota**/**crawl_cap**/**crawl_threshold**） |
| `updated_at` | DATETIME | 最后更新时间 |

> `llm_api_key`：**只写不回读、唯一来源 = 此表**（掩码、空 = 未配置等完整规则见「配置三真相源」）。改模型名/base_url/key 后 LLM 注册表缓存重建（下次真调生效）。
> `apply_mode`（2026-08-19，apply.md §11.1）：投递总开关「开启求职之路」——`on` 允许后台限速爬 + 消费触发，`off`（默认）整个不爬。钥匙交给用户：做简历时还没想好方向，不该半推半就投。重启后读取，决定爬取进程是否运行。
> `crawl_quota`（2026-08-31，apply.md §8② /）：每平台一轮抓几个新岗（`{liepin, job51}`，默认 40/20 = 各平台一页条数）。取代旧 `crawl_batch_size`——两平台翻页机制各异、每页条数不同，硬套全局「分摊」会拖住主源深爬量。
> `crawl_cap`（2026-08-31，apply.md §8④）：每轮最多扫几页（全局一个数，默认 10）。深爬/稳态是同一循环跑出来的自然现象，cap 是用户唯一能调「扫多深」的杠杆。
> `crawl_threshold`（2026-08-31 修订，apply.md §8①）：触发值 = 保留的未处理底仓（`unprocessed ≤ 此值`就补下一批），默认 5。语义从旧「backlog < 此值」收敛为「≤ 此值补」，非「清零才补」（避免空仓 + 避免 exhausted 转天解禁时缺那次踢）。
> `crawl_batch_size`（旧，2026-08-19）：已删，由 `crawl_quota` 取代。

读写：`app/repositories/settings.py`（`get_settings_json`/`save_settings_json` upsert）。

> **`app_meta` 行（2026-08-21）**：settings 表还有第二行 key=`app_meta`，存 `{"data_epoch": N}`——**数据代次（核爆计数器）**。与 `app` 行物理隔离：app 是用户偏好（PATCH 合并可能整份覆盖），app_meta 是系统真相（只增不减）。`data_epoch` 只在核爆（`POST /reset/all`）时 +1；爬虫（Electron）开抓时记下、循环检查点比对，变了 = 开抓后数据被核爆 → 丢弃在途、不回写。平时开/关爬虫不动它。`get_data_epoch`/`bump_data_epoch` 在此 repository。

### `user_facts`
**用户信息事实库**——存"对用户的了解"，简历/优化/对话生成/市场调研共享的地基（见 `docs/design/resume.md` §4）。
**自然语言事实 + 基本分类**，不用向量；是**原料不是成品**（整理渲染成简历是下游 agent 的活）。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增 |
| `category` | TEXT（index） | 基本分类：`basic` 基本信息 / `education` 教育经历 / `work` 工作经历 / `projects` 项目经历 / `skill` 个人技能（含证书）/ `other` 其他补充 |
| `title` | TEXT | **总条目/单句**（如"2021–2024 南京大学软件工程本科"） |
| `points` | TEXT（JSON 数组） | **子要点**（该条目下的一条条具体描述，可空 `[]` = 单句事实） |
| `source` | TEXT | 来源：`resume_upload` 上传识别（后台抽取）/ `chat` 对话记录 / `manual` 信息库界面手填 |
| `status` | TEXT（index） | `active` 生效 / `superseded` 被新事实覆盖（留 history，不删） |
| `on_resume` | BOOLEAN（默认 1） | **上简历维度（2026-09-06）**：该 active 事实该不该渲染进简历成品。默认 True；方向槽位（目标岗位/城市）、敏感隐私、gap 弱点等置 False——`build_facts_text` 只取 `active 且 on_resume=True`，机器门/生成共享同一过滤口径 |
| `occurred_at` | TEXT | **时间线**：事实发生的时间（如 "2018-2021"），时间上的来源——配合 `source` 做完整溯源，回滚/对账顺藤摸瓜 |
| `created_at` / `updated_at` | DATETIME | 时间戳 |

设计要点：
- **冲突留 history**：真伪冲突用户裁决后，旧的标 `superseded` 不删，新的 `active`——可追溯。
- **统一冲突检测（入库前）**：确认/对话/手填三路径入库前统一检测——同分类 + 高相似判定同一实体：内容相同跳过、不同实体直接写、真伪冲突按路径分（见 resume.md §4）。**比较单元 = `title`**（points 随条继承 title 的判定）。
- **真伪冲突裁决分路径（甲方案 2026-08-13；2026-09-09 起改为对话内反问）**：fuzzy 带（相似度 0.8~0.95）真伪冲突**永不自动裁决**——上传确认路径检出的冲突**不静默 supersede**，冲突条**未写入**、`ExtractConfirmResponse.conflicts` 返回待裁决项，agent 在聊天里**反问**用户（如「雅思 7.5 vs 4.5，以新的为准吗？」），用户拍板后调 `supersede_fact` 工具落定（用新的→旧 superseded 留 history、新写入 active / 保留旧的→新丢弃）；对话挖掘路径冲突同样返回 agent 反问。**冲突卡与 `POST /facts/conflict/resolve` 端点已删（2026-09-09）**。仅**明确意图路径**（信息库页手填编辑、目标岗位单值槽位）传 `auto_adjudicate=True` 自动 supersede——用户在界面上亲手操作，不存在"无感知顶掉"。
- **抽取信息卡片确认才入库**：上传后后台任务多抽对比取最优，结果进**抽取状态**（候选，不入库）；前端卡片确认后才写 `active`（`source=resume_upload`）。拒绝则该批不入库（见 resume.md §6）。
- **嵌套模型（2026-08-07 重构）**：层级在**一条内**——`title`（总条目）+ `points`（子要点数组）一并存储，不再用 `group_id` 外键/负索引（负索引依赖 LLM 数数，一偏就丢子条目）。前端展平成 `[category] title` + `- point` 行注入 prompt。
- **方向 = 派生事实（2026-08-19 定稿，apply.md §11.2）**：求职方向**不建独立表**，存 `user_facts`——「求职目标」是一条 fact，「搜索词」从目标**派生**、挂在同一条 fact 的 `points`。改方向 = 聊出新目标 → 新 fact supersede 旧 fact，正是 `user_facts` 追加+溯源本职（`status` active/superseded）。**不复制到独立表**（**同一份数据只放一处**——关键词放第二处过一阵就没人记得同步）。
  - **方向 fact 约定（复用现有 `save_target_role`，`generation.py`）**：`category=basic`、`title="目标岗位：{role}"`（单值槽位，已有的 `save_target_role` 就是干这个的）。**role = `keywords[0]` 派生（2026-08-19 收尾）**：`title` 存 role（= 首条搜索词），**`points` 存 `keywords[1:]` 展开**（如 title=`目标岗位：前端工程师`、points=`["前端","web前端","vue"]`）。城市 = 独立一条同构 fact `title="目标城市：{city}"`（单值槽位，**city 空 = 不限城市**，不写城市 fact）。
  - **读取端（抓取器）**：Electron 经 `GET /api/v1/direction` 拿当前方向的 `{role, keywords, city}`（后端 service 聚合时重建 `keywords=[role]+points`），替换 `job-scraper.js` 硬编码 `"前端"`/上海。抓取器不直接理解 facts 的 JSON——由后端 service 层聚合这两条 fact 后吐干净对象。
- **迁移**：`init_db` 检测旧结构（有 `content`/`group_id` 列）→ 重建表为 `title`/`points`（幂等）。`on_resume` 列缺则补（默认 1），并回填存量方向槽位（`目标岗位：%`/`目标城市：%`）为 `on_resume=0`（方向槽位不上简历，升级前靠 `_DIRECTION_FACT_PREFIXES` 特例、升级后靠该字段，行为无缝）。
- **快照保留 on_resume**：`resume_snapshots.facts_json` 里的每条 `SnapshotFact` 带 `on_resume`，回滚连事实还原时一并恢复（不丢「不上简历」标记）。
- 全应用唯一，Repository 层读写；agent 只有"记事实"权限，增删走信息库管理界面（防注入，见 resume.md §1）。

### `resume_documents`
**版本化简历文档**——成品/范本（见 `docs/design/resume.md` §9）。**整张表 = 单用户的唯一简历文档流**（封闭通道，一次只有一份简历），每行一版。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增 |
| `version` | INTEGER（index） | 稿号，从 1 递增（第 1 稿=上传转出，第 N 稿=生成/修改；软作废后从目标+1 继续，**可复用序号**） |
| `markdown` | TEXT | **上传版内容**：v1 的 MarkItDown 提取物（抽取输入 + 元件兜底预览）。**生成版此列为空**（内容真身是 `resume_json`）。历史库迁移：`init_db` 检测缺列自动 ALTER 补上 |
| `resume_json` | TEXT | **生成版结构化真身**（2026-08-28）：JSON-Resume 词表 + `layout` 映射表的整体成品。上传 v1 为空。是「内容永不丢」机器断言的校验对象，渲染层的唯一输入 |
| `html` | TEXT | **渲染快照**（`HTML = render(resume_json, typography)` 纯函数产物）：固定模板（`app/templates/resume.html.j2`）从该版 `resume_json` 确定性渲染的完整 HTML 文档（内联样式）。上传 v1 为空。**不再是 LLM 产物、不再需要「排版崩了重渲染」的修复路径**。历史库迁移：`init_db` 检测缺列自动 ALTER 补上 |
| `summary` | TEXT | **一句话版本简述**（Git commit message 意味，2026-08-14 S8）：LLM 产出新版本时顺带吐（`<summary>` 标记剥出）；上传 v1 固定「最初版本」；无标记时回退规则名。前端版本行显示「第 N 稿 · summary · 日期」；历史库迁移：`init_db` 检测缺列自动 ALTER 补上（默认空 = 无简述，回退显示 source） |
| `scale` | REAL | **字号阶梯**（2026-09-02 落库挂版本）：排版松紧度，1.0 = 基准字号；上传 v1 无意义留 1.0。**迁移期遗留**——新值统一走 `typography`，读侧以 `typography` 为准 |
| `typography` | TEXT | **排版自由度配置**（2026-09-02）：JSON 字符串，承载 `Typography` 五参数（`scale` / `line_height` / `spacing` / `letter_spacing` / `gutter`）；空 = 默认 `Typography`。改排版经 `POST /documents/current/typography` 落库、重渲染 `html` |
| `source` | TEXT | `upload` 上传转出 / `generated` LLM 组合产出 / `rollback`（旧纯追加回滚的遗留值，软作废回滚不再产生新行） |
| `original_name` | TEXT | 上传原件文件名（仅上传版有；生成版/回滚到生成版为空） |
| `original_ext` | TEXT | 上传原件扩展名（小写无点，驱动前端预览分档） |
| `superseded` | BOOLEAN（index） | 软作废（2026-08-10：回滚跳过的稿标 True，数据保留、UI 不显示） |
| `created_at` | DATETIME | 时间戳 |

设计要点：
- **身份锚定 id（2026-08-12）**：`id` 是每条稿的**唯一身份**（永不重复）；`version` 只是显示标签（「第 N 稿」）——软作废后新生成**可复用**同号（回 v2 后新稿 = v3，若历史 v3 已作废则同号两条：一作废一当前）。**任何界面每个「第 N 稿」只出现一次**（取非 superseded 的当前行），作废的同号旧稿彻底隐藏；回看按 `document_id` 精确取原件/文档，不撞作废行。
- **软作废回滚（2026-08-10）**：`rollback` 把目标稿之后所有稿标 `superseded=True`（**数据全保留**——session/快照/原件/建议不删），目标稿回 `False` 变当前。**下一稿 = 目标+1**（稿号连续不跳，`create_document` 的 max version 只统计非 superseded 稿）。
- **"当前" = `version` 最大且 `superseded=False` 的行**；`GET /documents/versions` 默认只显非 superseded（版本面板），`?all=true` 含软作废稿（回看时间线聚合边界用）。
- **回滚 = 版本变更 = 开新 session**（resume.md §11.1 落实）：`documents.rollback` 回滚后开新会话，绑定目标稿号。
- **回滚目标 = 当前版本时拒绝**（避免无意义操作）。
- 文档是**事实的一次投影**（排版+措辞+组装后的事实），不是事实真相源——源数据在 `user_facts`。
- **生成产出先暂存预览态，确认才写库**（resume.md §13.5）：1.3 生成结果先落预览态（不占稿号），用户确认才写入本表（版本变更）；不满意重新生成覆盖暂存，不堆垃圾稿。
- **封闭通道**：文档流 = 单用户的唯一简历；`ready` 后上传入口全关（后端 409 双保险），换简历走 `POST /documents/reset`（清文档流 + 快照，可选清事实）。
- **原件持久化（2026-08-09）**：上传的原件文件（PDF/HTML 等）落盘到 SQLite 同级 `originals/`（文件名 = `v{version}_{消毒后文件名}`，防路径穿越），本表只记 `original_name/original_ext` 元数据。重启后左栏仍可原生预览原件（`GET /documents/original` 取文件 → 前端 blob 重建）。回滚到上传版时原件随稿切回（软作废不产生新稿号，目标稿原件路径不变）。`reset` 时清空 `originals/`。历史库迁移：`init_db` 检测缺列自动 ALTER 补上（默认空 = 无原件）。

### `resume_snapshots`
**事实库整表快照**——与文档同代，按 **document_id** 锚定（见 `docs/design/resume.md` §9）。**建新版本前**给「将被替换的当前文档」打一份 `user_facts` 的 active 行快照。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增 |
| `document_id` | INTEGER（index） | 对应文档 id（唯一身份，2026-08-12 A3 从 `resume_version` 迁来——version 可复用会让同号快照互覆失真，id 永不复用） |
| `facts_json` | TEXT | user_facts 整表 JSON（active 行；superseded 历史不进快照） |
| `created_at` | DATETIME | 时间戳 |

设计要点：
- **快照时机（2026-08-06 定稿）**：`save_upload` 不打（v1 无前任）；`save_document`/回滚在**建新版本前**给当前版本打。vN 快照 = "vN 还是当前文档时的事实库"（vN 任期末）——v1 在 v2 创建时才打，天然含上传后台抽取结果。
- **幂等**：同一版本可重复打，已存在则先删再插——"最新一次打的"为准，取快照无歧义。
- 回滚文档时若选 `include_facts=true`，用目标版本那代的快照覆盖当前 active 事实（当前全标 superseded、快照重建 active、**嵌套层级随 `title`+`points` 一并还原**——一条一行，无需 id 映射）。回滚自身也先给被替换版本打快照。**快照里每条事实的 `source` 原样还原**（chat/manual 不回退成 resume_upload，2026-08-07 修正）。
- **快照粒度**：按"每次生成/回滚打一个"；对话中途细粒度回滚已砍（2026-08-31）。

### `chat_sessions` / `chat_messages`
**会话存储**——每版本一 session（1.2/1.3 共享底座，见 `docs/design/resume.md` §11）。**纯 SQLite 表，不引 checkpoint**。

`chat_sessions`：
| 列 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增 |
| `document_id` | INTEGER（index，可 NULL） | 会话开始时的当前文档 id（唯一身份，2026-08-12 A3 从 `document_version` 迁来）；`NULL` = 种子会话（尚无简历） |
| `created_at` | DATETIME | 创建时间 |

> `chat_sessions` / `chat_messages` 是**前端 UI 真相源**（对话原文 + 系统气泡，Append-only）。对话 agent 的上下文压缩由 deep agent 的 `SummarizationMiddleware` 在图内做（不再落库摘要列，2026-09-09 删 `summary` 列）。

`chat_messages`：
| 列 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增 |
| `session_id` | INTEGER（index） | 所属会话 |
| `role` | TEXT | `user` / `assistant` / `system` / `event`（系统事件气泡） |
| `content` | TEXT | 消息内容（用户消息 / 助手回复 / 系统事件文案） |
| `kind` | TEXT | **结构化动作类型**（2026-08-20 §11.8，仅 `event` 行用）。分阶段开场引导判定读它，**不匹配中文文案**（apply.md §7.3 铁律）。非 event 行空串。**词表（2026-09-01 系统气泡）**：事件灰条 = `upload` / `generated` / `rolled_back` / `direction_changed` / `suggestion_hint`（建议提示）/ `stopped`（已停止）；错误红条 = `error_*`（`error_upload` / `error_extract` / `error_llm` / `error_conflict` / `error_not_found` / `error_generic`）。系统卡片档已无产品实例（「去生成」卡 2026-09-08 删、冲突卡 2026-09-09 改对话内反问。历史库迁移：`init_db` 检测缺列自动 ALTER 补上（默认空串） |
| `ref_document_id` | INTEGER（index，可空） | **事件引用的文档 id**（2026-08-21 §11.8，仅 `event` 行用）：`rolled_back` 记「被覆盖稿」id——回滚引导精确说「上一版改了什么」（连续回滚时「id 最大 superseded」会取错，故精确记录不反推）。非回滚 event / 非 event 行 NULL。回滚动作点把它作为 `covered_document_id` 直接传给 `persist_opening`（不再事后反推）。历史库迁移：`init_db` 检测缺列自动 ALTER 补上（默认 NULL） |
| `seq` | INTEGER | 会话内顺序（保证 UI 与 LLM 发送序列一致） |
| `created_at` | DATETIME | 时间戳 |

设计要点：
- **当前 session = id 最大那个**，无状态字段。版本变更（生成/回滚）各开新 session；旧 session 只读。
- **事件消息也进 chat_messages**：上传/确认/回滚等系统事件由服务端端点顺手 append（如"已上传简历：xxx.pdf"、"已确认，5 条信息收录进信息库"）。UI 历史（连续显示所有 session）与 agent 发送序列（当前 session）同源，事件消息让 UI 段落感完整。
- **错误事件也留痕（2026-09-01 系统气泡）**：`AppError` 全局 handler 收口失败时，顺手把「错误事件」（`kind=error_*`，红色系统气泡）写进当前 session——成功事件与失败事件同一真相源（不再是「成功后端记、失败前端拼」的双源）。瞬时护栏/协作式信号（`EpochChangedError`、并发互斥 409）置 `record_event=False` 不落库。后台抽取失败（不走 AppError handler）在 `extract_facts_async` 显式记 `error_extract`。前端按 `kind` 判定：`error_*`→错误红条 / 其它 event→事件灰条。
- **服务端持有唯一历史**：`POST /chat {session_id, message}` 增量式——客户端只发新消息，服务端读全量历史 → append → 跑 agent → append 助手回复 → 持久化 → 返回。重启后按 session 拉历史完整还原。

### `optimization_pending`
**优化建议落库**——建议组从「聊天暂存」升级为「落库对象」，完整状态机（2026-08-10，见 `docs/design/resume.md` §12.3）。`suggest_improvements` 产出即落库 pending；面板三栏（待定/已确认/正在聊）直接读它；聊一聊/裁决更新状态；重启不丢。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增 |
| `session_id` | INTEGER（index） | 提出建议的会话 |
| `document_id` | INTEGER（index） | 建议基于的文档 id（唯一身份，2026-08-12 A3 从 `document_version` 迁来） |
| `type` | TEXT | 建议类型（六类：quantify/word_choice/structure/fill_gap/job_relevance/highlight） |
| `target` | TEXT | Markdown 锚点定位（如 "工作经历>腾讯>第2条"） |
| `original` | TEXT | 原文 |
| `suggested` | TEXT | 建议改法 |
| `reason` | TEXT | 为什么（引用判定标准） |
| `severity` | TEXT | high/medium/low |
| `status` | TEXT（index） | 状态机：**`proposed` 拟议**（2026-09-14 加——投递页分析产的处方初始态，**不进面板四栏**，点「改进」转 pending）/ `pending` 待定 / `confirmed` 已确认 / `rejected` 已拒绝 / `discussing` 正在聊（活跃）；`applied` 已应用进新稿 / `archived` 版本变更被结清未应用（终态，2026-08-20 软标记） |
| `origin` | TEXT（index，默认 `agent`） | 来源（2026-09-14）：`agent`（聊天 agent 提的）/ `job_analysis`（投递页分析产的处方） |
| `resolved_in_document_id` | INTEGER（index，可空） | **在哪个版本被应用/结清**（2026-08-20 软标记溯源）；NULL = 仍活跃。历史库迁移：`init_db` 检测缺列自动 ALTER 补上（默认 NULL = 活跃） |
| `split_from` | INTEGER（index，可空） | 分裂来源建议 id（`split` 裁决分裂出的新条标记来源）；NULL = 非拆分产生 |
| `reject_reason` | TEXT（默认 `''`） | 拒绝理由（2026-09-14 补——此前 `/optimization/reject` 收了 reason 却只 `logger.info` 丢掉）。"能力不到"这类值是**方向降级的证据来源** |
| `created_at` | DATETIME | 创建时间（旧库列名 `accepted_at`，`init_db` 迁移改名） |
| `updated_at` | DATETIME（可空） | 状态最近变更时间（2026-09-14 加）——注入聊天 agent 时 `render_panel` 据它标「← 你刚改的」行 |

设计要点：
- **状态机**：`suggest_improvements` 产出 → `pending`（面板左栏）；接受 → `confirmed`（右栏）；拒绝 → `rejected`（灰栏，**版本内可撤回**）；聊一聊 → `discussing`（右栏「正在聊」）；`update_suggestion`（原 `decide_suggestion`，2026-08-25 改名 + 开放全状态操作）→ accept→confirmed / reject→rejected / discuss→discussing / retract→pending / refine→内容更新回 pending / split→原条回 pending + 新条 pending（`split_from` 关联）。**撤回**（2026-08-12）：confirmed/discussing/rejected → pending（右三栏每条「撤回」）。
- **优化点操作权（2026-08-25 §12.6）**：`propose_suggestion` 工具 + 提案单例 + 聊天内提议卡**废除**——建议操作统一走 `update_suggestion` 工具 + 面板；状态守卫放宽到「只要活跃（`resolved_in_document_id IS NULL`）即可操作」，agent 在聊天里听到用户口头表态（"第 2 条接受"）直接改状态，不再要求先点「聊一聊」。
- **偏好延迟记录（2026-08-12 策略反转）**：拒绝**不即时记**偏好——拒绝可撤回，撤回后不算"这一类"。到**版本变更统一结清**时，按仍留在 rejected 里的最终结果，按 type 聚合记 `preferences`（`kind=reject`，"拒掉这一类"）。
- **处方（`proposed`，2026-09-14 apply.md §11.7.3）**：投递页分析（`scope=batch`）产的可执行建议，落库即 `proposed` + `origin=job_analysis`——**不进面板四栏**（`list_suggestions` 默认过滤掉），点「改进」走 `POST /optimization/promote` 转 `pending` 才进面板、才被聊天 agent 看见。**只有未处理分析出处方**；已投递/面试分析产的结论是**嘱咐**（随报告，无状态、无处收录，见 `analysis_reports`）。
- **版本变更统一结清（2026-08-12）**：生成确认 / 应用建议（"开始改"）/ 回滚三种版本变更统一——**保留 discussing**（聊一聊的内容留到下一版本继续聊，是否适合新版由用户判断）；结清 pending/confirmed/rejected/**proposed**（定论项 + 处方都锚在旧稿上，脱锚）；清 rejected 时记最终偏好。`reset_resume` 仍**无条件清空全部**（含 discussing，回到空态）。
- **结清 = 软标记，不物理删（2026-08-20 大雷修复，推翻旧 `clear_by_status` 物理删行）**：旧实现版本变更时 `session.delete(row)` **物理删行**——丢「哪版产生/哪版被应用/清掉」的溯源，回滚回来查不到「上一版改了哪几点」。改为**软标记 + 全留存**：结清时 `confirmed→applied`、`pending/rejected→archived` + 记 `resolved_in_document_id`（目标版本 id），**行永不删**（`reset` 全清除外，仍物理删 `clear_pending`）。`list_suggestions` 默认只读**活跃**（`resolved_in_document_id IS NULL`），终态行不进面板/计数。**溯源查询** `list_applied_for_document(doc_id)`：某版「改了哪些点」= `status=applied AND resolved_in_document_id=该版` 的建议——§11.8「已生成/已回滚」引导的数据源（不给 resume_documents 加 changes 列，一份数据两处用）。
- **回滚结清是「丢弃」不是「应用」（2026-08-21 修正）**：回滚时结清记到**被覆盖稿**（回滚前的当前稿）的 id，且 `confirmed→archived`（不是 applied）——回滚丢弃这轮改动、没应用进目标稿（目标稿是旧上传稿），标 applied 会谎称「目标稿应用了它们」。`soft_settle_by_status(..., discard=True)` 控制此分支；生成/改写/应用建议仍是 `confirmed→applied`（真应用）。§11.8 已回滚引导查被覆盖稿（`covered_document_id`，回滚动作点按 `ref_document_id` 当场传入）的 applied 建议，说「上一版改过哪些、现在撤回了」。
- **去重**（2026-08-10 修 bug）：`accept` 校验状态，已 `confirmed`/`rejected` 的拒绝再接受——旧实现同一条可无限次收录。
- **执行（"开始改"）**：只应用 **`confirmed`** 建议 → 文档任务增量改写 → 新稿。**门控**（2026-08-12）：有待定（pending）时前端禁用；有 discussing 未结论时前端弹确认（保留到下一版本）。
- **`list_suggestions` 默认不返回 `rejected`**（历史行为）；四栏读取显式 `include_rejected=True`——rejected 是"版本内可撤回"，仍要展示在灰栏。
- **跟稿走**：版本变更（生成/回滚/应用）时未定论建议按上面统一结清处理。
- **历史库迁移（2026-08-10）**：`init_db` 检测旧结构（`accepted_at` 列）→ RENAME 为 `created_at`；缺 `status` 补（默认 `confirmed` = 历史已接受建议保持原语义）；缺 `split_from` 补。**此前漏了改名迁移导致查询 `no such column: created_at`（已修 + 回归测试）。**

### `preferences`
**用户判定偏好**——拒绝项 + 自定义标准（见 `docs/design/resume.md` §5、§12.5）。独立于事实库。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增 |
| `kind` | TEXT（index） | `reject` 拒绝项 / `custom` 自定义标准 |
| `scope` | TEXT（index） | 偏好作用域（如 `quantify` 量化类，记"拒掉这一类"） |
| `content` | TEXT | 偏好内容（如"后端成就不可量化，不提供量化"） |
| `created_at` / `updated_at` | DATETIME | 时间戳 |

设计要点：
- 优化建议被拒 → 记 `{kind:'reject', scope:'quantify'}`（"拒掉这一类"，不是具体某条），同类不再建议；`kind='custom'` 存用户自定义标准，生成时注入 prompt（§13）。
- 生命周期同事实：聊出来 → 确认 → 持久化 → 设置页可编辑。

### `jobs`
**岗位聚合（阶段二 v1）**——一条 = 一个平台的**源岗位**（见 `docs/design/apply.md` §4）。跨源不去重：同一岗位在多个平台都保留（用户想投哪个平台投哪个，多多益善）。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增 |
| `source_name` | TEXT（index） | 平台：`liepin`（主，2026-08-13）/ `job51`（2026-08-17 读 DOM 接入）；`boss` 已彻底砍（2026-08-31 连数据清）、`zhilian` 已放弃（详情页撞 Security Verification 墙，2026-08-17） |
| `external_id` | TEXT | 平台岗位 ID（猎聘的 jobId 等）；与 `source_name` 组合唯一 |
| `title` | TEXT | 职位名 |
| `company` | TEXT | 公司 |
| `city` | TEXT | 城市（原始字符串，不强归一） |
| `salary_text` | TEXT | 薪资（原始字符串，不强归一） |
| `experience` / `degree` | TEXT | 经验 / 学历 |
| `skills` / `job_labels` | TEXT | 技能 / 标签（JSON 数组文本列） |
| `company_scale` / `company_stage` / `company_industry` / `welfare` | TEXT | 公司规模 / 阶段 / 行业 / 福利 |
| `description` | TEXT | JD 正文（详情页读 DOM 抓取；前程无忧 `.bmsg.job_msg`，2026-08-18 已通；BOSS 已砍、智联已放弃则空） |
| `jd_status` | TEXT（index，默认 `none`） | **JD 抓取状态**（2026-09-14 加，apply.md §11.7.8）：`none` 平台无 JD（猎聘）/ `fetched` 抓到 / `failed` 抓失败（job51 读 DOM 超时）。——`description==""` 分不清"岗位真空"与"抓失败"，投递页分析靠它写"深度分析（有 JD）vs 概览（无 JD）" |
| `source_url` | TEXT | 岗位详情页 URL |
| `apply_url` | TEXT | 投递入口 URL（投递引擎/跳外部用） |
| `liveness` | TEXT（index） | `active` / `stale` / `closed` / `unknown`（程序爬的候选信号，可改） |
| `platform_updated_at` | TEXT（可空） | 平台发布/更新时间（有的平台不公开，可空） |
| `last_seen_at` | DATETIME | **我们自己的抓取时间，恒有**——新鲜度排序地基 |
| `is_new` | BOOLEAN（默认 true） | 本轮刷新**新增**标记（前端"新"角标）；upsert 命中已有岗位时置 false，新插入为 true |
| `crawl_round_id` | INTEGER（index，可空） | **首触的抓取轮次 id**（2026-09-14 加，apply.md §11.7.8）——一次 kick 一个、两平台共用。**首触即定**：upsert 更新分支跳过（`_IMMUTABLE_ON_UPDATE`） |
| `found_by_query` | TEXT（默认 `''`） | **首触召回它的查询组词**（如 `Vue 前端 \| 杭州`）。**首触即定**（同上）——§3.1「哪组词好」的反馈侧前提 |
| `created_at` / `updated_at` | DATETIME | 时间戳 |

唯一性：同源幂等 upsert，**由应用层兜底，非数据库约束**——模型未声明 `UNIQUE(source_name, external_id)`，`create_all` 不建该约束；唯一性靠 `app/repositories/jobs.py` 的 `upsert_job` select-then-insert 保证（本地单用户单写者够用）。`external_id` 与 `source_name` 组合在语义上唯一（`apply.md` §5）。

设计要点：
- **同源去重（唯一约束）**：同平台同岗位不新增行，upsert 更新字段 + liveness + last_seen_at。落地 URL 是平台岗位 ID 的封装（`external_id` 取 ID、`source_url` 是派生）。
- **跨源不去重**：猎聘和前程无忧的同一岗位是两条，各自投递。
- **归属字段首触即定（2026-09-14）**：`crawl_round_id` / `found_by_query` 是"这岗第一次是谁抓的/哪个词召回的"，属历史事实。猎聘**每轮都重新抓到旧岗**送 upsert，全字段 setattr 会把归属漂移到最新一轮 → 分析取「本轮那批」时旧岗被错认成新岗。故 `upsert_job` 把这两列（连同唯一键）放进 `_IMMUTABLE_ON_UPDATE`，更新分支跳过——与"不动 `created_at` / 用户反馈"同一条规矩。
- **liveness 是候选信号**（程序爬的，可能错）——closed 的岗位灰显不藏；用户权威确认关闭走 `job_followup_events(status=not_pursuing, reason=job_closed)`（`apply.md` §6bis / §12.4）。
- **`platform_updated_at` 可空 + `last_seen_at` 恒有**——新鲜度排序靠 `last_seen_at` 兜底。

### `job_followup_events`
**投递跟进状态时间线**——一个岗位投出去之后的进展（见 `docs/design/apply.md` §12）。**时间线即真相**：追加式事件表，当前态 = 该岗位最新一条事件（派生，不建快照列）；未处理 = 无事件。取代已作废的 `job_screening`（「投了/没投布尔 + 6 值原因」——2026-08-30 作废清空重来，不迁移）。一条状态线：`applied` → `interviewing` → `offered`，负向终态 `not_pursuing`；任意跳、可回退。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增 |
| `job_id` | INTEGER（index，外键 → `jobs.id`） | 被记录状态的岗位；一个岗位可有多条事件（时间线） |
| `status` | TEXT | `applied` 已投递 / `interviewing` 待面试 / `offered` 已录用 / `not_pursuing` 不再追踪 |
| `reason` | TEXT（可空） | 仅 `not_pursuing` 有值：`withdrawn` 主动放弃 / `failed` 未通过 / `job_closed` 岗位关闭 / `duplicate` 已投过 |
| `stage` | TEXT（非空，默认 `""`） | 仅 `interviewing` 有值：面试轮次自由文本（如「技术二面」） |
| `note` | TEXT（非空，默认 `""`） | 这次变化的「为什么」（自由文本） |
| `source` | TEXT | `user` 手动标（v1 恒 user）；L2 观察 / L3 自动投递来了记 `observer` 兜底 |
| `at` | DATETIME | 变成这个状态的时间（跟进 tab 排序键 = 处理日期） |

设计要点：
- **当前态 = 派生**：按 `job_id` 取最新一条事件的 `status`；无事件 = 未处理（空着）。不建「当前态快照列」——避免「已投」两处真相。
- **`interviewed` 是派生态（2026-08-31）**：不写事件。读时若最新事件 = `interviewing` 且该岗位最新一场面试（`interviews`）约定时间已过、或 outcome 已非 `scheduled` → 派生为 `interviewed` 已面试。事件表本身只存 4 个状态值。
- **无状态机**：自由标签，任意跳、可回退；`applied → interviewing → offered` 只是推荐路径，不校验前序。事件日志忠实记「从哪跳到哪」。
- **`rejected` 已改名 `failed`（2026-08-31）**：中文「被拒」→「未通过」，值、文案一起改（更委婉，避免对求职者心态的挫败）。「默拒」不是状态——投了没回音停「已投递」，面了等消息停「等结果」。
- **「有回音」无此态**：「回音」没信息量，进到下一步（邀约/面试）才算推进。
- **取代 `job_screening`**：旧表的 `applied=true` → `status=applied`；`reason=closed` → `not_pursuing/job_closed`；`mismatch/not_suitable/underqualified` → `not_pursuing/withdrawn`；`duplicate_push` → `not_pursuing/duplicate`；`wrong_push` 的反哺回路没建、先丢。**v1 清空重来不迁移**（本地单用户历史不值钱，对比 user_facts 那轮「检测旧结构重建表」——那边历史值钱）。

### `interviews`
**面试场次**——一次具体约面（见 `docs/design/apply.md` §12.10 /）。与「面试阶段」（状态线管）分两层：阶段是岗位在流水线的位置，场次是那一段里的每次约面。**日历数据源 = 全表**（不过滤状态，编年可追溯）。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增 |
| `job_id` | INTEGER（index，外键 → `jobs.id`） | 被安排面试的岗位；一个岗位 0..N 场（1:N） |
| `scheduled_at` | DATETIME | 面试开始时间（必填；上午/下午由它派生） |
| `location` | TEXT（非空，默认 `""`） | 自由文本可空（线上填链接、线下填地址，不区分 mode） |
| `round` | TEXT（非空，默认 `""`） | 轮次自由文本（如「技术一面」「HR面」） |
| `outcome` | TEXT | `scheduled` 待面 / `awaiting` 等结果 / `next_round` 下一场 / `offered` 录用 / `failed` 未通过 |
| `next_round_id` | INTEGER（可空，自引用外键 → `interviews.id`） | 显式链指向下一场；只写一次（删除场景不存在） |
| `created_at` / `updated_at` | DATETIME | 时间戳 |

设计要点：
- **终局联动状态线（条件式）**：`outcome=offered` → 追加 `job_followup_events(offered)`；`outcome=failed` → 追加 `not_pursuing`（reason 当场选 `failed`/`job_closed`/`withdrawn`）。其余（scheduled/awaiting/next_round）不联动。
- **「安排下一场」= `outcome=next_round` + `next_round_id` 指向新行**（新行 scheduled）；状态线不新增（仍在面试阶段）。链只写一次。
- **结果可回退（任意态→任意态），只做正向联动**——改 outcome 只改这一行；回退时状态线手动退，不自动同步。
- **排面试 = 唯一进入「待面试」**：建场次（scheduled）+ 联动追加 `interviewing` 事件。
- **无「取消」档**：没面成要么改期（改 `scheduled_at`），要么自然死 = 未通过。

### `crawl_state`
**抓取进度（续页游标 + 单平台三态）**——见 `docs/design/apply.md` §8 / 。唯一真相源，统一存后端 SQLite：猎聘（后端 HTTP）与前程无忧（Electron 读 DOM）两边只报结果，后端记账推进游标 / 标三态。每平台一行。

| 列 | 类型 | 说明 |
|---|---|---|
| `source_name` | TEXT PK | 平台：`liepin` / `job51` |
| `group_idx` | INTEGER | 当前查询组下标（0 起，组间顺序消费） |
| `page` | INTEGER | 平台**原生页号**（猎聘 currentPage 0 起 / 前程无忧 pageNum 1 起） |
| `groups` | TEXT | JSON：`list[{exhausted_date, throttled_at}]`，长度 = 方向查询组数——组级三态 |
| `updated_at` | DATETIME | 最后更新时间 |

设计要点：
- **游标二维 `(组下标, 组内页号)`**：方向是 `keywords: list[list[str]]` 二维查询组，每组各自有第 1..K 页、组间顺序消费（组 1 走完再轮组 2），无扁平页号。`page` 存平台原生页号（后端不碰平台翻页语义）。
- **三态是组级**（exhausted/throttled 记到组粒度）：组 1 空页 = 组 1 exhausted、游标移到组 2；**所有组都到底才整家 exhausted**（派生，非存储）。throttled 记失败时间戳 + 惰性冷却比对（默认 15 分钟），exhausted 记日期、转天重查。
- **无 phase 字段**：深爬/稳态是同一循环的自然现象，无阶段存储。
- **两重置**：改方向（`commit_direction`）显式清两家游标 + 组内三态；核爆（`reset_all`）把 `crawl_state` 当求职数据随 reset 清空（不归「配置保留」）。

### `analysis_reports`
**投递页分析报告缓存**（apply.md §11.7，2026-09-14 定稿）——分析的产物，**算一次存一次**。三种分析共用一张表，靠 `scope` 分。

| 列 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增 |
| `scope` | TEXT（index） | `batch` 未处理批次 / `daily` 已投递日报 / `interview` 单场面试准备 |
| `scope_key` | TEXT（index） | `batch` = 抓取轮次 id / `daily` = **本地日**（`YYYY-MM-DD`，UTC+8）/ `interview` = 场次 id |
| `content` | TEXT | 报告结构化内容（JSON 文本列）：**batch / daily 都是** `{headline, blocks, meta?}`（块流见下）；interview 仍 `{headline, body}`（纯叙事，读模型层把 `body` 折成单个 text 块） |
| `created_at` | DATETIME | 生成时间 |

设计要点：
- **`content` 只装嘱咐，不装处方**：报告叙事（总览/日报/面试 tips）在这里；**处方**是 `optimization_pending` 的行（`status=proposed`、`origin=job_analysis`），随优化点状态机走。一份报告 + 一列处方 = "一屏两物"，不是"一物两存"。
- **`blocks`（2026-09-15 文/图穿插）**：`batch`/`daily` 报告的 content 带 `blocks: list[ReportBlock]`——analyze Agent 按叙述顺序产出的 `text`/`chart` 块（**图穿插在段落之间**，0~4 张、宁缺勿滥）。`ReportBlock` 契约见 `app/schemas/report_block.py`（text=Markdown 段 / chart 内嵌 `ChartSpec`，后者见 `app/schemas/chart.py`：6 种图型、series ≤ 2、字段须在 data 内）。读缓存时逐块校验（`_parse_blocks`），坏块（kind 不认得 / chart spec 配错）丢弃降级、不拖垮整份；**兼容旧缓存**——无 `blocks` 只有 `body` 时折成单个 text 块。前端另有 `toReportBlocks` + `isChartSpec` 兜底。
- **不存"未就绪"行**：算完才写；读不到 = 该 scope 还没算过，由 `GET /analysis/*` 补算（起后台任务返回 `computing`，前端轮询）。
- **`batch` 的 scope_key = 轮次 id**——分析输入 = **本轮抓到的那批**（检索条件会改、轮次间不可比，§11.7 grill 定）。无岗位的轮次 → 路由返回 `missing`，不起任务、不写报告。
- **`daily` 的 scope_key = 本地日的昨天**——报告是**日期的纯函数**（"截至昨天"，§11.7.5），同一日期永只算一份、永不需复算。**坑**：代码全 UTC，而"今天"是本地日（UTC+8）——按 UTC 算会把今天投的岗算进昨天。
- **`interview` 的 scope_key = 场次 id**——展开该场次时算。
- **两种数据源两种分析法**（`batch`）：猎聘（无 JD、结构化标签）代码聚合标签；前程无忧（有 JD）逐岗读 JD 提炼。`meta.with_jd` 供前端标注"深度/概览"。**2026-09-15 graph 化**：batch 改走 `graphs/analysis.py`（load → prepare → compute_stats → analyze → persist），analyze 用带工具位的 Agent，详见 apply.md §11.7.6。
- **核爆随 reset 清空**（派生缓存，不继承）。

## 配置三真相源（别搞混）
> 三条真相源为（`.env` / Electron `settings.json` / 后端 `settings` 表）。本文件只记对本表的影响，不复述整套规则：
- 端口 / 关闭行为 / 数据目录在 Electron 本地 `settings.json`，**都不在 `settings` 表**（data_dir 2026-08-08 从本表挪出）。
- **本表 `settings`** = 后端用户偏好 + API key（经 `/api/v1/settings` 读写）；API key 唯一来源 = 此表、只写不回读（掩码响应），见上方 `settings` 表段。
- 另见 `app_meta` 行（`data_epoch` 核爆计数器），上方 `settings` 表段。

## data_dir 迁移语义（Electron 线，2026-09-13）
> `data_dir`（Electron `settings.json`）的语义 = **应用数据目录**，不是"业务库所在目录"。一个目录 = 一份完整数据快照（拷一个目录即整份搬迁/备份）。

改 `data_dir` 后、**启动后端前**由 `electron/services/db-migrate.js` 整份复制（纯复制、不删旧文件、绝不覆盖目标已存在项）：

| 项 | 是什么 | 备注 |
|---|---|---|
| `career_nova.db` | 业务库 | 简历/版本/资料集/`settings` 表/`chat_messages` 原文 |
| `checkpoints.db` + `-wal` / `-shm` | LangGraph checkpointer | agent 工作记忆 + 编排挂起态。**WAL 由 `AsyncSqliteSaver.setup()` 主动打开**，必须连伴生文件一起搬，只搬主库会丢 WAL 里未 checkpoint 的对话 |
| `originals/` | 上传原件（PDF/HTML） | 文件名按版本号隔离 `v{n}_{name}`，跨库版本号仍唯一，整目录搬安全 |

- 三项**分项判定**：业务库已在目标位置时，checkpoint / originals 仍会被补齐（未必同步搬过）。
- **迁移源 = `settings.json` 的 `current_data_dir`**（上次实际用的目录），不是写死的默认 `data` 目录——否则第二次改目录会从过期的源搬、丢掉中间那次之后的新数据（2026-09-13 修）。
- 迁移幂等规则与 `log-migrate.js` 同构（同路径跳过 / 源不存在跳过 / 目标已存在不覆盖）。
- 迁移失败不阻断启动（新位置建空库，数据仍在旧位置）。

### WAL 折叠（2026-09-13）
> WAL 模式下主库文件**不随提交实时增长**——新页先追加进 `-wal`，只在「最后一个连接正常关闭 / 超 1000 页阈值 / 显式 checkpoint」时才折回主库。本地桌面应用常态是**被强杀**（Electron `taskkill /F /T`），走不到优雅关闭，于是 WAL 不折叠、主库停在空壳快照。

- **checkpoint 库**：`AsyncSqliteSaver.setup()` 主动开 WAL，且 saver **从不主动折叠**。实测主库 4KB、`-wal` 3.7MB（101 个 checkpoint 全悬在 WAL 里）。
- **关闭时折叠**：`close_checkpoint_store()`（checkpoint 连接）与 `dispose_engine()`（业务库，经 `checkpoint_and_dispose`）在关闭前各做一次 `PRAGMA wal_checkpoint(TRUNCATE)`，把 WAL 折回主库并截断。折叠失败不阻断关闭（优化，非正确性）。
- **但折叠只在「正常运行到关闭段」时跑**——`taskkill /F` 直接砍进程，那段永远不跑。所以 Electron 关后端前先请它优雅退，见下节。
- 效果：关闭后主库自包含（实测 4KB→692KB，WAL→0），拷贝数据库不再需要连 `-wal` 一起拿。

### 后端优雅关闭（2026-09-13）
> 光有折叠逻辑不够——进程被硬杀时它跑不到。Electron 关闭流程因此改了两步。

- **`POST /api/v1/system/shutdown`**（`app/api/v1/system.py`）：延迟 `signal.raise_signal(SIGINT)`，交给 uvicorn 的 handler 触发完整 graceful shutdown（lifespan 关闭段 = 折叠 WAL + 释放连接）。用**本进程内 raise** 而非跨进程发信号——后者在 Windows 上 notoriously 不可靠，且 uv 还是中间进程要穿透。
- **Electron 侧**（`process-manager.js` 的 `gracefulShutdownBackend`）：关后端前先 POST 该端点，等它自己退（超时 10s）；没退成则落回 `taskkill /F /T` 硬杀。**「关干净」永远优先于「关得优雅」**。
- 前端（vite）不走优雅路径，直接杀。
- 实测：优雅关闭在 ~1.5s 内完成，退出后 `data/` 无残留 `-wal`、端口释放、且**不留脱链的 uv 孤儿进程**（硬杀路径实测会留）。

## 预留线头
- **编排 checkpoint（`checkpoints.db`，独立文件）**：~~会话 checkpoint 用 `langgraph-checkpoint-sqlite`~~ 已放弃用 checkpoint 存**聊天会话**（那是 `chat_sessions`/`chat_messages` 的活，resume.md §11.2）。但 `langgraph-checkpoint-sqlite` 现用于**另一个用途**——**图编排挂起状态**（改写人门 / 抽取挂起的「挂起→人确认→重启恢复」），由 `app/graphs/checkpoint.py` 装配 `AsyncSqliteSaver`，存**业务库同目录的 `checkpoints.db`**（从 `DATABASE_URL` 派生、`with_name("checkpoints.db")`），**与业务库分离**——编排数据（checkpoint/writes 表）与业务数据生命周期不同，混一个库会把「回滚文档」和「回滚编排状态」绑在一起。`thread_id` 派生式 `{graph}:{scope}`（单用户单值，不落库）；业务库 `:memory:`（测试）时无文件可派生 → 不装配、图退回内存跑。**这是编排基础设施，不是业务表**（不经 SQLModel/Repository，`checkpoint.py` 自管连接生命周期）。
- **sqlite-vec**：Chatter 行业文档 RAG 的向量表，独立于普通业务表。
