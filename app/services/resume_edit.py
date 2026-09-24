"""简历「编辑/生成」共享核心（2026-09-01，冷启动/暖态统一 + 去孪生）。

结构化转向后（docs/adr/0004），原料（facts / 上传原件 Markdown）和产物（resume JSON）
被拆成两份，而「开始改」`_incremental_rewrite` 与「出简历」流水线都只认
「现有 JSON」这一份原料——冷启动（上传原件、无 JSON）时两条路都撞死路。本模块抽出
两条共享路径，让冷/暖两条路落到同一份核心上：

- `generate_json_from_facts`：facts → JSON（生成）。冷启动「开始改/改简历」也走它，
  把已确认建议 / 用户改简历请求作为「改进要求」注入生成 prompt，直接产出 JSON。
- `edit_json`：JSON + 指令 → 新 JSON（暖态增量改写）。facts 只补缺口（JSON 权威，
  facts 不覆盖用户手工改过的措辞），见 prompt 层 `rewrite_content.md`。

分层：本模块是 service 层（读 facts/preferences → 调 LLM），不碰 DB 写、不碰 Graph。
纯结构化输出（response_format=Resume）+ 坏答案识别（整份空 → EmptyOutputError）统一收口。
"""

from langchain_core.messages import HumanMessage

from app.core.errors import ConflictError, EmptyOutputError
from app.prompts import load_generate_resume_prompt
from app.repositories.facts import list_facts
from app.schemas.resume import Resume, resume_to_json
from app.services.direction import get_direction
from app.services.llm import llm_service
from app.services.optimization import build_decisions_text
from app.utils.facts import flatten_facts


async def build_facts_text() -> str:
    """把 active 且上简历（on_resume=True）的事实格式化成生成输入（按分类分组，嵌套展平）。

    格式：`[{category}] {title}` 后跟 `  - {point}` 缩进要点行——与 _verify_facts_covered
    的解析（title 行以 `[` 开头、要点行以 `-` 开头）严格对齐（共享 utils.facts.flatten_facts）。
    2026-09-06（ADR 0011）：只取 on_resume=True——方向槽位/敏感信息（on_resume=False）
    不进生成 prompt（连「别展示」的软指令都不给 LLM 看），机器门与生成共享同一过滤口径。
    """
    return flatten_facts(await list_facts(status="active"), on_resume_only=True)


async def build_focus() -> tuple[str, str]:
    """读当前侧重信号 (target_role, preferences)——生成改写都喂（ADR 0012 合并）。

    target_role 来自方向槽位（「目标岗位：X」active fact，经 get_direction 读回）；
    preferences 来自**持久决策记录**（kind=decision 的改动记录，2026-09-23 起取代 custom 偏好）——
    它们是跨版本约束（如"不要项目经历栏，并进工作经历"）。侧重只指导内容侧重、不写进简历成品
    （prompt 层规则，见 generate_resume.md / rewrite_content.md）。Node 不碰 DB，
    由 service 读出后经 state / 参数传进 prompt。
    """
    direction = await get_direction()
    preferences = await build_decisions_text()
    return direction.role, preferences


def _is_empty_resume(resume: Resume) -> bool:
    """结构化产出整份空判定：五大块全空 = 坏答案（统一坏答案识别口径）。"""
    return not (resume.basics.name or resume.work or resume.projects or resume.skills or resume.education)


async def _call_resume(prompt: str, empty_message: str) -> Resume:
    """结构化输出一份 resume JSON（json_schema 严格模式，不支持自动降级）；整份空 → EmptyOutputError。"""
    resume: Resume = await llm_service.call([HumanMessage(content=prompt)], response_format=Resume)
    if _is_empty_resume(resume):
        raise EmptyOutputError(empty_message)
    return resume


async def generate_json_from_facts(
    *,
    target_role: str | None = None,
    instruction: str = "",
    preferences: str | None = None,
) -> str:
    """Facts → 结构化 resume JSON（生成路径；冷启动「开始改/改简历」也复用）。

    target_role：目标岗位（可空 = 通用版）；instruction：冷启动注入的改进要求
    （已确认建议 / 用户改简历请求，生成时一并满足）。
    preferences：持久决策记录文本（kind=decision）——由调用方（cold_start_node）从
      state.preferences 透传，与暖态 content 节点同一数据源；传 None 时回退到本函数
      内部现读（保留旧行为，兼容直接调用方）。
    返回结构化真身 JSON 字符串。无 facts → ConflictError（无法生成）。
    2026-09-07（ADR 0012 合并）：事实覆盖校验统一交图机器门（validate_content 自愈回边），
    本函数不再自带 raise 断言（避免与暖态双标）；整份空仍由 _call_resume 拦（坏答案识别）。
    """
    facts_text = await build_facts_text()
    if not facts_text:
        raise ConflictError("还没有任何用户事实，无法生成简历——先在聊天里聊聊你的经历吧")
    if preferences is None:
        preferences = await build_decisions_text()
    prompt = load_generate_resume_prompt(target_role or "", facts_text, preferences, instruction)
    resume = await _call_resume(prompt, "模型没生成出简历内容，换个说法再试或换模型")
    return resume_to_json(resume)


async def edit_json(prompt: str) -> tuple[str, str]:
    """JSON + 指令 → 新 JSON（暖态增量改写；结构化输出 + 坏答案识别）。返回 (json, summary)。

    prompt 由调用方构造（load_rewrite_content_prompt，带 facts 补缺口段）；summary 走 resume.summary 字段（无则调用方回退规则名）。
    """
    resume = await _call_resume(prompt, "模型没改出内容，换个说法再试或换模型")
    return resume_to_json(resume), resume.summary
