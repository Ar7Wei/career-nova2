/**
 * 简历模块的数据模型（Suggestion / ChatMessage）。
 *
 * 纯类型，无「数据」语义——原先住 `mocks/`（开发迭代期的集中 mock 目录，2026-09-11 C7 归位）：
 * 上传/聊天/生成均已接真接口、mock 数据清零后只剩 schema，按 `types/` 约定落到这里。
 */

import { tNow } from '@/lib/i18n'

/** 一条优化建议（六类 schema，docs/design/resume.md §12.2）。 */
export interface Suggestion {
  type: 'quantify' | 'word_choice' | 'structure' | 'fill_gap' | 'job_relevance' | 'highlight'
  target: string
  original: string
  suggested: string
  reason: string
  severity: 'high' | 'medium' | 'low'
}

/** 优化点状态机（子项级）。终态 applied/archived 不在面板（已结清）。 */
export type ChangeStatus = 'pending' | 'confirmed' | 'rejected' | 'discussing' | 'applied' | 'archived'

/** 一个改动点（记录内的子项，**可独立定论**——操作粒度 = 单条子项）。
 *
 * id 在记录内唯一（子项定位用）；type/severity 供卡片展示改哪类、多要紧。
 * 对齐 change_records 的 `changes` JSON 列（后端 `app/schemas/optimization.py`）。 */
export interface ChangeItem {
  id: number
  target: string
  original: string
  suggested: string
  status: ChangeStatus
  type: Suggestion['type']
  severity: Suggestion['severity']
}

/** 一条改动记录：原因（为什么，主体）+ 改动点复数（改什么，可空）。
 *
 * 优化点与跨版本决策**同一张表**——靠 `kind` 分：`change` 本轮整改 / `decision` 持久决策。
 * 面板只展示带子项的记录（`decision` + 空子项是纯约束，不给用户看）。 */
export interface ChangeRecord {
  id: number
  reason: string
  changes: ChangeItem[]
  status: ChangeStatus
  kind: 'change' | 'decision'
  origin: 'agent' | 'job_analysis'
  document_id: number
  resolved_in_document_id: number | null
  updated_at: string
}

/** 聊天消息（简历页右栏）。text = 普通对话；event = 系统气泡·事件灰条；error = 系统气泡·错误红条；
 * divider = 版本分隔标记。
 * ADR 0017 系统气泡三档（2026-09-01）：event/error 是「系统气泡」族，区别于聊天气泡。
 * 2026-09-08：删 generate kind（「去生成」应急卡已退化，kind=upload 统一落事件灰条）。
 * 2026-09-09：删 conflict kind（冲突卡退化——冲突改对话内反问，不再单独渲染卡片）。 */
export type ChatMessage =
  | { id: string; kind: 'text'; role: 'user' | 'assistant'; content: string }
  | { id: string; kind: 'event'; role: 'event'; content: string }
  | { id: string; kind: 'error'; role: 'event'; content: string }
  | { id: string; kind: 'divider'; version: number | null }

/** 聊天框初始欢迎消息（空态气泡）。文案走 i18n `resume.welcome`（S9-2：随语言切换）。 */
export function initialWelcomeMessages(): ChatMessage[] {
  return [{ id: 'm0', kind: 'text', role: 'assistant', content: tNow('resume.welcome') }]
}
