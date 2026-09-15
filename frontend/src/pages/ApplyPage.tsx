import { useEffect, useMemo, useState } from 'react'
import { ArrowUp, ArrowDown, Calendar, Tag } from 'lucide-react'
import { Badge, Button, Modal, Menu, Select, Tabs, TextInput, Group } from '@mantine/core'
import { useClickOutside } from '@mantine/hooks'
import { DayPicker, type DateRange as DayPickerRange } from 'react-day-picker'
import 'react-day-picker/style.css'
import { useApplyStore, type StatusTab } from '@/stores/applyStore'
import { ApplyAnalysisPanel } from '@/components/apply/ApplyAnalysisPanel'
import { ConsoleTrigger } from '@/components/apply/ConsoleTrigger'
import { InterviewCalendar } from '@/components/apply/InterviewCalendar'
import { InterviewDetailModal, InterviewRescheduleModal, InterviewResultModal, ScheduleInterviewModal } from '@/components/apply/InterviewModals'
import { JOB_SOURCE_LABELS, NOT_PURSUING_REASONS } from '@/lib/applyLabels'
import type { FollowupReason, FollowupStatus, Interview, InterviewOutcome, Job } from '@/types/apply'
import { useT } from '@/lib/i18n'

/** 左侧二级导航项（顺序 = 展示顺序）。 */
const NAV_ITEMS: { value: StatusTab; labelKey: string }[] = [
  { value: 'unprocessed', labelKey: 'apply.tabUnprocessed' },
  { value: 'applied', labelKey: 'apply.tabApplied' },
  { value: 'interviewing', labelKey: 'apply.tabInterviewing' },
  { value: 'offered', labelKey: 'apply.tabOffered' },
  { value: 'not_pursuing', labelKey: 'apply.tabNotPursuing' },
]

/** 关窗标记框：用户关掉投递窗口 → 主进程发事件 → 弹框标「投了/没投」。 */
function MarkAppliedModal({ job, onDone }: { job: Job; onDone: () => void }) {
  const t = useT()
  const pushFollowup = useApplyStore((s) => s.pushFollowup)
  const [choice, setChoice] = useState<'applied' | 'not_pursuing' | null>(null)
  const [reason, setReason] = useState<FollowupReason>('withdrawn')
  const [note, setNote] = useState('')

  const confirm = () => {
    if (choice === 'applied') {
      void pushFollowup(job.id, 'applied')
    } else if (choice === 'not_pursuing') {
      // 关窗标记「没投」只给 3 类原因（去掉 failed——未通过前提是已投过，见 apply.md §12.7bis）
      void pushFollowup(job.id, 'not_pursuing', { reason, note })
    }
    onDone()
  }

  return (
    <Modal opened onClose={onDone} title={`${job.company} · ${job.title}`} centered overlayProps={{ backgroundOpacity: 0.4, blur: 3 }}>
      <div className="apply-mark-body">
        <div className="apply-mark-title">{t('apply.markTitle')}</div>
        <Tabs value={choice ?? ''} onChange={(v) => setChoice(v as 'applied' | 'not_pursuing')}>
          <Tabs.List>
            <Tabs.Tab value="applied">{t('apply.markApplied')}</Tabs.Tab>
            <Tabs.Tab value="not_pursuing">{t('apply.markNotPursuing')}</Tabs.Tab>
          </Tabs.List>
        </Tabs>
        {choice === 'not_pursuing' && (
          <div className="apply-mark-fields">
            <Select
              value={reason}
              onChange={(v) => setReason(v as FollowupReason)}
              data={NOT_PURSUING_REASONS.filter((r) => r.value !== 'failed').map((r) => ({ value: r.value, label: r.label }))}
              placeholder={t('apply.markReasonPlaceholder')}
              mb="sm"
            />
            <TextInput
              classNames={{ input: 'apply-mark-note' }}
              placeholder={t('apply.markNotePlaceholder')}
              value={note}
              onChange={(e) => setNote(e.currentTarget.value)}
            />
          </div>
        )}
        <Group justify="flex-end" mt="sm">
          <Button size="compact-sm" color="green" onClick={confirm} disabled={!choice}>
            {t('apply.markConfirm')}
          </Button>
        </Group>
      </div>
    </Modal>
  )
}

/** YYYY-MM-DD 字符串 ↔ Date（本地日，避免时区漂移）。 */
function parseDay(s: string | null): Date | undefined {
  if (!s) return undefined
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, m - 1, d)
}
function fmtDay(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

/**
 * 日期范围（react-day-picker range；2026-09-08 换掉 Mantine DatePickerInput——
 * 其 Popover 在本环境定位/测量坏，面板被撑爆溢出屏幕）。
 * 合并进 TabTimeControls 的连体 pill 左段：日历挂 position:absolute 容器、useClickOutside 收合，
 * 完全绕开 Mantine Popover。值走 YYYY-MM-DD 字符串，per-tab 独立。
 */

/** 空态：每个 tab 配标题 + 描述（2026-09-08 去掉图标）。 */
const EMPTY_STATE: Record<Exclude<StatusTab, 'interviewing'>, { titleKey: string; descKey: string }> = {
  unprocessed: { titleKey: 'apply.empty.unprocessedTitle', descKey: 'apply.empty.unprocessedDesc' },
  applied: { titleKey: 'apply.empty.appliedTitle', descKey: 'apply.empty.appliedDesc' },
  offered: { titleKey: 'apply.empty.offeredTitle', descKey: 'apply.empty.offeredDesc' },
  not_pursuing: { titleKey: 'apply.empty.notPursuingTitle', descKey: 'apply.empty.notPursuingDesc' },
}

/** 各 tab 的语义时间键标签（ADR 0013：每 tab 一个语义时间键，排序与范围筛选同键）。 */
const TIME_LABEL_KEY: Record<Exclude<StatusTab, 'interviewing'>, string> = {
  unprocessed: 'apply.timeKeyCollected', // 收录时间
  applied: 'apply.timeKeyApplied', // 投递时间
  offered: 'apply.timeKeyOffered', // 录用时间
  not_pursuing: 'apply.timeKeyHandled', // 处理时间
}

/**
 * 统一时间控件（ADR 0013）：[语义时间文字标签] [连体 pill = 日期范围 + 正倒序箭头]。
 * 连体 pill 左右结构：左段日期范围触发器（× 清空收进内部右 section，出现/消失不改变宽度），
 * 右段纯箭头钮切换正倒序（无文字，title/aria-label 兜可访问性）。
 * 四个列表 tab 复用、只换前缀标签词；面试 tab 是纯日历、不渲染此控件；不再追踪 tab 额外挂原因筛选 Select。
 */
function TabTimeControls() {
  const t = useT()
  const tab = useApplyStore((s) => s.tab)
  const order = useApplyStore((s) => s.orderByTab[tab])
  const setOrder = useApplyStore((s) => s.setOrder)
  const range = useApplyStore((s) => s.rangeByTab[tab])
  const setDateRange = useApplyStore((s) => s.setDateRange)
  const reasonFilter = useApplyStore((s) => s.reasonFilter)
  const setReasonFilter = useApplyStore((s) => s.setReasonFilter)
  const [open, setOpen] = useState(false)
  const wrapRef = useClickOutside(() => setOpen(false))
  const isNotPursuing = tab === 'not_pursuing'
  const labelKey = TIME_LABEL_KEY[tab as Exclude<StatusTab, 'interviewing'>]

  const selected: DayPickerRange | undefined = range?.start || range?.end
    ? { from: parseDay(range?.start ?? null), to: parseDay(range?.end ?? null) }
    : undefined

  const onSelect = (r: DayPickerRange | undefined) => {
    if (!r || (!r.from && !r.to)) {
      setDateRange(null)
      return
    }
    setDateRange({ start: r.from ? fmtDay(r.from) : null, end: r.to ? fmtDay(r.to) : null })
  }

  const dateLabel = selected?.from
    ? `${fmtDay(selected.from)}${selected.to ? ` – ${fmtDay(selected.to)}` : ''}`
    : t('apply.dateRangePlaceholder')

  const clearDate = () => { setDateRange(null); setOpen(false) }
  const toggleOrder = () => setOrder(order === 'desc' ? 'asc' : 'desc')

  return (
    <div className="apply-controls">
      <span className="apply-time-key">{t(labelKey)}</span>
      <div className="apply-time-pill" ref={wrapRef}>
        {/* 共用外壳：描边 + 圆角 + 底都在这层，两段内层去描边去圆角透明底。 */}
        <div className="apply-time-pill-body">
          {/* 左段：日期范围触发器（Mantine subtle 钮，去描边去圆角透明底，轮廓交给外壳）。 */}
          <div className="apply-time-pill-date">
            <Button
              variant="subtle"
              className={`apply-date-trigger${selected?.from ? ' is-set' : ''}`}
              onClick={() => setOpen((v) => !v)}
              leftSection={<Calendar size={13} />}
            >
              <span className="apply-date-trigger-label">{dateLabel}</span>
            </Button>
            {selected?.from && (
              <Button
                variant="subtle"
                size="compact-xs"
                className="apply-date-clear"
                aria-label={t('apply.dateRangeClear')}
                onClick={clearDate}
              >
                ×
              </Button>
            )}
          </div>
          {/* 右段：正倒序箭头（无文字）。 */}
          <Button
            variant="subtle"
            size="compact-xs"
            className="apply-order-toggle"
            aria-label={order === 'desc' ? t('apply.sortDesc') : t('apply.sortAsc')}
            title={order === 'desc' ? t('apply.sortDesc') : t('apply.sortAsc')}
            onClick={toggleOrder}
          >
            {order === 'desc' ? <ArrowDown size={14} /> : <ArrowUp size={14} />}
          </Button>
        </div>
        {open && (
          <div className="apply-date-panel">
            <DayPicker
              mode="range"
              selected={selected}
              onSelect={onSelect}
              defaultMonth={selected?.from ?? new Date()}
            />
          </div>
        )}
      </div>
      {isNotPursuing && (
        <Select
          value={reasonFilter ?? ''}
          onChange={(v) => setReasonFilter((v || null) as FollowupReason | null)}
          data={[{ value: '', label: t('apply.reasonFilterAll') }, ...NOT_PURSUING_REASONS.map((r) => ({ value: r.value, label: r.label }))]}
          size="xs"
          w={140}
        />
      )}
    </div>
  )
}

/** 跟进 tab 的 hover 快推：正向一键 / 不再追踪下拉 / 其他下拉。 */
function OverlayActions({ job, onSchedule }: { job: Job; onSchedule: (job: Job) => void }) {
  const t = useT()
  const pushFollowup = useApplyStore((s) => s.pushFollowup)
  const status = job.followupStatus

  // 已投递 → 「面试」现在是「排面试」（弹表单，ADR 0006）；interviewing 态不再出现在岗位列表（面试 tab = 日历）。
  const forward: FollowupStatus | null = status === 'applied' ? 'interviewing' : null

  const doPush = (s: FollowupStatus, extra?: { reason?: FollowupReason; stage?: string }) => {
    void pushFollowup(job.id, s, extra)
  }

  return (
    <div className="apply-overlay-actions">
      {forward ? (
        // 排面试（唯一进入「待面试」的入口）：不直接标状态，弹表单
        <Button variant="default" size="compact-sm" className="apply-overlay-btn" onClick={() => onSchedule(job)}>
          {t('apply.pushInterviewing')}
        </Button>
      ) : status === 'not_pursuing' ? (
        <Button variant="default" size="compact-sm" className="apply-overlay-btn" onClick={() => doPush('applied')}>
          {t('apply.pushBackToApplied')}
        </Button>
      ) : null}

      {/* 不再追踪下拉（含 4 原因；已投/已面后未通过是合法路径） */}
      {status !== 'not_pursuing' && (
        <Menu shadow="md" width={160}>
          <Menu.Target>
            <Button variant="default" size="compact-sm" className="apply-overlay-btn">{t('apply.pushNotPursuing')} ▾</Button>
          </Menu.Target>
          <Menu.Dropdown>
            {NOT_PURSUING_REASONS.map((r) => (
              <Menu.Item key={r.value} onClick={() => doPush('not_pursuing', { reason: r.value })}>
                {r.label}
              </Menu.Item>
            ))}
          </Menu.Dropdown>
        </Menu>
      )}

      {/* 其他下拉：任意可达状态（interviewing 移除——排面试是唯一入口，不能绕过日历直接标） */}
      <Menu shadow="md" width={160}>
        <Menu.Target>
          <Button variant="default" size="compact-sm" className="apply-overlay-btn">{t('apply.pushOther')} ▾</Button>
        </Menu.Target>
        <Menu.Dropdown>
          {(
            [
              { s: 'applied' as const, k: 'apply.pushApplied' },
              { s: 'offered' as const, k: 'apply.pushOffered' },
              { s: 'not_pursuing' as const, k: 'apply.pushNotPursuing' },
            ] as const
          )
            .filter(({ s }) => s !== status)
            .map(({ s, k }) => (
              <Menu.Item key={s} onClick={() => doPush(s)}>
                {t(k)}
              </Menu.Item>
            ))}
        </Menu.Dropdown>
      </Menu>
    </div>
  )
}

/** 单个岗位卡片：hover 遮罩（未处理 = 点开详情；跟进 tab = 快推按钮）。 */
function JobRow({ job, onOpenDetail, onSchedule }: { job: Job; onOpenDetail: (job: Job) => void; onSchedule: (job: Job) => void }) {
  const t = useT()
  const tab = useApplyStore((s) => s.tab)
  const isUnprocessed = tab === 'unprocessed'
  const isClosed = job.liveness === 'closed'
  const reasonLabel = job.followupStatus === 'not_pursuing' ? NOT_PURSUING_REASONS.find((r) => r.value === job.followupReason)?.label : undefined

  return (
    <div className={`apply-job-row${isClosed ? ' is-closed' : ''}`}>
      <div className="apply-job-main">
        <div className="apply-job-title-line">
          <span className="apply-job-title">{job.title}</span>
          {job.isNew && (
            <Badge color="green" size="xs" variant="light">
              {t('apply.newTag')}
            </Badge>
          )}
          <Badge color="gray" size="xs" variant="light">
            {JOB_SOURCE_LABELS[job.source]}
          </Badge>
        </div>
        <div className="apply-job-meta">
          <span>{job.company}</span>
          <span>{job.city}</span>
          <span>{job.salaryText}</span>
          <span>{job.experience}</span>
          <span>{job.degree}</span>
        </div>
        {job.skills.length > 0 && (
          <div className="apply-job-skills">
            {job.skills.map((s) => (
              <span key={s} className="apply-job-skill">
                <Tag size={10} /> {s}
              </span>
            ))}
          </div>
        )}
      </div>
      <div className="apply-job-side">
        <div className="apply-job-liveness">
          <span className={`liveness-dot liveness-${job.liveness}`} />
          {job.liveness === 'active' ? t('apply.livenessActive') : job.liveness === 'stale' ? t('apply.livenessStale') : job.liveness === 'closed' ? t('apply.livenessClosed') : t('apply.livenessUnknown')}
        </div>
        {reasonLabel && <span className="apply-job-reason-chip">{reasonLabel}</span>}
      </div>

      {/* hover 遮罩 */}
      <div className="apply-job-overlay">
        {isUnprocessed ? (
          <Button variant="default" size="compact-sm" className="apply-overlay-view" onClick={() => onOpenDetail(job)}>
            {t('apply.hoverViewDetail')}
          </Button>
        ) : (
          <OverlayActions job={job} onSchedule={onSchedule} />
        )}
      </div>
    </div>
  )
}

/** 日历日分组（天粒度，借 beacon activityList 分组算法）。 */
function groupJobsByDay(jobs: Job[], tab: StatusTab): { key: string; label: string; jobs: Job[] }[] {
  const now = new Date()
  const todayKey = dayKey(now)
  const yest = new Date(now)
  yest.setDate(now.getDate() - 1)
  const yesterdayKey = dayKey(yest)

  const map = new Map<string, Job[]>()
  for (const j of jobs) {
    const ts = tab === 'unprocessed' ? j.lastSeenAt : j.followupAt
    const key = dayKey(new Date(ts))
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(j)
  }
  // 按日期倒序（新在前；列表已按 sort 排序，这里只按日分组，组内保持原序）
  const keys = Array.from(map.keys()).sort((a, b) => b.localeCompare(a))
  return keys.map((key) => {
    let label: string
    if (key === todayKey) label = '今天'
    else if (key === yesterdayKey) label = '昨天'
    else {
      const [y, m, d] = key.split('-').map(Number)
      label = `${y}年${m}月${d}日`
    }
    return { key, label, jobs: map.get(key)! }
  })
}

function dayKey(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

/** 岗位列表：按日历日分分割线（今天/昨天/具体日期），每天一条。 */
function JobList({ jobs, onOpenDetail, onSchedule }: { jobs: Job[]; onOpenDetail: (job: Job) => void; onSchedule: (job: Job) => void }) {
  const t = useT()
  const tab = useApplyStore((s) => s.tab)
  const groups = useMemo(() => groupJobsByDay(jobs, tab), [jobs, tab])

  if (jobs.length === 0) {
    const empty = EMPTY_STATE[tab as Exclude<StatusTab, 'interviewing'>]
    return (
      <div className="apply-empty">
        <h3>{t(empty.titleKey)}</h3>
        <p>{t(empty.descKey)}</p>
      </div>
    )
  }

  return (
    <>
      {groups.map((g) => (
        <div key={g.key} className="apply-day-group">
          <div className="apply-day-divider">
            <span className="apply-day-line" />
            <span className="apply-day-label">{g.label}</span>
            <span className="apply-day-line" />
          </div>
          {g.jobs.map((job) => (
            <JobRow key={job.id} job={job} onOpenDetail={onOpenDetail} onSchedule={onSchedule} />
          ))}
        </div>
      ))}
    </>
  )
}

/** 投递页：左侧二级导航（状态）+ 右侧视图（岗位列表/面试日历）+ 关窗标记框。 */
export function ApplyPage() {
  const t = useT()
  const jobs = useApplyStore((s) => s.jobs)
  const interviews = useApplyStore((s) => s.interviews)
  const counts = useApplyStore((s) => s.counts)
  const tab = useApplyStore((s) => s.tab)
  const lastRoundId = useApplyStore((s) => s.lastRoundId)
  const setTab = useApplyStore((s) => s.setTab)
  const kick = useApplyStore((s) => s.kick)
  const refreshList = useApplyStore((s) => s.refreshList)
  const refreshInterviews = useApplyStore((s) => s.refreshInterviews)
  const scheduleInterview = useApplyStore((s) => s.scheduleInterview)
  const updateInterview = useApplyStore((s) => s.updateInterview)

  // 关窗标记框状态：用户关投递窗口 → 主进程发 apply:detail-closed → 弹框标这个岗位。
  const [pendingMarkJob, setPendingMarkJob] = useState<Job | null>(null)

  // 面试弹窗状态
  const [scheduleJob, setScheduleJob] = useState<Job | null>(null)
  const [detailIv, setDetailIv] = useState<Interview | null>(null)
  const [resultIv, setResultIv] = useState<Interview | null>(null)
  const [rescheduleIv, setRescheduleIv] = useState<Interview | null>(null)

  // 挂载：拉当前 tab 列表 + 面试场次 + 进入投递页即踢（apply.md §8① 触发点③：在场即踢，
  // 受 should_crawl 门控——顺手解冷启动：apply_mode 开、backlog 空时进页面就是叫醒事件）。
  useEffect(() => {
    void refreshList(useApplyStore.getState().tab)
    void refreshInterviews()
    void kick()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 订阅关窗事件（Electron 主进程在投递窗口被关时发出）
  useEffect(() => {
    if (!window.desktop?.onDetailClosed) return
    const off = window.desktop.onDetailClosed((payload) => {
      const job = useApplyStore.getState().jobs.find((j) => j.id === payload.jobId)
      if (job) setPendingMarkJob(job)
    })
    return off
  }, [])

  const openDetail = (job: Job) => {
    if (window.desktop?.openJobDetail) {
      void window.desktop.openJobDetail({ source: job.source, url: job.sourceUrl, title: job.title, jobId: job.id })
    } else {
      window.open(job.sourceUrl, '_blank')
    }
  }

  const onMarkDone = () => {
    // 标记完：真正关掉岗位窗口（跳过 close 拦截）
    if (pendingMarkJob && window.desktop?.closeJobDetail) {
      void window.desktop.closeJobDetail(pendingMarkJob.id)
    }
    setPendingMarkJob(null)
  }

  const onScheduleSubmit = (input: { scheduledAt: string; location: string; round: string }) => {
    if (scheduleJob) void scheduleInterview(scheduleJob.id, input)
    setScheduleJob(null)
  }

  const onResultSubmit = (outcome: InterviewOutcome, reason?: FollowupReason) => {
    if (resultIv) void updateInterview(resultIv.id, { outcome, reason })
    setResultIv(null)
  }

  const onRescheduleSubmit = (scheduledAt: string) => {
    if (rescheduleIv) void updateInterview(rescheduleIv.id, { scheduledAt })
    setRescheduleIv(null)
  }

  return (
    <div className="apply-layout">
      {/* 左侧二级导航：标题 + 5 状态（带徽标） */}
      <nav className="apply-nav">
        <div className="subnav-head">
          <h1 className="page-title">{t('page.apply.title')}</h1>
          <p className="page-subtitle">{t('page.apply.desc')}</p>
        </div>
        {NAV_ITEMS.map((item) => (
          <Button
            key={item.value}
            variant="subtle"
            className={`apply-nav-item${tab === item.value ? ' active' : ''}${item.value === 'not_pursuing' ? ' apply-nav-item--bottom' : ''}`}
            onClick={() => setTab(item.value)}
            rightSection={<span className="apply-tab-count">{counts[item.value]}</span>}
          >
            {t(item.labelKey)}
          </Button>
        ))}
      </nav>

      {/* 右侧视图 */}
      <div className="apply-body">
        {/* 顶部栏：入口（仅未处理 tab，左）+ 统一时间控件（右）；面试 tab 无此行 */}
        {tab !== 'interviewing' && (
          <div className="apply-console-row">
            <div className="apply-console-left">
              {tab === 'unprocessed' && <ConsoleTrigger />}
              {/* 分析面板（§11.7.1）：未处理（本轮批次）/ 已投递（日报）两 tab 有；录用不做。 */}
              {(tab === 'unprocessed' || tab === 'applied') && (
                <ApplyAnalysisPanel tab={tab} roundId={lastRoundId} />
              )}
            </div>
            <div className="apply-console-actions">
              <TabTimeControls />
            </div>
          </div>
        )}

        {/* 面试 tab = 日历；其余 tab = 岗位列表（按天分分割线）。两者共用灰容器底。 */}
        {tab === 'interviewing' ? (
          <div className="apply-list apply-list--panel">
            <InterviewCalendar interviews={interviews} onDetail={setDetailIv} onResult={setResultIv} />
          </div>
        ) : (
          <div className="apply-list apply-list--panel">
            <JobList jobs={jobs} onOpenDetail={openDetail} onSchedule={setScheduleJob} />
          </div>
        )}
      </div>

      {/* 关窗标记框 */}
      {pendingMarkJob && <MarkAppliedModal job={pendingMarkJob} onDone={onMarkDone} />}

      {/* 面试弹窗 */}
      {scheduleJob && <ScheduleInterviewModal job={scheduleJob} onClose={() => setScheduleJob(null)} onSubmit={onScheduleSubmit} />}
      {detailIv && (
        <InterviewDetailModal
          iv={detailIv}
          job={detailIv.job}
          onClose={() => setDetailIv(null)}
          onReschedule={(iv) => {
            setDetailIv(null)
            setRescheduleIv(iv)
          }}
        />
      )}
      {resultIv && <InterviewResultModal iv={resultIv} onClose={() => setResultIv(null)} onSubmit={onResultSubmit} />}
      {rescheduleIv && <InterviewRescheduleModal iv={rescheduleIv} onClose={() => setRescheduleIv(null)} onSubmit={onRescheduleSubmit} />}
    </div>
  )
}
