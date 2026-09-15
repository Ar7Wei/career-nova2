/**
 * 图表契约（ChartSpec）：投递页分析「自由配图」的稳定形状（2026-09-15 方向定稿）。
 *
 * analyze Agent 在产出分析叙事的同时可选配 0~2 张图。本类型与后端 `app/schemas/chart.py`
 * **字段一一对应**（双侧同构），改一边必须改另一边。前端渲染前经 `isChartSpec` 校验兜底——
 * 模型配错图时静默降级（不渲染、不崩），绝不让坏 spec 白屏。
 */

/** 开放的 6 种图型（ChartRenderer 用 @mantine/charts 一一渲染）。 */
export type ChartType = 'bar' | 'line' | 'area' | 'donut' | 'pie' | 'radar'

export interface ChartSeries {
  key: string
  label?: string
  color?: string
}

export interface ChartSpec {
  type: ChartType
  title?: string
  data: Record<string, unknown>[]
  x: string
  series: ChartSeries[]
  orientation?: 'horizontal' | 'vertical'
  note?: string
}

const KNOWN_TYPES: readonly string[] = ['bar', 'line', 'area', 'donut', 'pie', 'radar']

/**
 * 运行时校验：这份 spec 能不能画（模型可能配错——类型不认得 / data 空 / x、series.key
 * 对不上 data 行）。任一不过 → false，调用方降级（跳过这张图，不渲染、不崩）。
 */
export function isChartSpec(raw: unknown): raw is ChartSpec {
  if (typeof raw !== 'object' || raw === null) return false
  const s = raw as Partial<ChartSpec>
  if (typeof s.type !== 'string' || !KNOWN_TYPES.includes(s.type)) return false
  if (!Array.isArray(s.data) || s.data.length === 0) return false
  if (typeof s.x !== 'string' || s.x === '') return false
  if (!Array.isArray(s.series) || s.series.length === 0 || s.series.length > 2) return false
  const keys = [s.x, ...s.series.map((se) => se?.key)]
  if (keys.some((k) => typeof k !== 'string' || k === '')) return false
  // x 与每个 series.key 都要能在 data 行里找到（否则画不出来，属配错）。
  return keys.every((k) => s.data!.some((row) => row !== null && typeof row === 'object' && k in row))
}
