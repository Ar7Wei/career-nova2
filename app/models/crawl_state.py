"""数据表模型：抓取进度（crawl_state，apply.md §8 / ADR 0008）。

续页游标 + 单平台三态的**唯一真相源**，统一存后端 SQLite——猎聘（后端 HTTP）与
前程无忧（Electron 读 DOM）两边只报结果，后端统一记账推进游标 / 标三态（限流信号
在哪产生就在哪归类）。

- 每平台一行（`source_name` 主键）。
- 游标二维 `(group_idx, page)`：`group_idx` = 当前查询组下标（0 起，组间顺序消费），
  `page` = 平台**原生页号**（猎聘 currentPage 0 起、前程无忧 pageNum 1 起）。后端
  不碰平台翻页语义，只存「下次请求哪个页号」。
- `groups` = JSON 数组，长度 = 方向查询组数，每项是该组的**组级三态**：
  `exhausted_date`（空页打日期标记，当天跳过、转天重查）/ `throttled_at`（限流失败
  时间戳，惰性冷却比对，默认 15 分钟）。组 1 空页 = 组 1 exhausted、游标移到组 2；
  所有组都到底才整家 exhausted。
- 无 phase 字段（深爬/稳态是同一循环的自然现象，无阶段存储）。
- 改方向 / 核爆 → 游标归零（reset/clear）。
"""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class CrawlState(SQLModel, table=True):
    """一行 = 一个平台的抓取进度（游标 + 组级三态）。"""

    __tablename__ = "crawl_state"

    source_name: str = Field(primary_key=True)  # 平台：liepin / job51
    group_idx: int = Field(default=0)  # 当前查询组下标（0 起）
    page: int = Field(default=0)  # 平台原生页号（猎聘 0 起 / 前程无忧 1 起）
    groups: str = Field(default="[]")  # JSON：list[{exhausted_date, throttled_at}]
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
