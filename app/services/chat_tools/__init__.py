"""deep agent 工具与装配（B3 拆分，2026-09-11）。

原 `app/services/chat_tools.py` 一个文件同时承担：`@tool` LLM 契约声明、参数归一化、
system prompt 装配、图装配注入——发散式修改。现按职责拆成包：

- `tools.py`：15 个 `@tool` 声明 + TOOLS 清单（LLM 契约，措辞不可随意改）。
- `_common.py`：参数归一化（非法值收口）+ 结果序列化等共用助手。
- `_assembly.py`：system prompt 重建 + 工具注入 graph。

本 `__init__` 汇总对外公共面（工具函数 + TOOLS + build_deep_system_prompt +
inject_into_graph），既有调用方 `chat_tools.<name>` 与 `from app.services.chat_tools
import <name>` 均不受拆分影响。
"""

from app.services.chat_tools._assembly import build_deep_system_prompt, inject_into_graph
from app.services.chat_tools.tools import (
    TOOLS,
    apply_suggestions_tool,
    commit_direction_tool,
    generate_resume_tool,
    propose_execution_tool,
    query_decisions_tool,
    query_market_tool,
    record_change_tool,
    record_decision_tool,
    record_facts_tool,
    refine_direction_tool,
    set_change_status_tool,
    set_fact_on_resume_tool,
    suggest_improvements_tool,
    supersede_fact_tool,
)

__all__ = [
    "TOOLS",
    "apply_suggestions_tool",
    "build_deep_system_prompt",
    "commit_direction_tool",
    "generate_resume_tool",
    "inject_into_graph",
    "propose_execution_tool",
    "query_decisions_tool",
    "query_market_tool",
    "record_change_tool",
    "record_decision_tool",
    "record_facts_tool",
    "refine_direction_tool",
    "set_change_status_tool",
    "set_fact_on_resume_tool",
    "suggest_improvements_tool",
    "supersede_fact_tool",
]
