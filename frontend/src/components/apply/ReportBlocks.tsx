import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ChartRenderer } from '@/components/charts/ChartRenderer'
import type { ReportBlock } from '@/types/report_block'

/**
 * 报告块流渲染器：把 analyze Agent 产出的有序 text/chart 块按序渲染（2026-09-15 穿插定稿）。
 *
 * 图自然穿插在段落之间（讲一段→配一张→再讲一段），不再是「正文 + 尾部图集」。
 * text 块渲 **Markdown**（复用全局 `.md-body` 样式，见 styles/resume.css）——2026-10-11：
 * 旧版直接吐裸文本（`{b.md}`），模型写的 `**强调**`/列表都成了原文，报告看着"简陋"；
 * 改走 md-body 后行内强调/小标题/列表都能正确排版。chart 块走 ChartRenderer（内含
 * isChartSpec 兜底，坏 spec 静默跳过、不占位）。
 */
export function ReportBlocks({ blocks }: { blocks: ReportBlock[] }) {
  return (
    <>
      {blocks.map((b, i) =>
        b.kind === 'text' ? (
          <div key={i} className="analysis-text md-body">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{b.md}</ReactMarkdown>
          </div>
        ) : (
          <ChartRenderer key={i} spec={b.spec} />
        ),
      )}
    </>
  )
}
