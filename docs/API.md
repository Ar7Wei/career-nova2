# Career Nova22 — 接口说明

> 本地 FastAPI 服务，前缀 `/api/v1`（见 `app/main.py` / `app/api/v1`）。
> 本文件是接口的单一真相源，改了就回来更新。
> 交互式文档：起服务后访问 `/docs`（Swagger）。

## 约定
- 无认证（本地单用户）。请求/响应均为 JSON。
- 校验失败返回 `422`，body 为 `{detail, errors:[{field, message}]}`（已格式化成易读形式）。
- **业务异常 = 统一错误契约（2026-08-08）**：service 层抛 `AppError` 子类，全局 handler 收口（`app/core/errors.py`），body 固定为：
  ```json
  { "code": "llm_unavailable", "message": "面向用户的文案（可含出路）", "detail": "真因", "retryable": true, "action": "open_settings" }
  ```
  - `code`：机器可读（`not_found` / `conflict` / `empty_output` / `llm_unavailable` …），前端分支用。
  - `retryable`：驱动前端「重试」按钮；`action`：恢复动作（`open_settings` / `retry` / `reupload` …）。
  - 前端拦截器（`frontend/src/lib/api.ts`）读一次转 `AppApiError`，全站生效；网络错误归一为 `code=network_error`。
  - **坏答案识别**：有确定产物的任务（抽取/生成/改写）在 service 层校验空输出 → 抛 `empty_output`（HTTP 200 不是坏答案的护身符）；聊天开放对话不判"坏"。
- 状态码：`404 not_found` / `409 conflict` / `503 llm_unavailable` / `422 empty_output`。

## 端点清单

### 根与健康（无 `/api/v1` 前缀）

#### `GET /`
返回应用基本信息。
- 响应 `200`：`{name, version, status, swagger_url}`

#### `GET /health`
健康检查；数据库不可达时降级。
- 响应 `200`：`{status:"healthy", version, components:{api, database}, timestamp}`
- 响应 `503`：同上但 `status:"degraded"`、`components.database:"unhealthy"`

### 聊天（前缀 `/api/v1`）

#### `POST /api/v1/chat`
处理一条对话消息（增量式）：服务端是历史的唯一持有者，客户端只发新消息。

`POST /chat {session_id, message}`——服务端读该 session 全量历史 → append 用户消息 → 持久化 → 跑对话 agent（采集/优化姿态，deep agent，**工具循环住图**）→ append 助手回复 → 持久化 → 返回。重启后按 session 拉历史完整还原。
- 请求体 `ChatRequestIn`：`{ "session_id": 3, "message": { "role": "user", "content": "我在腾讯做了三年后端" } }`（`session_id` 可省略=新建种子会话）。
- 响应 `200` `ChatSessionResponse`：`{ "session_id": 3, "messages": [ ...完整历史含新增回复... ], "suggestion_count": 0, "preview_pending": false, "version_changed": false, "direction_changed": false }`。
  - `suggestion_count`（2026-08-10）：建议面板未定论建议数（待定 + 正在聊）——建议组已落库，不再经 /chat 透传，前端据此刷新面板角标。**建议提示（2026-09-01）**：`suggestion_count>0` 时服务端顺手落库一条 `suggestion_hint` 事件（在 `messages` 里，kind=suggestion_hint），前端渲染成「事件灰条」——不再前端硬编码 assistant 文本气泡。
  - `preview_pending`（2026-08-12）：是否存在待确认的生成预览（agent 生成工具产出后）——前端据此拉预览显示「确认保存 / 重新生成」入口。
  - `version_changed`（2026-08-13）：本轮 agent 是否**写了新版本**——前端据此刷新左栏预览（`loadCurrent` + `loadVersions` + `loadChatHistory`）。**2026-09-09起恒为 false**：出简历草稿（`generate_resume`）与「开始改」（`apply_suggestions`）都是人门挂起态不写库、不置真——确认落库走独立端点 `/resume/generate/confirm`，前端在 confirmGeneration 里刷新。字段保留仅为契约兼容。
  - `pending_disposal_count`（2026-08-25 方向口头化）：agent 落定方向后**待处置的未处理岗位数**——>0 前端就地弹「留/删」确认（清走 `POST /direction/dispose-unprocessed`）；0 = 无待处置。取代旧 `direction_proposal` 方向卡。
  - `direction_changed`（2026-08-30 方向跨页同步）：本轮 agent 是否**落定了方向**（`commit_direction` 工具写回 facts 后）——true 时前端刷新 `directionStore`（`useDirectionStore.load()`），让投递页检索控制台标签（关键词组数 · 城市）跨页/最小化也能同步到最新方向。
  - **2026-08-25（§12.6 D8）**：`proposal` 字段删除——提议卡 + `propose_suggestion` 工具废除，优化点操作统一走 agent 工具 + 面板（2026-09-23 起为 `set_change_status`）。
  - **2026-09-09**：`conflict` 字段删除——冲突卡废除，事实真伪冲突改为**对话内反问**：`record_facts` 返回 `conflicts`，agent 在聊天里问用户「你之前说过 X，以新的为准吗？」，用户拍板后调 `supersede_fact` 工具落定（旧 superseded 留 history、新写入 active）。`POST /facts/conflict/resolve` 端点一并删除。
- 响应 `404`：session_id 不是当前会话（历史只读会话不可续聊）。
- 响应 `503`：LLM 调用失败（模型连不上/超时/无 key）——契约 body `code=llm_unavailable`，前端显示真因 + 给「去设置」出路。
- 旧版 `POST /chatbot/chat`（无状态）**已移除**——被本接口取代。

#### `POST /api/v1/chat/opening`（已移除，2026-08-23）
原「分阶段开场引导」端点已**移除**——引导词改为**动作时刻生成 + 落库**为当前 session 的一条 `assistant` 消息（§11.8，2026-08-23），不再有独立端点。前端 `loadChatHistory` 只「读」历史（引导已在里面）；冷启动（无 session）由前端 i18n 静态欢迎语兜底。

#### `POST /api/v1/chat/stop`
真停止（2026-08-14）：取消当前在途的聊天 generation（停止按钮触发）。

`POST /chat/stop`——set 当前在途对话的 cancel_event → `run_with_cancel` 里 `task.cancel()` 整段工具循环，烧 token 源头被掐断（不只是前端"不收回复"）。`handle_message` 捕获取消、补「已停止」事件气泡、干净返回。
- 请求体（可选）`ChatStopRequest`：`{ "retract": true }`（2026-09-09 撤回）。`retract=true` = 停止后顺带撤回最后一条 user 消息、原文回填输入框（取消重新输入）；缺省 `false` = 纯停止（兜底调用 `cancelUpload`/`removeResume`/`loadChatHistory` 复用）。
- 响应 `200` `ChatStopResponse`：`{ "stopped": true|false, "retracted": true|false, "retracted_text": "..." }`。
  - `stopped`：是否确有在途被取消（`false` = 无在途，幂等）。
  - `retracted`：是否成功撤回（仅 `retract=true` 且「发送时的 session 仍是当前 session」时为 `true`；`apply_suggestions` 中途切 session → 静默降级为纯停止，`false`）。
  - `retracted_text`：被撤回的原文（前端回填输入框用；未撤回为空）。
- 单用户 + `generating` 已挡并发发送 → 后端一个模块级 cancel_event 引用即可；每轮生成新建一把，stop 只点聊天这把，**不误伤抽取等其他任务**。

#### `POST /api/v1/chat/opening/stop`
真暂停（2026-08-30）：取消当前在途的后台开场引导（`schedule_opening` 的 fire-and-forget 任务）。

`POST /chat/opening/stop`——set `opening._opening_cancel_event` → 后台 `generate_opening_content` 里的 `llm_service.call` 被中途取消 → `persist_opening` 捕获取消、**不落任何消息**（用户主动停了，不留半截/说明）。响应复用 `ChatStopResponse`：`{ "stopped": true/false }`（无在途 = false，幂等）。仅作用于后台 fire-and-forget 那条；同步 `persist_opening`（生成/回滚/应用建议内联）不在本端点范围。

### 会话（前缀 `/api/v1`）

#### `GET /api/v1/sessions`
列会话（版本面板历史 session 只读查看用）。
- query 参数（可选）：`document_id` 过滤某文档的会话。
- 响应 `200` `SessionsResponse`：`{ "sessions": [ { "id", "document_id", "created_at", "message_count" } ] }`。

#### `GET /api/v1/sessions/{id}`
读单个会话（含消息数）。
- 响应 `200` `Session`：`{ "id", "document_id", "created_at", "message_count" }`。
- 响应 `404`：会话不存在。

#### `GET /api/v1/sessions/{id}/messages`
读某 session 的完整消息（回看模式只读查看 + 挂载恢复当前会话用）。
- 响应 `200` `MessagesResponse`：`{ "messages": [ { "role", "content", "kind", "ref_document_id" } ] }`。
- `role` 可为 `event`（系统气泡——上传/确认/回滚/错误等，落库）：前端渲染**系统气泡**（区别于聊天气泡）；`chat` 服务注入 agent 前**过滤 event**（§11.3，系统动作无需 agent 处理）。
- `kind`/`ref_document_id`（2026-09-01 仅 `event` 行有值）：**结构化动作类型**，前端按 `kind` 判定系统气泡呈现（不靠文案匹配）——`error_*`→**错误红条**（失败/约束报错）；其它 event（含 `upload`/`generated`/`rolled_back`/`suggestion_hint`）→**事件灰条**。2026-09-08 删「系统卡片·去生成」档——`upload` 统一落事件灰条（后零文档也能生成，那堵「没有结构化简历」的死路已消解）。`ref_document_id` 仅 `rolled_back` 用（被覆盖稿 id，回滚溯源）。

#### `POST /api/v1/sessions`
新建会话（版本变更——生成/回滚后前端开新 session）。
- 请求体 `SessionCreate`：`{ "document_id": 4 }`（可省略 = 种子会话）。
- 响应 `201`：`{ "id": 5, "document_id": 4, "created_at": "..." }`。

### 优化点（1.2，前缀 `/api/v1`）
> **2026-09-23 一张表**：优化点 = **改动记录**（`change_records`）——`reason`（为什么，主体）+ `changes`（改什么，复合子项，各自带 status）。取代旧的扁平建议流（`optimization_pending`，已停用）。
> 面板读 `/optimization/records`，**逐条子项**改状态走 `/optimization/record/status`。旧的 `/pending`、`/accept`、`/reject`、`/discuss`、`/retract`、`/update` 六个端点随旧表停用一并删除。

#### `GET /api/v1/optimization/records`
读活跃改动记录（优化点面板展开时拉取；`kind` 可选过滤 `change`/`decision`）。
- 响应 `200` `ChangeRecordListResponse`：
  ```json
  {
    "records": [
      {
        "id": 7,
        "reason": "成果要量化",
        "changes": [
          { "id": 1, "target": "工作经历>腾讯>第2条", "original": "负责维护系统",
            "suggested": "优化性能40%", "status": "pending", "type": "quantify", "severity": "high" }
        ],
        "status": "pending", "kind": "change", "origin": "agent",
        "document_id": 3, "resolved_in_document_id": null, "updated_at": "2026-09-23T..."
      }
    ]
  }
  ```
  - `reason`：为什么改（记录主体，一个原因一行）。
  - `changes[]`：改什么（子项，**可空**——原因先行、子项后补）；子项自带 `status`（**操作粒度 = 单条子项**）与 `type`/`severity`（面板卡片标签）。
  - `kind`：`change` 本轮整改 / `decision` 跨版本持续决策。
  - `status`（记录级）仅兜底；面板分栏按**子项** `status` 判：`pending` 待定 / `confirmed` 已确认 / `discussing` 正在聊 / `rejected` 已拒绝（版本内可撤回）。终态 `applied`/`archived` 不返回（已结清）。
  - `origin`：`agent`（聊天 agent 提的）/ `job_analysis`（投递页分析产的处方）。
  - **面板只展示带子项的记录**：`kind=decision` 且 `changes` 为空的是纯跨版本约束，不进面板。

#### `POST /api/v1/optimization/record/status`
改一条改动记录/子项的状态（面板四栏互转的唯一写口）。
- 请求体 `ChangeStatusRequest`：`{ "record_id": 7, "change_id": 1, "status": "confirmed" }`。
  - `change_id` 给了就改**那一条子项**；省略则改整条记录（子项还没列出时的兜底）。
  - `status`：`confirmed` 接受 / `rejected` 拒绝 / `discussing` 聊一聊 / `pending` 撤回（回到待定）。
- 响应 `200` `OptimizationDecideResponse`：`{ "result": "改动点 #7.1 已标为 confirmed。" }`。
- `409`：文档任务（生成/改写/回滚/重置）执行中——撞锁，稍后再操作。

#### `POST /api/v1/optimization/promote`
收录一条投递页处方进优化点——投递页分析面板的「改进」按钮（apply.md §11.7.7）。
- 请求体 `PromotionRequest`：`{ "suggestion_id": 31 }`（旧处方行 id，来自分析报告的 `suggestions`）。
- 行为：**落一条改动记录**（`origin=job_analysis`，子项直接 `confirmed`——点「改进」= 用户已认可），
  并把旧处方行软结清（`archived`，留溯源）。不再改旧行的状态。
- 响应 `200` `OptimizationDecideResponse`：`{ "result": "处方已收录为改动记录 #12。" }`；
  `404`：处方不存在或不是待收录状态（已收录的再点无效）。
- 到这一步面板就多了一行，而面板**本就每轮注入聊天 agent**——这既是"收录"也是"交给 agent"。

> **2026-08-25（§12.6 D8）**：`POST /optimization/decide` 改名 `POST /optimization/update`；`POST /optimization/proposal/confirm`、`POST /optimization/proposal/dismiss` 删除（提议卡 + `propose_suggestion` 工具废除）。`/optimization/update` 已在 2026-09-23 随旧表停用删除。
> **2026-09-09**：`POST /optimization/apply` 删除——「开始改」折进统一简历图（见下「简历生成」），走 `POST /resume/generate` 的 `apply_confirmed=true`。

### 简历生成（1.3，前缀 `/api/v1`）
> 统一「出简历」流水线（2026-09-07 合并）：**生成、改写、开始改同一入口**——都是「出草稿 → 人看 → 确认落库」。跑统一图到 `human_gate` 挂起（产出经 checkpointer 持久化，**重启不丢**）→ 人拍板才写库（版本变更 = 新 session）。冷/暖由图入口判（无当前 JSON → facts 生成；有 → 暖态改写）。确认支持「重新生成」（revise 回图重改、不落库；**意见可选可空**，2026-09-08 从「带意见重改」改名并放开空意见）。
> **「开始改」折进本入口（2026-09-09）**：`apply_confirmed=true` → 把已确认建议渲染成文本并进图（机器门 + 人门同款覆盖），不再走 `POST /optimization/apply` 直接落库。
> **产物链（2026-08-28 转向）**：出稿即由固定模板（Jinja2）从 resume JSON 确定性渲染 HTML，预览直接看 HTML。

#### `POST /api/v1/resume/generate`
出简历草稿（统一入口，生成 + 改写 + 开始改）。跑统一流水线到人门挂起 → 产出**挂起草稿（不写库）** → 人确认才写库（vN）+ 版本变更 + 开新 session。**零文档可生成**——无上传也能从 facts 冷启动出第一版。
- 请求体 `GenerateRequest`：
  ```json
  { "target_role": "后端工程师", "apply_confirmed": true }
  ```
  - `target_role`：目标岗位（可空 = 通用版）；作为事实（basic 类、**on_resume=False 方向槽位**）存 user_facts——单值槽位，旧岗标 superseded。
  - `apply_confirmed`：真 = 「开始改」——把已确认建议渲染成文本并进图（`user_request` 槽，冷启动自然进 `instruction`、暖态进 `content` 请求），产出一版应用了建议的预览。无已确认建议 → `409`。
  - （`pages` 已废弃：页数由 scale 阶梯 + A4 预览人工调。）
- 响应 `200` `GeneratePreviewResponse`：`{ "markdown": "<结构化 resume JSON>", "html": "<!doctype html>…" }`（挂起预览态，未写库；`markdown` 字段名沿用旧契约，实为 JSON 真身）。
- 响应 `409`：还没有任何用户事实，无法生成；或 `apply_confirmed=true` 却没有已确认建议。
- 响应 `422` `code=empty_output`：模型没生成出内容（坏答案识别）。
- 备注：PDF 导出（左栏「下载」菜单，Electron `resume:export` IPC，printToPDF 矢量直转）已实现，不在此 HTTP 端点。

#### `GET /api/v1/resume/generate/preview`
读当前挂起的草稿（agent 出稿后 / 重启恢复，前端拉取显示「确认保存 / 重新生成」入口）。空字符串 = 无挂起。挂起态存 checkpointer（独立 checkpoints.db），重启后按派生 thread_id 查回。
- 响应 `200` `GeneratePreviewResponse`：`{ "markdown": "...", "html": "..." }`（JSON 真身 + 渲染快照）。

#### `POST /api/v1/resume/generate/confirm`
人门拍板：
- `decision=confirm` → 恢复图拿最终产物 → 写库 vN（`resume_json` + `html` 双写）→ 开新 session → **版本变更统一结清**（§12.3）→ 清草稿。
- `decision=revise` + `feedback`（**意见可选、可空**，2026-09-08）→ 回图重改、再挂人门，**不写库**（`version` 返回 null；前端再拉 preview 看新草稿）。空意见 = 整份重来，填了 = 定向重改。
- 请求体 `GenerateConfirmRequest`：`{ "decision": "confirm", "feedback": "" }`。
- 响应 `200` `GenerateConfirmResponse`：`{ "version": 5 }`（confirm）；revise 时 `{ "version": null }`。
- 响应 `409`：没有待确认的草稿（先出稿再拍板）。

#### `POST /api/v1/resume/generate/stop`
**真停止（2026-09-08）**：取消当前在途的「重新生成」（revise 回边重改，重新生成按钮触发）。revise 跑图（content 节点 + LLM）在 chat 循环之外、不经 `/chat/stop`，故需独立端点单独点它——与 `POST /chat/stop` 同型。
- 响应 `200` `ChatStopResponse`：`{ "stopped": true }`（确有在途 revise 被取消）/ `{ "stopped": false }`（无在途，幂等）。
- 取消后清挂起草稿（resume 中途打断、挂起态已坏），前端 `refreshPreview` 读到空 → 预览条收起、回落到上一版已保存文档。

> **`POST /resume/fix-html` 已删除（2026-08-28）**：排版转向后 HTML 由固定模板确定性渲染（`HTML = render(resume_json, typography)` 是纯函数），不再有「排版崩了让 LLM 重渲染」这条路径，该端点与入口一并移除。

### 简历与文档（前缀 `/api/v1`）
> 重构后（文档为成品）：上传 → MarkItDown 转文档 v1（快，无 LLM）→ 后台异步抽事实进抽取状态 → 前端卡片确认才入库。
> 文档版本化 + 快照回滚；抽取状态机驱动蒙版/卡片/通道。设计与理由见 [`docs/design/resume.md`](design/resume.md)。

#### `POST /api/v1/resume/parse`
上传简历文件：转文档 v1 立即返回（**不阻塞等抽取**），后台异步抽事实进抽取状态。
- 请求：`multipart/form-data`，字段 `file`。
- 支持格式（白名单）：`txt / md / pdf / docx / html / csv / pptx / xlsx`。其余扩展名直接拒收。
- 响应 `200` `ResumeUploadResponse`：
  ```json
  {
    "document_id": 1,
    "version": 1,
    "markdown": "张伟，28 岁……",
    "resume_name": "张伟-简历.pdf",
    "original_name": "张伟-简历.pdf",
    "original_ext": "pdf"
  }
  ```
  - `version`：文档版本号（首次上传 = v1）。
  - `markdown`：MarkItDown 解析后的 Markdown（既是文档 v1 内容，也是 DOCX/PPTX 等左栏预览兜底）。
  - `original_name`/`original_ext`：上传原件文件名/扩展名（后端已消毒防路径穿越）；原件文件落盘 `originals/`，重启后经 `GET /documents/original` 取回原生预览。
  - **不返回 facts/coverage**——抽取在后台异步进行，结果进抽取状态（前端卡片确认才入库）。
- 响应 `415`：不支持的文件格式（扩展名不在白名单）。
- 响应 `422`：文件可识别但提取不出文本（如扫描件 PDF）——`detail` 提示"疑似图片型/扫描件，请换文本型或手打"。
- 响应 `413`：文件过大（上限 20MB）。
- 响应 `409`：已确认（ready）后再上传——封闭通道，换简历走 `POST /documents/reset`。

#### `GET /api/v1/resume/extract-status`
读后台抽取状态（前端轮询：蒙版/进度条/卡片/通道开关）。
- 响应 `200` `ExtractState`：
  ```json
  {
    "state": "extracting",
    "document_id": 1,
    "generation": 3,
    "facts": [],
    "error": null
  }
  ```
  - `state`：`idle | extracting | extracted | failed | confirmed`。`extracted` 弹卡片、`failed` 错误提示 + 通道重开、`confirmed` 蒙版消失 + 通道关闭。
  - `generation`：当前代次（confirm/reject 防陈旧）。
  - `facts`：抽取候选（确认才入库；`extracted` 时有值）。

#### `POST /api/v1/resume/extract/confirm`
确认抽取卡片：统一冲突检测后入库（`source=resume_upload`），状态 → `confirmed`（蒙版消失、通道关闭）。
- 请求体 `ExtractConfirmRequest`：`{ "generation": 3, "facts": [ { "category": "work", "title": "某公司 后端工程师", "points": ["主导订单系统", "性能优化"] } ] }`（`facts` 为卡片编辑后的清单；`points` 可空 = 单句事实）。
- 响应 `200` `ExtractConfirmResponse`：`{ "saved": 12, "state": "confirmed", "conflicts": [] }`。
  - `conflicts`（2026-08-13 甲方案；**2026-09-09 起改对话内反问**）：**fuzzy 带（相似度 0.8~0.95）真伪冲突不自动裁决**——不静默 supersede 旧事实，冲突条**未写入**，返回给前端提示「本次有 N 条与已存内容冲突，去聊天里确认」。**不再有冲突卡片 + `/facts/conflict/resolve` 端点**——用户在聊天里答复后，agent 调 `supersede_fact` 工具落定（旧 superseded 留 history、新写入 active）。
- 响应 `409`：代次过期（旧卡片）或状态非 `extracted`。

#### `POST /api/v1/resume/extract/reject`
拒绝抽取卡片：该批不入库，清空文档流 + 快照、状态回 `idle`、通道重开（可重传）。
- 请求体 `ExtractRejectRequest`：`{ "generation": 3 }`。
- 响应 `204`。
- 响应 `409`：代次过期。

#### `POST /api/v1/resume/extract/retry`
**重新抽取（2026-08-08 新增）**：对**当前文档**重新抽一遍（**不重传、不清文档流**）。用户对当前抽取结果不满意（空卡片/想再试一次）时点「重新抽取」。
- 请求体：无。
- 响应 `200` `ExtractRetryResponse`：`{ "generation": 4, "document_id": 1 }`。
  - `generation`：新代次（作废旧抽取——旧后台任务结果丢弃）；前端用它更新 `extractGeneration` 并重新轮询。
- 响应 `404`：还没有简历文档（无法重抽）。

#### `POST /api/v1/documents/save`
存一版生成/修改的简历文档（LLM 组合产出），顺带给新稿打一份**工作台快照**（该版开始时的工作台：资料集 + 改动记录，与版本同代）。
- 请求体 `ResumeDocumentCreate`：`{ "markdown": "...", "html": "", "source": "generated" }`（`source` ∈ `upload|generated|rollback`；`html` = 排版层产物，可空）。
- 响应 `200` `ResumeDocument`：`{ "id", "version", "markdown", "html", "summary", "source", "created_at" }`。

#### `GET /api/v1/documents/versions`
列当前简历的稿（v1→vN 升序）。
- query 参数（可选）：`all`——`true` = 含软作废稿（回看时间线聚合边界用）；默认只显非 superseded（版本面板）。
- 响应 `200` `ResumeVersionsResponse`：`{ "versions": [ { "id", "version", "markdown", "html", "summary", "source", "original_name", "original_ext", "superseded", "created_at" } ] }`。
  - `summary`（2026-08-14）：一句话版本简述（Git 意味）——LLM 产出新版本时顺带吐，上传 v1 =「最初版本」；前端版本面板显示「第 N 稿 · summary · 日期」。

#### `GET /api/v1/documents/current`
取当前（最新）简历文档。
- 响应 `200` `ResumeCurrentResponse`：`{ "document": ResumeDocument | null }`（`markdown` = 内容层、`html` = 排版层，生成版 html 有值、上传 v1 为空）。
- **无文档 = 正常空态 → `200` + `document: null`（2026-09-09 契约修订，不再用 `404`）**：「没有」是正常数据不是错误——前端挂载恢复必打这条，用 404 表示空态会让浏览器为 4xx 记一条 console 红字（JS 无法抑制）。前端判 `document === null` 早退即可，无需 catch。

#### `GET /api/v1/documents/original`
取某文档的上传原件文件（PDF/HTML 等，原生预览用）。
- query 参数（可选）：`document_id`——**文档 id（唯一身份，2026-08-12）**；省略 = 当前版本（默认路径，重启后重建当前预览）。回看模式传该稿的 `document_id`（**不是 version**——version 是显示标签可复用，id 才是唯一身份，按 version 会撞同号作废稿）。
- 响应 `200`：原始字节流 + `Content-Type` 按扩展名（html→`text/html`、pdf→`application/pdf`——**重启后前端 blob 的 MIME 必须正确 iframe 才能渲染**，若 octet-stream 会卡"正在渲染 HTML"，2026-08-10 修复）+ `Content-Disposition`（**RFC 5987 编码** `filename*=UTF-8''...`，中文文件名百分号编码——直接内联中文会让 Starlette 抛 UnicodeEncodeError → 500，前端静默落 Markdown，同日修复）；前端 `responseType: 'blob'` 取回 → `URL.createObjectURL` 重建原生预览。
- `404`：该文档无原件（生成/回滚到生成版）或原件文件缺失（历史遗留）——前端落 Markdown 预览。

#### `POST /api/v1/documents/current/typography`
**排版自由度落库（2026-09-02）**：改当前版排版参数（`Typography` 五参数：字号 / 行距 / 段距 / 字间距 / 栏距）→ 后端用该版 `resume_json` 重渲染 `html` + 配置落库。前端改排版先本地注入预览（即时），防抖 ~500ms 后打这里落库——导出 / 重开吃这份带正确排版变量的 HTML。
- 请求体 `ResumeTypographyUpdate`：`{ "typography": { "scale": 1.0, "line_height": 1.25, "spacing": 1.0, "letter_spacing": 0, "gutter": 55 } }`。
- 响应 `200` `ResumeDocument`（含重渲染后的 `html`）。
- 响应 `409`：上传原件（无 `resume_json`，无法重渲染）——前端已置灰控件，这是双保险。

#### `POST /api/v1/documents/rollback`
回滚到目标稿（2026-08-10 软作废回滚；**2026-08-12 A3 身份锚定**）：按 **document_id**（id 唯一身份、永不复用），不按 version——version 是显示标签（软作废后可复用，同号一作废一当前两条），按 version 会撞错稿。目标稿之后所有稿标 `superseded`（**数据全保留**，UI 不显示），目标稿变当前，下一个版本 = 目标稿号+1（稿号连续不跳）。
**2026-09-24 回滚语义改判**：回滚 = **整份恢复目标版开始时的工作台**（资料集 + 改动记录），**直回不叠加**——不是"撤销目标版之后的变化"，而是直接换成那一刻的那一份。回滚后开新 session（版本变更，绑定目标稿 id，resume.md §11.1）；开场引导会说清「从哪退到哪 + 放弃了哪些变化」。
- 请求体 `RollbackRequest`：`{ "document_id": 1 }`。
  - `document_id`：目标文档 id（唯一身份；前端版本面板每行带 `id`）。
  - ~~`include_facts`~~ **已删**：工作台恢复是整份的，没有"只回文档不回工作台"这个半吊子选项。
- 响应 `200` `RollbackResponse`：`{ "version": 2, "workspace_restored": true }`（`version` = 目标稿号，显示用；`workspace_restored` = 是否做了整份恢复）；`404` 目标稿不存在。
  - **响应很快（2026-09-24）**：文档/工作台/session/事件同步完成，**开场引导生成已挪后台**（`schedule_opening`，不再拖住响应）——引导稍晚落库为新 session 的第一条助手消息，前端短轮询带回。回滚**本身全程原子、不可暂停**（停到"文档已作废、工作台恢复一半"比慢更糟）。
- `409` 两种：①「回滚目标 = 当前文档」（回滚到当下无意义）；②**回滚到最早一版**——v1 的快照是空的，恢复等于清空工作台，那是「重置」（`POST /documents/reset`）不是「回滚」。

#### `POST /api/v1/documents/reset`
重置简历：清空文档流 + 快照（回到空态，可重传新 v1）；`clear_facts` 连事实库一起清空。**待执行建议无条件清空**（版本没了建议脱锚，与回滚一致，§12.3 决策四）。
- 请求体 `ResumeResetRequest`：`{ "clear_facts": false }`。
- 响应 `200` `ResumeResetResponse`：`{ "documents": 2, "snapshots": 1, "facts": 0, "pending": 1 }`。

#### `POST /api/v1/reset/all`
**核爆（「重新开始」）**：清空一切求职数据回到出厂态（不推荐的后悔药）。设置页「危险区」按钮触发。
- 无请求体。
- 响应 `200` `ResetAllResponse`：
  ```json
  { "documents": 2, "snapshots": 1, "facts": 12, "pending": 3, "preferences": 2, "followup_events": 5, "jobs": 30, "sessions": 4 }
  ```
- 删什么：文档流 + 快照 + 原件目录、事实库、会话/消息、优化建议、判定偏好（preferences）、投递侧（jobs + job_followup_events + interviews）——**全量清空**。
- **保留什么**：用户配置（`settings` 表 app 行——LLM key/模型/base_url/语言/阈值等）原样不动。
- **副作用**：`apply_mode` 强制置 `off`（方向 facts 删了，爬虫再开只能空转）；`data_epoch` +1（在途爬虫下次检查点发现代次变了 → 丢弃在途、不回写、自我了断，方案②协作式取消）。
- 与 `documents/reset` 的区别：后者清文档流、可留 facts（换简历）；本端点连投递数据 + 偏好 + facts 全删，等价「一键回出厂（配置除外）」。
- 顺序关键：先 bump epoch + 关 apply_mode + 删 facts（给在途爬虫立「世界没了」信号），再删 jobs/followup_events/其余表，最后内存态（冲突/抽取）归零——顺序反了会被在途爬虫回写。

#### `POST /api/v1/facts/confirm`
确认入库：批量写入编辑后的事实清单（信息库页手填/编辑确认）。
- 请求体 `FactsConfirmRequest`：`{ "facts": [ { "category", "title", "points" } ] }`。
- 信息库页手填/编辑是**明确意图** → 内部 `auto_adjudicate=True`（fuzzy 带冲突自动 supersede 旧条——用户正在界面亲手编辑，不存在"无感知顶掉"，区别于上传确认路径的卡片裁决）。
- 响应 `200` `FactsConfirmResponse`：`{ "saved": 3 }`。

#### `GET /api/v1/facts`
读事实列表（信息库页按分类展示/编辑用）。
- query 参数（可选）：`category`、`status`（默认 `active`）。
- 响应 `200` `FactsListResponse`：`{ "facts": [ { "id", "category", "title", "points", "source", "status", "occurred_at", "updated_at" } ] }`。
- 嵌套模型（2026-08-07 重构）：一条记录 = `title`（总条目/单句）+ `points`（子要点数组，JSON 文本列，可空）；层级在一条内，不再用 `group_id` 外键。前端展平成 `[category] title` + `- point` 行注入 prompt。

#### `PATCH /api/v1/facts/{id}`
部分更新一条事实（信息库页编辑；也可用于把某条标 `superseded` 留 history）。
- 请求体 `FactUpdate`（全可选）：`{ "category"?, "title"?, "points"?, "status"? }`。
- 响应 `200` `Fact`；`404` 不存在。

#### `DELETE /api/v1/facts/{id}`
删除一条事实（信息库页删除）。
- 响应 `204`；`404` 不存在。

### 投递 · 岗位聚合（阶段二 v1，前缀 `/api/v1`）
> 2026-08-13 前端先行定稿，从界面用到的字段倒推契约。设计与理由 → `docs/design/apply.md`。
> 岗位 = 跨平台统一列表（源岗位，同源幂等 upsert）；跟进 = 用户对岗位的投递状态时间线（`job_followup_events`，单一状态线，取代旧「筛选反馈」）。
> 2026-08-31 抓取触发重构：废手动刷新 + 废轮巡 → 事件驱动 `kickCrawl` + 续页游标 + 单平台三态；平台收敛两家（猎聘 + 前程无忧，BOSS 彻底砍）。

#### `POST /api/v1/jobs/crawl/kick`
事件驱动的一脚（前端 `kickCrawl` → Electron → 后端）。门控 + 持锁跑猎聘一轮（自循环 + 自记账），返回猎聘结果 + 前程无忧只读建议。
- 请求体：无（门控在后端判：`apply_mode 开 && 方向非空 && unprocessed < 触发值 && 尚有可爬组`）。
- 响应 `200` `KickResponse`：
  ```json
  {
    "liepin": { "source": "liepin", "kind": "fresh", "group_idx": 0, "page": 2, "added": 3, "updated": 0, "exhausted": false },
    "job51": { "source": "job51", "kind": "fresh", "group_idx": 0, "page": 1, "added": 0, "updated": 0, "exhausted": false },
    "data_epoch": 3,
    "job51_quota": 20,
    "cap": 10,
    "gate_reason": "crawled",
    "round_id": 7
  }
  ```
  - `liepin`：后端自循环一轮的结果（已在锁内推进游标 + 标三态 + 入库）。`null` = 门控拒（大闸关/方向空/backlog 满/全 exhausted 或 throttled）。
  - `job51`：只读建议——`null` = 不该爬；非 null 则 Electron 据 `group_idx`/`page` 跑前程无忧 DOM 一轮。
  - `data_epoch`：核爆计数器，Electron 当前程无忧一轮的检查点基线。
  - `job51_quota`/`cap`：前程无忧一轮参数（凑满 quota 停 / 最多扫 cap 页）。
  - `round_id`（2026-09-14 加，apply.md §11.7）：本轮抓取轮次 id——**一次 kick 一个、两平台共用**。前端在 kickCrawl 尾部拿它调 `GET /analysis/unprocessed?round_id=`；岗位的 `crawl_round_id` 列据此标归属（首触即定）。
  - `gate_reason`（2026-09-01 加，投递页「求职之路」指示器信号）：这一脚整体的门控结果。枚举——
    - `crawled`：至少一家在爬（正常）。
    - `apply_mode_off`：大闸关。
    - `no_direction`：方向空。
    - `backlog_full`：未处理 ≥ 触发值（底仓满，正常暂停，不是受限）。
    - `throttled`：两家都在限流冷却期（等 ~15 分钟）。
    - `exhausted`：两家当天都爬完（转天自动恢复）。
    门控拒时 `liepin`/`job51` 都 `null`，靠 `gate_reason` 区分「受限（throttled/exhausted）」与「backlog 满」——否则两者在返回里长得一样。

#### `POST /api/v1/jobs/crawl-state`
前程无忧一轮结果上报（Electron 读 DOM → 后端记账推进游标 + 标三态）。猎聘不走这里（后端自循环自记账）。
- 请求体 `CrawlStateReport`：`{ "source": "job51", "kind": "fresh", "group_idx": 0, "page": 3, "added": 5, "updated": 0, "reason": "quota" }`
  - `kind`：`fresh`（有新增）/ `throttled`（被限流，Electron `bodyLen=0`/翻页器点不动）/ `exhausted`（空页到底）。
  - `reason`：仅 `kind=fresh` 有值——`quota`（凑满，游标前进）/ `cap`（撞页数上限，游标回第 1 页）。
- 响应 `200` `CrawlStateResult`：`{ "source": "job51", "kind": "fresh", "group_idx": 0, "page": 3, "added": 5, "updated": 0, "exhausted": false }`（记账后的游标 + 是否整家 exhausted）。

#### `GET /api/v1/jobs/crawl-state?source=job51`
读某平台抓取游标（Electron 前程无忧一轮的起点）。
- 响应 `200` `CrawlStateRead`：`{ "source": "job51", "group_idx": 0, "page": 1, "group_count": 2, "exhausted": false }`

#### `POST /api/v1/jobs/ingest`
批量入库 Electron 读 DOM 抓到的岗位（同源幂等 upsert）。
- 请求体 `JobIngestRequest`：`{ "jobs": [{ "source_name": "job51", "external_id": "...", "title": "...", ... }, ...], "data_epoch": 3 }`
  - 每条岗位字段与 `repo.upsert_job` 入参同形：`source_name` / `external_id` / `title` / `company` / `city` / `salary_text` / `experience` / `degree` / `skills` / `job_labels` / `welfare` / `description` / `source_url` / `apply_url` / `liveness` / `platform_updated_at`。
  - `source_name` 支持 `liepin` / `job51`（`boss` 已彻底砍、`zhilian` 已放弃——2026-08-31）。
  - `data_epoch`（可选，前程无忧一轮传）：开抓时的数据代次。后端在写事务里比对——若代次已变（核爆 `POST /reset/all` 重置了数据），**丢弃这批、不回写**，返回 `aborted: true`。
- 响应 `200` `JobIngestResponse`：`{ "added": 1, "updated": 2, "aborted": false }`
- **核爆协作式取消（2026-08-21 方案②）**：`ingest` 的写事务与 `reset_all` 的 `bump_data_epoch` 共用一把进程内锁——「读 epoch 比对 + 写 jobs」原子。核爆后带旧代次入库 → `aborted: true`（非错误，是协作式取消的正常收尾）。两向都无孤儿岗位。
- 由 Electron 前程无忧一轮调用（读 DOM 抓取 → 批量入库）。

#### `GET /api/v1/jobs/external-ids`
读某平台已入库的全部 external_id（Electron 前程无忧去重用：只抓没抓过的岗位）。
- query 参数：`source`（必填，`liepin`/`job51`）
- 响应 `200`：`{ "ids": ["job51-1", "job51-2", ...] }`

#### `GET /api/v1/jobs`
读岗位列表（跨平台统一列表）。
- query 参数（可选）：`sort`（`time`/`followup`）、`order`（`asc`/`desc`）、`status`（状态 tab，见下）、`reason`（仅 `status=not_pursuing` 叠加）、`start`/`end`（日期范围，闭区间，ISO 字符串，按「处理日期」过滤——未处理按 `last_seen_at`、其余按 `followup_at`）。**`source` 平台筛选已删（2026-09-01）**——前端不再提供平台下拉，列表恒全平台；**`sort=freshness/relevance` 与 `query`（检索关键词）已删（2026-09-08）**——排序只剩单一「时间」维度，「相关度」是死维度（前端从不传 `query`）。
- 响应 `200` `JobsListResponse`（2026-08-31 加 `counts`）：
  ```json
  {
    "jobs": [
      {
        "id": 1, "source": "liepin", "external_id": "lp-1001",
        "title": "资深前端工程师", "company": "字节跳动", "city": "上海", "salary_text": "30-45K·14薪",
        "experience": "3-5年", "degree": "本科",
        "skills": ["React", "TypeScript"], "job_labels": ["大厂"], "welfare": ["六险一金"],
        "description": "负责前端开发…（JD 正文，详情页读 DOM 抓取，可空）",
        "source_url": "https://…", "apply_url": "https://…",
        "liveness": "active", "last_seen_at": "2026-08-13T09:00:00Z", "is_new": true,
        "followup_status": "applied", "followup_reason": null, "followup_at": "2026-08-30T…"
      }
    ],
    "counts": {
      "unprocessed": 32, "applied": 15, "interviewing": 4, "offered": 1, "not_pursuing": 28
    }
  }
  ```
  - `counts`：各状态徽标数（一次请求列表 + 全徽标）。**`interviewing` = 待面试场次数**（非岗位数，与「面试」tab 徽标一致；`outcome=scheduled` 且 `scheduled_at >= now`）。
- 前端 `is_new`：本轮刷新新增的岗位（增量标记）。`liveness`：active 新抓在前/stale 沉底/closed 灰显。

#### `GET /api/v1/jobs/{id}`
读单条岗位详情（详情弹窗岗位概览用）。
- 响应 `200` `Job`（同列表字段）。
- 响应 `404`：岗位不存在。

#### `POST /api/v1/jobs/{id}/followup`
追加一条投递跟进状态事件（**取代已作废的 `PUT /jobs/{id}/screening`**，2026-08-30）。状态线 = 追加式时间线，当前态派生；任意跳、可回退、无状态机校验。设计与理由 → `docs/design/apply.md` §12。
- 请求体 `FollowupEventCreate`：
  ```json
  { "status": "applied" }
  { "status": "interviewing", "stage": "技术二面" }
  { "status": "not_pursuing", "reason": "failed", "note": "面后未通过" }
  { "status": "offered" }
  ```
  - `status`（必填）：`applied` 已投递 / `interviewing` 待面试 / `offered` 已录用 / `not_pursuing` 不再追踪。
  - `reason`（仅 `not_pursuing` 有值）：`withdrawn` 主动放弃 / `failed` 未通过 / `job_closed` 岗位关闭 / `duplicate` 已投过。
  - `stage`（仅 `interviewing` 有值，自由文本）：面试轮次（如「技术二面」）；更新轮次 = 再追加一条 `interviewing` 事件带新 stage。
  - `note`（自由文本，可空）：这次变化的「为什么」。
- 响应 `200` `FollowupEvent`：`{ "id": 1, "job_id": 5, "status": "applied", "reason": null, "stage": "", "note": "", "source": "user", "at": "2026-08-30T…" }`
- 响应 `404`：岗位不存在。

#### `GET /api/v1/jobs/{id}/followup`
读某岗位的完整状态时间线（`FollowupEvent[]`，按 `at` 升序）。
- 响应 `200`：`{ "events": [{...}, {...}] }`；无事件 = 空数组（岗位未处理）。
- 用途：详情弹窗显示进度史；复盘 agent 读「被拒又捞回」这类市场信号。

#### `GET /api/v1/jobs?status=…`
读岗位列表——**升级支持按状态 tab 查询**（2026-08-30；2026-08-31 加派生态 `interviewed` + `start`/`end` 日期范围）。
- query 参数（新增）：`status`（`unprocessed` / `applied` / `interviewing` / `interviewed` / `offered` / `not_pursuing`）——按当前态（= 最新事件，`interviewing` 按场次 `scheduled_at` 派生为 `interviewed`）过滤岗位；缺省 = 全部。
  - `unprocessed` = 无任何事件的岗位。
  - `interviewing` = 待面试（最新事件 interviewing 且最新场次约定时间未到、outcome 仍 scheduled）。
  - `interviewed` = 已面试（最新事件 interviewing 且约定时间已过，或 outcome 已非 scheduled）——**惰性派生态**，不写事件。
  - `not_pursuing` 可再叠加 `reason`（`withdrawn`/`failed`/`job_closed`/`duplicate`）——不再追踪 tab 顶部的原因筛选下拉。
  - `start`/`end`：日期范围（闭区间 `[start, end]`，ISO 字符串）。未处理按 `last_seen_at` 过滤，其余按 `followup_at`（处理日期）过滤。
- 响应 `200` `JobsListResponse`，每条 `Job` 附当前态（派生）+ 顶层 `counts`：
  ```json
  { "jobs": [{ "...": "...", "followup_status": "interviewing", "followup_at": "2026-08-30T…" }], "counts": { "unprocessed": 32, "applied": 15, "interviewing": 4, "offered": 1, "not_pursuing": 28 } }
  ```
  - `followup_status`：当前态（无事件 = `null` = 未处理；可能是派生的 `interviewed`）。
  - `followup_at`：当前态变更时间（跟进 tab 排序键 = 处理日期；未处理时按 `last_seen_at` 排）。
  - `counts`：各状态徽标数（`interviewing` = 待面试场次数，非岗位数）。
- **排序语义（分 tab）**：`unprocessed` 按 `last_seen_at`（鲜活度）；其余 tab 按 `followup_at`（处理日期）；`order`（asc/desc）切换正倒序。

#### 面试场次（`interviews`，2026-08-31）
> 面试 tab = 日历。数据源 = `interviews` 全表（不过滤状态，编年可追溯）。场次与「面试阶段」（状态线管）分两层；终局（offered/failed）条件式驱动状态线。

#### `GET /api/v1/interviews`
列面试场次（日历数据源 = 全表）。query 参数 `job_id` 可选（只看某岗位）。
- 响应 `200` `InterviewListResponse`（2026-08-31 内嵌 `job` 摘要——日历自给自足，不依赖全量 jobs）：
  ```json
  {
    "interviews": [
      {
        "id": 1, "job_id": 5, "scheduled_at": "…", "location": "", "round": "技术一面",
        "outcome": "scheduled", "next_round_id": null,
        "job": { "company": "字节跳动", "title": "资深前端工程师", "description": "负责前端开发…" }
      }
    ]
  }
  ```
  - `job`：内嵌岗位摘要（`company`/`title`/`description`）——面试详情弹窗的岗位信息 + JD 联查用。场次编年、不过滤状态，靠全量 jobs 联查会漏已面岗位信息，故内嵌。

#### `POST /api/v1/jobs/{id}/interviews`
排一场面试（唯一进入「待面试」的入口）：建场次（outcome=scheduled）+ 联动追加 `interviewing` 事件。
- 请求体 `InterviewCreate`：`{ "scheduled_at": "2026-09-01T14:00:00Z", "location": "线上", "round": "技术一面" }`
- 响应 `201` `Interview`；`404` 岗位不存在。

#### `PATCH /api/v1/interviews/{id}`
更新一场面试（改期 / 改结果 / 安排下一场）。终局（offered/failed）联动状态线。
- 请求体 `InterviewUpdate`（全字段可选）：
  ```json
  { "outcome": "failed", "reason": "job_closed" }
  { "outcome": "offered" }
  { "outcome": "next_round", "next_scheduled_at": "2026-09-10T14:00:00Z", "next_round": "技术二面" }
  { "scheduled_at": "2026-09-05T15:00:00Z" }
  ```
  - `outcome`：`scheduled` / `awaiting` / `next_round` / `offered` / `failed`。
  - `reason`（仅 `outcome=failed` 时用于联动状态线）：默认 `failed`，可当场选 `job_closed` / `withdrawn`。
  - `next_scheduled_at`/`next_round`（仅 `outcome=next_round`）：下一场的时间/轮次——本场标 `next_round` + `next_round_id` 指向新行（新行 scheduled）。
  - `scheduled_at`：改期（不早于今天）。
- 响应 `200` `Interview`（本场，含更新后的 `outcome` / `next_round_id`）；`404` 场次不存在。

### 投递 · 分析（阶段二，前缀 `/api/v1`）
> 2026-09-14（apply.md §11.7 grill 定稿）。投递页的**分析不是"被送达的消息"，是页面的一部分**——
> 翻到哪个 tab、点开顶部那个分析控件，就看到那个 tab 的分析（**顶部一行控件，不是右栏**）。
> 三 tab 三套：未处理（适配度 + 改进方向 = **处方**）｜已投递（投递日报 = 嘱咐）｜面试（单场准备，落详情弹窗）；
> 录用 tab 不做。**算一次存一次**（落 `analysis_reports`），本组端点有则读、无则后台补算返回 `computing`。
> 设计与理由 → `docs/design/apply.md` §11.7。

#### `GET /api/v1/analysis/unprocessed?round_id=`
未处理 tab 的批次分析（本轮那批岗位）。**一轮爬完触发**——前端在 `kickCrawl` 尾部拿 `round_id` 调。
- 查询参数：`round_id`（必填，抓取轮次 id——分析输入 = **本轮抓到的那批**，检索条件会改、轮次间不可比）。
- 响应 `200` `AnalysisReportOut`：
  ```json
  {
    "scope": "batch", "scope_key": "7", "status": "ready",
    "headline": "这批偏资深前端，普遍要 Vue3 + TypeScript",
    "blocks": [
      { "kind": "text", "md": "这批岗位以资深前端为主，React/Vue 是硬门槛……" },
      { "kind": "chart", "spec": { "type": "bar", "title": "技能词频",
        "data": [{"label": "Vue", "count": 8}], "x": "label",
        "series": [{"key": "count"}], "orientation": "vertical", "note": "仅猎聘样本" } },
      { "kind": "text", "md": "学历上硕士占大头，你差在……" }
    ],
    "meta": { "total": 12, "liepin": 8, "job51": 4, "with_jd": 4 },
    "created_at": "2026-09-14T02:11:03+00:00",
    "suggestions": [
      { "id": 31, "type": "fill_gap", "target": "岗位：某公司·资深前端", "original": "",
        "suggested": "补一个 LangGraph 项目经历", "reason": "本批 3 家要求 LangGraph",
        "severity": "high", "status": "proposed", "origin": "job_analysis" }
    ]
  }
  ```
  - `status`：`ready`（有缓存）/ `computing`（正在算，**前端轮询**）/ `missing`（本轮无岗位，没东西可算）。
  - `blocks`（2026-09-15 文/图穿插）：报告正文的**有序块流**——`{"kind":"text","md":…}`（Markdown 段）/
    `{"kind":"chart","spec":…}`（内嵌 `ChartSpec`）。analyze Agent 按叙述顺序产出，**图插在讲它的那段文字
    之后**（讲一段→配一张→再讲一段）。图表 0~4 张、宁缺勿滥；无图 = 全 text 块。`ChartSpec` 契约见
    `app/schemas/chart.py`——6 种图型 `bar/line/area/donut/pie/radar`、series ≤ 2、`x`/`series.key` 须在
    data 行内；数据来自 `compute_stats` 的 StatsPack。前端 `toReportBlocks` + `isChartSpec` 校验兜底，
    坏块（kind 不认得 / chart 配错）单独丢、text 照显。**兼容旧缓存**：无 `blocks` 只有 `body` 时折成单个 text 块。
  - `meta`：统计口径——`total` 本批岗位数 / `liepin`·`job51` 来源分布 / `with_jd` 有 JD 数
    （前端据此标注"深度分析（有 JD）vs 概览（无 JD）"，不让用户以为覆盖不全都一样深）。
  - `suggestions`：**待收录的处方**（`origin=job_analysis`、子项仍 `pending` 的改动记录）——**不进优化点面板**
    （面板只读带子项的记录；这些子项还没被用户认可），点「改进」走 `POST /optimization/promote`
    收录成一条改动记录（子项直接 `confirmed`）→ 面板本就每轮注入聊天 agent → 去简历页拍板。
  - **2026-09-15 graph 化**：batch 走 `graphs/analysis.py`（load → prepare → compute_stats → analyze →
    persist），analyze 用带工具位的 Agent（详见 apply.md §11.7.6）。两种数据源两种分析法不变：猎聘
    （无 JD、结构化标签）代码聚合；前程无忧（有 JD）逐岗读 JD 提炼（留在 service prepare 阶段）。

#### `GET /api/v1/analysis/applied`
已投递 tab 的投递日报。**进页面触发**（后端按本地日 "截至昨天" 的缓存键）。
- 响应 `200` `AnalysisReportOut`（`scope=daily`，**无 suggestions**——日报是纯**嘱咐**，不是简历改动清单）。
- `scope_key` = 本地日的昨天（`YYYY-MM-DD`，UTC+8）——报告是日期的纯函数，同一日期永只算一份、永不需复算。
- **2026-09-15 graph 化**：与 batch 同构——走 `graphs/analysis.py`，取数 = 观察窗（往回 14 天）内的
  投递活动（`job_followup_events` 事件 + 岗位对），前置统计用代码算（`compute_daily_stats` →
  `DailyStatsPack`：状态分布 / 来源分布 / 逐日投递趋势 / 响应率）——**投递活动视角**（非 batch 的岗位画像）。
- `blocks` 同 batch（有序块流、文/图穿插、配图数据取自「本段统计」）。观察窗内**无任何活动** → 不写空报告
  （读不到 → 每次仍返回 `computing`，属正当信号：真没东西可报）。

#### `GET /api/v1/analysis/interview/{interview_id}`
单场面试准备。**展开该场次时触发**（面试详情弹窗）。
- 响应 `200` `AnalysisReportOut`（`scope=interview`，纯嘱咐）。JD 缺失（猎聘侧无 `description`）→ 诚实标注，只给通用准备。

### 投递 · 方向（阶段二 v2，前缀 `/api/v1`）
> 2026-08-19 grill 定稿。方向 = **派生事实**（存 `user_facts`，不建独立表）——「求职目标」= `category=basic`、`title="目标岗位：{role}"` 的单值槽位（`save_target_role` 已实现），「搜索词」挂同一条 fact 的 `points`，「城市」= 同构单值槽位 `title="目标城市：{city}"`。设计与理由 → `docs/design/apply.md` §11.2。
> **2026-08-19 收尾（role 派生 + 面板可编辑）**：推翻「面板只读三道闸」——方向面板**可编辑**（搜索词增删 + 城市单值可空），role = `keywords[0]` 纯派生（不单独存/不单独编辑/不入参）。
> **2026-08-20 定稿（方向靠聊天自动生成，删投递页 refine 按钮）**：方向由**聊天 agent** 在简历页聊出目标后经 `refine_direction`/`commit_direction` 工具生成（走 service 真源，不经 HTTP）。投递页面板**只读当前方向 + 手填编辑 + 保存**（commit）。**删去 `POST /direction/refine` 端点**——refine 只是 service 函数（聊天 agent 工具直调），不再暴露 HTTP。
> **2026-08-21 定稿（查询组二维化 + role 独立）**：搜索词从一维升级为二维查询组 `list[list[str]]`——**组内多词、组间 OR**。**role 恢复为独立字段**（岗位标签，写简历/方向展示用，**不参与搜索**），与 keywords 互不派生、内容可重复。落盘：`title` 存 role、`points` 每个元素 = 组内空格 join（`"全栈 英语"`），`get_direction` 读回时 `split()` 拆回二维。
> **2026-09-11 实测修正**：原「组内 AND」是**不完整**的描述——平台对多词是**自适应 AND**（有交集按 AND、交集不足**静默放宽成 OR**，放宽留哪个词与词序无关）。**写法**（一组 = 概念 + 大范围共现的收窄词、同义变体只留最强的一个、2~3 组起步）见 `app/skills/direction/SKILL.md`。

#### `GET /api/v1/direction`
读当前方向（抓取器替换硬编码 `"前端"`/上海用；Electron `job-scraper.js` 抓前调一次）。
- 响应 `200` `DirectionResponse`：
  ```json
  {
    "role": "全栈工程师",
    "keywords": [["全栈", "英语"], ["后端"]],
    "city": "上海"
  }
  ```
  - `role`：目标岗位标签（写简历 target + 方向展示用，**不参与搜索**）；可空。
  - `keywords`：二维查询组（组内多词——真实语义自适应 AND；组间靠 `external_id` 同源去重实现 OR 语义）；空 = 无方向。
  - `city`：目标城市（**必填 + 单城市白名单，2026-09-01**：只支持 25 热门城市
    [北京/上海/广州/深圳/武汉/西安/杭州/南京/成都/重庆/东莞/大连/沈阳/苏州/昆明/长沙/合肥/宁波/郑州/天津/青岛/济南/哈尔滨/长春/福州]）。
    空/多城市/白名单外 → 409 出声。空 city 不再允许（前程无忧省略 jobArea 实为 IP 城市、非全国）。
- 由后端 service 聚合「目标岗位」+「目标城市」两条 fact 吐干净对象；抓取器不直接解析 facts JSON。抓取器只读 `keywords`（role 不参与搜索）。

#### `GET /api/v1/direction/cities`
可选城市库（2026-09-01 新增）：前端城市下拉数据源。
- 响应 `200` `CitiesResponse`：`{ "cities": ["北京", "上海", ...25 城...] }`（有序）。

#### `POST /api/v1/direction/commit`
改方向（人拍板）：写回 facts，返回待处置的未处理岗位数。
- **确认流程（提交前，不在本端点内）**：agent 口头问 / 面板就地编辑，用户拍板后才调本端点；岗位处置在 commit **之后**由前端就地弹窗（见下），不在 commit 内。
- 请求体 `DirectionCommitRequest`（**role 独立入参**，与 keywords 互不派生）：
  ```json
  {
    "role": "全栈工程师",
    "keywords": [["全栈", "英语"], ["后端"]],
    "city": "上海"
  }
  ```
  - `role`：目标岗位标签（写简历/展示用，不参与搜索）；可空。
  - `keywords`：二维查询组（非空；组内多词——自适应 AND；组间 OR）。落 `target_role` fact 的 `title=role` + `points=[" ".join(g) for g in keywords]`。
  - `city`：目标城市（**必填 + 单城市白名单，2026-09-01**：25 热门城市，见 GET /direction/cities）。空/多城市/白名单外 → 409 出声。
  - **2026-08-25**：删 `dispose_unprocessed`——commit 只写方向不碰岗位。
- 响应 `200` `DirectionCommitResponse`：`{ "role": "全栈工程师", "keywords": [["全栈", "英语"], ["后端"]], "city": "上海", "pending_disposal_count": 2 }`。
  - `pending_disposal_count`（2026-08-25）：落定方向后**仍未处理的岗位数**（无 `job_followup_events` 记录）——>0 前端就地弹「留/删」确认，清走 `POST /direction/dispose-unprocessed`；commit 本身不删任何岗位。
- 响应 `409` `code=conflict`：keywords 为空；或 city 空/多城市/不在白名单（2026-09-01 必填）。
- **系统事件气泡**：commit 成功（按钮路径，`record_event=true`）→ 往当前 session 补一条 event 气泡「已把求职方向改为…」；agent 工具路径（`record_event=false`）不补（agent 自己返回落定文案）。

#### `POST /api/v1/direction/dispose-unprocessed`
删除未处理岗位（前端岗位处置弹窗选了「清」）。**keep（留）无操作，前端不调本端点**。
- 请求体：无。
- 响应 `200` `DirectionDisposeResponse`：`{ "deleted_jobs": 1 }`——只删无 `job_followup_events` 记录的岗位；已投 / 已处理（有事件）无条件保留。

### 设置（前缀 `/api/v1`）
> 只放**后端用户偏好**（SQLite 真相源）：语言、LLM 模型/base_url/API key。
> API key **只写不回读**（响应掩码）；端口/关闭行为/数据目录在 Electron 本地 `settings.json` 不经此接口。

#### `GET /api/v1/settings`
读用户偏好；无记录时返回默认值。同时把运行时相关项同步进后端（LLM 模型/base_url/API key、语言）。
- 响应 `200` `AppSettings`（`llm_api_key` 掩码）：
  ```json
  {
    "language": "zh",
    "llm_model": "",
    "llm_base_url": "",
    "llm_api_key": "",
    "apply_mode": false,
    "crawl_quota": { "liepin": 40, "job51": 20 },
    "crawl_cap": 10,
    "crawl_threshold": 5
  }
  ```
  > data_dir 已从后端设置移除（挪到 Electron 线，见「配置三真相源」）。
  > `llm_model` 出厂为空（2026-08-12）：本地不存"出厂默认模型名"，模型名只来自探测接口拉取的真实列表。
  > `apply_mode`/`crawl_quota`/`crawl_cap`/`crawl_threshold`（2026-08-31，apply.md §8 /）：抓取四件套，见 PATCH 字段说明。

#### `PATCH /api/v1/settings`
部分更新：只更新传入字段，其余保持。持久化到 SQLite 并同步 runtime（改模型名/base_url/key 后重置 LLM 缓存，下次真调生效）。
- 请求体 `AppSettingsPatch`（全字段可选），如 `{"language":"en"}`。`llm_api_key` 传明文（写）；留空（不传该字段）= 不修改已存 key。
- 响应 `200`：合并后的完整 `AppSettings`（`llm_api_key` **掩码** `★★★★★★★★★★★★`）。
- 响应 `422`：字段校验失败（如 `language` 非 zh/en）。
- 字段说明：
  - `language`：`zh`|`en`。界面语言 + LLM 输出语言（单一源，后端 Agent 据此注入回答语言）。
  - `llm_model`/`llm_base_url`：LLM 配置（懒加载，下次真调生效）。
  - `llm_api_key`：模型 API key（只写不回读）。**唯一来源 = SQLite**（2026-08-13 N1 删 .env 兜底）；空 = 未配置。
  - `apply_mode`（2026-08-19，apply.md §11.1）：投递总开关「开启求职之路」——`on` / `off`（默认）。`on` 才允许后台限速爬 + 消费触发；`off` 整个不爬（做简历时还没想好方向，不该半推半就投）。重启后读取决定爬取进程是否运行。
  - `crawl_quota`（2026-08-31，apply.md §8② /）：每平台一轮抓几个新岗（`{liepin, job51}`，默认 40 / 20 = 各平台一页条数）。**取代旧 `crawl_batch_size`**——两平台翻页机制各异、每页条数不同，硬套全局「分摊」会拖住主源深爬量。
  - `crawl_cap`（2026-08-31，apply.md §8④）：每轮最多扫几页（全局一个数，默认 10）。深爬 / 稳态是同一循环跑出来的自然现象，cap 是用户唯一能调「扫多深」的杠杆。
  - `crawl_threshold`（2026-08-31 修订，apply.md §8①）：触发值 = 保留的未处理底仓（`unprocessed ≤ 此值` 就补下一批），默认 5。语义从旧「backlog < 此值」收敛为「≤ 此值补」，非「清零才补」。

#### `POST /api/v1/settings/llm/probe`
合并的「测试连接 + 拉模型列表」：先验证 key/端点，连不上**不再继续拉列表**；连上后拉供应商模型列表（OpenAI 兼容 `GET /models`）。
- 请求体 `LlmProbeRequest`（全字段可选，**不继承 AppSettingsPatch**——只接受探测字段，不接受 language/data_dir 等设置字段）：不传则用已保存设置；可传 `llm_model`/`llm_base_url`/`llm_api_key` 覆盖（**覆盖不写入设置**，仅本次探测用）。
- 连接测试（2026-08-12 起，本地不存默认模型名）：
  - 有模型名（已保存/请求覆盖）→ 发一条最短消息验证 key/端点。
  - 无模型名 → 跳过消息测试，直接拉模型列表——列表本身就需要有效 key/端点，成功即证明连接通。
- 响应 `200`（三态）：
  ```json
  { "ok": true, "models": ["gpt-4o", "gpt-5"], "models_ok": true, "message": "" }
  ```
  - **`ok=false`**：连接失败（缺 key、401 鉴权失败、端点不通等），`models_ok=false`、`models=[]`，`message` 为可读原因。前端不再继续拉模型。
  - **`ok=true, models_ok=false`**：连接过但拉不到模型列表（部分供应商 `/models` 缺失/404，此时必须有已存模型名做消息测试，`models=[]`，`message` 为原因）。前端提示用户手动输入模型名。
  - **`ok=true, models_ok=true`**：全成功，`models` 为真实列表。前端展示模型下拉供选择。

### 系统（前缀 `/api/v1`）

#### `POST /api/v1/system/shutdown`
请求后端**优雅关闭**（2026-09-13）。给 Electron 壳用：关后端前先调它，让后端跑完整 lifespan 关闭段（折叠 WAL + 释放连接），而不是被 `taskkill /F` 直接砍掉留个没折叠的 WAL。
- 响应 `200`：`{ok: true}`（立即返回；实际关停由一个 0.2s 后的定时器触发，够把响应写回调用方）。
- 机制：延迟 `signal.raise_signal(SIGINT)`，交给 uvicorn 的 handler 走 graceful shutdown。**本进程内 raise**，不跨进程发信号（Windows 上后者不可靠，且 uv 是中间进程要穿透）。
- 调用方（Electron `gracefulShutdownBackend`）等超时 10s，没退成则落回 `taskkill /F /T` 硬杀——「关干净」优先于「关得优雅」。

## 另两条配置线（不经此接口）
- **`.env`**：纯本地开发覆盖（打包 exe 不读），不再兜底 API key（N1，2026-08-13）。
- **Electron 本地 `settings.json`**（userData）：`backend_port`、`close_action`、`data_dir`、`current_data_dir`、日志三件套。前端经 IPC（`window.desktop.settings.get/set`）读写；端口/数据目录改动重启后生效，关闭行为热生效。
