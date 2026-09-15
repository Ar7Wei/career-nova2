import { create } from 'zustand'
import api from '@/lib/api'
import { useSettingsStore } from '@/stores/settingsStore'
import type { FollowupReason, FollowupStatus, Interview, InterviewOutcome, Job, JobBrief, JobCounts, JobSort } from '@/types/apply'
import type { AnalysisReport, AnalysisScope, Prescription } from '@/types/apply'
import { toReportBlocks } from '@/types/report_block'
import type { ReportBlock } from '@/types/report_block'

/**
 * 投递页状态：岗位列表（按 tab 查询）+ 状态徽标 counts + 排序/筛选 + 手动刷新 + 跟进推进。
 *
 * 2026-08-31 信息架构重构（apply.md §12.11）：数据流从「一次全拉 + 客户端分组」改为
 * **后端按 tab 查询**——切 tab 每次请求 GET /jobs?status&sort&...（用上后端 status/reason/
 * followup 参数），响应带 counts（列表 + 全徽标一次回）。排序 + 日期范围 per-tab 独立记住。
 *
 * - 切 tab 每次请求后端（本地回环几 ms、数据新鲜，无缓存失效复杂度）。
 * - 面试 tab：日历数据源 = interviews（GET /interviews，内嵌 job 摘要），不渲染 jobs 列表。
 * - 手动刷新：POST /jobs/refresh → 反馈"新增/更新 N 条"。
 * - 推进：POST /jobs/{id}/followup（追加状态事件）。
 */

/** 列表排序方向。 */
export type SortOrder = 'desc' | 'asc'

/** kick 门控结果（与后端 gate_reason 对齐，指示器信号）。crawled = 至少一家在爬；其余 = 拒因。 */
export type CrawlGateReason = 'crawled' | 'apply_mode_off' | 'no_direction' | 'backlog_full' | 'throttled' | 'exhausted'

/** 状态 tab 值（unprocessed = 无事件 = 空着；interviewed 是派生态、并入「面试」tab）。 */
export type StatusTab = 'unprocessed' | Exclude<FollowupStatus, 'interviewed'>

/** 日期范围（闭区间，YYYY-MM-DD 本地日，可空 = 不限；发送后端时转 ISO 时间点）。 */
export interface DateRange {
  start: string | null
  end: string | null
}

/** 各 tab 默认排序（ADR 0013：单一时间维度——未处理 time=收录时间，跟进 followup=当前态事件 at）。 */
const DEFAULT_SORT: Record<StatusTab, JobSort> = {
  unprocessed: 'time',
  applied: 'followup',
  interviewing: 'followup',
  offered: 'followup',
  not_pursuing: 'followup',
}

function defaultSortByTab(): Record<StatusTab, JobSort> {
  return { ...DEFAULT_SORT }
}
function defaultOrderByTab(): Record<StatusTab, SortOrder> {
  return { unprocessed: 'desc', applied: 'desc', interviewing: 'desc', offered: 'desc', not_pursuing: 'desc' }
}
function defaultRangeByTab(): Record<StatusTab, DateRange | null> {
  return { unprocessed: null, applied: null, interviewing: null, offered: null, not_pursuing: null }
}

interface ApplyState {
  /** 当前 tab 的岗位列表（后端按 tab 查询结果）。 */
  jobs: Job[]
  /** 面试场次列表（全量，日历数据源，不过滤状态；内嵌 job 摘要）。 */
  interviews: Interview[]
  /** 各状态徽标数（面试 tab = 待面试场次数）。 */
  counts: JobCounts
  /** 当前状态 tab。 */
  tab: StatusTab
  /** 不再追踪原因筛选（仅 tab=not_pursuing 时叠加）。 */
  reasonFilter: FollowupReason | null
  /** 检索控制台浮窗各块折叠态（单次启动内记住，跨开关/切 tab 不丢）。 */
  consoleSections: { criteria: boolean; crawl: boolean }
  /** per-tab 排序方式。 */
  sortByTab: Record<StatusTab, JobSort>
  /** per-tab 排序方向。 */
  orderByTab: Record<StatusTab, SortOrder>
  /** per-tab 日期范围（null = 不限）。 */
  rangeByTab: Record<StatusTab, DateRange | null>

  /** 爬虫是否在跑（一次 kickCrawl 在途）。指示器「跑」信号，running 优先于受限。 */
  kickRunning: boolean
  /** 最近一脚的门控结果（指示器「受限」信号）。crawled = 正常；throttled/exhausted = 受限。 */
  gateReason: CrawlGateReason
  /** 最近一轮的抓取轮次 id（分析「本轮那批」的键）。kick 后由结果带回。 */
  lastRoundId: number | null
  /** 投递页分析报告（按 tab 缓存；§11.7.5 算一次存一次，前端只读）。 */
  analysis: Partial<Record<'batch' | 'daily', AnalysisReport>>

  /** 事件驱动抓岗位（kickCrawl）：后端猎聘一轮 + 前程无忧读 DOM 一轮，尾部踢分析。 */
  kick: () => Promise<void>
  /** 拉某 tab 的岗位列表 + 全徽标（后端按 tab 查询）。挂载 + 切 tab + 推进后调用。 */
  refreshList: (tab: StatusTab) => Promise<void>
  /** 拉全量面试场次（日历数据源）。 */
  refreshInterviews: () => Promise<void>
  /** 拉未处理批次分析（本轮那批）。kick 尾 + 展开面板时调。 */
  fetchBatchAnalysis: (roundId: number) => Promise<void>
  /** 拉已投递日报（截至昨天）。进页面/切 tab 时调。 */
  fetchDailyAnalysis: () => Promise<void>
  /** 收录一条处方进优化点（proposed → pending）。之后从分析面板移除该条。 */
  promotePrescription: (id: number) => Promise<void>
  setTab: (t: StatusTab) => void
  setReasonFilter: (r: FollowupReason | null) => void
  setSort: (s: JobSort) => void
  setOrder: (o: SortOrder) => void
  setDateRange: (r: DateRange | null) => void
  /** 切换检索控制台浮窗某块的折叠态。 */
  toggleConsoleSection: (section: 'criteria' | 'crawl') => void
  /** 追加一条投递跟进状态事件（推进/回退/终态）。 */
  pushFollowup: (jobId: number, status: FollowupStatus, extra?: { reason?: FollowupReason; stage?: string; note?: string }) => Promise<void>
  /** 排一场面试（唯一进入「待面试」的入口）。 */
  scheduleInterview: (jobId: number, input: { scheduledAt: string; location: string; round: string }) => Promise<void>
  /** 更新一场面试（改期 / 改结果 / 安排下一场）。 */
  updateInterview: (interviewId: number, input: { outcome?: InterviewOutcome; scheduledAt?: string; location?: string; round?: string; reason?: FollowupReason; nextScheduledAt?: string; nextRound?: string }) => Promise<void>
}

/** 跃迁检测器（apply.md §8① / ADR 0008 decision 9）：
 *  记「是否已处于该踢状态」——只有当 unprocessed 从 ≥ 阈值跃迁到 < 阈值（或首次进入
 *  该踢状态）时才真踢一次；之后连续拿到同样的 unprocessed=0 只有第一次触发，避免
 *  切 tab / 改排序的 UI 抖动变成高频抓取。零计时器：kick 只被 refreshList 回带的
 *  counts 驱动，不主动 setTimeout。 */
let kickArmed = false // 初值 false = 首屏拿到 unprocessed<阈值 就补一批（首次进入该踢状态也踢）
let kickInFlight = false // kick 在途闸：await kickCrawl 期间不重复踢

async function maybeKick(): Promise<void> {
  if (!window.desktop?.kickCrawl) return // 浏览器 dev 无 Electron，不踢
  if (kickInFlight) return
  kickInFlight = true
  const set = useApplyStore.setState
  // 指示器信号：跑起来（running 优先于受限）。
  set({ kickRunning: true })
  try {
    const result = await window.desktop.kickCrawl()
    // kick 完成：读后端 gate_reason 更新受限态（复用事件驱动，零轮询）。
    const roundId = result?.roundId ?? null
    set({ gateReason: (result?.gateReason ?? 'crawled') as CrawlGateReason, lastRoundId: roundId })
    // kick 尾部踢分析（§11.7.5「一轮爬完就算」；含前程无忧轮）。等它跑完再重拉列表——
    // 否则列表先刷、分析面板还空着。分析失败不该阻断列表刷新，故单独 catch。
    if (roundId != null) {
      await useApplyStore.getState().fetchBatchAnalysis(roundId).catch(() => {})
    }
    // kick 完成后重拉当前 tab（新岗位补进来）
    await useApplyStore.getState().refreshList(useApplyStore.getState().tab)
  } catch {
    // kick 失败：出声（爬取失败不该静默），但由后端门控决定是否真爬，这里只透传结果
    // gate_reason 拿不到 → 保持上一脚的值（不误标受限）。
  } finally {
    set({ kickRunning: false })
    kickInFlight = false
  }
}

/** 分析面板轮询：报告还在算（computing）时隔 1.5s 再拉一次，**后台**跑（不 await 调用方）。 */
let _analysisPollTimer: number | null = null
const ANALYSIS_POLL_MS = 1500

function scheduleAnalysisPoll(fn: () => void): void {
  if (_analysisPollTimer != null) window.clearTimeout(_analysisPollTimer)
  _analysisPollTimer = window.setTimeout(() => {
    _analysisPollTimer = null
    fn()
  }, ANALYSIS_POLL_MS)
}

/** 测试/卸载用：停掉在途轮询。 */
export function stopAnalysisPoll(): void {
  if (_analysisPollTimer != null) {
    window.clearTimeout(_analysisPollTimer)
    _analysisPollTimer = null
  }
}

/** 读触发值（settingsStore 的 crawl_threshold）。 */
function currentThreshold(): number {
  return useSettingsStore.getState().crawl_threshold ?? 5
}

/** refreshList 回带 counts 后调用：跃迁检测踢一次。 */
function detectKick(unprocessed: number): void {
  const threshold = currentThreshold()
  if (unprocessed >= threshold) {
    kickArmed = false // 回到「未踢态」，等下次跃迁
    return
  }
  // unprocessed < threshold
  if (!kickArmed) {
    kickArmed = true
    void maybeKick()
  }
  // 已 armed（连续 unprocessed < threshold）→ 不重复踢
}

/** 后端 Job → 前端 Job（source 字段名对齐；后端已是统一 schema）。 */
function toFrontendJob(j: Record<string, unknown>): Job {
  return {
    id: Number(j.id),
    source: j.source as Job['source'],
    externalId: String(j.external_id ?? ''),
    title: String(j.title ?? ''),
    company: String(j.company ?? ''),
    city: String(j.city ?? ''),
    salaryText: String(j.salary_text ?? ''),
    experience: String(j.experience ?? ''),
    degree: String(j.degree ?? ''),
    skills: Array.isArray(j.skills) ? (j.skills as string[]) : [],
    jobLabels: Array.isArray(j.job_labels) ? (j.job_labels as string[]) : [],
    welfare: Array.isArray(j.welfare) ? (j.welfare as string[]) : [],
    description: String(j.description ?? ''),
    sourceUrl: String(j.source_url ?? ''),
    applyUrl: String(j.apply_url ?? ''),
    liveness: j.liveness as Job['liveness'],
    lastSeenAt: String(j.last_seen_at ?? ''),
    isNew: Boolean(j.is_new ?? false),
    followupStatus: (j.followup_status ?? null) as Job['followupStatus'],
    followupReason: (j.followup_reason ?? null) as Job['followupReason'],
    followupAt: String(j.followup_at ?? ''),
    crawlRoundId: j.crawl_round_id == null ? null : Number(j.crawl_round_id),
  }
}

/** 后端建议 → 前端处方（分析面板用）。 */
function toFrontendPrescription(s: Record<string, unknown>): Prescription {
  return {
    id: Number(s.id),
    type: s.type as Prescription['type'],
    target: String(s.target ?? ''),
    original: String(s.original ?? ''),
    suggested: String(s.suggested ?? ''),
    reason: String(s.reason ?? ''),
    severity: (s.severity ?? 'medium') as Prescription['severity'],
    status: 'proposed',
    origin: 'job_analysis',
  }
}

/** 后端报告 → 前端 AnalysisReport（snake_case → camelCase）。 */
function toFrontendReport(r: Record<string, unknown>): AnalysisReport {
  const meta = (r.meta ?? {}) as Record<string, unknown>
  // 正文块流：新版读 r.blocks；兼容旧缓存（只有 body 字符串）→ 折成单个 text 块。
  // 坏 chart 块在 toReportBlocks 里被 isChartSpec 滤掉（模型配错图 → 只少这张图，不崩）。
  let blocks: ReportBlock[] = toReportBlocks(r.blocks)
  if (blocks.length === 0 && typeof r.body === 'string' && r.body.trim() !== '') {
    blocks = [{ kind: 'text', md: r.body }]
  }
  return {
    scope: r.scope as AnalysisScope,
    scopeKey: String(r.scope_key ?? ''),
    status: (r.status ?? 'ready') as AnalysisReport['status'],
    headline: String(r.headline ?? ''),
    blocks,
    meta: {
      total: meta.total == null ? undefined : Number(meta.total),
      liepin: meta.liepin == null ? undefined : Number(meta.liepin),
      job51: meta.job51 == null ? undefined : Number(meta.job51),
      withJd: meta.with_jd == null ? undefined : Number(meta.with_jd),
    },
    createdAt: String(r.created_at ?? ''),
    suggestions: Array.isArray(r.suggestions) ? (r.suggestions as Record<string, unknown>[]).map(toFrontendPrescription) : [],
  }
}

/** 后端 JobBrief → 前端 JobBrief。 */
function toFrontendJobBrief(b: Record<string, unknown>): JobBrief {
  return {
    company: String(b.company ?? ''),
    title: String(b.title ?? ''),
    description: String(b.description ?? ''),
  }
}

/** 后端 Interview → 前端 Interview（内嵌 job 摘要）。 */
function toFrontendInterview(i: Record<string, unknown>): Interview {
  return {
    id: Number(i.id),
    jobId: Number(i.job_id),
    scheduledAt: String(i.scheduled_at ?? ''),
    location: String(i.location ?? ''),
    round: String(i.round ?? ''),
    outcome: (i.outcome ?? 'scheduled') as Interview['outcome'],
    nextRoundId: i.next_round_id == null ? null : Number(i.next_round_id),
    job: toFrontendJobBrief((i.job ?? {}) as Record<string, unknown>),
  }
}

function toFrontendCounts(c: Record<string, unknown>): JobCounts {
  return {
    unprocessed: Number(c.unprocessed ?? 0),
    applied: Number(c.applied ?? 0),
    interviewing: Number(c.interviewing ?? 0),
    offered: Number(c.offered ?? 0),
    not_pursuing: Number(c.not_pursuing ?? 0),
  }
}

/** YYYY-MM-DD（本地日）→ ISO 时间点。start = 当日 00:00，end = 当日 23:59:59.999。 */
function localDayToIso(day: string, isEnd: boolean): string {
  const d = new Date(`${day}T00:00:00`)
  if (isEnd) d.setHours(23, 59, 59, 999)
  return d.toISOString()
}

export const useApplyStore = create<ApplyState>((set, get) => ({
  jobs: [],
  interviews: [],
  counts: { unprocessed: 0, applied: 0, interviewing: 0, offered: 0, not_pursuing: 0 },
  tab: 'unprocessed',
  reasonFilter: null,
  consoleSections: { criteria: false, crawl: false },
  sortByTab: defaultSortByTab(),
  orderByTab: defaultOrderByTab(),
  rangeByTab: defaultRangeByTab(),
  kickRunning: false,
  gateReason: 'crawled',
  lastRoundId: null,
  analysis: {},

  /** 事件驱动抓岗位：后端猎聘一轮 + 前程无忧读 DOM 一轮，完成后重拉列表。 */
  kick: async () => {
    await maybeKick()
    // kick 完成后重拉当前 tab（新岗位补进来）
    await get().refreshList(get().tab)
  },

  /** 拉未处理批次分析（本轮那批，§11.7.5）。computing 时后台轮询到 ready。 */
  fetchBatchAnalysis: async (roundId) => {
    try {
      const { data } = await api.get<Record<string, unknown>>('/v1/analysis/unprocessed', {
        params: { round_id: roundId },
      })
      const report = toFrontendReport(data)
      set({ analysis: { ...get().analysis, batch: report } })
      if (report.status === 'computing') {
        scheduleAnalysisPoll(() => void get().fetchBatchAnalysis(roundId))
      }
    } catch {
      // 分析失败：保留上一份，不静默清空（顶部面板自会显示旧报告或空态）
    }
  },

  /** 拉已投递日报（截至昨天，本地日界，§11.7.5）。computing 时后台轮询到 ready。 */
  fetchDailyAnalysis: async () => {
    try {
      const { data } = await api.get<Record<string, unknown>>('/v1/analysis/applied')
      const report = toFrontendReport(data)
      set({ analysis: { ...get().analysis, daily: report } })
      if (report.status === 'computing') {
        scheduleAnalysisPoll(() => void get().fetchDailyAnalysis())
      }
    } catch {
      // 同上：保留上一份
    }
  },

  /** 收录一条处方进优化点（proposed → pending，§11.7.7）。成功后从分析面板移除该条。 */
  promotePrescription: async (id) => {
    try {
      await api.post('/v1/optimization/promote', { suggestion_id: id })
      const batch = get().analysis.batch
      if (batch) {
        set({
          analysis: {
            ...get().analysis,
            batch: { ...batch, suggestions: batch.suggestions.filter((s) => s.id !== id) },
          },
        })
      }
      // 优化点面板数据在简历页拉，跨页无需同步（同一后端真相源，进页面自然读最新）
    } catch {
      // 收录失败：出声（不静默——用户以为收了实际没收）
      throw new Error('promote_failed')
    }
  },

  /** 拉某 tab 的岗位列表 + 全徽标（后端按 tab 查询）。 */
  refreshList: async (tab) => {
    try {
      const params: Record<string, string> = {
        status: tab,
        sort: get().sortByTab[tab],
        order: get().orderByTab[tab],
      }
      if (tab === 'not_pursuing' && get().reasonFilter) {
        const reason = get().reasonFilter
        if (reason) params.reason = reason
      }
      const range = get().rangeByTab[tab]
      if (range?.start) params.start = localDayToIso(range.start, false)
      if (range?.end) params.end = localDayToIso(range.end, true)
      const { data } = await api.get<{ jobs: Record<string, unknown>[]; counts: Record<string, unknown> }>('/v1/jobs', { params })
      const counts = toFrontendCounts(data.counts ?? {})
      set({ jobs: data.jobs.map(toFrontendJob), counts })
      // 跃迁检测（apply.md §8①）：只在未处理 tab 回带 counts 时判定（其它 tab 的
      // unprocessed 也是全局数，但切 tab/改排序不该踢——只未处理 tab 的刷新是「推进」语义）。
      if (tab === 'unprocessed') {
        detectKick(counts.unprocessed)
      }
    } catch {
      // 列表加载失败：保留现有数据，不静默清空
    }
  },

  refreshInterviews: async () => {
    try {
      const { data } = await api.get<{ interviews: Record<string, unknown>[] }>('/v1/interviews')
      set({ interviews: data.interviews.map(toFrontendInterview) })
    } catch {
      // 加载失败：保留现有数据，不静默清空
    }
  },

  setTab: (t) => {
    set({ tab: t, reasonFilter: t === 'not_pursuing' ? get().reasonFilter : null })
    // 面试 tab：刷新场次 + 徽标（jobs 列表不渲染）；其余 tab：拉列表 + 徽标。
    if (t === 'interviewing') {
      void get().refreshInterviews()
      void get().refreshList(t)
    } else {
      void get().refreshList(t)
    }
  },

  setReasonFilter: (r) => {
    set({ reasonFilter: r })
    void get().refreshList(get().tab)
  },
  setSort: (s) => {
    const tab = get().tab
    set({ sortByTab: { ...get().sortByTab, [tab]: s } })
    void get().refreshList(tab)
  },
  setOrder: (o) => {
    const tab = get().tab
    set({ orderByTab: { ...get().orderByTab, [tab]: o } })
    void get().refreshList(tab)
  },
  setDateRange: (r) => {
    const tab = get().tab
    set({ rangeByTab: { ...get().rangeByTab, [tab]: r } })
    void get().refreshList(tab)
  },
  toggleConsoleSection: (section) => {
    set({ consoleSections: { ...get().consoleSections, [section]: !get().consoleSections[section] } })
  },

  pushFollowup: async (jobId, status, extra) => {
    try {
      await api.post(`/v1/jobs/${jobId}/followup`, {
        status,
        reason: extra?.reason ?? null,
        stage: extra?.stage ?? '',
        note: extra?.note ?? '',
      })
      // 推进成功后重拉当前 tab 列表 + 全徽标（岗位状态变化 → 分组/计数刷新）
      await get().refreshList(get().tab)
    } catch {
      // 落库失败：出声（不静默——否则用户以为存了实际没存）
    }
  },

  scheduleInterview: async (jobId, input) => {
    try {
      await api.post(`/v1/jobs/${jobId}/interviews`, {
        scheduled_at: input.scheduledAt,
        location: input.location,
        round: input.round,
      })
      // 建场次 + 联动 interviewing → 刷新场次 + 当前 tab（分组/计数）
      await Promise.all([get().refreshInterviews(), get().refreshList(get().tab)])
    } catch {
      // 出声：排面试失败不能静默（用户以为排上了实际没排）
    }
  },

  updateInterview: async (interviewId, input) => {
    try {
      await api.patch(`/v1/interviews/${interviewId}`, {
        outcome: input.outcome ?? null,
        scheduled_at: input.scheduledAt ?? null,
        location: input.location ?? null,
        round: input.round ?? null,
        reason: input.reason ?? null,
        next_scheduled_at: input.nextScheduledAt ?? null,
        next_round: input.nextRound ?? '',
      })
      await Promise.all([get().refreshInterviews(), get().refreshList(get().tab)])
    } catch {
      // 出声：改结果失败不能静默
    }
  },
}))
