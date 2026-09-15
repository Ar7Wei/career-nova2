import { AreaChart, BarChart, DonutChart, LineChart, PieChart, RadarChart } from '@mantine/charts'
import type { ChartSpec } from '@/types/chart'
import { isChartSpec } from '@/types/chart'

/**
 * 图表渲染器：吃一份 ChartSpec → 调 @mantine/charts 渲染（2026-09-15 方向定稿）。
 *
 * **校验兜底是红线**：spec 来自 analyze Agent 的自由产出，模型可能配错（类型不认得 / data 空 /
 * 字段对不上）。`isChartSpec` 不过 → 返回 null（静默跳过这张图），绝不让坏 spec 白屏——
 * 这是「让模型自由配图」能上线的前提（ADR：LLM 可配错、前端必须兜）。
 */

// 主题绿色系（与 alger 主题 green 色板同源）：series 缺 color 时按序取。
const PALETTE = ['green.6', 'green.4', 'green.8', 'gray.5']

const CHART_HEIGHT = 200

/** donut/pie 需要 {name,value,color} cell 数组：name 取 x、value 取第一个 series.key。 */
function toCells(spec: ChartSpec) {
  const valueKey = spec.series[0].key
  return spec.data.map((row, i) => ({
    name: String(row[spec.x] ?? ''),
    value: Number(row[valueKey] ?? 0),
    color: PALETTE[i % PALETTE.length],
  }))
}

function gridSeries(spec: ChartSpec) {
  return spec.series.map((s, i) => ({ name: s.key, label: s.label || s.key, color: s.color || PALETTE[i % PALETTE.length] }))
}

export function ChartRenderer({ spec }: { spec: ChartSpec }) {
  if (!isChartSpec(spec)) return null

  const title = spec.title ? <div className="chart-title">{spec.title}</div> : null
  const note = spec.note ? <div className="chart-note">{spec.note}</div> : null

  let chart: React.ReactNode
  switch (spec.type) {
    case 'bar':
      chart = (
        <BarChart
          h={CHART_HEIGHT}
          data={spec.data}
          dataKey={spec.x}
          orientation={spec.orientation ?? 'horizontal'}
          series={gridSeries(spec)}
          withBarValueLabel
          tickLine="none"
          gridAxis={spec.orientation === 'vertical' ? 'none' : 'x'}
          withLegend={spec.series.length > 1}
        />
      )
      break
    case 'line':
      chart = (
        <LineChart h={CHART_HEIGHT} data={spec.data} dataKey={spec.x} series={gridSeries(spec)} tickLine="none" gridAxis="x" withLegend={spec.series.length > 1} />
      )
      break
    case 'area':
      chart = (
        <AreaChart h={CHART_HEIGHT} data={spec.data} dataKey={spec.x} series={gridSeries(spec)} curveType="natural" tickLine="none" gridAxis="x" withLegend={spec.series.length > 1} />
      )
      break
    case 'donut':
      chart = <DonutChart data={toCells(spec)} size={160} thickness={16} withLegend tooltipDataSource="segment" />
      break
    case 'pie':
      chart = <PieChart data={toCells(spec)} size={160} withLegend tooltipDataSource="segment" />
      break
    case 'radar':
      chart = (
        <RadarChart
          h={CHART_HEIGHT + 40}
          data={spec.data}
          dataKey={spec.x}
          series={spec.series.map((s, i) => ({ name: s.key, label: s.label || s.key, color: s.color || PALETTE[i % PALETTE.length] }))}
          withPolarGrid
          withPolarAngleAxis
          withTooltip
          withLegend={spec.series.length > 1}
        />
      )
      break
    default:
      return null
  }

  return (
    <div className="chart-block">
      {title}
      {chart}
      {note}
    </div>
  )
}
