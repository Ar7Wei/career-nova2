"""优化点（1.2）传输模型。

**2026-09-23 一张表**：优化点 = 改动记录（`change_records`），`reason`（为什么，父/主体）
+ `changes`（改什么，JSON 复合子项，各自带 id + status）。取代旧的扁平 `optimization_pending`
（建议逐条一行）与 `preferences(kind=custom)`（自定义标准）。

- 子项状态机（SuggestionStatus）：pending 待定 / confirmed 已确认 / rejected 已拒绝 /
  discussing 正在聊；终态 applied（已应用进新稿，版本变更时由 confirmed 迁移）/ archived（被结清）。
- 记录级 status 仅作兜底（子项全空时）与整条存废；**操作粒度 = 单条子项**。
- kind：change 本轮整改（用完即结清）/ decision 跨版本持续决策（每版注入 prompt）。
"""

from typing import Literal

from pydantic import BaseModel, Field

# 建议类型（对齐 resume.md §5 LLM 判定维度）——子项自带，供面板 label。
SuggestionType = Literal[
    "quantify",
    "word_choice",
    "structure",
    "fill_gap",
    "job_relevance",
    "highlight",
]

Severity = Literal["high", "medium", "low"]

# 状态机：proposed 是历史遗留（旧处方表停用后不再产生）；applied/archived 为终态（软标记全留存）
SuggestionStatus = Literal["proposed", "pending", "confirmed", "rejected", "discussing", "applied", "archived"]

# 来源：agent 聊天提的 / job_analysis 投递页分析产的处方
SuggestionOrigin = Literal["agent", "job_analysis"]

# 记录种类：change 本轮整改 / decision 跨版本持续决策
ChangeKind = Literal["change", "decision"]


# 改动记录/子项的操作决定（§12.6 优化点操作权，2026-08-29 分级自主度）——闭集，
# 用户 HTTP（面板）与 agent 工具都靠它拦非法值。
ChangeDecision = Literal["accept", "reject", "discuss", "retract"]


class Suggestion(BaseModel):
    """一条优化建议（LLM 产出形状，落库前的中间态；对齐 resume.md §12.2 六类 schema）。"""

    model_config = {"extra": "ignore"}

    type: SuggestionType
    target: str = Field(default="", description="Markdown 锚点定位（如 工作经历>腾讯>第2条）")
    original: str = Field(default="", description="原文")
    suggested: str = Field(default="", description="建议改法")
    reason: str = Field(default="", description="为什么（引用判定标准，如 成果导向）")
    severity: Severity = "medium"


class PendingPrescription(BaseModel):
    """旧 `optimization_pending` 表的一行（**只读，供「改进」收录期用**）。

    旧表已停用待 DROP；本模型只为 `promote_suggestion` 读一条 proposed 处方、
    以及软结清后的行速览——新代码一律用 `ChangeRecord`。
    """

    model_config = {"extra": "ignore"}

    id: int
    type: SuggestionType = "structure"
    target: str = ""
    original: str = ""
    suggested: str = ""
    reason: str = ""
    severity: Severity = "medium"
    status: str = "proposed"
    origin: SuggestionOrigin = "job_analysis"
    document_id: int = 0
    resolved_in_document_id: int | None = None


class ChangeItem(BaseModel):
    """一个改动点（记录内的子项，**可独立定论**）。

    id 在记录内唯一（子项定位用）；status 子项自带（操作粒度 = 单条子项）。
    type/severity 供面板展示（改哪类、多要紧）；target/original/suggested 是改什么。
    """

    model_config = {"extra": "ignore"}

    id: int
    target: str = Field(default="", description="定位锚点（当前简历 JSON 里能对上的位置）")
    original: str = Field(default="", description="原文")
    suggested: str = Field(default="", description="建议改法")
    status: SuggestionStatus = "pending"
    type: SuggestionType = "structure"
    severity: Severity = "medium"


class ChangeRecord(BaseModel):
    """一条改动记录（读模型）：原因（why，主体）+ 改动点复数（what，可空）。

    - reason：为什么改（一等公民，一行一份）。
    - changes：改什么（子项数组，可空——原因先行、子项后补）。
    - status：记录级兜底状态（子项全空时；也作整条存废）。
    - kind：change 本轮整改 / decision 跨版本持续决策。
    """

    model_config = {"extra": "ignore"}

    id: int
    reason: str = ""
    changes: list[ChangeItem] = Field(default_factory=list)
    status: SuggestionStatus = "pending"
    kind: ChangeKind = "change"
    origin: SuggestionOrigin = "agent"
    document_id: int = 0
    resolved_in_document_id: int | None = None
    updated_at: str = ""


class ChangeRecordListResponse(BaseModel):
    """改动记录列表（面板/查询用）。"""

    records: list[ChangeRecord] = Field(default_factory=list)


class ChangeStatusRequest(BaseModel):
    """改改动记录/子项状态的入参。change_id 给了改子项，否则改整条记录。"""

    record_id: int
    status: str
    change_id: int | None = None


class PromotionRequest(BaseModel):
    """投递页处方「改进」的入参：要收录的旧处方行 id。"""

    suggestion_id: int


class OptimizationDecideResponse(BaseModel):
    """操作结果（给前端/agent 的可读反馈）。"""

    result: str
