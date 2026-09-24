"""简历文档（resume_documents）传输模型。

文档是"成品/范本"——三列并存（docs/adr/0004，2026-08-28）：
- markdown = 上传 v1 内容层（抽取输入 + 预览兜底）。
- resume_json = 生成版结构化真身（JSON-Resume + layout）。
- html = 生成版渲染快照（固定模板从 resume_json 渲染）。
源数据在 user_facts；文档是事实的一次投影（排版+措辞），不是事实真相源。
"""

from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.resume import Typography

# 文档来源：上传转出 / LLM 生成 / 回滚（`rollback` 为旧「纯追加回滚」遗留枚举值——
# 软作废回滚不再产生新行，保留此值以兼容历史数据）
DocumentSource = Literal["upload", "generated", "rollback"]


class ResumeDocument(BaseModel):
    """一版简历文档（读模型）。"""

    model_config = {"extra": "ignore"}

    id: int
    version: int
    markdown: str
    resume_json: str = ""  # 生成版结构化真身（上传 v1 为空）
    html: str = ""  # 生成版渲染快照（上传 v1 为空）
    summary: str = ""  # 一句话版本简述（Git 意味，S8 2026-08-14）；旧数据空串
    typography: Typography = Field(default_factory=Typography)  # 排版自由度配置（2026-09-02 四参数）：一体落库随版本走、进导出
    source: DocumentSource = "upload"
    original_name: str = ""
    original_ext: str = ""
    superseded: bool = False  # 软作废（2026-08-10：回滚跳过，数据保留 UI 不显示）
    created_at: str = ""


class ResumeDocumentCreate(BaseModel):
    """存一版文档（生成/修改时用）。"""

    model_config = {"extra": "ignore"}

    markdown: str = Field(default="", description="上传 v1 内容层（生成版空）")
    resume_json: str = Field(default="", description="生成版结构化真身（上传 v1 空）")
    html: str = ""  # 生成版渲染快照（可空 = 上传 v1 / 旧数据）
    summary: str = ""  # 一句话版本简述（S8）
    typography: Typography = Field(default_factory=Typography)  # 排版配置（2026-09-02）：新生成稿继承上一版（service 层传入）
    source: DocumentSource = "generated"


class ResumeUploadResponse(BaseModel):
    """上传响应：文档 v1 已就位（不阻塞等抽取），后台异步抽事实。"""

    document_id: int
    version: int
    markdown: str
    resume_name: str
    original_name: str = ""
    original_ext: str = ""


class ResumeTypographyUpdate(BaseModel):
    """排版自由度防抖回后端（2026-09-02）：改当前版排版四参数，后端重渲染 html 落库。"""

    model_config = {"extra": "ignore"}

    typography: Typography = Field(..., description="排版四参数（scale/lineHeight/spacing/letterSpacing）")


class ResumeVersionsResponse(BaseModel):
    """文档版本列表。"""

    versions: list[ResumeDocument] = Field(default_factory=list)


class ResumeCurrentResponse(BaseModel):
    """当前（最新）简历文档。无文档是正常空态 → document 为 null（2026-09-09 契约修订：「没有」是正常数据不是错误，不再用 404 表示空态——否则前端挂载恢复必打一条 console 404）。"""

    document: ResumeDocument | None = None


class RollbackRequest(BaseModel):
    """回滚请求：目标文档 id（唯一身份）。

    2026-08-12 身份锚定：按 **document_id**（id 唯一、永不复用），不按 version——
    version 是显示标签（软作废后可复用，同号可能一作废一当前两条），按 version 会撞错稿。
    2026-09-24：`include_facts` 开关已删——回滚 = **整份恢复该版开始时的工作台**
    （资料集 + 改动记录），不给用户一个会产生半吊子状态的选择。
    """

    document_id: int = Field(..., ge=1)


class RollbackResponse(BaseModel):
    """回滚结果。

    2026-08-10 软作废回滚：不复制新版本，直接回到目标稿（目标之后全标 superseded）。
    version = 目标稿号（当前 = 目标），下一个版本 = 目标+1（序号连续）。
    2026-09-24：`facts_restored` 改名 `workspace_restored`——恢复的是整个工作台
    （资料集 + 改动记录），不再只是事实。
    """

    version: int
    workspace_restored: bool


class ResumeResetRequest(BaseModel):
    """重置简历：清空文档流 + 快照；clear_facts=True 时连事实库一起清空。"""

    model_config = {"extra": "ignore"}

    clear_facts: bool = False


class ResumeResetResponse(BaseModel):
    """重置结果：清理行数统计。"""

    documents: int
    snapshots: int
    facts: int
    records: int = 0  # 改动记录（change_records）
    sessions: int = 0  # 重置连会话一起清（S1-6，2026-08-13）


class ResetAllResponse(BaseModel):
    """核爆结果（「重新开始」）：全量清理行数统计。

    比 ResumeResetResponse 多投递侧（jobs/followup_events）+ 偏好（preferences）——
    核爆连投递数据和判定偏好一起清，用户配置（LLM/语言）保留。
    """

    documents: int = 0
    snapshots: int = 0
    facts: int = 0
    records: int = 0
    preferences: int = 0
    followup_events: int = 0
    jobs: int = 0
    sessions: int = 0
