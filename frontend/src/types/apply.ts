/**
 * 投递模块的数据模型（2026-08-13；2026-08-30 投递跟进定稿）。
 *
 * 纯类型，无「数据」语义——原先住 `mocks/`（那是开发迭代期的集中 mock 目录，
 * 2026-09-11 C7 归位）：后端 jobs/job_followup_events 接口早已接通、mock 数据清零后，
 * 只剩 schema 本身，按 `types/` 约定落到这里。展示常量见 `lib/applyLabels.ts`。
 *
 * 数据形态对齐 apply.md §4 + §12：jobs（源岗位）+ job_followup_events（状态时间线）。
 */

import type { ReportBlock } from '@/types/report_block'
import type { Suggestion } from '@/types/resume'

/** 岗位来源平台（v1：猎聘主 connector + 前程无忧走 Electron 读 DOM；BOSS/智联已砍 2026-08-31）。 */
export type JobSource = 'liepin' | 'job51'

/** 岗位生死：程序从平台爬的候选信号（可能错，closed 灰度可见）。 */
export type JobLiveness = 'active' | 'stale' | 'closed' | 'unknown'

/** 投递跟进状态（单一状态线，apply.md §12）。存储态 + 派生态 interviewed。 */
export type FollowupStatus = 'applied' | 'interviewing' | 'interviewed' | 'offered' | 'not_pursuing'

/** 不再追踪的原因（仅 status=not_pursuing 有值）。 */
export type FollowupReason = 'withdrawn' | 'failed' | 'job_closed' | 'duplicate'

/** 一条源岗位（apply.md §4 jobs 表）。跨源不去重——同一岗位多平台都留。 */
export interface Job {
  id: number
  /** 平台：liepin / job51。 */
  source: JobSource
  /** 平台岗位 ID（同源去重用：source + externalId 唯一）。 */
  externalId: string
  title: string
  company: string
  city: string
  /** 薪资（原始字符串，不强归一）。 */
  salaryText: string
  experience: string
  degree: string
  skills: string[]
  jobLabels: string[]
  welfare: string[]
  /** JD 正文（详情页读 DOM 抓取，可空）。 */
  description: string
  sourceUrl: string
  applyUrl: string
  /** 岗位生死（程序爬的候选信号）。 */
  liveness: JobLiveness
  /** 我们抓取时间（恒有，新鲜度地基）。 */
  lastSeenAt: string
  /** 是否本轮新增（增量标记，2026-08-13）。 */
  isNew: boolean
  /** 投递跟进当前态（2026-08-30，派生）；null = 未处理。 */
  followupStatus: FollowupStatus | null
  /** 不再追踪的原因（仅 followupStatus=not_pursuing 有值）。 */
  followupReason: FollowupReason | null
  /** 当前态变更时间（跟进 tab 排序键）。 */
  followupAt: string
  /** 首触的抓取轮次 id（首触即定，后轮不改）——投递页分析取「本轮那批」用。 */
  crawlRoundId: number | null
}

/** 面试场次结果（apply.md §12.10 / ADR 0006）。 */
export type InterviewOutcome = 'scheduled' | 'awaiting' | 'next_round' | 'offered' | 'failed'

/** 一场面试（日历的一个标注）。 */
export interface Interview {
  id: number
  jobId: number
  /** 面试开始时间（ISO 字符串）。 */
  scheduledAt: string
  location: string
  /** 轮次自由文本（「技术一面」「HR面」）。 */
  round: string
  outcome: InterviewOutcome
  nextRoundId: number | null
  /** 内嵌岗位摘要（日历详情弹窗联查用，后端 GET /interviews 返回）。 */
  job: JobBrief
}

/** 面试场次内嵌的岗位摘要（company/title/description）。 */
export interface JobBrief {
  company: string
  title: string
  description: string
}

/** 各状态徽标数（GET /jobs 顶层 counts）。interviewing = 待面试场次数。 */
export interface JobCounts {
  unprocessed: number
  applied: number
  interviewing: number
  offered: number
  not_pursuing: number
}

/** 列表排序方式（ADR 0013：已收敛为单「时间」维度，每 tab 一个语义时间键——
 *  未处理 time = 收录时间 last_seen_at；跟进 followup = 当前态事件 at）。
 *  freshness/relevance 维度已砍 2026-09-08。 */
export type JobSort = 'time' | 'followup'

/**
 * 投递页分析（apply.md §11.7，2026-09-14）。
 *
 * 分析 = 页面的一部分（顶部一行控件展开），不是"被送达的消息"。三 tab 三套分析：
 * 未处理（处方 + 总览）/ 已投递（日报）/ 面试（单场准备，落详情弹窗）。
 */

/** 分析 scope：batch 未处理批次 / daily 已投递日报 / interview 单场面试。 */
export type AnalysisScope = 'batch' | 'daily' | 'interview'

/** 报告就绪态：ready 有缓存 / computing 正在算（前端轮询）/ missing 没东西可算。 */
export type AnalysisStatus = 'ready' | 'computing' | 'missing'

/** 分析产出的「处方」——可被收录进优化点面板（status=proposed，点「改进」→ pending）。 */
export interface Prescription {
  id: number
  type: Suggestion['type']
  target: string
  original: string
  suggested: string
  reason: string
  severity: Suggestion['severity']
  status: 'proposed'
  origin: 'job_analysis'
}

/** 一份分析报告（GET /analysis/*）。headline 一句话 + blocks 正文块流 + meta 统计口径。 */
export interface AnalysisReport {
  scope: AnalysisScope
  scopeKey: string
  status: AnalysisStatus
  headline: string
  /** 正文块流（text/chart 穿插，前端经 toReportBlocks 校验兜底渲染，坏块降级不崩）。 */
  blocks: ReportBlock[]
  meta: {
    total?: number
    liepin?: number
    job51?: number
    withJd?: number
  }
  createdAt: string
  /** 仅 batch 有值：待收录的处方（点「改进」收录进优化点）。 */
  suggestions: Prescription[]
}

/** 一个求职方向（apply.md §11.2）：role 与 keywords 独立，互不派生。 */
export interface Direction {
  /** 目标岗位标签（写简历 target + 方向展示用，不参与搜索）。 */
  role: string
  /** 搜索查询组（二维，爬虫唯一真源）：组内 AND、组间 OR，可增删。 */
  keywords: string[][]
  /** 逐组城市（与 keywords 平行等长，ADR 0019）：`cities[i]` 是第 i 组的城市，逐组必填。 */
  cities: string[]
}
