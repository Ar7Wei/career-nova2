import type { ReactNode } from 'react'
import { SuggestionCard } from './SuggestionCard'
import type { ChangeItem, ChangeRecord } from '@/types/resume'

/**
 * 优化点/改进方向的共享展示（2026-10-11 抽出）。
 *
 * 优化点面板（待定栏）与投递页分析面板（改进方向）渲染的是**同一份数据**——change_records
 * 的「记录 → 子项」二元组。两处都按**原因分组**（原因写一次当组头，下面挂该原因的改动点卡片），
 * 卡片默认展开、可手动收起。差异只在动作区：优化点面板注入「拒绝/接受/聊一聊」三按钮，
 * 分析面板注入单个「改进」。故把展示骨架抽到这里，动作区由 `renderActions` 注入。
 */

/** 面板的一行 = 「某条记录里的某个改动点」（子项级）。 */
export interface PanelRow {
  record: ChangeRecord
  change: ChangeItem
}

/** 行标识：记录 id + 子项 id 二元组。 */
export const rowKey = (r: PanelRow) => `${r.record.id}.${r.change.id}`

/** 栏内按「原因」分组（保持记录内子项的原序）。 */
export function groupByReason(rows: PanelRow[]): { reason: string; rows: PanelRow[] }[] {
  const groups = new Map<string, PanelRow[]>()
  for (const r of rows) {
    const key = r.record.reason || '简历改进'
    const bucket = groups.get(key)
    if (bucket) bucket.push(r)
    else groups.set(key, [r])
  }
  return [...groups.entries()].map(([reason, groupRows]) => ({ reason, rows: groupRows }))
}

export function PendingGroupList({
  rows,
  renderActions,
  typeLabel,
  leaving,
  entering,
}: {
  rows: PanelRow[]
  /** 动作区按钮行（每行注入）。优化点面板 = 三按钮，分析面板 = 单个「改进」。 */
  renderActions: (row: PanelRow) => ReactNode
  typeLabel?: (type: ChangeItem['type']) => string
  /** 换栏动画态（可空）：leaving=源栏淡出中，entering=目标栏淡入中。 */
  leaving?: string | null
  entering?: string | null
}) {
  return (
    <div className="suggestion-list">
      {groupByReason(rows).map((group) => (
        <div key={group.reason} className="suggestion-group">
          <div className="suggestion-group-head" title={group.reason}>
            {group.reason}
          </div>
          {group.rows.map((row) => {
            const key = rowKey(row)
            return (
              <div
                key={key}
                className={`suggestion-card-wrap${leaving === key ? ' leaving' : ''}${entering === key ? ' entering' : ''}`}
              >
                {/* 待定/改进方向：默认展开、可手动收起。原因已提到分组头，卡内不再重复。 */}
                <SuggestionCard suggestion={{ ...row.change, reason: '' }} typeLabel={typeLabel} collapsible>
                  {renderActions(row)}
                </SuggestionCard>
              </div>
            )
          })}
        </div>
      ))}
    </div>
  )
}
