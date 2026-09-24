"""对话 deep agent 图：组装 create_deep_agent（前台接待员，ADR 0016）。

本模块把对话的工具循环**搬进 LangGraph 图**（deepagents 编译产物），符合「工具循环住图」
的铁律。业务工具与 prompt 重建函数由 service 层注入（graph 不 import 业务 service）。

关闭 deepagents 默认能力（本场景用不上且有害）：
- **filesystem 工具**：只留 `read_file`（SkillsMiddleware 渐进披露要靠它读 SKILL.md 全文），
  关掉 ls/write/edit/delete/glob/grep/execute——简历聊天不碰盘，写盘工具是纯污染。
- **task 工具（subagent）**：关掉默认 general-purpose 子 agent（前台接待员 = 单 agent，
  subagent 留未来门，ADR 0016）。经 `register_harness_profile("openai", ...)` 关——
  provider 键匹配真实 ChatOpenAI 实例（provider='openai'，见 spike）。

checkpointer：`AsyncSqliteSaver`（复用 graphs/checkpoint.py 基建），thread_id=chat:{session_id}
——agent 工作记忆（恢复上下文 + 折叠）。UI 历史与对话原文的真相源是 `chat_messages` 表
（ADR 0016 双投影：表存 UI 历史、图存 agent 记忆；撤回时两侧同删最后一条 user 消息）。
未装配（:memory: 测试）时退回无 checkpointer（内存跑）。

三件装备：TodoListMiddleware（opt-in）+ SkillsMiddleware（skills= 参数自动加）+
SummarizationMiddleware（默认自带）。skill 文件在 `app/skills/`（真文件 + FilesystemBackend
只读限定到该目录，不碰用户数据/密钥）。
"""

from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from langchain.agents.middleware import AgentMiddleware, TodoListMiddleware
from langchain_core.messages import AIMessage, RemoveMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

from app.core.logging import logger
from app.graphs.checkpoint import get_checkpointer, thread_id
from app.services.llm import LLMRegistry
from app.utils.graph import extract_text_content

# 业务工具由 service 层注入（graph 不 import 业务 service，防「单向向下」铁律违例）。
# 与 nodes/rewrite.py 的 generate_json_from_facts 注入同构：service 装配时把真工具
# 赋到这里，graph 组装时读走。未注入即组装 = 装配遗漏（RuntimeError）。
TOOLS: Sequence[BaseTool] | None = None

# 重建 system prompt 的函数由 service 层注入（读 facts/偏好/简历/面板 = 读 DB，graph 层
# 不碰 DB）。middleware 每轮 LLM 调用前调它重建 system prompt（面板四栏最新，§12.6 D10）。
# 未注入 → middleware 跳过重建（system prompt 保持组装时的空占位）。
REBUILD_SYSTEM_PROMPT: Callable[[], Awaitable[str]] | None = None

# skill 目录绝对路径（app/skills/，含 grill/collect/optimize/direction 四个 SKILL.md）。
_SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


class _PromptRebuildMiddleware(AgentMiddleware):
    """每轮 LLM 调用前重建 system prompt（面板四栏最新，§12.6 D10 档 0）。

    瞬态改 request.system_message，不写 checkpoint（与 before_model 的持久化语义区分——
    见 ADR 0016）。重建函数 REBUILD_SYSTEM_PROMPT 由 service 注入（读 DB 是 service 的活），
    本 middleware 只调注入的函数，不 import 业务 service、不碰 DB。
    """

    async def awrap_model_call(self, request, handler):
        if REBUILD_SYSTEM_PROMPT is not None:
            prompt = await REBUILD_SYSTEM_PROMPT()
            request = request.override(system_message=SystemMessage(content=prompt))
        return await handler(request)


def _register_profile() -> None:
    """注册 harness profile：关掉默认 general-purpose 子 agent（进而关掉 task 工具）。

    幂等：deepagents 的 register_harness_profile 对同名 key 是 merge，重复注册无害。
    惰性在首次组装时执行（不 import 即注册副作用，避免模块 import 时污染全局）。
    """
    from deepagents import GeneralPurposeSubagentProfile, HarnessProfile, register_harness_profile
    from deepagents.middleware import FilesystemMiddleware  # noqa: F401  # 确保 deepagents 中间件可 import

    register_harness_profile(
        "openai",
        HarnessProfile(general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)),
    )


_graph: CompiledStateGraph | None = None
_impl_model_name: str | None = None


def get_chat_graph(force: bool = False) -> CompiledStateGraph:
    """返回编译好的对话 deep agent 图（惰性单例）。

    force=True 重建（模型名/base_url/key 变更后调用，配合 LLMRegistry.reset()）。
    """
    global _graph, _impl_model_name
    current_model = LLMRegistry.get()
    if _graph is not None and not force and _impl_model_name == getattr(current_model, "model_name", None):
        return _graph

    _register_profile()

    from deepagents import create_deep_agent
    from deepagents.backends import FilesystemBackend
    from deepagents.middleware import FilesystemMiddleware

    if TOOLS is None:
        raise RuntimeError("chat 图工具未注入（service 装配遗漏，见 app/services/chat.py）")

    # FilesystemBackend 只读限定到 app/skills/（virtual_mode 默认 True，/skills/ 锚到 root_dir）。
    # skill 是静态 prompt 文件，read_file 只碰这个目录；用户数据/密钥在业务库与 .env，不在该目录。
    backend = FilesystemBackend(root_dir=_SKILLS_DIR)

    # 只留 read_file（SkillsMiddleware 需要），关其余 filesystem 工具。
    # 中间件泛型与 create_deep_agent 期望的 AgentMiddleware 对齐有版本差异，显式 Any 收口。
    _middleware: list[Any] = [
        FilesystemMiddleware(tools=["read_file"], backend=backend),
        TodoListMiddleware(),
        _PromptRebuildMiddleware(),
    ]
    agent = create_deep_agent(
        model=current_model,
        tools=list(TOOLS),
        system_prompt="",  # 真正的 system prompt 由 wrap_model_call 每轮重建（块 3），这里先空占位
        skills=["/"],  # 相对 backend root_dir（app/skills/）的技能目录
        backend=backend,
        middleware=_middleware,
        checkpointer=get_checkpointer(),
        name="career-nova-chat",
    )
    _graph = agent
    _impl_model_name = getattr(current_model, "model_name", None)
    logger.info("chat_deep_agent_assembled", model=_impl_model_name)
    return _graph


def chat_graph_config(session_id: int, resume_epoch: int) -> RunnableConfig:
    """对话图的 config：thread_id=chat:{resume_epoch}:{session_id}（2026-09-24 带世界代次）。

    代次（resume_epoch）隔离：核爆（reset_all）与版本回滚（rollback）都 bump resume_epoch——
    代次一变，旧 thread_id 永不命中，agent 不会把上一段「世界」的工作记忆（旧 session 的
    改动讨论）捞回来。这修的是「核爆后 id 归零重算 → 新会话 thread 撞上旧残留 → 幻视」，
    以及「回滚后旧 thread 成哑弹」两个同根问题。

    resume_epoch 由调用方（service 层）读好传入——graph 不碰 DB（分层铁律）。
    """
    return {"configurable": {"thread_id": thread_id("chat", f"{resume_epoch}:{session_id}")}}


async def run_chat_agent(session_id: int, user_text: str, resume_epoch: int) -> str:
    """跑对话 deep agent，返回助手最终回复文本。

    session_id：当前 session id（与 resume_epoch 一起决定 thread_id → 从 checkpointer 恢复历史）。
    user_text：本轮用户新消息（增量式——checkpoint 里已有历史，只追加这条）。
    resume_epoch：简历世界代次（service 层读好传入），参与 thread_id 派生做记忆隔离。
    返回：助手最终回复纯文本。
    """
    graph = get_chat_graph()
    result = await graph.ainvoke(
        {"messages": [{"role": "user", "content": user_text}]},
        config=chat_graph_config(session_id, resume_epoch),
    )
    # 最终回复 = 最后一条 AIMessage 的文本内容（无 tool_calls）。
    messages = result.get("messages", [])
    for m in reversed(messages):
        if isinstance(m, AIMessage) and m.content and not m.tool_calls:
            return extract_text_content(m.content)
    # 走到这 = 图跑完却没有可读的助手回复（模型只吐了 tool_calls 后中断等）。出声，不静默吞。
    logger.warning("chat_agent_no_assistant_reply", session_id=session_id, message_count=len(messages))
    return ""


def reset_chat_graph() -> None:
    """清缓存图（改模型/base_url/key 后调用，与 LLMRegistry.reset 同频）。"""
    global _graph, _impl_model_name
    _graph = None
    _impl_model_name = None


async def retract_last_user_message_from_graph(session_id: int, resume_epoch: int) -> str | None:
    """Deep 撤回（甲方案）：从 checkpointer 精准删最后一条 user 消息，返回其原文。

    双投影一致性（2026-09-09）：表删了最后 user 消息，checkpointer 的 thread 也得删
    同一条——否则 agent 下次恢复上下文还看得到已撤回的话，表与 agent 记忆脱钩。

    撤回真实场景 = 停止后那轮 agent 被 cancel、没产出回复，thread 里最后一条即该
    user 消息。用 RemoveMessage(id=...) 精准删（按 id 移除，reducer 识别删除指令），
    不动其它消息。无 checkpointer（:memory: 测试）或无 user 消息 → 返回 None（幂等）。
    resume_epoch 参与 thread_id 派生（与 run_chat_agent 同一代次才定位得到同一条 thread）。
    """
    checkpointer = get_checkpointer()
    if checkpointer is None:
        return None
    graph = get_chat_graph()
    cfg = chat_graph_config(session_id, resume_epoch)
    state = await graph.aget_state(cfg)
    messages = state.values.get("messages", [])
    last_user = next((m for m in reversed(messages) if m.type == "human"), None)
    if last_user is None or last_user.id is None:
        return None
    await graph.aupdate_state(cfg, {"messages": [RemoveMessage(id=last_user.id)]})
    logger.info("chat_retract_message_removed_from_checkpoint", session_id=session_id)
    return extract_text_content(last_user.content)
