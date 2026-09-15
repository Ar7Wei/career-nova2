/**
 * 投递模块的展示常量（标签映射 + 选项闭集）。
 *
 * 与 `types/apply.ts` 配对：那里是数据模型，这里是「怎么显示」。原先一并住 `mocks/`，
 * 2026-09-11 C7 按「类型 → types/、展示常量 → lib/」归位。
 */

import type { FollowupReason, InterviewOutcome, JobSource } from '@/types/apply'

/** 岗位来源平台显示名。 */
export const JOB_SOURCE_LABELS: Record<JobSource, string> = {
  liepin: '猎聘',
  job51: '前程无忧',
}

/** 登录入口覆盖的平台（含猎聘——虽免登录抓取，但用户可能想点开岗位时已登录）。 */
export const LOGIN_SOURCES: JobSource[] = ['liepin', 'job51']

/** 不再追踪原因选项（负向终态内部分类）。 */
export const NOT_PURSUING_REASONS: { value: FollowupReason; label: string }[] = [
  { value: 'withdrawn', label: '主动放弃' },
  { value: 'failed', label: '未通过' },
  { value: 'job_closed', label: '岗位关闭' },
  { value: 'duplicate', label: '已投过' },
]

/** 面试场次结果显示标签。 */
export const INTERVIEW_OUTCOME_LABELS: Record<InterviewOutcome, string> = {
  scheduled: '待面',
  awaiting: '等结果',
  next_round: '下一轮',
  offered: '录用',
  failed: '未通过',
}
