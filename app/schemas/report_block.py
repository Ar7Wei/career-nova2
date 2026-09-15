"""分析报告的「块流」契约（ReportBlock）：文/图穿插叙事的稳定形状（2026-09-15 定稿）。

报告正文不再是「一个 body 字符串 + 尾部一摞 charts」，而是**有序的块序列**——analyze Agent
按叙述顺序产出 `text` / `chart` 块，前端按序渲染，图自然穿插在段落之间（讲一段→配一张→
再讲一段）。这比「正文 + 图集」更贴合「分维度逐段讲」的分析形态。

兜底红线不变：坏 `chart` 块（isChartSpec 不过）**单独丢弃、text 块照显**——模型配错图只少
一张图，绝不拖垮整段文字。双侧同构：本文件（Python）⇄ `frontend/src/types/report_block.ts`（TS）。
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.schemas.chart import ChartSpec


class TextBlock(BaseModel):
    """一段 Markdown 文字。"""

    kind: Literal["text"] = "text"
    md: str = ""


class ChartBlock(BaseModel):
    """一张图（内嵌 ChartSpec，走与独立配图同一份契约/校验）。"""

    kind: Literal["chart"] = "chart"
    spec: ChartSpec


# 判别联合：按 kind 分流。报告正文 = 这个块的有序列表（0~N 块，宁缺勿滥）。
ReportBlock = Annotated[TextBlock | ChartBlock, Field(discriminator="kind")]
