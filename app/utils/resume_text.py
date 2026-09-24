"""简历「给 prompt 的文本表示」纯函数（分层铁律：纯函数，不碰 DB、不知道 LLM 存在）。

放在 utils 而非 services/documents.py，是为了让 opening.py 也能用而不成环：
`services/documents.py` 顶部 `from app.services.opening import schedule_opening`，
opening 若反过来 import documents 就成环。本模块只依赖 schemas，无环。
"""

from app.schemas.documents import ResumeDocument


def resume_prompt_text(doc: ResumeDocument | None) -> str:
    """把当前简历文档转成「给 prompt 用的文本」——生成版给 resume_json，上传 v1 回退 markdown。

    为什么收口成一个函数：简历有两种表示，散在各 prompt 注入点（聊天 system prompt、
    开场引导）各自取字段。2026-09-11 之前两处都写 `doc.markdown`——而**生成版的
    markdown 恒为空串**（save_document 传 markdown=""，结构化真身在 resume_json）。
    后果：用户生成/改出一版简历后，聊天 agent 的「用户当前的简历文档」段是空的，
    agent 对自己刚做的简历完全瞎——suggest_improvements 只能盲抓、开场引导也没得点评。

    口径：生成版（resume_json 非空）→ JSON 文本，加一行标注告诉模型这是结构化 JSON
    （与 generate_resume.md / rewrite_content.md 的 input 同一套词表，模型不陌生）；
    上传 v1（无 resume_json）→ 原件 Markdown。两者都没有 → 空串（调用方各自兜底文案）。
    """
    if doc is None:
        return ""
    if doc.resume_json.strip():
        return f"（以下是结构化简历 JSON）\n{doc.resume_json}"
    return doc.markdown
