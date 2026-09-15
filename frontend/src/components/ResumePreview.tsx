import { PdfPreview } from './PdfPreview'
import { HtmlPreview } from './HtmlPreview'
import { MessageMarkdown } from './chat/MessageMarkdown'
import { DEFAULT_TYPOGRAPHY, type Typography } from '@/stores/resumeStore'

/**
 * 左栏简历预览（分档，遵循 docs/design/resume.md §8）：
 * - PDF → react-pdf 原生渲染原件
 * - HTML → iframe + blob URL 原生渲染（sandbox 禁脚本，保留样式布局）
 * - 排版层 HTML（2026-08-13 双源分层）→ iframe + blob 渲染排版产物（生成/改写版）
 * - TXT/MD → 原文渲染（text 来自 blob 读出的原文件内容，当次上传或重启后）
 * - DOCX/PPTX/XLSX/CSV → MarkItDown 解析后的 Markdown 兜底（所见即所抽）
 *
 * 分档优先级：原件(pdf/html) > 排版层 html > txt/md 原文 > markdown 兜底。
 * previewMarkdown 由后端 parse 返回；原生预览格式（pdf/html/txt/md）走 blob URL，
 * text 由父组件从 blob 读出（重启后同样可用——原件已落盘）。
 */

interface ResumePreviewProps {
  /** object URL（PDF / HTML / TXT / MD 预览用）。 */
  url: string | null
  /** 扩展名（小写，无点），驱动分档。 */
  ext: string
  /** 后端 parse 返回的 Markdown（DOCX 等预览兜底 / 内容层）。 */
  markdown: string
  /** 文件文本内容（TXT/MD 原文渲染用，父组件从 blob 读）。 */
  text: string
  /** 排版层 HTML（2026-08-13）：排版 agent 从 markdown 渲染的完整 HTML 文档，iframe 渲染。 */
  html?: string
  /** 排版自由度配置（2026-09-02）：仅"生成的 HTML 简历"路径生效（html prop 透传到 HtmlPreview 注入）。
      用户上传原件（url 路径）不开放排版调节，此值被忽略。 */
  typography?: Typography
  /** 控件 B 整页缩放倍率（2026-09-02）：同 typography，仅生成的 HTML 路径生效。 */
  pageZoom?: number
  /** 是否画 A4 分页辅助线（2026-09-02 grill）：仅生成的 HTML 简历传 true。 */
  showPageGuides?: boolean
}

/** 原件可原生渲染的文本格式：直接用原文，不经后端。 */
const NATIVE_TEXT_EXTS = new Set(['txt', 'md'])
/** 原件不可渲染 → MarkItDown 解析后 Markdown 兜底。 */
const MARKDOWN_FALLBACK_EXTS = new Set(['docx', 'pptx', 'xlsx', 'csv'])

export function ResumePreview({ url, ext, markdown, text, html, typography = DEFAULT_TYPOGRAPHY, pageZoom = 1, showPageGuides = false }: ResumePreviewProps) {
  // 排版层 HTML 优先于原件/兜底：生成/改写版有排版产物 → iframe 渲染（像简历纸），支持排版调节 + 分页辅助线
  if (html) {
    return <HtmlPreview html={html} typography={typography} pageZoom={pageZoom} showPageGuides={showPageGuides} />
  }

  if (ext === 'pdf' && url) {
    return <PdfPreview url={url} />
  }

  if (ext === 'html' && url) {
    return <HtmlPreview url={url} />
  }

  if (NATIVE_TEXT_EXTS.has(ext)) {
    return (
      <div className="resume-preview-text">
        {text ? <MessageMarkdown content={text} /> : <p className="resume-preview-empty">正在读取文件…</p>}
      </div>
    )
  }

  if (MARKDOWN_FALLBACK_EXTS.has(ext)) {
    return (
      <div className="resume-preview-text">
        {markdown ? <MessageMarkdown content={markdown} /> : <p className="resume-preview-empty">正在解析…</p>}
      </div>
    )
  }

  // 生成预览（1.3）：无原件、但已有 Markdown（生成草稿暂存态）→ 直接渲染 Markdown。
  if (markdown) {
    return (
      <div className="resume-preview-text">
        <MessageMarkdown content={markdown} />
      </div>
    )
  }

  // 未知扩展名（理论上进不来，白名单外已在入口拦截）：空态
  return (
    <div className="resume-preview-text">
      <p className="resume-preview-empty">无法预览此格式。</p>
    </div>
  )
}
