"""抓取进度（crawl_state）传输模型。

API 契约：crawl_state 对 Electron（前程无忧 DOM 一轮）暴露「读游标 + 报结果」两个
面。猎聘一轮在 kick 端点内部自循环、自记账，不经过 CrawlStateReport（那是 Electron
前程无忧专用）。

游标推进规则统一在后端（decision 3/12）：Electron 报「一轮停在哪个组/页 + 什么态 +
为何停」，后端据 report 推进游标 + 标三态。同样的规则被 kick 的猎聘自循环内联复用。
"""

from typing import Literal

from pydantic import BaseModel, Field

# 参与抓取的平台（BOSS 已彻底砍，2026-08-31；只留猎聘 + 前程无忧）
CrawlSource = Literal["liepin", "job51"]
# 单平台一轮的三态（fresh 有新增 / throttled 被限流 / exhausted 空页到底）
CrawlStateKind = Literal["fresh", "throttled", "exhausted"]
# fresh 的停因：quota 凑满（游标继续前进）/ cap 撞页数上限（游标回第 1 页）
CrawlStopReason = Literal["quota", "cap"]
# kick 门控结果（前端指示器信号）：crawled = 至少一家在爬；其余 = 这一脚整体被门控拒的原因。
# throttled / exhausted 都表「受限」（黄点）——细分给 tooltip：throttled = 冷却期未过（等 15 分钟），
# exhausted = 当天已爬完（明天自动恢复）。混合时 throttled 优先（更即时可行动）。
CrawlGateReason = Literal["crawled", "apply_mode_off", "no_direction", "backlog_full", "throttled", "exhausted"]


class CrawlGroupState(BaseModel):
    """一个查询组的抓取三态（游标级的组级标记，随 CrawlCursor 一起存 JSON 列）。

    exhausted_date：该组已爬到底的日期（当天跳过，转天回第 1 页重查）。
    throttled_at：该组最近一次被限流的时间戳（冷却期内跳过）。
    """

    model_config = {"extra": "ignore"}

    exhausted_date: str | None = None
    throttled_at: str | None = None


class CrawlCursor(BaseModel):
    """某平台的抓取游标 + 组级三态（repository 读回的定型结果，取代裸 dict）。

    group_idx/page：平台原生游标（页号语义由 PAGE_ORIGIN 定义）。
    groups：各查询组的三态，长度由 service 层按方向组数对齐。
    """

    model_config = {"extra": "ignore"}

    group_idx: int = 0
    page: int = 0
    groups: list[CrawlGroupState] = Field(default_factory=list)


class CrawlStateReport(BaseModel):
    """一轮抓取结果上报（Electron 前程无忧一轮 → 后端记账）。

    group_idx/page：一轮停在哪组、哪页（平台原生页号）。
    kind：本轮三态。reason：kind=fresh 时停因（quota/cap）。
    限流信号（ok=false/bodyLen=0）在 Electron 产生，归 kind=throttled。
    """

    model_config = {"extra": "ignore"}

    source: CrawlSource
    kind: CrawlStateKind
    group_idx: int = 0
    page: int = 0
    added: int = 0
    updated: int = 0
    reason: CrawlStopReason | None = None


class CrawlStateResult(BaseModel):
    """一轮记账后的结果（游标推进到哪 / 是否整家 exhausted）。"""

    source: CrawlSource
    kind: CrawlStateKind
    group_idx: int = 0
    page: int = 0
    added: int = 0
    updated: int = 0
    exhausted: bool = False  # 整家所有组都到底（派生，非存储）
    round_id: int | None = None  # 本轮抓取轮次 id（job51 只读建议带出，Electron 据此标归属）


class KickResponse(BaseModel):
    """POST /jobs/crawl/kick 响应。

    liepin：后端自循环一轮的结果（已在锁内推进游标 + 标三态 + 入库）；None = 门控拒了。
    job51：只读建议——should_crawl 决定 Electron 要不要跑 DOM 一轮；group_idx/page 是
    前程无忧当前游标（Electron 从这开始）。data_epoch 当前程无忧一轮的核爆检查点基线。
    job51_quota / cap：前程无忧一轮的参数（凑满 quota 停 / 最多扫 cap 页）——直接带出，
    省掉 Electron 多读一次 settings。
    gate_reason：这一脚整体的门控结果（前端指示器信号）。crawled = 至少一家在爬；
    其余 = 门控拒的原因（apply_mode_off / no_direction / backlog_full /
    throttled / exhausted）。门控拒时 liepin/job51 都 None，靠它区分「受限」与
    「backlog 满」，并在受限里细分「冷却期（throttled）」vs「当天爬完（exhausted）」。
    """

    model_config = {"extra": "ignore"}

    liepin: CrawlStateResult | None = None
    job51: CrawlStateResult | None = None
    data_epoch: int = 0
    job51_quota: int = 20
    cap: int = 10
    gate_reason: CrawlGateReason = "crawled"
    # 本轮 id（2026-09-14，apply.md §11.7）：一次 kick 一个、两平台共用。前端在 kickCrawl
    # 尾部拿它调 `GET /analysis/unprocessed?round_id=`——分析输入 = 本轮那批岗位。
    round_id: int | None = None


class CrawlStateRead(BaseModel):
    """GET /jobs/crawl-state 读某平台游标（Electron 前程无忧一轮的起点）。"""

    source: CrawlSource
    group_idx: int = 0
    page: int = 0
    group_count: int = 0  # 方向查询组数（groups 数组长度）
    exhausted: bool = False  # 整家所有组都到底（当天跳过）
