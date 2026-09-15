import { useState, type ReactNode } from 'react'
import { Button } from '@mantine/core'
import { ChevronDown, ChevronUp } from 'lucide-react'
import { useT } from '@/lib/i18n'
import type { Suggestion } from '@/types/resume'

/**
 * 统一建议卡片（2026-08-13，左右栏 + 聊天提议卡共用，治"左右显示不一致"）。
 *
 * 元素序统一（用户确认的左右同构方案）：
 * `sev 点` + `type` 徽标 + `target`（在哪/作用域，超长省略）+ **原文（划线）→ 新改法** + `reason`（小字）。
 * 动作区由消费方（SuggestionBasket 的接受/拒绝/聊一聊、撤回；聊天提议卡的收进已确认/继续聊）注入。
 *
 * 原文为空（提炼型建议无原文）时只显示改法——避免裸划线占位。
 *
 * 收起/展开（2026-08-13，grill 定稿）：
 * - 待定卡默认展开、右三栏卡默认收起，**都可手动点开/收起**（卡头右侧 ▾/▴ 独立按钮）。
 * - 收起态 = 两行：`sev+type+target` 一行 + 改法截断一行（不点开也能瞥一眼方向）。
 * - 聊天提议卡不折叠（collapsible=false = 始终全展开）。
 *
 * `headActions`（2026-08-13）：卡头右侧、折叠按钮旁的操作位——右三栏「撤回」常驻这里，
 * 收起/展开都能直接撤回（不再藏在展开区的 actions 里）。
 */
export function SuggestionCard({
  suggestion,
  typeLabel,
  children,
  headActions,
  collapsible = false,
  defaultCollapsed = false,
}: {
  suggestion: Pick<Suggestion, 'type' | 'target' | 'original' | 'suggested' | 'reason' | 'severity'>
  typeLabel?: (type: Suggestion['type']) => string
  /** 动作区（按钮行），仅展开态渲染。 */
  children?: ReactNode
  /** 卡头右侧操作位（折叠按钮旁，收起/展开都渲染）。 */
  headActions?: ReactNode
  /** 是否可收起/展开（面板卡 true；聊天提议卡 false）。 */
  collapsible?: boolean
  /** 初始是否收起（右三栏默认收起；待定默认展开传 false）。 */
  defaultCollapsed?: boolean
}) {
  const t = useT()
  const label = typeLabel ? typeLabel(suggestion.type) : suggestion.type
  const [collapsed, setCollapsed] = useState(defaultCollapsed)

  return (
    <div className={`suggestion-card${collapsed ? ' collapsed' : ''}`}>
      <div className="suggestion-card-head">
        <span className={`suggestion-sev sev-${suggestion.severity}`} />
        <span className="suggestion-item-type">{label}</span>
        <span className="suggestion-item-target" title={suggestion.target}>
          {suggestion.target}
        </span>
        {headActions}
        {collapsible && (
          <Button
            variant="subtle"
            size="compact-xs"
            className="suggestion-toggle"
            aria-label={collapsed ? t('resume.suggestionExpand') : t('resume.suggestionCollapse')}
            title={collapsed ? t('resume.suggestionExpand') : t('resume.suggestionCollapse')}
            onClick={() => setCollapsed((v) => !v)}
          >
            {collapsed ? <ChevronDown size={13} /> : <ChevronUp size={13} />}
          </Button>
        )}
      </div>
      {collapsed ? (
        <div className="suggestion-card-preview">{suggestion.suggested}</div>
      ) : (
        <>
          <div className="suggestion-card-body">
            {suggestion.original ? (
              <>
                <span className="suggestion-card-original">{suggestion.original}</span>
                <span className="suggestion-card-arrow">→</span>
              </>
            ) : null}
            <span className="suggestion-card-suggested">{suggestion.suggested}</span>
          </div>
          {suggestion.reason && <div className="suggestion-card-reason">{t('resume.suggestionReason')}: {suggestion.reason}</div>}
          {children && <div className="suggestion-card-actions">{children}</div>}
        </>
      )}
    </div>
  )
}
