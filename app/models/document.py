"""数据表模型：简历文档（版本化 Markdown 内容层 + resume_json 结构化真身 + HTML 排版层）。

简历的"成品/范本"——v1 是上传文件经 MarkItDown 转出的 Markdown；
之后每生成/修改一版存一行（version 递增）。源数据在 user_facts，
本表只承载"排版 + 措辞 + 组装后的事实"，是事实的一次投影，不是事实真相源。

2026-08-28 排版转向（docs/adr/0004）：LLM 退出排版，固定模板确定性渲染。三列并存：
- `markdown` = 上传 v1 的内容层（MarkItDown 提取物）：后台抽取的输入 + 原件不可渲染时的预览兜底。
  生成版 markdown 为空（结构化 JSON 取代自由 Markdown）。
- `resume_json` = 生成版的结构化真身（JSON-Resume + layout，schema 见 app/schemas/resume.py）。
  上传 v1 为空（上传时无 LLM）。
- `html` = 生成版渲染快照（固定模板 render_resume 从 resume_json 渲染，纯函数输出）。
  上传 v1 无（原件预览兜底用 markdown）。
"""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class ResumeDocument(SQLModel, table=True):
    """一版简历文档。version 从 1 递增，回滚 = 切到目标版本的 markdown（+ html）。

    superseded（2026-08-10 软作废回滚）：回滚跳过的稿标 True（数据保留，
    UI 不显示），目标稿变当前，下一个版本 = 目标+1（序号连续不跳）。
    """

    __tablename__ = "resume_documents"

    id: int | None = Field(default=None, primary_key=True)
    version: int = Field(index=True)  # v1, v2, ...（可复用序号：软作废后从目标+1 继续）
    markdown: str = Field(default="")  # 上传 v1 内容层（MarkItDown 提取物，抽取输入 + 预览兜底）
    resume_json: str = Field(default="")  # 生成版结构化真身（JSON-Resume + layout；上传 v1 为空）
    html: str = Field(default="")  # 生成版渲染快照（固定模板从 resume_json 渲染的完整 HTML 文档）
    summary: str = Field(default="")  # 一句话版本简述（Git 意味，S8 2026-08-14）：LLM 产出时顺带吐，上传 v1 规则名「最初版本」
    scale: float = Field(default=1.0)  # 控件 A 字号阶梯（2026-09-02 落库挂版本）：排版松紧度，1.0=基准字号；上传 v1 无意义留 1.0
    typography: str = Field(default="")  # 排版自由度配置（2026-09-02 四参数收成一列）：JSON 字符串，承载 scale/lineHeight/spacing/letterSpacing；空=默认 Typography。读侧以它为准（scale 列是迁移期遗留）
    source: str = Field(default="upload")  # upload / generated（`rollback` 为旧纯追加回滚的遗留值，软作废回滚不再产生新行）
    original_name: str = Field(default="")  # 上传原件文件名（仅上传版有；软作废回滚不复制，作废稿保留其原件）
    original_ext: str = Field(default="")  # 上传原件扩展名（小写无点，驱动前端预览分档）
    superseded: bool = Field(default=False, index=True)  # 软作废（回滚跳过；数据保留 UI 不显示）
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
