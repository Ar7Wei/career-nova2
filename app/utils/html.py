"""HTML 排版产物（resume_documents.html）的工具函数。

职责（分层铁律：纯函数，不碰 DB、不知道 LLM 存在）：
- `extract_html_document`：从 LLM 回复里可靠提取完整 HTML 文档（剥围栏/散文）。
- `validate_html`：落库前结构校验（配对/无 script/非空）。

排版 agent（render.py）产出 HTML 后经此层清洗 + 校验，坏答案在 service 层抛
EmptyOutputError（统一错误契约）。HTML 是 Markdown 的排版渲染（docs/design/resume.md
§13.3），不是内容真相——校验只查结构，不查内容。
"""

from html.parser import HTMLParser

__all__ = ["extract_html_document", "validate_html"]


def extract_html_document(content: str) -> str:
    """从 LLM 回复提取完整 HTML 文档。

    兼容：
    - 回复可能被 ```html ... ``` 代码围栏包着（常见）。
    - 回复可能带前导散文/说明文字（"这是生成的简历："）。
    - 从第一个 `<!doctype` 或 `<html` 切到最后一个 `</html>`。
    提取不到 → 返回空串（调用方据此抛 EmptyOutputError）。

    注意：不做大括号/标签配对修复——那是 validate_html 的活。
    """
    if not content:
        return ""
    text = content.strip()
    # 剥 Markdown 代码围栏（```html ... ``` / ``` ... ```）
    if text.startswith("```"):
        # 去掉首行 ```(html)? 与末尾 ```，其余原样保留
        first_nl = text.find("\n")
        if first_nl != -1:
            text = text[first_nl + 1 :]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    # 定位文档边界：从 <!doctype 或 <html 起到最后 </html>
    lower = text.lower()
    start = lower.find("<!doctype")
    if start == -1:
        start = lower.find("<html")
    end = lower.rfind("</html>")
    if start == -1 or end == -1 or end <= start:
        return ""
    return text[start : end + len("</html>")].strip()


def validate_html(html: str) -> list[str]:
    """落库前 HTML 结构校验。返回问题列表，空列表 = 通过。

    检查：
    - 非空（空 = 模型没产出）。
    - 有 `<html` / `</html>` 配对（完整文档）。
    - `<style>` / `</style>` 配对（CSS 不吞 body——`{` 漏花括号/选择器错会让
      解析器把后续内容当样式吃掉，最常导致"内容被吞"）。
    - 无 `<script`（prompt 已禁；防注入/脚本执行）。
    不检查内容语义（浏览器对 HTML 语法极容错，结构对 + 无 script 即可）。
    """
    problems: list[str] = []
    if not html or not html.strip():
        problems.append("empty")
        return problems
    lower = html.lower()
    if "<html" not in lower:
        problems.append("missing_html_open")
    if "</html>" not in lower:
        problems.append("missing_html_close")
    if lower.count("<style") != lower.count("</style>"):
        problems.append("unbalanced_style")
    if "<script" in lower:
        problems.append("contains_script")
    return problems


class _TextExtractor(HTMLParser):
    """把 HTML 转纯文本：剥 <style>/标签，块级边界换行，解实体。"""

    _BLOCK_TAGS = {"div", "p", "section", "h1", "h2", "h3", "h4", "h5", "h6", "li", "ul", "ol", "br", "table", "tr"}
    _SKIP_TAGS = {"style", "script"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0
        self._pending_br = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if tag == "br":
            self._pending_br = True
        elif tag in self._BLOCK_TAGS:
            self._pending_br = True

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in self._BLOCK_TAGS and not self._pending_br:
            self._pending_br = True

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if data.strip():
            if self._pending_br:
                self.parts.append("\n")
                self._pending_br = False
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """HTML → 纯文本（轻量，非通用渲染器）。

    agent 上下文注入 / recompose 内容保留用：剥 <style> 与标签、块级换行、
    解实体。够读、够小，不追求布局保真。
    """
    if not html:
        return ""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    lines: list[str] = []
    for part in parser.parts:
        # 合并空行/首尾空白
        stripped = part.strip()
        if stripped:
            lines.append(stripped)
    return "\n".join(lines)
