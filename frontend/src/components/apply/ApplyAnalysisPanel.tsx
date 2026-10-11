import { useEffect } from 'react'
import { Button, Loader } from '@mantine/core'
import { Sparkles } from 'lucide-react'
import { notifications } from '@mantine/notifications'
import { ToolTab } from '@/components/ToolTab'
import { ReportBlocks } from '@/components/apply/ReportBlocks'
import { PendingGroupList, type PanelRow } from '@/components/chat/PendingGroupList'
import { useApplyStore } from '@/stores/applyStore'
import { useT } from '@/lib/i18n'
import { userErrorText } from '@/lib/errors'
import type { StatusTab } from '@/stores/applyStore'

/**
 * 投递页分析面板（apply.md §11.7.1，2026-09-14）。
 *
 * 形态 = **顶部一行控件，点击展开**（与「投递时间选择器」同款位置与外观），**不是右栏**——
 * 固定右栏会暗示"分析默认开着、一直要参照"，而多数时候用户只瞥一眼列表。
 *
 * 三 tab 三套（§11.7.2）：未处理 = 适配度 + 改进方向（**处方**，可收录进优化点）｜
 * 已投递 = 投递日报（**嘱咐**，纯报告）｜面试 = 不做 tab 级分析（落单场详情弹窗）。
 * 录用 tab 不做。
 *
 * 算一次存一次（§11.7.5）：数据来自后端缓存，本组件只读 + 触发拉取 + 轮询「分析中」。
 */
export function ApplyAnalysisPanel({ tab }: { tab: StatusTab }) {
  const t = useT()
  const batch = useApplyStore((s) => s.analysis.batch)
  const daily = useApplyStore((s) => s.analysis.daily)
  const fetchBatch = useApplyStore((s) => s.fetchBatchAnalysis)
  const fetchDaily = useApplyStore((s) => s.fetchDailyAnalysis)
  const promote = useApplyStore((s) => s.promotePrescription)

  // 挂载/切 tab 拉一次：未处理看本轮批次（轮次由后端从 jobs 推，前端不记）、已投递看昨天日报。
  // 动作走 getState 引用，不进依赖数组（SuggestionBasket 同源教训：不稳定依赖会打爆后端）。
  useEffect(() => {
    if (tab === 'unprocessed') void fetchBatch()
    if (tab === 'applied') void fetchDaily()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab])

  const report = tab === 'unprocessed' ? batch : daily
  // 块流里有没有文字（有 headline 或有 text 块才算有内容——纯图块不算一份报告）。
  const hasText = (report?.blocks.some((b) => b.kind === 'text') ?? false) || Boolean(report?.headline)

  // 处方（change_records）→ 面板行（记录 + 子项）。分析面板只展示仍 pending 的子项。
  const prescriptionRows: PanelRow[] =
    tab === 'unprocessed' && report
      ? report.suggestions.flatMap((record) =>
          record.changes.filter((c) => c.status === 'pending').map((change) => ({ record, change })),
        )
      : []

  const onPromote = (recordId: number, changeId: number) => {
    void promote(recordId, changeId).catch((err) => {
      notifications.show({ color: 'red', title: t('apply.analysisTitle'), message: userErrorText(err) })
    })
  }

  return (
    <ToolTab
      align="left"
      panelClassName="analysis-panel"
      badge={
        report?.status === 'ready' && prescriptionRows.length > 0
          ? { count: prescriptionRows.length, color: 'green' }
          : null
      }
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
            {report.headline && <div className="analysis-headline">{report.headline}</div>}
            {/* 正文块流（文/图穿插）：text 块渲文字、chart 块走 ChartRenderer（坏 spec 静默跳过）。 */}
            <ReportBlocks blocks={report.blocks} />
            {/* 深度/概览标注（§11.7.4）——别让用户以为覆盖不全都一样深 */}
            {tab === 'unprocessed' && report.meta.total != null && (
              <div className="analysis-meta">
                {t('apply.analysisCoverage')
                  .replace('{total}', String(report.meta.total))
                  .replace('{withJd}', String(report.meta.withJd ?? 0))}
              </div>
            )}
            {tab === 'unprocessed' && prescriptionRows.length > 0 && (
              <div className="analysis-prescriptions">
                <div className="analysis-prescriptions-title">{t('apply.analysisPrescriptions')}</div>
                {/* 与优化点面板同款展示（按原因分组、默认展开可收起），动作区换成单个「改进」。 */}
                <PendingGroupList
                  rows={prescriptionRows}
                  typeLabel={(type) => t(`resume.suggestionTypes.${type}` as never)}
                  renderActions={(row) => (
                    <Button size="compact-xs" color="green" onClick={() => onPromote(row.record.id, row.change.id)}>
                      {t('apply.analysisImprove')}
                    </Button>
                  )}
                />
              </div>
            )}
            {tab === 'unprocessed' && prescriptionRows.length === 0 && (
              <div className="analysis-meta">{t('apply.analysisNoPrescription')}</div>
            )}
          </>
        )}
      </div>
    </ToolTab>
  )
}
