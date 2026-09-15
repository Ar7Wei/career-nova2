/**
 * 分析报告的「块流」契约（ReportBlock）：文/图穿插叙事（2026-09-15 定稿）。
 *
 * 报告正文 = 有序的 text/chart 块序列，前端按序渲染、图穿插在段落之间。与后端
 * `app/schemas/report_block.py` **字段一一对应**（双侧同构），改一边必须改另一边。
 * 坏 chart 块单独丢弃、text 块照显（兜底红线）。
 */

import { isChartSpec, type ChartSpec } from '@/types/chart'

export interface TextBlock {
  kind: 'text'
  md: string
}

export interface ChartBlock {
  kind: 'chart'
  spec: ChartSpec
}

export type ReportBlock = TextBlock | ChartBlock

/**
 * 运行时校验 + 清洗：把后端/模型给的原始块数组洗成可安全渲染的 ReportBlock[]。
 * - 非对象 / kind 不认得 → 丢。
 * - text 块：md 非字符串 → 丢；空串也丢（没内容别占位）。
 * - chart 块：spec 过 isChartSpec 才留（模型配错图 → 只少这张图，不崩）。
 */
export function toReportBlocks(raw: unknown): ReportBlock[] {
  if (!Array.isArray(raw)) return []
  const out: ReportBlock[] = []
  for (const b of raw) {
    if (typeof b !== 'object' || b === null) continue
    const kind = (b as { kind?: unknown }).kind
    if (kind === 'text') {
      const md = (b as { md?: unknown }).md
      if (typeof md === 'string' && md.trim() !== '') out.push({ kind: 'text', md })
    } else if (kind === 'chart') {
      const spec = (b as { spec?: unknown }).spec
      if (isChartSpec(spec)) out.push({ kind: 'chart', spec })
    }
  }
  return out
}
