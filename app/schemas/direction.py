"""投递方向（阶段二 v2）传输模型。

方向 = 派生事实（存 user_facts，不建独立表，apply.md §11.2）：
- 单值槽位 fact：category=basic、title="目标岗位：{role}"，**一个查询组一条 point**：组词与
  城市同条存（如 "Vue 前端 | 杭州"），组词与城市一一对应、平行等长。role 仍住 title。
- 目标城市**没有独立槽位**——城市挂在查询组上（2026-09-13，ADR 0019），是这个 fact 的一部分。

city 挂到查询组上（2026-09-13 grill 定稿，ADR 0019）——城市是**每个查询组的属性**，不是方向级
单值：`["Java","后端"]` 配成都、`["产品"]` 配杭州各搜各的。抓取的天然单元本就是「一组词 + 一个
城市」，故探针数 / 三态 / 游标维度全不变（不顶 ADR 0009 封顶）。**逐组必填、无「全国」**
（前程无忧留空 = 回落 IP 城市，语义是坏的）；`cities` 与 `keywords` **平行等长**，落定前双校验
（长度 + 逐项 25 城白名单）。

role 与 keywords 是两份独立数据（2026-08-21 定稿）：
- **role（目标岗位）**：人话标签，给简历生成（target_role）+ 方向标签展示用，**不参与搜索**。
  独立字段、独立编辑、独立存储（fact title），可被多套 keywords 复用。
- **keywords（查询组）**：二维 `list[list[str]]`，**组内多词、组间 OR**，只管搜索。
  组内多词的真实语义是**自适应 AND**（交错时 AND、交集不足时平台静默放宽成 OR，ADR 0018），
  写法（一组 = 概念 + 大范围共现的收窄词、同义变体只留最强的一个）见 direction 技能。
  与 role 内容可重复，但互不派生、不混用。

「城市不定」（用户还没想清去哪）不靠结构、靠 agent：调多次 `query_market` 逐城比，或复制一组
表达多城（见 direction 技能 + ADR 0019「补充」）。

改方向 = 两次确认（apply.md §11.2bis/ter）：确认在提交前发生（agent 问 / 前端弹），
本层只提供 refine（提炼提案）+ commit（拍板写回）+ get（读当前方向）。
分层：Router → Service → Repository；本层编排 facts/jobs repository + LLM，抛 AppError。
"""

from pydantic import BaseModel, Field

# 方向查询组（keywords）组数上限（ADR 0009，2026-09-01）：爬虫把每一组当一轮独立分页抓取，
# 组数直接决定单次全量抓取的请求总量（组数 × 2 站 × N 页），故封顶防抓取循环失控膨胀。
# commit_direction（落定方向）与 query_market（摸底查询）共用此上限；超限 service 层抛
# ConflictError 出声，不静默截断（静默砍组会让方向失真）。写死不做设置项——本地单用户，别过剩。
MAX_KEYWORD_GROUPS = 6


class Direction(BaseModel):
    """一个方向（岗位标签 + 查询组 + 逐组城市）。role 与 keywords 独立，互不派生。

    cities 与 keywords **平行等长**（`cities[i]` 是第 i 组的城市）——见模块 docstring。
    """

    role: str = ""
    keywords: list[list[str]] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)


class DirectionRefineResponse(BaseModel):
    """提炼方向结果（refine_direction 工具口头转述用）。

    2026-08-25：删 is_new_direction / unprocessed_count——岗位处置判定从「颠覆 vs 微调」
    简化为「有没有未处理岗位」，由 commit 后的 pending_disposal_count 统一表达，不再由
    refine 判定连续性（apply.md §11.2ter 修订）。
    """

    current: Direction | None = None  # 当前已存方向（无则 None）
    proposed: Direction | None = None  # LLM 提炼出的方向（无 facts 可提炼则 None）


class DirectionCommitRequest(BaseModel):
    """改方向定稿（人拍板后）。role 与 keywords/cities 独立入参。

    2026-08-25：删 dispose_unprocessed——commit 只写方向；岗位处置是独立动作
    （POST /direction/dispose-unprocessed），由后端检测未处理岗位数后前端就地弹窗。
    2026-09-13（ADR 0019）：city → cities，与 keywords 平行等长、逐组必填。
    """

    role: str = ""
    keywords: list[list[str]] = Field(..., min_length=1)
    cities: list[str] = Field(default_factory=list)


class DirectionCommitResponse(BaseModel):
    """改方向结果。role 与 keywords/cities 独立返回。

    2026-08-25：deleted_jobs → pending_disposal_count——commit 不再删岗位，只返回
    待处置（未处理）岗位数，前端据此决定要不要弹「留/删」确认。
    """

    role: str
    keywords: list[list[str]] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
    pending_disposal_count: int = 0  # 落定方向后仍未处理的岗位数（>0 前端就地弹处置确认）


class DirectionDisposeResponse(BaseModel):
    """岗位处置结果（POST /direction/dispose-unprocessed）。"""

    deleted_jobs: int = 0  # 删掉的未处理岗位数（keep 无操作，前端不调此端点）


class CitiesResponse(BaseModel):
    """可选城市库（有序 25 城，前端城市下拉数据源）。"""

    cities: list[str] = Field(default_factory=list)


# ---- LLM 结构化输出（refine 内部用，非 API 响应）----


class DirectionRefineOutput(BaseModel):
    """LLM 从 facts 提炼方向的结构化输出。role 与 keywords 独立。

    2026-08-25：删 is_new_direction（岗位处置判定已简化为 commit 后的未处理岗位数）。
    2026-09-13（ADR 0019）：city → cities——与 keywords 平行等长；**允许空串**（LLM 没把握
    就留空，绝不编造城市），commit 时才逐组必填。
    """

    role: str = ""
    keywords: list[list[str]] = Field(default_factory=list)
    cities: list[str] = Field(default_factory=list)
