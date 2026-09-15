import { useEffect, useState } from 'react'
import { Button, TextInput } from '@mantine/core'
import { Maximize, Minus, Plus } from 'lucide-react'
import { useT } from '@/lib/i18n'

/** 控件 B 整页缩放（2026-09-02，预览镜头/视图层）：
 * 5% 步进，30%~150%，100% = A4 物理大小。默认打开预览时父组件已「自动适应宽度」，
 * 这里做手动微调 + 一键回到适应宽度。只把 A4 纸等比缩进预览窗看全貌，不重排内容、与导出无关。 */
const STEP = 5
const MIN = 30
const MAX = 150

const clamp = (n: number) => Math.min(MAX, Math.max(MIN, n))

interface ZoomControlProps {
  /** 当前整页缩放（%）。 */
  value: number
  onChange: (pct: number) => void
  /** 一键「适应宽度」：父组件按预览窗宽 ÷ A4 宽算出倍率回填。 */
  onFitWidth: () => void
  /** 禁用（2026-09-02 统一「不可用=置灰」）：上传原件时置灰 + 悬停提示，不隐藏。 */
  disabled?: boolean
  /** 禁用原因（悬停 title）。 */
  disabledReason?: string
}

/**
 * 控件 B 整页缩放控件：左下角悬浮玻璃（复用 .glass 语言），`适应宽度 / − / 输入% / ＋`。
 * 仅"生成的 HTML 简历"开放（用户上传原件隐藏，父组件控制显隐）。
 * - 适应宽度钮：回到「刚好铺满预览窗宽」的镜头（看全貌的起点）。
 * - ±：按 STEP 步进。中间输入框：手动输入百分比，回车/失焦生效（夹取范围）。
 * 与控件 A（字号阶梯，编辑项面板里）独立：A 改排版/重排内容/关联导出，B 只是预览镜头。
 */
export function ZoomControl({ value, onChange, onFitWidth, disabled = false, disabledReason }: ZoomControlProps) {
  const t = useT()
  // 输入框草稿：跟随外部 value，但用户编辑期间以草稿为准（不打断输入）。
  const [draft, setDraft] = useState(String(value))
  useEffect(() => {
    setDraft(String(value))
  }, [value])

  const commitDraft = () => {
    const n = Number(draft.replace(/[^\d.]/g, ''))
    if (!Number.isFinite(n) || draft.trim() === '') {
      setDraft(String(value)) // 非法 → 还原
      return
    }
    onChange(clamp(Math.round(n)))
  }

  return (
    <div className={`zoom-control glass${disabled ? ' is-disabled' : ''}`} role="group" aria-label={t('resume.zoom')} title={disabled ? disabledReason : undefined}>
      <Button variant="subtle" size="compact-xs" className="zoom-btn" aria-label={t('resume.fitWidth')} title={t('resume.fitWidth')} onClick={onFitWidth} disabled={disabled}>
        <Maximize size={13} />
      </Button>
      <Button
        variant="subtle"
        size="compact-xs"
        className="zoom-btn"
        aria-label={t('resume.zoomOut')}
        onClick={() => onChange(clamp(value - STEP))}
        disabled={disabled || value <= MIN}
      >
        <Minus size={13} />
      </Button>
      <TextInput
        classNames={{ input: 'zoom-input' }}
        value={draft}
        aria-label={t('resume.zoom')}
        onChange={(e) => setDraft(e.currentTarget.value)}
        onBlur={commitDraft}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.currentTarget.blur() // 触发 commitDraft
          }
        }}
        inputMode="numeric"
        disabled={disabled}
      />
      <span className="zoom-pct">%</span>
      <Button
        variant="subtle"
        size="compact-xs"
        className="zoom-btn"
        aria-label={t('resume.zoomIn')}
        onClick={() => onChange(clamp(value + STEP))}
        disabled={disabled || value >= MAX}
      >
        <Plus size={13} />
      </Button>
    </div>
  )
}
