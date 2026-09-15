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
import type { Suggestion } from '@/types/resume'

/**
 * 优化点面板（四栏，docs/design/resume.md §12.3，2026-08-12 升级）。
 *
 * 建议组已落库（optimization_pending 完整状态机），本面板是唯一操作入口：
 * - 左栏：待定（pending）——每条**默认展开**（统一建议卡 + 三按钮横排）。
 * - 右栏上：已确认（confirmed）——「开始改」应用这批。
 * - 右栏中：正在聊（discussing）——agent 讨论中。
 * - 右栏下：已拒绝（rejected）——版本内可撤回。
 *
 * 2026-08-13 改动：
 * - **左右 1:1 布局**：左待定 / 右三态组等宽（`.suggestion-col-group` flex 2.2 → 1）。
 * - **统一建议卡** `SuggestionCard`：左右同构（type + target + 原文划线 → 新改法），治"左右显示不一致"。
 * - **删头部总数 pill**（分栏后每栏已有计数）。
 *
 * 既有行为（2026-08-12）：
 * - 四栏（新增「已拒绝」，灰色半透明背景；版本内可撤回）。
 * - 每条右三栏有「撤回」按钮 → 回待定。
 * - 待定项默认展开（不再点击才展开）。
 * - 「开始改」门控：有待定 → 禁用；有 discussing 未结论 → 点击弹确认。
 * - 角标优先级级联：红=pending数 / 蓝=discussing数 / 绿=confirmed数 / 全0无。
 * - 事件驱动刷新（挂载 + resume-data-changed 事件 + 展开时）。
 */

interface PanelSuggestion extends Suggestion {
  id: number
  status: 'pending' | 'confirmed' | 'rejected' | 'discussing'
  split_from?: number | null
}

interface PanelData {
  pending: PanelSuggestion[]
  confirmed: PanelSuggestion[]
  discussing: PanelSuggestion[]
  rejected: PanelSuggestion[]
}

/** 角标优先级级联（2026-08-12）：待定>正在聊>已确认；全0 无角标。 */
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
  const [leaving, setLeaving] = useState<number | null>(null)
  const [entering, setEntering] = useState<number | null>(null)
  /** 换栏后目标栏计数 pulse（确认微反馈）：记录刚接收卡片的栏名。 */
  const [countPulse, setCountPulse] = useState<string | null>(null)
  /** 换栏两段式：先播源栏淡出，再发请求换栏，再播目标栏淡入 + 目标栏计数 pulse。 */
  const transitionMove = async (id: number, targetCol: string, action: () => Promise<unknown>) => {
    setLeaving(id)
    await new Promise((r) => setTimeout(r, 180)) // 与 CSS .leaving 过渡时长一致
    try {
      await action()
      await load()
    } finally {
      setLeaving(null)
      // 目标栏淡入：下一帧触发（让新卡片先以 entering 初态挂载），随后清除
      requestAnimationFrame(() => setEntering(id))
      setTimeout(() => setEntering(null), 240)
      // 目标栏计数 pulse：与淡入同步，0.4s 后清除
      setCountPulse(targetCol)
      setTimeout(() => setCountPulse(null), 420)
    }
  }
  // S7 统一 busy（2026-08-14）：busy = generating || applying。建议面板是「写建议状态」
  // 的操作点——无论用户点 apply 还是 agent 在跑（生成中 agent 可能 decide/propose 改建议），
  // 都要锁。busy 时：建议操作按钮 disabled + 浮窗盖毛玻璃（BusyOverlay）。
  const applying = useResumeStore((s) => s.applying)
  const generating = useResumeStore((s) => s.generating)
  const busy = applying || generating
  const busyLabel = applying ? t('resume.busyApplying') : t('resume.busyAgentWorking')
  const applySuggestions = useResumeStore((s) => s.applySuggestions)

  /**
   * 拉取建议面板四栏。供挂载 / 事件刷新 / ToolTab onOpen（展开时）调用。
   * 注意：不能用「useCallback([t]) + useEffect([load])」组合——useT() 每次渲染返回新
   * 闭包 → load 每次变 → effect 每次重跑 → 死循环（曾把后端打爆，SuggestionBasket 事故）。
   * 加载改为：挂载一次 + 事件驱动 + 展开手动刷新。
   */
  const load = async () => {
    try {
      const { data } = await api.get<PanelData>('/v1/optimization/pending')
      setPanel(data)
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

  /** 接受：pending/discussing → confirmed（去重：已定论后端兜底拒绝）。 */
  const accept = async (id: number) => {
    try {
      await transitionMove(id, 'confirmed', () => api.post('/v1/optimization/accept', { suggestion_id: id }))
    } catch (err) {
      notifyError(err)
    }
  }

  /** 拒绝：pending/discussing → rejected（灰栏，版本内可撤回；偏好延迟到版本变更记）。 */
  const reject = async (id: number) => {
    try {
      await transitionMove(id, 'rejected', () => api.post('/v1/optimization/reject', { suggestion_id: id, reason: '' }))
    } catch (err) {
      notifyError(err)
    }
  }

  /** 聊一聊：pending → discussing + 提示去聊天框提问。 */
  const discuss = async (id: number) => {
    try {
      await transitionMove(id, 'discussing', () => api.post('/v1/optimization/discuss', { suggestion_id: id }))
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
  const retract = async (id: number) => {
    try {
      await api.post('/v1/optimization/retract', { suggestion_id: id })
      await load()
    } catch (err) {
      notifyError(err)
    }
  }

  /** 开始改：门控——有待定禁用；有 discussing 弹确认（保留聊一聊，2026-08-12）。 */
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
   * S7：走 store.applySuggestions（统一 applying 锁定 + 出预览），成功后本地刷新面板四栏 + 提示去预览确认。 */
  const doApply = async () => {
    const appliedCount = panel.confirmed.length
    await applySuggestions()
    // 出预览后：面板建议仍在（confirm 才结清）；提示用户去左侧预览确认。
    notifications.show({
      color: 'green',
      title: t('resume.suggestionApply'),
      message: t('resume.notify.suggestionPreviewReady').replace('{n}', String(appliedCount)),
    })
    await load()
  }

  /** 统一失败发声：red toast + 面板标题 + 后端契约文案（本组件 6 处 catch 全走这里）。 */
  const notifyError = (err: unknown) => {
    notifications.show({ color: 'red', title: t('resume.suggestionBasket'), message: userErrorText(err) })
  }

  const typeLabel = (type: PanelSuggestion['type']) => t(`resume.suggestionTypes.${type}` as never)
  const badge = computeBadge(panel)
  // 「开始改」门控（2026-08-24）：
  // - 还有待定 → 禁用（必须先处理完待定）；
  // - 没有已确认 → 禁用（没有可改的，点了也白点）；
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

      {/* 2026-08-24：空态也渲染四栏骨架（四块淡色半透明分栏 + 各自空文案），
          不再只显示一行大字——结构常驻，用户一眼看清四栏是干嘛的。 */}
      <div className="suggestion-panel-body">
          {/* 左栏组：待定（3）+ 已拒绝（1），上下堆叠（2026-08-13：已拒绝挪到左下，右栏腾空间） */}
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
                  {panel.pending.map((s) => (
                    <div key={s.id} className={`suggestion-card-wrap${leaving === s.id ? ' leaving' : ''}${entering === s.id ? ' entering' : ''}`}>
                      {/* 待定：默认展开，可手动收起 */}
                      <SuggestionCard suggestion={s} typeLabel={typeLabel} collapsible>
                        {/* 三动作（2026-08-24 决策：面板内一律紧凑纯图标 + Tooltip 释义，去文字去"重"按钮）。
                            颜色 = 目标栏色：拒绝→灰、接受→绿、聊一聊→蓝；hover Tooltip 说明含义。 */}
                        <Tooltip label={t('resume.suggestionReject')} withArrow>
                          <Button className="btn-icon btn-icon-sm" variant="light" color="gray" size="compact-sm" aria-label={t('resume.suggestionReject')} onClick={() => reject(s.id)} disabled={busy}>
                            <X size={14} />
                          </Button>
                        </Tooltip>
                        <Tooltip label={t('resume.suggestionAccept')} withArrow>
                          <Button className="btn-icon btn-icon-sm" variant="light" color="green" size="compact-sm" aria-label={t('resume.suggestionAccept')} onClick={() => accept(s.id)} disabled={busy}>
                            <Check size={14} />
                          </Button>
                        </Tooltip>
                        <Tooltip label={t('resume.suggestionDiscuss')} withArrow>
                          <Button className="btn-icon btn-icon-sm" variant="light" color="blue" size="compact-sm" aria-label={t('resume.suggestionDiscuss')} onClick={() => discuss(s.id)} disabled={busy}>
                            <MessageCircle size={14} />
                          </Button>
                        </Tooltip>
                      </SuggestionCard>
                    </div>
                  ))}
                </div>
              )}
            </div>

            <SettledColumn
              variant="rejected" dotClass="dot-gray" titleKey="resume.suggestionRejected" emptyKey="resume.suggestionRejectedEmpty"
              items={panel.rejected} pulse={countPulse === 'rejected'}
              typeLabel={typeLabel} onRetract={retract} busy={busy} leaving={leaving} entering={entering}
            />
          </div>

          {/* 右栏组：已确认（淡绿）/ 正在聊（淡蓝），1:1（2026-08-13：已拒绝挪走后右栏只剩两栏，更宽不挤） */}
          <div className="suggestion-col-group suggestion-col-right">
            <SettledColumn
              variant="confirmed" dotClass="dot-green" titleKey="resume.suggestionConfirmed" emptyKey="resume.suggestionConfirmedEmpty"
              items={panel.confirmed} pulse={countPulse === 'confirmed'}
              typeLabel={typeLabel} onRetract={retract} busy={busy} leaving={leaving} entering={entering}
            />

            <SettledColumn
              variant="discussing" dotClass="dot-blue" titleKey="resume.suggestionDiscussing" emptyKey="resume.suggestionDiscussingEmpty"
              items={panel.discussing} pulse={countPulse === 'discussing'}
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

/** 已定论栏（右三栏共用）：栏头（色点 + 标题 + 计数）+ 空态 + 卡片列表。
 *  三栏此前是逐字复制的同一段 JSX，只有 variant/色点/文案/数据源不同。 */
function SettledColumn({
  variant,
  dotClass,
  titleKey,
  emptyKey,
  items,
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
  items: PanelSuggestion[]
  pulse: boolean
  typeLabel: (type: PanelSuggestion['type']) => string
  onRetract: (id: number) => void
  busy: boolean
  leaving: number | null
  entering: number | null
}) {
  const t = useT()
  return (
    <div className={`suggestion-col suggestion-col-${variant}`}>
      <div className="suggestion-col-title">
        <span className={`suggestion-col-dot ${dotClass}`} />
        {t(titleKey as never)}
        <span className={`suggestion-col-count${pulse ? ' pulse' : ''}`}>{items.length}</span>
      </div>
      {items.length === 0 ? (
        <div className="suggestion-col-empty">{t(emptyKey as never)}</div>
      ) : (
        <div className="suggestion-list suggestion-list-right">
          {items.map((s) => (
            <SettledRow key={s.id} s={s} typeLabel={typeLabel} onRetract={onRetract} disabled={busy} leaving={leaving === s.id} entering={entering === s.id} />
          ))}
        </div>
      )}
    </div>
  )
}

/** 右栏已定论建议行（已确认/正在聊/已拒绝共用）：统一建议卡 + 「撤回」。
 * 默认收起（grill 2026-08-13），可手动点开；收起态 = 两行（target + 改法截断）。
 * 撤回常驻卡头（headActions，折叠按钮旁）——收起/展开都能直接撤回，不再藏在展开区。
 * S7（2026-08-14）：apply 期间禁用撤回（锁写动作，防新建议状态溜进改写窗口）。 */
function SettledRow({
  s,
  typeLabel,
  onRetract,
  disabled,
  leaving,
  entering,
}: {
  s: PanelSuggestion
  typeLabel: (type: PanelSuggestion['type']) => string
  onRetract: (id: number) => void
  disabled?: boolean
  leaving?: boolean
  entering?: boolean
}) {
  return (
    <div className={`suggestion-card-wrap${leaving ? ' leaving' : ''}${entering ? ' entering' : ''}`}>
      <SuggestionCard
        suggestion={s}
        typeLabel={typeLabel}
        collapsible
        defaultCollapsed
        headActions={
          <Button
            variant="subtle"
            size="compact-xs"
            className="suggestion-retract"
            aria-label="撤回"
            title="撤回"
            onClick={() => onRetract(s.id)}
            disabled={disabled}
          >
            <Undo2 size={13} />
          </Button>
        }
      />
    </div>
  )
}
