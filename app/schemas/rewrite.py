"""编辑流水线（rewrite）状态模型：用户"改简历"请求 → 分类 → 内容/排版 → 校验。

2026-08-28 结构化转向（docs/adr/0004）：内容层从自由 Markdown 换成结构化 resume JSON。
- classify：识别用户意图（改内容 / 改布局顺序 / 两者 / 都不是）。
- content：内容优化——基于当前 resume JSON 改内容（结构化输出）。
- layout：排版——固定模板从 resume JSON 渲染 HTML（纯函数，不再 LLM 排版）。
- validate：HTML 结构校验（service 层重试，graph 纯推理不碰 DB）。
产出：新 resume JSON（内容层）+ 新 HTML（渲染快照），写库由 service 编排。
"""

from pydantic import BaseModel, Field

from app.schemas.resume import Typography

class RewriteIntent(BaseModel):
    """分类节点产出：用户意图。"""

    text: str = Field(default="content", description="content|layout|both|none")
    content_request: str = Field(default="", description="内容修改方向（自然语言，content/both 时）")
    layout_request: str = Field(default="", description="布局调整方向（自然语言，layout/both 时）")


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
    # 中间/产出
    intent: RewriteIntent = Field(default_factory=RewriteIntent, description="分类节点产出")
    new_json: str = Field(default="", description="内容层产出（内容优化后；无内容优化 = 原样）")
    new_html: str = Field(default="", description="渲染快照产出（固定模板渲染）")
    summary: str = Field(default="", description="版本名（LLM 顺带吐，无则回退规则名）")
    problems: list[str] = Field(default_factory=list, description="HTML 结构校验问题（空 = 通过）")
    # 机器自愈门（ADR 0012，第 2 块）：content 与 validate_content 的反馈回路。
    content_problems: list[str] = Field(default_factory=list, description="事实覆盖校验缺失清单（喂回 content 补）")
    iterations: int = Field(default=0, description="content 已跑过几轮（MAX_ITERATIONS 兜底防死循环）")
    # 人门（ADR 0012，第 3 块）：human_gate 挂起等人拍板。decision=confirm→落库 / revise→带 feedback 回 content。
    decision: str = Field(default="", description="人门拍板：confirm|revise（Command(resume=) 恢复时写入）")
    feedback: str = Field(default="", description="人门修改意见（decision=revise 时，喂回 content 当本轮修改请求）")
