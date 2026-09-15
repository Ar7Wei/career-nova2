"""简历识别图的状态模型。"""

from pydantic import BaseModel, Field

from app.schemas.facts import ExtractedFact, ParseCoverage


class ResumeParseState(BaseModel):
    """简历识别工作流状态：简历 Markdown 进，事实清单 + 覆盖率出。"""

    resume_markdown: str = Field(default="", description="MarkItDown 转出的简历 Markdown")
    facts: list[ExtractedFact] = Field(default_factory=list, description="抽取出的事实清单")
    coverage: ParseCoverage = Field(default_factory=ParseCoverage, description="覆盖率自述")
