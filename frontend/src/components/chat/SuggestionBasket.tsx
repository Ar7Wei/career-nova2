import { useEffect, useState } from 'react'
import { Sparkles, Wand2, MessageCircle, Check, X, Undo2 } from 'lucide-react'
import { Button, Tooltip } from '@mantine/core'
import { modals } from '@mantine/modals'
import { notifications } from '@mantine/notifications'
import api from '@/lib/api'
import { on } from '@/lib/events'
import { userErrorText } from '@/lib/errors'
import { useT } from '@/lib/i18n'
import { useResumeStore } from '@/stores/resumeStore'
import { ToolTab, type ToolBadge } from '@/components/ToolTab'
import { BusyOverlay } from '@/components/BusyOverlay'
import { SuggestionCard } from './SuggestionCard'
import type { ChangeItem, ChangeRecord, ChangeStatus } from '@/types/resume'

/**
 * 优化点面板（四栏，docs/design/resume.md §12.3）。
 *
 * **2026-09-23 迁到 change_records（一张表）**：优化点 = 改动记录（`reason` 原因 + `changes`
 * 改动点，各自带 status）。取代旧的扁平建议表——「优化点和改进记录本就是一回事，靠状态切换」。
 *
 * - 读 `GET /optimization/records` → `ChangeRecord[]`；**按子项状态**分入四栏（操作粒度 = 单条子项）。
 * - **栏内按「原因」分组**：一个原因下 N 个改动点，只写一次原因（旧结构是每条各抄一遍 reason）。
 * - 只收**带子项**的记录：`kind=decision` 且无子项的是纯跨版本约束，不给用户看（后端仍返回）。
 * - 左栏：待定（pending）——每条**默认展开**（卡片 + 三按钮横排）。
 * - 右栏：已确认（confirmed）/ 正在聊（discussing）/ 已拒绝（rejected，版本内可撤回）。
 *
 * 既有行为（2026-08-12 起沿用）：
 * - 每条右三栏有「撤回」按钮 → 回待定。
 * - 「开始改」门控：有待定 → 禁用；有 discussing 未结论 → 点击弹确认；无已确认 → 禁用。
 * - 角标优先级级联：红=pending 数 / 蓝=discussing 数 / 绿=confirmed 数 / 全 0 无。
 * - 事件驱动刷新（挂载 + resume-data-changed 事件 + 展开时）。
 */

/** 面板的一行 = 「某条记录里的某个改动点」（子项级）。 */
interface PanelRow {
  record: ChangeRecord
  change: ChangeItem
}

/** 行标识：记录 id + 子项 id 二元组。 */
const rowKey = (r: PanelRow) => `${r.record.id}.${r.change.id}`

/** 按子项状态把记录摊平成四栏。**只收带子项的记录**——纯决策约束不上用户面板。 */
function derivePanel(records: ChangeRecord[]) {
  const rows: PanelRow[] = []
  for (const record of records) {
    if (record.changes.length === 0) continue // kind=decision 的空壳：跨版本约束，不是优化点
    for (const change of record.changes) rows.push({ record, change })
  }
  const pick = (status: ChangeStatus) => rows.filter((r) => r.change.status === status)
  return {
    pending: pick('pending'),
    confirmed: pick('confirmed'),
    discussing: pick('discussing'),
    rejected: pick('rejected'),
  }
}

type PanelData = ReturnType<typeof derivePanel>

/** 栏内按「原因」分组（保持记录内子项的原序）。 */
function groupByReason(rows: PanelRow[]): { reason: string; rows: PanelRow[] }[] {
  const groups = new Map<string, PanelRow[]>()
  for (const r of rows) {
    const key = r.record.reason || '简历改进'
    const bucket = groups.get(key)
    if (bucket) bucket.push(r)
    else groups.set(key, [r])
  }
  return [...groups.entries()].map(([reason, groupRows]) => ({ reason, rows: groupRows }))
}

/** 角标优先级级联：待定>正在聊>已确认；全0 无角标（口径 = 子项数）。 */
function computeBadge(panel: PanelData): ToolBadge | null {
  if (panel.pending.length > 0) return { count: panel.pending.length, color: 'red' }
  if (panel.discussing.length > 0) return { count: panel.discussing.length, color: 'blue' }
  if (panel.confirmed.length > 0) return { count: panel.confirmed.length, color: 'green' }
  return null
}

export function SuggestionBasket() {
  const t = useT()
  const [panel, setPanel] = useState<PanelData>({ pending: [], confirmed: [], discussing: [], rejected: [] })
  // 四栏换栏两段式微反馈（2026-08-18 批次 4 §9.4）：leaving=源栏卡片淡出+微下移中，
  // entering=目标栏卡片淡入+微上移（load 完成后下一帧触发，~180ms 后清除）。
  const [leaving, setLeaving] = useState<string | null>(null)
  const [entering, setEntering] = useState<string | null>(null)
  /** 换栏后目标栏计数 pulse（确认微反馈）：记录刚接收卡片的栏名。 */
  const [countPulse, setCountPulse] = useState<string | null>(null)
  /** 换栏两段式：先播源栏淡出，再发请求换栏，再播目标栏淡入 + 目标栏计数 pulse。 */
  const transitionMove = async (key: string, targetCol: string, action: () => Promise<unknown>) => {
    setLeaving(key)
    await new Promise((r) => setTimeout(r, 180)) // 与 CSS .leaving 过渡时长一致
    try {
      await action()
      await load()
    } finally {
      setLeaving(null)
      // 目标栏淡入：下一帧触发（让新卡片先以 entering 初态挂载），随后清除
      requestAnimationFrame(() => setEntering(key))
      setTimeout(() => setEntering(null), 240)
      // 目标栏计数 pulse：与淡入同步，0.4s 后清除
      setCountPulse(targetCol)
      setTimeout(() => setCountPulse(null), 420)
    }
  }
  // S7 统一 busy（2026-08-14）：busy = generating || applying。优化点面板是「写状态」的操作点——
  // 无论用户点 apply 还是 agent 在跑，都要锁。busy 时：操作按钮 disabled + 浮窗盖毛玻璃。
  // 2026-09-24：回滚正在整份换改动记录，面板必须锁（否则改动会被恢复覆盖掉）。
  const applying = useResumeStore((s) => s.applying)
  const generating = useResumeStore((s) => s.generating)
  const rollingBack = useResumeStore((s) => s.rollingBack)
  const busy = applying || generating || rollingBack
  const busyLabel = rollingBack ? t('resume.rollingBack') : applying ? t('resume.busyApplying') : t('resume.busyAgentWorking')
  const applySuggestions = useResumeStore((s) => s.applySuggestions)

  /**
   * 拉取优化点（改动记录）。供挂载 / 事件刷新 / ToolTab onOpen（展开时）调用。
   * 注意：不能用「useCallback([t]) + useEffect([load])」组合——useT() 每次渲染返回新
   * 闭包 → load 每次变 → effect 每次重跑 → 死循环（曾把后端打爆，SuggestionBasket 事故）。
   */
  const load = async () => {
    try {
      const { data } = await api.get<{ records: ChangeRecord[] }>('/v1/optimization/records')
      setPanel(derivePanel(data.records))
    } catch (err) {
      notifyError(err)
    }
  }
  // 挂载拉一次 + 订阅跨组件数据变更（确认抽取/发消息后刷新角标）
  useEffect(() => {
    void load()
    return on('resume-data-changed', () => {
      void load()
    })
    // 仅挂载时订阅；load 用 ref 最新值（on 回调里调用的是当前闭包，setPanel 稳定）
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  /** 子项级状态迁移（四栏互转的唯一写口）：POST /record/status。 */
  const moveItem = async (row: PanelRow, status: ChangeStatus, targetCol: string) =>
    transitionMove(rowKey(row), targetCol, () =>
      api.post('/v1/optimization/record/status', {
        record_id: row.record.id,
        change_id: row.change.id,
        status,
      }),
    )

  /** 接受：→ confirmed（去重由后端按子项状态保证）。 */
  const accept = async (row: PanelRow) => {
    try {
      await moveItem(row, 'confirmed', 'confirmed')
    } catch (err) {
      notifyError(err)
    }
  }

  /** 拒绝：→ rejected（灰栏，版本内可撤回；偏好延迟到版本变更记）。 */
  const reject = async (row: PanelRow) => {
    try {
      await moveItem(row, 'rejected', 'rejected')
    } catch (err) {
      notifyError(err)
    }
  }

  /** 聊一聊：→ discussing + 提示去聊天框提问。 */
  const discuss = async (row: PanelRow) => {
    try {
      await moveItem(row, 'discussing', 'discussing')
      notifications.show({
        color: 'green',
        title: t('resume.suggestionBasket'),
        message: t('resume.suggestionDiscussHint'),
      })
    } catch (err) {
      notifyError(err)
    }
  }

  /** 撤回：confirmed/discussing/rejected → pending（右三栏每条，2026-08-12）。 */
  const retract = async (row: PanelRow) => {
    try {
      await api.post('/v1/optimization/record/status', {
        record_id: row.record.id,
        change_id: row.change.id,
        status: 'pending',
      })
      await load()
    } catch (err) {
      notifyError(err)
    }
  }

  /** 开始改：门控——有待定子项禁用；有 discussing 弹确认（保留聊一聊，2026-08-12）。 */
  const apply = async () => {
    if (panel.pending.length > 0) return // 门控 1：有待定，禁用（按钮已 disabled，双保险）
    if (panel.discussing.length > 0) {
      // 门控 2：有未结论的聊一聊 → 弹确认（保留到下一版本继续聊）
      modals.openConfirmModal({
        title: t('resume.suggestionApply'),
        centered: true,
        children: <p style={{ whiteSpace: 'pre-wrap' }}>{t('resume.suggestionApplyDiscussing').replace('{n}', String(panel.discussing.length))}</p>,
        labels: { confirm: t('common.confirm'), cancel: t('common.cancel') },
        confirmProps: { color: 'green' },
        onConfirm: () => {
          void doApply()
        },
      })
      return
    }
    void doApply()
  }

  /** 实际应用（ADR 0015）：confirmed 折进统一图 → 出预览态，用户在预览确认才落库。
   * S7：走 store.applySuggestions（统一 applying 锁定 + 出预览），成功后本地刷新 + 提示去预览确认。 */
  const doApply = async () => {
    const appliedCount = panel.confirmed.length
    await applySuggestions()
    // 出预览后：改动记录仍在（confirm 才结清）；提示用户去左侧预览确认。
    notifications.show({
      color: 'green',
      title: t('resume.suggestionApply'),
      message: t('resume.notify.suggestionPreviewReady').replace('{n}', String(appliedCount)),
    })
    await load()
  }

  /** 统一失败发声：red toast + 面板标题 + 后端契约文案（本组件 catch 全走这里）。 */
  const notifyError = (err: unknown) => {
    notifications.show({ color: 'red', title: t('resume.suggestionBasket'), message: userErrorText(err) })
  }

  const typeLabel = (type: ChangeItem['type']) => t(`resume.suggestionTypes.${type}` as never)
  const badge = computeBadge(panel)
  // 「开始改」门控（2026-08-24，口径 = 子项）：
  // - 还有待定子项 → 禁用（必须先处理完待定）；
  // - 没有已确认子项 → 禁用（没有可改的，点了也白点）；
  // - busy → 禁用（改写进行中）。
  const applyDisabled = panel.pending.length > 0 || panel.confirmed.length === 0 || busy

  return (
    <ToolTab
      icon={Sparkles}
      label={t('resume.suggestionBasket')}
      badge={badge}
      onOpen={load}
      panelClassName="suggestion-panel"
    >
      <div className="suggestion-panel-head">
        <span className="suggestion-panel-title">{t('resume.suggestionBasket')}</span>
      </div>
      <BusyOverlay busy={busy} label={busyLabel} />

      {/* 空态也渲染四栏骨架（四块淡色半透明分栏 + 各自空文案），结构常驻。 */}
      <div className="suggestion-panel-body">
          {/* 左栏组：待定 + 已拒绝，上下堆叠（2026-08-13：已拒绝挪到左下，右栏腾空间） */}
          <div className="suggestion-col-group suggestion-col-left">
            <div className="suggestion-col suggestion-col-pending">
              <div className="suggestion-col-title">
                <span className={`suggestion-col-dot dot-red`} />
                {t('resume.suggestionPending')}
                <span className="suggestion-col-count">{panel.pending.length}</span>
              </div>
              {panel.pending.length === 0 ? (
                <div className="suggestion-col-empty">{t('resume.suggestionPendingEmpty')}</div>
              ) : (
                <div className="suggestion-list">
                  {groupByReason(panel.pending).map((group) => (
                    <div key={group.reason} className="suggestion-group">
                      <div className="suggestion-group-head" title={group.reason}>{group.reason}</div>
                      {group.rows.map((row) => {
                        const key = rowKey(row)
                        return (
                          <div key={key} className={`suggestion-card-wrap${leaving === key ? ' leaving' : ''}${entering === key ? ' entering' : ''}`}>
                            {/* 待定：默认展开，可手动收起。原因已提到分组头，卡内不再重复。 */}
                            <SuggestionCard suggestion={{ ...row.change, reason: '' }} typeLabel={typeLabel} collapsible>
                              {/* 三动作（2026-08-24：面板内一律紧凑纯图标 + Tooltip 释义）。
                                  颜色 = 目标栏色：拒绝→灰、接受→绿、聊一聊→蓝。 */}
                              <Tooltip label={t('resume.suggestionReject')} withArrow>
                                <Button className="btn-icon btn-icon-sm" variant="light" color="gray" size="compact-sm" aria-label={t('resume.suggestionReject')} onClick={() => reject(row)} disabled={busy}>
                                  <X size={14} />
                                </Button>
                              </Tooltip>
                              <Tooltip label={t('resume.suggestionAccept')} withArrow>
                                <Button className="btn-icon btn-icon-sm" variant="light" color="green" size="compact-sm" aria-label={t('resume.suggestionAccept')} onClick={() => accept(row)} disabled={busy}>
                                  <Check size={14} />
                                </Button>
                              </Tooltip>
                              <Tooltip label={t('resume.suggestionDiscuss')} withArrow>
                                <Button className="btn-icon btn-icon-sm" variant="light" color="blue" size="compact-sm" aria-label={t('resume.suggestionDiscuss')} onClick={() => discuss(row)} disabled={busy}>
                                  <MessageCircle size={14} />
                                </Button>
                              </Tooltip>
                            </SuggestionCard>
                          </div>
                        )
                      })}
                    </div>
                  ))}
                </div>
              )}
            </div>

            <SettledColumn
              variant="rejected" dotClass="dot-gray" titleKey="resume.suggestionRejected" emptyKey="resume.suggestionRejectedEmpty"
              rows={panel.rejected} pulse={countPulse === 'rejected'}
              typeLabel={typeLabel} onRetract={retract} busy={busy} leaving={leaving} entering={entering}
            />
          </div>

          {/* 右栏组：已确认（淡绿）/ 正在聊（淡蓝），1:1 */}
          <div className="suggestion-col-group suggestion-col-right">
            <SettledColumn
              variant="confirmed" dotClass="dot-green" titleKey="resume.suggestionConfirmed" emptyKey="resume.suggestionConfirmedEmpty"
              rows={panel.confirmed} pulse={countPulse === 'confirmed'}
              typeLabel={typeLabel} onRetract={retract} busy={busy} leaving={leaving} entering={entering}
            />

            <SettledColumn
              variant="discussing" dotClass="dot-blue" titleKey="resume.suggestionDiscussing" emptyKey="resume.suggestionDiscussingEmpty"
              rows={panel.discussing} pulse={countPulse === 'discussing'}
              typeLabel={typeLabel} onRetract={retract} busy={busy} leaving={leaving} entering={entering}
            />
          </div>
        </div>

      <div className="suggestion-panel-foot">
        <Button
          onClick={apply}
          disabled={applyDisabled}
          leftSection={<Wand2 size={14} />}
          title={
            panel.pending.length > 0
              ? t('resume.suggestionApplyDisabledHint').replace('{n}', String(panel.pending.length))
              : panel.confirmed.length === 0
                ? t('resume.suggestionBasketEmpty')
                : undefined
          }
        >
          {applying ? t('resume.suggestionApplying') : t('resume.suggestionApply')}
        </Button>
      </div>
    </ToolTab>
  )
}

/** 已定论栏（右三栏共用）：栏头（色点 + 标题 + 计数）+ 空态 + 按原因分组的卡片列表。
 *  三栏此前是逐字复制的同一段 JSX，只有 variant/色点/文案/数据源不同。 */
function SettledColumn({
  variant,
  dotClass,
  titleKey,
  emptyKey,
  rows,
  pulse,
  typeLabel,
  onRetract,
  busy,
  leaving,
  entering,
}: {
  variant: 'confirmed' | 'discussing' | 'rejected'
  dotClass: string
  titleKey: string
  emptyKey: string
  rows: PanelRow[]
  pulse: boolean
  typeLabel: (type: ChangeItem['type']) => string
  onRetract: (row: PanelRow) => void
  busy: boolean
  leaving: string | null
  entering: string | null
}) {
  const t = useT()
  return (
    <div className={`suggestion-col suggestion-col-${variant}`}>
      <div className="suggestion-col-title">
        <span className={`suggestion-col-dot ${dotClass}`} />
        {t(titleKey as never)}
        <span className={`suggestion-col-count${pulse ? ' pulse' : ''}`}>{rows.length}</span>
      </div>
      {rows.length === 0 ? (
        <div className="suggestion-col-empty">{t(emptyKey as never)}</div>
      ) : (
        <div className="suggestion-list suggestion-list-right">
          {groupByReason(rows).map((group) => (
            <div key={group.reason} className="suggestion-group">
              <div className="suggestion-group-head" title={group.reason}>{group.reason}</div>
              {group.rows.map((row) => {
                const key = rowKey(row)
                return (
                  <SettledRow
                    key={key}
                    row={row}
                    typeLabel={typeLabel}
                    onRetract={onRetract}
                    disabled={busy}
                    leaving={leaving === key}
                    entering={entering === key}
                  />
                )
              })}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

/** 右栏已定论行（已确认/正在聊/已拒绝共用）：统一卡片 + 「撤回」。
 * 默认收起（grill 2026-08-13），可手动点开；收起态 = 两行（target + 改法截断）。
 * 撤回常驻卡头（headActions，折叠按钮旁）——收起/展开都能直接撤回。
 * S7（2026-08-14）：apply 期间禁用撤回（锁写动作，防新状态溜进改写窗口）。 */
function SettledRow({
  row,
  typeLabel,
  onRetract,
  disabled,
  leaving,
  entering,
}: {
  row: PanelRow
  typeLabel: (type: ChangeItem['type']) => string
  onRetract: (row: PanelRow) => void
  disabled?: boolean
  leaving?: boolean
  entering?: boolean
}) {
  const t = useT()
  return (
    <div className={`suggestion-card-wrap${leaving ? ' leaving' : ''}${entering ? ' entering' : ''}`}>
      <SuggestionCard
        suggestion={{ ...row.change, reason: '' }}
        typeLabel={typeLabel}
        collapsible
        defaultCollapsed
        headActions={
          <Button
            variant="subtle"
            size="compact-xs"
            className="suggestion-retract"
            aria-label={t('resume.suggestionRetract')}
            title={t('resume.suggestionRetract')}
            onClick={() => onRetract(row)}
            disabled={disabled}
          >
            <Undo2 size={13} />
          </Button>
        }
      />
    </div>
  )
}
