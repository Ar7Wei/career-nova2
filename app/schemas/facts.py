"""用户信息事实库（user_facts）传输模型。

自然语言事实 + 基本分类；是"对用户的了解"的原料，全局共享（FRAMEWORK 1.0）。
分类不是硬 schema，是归类；用 Literal 收口在五个基本分类上。
"""

from typing import Literal

from pydantic import BaseModel, Field

# 基本分类：基本信息 / 教育经历 / 工作经历 / 项目经历 / 个人技能（含证书）/ 其他补充
# 项目经历单独一档（2026-08-29）：原简历常有独立「项目经历」章节，第一次抽取时照着搬
# 几乎零成本，且避免 work 混装 projects 后，下游大源模型要反推「哪段是工作、哪段是项目」。
FactCategory = Literal["basic", "education", "work", "projects", "skill", "other"]

# 来源：上传识别 / 对话记录 / 信息库界面手填
FactSource = Literal["resume_upload", "chat", "manual"]

# 状态：active 生效 / superseded 被新事实覆盖（留 history）
FactStatus = Literal["active", "superseded"]


class Fact(BaseModel):
    """一条用户信息事实（读模型，含 id 与时间戳）。

    2026-08-07 重构：嵌套模型。title = 总条目/单句事实；points = 子要点数组（可空）。
    层级在一条内表达，不再用 group_id 外键。
    """

    model_config = {"extra": "ignore"}

    id: int
    category: FactCategory
    title: str = ""
    points: list[str] = Field(default_factory=list)
    source: FactSource = "manual"
    status: FactStatus = "active"
    on_resume: bool = True  # 该不该渲染进简历成品（ADR 0011）；方向/敏感→False，内容→True
    occurred_at: str | None = None  # 事实时间线（时间上的来源）
    updated_at: str = ""


class FactCreate(BaseModel):
    """新建事实（确认入库 / 对话记录 / 手填共用）。

    title + points：无 points = 单句事实；有 points = 总条目 + 子要点。
    """

    model_config = {"extra": "ignore"}

    category: FactCategory
    title: str = Field(..., min_length=1, max_length=2000)
    points: list[str] = Field(default_factory=list)
    source: FactSource = "manual"
    on_resume: bool = True  # 该不该上简历（ADR 0011）；默认 True，方向/敏感由调用方或 LLM 置 False
    occurred_at: str | None = None


class FactUpdate(BaseModel):
    """部分更新事实：全字段可选（信息库界面编辑用）。"""

    model_config = {"extra": "ignore"}

    category: FactCategory | None = None
    title: str | None = Field(default=None, min_length=1, max_length=2000)
    points: list[str] | None = None
    status: FactStatus | None = None
    on_resume: bool | None = None


class FactsConfirmRequest(BaseModel):
    """识别确认入库：一组待确认事实（用户可在确认前编辑 content）。"""

    facts: list[FactCreate] = Field(default_factory=list)


class FactsConfirmResponse(BaseModel):
    """确认入库结果：实际写入条数。"""

    saved: int


class FactsListResponse(BaseModel):
    """事实列表（信息库页按分类展示/编辑用）。"""

    facts: list[Fact] = Field(default_factory=list)


class FactConflict(BaseModel):
    """一条未裁决的真伪冲突（新事实撞上已存在的相似事实，不写库、交用户裁决）。

    两种调用方共用同一形状（甲方案，2026-08-13）：
    - new_fact：对话挖掘路径带完整候选事实（agent 据此组织反问话术）。
    - new_title：上传确认路径只带新事实标题（冲突条不写库）。
    existing_id / existing_title：撞上的旧事实（id 供后续 supersede 用）。
    """

    model_config = {"extra": "ignore"}

    new_fact: FactCreate | None = None
    new_title: str = ""
    existing_id: int
    existing_title: str = ""


# ---- 简历识别（LLM 抽取）----


class ExtractedFact(BaseModel):
    """LLM 从简历抽出的单条事实（无 id，确认入库时才分配）。

    2026-08-07 重构：嵌套。title = 总条目（如 "腾讯 后端工程师 2021-至今"），
    points = 子要点数组（如 ["订单系统开发", "性能优化40%"]）；单点事实无 points。
    """

    model_config = {"extra": "ignore"}

    category: FactCategory
    title: str = Field(..., min_length=1, max_length=2000)
    points: list[str] = Field(default_factory=list)
    on_resume: bool = True  # 抽取时 LLM 判「该不该上简历」（ADR 0011）：敏感/不该上→False


class ParseCoverage(BaseModel):
    """覆盖率自述：让"漏抽"可见（识别到 N 段 vs 抽出 M 段）。"""

    detected_sections: int = Field(default=0, description="识别到的经历/板块约数")
    extracted_sections: int = Field(default=0, description="实际抽取的段数")
    note: str = Field(default="", description="一句话覆盖率说明")


class ExtractedFacts(BaseModel):
    """LLM 抽取结果（结构化输出 schema）：事实清单 + 覆盖率自述。"""

    facts: list[ExtractedFact] = Field(default_factory=list)
    coverage: ParseCoverage = Field(default_factory=ParseCoverage)


# ---- 抽取状态（后台抽取 → 卡片确认）----


class ExtractState(BaseModel):
    """后台抽取的内存态：前端轮询 extract-status 驱动蒙版/卡片/通道开关。

    - idle：无抽取任务（可上传）。
    - extracting：抽取进行中（蒙版 + 进度条）。
    - extracted：抽取完成，候选事实已备（弹卡片，待确认）。
    - failed：抽取失败（错误提示 + 通道重开）。
    - confirmed：确认入库完成（蒙版消失、通道关闭、ready）。
    """

    model_config = {"extra": "ignore"}

    state: Literal["idle", "extracting", "extracted", "failed", "confirmed"] = "idle"
    document_id: int | None = None  # 本次抽取锚定的文档 id（唯一身份，A3）
    generation: int = 0  # 当前代次（confirm/reject 防陈旧）
    facts: list[ExtractedFact] = Field(default_factory=list)  # 抽取候选（确认才入库）
    error: str | None = None  # failed 时的原因


class ExtractConfirmRequest(BaseModel):
    """确认抽取结果：卡片编辑后的事实清单（统一冲突检测后入库，source=resume_upload）。

    generation：本次确认对应的抽取代次——防陈旧（用户对着旧卡片确认时，
    后台代次已递增，确认被拒）。
    """

    model_config = {"extra": "ignore"}

    generation: int = Field(..., ge=0)
    facts: list[FactCreate] = Field(default_factory=list)


class ExtractConfirmResponse(BaseModel):
    """确认入库结果。

    conflicts（甲方案，2026-08-13）：fuzzy 带真伪冲突**未写入**、待用户裁决的对
    ——前端据此在聊天里让 agent 反问用户（如「雅思 7.5 vs 4.5」）。
    """

    saved: int
    state: Literal["confirmed"] = "confirmed"
    conflicts: list[FactConflict] = Field(default_factory=list)


class ExtractRejectRequest(BaseModel):
    """拒绝抽取结果：该批不入库，状态回 idle、通道重开。"""

    model_config = {"extra": "ignore"}

    generation: int = Field(..., ge=0)


class ExtractRetryResponse(BaseModel):
    """重新抽取：代次 +1 作废旧抽取，对当前文档重抽。"""

    generation: int
    document_id: int


class ResumeParseResponse(BaseModel):
    """简历识别响应：文件名 + 抽取事实 + 覆盖率 + 解析后的 Markdown（供左栏兜底预览）。"""

    resume_name: str
    facts: list[ExtractedFact] = Field(default_factory=list)
    coverage: ParseCoverage = Field(default_factory=ParseCoverage)
    markdown: str = Field(default="", description="MarkItDown 解析出的 Markdown（DOCX/PPTX 等左栏预览兜底用）")
