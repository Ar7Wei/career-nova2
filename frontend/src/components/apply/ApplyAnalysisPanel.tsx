import { useEffect } from 'react'
import { Button, Loader } from '@mantine/core'
import { Sparkles } from 'lucide-react'
import { notifications } from '@mantine/notifications'
import { ToolTab } from '@/components/ToolTab'
import { ReportBlocks } from '@/components/apply/ReportBlocks'
import { SuggestionCard } from '@/components/chat/SuggestionCard'
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
export function ApplyAnalysisPanel({ tab, roundId }: { tab: StatusTab; roundId: number | null }) {
  const t = useT()
  const batch = useApplyStore((s) => s.analysis.batch)
  const daily = useApplyStore((s) => s.analysis.daily)
  const fetchBatch = useApplyStore((s) => s.fetchBatchAnalysis)
  const fetchDaily = useApplyStore((s) => s.fetchDailyAnalysis)
  const promote = useApplyStore((s) => s.promotePrescription)

  // 挂载/切 tab 拉一次：未处理看本轮批次、已投递看昨天日报。动作走 getState 引用，
  // 不进依赖数组（SuggestionBasket 同源教训：不稳定依赖会打爆后端）。
  useEffect(() => {
    if (tab === 'unprocessed' && roundId != null) void fetchBatch(roundId)
    if (tab === 'applied') void fetchDaily()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, roundId])

  const report = tab === 'unprocessed' ? batch : daily
  // 块流里有没有文字（有 headline 或有 text 块才算有内容——纯图块不算一份报告）。
  const hasText = (report?.blocks.some((b) => b.kind === 'text') ?? false) || Boolean(report?.headline)

  const onPromote = (id: number) => {
    void promote(id).catch((err) => {
      notifications.show({ color: 'red', title: t('apply.analysisTitle'), message: userErrorText(err) })
    })
  }

  return (
    <ToolTab
      align="left"
      panelClassName="analysis-panel"
      label={
        <span className="analysis-trigger-label">
          <Sparkles size={13} />
          <span>{t('apply.analysisTitle')}</span>
          {report?.status === 'ready' && report.suggestions.length > 0 && (
            <span className="analysis-trigger-count">{report.suggestions.length}</span>
          )}
        </span>
      }
      onOpen={() => {
        // 展开时若还没数据/还在算，补一次拉取（幂等：有缓存即读、computing 会自行轮询）。
        if (tab === 'unprocessed' && roundId != null && !batch) void fetchBatch(roundId)
        if (tab === 'applied' && !daily) void fetchDaily()
      }}
    >
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
            {tab === 'unprocessed' && report.suggestions.length > 0 && (
              <div className="analysis-prescriptions">
                <div className="analysis-prescriptions-title">{t('apply.analysisPrescriptions')}</div>
                {report.suggestions.map((s) => (
                  <SuggestionCard
                    key={s.id}
                    suggestion={s}
                    typeLabel={(type) => t(`resume.suggestionTypes.${type}` as never)}
                  >
                    <Button size="compact-xs" color="green" onClick={() => onPromote(s.id)}>
                      {t('apply.analysisImprove')}
                    </Button>
                  </SuggestionCard>
                ))}
              </div>
            )}
            {tab === 'unprocessed' && report.suggestions.length === 0 && (
              <div className="analysis-meta">{t('apply.analysisNoPrescription')}</div>
            )}
          </>
        )}
      </div>
    </ToolTab>
  )
}
