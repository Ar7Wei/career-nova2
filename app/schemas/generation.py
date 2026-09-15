"""简历生成（1.3）传输模型。

文档任务整份生成：产出暂存预览态（不写库）→ 用户确认才写库 vN（版本变更 = 新 session）。
不满意重新生成 = 覆盖暂存，不堆垃圾版本（§13.5）。

2026-08-28 结构化转向（docs/adr/0004）：生成产出结构化 resume JSON + 渲染快照 HTML。
页数不再由闭环收敛（scale 阶梯 + A4 预览人工调），故去掉 pages 参数。
"""

from pydantic import BaseModel, Field


class GenerateRequest(BaseModel):
    """整份生成请求。

    target_role：目标岗位（可空 = 通用版，存 user_facts basic 类）。
    apply_confirmed：真 = 「开始改」——把已确认建议渲染成文本并进图（ADR 0015）。
    """

    model_config = {"extra": "ignore"}

    target_role: str | None = Field(default=None, description="目标岗位（可空 = 通用版）")
    apply_confirmed: bool = Field(default=False, description="真 = 应用已确认建议（「开始改」）")


class GeneratePreviewResponse(BaseModel):
    """生成预览：暂存态，未写库。json_text（结构化真身）+ html（渲染快照）双份。"""

    markdown: str = Field(default="", description="结构化真身 JSON 字符串（字段名沿用旧契约，实际是 JSON）")
    html: str = ""  # 渲染快照（固定模板渲染；预览直接显示 HTML）


class GenerateConfirmResponse(BaseModel):
    """确认生成：写库 vN（confirm）；revise 回图重改时不写库、不返回版本。"""

    version: int | None = Field(default=None, description="confirm 落库的新版本号；revise 时为 None（不写库）")


class GenerateConfirmRequest(BaseModel):
    """人门拍板请求（ADR 0012 第 3 块）：confirm 落库 / revise 带 feedback 回图重改。"""

    model_config = {"extra": "ignore"}

    decision: str = Field(default="confirm", description="confirm|revise")
    feedback: str = Field(default="", description="decision=revise 时的修改意见（喂回 content 重改）")
