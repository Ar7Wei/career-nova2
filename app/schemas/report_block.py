"""分析报告的「块流」契约（ReportBlock）：文/图/小节穿插叙事的稳定形状（2026-09-15 定稿）。

报告正文不再是「一个 body 字符串 + 尾部一摞 charts」，而是**有序的块序列**——analyze Agent
按叙述顺序产出 `section`（小节标题）/ `text` / `chart` 块，前端按序渲染，图自然穿插在段落之间
（讲一段→配一张→再讲一段）。这比「正文 + 图集」更贴合「分维度逐段讲」的分析形态。

**`section` 块（2026-10-11 补）**：报告脊柱（判词 → 门槛 → 你的牌/风险 → 提案）此前只有
prompt 约定、UI 上无形体，四段糊成一坨流式 markdown。加 `section` 块让 Agent 在每个脊柱段
开头放一个主题词标题（「门槛」「你的牌 / 风险」…），前端渲染成分节、把信息层级立起来。
判词仍走 `headline`（不进块流），只是主题词**不是序号**。

兜底红线不变：坏块（chart spec 不过 / section 空标题）**单独丢弃、text 块照显**。双侧同构：
本文件（Python）⇄ `frontend/src/types/report_block.ts`（TS），改一边必须改另一边。
"""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.schemas.chart import ChartSpec


class SectionBlock(BaseModel):
    """一个小节标题（脊柱主题词，如「门槛」「你的牌 / 风险」）——把报告分层。"""

    kind: Literal["section"] = "section"
    title: str = ""


class TextBlock(BaseModel):
    """一段 Markdown 文字。"""

    kind: Literal["text"] = "text"
    md: str = ""


class ChartBlock(BaseModel):
    """一张图（内嵌 ChartSpec，走与独立配图同一份契约/校验）。"""

    kind: Literal["chart"] = "chart"
    spec: ChartSpec


# 判别联合：按 kind 分流。报告正文 = 这个块的有序列表（0~N 块，宁缺勿滥）。
ReportBlock = Annotated[SectionBlock | TextBlock | ChartBlock, Field(discriminator="kind")]
