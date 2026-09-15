"""简历渲染：结构化 JSON → HTML（确定性纯函数）。

2026-08-28 排版转向（docs/adr/0004）：LLM 退出排版，固定模板确定性渲染。
`render_resume(resume, typography)` 是**纯函数**——同样的 JSON + 排版配置永远吐同样的 HTML，
内容与排版永久解耦，HTML 是内容的派生视图（不再是 LLM 自由生成物）。

2026-09-02 排版自由度（grill）：排版参数从单一 scale 扩成四参数 Typography
（字号/行距/段距/字间距），一体注入模板 :root CSS 变量（--scale/--lh/--spacing/--ls）。

分层：本层是纯渲染（放 utils，不属 service 编排层）——不碰 DB、不知道 LLM 存在。
输入 Resume schema + Typography，输出完整 HTML 文档（内联 <style> + :root 排版变量），
可落库（resume_documents.html）也可前端 iframe 直接预览。

方位（header/left/right）由 layout 映射表决定：render 按 slot 把 block 名分组，模板按分组
顺序渲染。调顺序 = 改 layout 数据（`default_layout()` / 前端编辑），不动模板。
"""

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from app.schemas.resume import BlockName, Resume, Typography, default_layout

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
# 惰性单例（模块级）：模板 + CSS 编译一次，per-request 不再做文件 IO / 重编译。
_env: Environment | None = None
_css: str = ""


def _get_env() -> Environment:
    """返回编译好的 Jinja2 环境（模板 + 宏，惰性单例）。"""
    global _env, _css
    if _env is None:
        _env = Environment(
            loader=FileSystemLoader(str(_TEMPLATES_DIR)),
            autoescape=True,  # 简历内容是纯文本，转义防注入（HTML 已从 LLM 手里收回）
        )
        _css = (_TEMPLATES_DIR / "resume.css").read_text(encoding="utf-8")
    return _env


def _group_blocks(resume: Resume) -> tuple[list[BlockName], list[BlockName], list[BlockName]]:
    """按 layout 的 slot 把 block 名分三组：header / left / right。

    - layout 为空（生成层没吐）→ 用 default_layout()。
    - 未在 layout 出现的 block 不出现在产物里（layout 是顺序与包含关系的唯一真相）。
    - 同一 block 可出现在多个 slot（如 skills 想左右都放）。
    返回 (header_blocks, left_blocks, right_blocks)，元素是 BlockName 字符串。
    """
    slots = resume.layout if resume.layout else default_layout()
    header = [s.block for s in slots if s.slot == "header"]
    left = [s.block for s in slots if s.slot == "left"]
    right = [s.block for s in slots if s.slot == "right"]
    return header, left, right


def render_resume(resume: Resume, scale: float | None = None, typography: Typography | None = None) -> str:
    """把结构化简历渲染成完整 HTML 文档（内联 CSS + 排版变量）。

    typography：排版自由度四参数（2026-09-02），注入 :root --scale/--lh/--spacing/--ls。
    scale：字号倍率快捷位（向后兼容旧调用 render_resume(r, 0.9)）——并入 Typography(scale=scale)。
    typography 优先；只给 scale 时其余三参数用默认。两者都缺省 = 默认排版。
    返回完整 <!doctype html> 文档，可直接落库 / iframe 预览 / printToPDF 导出。
    """
    typo = typography or Typography(scale=scale if scale is not None else 1.0)
    env = _get_env()
    header, left, right = _group_blocks(resume)
    template = env.get_template("resume.html.j2")
    return template.render(
        resume=resume,
        css=_css,
        typography=typo,
        header_blocks=header,
        left_blocks=left,
        right_blocks=right,
    )
