import { useEffect, useState } from 'react'
import { Button, Loader } from '@mantine/core'
import { modals } from '@mantine/modals'
import { notifications } from '@mantine/notifications'
import { Check, ChevronDown, ChevronRight, Sparkles, SlidersHorizontal, Trash2, X } from 'lucide-react'
import { ToolTab } from '@/components/ToolTab'
import { ReportBlocks } from '@/components/apply/ReportBlocks'
import { useApplyStore } from '@/stores/applyStore'
import { useT } from '@/lib/i18n'
import { userErrorText } from '@/lib/errors'
import type { StatusTab } from '@/stores/applyStore'

/**
 * 投递页分析面板（apply.md §11.7.1）。
 *
 * 形态 = **顶部一行控件，点击展开**（与「投递时间选择器」同款位置与外观），**不是右栏**——
 * 固定右栏会暗示"分析默认开着、一直要参照"，而多数时候用户只瞥一眼列表。
 *
 * 两个 tab 两套（§11.7.2）：未处理 = 供给分析报告（**你的门槛过几成 / 这批岗该扔哪些 / 检索词要不要调**）｜
 * 已投递 = 投递日报。面试不做 tab 级分析（落单场详情弹窗）。录用 tab 不做。
 *
 * 2026-10-11（ADR 0023）：批次分析**不再产出「改进简历」的处方**——投递分析在上游、只分析
 * 爬来的岗位供给本身（技能缺口属下游 JD 数据，投递前拿不到）。取而代之「**投递提案**」——
 * 报告之下一个**行动区**（人点确认才执行，与 dismiss/dispose-unprocessed 同构），三条：
 *   ① 剔除偏岗：列分析挑出的偏岗 → 一键落到跟进状态线（not_pursuing/withdrawn）。
 *   ② 收紧检索词：把诊断喂 refine → 打开检索控制台预填草稿 → 人复核 commit（不自动写方向）。
 *   ③ 先投最契合：一键把未处理列表按匹配度排序（报告行动区头部的「按匹配度」钮，2026-10-11 从工具栏搬来）。
 *   **提案有生命周期**（2026-10-11 补）：执行/忽略后落 `analysis_proposal_state`，本面板按
 *   proposalState 把动作钮换成「已剔除 / 已忽略」标记——报告是缓存的、内容不变，处置单存一张表。
 *
 * 算一次存一次（§11.7.5）：数据来自后端缓存，本组件只读 + 触发拉取 + 轮询「分析中」。
 */
export function ApplyAnalysisPanel({ tab }: { tab: StatusTab }) {
  const t = useT()
  const batch = useApplyStore((s) => s.analysis.batch)
  const daily = useApplyStore((s) => s.analysis.daily)
  const fetchBatch = useApplyStore((s) => s.fetchBatchAnalysis)
  const fetchDaily = useApplyStore((s) => s.fetchDailyAnalysis)
  const applyProposalDrop = useApplyStore((s) => s.applyProposalDrop)
  const dismissProposal = useApplyStore((s) => s.dismissProposal)
  const setRefineHint = useApplyStore((s) => s.setRefineHint)
  const setSort = useApplyStore((s) => s.setSort)
  const sortMatch = useApplyStore((s) => s.sortByTab.unprocessed === 'match')
  const [dropOpen, setDropOpen] = useState(false)

  // 挂载/切 tab 拉一次：未处理看本轮批次（轮次由后端从 jobs 推，前端不记）、已投递看昨天日报。
  // 动作走 getState 引用，不进依赖数组（SuggestionBasket 同源教训：不稳定依赖会打爆后端）。
  useEffect(() => {
    if (tab === 'unprocessed') void fetchBatch()
    if (tab === 'applied') void fetchDaily()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab])

  const report = tab === 'unprocessed' ? batch : daily
  // 块流里有没有文字（有 headline 或有 text 块才算有内容——纯 section/图块不算一份报告）。
  const hasText = (report?.blocks.some((b) => b.kind === 'text') ?? false) || Boolean(report?.headline)
  const dropJobs = tab === 'unprocessed' ? (report?.dropJobs ?? []) : []
  const refineHint = tab === 'unprocessed' ? (report?.refineHint ?? '') : ''
  const proposalState = report?.proposalState ?? {}
  const dropState = proposalState.drop ?? 'pending'
  const refineState = proposalState.refine ?? 'pending'
  const roundId = tab === 'unprocessed' ? Number(report?.scopeKey ?? 0) : 0
  // 行动区里有没有东西可给（剔除 / 收紧检索词 / 先投最契合）——有报告就至少有「先投最契合」。
  const hasActions = tab === 'unprocessed' && (dropJobs.length > 0 || Boolean(refineHint) || report?.status === 'ready')

  /** 剔除偏岗：确认 → 端点执行（服务端现读 id 落状态线 + 记提案态）→ 本地翻转 + 重拉。 */
  const confirmDrop = () => {
    if (dropJobs.length === 0) return
    modals.openConfirmModal({
      title: t('apply.proposalDropTitle'),
      children: <div className="direction-confirm"><p>{t('apply.proposalDropHint').replace('{n}', String(dropJobs.length))}</p></div>,
      labels: { confirm: t('apply.proposalDropConfirm'), cancel: t('common.cancel') },
      confirmProps: { color: 'red' },
      onConfirm: async () => {
        try {
          const n = await applyProposalDrop(roundId)
          notifications.show({
            color: 'green',
            title: t('apply.analysisTitle'),
            message: t('apply.proposalDropDone').replace('{n}', String(n)),
          })
        } catch (err) {
          notifications.show({ color: 'red', title: t('apply.analysisTitle'), message: userErrorText(err) })
        }
      },
    })
  }

  /** 忽略提案（drop / refine）：记 dismissed → 本地翻转。 */
  const doDismiss = async (kind: 'drop' | 'refine') => {
    try {
      await dismissProposal(roundId, kind)
    } catch (err) {
      notifications.show({ color: 'red', title: t('apply.analysisTitle'), message: userErrorText(err) })
    }
  }

  /** 按匹配度排序：一键把未处理列表按契合度排（报告行动区头部的钮）。 */
  const doReorder = () => setSort('match')

  return (
    <ToolTab
      align="left"
      panelClassName="analysis-panel"
      label={
        <span className="analysis-trigger-label">
          <Sparkles size={13} />
          <span>{t('apply.analysisTitle')}</span>
        </span>
      }
      onOpen={() => {
        // 展开时若还没数据/还在算，补一次拉取（幂等：有缓存即读、computing 会自行轮询）。
        if (tab === 'unprocessed' && !batch) void fetchBatch()
        if (tab === 'applied' && !daily) void fetchDaily()
      }}
    >
      {/* 面板标题：统一浮窗骨架 panel-head（2026-10-11 收敛补 header）；报告 headline 是内容标题，15px 保留。 */}
      <div className="panel-head">
        <span className="panel-title">{t('apply.analysisTitle')}</span>
      </div>
      <div className="analysis-body">
        {!report || report.status === 'computing' ? (
          <div className="analysis-status">
            <Loader size="xs" />
            <span>{t('apply.analysisComputing')}</span>
          </div>
        ) : report.status === 'missing' || !hasText ? (
          <div className="analysis-empty">{t('apply.analysisEmpty')}</div>
        ) : (
          <>
            {/* 卡片头：本批规模 + 分析日期 + 深度/概览覆盖（§11.7.4）——把报告和"工具提示"区分开。
                仅未处理批次有「本批 N 岗」；日报只有日期。 */}
            <div className="analysis-card-head">
              {tab === 'unprocessed' && report.meta.total != null && (
                <span className="analysis-card-head-main">
                  {t('apply.analysisBatchHead').replace('{n}', String(report.meta.total))}
                </span>
              )}
              {report.createdAt && <span className="analysis-card-head-date">{report.createdAt.slice(0, 10)}</span>}
              {tab === 'unprocessed' && report.meta.total != null && (
                <span className="analysis-card-head-coverage">
                  {t('apply.analysisCoverage')
                    .replace('{total}', String(report.meta.total))
                    .replace('{withJd}', String(report.meta.withJd ?? 0))}
                </span>
              )}
            </div>
            {report.headline && <div className="analysis-headline">{report.headline}</div>}
            {/* 正文块流（section/text/chart 穿插）：section 分节、text 渲 Markdown、chart 走 ChartRenderer。 */}
            <ReportBlocks blocks={report.blocks} />

            {/* 行动区（ADR 0023）：报告之下的可执行载体，人点确认才动。报告有就显示（至少给「按匹配度」钮）。 */}
            {hasActions && (
              <div className="analysis-proposal">
                {/* 行动区头部：标题 + 「按匹配度排序」钮（报告告诉你「先投哪些」→ 一键照做；
                    已按匹配度排时换成「已按匹配度排序」标记）。 */}
                <div className="analysis-proposal-title">
                  <span>{t('apply.proposalTitle')}</span>
                  {sortMatch ? (
                    <span className="analysis-proposal-done is-applied">
                      <Check size={13} /> {t('apply.proposalReorderDone')}
                    </span>
                  ) : (
                    <Button variant="light" size="compact-sm" leftSection={<Sparkles size={13} />} onClick={doReorder}>
                      {t('apply.proposalReorderApply')}
                    </Button>
                  )}
                </div>

                {dropJobs.length > 0 && (
                  <div className="analysis-proposal-item">
                    <div className="analysis-proposal-item-head">
                      <Button
                        variant="subtle"
                        className="analysis-drop-toggle"
                        leftSection={dropOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                        onClick={() => setDropOpen((v) => !v)}
                      >
                        {t('apply.proposalDropLabel').replace('{n}', String(dropJobs.length))}
                      </Button>
                      {dropState === 'pending' ? (
                        <span className="analysis-proposal-actions">
                          <Button color="red" variant="light" size="compact-sm" leftSection={<Trash2 size={13} />} onClick={confirmDrop}>
                            {t('apply.proposalDropApply')}
                          </Button>
                          <Button variant="subtle" size="compact-sm" leftSection={<X size={13} />} onClick={() => void doDismiss('drop')}>
                            {t('apply.proposalDismiss')}
                          </Button>
                        </span>
                      ) : (
                        <span className={`analysis-proposal-done${dropState === 'applied' ? ' is-applied' : ''}`}>
                          {dropState === 'applied' ? (
                            <><Check size={13} /> {t('apply.proposalDropApplied').replace('{n}', String(dropJobs.length))}</>
                          ) : (
                            <><X size={13} /> {t('apply.proposalDismissed')}</>
                          )}
                        </span>
                      )}
                    </div>
                    {dropOpen && (
                      <ul className="analysis-drop-list">
                        {dropJobs.map((d) => (
                          <li key={d.id}>
                            <span className="analysis-drop-title">{d.title}</span>
                            {d.company && <span className="analysis-drop-company"> @ {d.company}</span>}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                )}

                {refineHint && (
                  <div className="analysis-proposal-item">
                    <div className="analysis-proposal-item-head">
                      <span className="analysis-proposal-text">{refineHint}</span>
                      {refineState === 'pending' ? (
                        <span className="analysis-proposal-actions">
                          <Button
                            variant="light"
                            size="compact-sm"
                            leftSection={<SlidersHorizontal size={13} />}
                            onClick={() => setRefineHint(refineHint)}
                          >
                            {t('apply.proposalRefineApply')}
                          </Button>
                          <Button variant="subtle" size="compact-sm" leftSection={<X size={13} />} onClick={() => void doDismiss('refine')}>
                            {t('apply.proposalDismiss')}
                          </Button>
                        </span>
                      ) : (
                        <span className="analysis-proposal-done">
                          <X size={13} /> {t('apply.proposalDismissed')}
                        </span>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )}
          </>
        )}
      </div>
    </ToolTab>
  )
}
