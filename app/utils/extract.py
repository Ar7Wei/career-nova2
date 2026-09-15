"""文件提取工具：把上传文件统一转成 Markdown（喂 LLM 抽事实）。

职责单一：二进制 → Markdown 的"转码"，不做任何理解/抽取（那是 LLM 的活）。
- 白名单按扩展名拦截（MarkItDown 前的一道闸，给出明确不支持的错误）。
- 文本类（txt/md）先走编码嗅探（防 GBK/UTF-8 乱码），不绕 MarkItDown。
- 其余（pdf/docx/html/csv/pptx/xlsx）交给 MarkItDown 编排层。
"""

import io
from pathlib import Path

import charset_normalizer
from markitdown import MarkItDown

from app.core.errors import AppError

# 入口白名单：只收这些扩展名（详见 docs/design/resume.md §7）。
_ALLOWED_EXTS = {"txt", "md", "pdf", "docx", "html", "csv", "pptx", "xlsx"}

# 纯文本类：自己走编码嗅探，比绕 MarkItDown 更可控（GBK 坑）。
_TEXT_EXTS = {"txt", "md"}

# 提取结果低于这个字符数，视为"没抽出来"（扫描件 PDF 的典型特征）。
_MIN_EXTRACTED_CHARS = 20


class UnsupportedFileTypeError(AppError):
    """文件扩展名不在白名单内。"""

    code = "unsupported_file_type"
    status = 415
    message = "不支持的文件格式"
    action = "reupload"


class EmptyExtractionError(AppError):
    """文件可识别但提取不出文本（如扫描件 PDF）。"""

    code = "empty_extraction"
    status = 422
    message = "未能从文件中提取出有效文本（疑似图片型/扫描件）"
    action = "reupload"


def validate_filename(filename: str) -> str:
    """校验扩展名在白名单内，返回小写扩展名；否则抛 UnsupportedFileTypeError。"""
    ext = Path(filename).suffix.lstrip(".").lower()
    if ext not in _ALLOWED_EXTS:
        raise UnsupportedFileTypeError(f"不支持的文件格式：.{ext or '(无扩展名)'}")
    return ext


def _decode_text(data: bytes) -> str:
    """解码文本，防 GBK/UTF-8 乱码。

    中文简历 TXT 以 UTF-8 / GBK 为主。短文本上 charset_normalizer 常把 GBK
    误判成 big5（解出乱码），故对 CJK 编码统一归并到 GBK（big5/gb2312 的超集，
    对中文 Windows 文件命中率最高）。策略：
    1. 嗅探器认 UTF/ASCII → 用 UTF-8 严格解码；
    2. 嗅探器指向 CJK 编码（gbk/big5/gb2312）或未知 → 用 GBK；
    3. 其余信嗅探器；最终兜底 UTF-8 替换，保证不抛异常。
    """
    detected = charset_normalizer.from_bytes(data).best()
    detected_enc = (detected.encoding or "").lower().replace("-", "_") if detected else ""

    if detected_enc in ("utf_8", "ascii"):
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            pass

    # CJK 或未知：中文场景优先 GBK（big5/gb2312 超集）
    if detected_enc in ("", "gbk", "gb2312", "gb18030", "big5", "big5hkscs"):
        try:
            return data.decode("gbk")
        except (UnicodeDecodeError, LookupError):
            pass

    if detected is not None and detected_enc:
        try:
            return data.decode(detected_enc)
        except (UnicodeDecodeError, LookupError):
            return str(detected)

    return data.decode("utf-8", errors="replace")


def extract_markdown(filename: str, data: bytes) -> str:
    """把上传文件转成 Markdown 文本。

    - 扩展名不在白名单 → UnsupportedFileTypeError（415）。
    - 提取结果过短（疑似扫描件）→ EmptyExtractionError（422）。
    """
    ext = validate_filename(filename)

    if ext in _TEXT_EXTS:
        text = _decode_text(data).strip()
    else:
        md = MarkItDown()
        result = md.convert(io.BytesIO(data))
        text = (result.text_content or "").strip()

    if len(text) < _MIN_EXTRACTED_CHARS:
        raise EmptyExtractionError("未能从文件中提取出有效文本（疑似图片型/扫描件）")
    return text
