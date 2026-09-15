"""数据表模型：优化建议（1.2）。

- `preferences`：用户判定偏好——拒绝项 + 自定义标准（见 docs/design/resume.md §5、§12.5）。
  独立于事实库；优化建议被拒 = 记"拒掉这一类"（如 reject_quantify），同类不再建议。
- `optimization_pending`：悬浮改进卡片的落库——被接受的建议攒批，重启不丢（§12.3）。
  版本变更（生成/回滚/应用）时卡片必须结清（应用并清空），不会跨版本脱锚。
"""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class Preference(SQLModel, table=True):
    """一条用户判定偏好：拒绝项 / 自定义标准。"""

    __tablename__ = "preferences"

    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(default="reject", index=True)  # reject / custom
    scope: str = Field(default="", index=True)  # 偏好作用域（如 quantify），记"拒掉这一类"
    content: str = Field(default="")  # 偏好内容（如"后端成就不可量化，不提供量化"）
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class OptimizationPending(SQLModel, table=True):
    """一条优化建议（1.2 建议流落库，2026-08-10 从「仅已接受」升级为完整状态机）。

    建议从聊天暂存升级为落库对象：suggest_improvements 产出即落库 pending，
    面板三栏（待定/已确认/正在聊）直接读它，聊一聊/裁决更新状态，切页/刷新不丢。
    版本变更（生成/回滚/应用）时 confirmed 的建议应用并结清（§12.3），不跨稿脱锚。

    2026-08-20 大雷修复：结清从**物理删除**改为**软标记 + 全留存**——
    加终态 applied/archived + resolved_in_document_id（在哪个版本被应用/结清，NULL=活跃）。
    行永不删（reset 除外），溯源全留：哪版产生/哪版应用/改了哪几点可查，
    是 §11.8「已生成/已回滚」引导说「这版改了什么」的数据地基。
    """

    __tablename__ = "optimization_pending"

    id: int | None = Field(default=None, primary_key=True)
    session_id: int = Field(default=0, index=True)  # 提出建议的会话
    document_id: int = Field(default=0, index=True)  # 建议基于的文档 id（唯一身份；旧列名 document_version，init_db 迁移改名）
    type: str = Field(default="")  # quantify/word_choice/structure/fill_gap/job_relevance/highlight
    target: str = Field(default="")  # Markdown 锚点定位（如 "工作经历>腾讯>第2条"）
    original: str = Field(default="")
    suggested: str = Field(default="")
    reason: str = Field(default="")
    severity: str = Field(default="medium")  # high/medium/low
    # **proposed** 拟议 / pending 待定 / confirmed 已确认 / rejected 已拒绝 / discussing 正在聊（活跃）
    # applied 已应用进新稿 / archived 版本变更被结清未应用（终态，全留存）
    # proposed（2026-09-14，apply.md §11.7.3）：投递页分析产的「处方」初始态——**不进优化点面板**
    # （面板四栏只认 pending/confirmed/discussing/rejected），点「改进」才 proposed → pending。
    status: str = Field(default="pending", index=True)
    # 来源（2026-09-14，apply.md §11.7.8）：agent = 聊天 agent 提的（suggest_improvements）；
    # job_analysis = 投递页分析产的处方。分不清来源就分不清「这条该不该带『改进』按钮」。
    origin: str = Field(default="agent", index=True)
    resolved_in_document_id: int | None = Field(default=None, index=True)  # 在哪个版本被应用/结清；NULL=仍活跃
    split_from: int | None = Field(default=None, index=True)  # 分裂来源建议 id（分裂出的新条）
    # 拒绝原因（2026-09-14，apply.md §11.7.8）：`/optimization/reject` 一直收 reason、schema
    # 注释也写"记进偏好"，但 service 只 logger.info、从不落库——"能力不到"这类拒因丢了，
    # 而它是方向降级的证据来源（降级本身归 agent，本波只保证证据不丢）。
    reject_reason: str = Field(default="")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    # 状态最近变更时间（2026-09-14）：render_panel 的「← 你刚改的」标记靠它比对——agent 一轮
    # 开跑后哪些行被动过。旧实现无此列，动作对整个循环不可见（§11.7.8「状态可见、动作不可见」）。
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
