"""图表契约（ChartSpec）：投递页分析「自由配图」的稳定形状（2026-09-15 方向定稿）。

analyze Agent 在产出分析叙事的同时，**可选**地配 0~2 张图辅助说明——图是它的自由产出，
但形状必须收敛到这份契约，前端才敢渲染（配错图前端另有一道运行时降级，本层是「契约尽量
不让坏 spec 出门」）。

约束理由：
- **6 种图型**（bar/line/area/donut/pie/radar）：demo 验证过的分析构图全集，覆盖
  分布/对比/占比/趋势/多维画像。桑基/热力/漏斗等对数据形状苛刻，模型自由发挥易配废，不开放。
- **series ≤ 2**：「市场 vs 你」这种二元对比是上限，更多列就是花图。
- **x / series.key 必须在 data 行里真实存在**：防 LLM 编出对不上的列名（契约先拦一道）。

双侧同构：本文件（Python）与 `frontend/src/types/chart.ts`（TS）字段一一对应，改一边必须改另一边。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

# 开放的 6 种图型（前端 @mantine/charts 渲染器一一对应）。
ChartType = Literal["bar", "line", "area", "donut", "pie", "radar"]


class ChartSeries(BaseModel):
    """一个数值列：key 指向 data 行的字段名；label/color 展示用（可空，前端给默认）。"""

    key: str
    label: str = ""
    color: str = ""


class ChartSpec(BaseModel):
    """一份声明式图表描述：analyze Agent 产出，前端纯渲染（不附带判断逻辑）。"""

    type: ChartType
    title: str = ""
    data: list[dict[str, Any]] = Field(min_length=1)
    x: str  # 类目/时间轴字段名
    series: list[ChartSeries] = Field(min_length=1, max_length=2)
    # bar 专用：horizontal=纵向柱（类目在 X）/ vertical=横向条（中文长标签好排）。
    orientation: Literal["horizontal", "vertical"] = "horizontal"
    note: str = ""  # 口径标注（如「仅猎聘样本」），诚实标注数据来源

    @model_validator(mode="after")
    def _keys_exist_in_data(self) -> "ChartSpec":
        """X 与每个 series.key 都必须能在 data 行里找到——否则图根本画不出来，属配错。"""
        keys = {self.x, *(s.key for s in self.series)}
        if not all(any(k in row for row in self.data) for k in keys):
            raise ValueError(f"chart spec 引用了 data 里不存在的字段：{sorted(keys)}")
        return self
