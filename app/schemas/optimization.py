"""优化建议（1.2）传输模型。

建议六类 schema（docs/design/resume.md §12.2）：
type/target/original/suggested/reason/severity——对齐 §5 LLM 判定维度。
2026-08-10 落库升级：建议组从「聊天暂存」改为「落库对象」，带状态机——
pending 待定 / confirmed 已确认 / rejected 已拒绝 / discussing 正在聊。
suggest → 落库 pending；accept → confirmed；reject → rejected + 记偏好；
聊一聊 → discussing；裁决（decide_suggestion）→ confirmed/rejected 或细化/分裂。
apply → 应用 confirmed → 新稿。
"""

from typing import Literal

from pydantic import BaseModel, Field

# 建议类型（对齐 §5 LLM 判定维度）
SuggestionType = Literal[
    "quantify",
    "word_choice",
    "structure",
    "fill_gap",
    "job_relevance",
    "highlight",
]

Severity = Literal["high", "medium", "low"]

# 建议状态机（2026-08-10）；2026-08-20 加终态 applied/archived（软标记，全留存不删行）；
# 2026-09-14 加 proposed（投递页分析产的处方初始态，**不进面板四栏**，点「改进」→ pending）
SuggestionStatus = Literal["proposed", "pending", "confirmed", "rejected", "discussing", "applied", "archived"]

# 建议来源（2026-09-14，apply.md §11.7.8）：agent 聊天提的 / job_analysis 投递页分析产的处方
SuggestionOrigin = Literal["agent", "job_analysis"]

# 优化点操作决定（§12.6 优化点操作权，2026-08-29 分级自主度）——闭集，工具入参靠它拦住非法值。
SuggestionDecision = Literal["accept", "reject", "refine", "split", "discuss", "retract"]


class Suggestion(BaseModel):
    """一条优化建议（聊天 agent 产出 / 落库持有）。"""

    model_config = {"extra": "ignore"}

    type: SuggestionType
    target: str = Field(default="", description="Markdown 锚点定位（如 工作经历>腾讯>第2条）")
    original: str = Field(default="", description="原文")
    suggested: str = Field(default="", description="建议改法")
    reason: str = Field(default="", description="为什么（引用判定标准，如 成果导向）")
    severity: Severity = "medium"


class Preference(BaseModel):
    """一条用户判定偏好（拒绝项 / 自定义标准，读模型）。

    kind：reject（拒掉这一类）/ custom（自定义标准）。scope 作用域（如 quantify）。
    content 偏好内容。created_at 供排序/审计。
    """

    model_config = {"extra": "ignore"}

    id: int
    kind: str = "reject"
    scope: str = ""
    content: str = ""
    created_at: str = ""


class PendingSuggestion(BaseModel):
    """一条已落库的建议（带 id + 状态）。"""

    id: int
    type: SuggestionType
    target: str
    original: str
    suggested: str
    reason: str
    severity: Severity = "medium"
    status: SuggestionStatus = "pending"
    origin: SuggestionOrigin = "agent"  # 来源（agent 提的 / 投递页分析产的处方）
    document_id: int = 0  # 建议基于的文档 id（分裂时新条沿用同稿）
    resolved_in_document_id: int | None = None  # 在哪个版本被应用/结清（2026-08-20 软标记）；None=仍活跃
    split_from: int | None = None  # 分裂来源建议 id
    updated_at: str = ""  # 状态最近变更时间（ISO；render_panel 变化标记比对用）


class PendingSuggestionsResponse(BaseModel):
    """建议面板四栏数据（悬浮卡片展开时拉取，2026-08-12 加「已拒绝」栏）。

    已拒绝（rejected）版本内**可撤回**（回到待定），故展示在灰栏；
    到版本变更时才清空并延迟记录最终被拒类别的偏好。
    """

    pending: list[PendingSuggestion] = Field(default_factory=list, description="左栏：待定（未处理）")
    confirmed: list[PendingSuggestion] = Field(default_factory=list, description="右栏上：已确认（下次改进方向）")
    discussing: list[PendingSuggestion] = Field(default_factory=list, description="右栏中：正在聊（讨论中未定论）")
    rejected: list[PendingSuggestion] = Field(default_factory=list, description="右栏下：已拒绝（版本内可撤回）")


class SuggestionAcceptRequest(BaseModel):
    """确认一条已落库建议（按 id，2026-08-10：不再传整条 Suggestion）。"""

    suggestion_id: int


class SuggestionAcceptResponse(BaseModel):
    """确认结果。"""

    pending_id: int


class SuggestionRejectRequest(BaseModel):
    """拒绝一条已落库建议：记偏好"拒掉这一类"。"""

    suggestion_id: int
    reason: str = Field(default="", description="用户拒绝理由（记进偏好）")


class SuggestionDiscussRequest(BaseModel):
    """聊一聊：pending → discussing（面板右栏「正在聊」区）。"""

    suggestion_id: int


class SuggestionRetractRequest(BaseModel):
    """撤回一条已定论建议（confirmed/discussing/rejected → pending，2026-08-12）。"""

    suggestion_id: int


class OptimizationDecideRequest(BaseModel):
    """更新一条建议（agent update_suggestion 工具执行，2026-08-25 起开放全状态操作）。

    decision：accept 接受 / reject 拒绝 / refine 细化 / split 分裂 / discuss 聊一聊 / retract 撤回。
    前四个 = 讨论裁决（原 decide_suggestion 语义）；discuss/retract = 面板动作（聊一聊/撤回）
    下放给 agent（§12.6 优化点操作权）。状态守卫：只要还活跃（resolved_in_document_id IS NULL）即可操作。
    """

    suggestion_id: int
    decision: Literal["accept", "reject", "refine", "split", "discuss", "retract"]
    refined: Suggestion | None = Field(default=None, description="refine 时传：更新后的建议内容")
    split_to: list[Suggestion] | None = Field(default=None, description="split 时传：拆分出的新建议")


class OptimizationDecideResponse(BaseModel):
    """裁决结果（给 agent 的可读反馈）。"""

    result: str
