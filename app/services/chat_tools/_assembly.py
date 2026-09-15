"""deep agent 的装配：system prompt 重建 + 工具注入 graph（ADR 0016，B3 拆分）。

与工具声明分开：这里只有"把领域数据拼成 prompt"和"把工具塞进 graph 占位"两件事，
不定义工具本身。读 DB 是 service 的活，graph 层 middleware 只调这里的函数。
"""

from datetime import UTC, datetime

from app.graphs import chat as chat_graph
from app.prompts import load_resume_agent_deep_prompt, preview_pending_hint
from app.repositories.documents import latest_document
from app.repositories.facts import list_facts
from app.services.chat_tools.tools import TOOLS
from app.services.facts import compute_info_gaps, render_info_gaps
from app.services.optimization import get_preferences, pending_suggestions, render_panel
from app.services.rewrite import get_preview
from app.utils.facts import flatten_facts
from app.utils.resume_text import resume_prompt_text

# 本轮 agent 开跑的时间点快照（2026-09-14，apply.md §11.7.8）。render_panel 的
# 「← 你刚改的」靠它比对：`updated_at > _panel_changed_since` 的行 = 本轮被动过。
# 单用户单在途对话，一个模块级变量即可（与 chat.py 的 _current_cancel_event 同型）。
# begin_turn() 在每轮跑 agent 前记一次；None 时 render_panel 不标任何行。
_panel_changed_since: datetime | None = None


def begin_turn() -> None:
    """记下本轮开跑时间点（每轮跑 agent 前调一次）。render_panel 据此标「刚改的」行。"""
    global _panel_changed_since
    _panel_changed_since = datetime.now(UTC)


def _reset_panel_snapshot_for_tests() -> None:
    """测试用：清空时间点快照（免跨测试误标变化行）。"""
    global _panel_changed_since
    _panel_changed_since = None


async def build_deep_system_prompt() -> str:
    """Deep agent 每轮 system prompt：读 facts/偏好/简历/面板四栏 → 拼瘦身版 base prompt。

    这是「上下文注入」的 service 层实现（读 DB 是 service 的活，graph 层 middleware 只调它）。
    用 load_resume_agent_deep_prompt（瘦身版，四块规则已拆进 skill，prompt 里只留指向），
    并复用 optimization.render_panel 渲染面板四栏（含变化标记 + 门控，不重复造）。
    """
    facts = await list_facts(status="active")
    # 嵌套展平：条目一行 + 各要点缩进行（无 points 只有条目行）——共用 utils 的展平口径。
    facts_text = flatten_facts(facts, bullet_title=True)
    resume = await latest_document()
    prefs_text = await get_preferences()
    panel = await pending_suggestions()
    # changed_since = 本轮开跑时间点（begin_turn 记的）：标出本轮 agent 自己动过 / 用户在
    # 面板上操作过的行，免得 agent 把动作误当"一开始就在这儿"。
    panel_text = render_panel(panel, changed_since=_panel_changed_since)
    gaps_text = render_info_gaps(compute_info_gaps(facts))
    prompt = load_resume_agent_deep_prompt(
        facts=facts_text,
        preferences=prefs_text,
        resume_document=resume_prompt_text(resume),
        pending_suggestions=panel_text,
        info_gaps=gaps_text,
    )
    # 暂存预览提示（与 legacy 同构）：有待确认的生成预览时注入引导（文本归 app/prompts/）。
    preview = await get_preview()
    if preview.strip():
        prompt += "\n\n" + preview_pending_hint()
    return prompt


def inject_into_graph() -> None:
    """把业务工具 + 重建 prompt 函数注入 graph 层占位（幂等，service → graph 单向）。

    deep agent 首次跑前调用（见 services/chat.py 的 _run_agent_impl）。graph 不 import
    业务 service，这里把真工具与重建函数赋给 graph 的模块级占位。

    注：本模块顶层 `from app.graphs import chat as chat_graph` 是安全的——graphs.chat
    不 import 本层（只在 middleware 里惰性取 REBUILD_SYSTEM_PROMPT 占位），故不构成环。
    """
    chat_graph.TOOLS = TOOLS
    chat_graph.REBUILD_SYSTEM_PROMPT = build_deep_system_prompt
