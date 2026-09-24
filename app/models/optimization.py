"""数据表模型：优化点（1.2）。

- `change_records`：**唯一活表**（2026-09-23 起）——优化点 = 改动记录，`reason`（为什么）
  + `changes`（改什么，复合子项，各自带 status）。聊天 agent、投递页处方、面板都读写它。
- `optimization_pending`：**停用，待 DROP**——旧建议流（逐条一行）的落库表。行留存不删，
  仅供「这版改了什么」溯源回查；新代码不得再写。
- `preferences`：**停用，待 DROP**——旧「拒掉这一类」偏好（含 kind=custom 自定义标准）。
  随优化点合并进 change_records 而废弃（见 docs/design/resume.md §5、§12.5）。
"""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


class ChangeRecord(SQLModel, table=True):
    """一条「改动记录」：原因（why）+ 改动点（what，复合子项）。

    2026-09-23 起取代 optimization_pending + preferences(kind=custom)——优化点与
    用户决策记录合一为一张表（照 UserFact 的 title + points 复合形状）。

    - reason：改动原因（为什么），记录主体，一行一份、不重复（同原因的新改动 append 子项）。
    - changes：改动点（改什么），JSON 字符串列，子项
      `{id, target, original, suggested, status, type, severity}`；
      **可空数组**（原因先行、子项后补）。子项 **id 在记录内唯一**、**自带 status**——操作粒度 = 单条子项。
      `type`/`severity` 供面板展示（改哪类、多要紧）。
    - status：记录级兜底状态（子项全空时的粗状态、整条存废）。
    - kind：`change`（本轮整改，用完即丢）/ `decision`（跨版本持续生效的决策记录）。
    """

    __tablename__ = "change_records"

    id: int | None = Field(default=None, primary_key=True)
    reason: str = Field(default="")  # 改动原因（为什么）——记录主体
    changes: str = Field(default="[]")  # 改动点 JSON 列：[{id,target,original,suggested,status,type,severity}]
    status: str = Field(default="pending", index=True)  # 记录级兜底/存废：pending/discussing/.../archived
    kind: str = Field(default="change", index=True)  # change（本轮整改）/ decision（持续决策记录）
    origin: str = Field(default="agent", index=True)  # agent / job_analysis
    session_id: int = Field(default=0, index=True)  # 提出会话
    document_id: int = Field(default=0, index=True)  # 基于哪版
    resolved_in_document_id: int | None = Field(default=None, index=True)  # 哪版结清；NULL=活跃
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class Preference(SQLModel, table=True):
    """一条用户判定偏好：拒绝项 / 自定义标准。**停用，待 DROP**（2026-09-23）。"""

    __tablename__ = "preferences"

    id: int | None = Field(default=None, primary_key=True)
    kind: str = Field(default="reject", index=True)  # reject / custom
    scope: str = Field(default="", index=True)  # 偏好作用域（如 quantify），记"拒掉这一类"
    content: str = Field(default="")  # 偏好内容（如"后端成就不可量化，不提供量化"）
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class OptimizationPending(SQLModel, table=True):
    """一条优化建议（1.2 建议流落库，2026-08-10 从「仅已接受」升级为完整状态机）。

    **2026-09-23 停用，待 DROP**：优化点已统一到 `change_records`（原因 + 改动点复合表）。
    本表行留存不删，仅供历史溯源；新代码不得再写。旧语义留档如下。

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
