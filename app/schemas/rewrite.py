"""编辑流水线（rewrite）状态模型：用户"改简历"请求 → 分类 → 内容/排版 → 校验。

2026-08-28 结构化转向（docs/adr/0004）：内容层从自由 Markdown 换成结构化 resume JSON。
- classify：识别用户意图（改内容 / 改布局顺序 / 两者 / 都不是）。
- content：内容优化——基于当前 resume JSON 改内容（结构化输出）。
- layout：排版——固定模板从 resume JSON 渲染 HTML（纯函数，不再 LLM 排版）。
- validate：HTML 结构校验（service 层重试，graph 纯推理不碰 DB）。
产出：新 resume JSON（内容层）+ 新 HTML（渲染快照），写库由 service 编排。
"""

from pydantic import BaseModel, Field

from app.schemas.resume import Resume, Typography

class RewriteIntent(BaseModel):
    """分类节点产出：用户意图。"""

    text: str = Field(default="content", description="content|layout|both|none")
    content_request: str = Field(default="", description="内容修改方向（自然语言，content/both 时）")
    layout_request: str = Field(default="", description="布局调整方向（自然语言，layout/both 时）")


class MissedChange(BaseModel):
    """一条「聊定了但没落实」的改动（复核节点判出的遗漏）。

    复核 LLM 逐条对 confirmed 改动，判「新稿里这条改了没」。没改的列出来：
    target 锚点 + 该怎么改（suggested）+ 为什么算没改（note）——喂回 content 当硬补改指令。
    """

    target: str = Field(default="", description="改动点锚点（定位到简历哪一处）")
    suggested: str = Field(default="", description="应该改成什么样")
    note: str = Field(default="", description="为什么判定没落实（新稿该处现在是什么样）")


class VerifyChangesResult(BaseModel):
    """改动落实复核的结构化产出（2026-09-28 改动落实门）。

    missed 为空 = confirmed 改动全部落实，放行往下走；非空 = 有遗漏，回边 content 补改。
    skipped（2026-09-29）：评价性/方向性、无法客观核对的条目（「整体更突出领导力」这类），
    复核跳过不计——单独报出来是让「可核率」可观测，长期大量 skipped 说明上游收录改动时
    写得太虚（该治 record_decision 那侧的收录标准，不是复核的问题）。
    """

    missed: list[MissedChange] = Field(default_factory=list, description="聊定了但没落实的改动点（空=全落实）")
    skipped: list[str] = Field(default_factory=list, description="评价性/方向性、无法客观核对而跳过的条目锚点（跳过不计）")


class ContentEditResult(BaseModel):
    """content agent 的结构化输出（2026-09-28 包一层）：简历本体 + 可选异议。

    为什么不直接用 Resume 当输出 schema：Resume 是**产物**（要落库、要渲染），不该带
    「对复核判定的异议」这种过程性字段。objection 只在落实门补改这一轮有意义——content
    认为复核把「其实改了」的判成「没改」时，写一句异议，交回复核仲裁（见 verify_changes）。
    """

    model_config = {"extra": "ignore"}

    resume: Resume = Field(default_factory=Resume, description="改后的简历本体")
    objection: str = Field(default="", description="对上一轮复核判定的异议（认为误判时填；无异议留空）")


class RewriteState(BaseModel):
    """编辑流水线状态：当前文档进，新 resume JSON + HTML 出。"""

    # 输入
    current_json: str = Field(default="", description="当前内容层 resume JSON（生成版才有；上传 v1 空）")
    current_html: str = Field(default="", description="当前渲染快照 HTML（可为空）")
    user_request: str = Field(default="", description="用户的改简历请求（自然语言）")
    facts_text: str = Field(default="", description="资料集当前事实（暖态内容编辑只补缺口，JSON 权威）")
    typography: Typography = Field(default_factory=Typography, description="排版自由度配置（2026-09-02）：service 层从当前版本读出注入，layout 渲染用；Node 不碰 DB")
    target_role: str = Field(default="", description="目标岗位侧重（2026-09-07 ADR 0012 合并）：service 从方向槽位读出注入，content 生成侧重；Node 不碰 DB")
    preferences: str = Field(default="", description="自定义侧重（2026-09-07 ADR 0012 合并）：service 从 custom 偏好读出注入，content 生成侧重；Node 不碰 DB")
    # 本版**真进了图**的已确认改动点 (record_id, change_id)（件 1，2026-09-24）：随草稿一起
    # 过 checkpointer（重启不丢）。结清时据此判 applied/archived——`applied` 的字面语义是
    # 「这版应用了它」，不能因为"有 confirmed"就一律标已应用（generate_resume 这条路不带
    # 已确认改动，旧实现照样标 applied，害得新版开场引导谎报「这版做了这些调整」）。
    # 只有 apply_confirmed=True（「开始改」）才非空；其余一律空 = 全标 archived。
    applied_changes: list[tuple[int, int]] = Field(default_factory=list, description="本版进了图的已确认改动点 (record_id, change_id)")
    # 改动落实门（2026-09-28）：service 把本版带进图的 confirmed 改动**渲染成文本**注入（与进
    # user_request 的同源），复核节点拿它逐条对「新稿落实了没」。空 = 无 confirmed 改动可核（不跑复核）。
    confirmed_changes_text: str = Field(default="", description="本版待核对的已确认改动清单（service 注入）")
    missed_changes: list[MissedChange] = Field(default_factory=list, description="复核判出的遗漏改动（非空 → 回边 content 补改）")
    # content 的异议（2026-09-28 反驳机制）：落实门补改时，content 若认为复核判错了（那条其实改了/
    # 不该改），把异议写在这。复核节点下一轮仲裁时读它——成立的误判由复核撤掉，不进 missed。
    content_objection: str = Field(default="", description="content 对上一轮复核判定的异议（空=无异议）")
    # 中间/产出
    intent: RewriteIntent = Field(default_factory=RewriteIntent, description="分类节点产出")
    new_json: str = Field(default="", description="内容层产出（内容优化后；无内容优化 = 原样）")
    new_html: str = Field(default="", description="渲染快照产出（固定模板渲染）")
    summary: str = Field(default="", description="版本名（LLM 顺带吐，无则回退规则名）")
    problems: list[str] = Field(default_factory=list, description="HTML 结构校验问题（空 = 通过）")
    # 机器自愈门（ADR 0012，第 2 块）：content 与 validate_content 的反馈回路。
    content_problems: list[str] = Field(default_factory=list, description="事实覆盖校验缺失清单（喂回 content 补）")
    iterations: int = Field(default=0, description="content 已跑过几轮（MAX_ITERATIONS 兜底防死循环）")
    # 改动落实门独立计数（2026-09-29）：落实门回边补改的预算**不与人门 revise 共享 iterations**——
    # 两道门的「最大重试预算」是两件独立的事，共享会让人门 revise 吃掉落实门的补改预算（用户
    # 改几轮后落实门再想补，可能因 iterations 满了被直接放行，漏网的 confirmed 就这么进了人门）。
    verify_iterations: int = Field(default=0, description="改动落实门已回边补改过几轮（独立预算，MAX_ITERATIONS 兜底）")
    # 结构完整性门（2026-09-30，两次「JSON 塌了渲染成代码」事故的根治）：content 产出后、渲染前，
    # 校验 resume JSON 能不能干净解析成 Resume。判据确定性（pydantic 校验，不调 LLM、零误报），
    # 与已停用的「事实覆盖门」（字面子串、误报罚改写）本质不同——所以这道门**敢回边**。
    # structure_problems 非空 = 塌了（指得出塌在哪个字段），喂回 content 当补改指令。
    structure_problems: list[str] = Field(default_factory=list, description="结构塌检测问题清单（非空 → 回边 content 重改）")
    # 独立计数（与 verify_iterations / 人门 revise 的 iterations 都不共享，理由同 2026-09-29 拆分）：
    # 结构门重试预算是第三件独立的事，共享会被人门 revise 或落实门挤占，把「塌了」漏进渲染。
    structure_iterations: int = Field(default=0, description="结构门已回边重改过几轮（独立预算，MAX_ITERATIONS 兜底）")
    # 人门（ADR 0012，第 3 块）：human_gate 挂起等人拍板。decision=confirm→落库 / revise→带 feedback 回 content。
    decision: str = Field(default="", description="人门拍板：confirm|revise（Command(resume=) 恢复时写入）")
    feedback: str = Field(default="", description="人门修改意见（decision=revise 时，喂回 content 当本轮修改请求）")
