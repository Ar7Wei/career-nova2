"""市场快照（query_market 工具的产物，ADR 0007 / 0018）传输模型。

query_market = 简历 agent 现场查猎聘市场的工具。返回**原始统计、不做判断**——
判断（你 vs 市场缺什么）由 agent 拿统计比对已注入 facts 自己讲出。
样本仅猎聘，source_note 诚实标注。

ADR 0018：另附每组**召回诊断**（原始计数）——平台响应不暴露命中总数，靠它把
「这组词实际召回什么」交回 agent，让它自己读「AND 没生效 / 词召回太窄」。

**工具不替 agent 做策略**（ADR 0007）：比城市靠 agent 自己「一城一调」逐次查、拿多份快照比——
不提供「一次传多城、工具帮你横向对比」的结构。唯一额外吐的原始字段是 `est_totals`
（猎聘 `pagination.totalCounts`）——它无法从岗位列表推出来，不吐就永久丢失。
"""

from pydantic import BaseModel, Field


class DistributionSlice(BaseModel):
    """一个维度的分布切片：值 + 数量（按 count 降序）。"""

    label: str
    count: int


class GroupDiagnostic(BaseModel):
    """一组查询词的召回诊断——**原始计数，不下结论**（ADR 0018）。

    平台对多词是「自适应 AND」：交集够大时按 AND，交集不足时静默放宽成 OR
    （ADR 0018 实测）。但响应不暴露命中总数（猎聘恒 42 条/页），岗位量本身没有
    判别力——所以把「这组词实际召回了什么」以原始计数交回给 agent 自己读。

    计数口径：按**标题**做子串命中（大小写不敏感）。是代理指标而非精确命中
    （岗位可能标题不含、JD 含），但足以让 agent 看出「AND 没生效 / 词召回太窄」。
    """

    group: list[str]  # 该查询组（词列表）
    total: int  # 该组现场召回条数（未去重）
    per_word: dict[str, int]  # 每个词在标题里命中的条数
    all_hit: int  # 组内全部词同时命中的条数（≈ total 说明 AND 生效）
    sample_titles: list[str] = Field(default_factory=list)  # 前几条标题（agent 看噪音性质）


class ProbeEstimate(BaseModel):
    """一个 (查询组, 城市) 探针的**岗位规模估值**（猎聘 `pagination.totalCounts` 原始字段）。

    **真计数、但封顶在 ~800**（2026-09-13 实测，见 ADR 0020）：`昆明 224 / 大连 349 /
    济南 675` 这类**低值可信**（说明这小市场岗少）；大城市全压在 800 附近、**互相不可比**。
    无法从岗位列表推出来（列表恒 42 条/页），故单独吐——**怎么用归 agent**。
    """

    group: list[str]  # 该探针的查询组（词列表）
    city: str  # 该探针的城市（空 = 未指定/全国）
    est_total: int | None = None  # 岗位规模估值（封顶数，低值可信、高值不可比）


class MarketSnapshot(BaseModel):
    """市场快照：现场查猎聘后聚合出的客观统计。

    只吐数字事实，不含任何「你该怎么着」的建议。现场查即弃，不落库、不缓存。
    """

    role: str = ""  # 目标岗位标签（仅标注，不参与查询）
    cities: list[str] = Field(default_factory=list)  # 逐组查询城市（与查询组平行，ADR 0019）；空 = 未指定
    total: int = 0  # 去重后的岗位量
    by_city: list[DistributionSlice] = Field(default_factory=list)  # 城市分布（岗位自身 dq，真数据）
    by_degree: list[DistributionSlice] = Field(default_factory=list)  # 学历分布
    by_experience: list[DistributionSlice] = Field(default_factory=list)  # 年限分布
    salary_buckets: list[DistributionSlice] = Field(default_factory=list)  # 薪资段分布
    diagnostics: list[GroupDiagnostic] = Field(default_factory=list)  # 每组召回诊断（ADR 0018）
    est_totals: list[ProbeEstimate] = Field(default_factory=list)  # 逐探针岗位规模估值（原始字段）
    source_note: str = ""  # 诚实标注：仅猎聘样本

