import { useEffect, useState } from 'react'
import { ScrollText } from 'lucide-react'
import { notifications } from '@mantine/notifications'
import api from '@/lib/api'
import { on } from '@/lib/events'
import { userErrorText } from '@/lib/errors'
import { useT } from '@/lib/i18n'
import { useResumeStore } from '@/stores/resumeStore'
import { ToolTab } from '@/components/ToolTab'
import { BusyOverlay } from '@/components/BusyOverlay'
import type { ChangeItem, ChangeRecord, ChangeStatus } from '@/types/resume'

/**
 * 决策·记录流面板（只读，2026-09-29）。
 *
 * 为什么有它：**跨版本决策**（用户拍过板的原则、"不做 X" 的负向约束）此前**在界面上完全看不见**
 * ——优化点四栏刻意跳过无子项的 `kind=decision`，而设置页那个「偏好可编辑」的入口从未建成。
 * 结果是用户看不到自己被禁了什么、也没有解除入口（word_choice 事故里，agent 甚至把这个
 * 系统自动聚合出来的约束当成"用户拍过板"讲给用户听）。
 *
 * 定位（与优化点面板的分工）：
 * - **优化点面板** = 待处理的条目（有状态、可操作、随版本结清）。
 * - **本面板** = 全量记录流（历史 + 决策），**只读**。
 *
 * 只读是刻意的：决策是**记录表**，不是设置项——不给用户手动增删改（那会变成第二真相源）。
 * 想改约束 → 在聊天里跟 agent 说，由它记一条新的。
 *
 * 忙碌态：与优化点面板共享同一套 `document_is_busy` 信号（applying/generating/rollingBack），
 * 工作时封闭——回滚正在整份换改动记录，读到的会是中间态。
 */

/** 状态 → i18n 键（记录流用，覆盖全部会出现的状态）。
 *  `proposed` 不在 `ChangeStatus` 里（它是旧处方表的残留态，收录后即转 pending）——
 *  渲染时兜底回落到原值，避免出现未知状态就崩。 */
const STATUS_KEYS: Record<ChangeStatus, string> = {
  pending: 'pending',
  confirmed: 'confirmed',
  rejected: 'rejected',
  discussing: 'discussing',
  applied: 'applied',
  archived: 'archived',
}

/** 已结清的记录（resolved_in_document_id 有值）——显示为历史，弱化。 */
const isSettled = (r: ChangeRecord) => r.resolved_in_document_id !== null

export function DecisionPanel() {
  const t = useT()
  const [records, setRecords] = useState<ChangeRecord[]>([])
  const applying = useResumeStore((s) => s.applying)
  const generating = useResumeStore((s) => s.generating)
  const rollingBack = useResumeStore((s) => s.rollingBack)
  const busy = applying || generating || rollingBack
  const busyLabel = rollingBack ? t('resume.rollingBack') : applying ? t('resume.busyApplying') : t('resume.busyAgentWorking')

  /**
   * 拉全量记录流。供挂载 / 事件刷新 / 展开时调用。
   * ⚠️ 别把 `load` 塞进 effect 依赖——`useT()` 每次渲染返回新闭包会让 effect 无限重跑
   * （SuggestionBasket 事故同源，曾把后端打爆）。
   */
  const load = async () => {
    try {
      const { data } = await api.get<{ records: ChangeRecord[] }>('/v1/optimization/records?scope=all')
      setRecords(data.records)
    } catch (err) {
      notifications.show({ color: 'red', title: t('resume.decisionPanel'), message: userErrorText(err) })
    }
  }

  useEffect(() => {
    void load()
    return on('resume-data-changed', () => {
      void load()
    })
    // 仅挂载时订阅；load 在回调里读最新闭包（setRecords 稳定）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const typeLabel = (type: ChangeItem['type']) => t(`resume.suggestionTypes.${type}` as never)
  const statusLabel = (s: ChangeStatus) => t(`resume.changeStatus.${STATUS_KEYS[s]}` as never)
  // 决策在前（跨版本约束，最该被看见），整改在后；各自保持记录 id 原序。
  const decisions = records.filter((r) => r.kind === 'decision')
  const changes = records.filter((r) => r.kind === 'change')

  const renderRecord = (r: ChangeRecord) => (
    <div key={r.id} className={`decision-record${isSettled(r) ? ' settled' : ''}`}>
      <div className="decision-record-head">
        <span className={`decision-record-status st-${r.status}`}>{statusLabel(r.status)}</span>
        <span className="decision-record-reason" title={r.reason}>{r.reason}</span>
        {isSettled(r) && <span className="decision-record-version">#{r.resolved_in_document_id}</span>}
      </div>
      {r.changes.map((c) => (
        <div key={c.id} className="decision-change">
          <span className="decision-change-type">{typeLabel(c.type)}</span>
          {c.original ? (
            <span className="decision-change-text">
              <span className="decision-change-original">{c.original}</span>
              <span className="decision-change-arrow">→</span>
              <span className="decision-change-suggested">{c.suggested}</span>
            </span>
          ) : (
            <span className="decision-change-text">{c.suggested}</span>
          )}
          <span className={`decision-change-status st-${c.status}`}>{statusLabel(c.status)}</span>
        </div>
      ))}
    </div>
  )

  const section = (title: string, rows: ChangeRecord[], empty: string) => (
    <div className="decision-section">
      <div className="decision-section-head">
        {title}
        <span className="decision-section-count">{rows.length}</span>
      </div>
      {rows.length === 0 ? <div className="decision-section-empty">{empty}</div> : rows.map(renderRecord)}
    </div>
  )

  return (
    <ToolTab icon={ScrollText} label={t('resume.decisionPanel')} onOpen={load} panelClassName="decision-panel">
      <div className="suggestion-panel-head">
        <span className="suggestion-panel-title">{t('resume.decisionPanel')}</span>
      </div>
      <BusyOverlay busy={busy} label={busyLabel} />
      <div className="decision-panel-body">
        {section(t('resume.decisionSectionDecisions'), decisions, t('resume.decisionEmptyDecisions'))}
        {section(t('resume.decisionSectionChanges'), changes, t('resume.decisionEmptyChanges'))}
      </div>
    </ToolTab>
  )
}
