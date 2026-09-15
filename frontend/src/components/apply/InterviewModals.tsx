import { useEffect, useState } from 'react'
import { Modal, Button, Group, Select, TextInput, Loader } from '@mantine/core'
import api from '@/lib/api'
import type { AnalysisReport, FollowupReason, Interview, InterviewOutcome, Job, JobBrief } from '@/types/apply'
import { toReportBlocks } from '@/types/report_block'
import { ReportBlocks } from '@/components/apply/ReportBlocks'
import { INTERVIEW_OUTCOME_LABELS, NOT_PURSUING_REASONS } from '@/lib/applyLabels'
import { useT } from '@/lib/i18n'

/**
 * 面试场次弹窗集合（ADR 0006）：
 * - 详情：时间/地点/轮次 + 岗位信息 + JD + AI 占位。
 * - 结果：选 outcome（终局 offered/failed 联动状态线）。
 * - 改期：选新时间（不早于今天）。
 * - 排面试：新建一场（时间/地点/轮次）。
 */

function fmtDateTime(iso: string): string {
  const d = new Date(iso)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${y}-${m}-${day} ${hh}:${mm}`
}

/** datetime-local 输入值（本地时区，YYYY-MM-DDTHH:mm）。 */
function toLocalInput(iso: string): string {
  const d = new Date(iso)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${y}-${m}-${day}T${hh}:${mm}`
}

/** datetime-local → ISO（带 Z，本地时间当 UTC 存）。 */
function fromLocalInput(v: string): string {
  return new Date(v).toISOString()
}

function nowLocalInput(): string {
  const d = new Date()
  return toLocalInput(d.toISOString())
}

function minToday(): string {
  const d = new Date()
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}T00:00`
}

/** 详情弹窗。 */
export function InterviewDetailModal({
  iv,
  job,
  onClose,
  onReschedule,
}: {
  iv: Interview
  job: JobBrief | undefined
  onClose: () => void
  onReschedule: (iv: Interview) => void
}) {
  const t = useT()
  // 单场面试准备（apply.md §11.7.2）：展开这场就拉一次后端分析（算一次存一次，后端缓存）。
  const [analysis, setAnalysis] = useState<AnalysisReport | null>(null)
  useEffect(() => {
    let alive = true
    const load = async () => {
      try {
        const { data } = await api.get<Record<string, unknown>>(`/v1/analysis/interview/${iv.id}`)
        if (!alive) return
        const status = String(data.status ?? 'ready')
        // 正文块流（单场是纯文本 → 后端给单个 text 块；兼容旧 body 键）。
        const blocks =
          toReportBlocks(data.blocks).length > 0
            ? toReportBlocks(data.blocks)
            : typeof data.body === 'string' && data.body.trim() !== ''
              ? [{ kind: 'text' as const, md: data.body }]
              : []
        setAnalysis({
          scope: 'interview',
          scopeKey: String(data.scope_key ?? iv.id),
          status: status as AnalysisReport['status'],
          headline: String(data.headline ?? ''),
          blocks,
          meta: {},
          createdAt: String(data.created_at ?? ''),
          suggestions: [],
        })
        // 还在算 → 1.5s 后再拉（与 applyStore 的轮询同节奏）
        if (status === 'computing') window.setTimeout(() => alive && void load(), 1500)
      } catch {
        // 分析拉取失败：不阻断详情弹窗（JD/时间仍可看）
      }
    }
    void load()
    return () => {
      alive = false
    }
  }, [iv.id])

  return (
    <Modal opened onClose={onClose} title={job ? `${job.company} · ${job.title}` : t('apply.ivDetail')} centered overlayProps={{ backgroundOpacity: 0.4, blur: 3 }}>
      <div className="iv-detail">
        <div className="iv-detail-meta">
          <Meta label={t('apply.ivTime')} value={fmtDateTime(iv.scheduledAt)} />
          <Meta label={t('apply.ivRound')} value={iv.round || '—'} />
          <Meta label={t('apply.ivLocation')} value={iv.location || '—'} />
          <Meta label={t('apply.ivResult')} value={INTERVIEW_OUTCOME_LABELS[iv.outcome]} />
        </div>
        <div className="iv-detail-jd">
          <div className="iv-detail-jd-title">{t('apply.ivJd')}</div>
          <div className="iv-detail-jd-body">{job?.description ? job.description : t('apply.ivNoJd')}</div>
        </div>
        {/* 单场准备（嘱咐，不是简历改动清单）——AI 占位升级为真实分析 */}
        <div className="iv-detail-ai">
          {!analysis || analysis.status === 'computing' ? (
            <span className="analysis-status">
              <Loader size="xs" />
              <span>{t('apply.analysisComputing')}</span>
            </span>
          ) : analysis.headline || analysis.blocks.length > 0 ? (
            <>
              {analysis.headline && <div className="analysis-headline">{analysis.headline}</div>}
              <ReportBlocks blocks={analysis.blocks} />
            </>
          ) : (
            t('apply.ivAiPlaceholder')
          )}
        </div>
        <Group justify="flex-end" mt="sm">
          <Button size="compact-sm" variant="default" onClick={() => onReschedule(iv)}>
            {t('apply.ivReschedule')}
          </Button>
        </Group>
      </div>
    </Modal>
  )
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <div className="iv-detail-meta-item">
      <span className="iv-detail-meta-label">{label}</span>
      <span className="iv-detail-meta-value">{value}</span>
    </div>
  )
}

/** 结果弹窗：选 outcome；终局联动由 store 处理。 */
export function InterviewResultModal({
  iv,
  onClose,
  onSubmit,
}: {
  iv: Interview
  onClose: () => void
  onSubmit: (outcome: InterviewOutcome, reason?: FollowupReason) => void
}) {
  const t = useT()
  const [outcome, setOutcome] = useState<InterviewOutcome>(iv.outcome)
  const [reason, setReason] = useState<FollowupReason>('failed')

  return (
    <Modal opened onClose={onClose} title={t('apply.ivResult')} centered overlayProps={{ backgroundOpacity: 0.4, blur: 3 }}>
      <div className="iv-modal-body">
        <Select
          label={t('apply.ivResult')}
          value={outcome}
          onChange={(v) => setOutcome(v as InterviewOutcome)}
          data={(Object.keys(INTERVIEW_OUTCOME_LABELS) as InterviewOutcome[]).map((o) => ({ value: o, label: INTERVIEW_OUTCOME_LABELS[o] }))}
        />
        {outcome === 'failed' && (
          <Select
            label={t('apply.ivFailedReason')}
            value={reason}
            onChange={(v) => setReason(v as FollowupReason)}
            data={NOT_PURSUING_REASONS.filter((r) => r.value === 'failed' || r.value === 'job_closed' || r.value === 'withdrawn').map((r) => ({ value: r.value, label: r.label }))}
          />
        )}
        <Group justify="flex-end" mt="sm">
          <Button
            size="compact-sm"
            color="green"
            onClick={() => onSubmit(outcome, outcome === 'failed' ? reason : undefined)}
          >
            {t('apply.markConfirm')}
          </Button>
        </Group>
      </div>
    </Modal>
  )
}

/** 改期弹窗：新时间不早于今天。 */
export function InterviewRescheduleModal({
  iv,
  onClose,
  onSubmit,
}: {
  iv: Interview
  onClose: () => void
  onSubmit: (scheduledAt: string) => void
}) {
  const t = useT()
  const [value, setValue] = useState<string>(toLocalInput(iv.scheduledAt))

  return (
    <Modal opened onClose={onClose} title={t('apply.ivRescheduleTitle')} centered overlayProps={{ backgroundOpacity: 0.4, blur: 3 }}>
      <div className="iv-modal-body">
        <TextInput label={t('apply.ivScheduleTime')} type="datetime-local" value={value} min={minToday()} onChange={(e) => setValue(e.currentTarget.value)} />
        <Group justify="flex-end" mt="sm">
          <Button size="compact-sm" color="green" onClick={() => onSubmit(fromLocalInput(value))}>
            {t('apply.ivRescheduleConfirm')}
          </Button>
        </Group>
      </div>
    </Modal>
  )
}

/** 排面试弹窗（新建一场；已投递 tab 的「面试」按钮触发，Q12）。 */
export function ScheduleInterviewModal({
  job,
  onClose,
  onSubmit,
}: {
  job: Job
  onClose: () => void
  onSubmit: (input: { scheduledAt: string; location: string; round: string }) => void
}) {
  const t = useT()
  const [time, setTime] = useState<string>(nowLocalInput())
  const [location, setLocation] = useState('')
  const [round, setRound] = useState('')

  return (
    <Modal opened onClose={onClose} title={`${t('apply.ivScheduleTitle')} · ${job.company} ${job.title}`} centered overlayProps={{ backgroundOpacity: 0.4, blur: 3 }}>
      <div className="iv-modal-body">
        <TextInput label={t('apply.ivScheduleTime')} type="datetime-local" value={time} min={minToday()} onChange={(e) => setTime(e.currentTarget.value)} />
        <TextInput label={t('apply.ivScheduleLocation')} value={location} onChange={(e) => setLocation(e.currentTarget.value)} />
        <TextInput label={t('apply.ivScheduleRound')} value={round} onChange={(e) => setRound(e.currentTarget.value)} />
        <Group justify="flex-end" mt="sm">
          <Button size="compact-sm" color="green" onClick={() => onSubmit({ scheduledAt: fromLocalInput(time), location, round })}>
            {t('apply.ivScheduleConfirm')}
          </Button>
        </Group>
      </div>
    </Modal>
  )
}
