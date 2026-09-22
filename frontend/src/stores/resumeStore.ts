import { notifications } from '@mantine/notifications'
import { create } from 'zustand'
import api, { AppApiError } from '@/lib/api'
import { emit } from '@/lib/events'
import { userErrorText } from '@/lib/errors'
import { initialWelcomeMessages, type ChatMessage } from '@/types/resume'
import { confirmDisposeJobs } from '@/components/apply/confirmDisposeJobs'
import { useDirectionStore } from '@/stores/directionStore'

/**
 * 简历页状态：管理当前简历文档（版本化）、抽取状态机、对话消息。
 *
 * 新架构（文档为成品，事实为唯一真相源）+ 抽取信息卡片（确认才入库）：
 * - 上传 = MarkItDown 转文档 v1（后端快路径，无 LLM），左栏立即显示。
 * - 后台异步抽事实 → 状态机驱动蒙版/进度条/卡片/通道开关。
 *   idle → parsing（上传 HTTP）→ extracting（后台抽取）→ extracted（弹卡片）
 *   → confirmed（确认入库，通道关闭）/ failed（失败，通道重开）。
 * - 卡片确认才入库（source=resume_upload，统一冲突检测）；拒绝该批不入库、通道重开。
 * - 生成/修改存新版本；可回滚（文档切版本，可选连事实快照还原）。
 */

/** 后端 /resume/parse 的响应形态（文档 v1，不阻塞等抽取）。 */
interface UploadResponse {
  document_id: number
  version: number
  markdown: string
  resume_name: string
  original_name: string
  original_ext: string
}

/** 抽取状态机的状态（与后端 ExtractState.state 对齐）。 */
export type ExtractPhase = 'idle' | 'parsing' | 'extracting' | 'extracted' | 'failed' | 'confirmed'

/** 抽取候选事实（卡片可编辑；确认才入库）。
 * 2026-08-07 重构：嵌套模型——title = 总条目/单句，points = 子要点数组（可空）。 */
export interface ExtractCandidate {
  category: 'basic' | 'education' | 'work' | 'projects' | 'skill' | 'other'
  title: string
  points: string[]
  /** 该不该上简历（后端抽取 LLM 判，ADR 0011）；默认 true */
  on_resume?: boolean
  /** 事实时间线（如 "2018-2021"/"2021-至今"）；无日期为空串。2026-09-21 接通。 */
  occurred_at?: string
}

/** 后端 GET /resume/extract-status 响应。 */
interface ExtractStatusResponse {
  state: ExtractPhase
  document_id: number | null
  generation: number
  facts: ExtractCandidate[]
  error: string | null
}

/** 后端增量式 /chat 的响应（session + 完整历史 + 建议数提示 + 待确认预览标记）。 */
interface ChatSessionResponse {
  session_id: number
  messages: BackendMessage[]
  suggestion_count?: number
  /** 是否有待确认的生成预览（agent 生成工具产出后，前端据此拉预览显示，2026-08-12）。 */
  preview_pending?: boolean
  /** 本轮 agent 是否写了新版本（rewrite_resume 写库成功，2026-08-13）：前端据此刷新左栏。 */
  version_changed?: boolean
  /** agent 落定方向后待处置的未处理岗位数（2026-08-25）：>0 前端就地弹岗位处置确认。 */
  pending_disposal_count?: number
  /** 本轮 agent 是否落定了方向（2026-08-30）：true → 前端刷新 directionStore 跨页同步方向标签。 */
  direction_changed?: boolean
}

/** 后端消息（role/content/kind）。event = 系统气泡（ADR 0017 三档：事件灰条/错误红条/系统卡片，按 kind 判定）。 */
interface BackendMessage {
  role: 'user' | 'assistant' | 'system' | 'event'
  content: string
  /** 结构化动作类型（仅 event 行）：upload/generated/rolled_back/suggestion_hint/error_* 等，三档呈现判定用。 */
  kind?: string
  /** 事件引用的文档 id（仅 event 行；rolled_back = 被覆盖稿）。 */
  ref_document_id?: number | null
}

/** 后端 POST /chat/stop 响应（2026-09-09 撤回）。retracted=true 时 retracted_text 回填输入框。 */
interface ChatStopResponse {
  stopped: boolean
  retracted: boolean
  retracted_text: string
}

/** 后端消息 → 前端 ChatMessage（id 本地生成）。
 * ADR 0017 系统气泡三档（按 kind 判定，不靠文案匹配）：
 * - error_* → 错误红条（kind='error'）。
 * - 其它 event（upload/generated/rolled_back/suggestion_hint/…）→ 事件灰条（kind='event'）。
 * 2026-09-08：删「去生成」应急卡——ADR 0012 后零文档也能生成、冷启动「开始改」自动转生成，
 * 那堵「没有结构化简历」的死路已消解，kind=upload 统一落事件灰条（不再升级为 generate 卡）。
 */
function toChatMessages(msgs: BackendMessage[]): ChatMessage[] {
  return msgs.map((m) => {
    if (m.role === 'event') {
      const kind = m.kind ?? ''
      if (kind.startsWith('error_')) {
        return { id: nid('x'), kind: 'error' as const, role: 'event' as const, content: m.content }
      }
      return { id: nid('e'), kind: 'event' as const, role: 'event' as const, content: m.content }
    }
    return { id: nid(m.role === 'user' ? 'u' : 'a'), kind: 'text' as const, role: m.role === 'user' ? 'user' : 'assistant', content: m.content }
  })
}

/** 后端 /documents/versions 里的一版。 */
/** 排版自由度五参数（2026-09-02 grill）：scale 字号倍率 / lineHeight 行距倍率 / spacing 段距倍率 /
    letterSpacing 字间距 px / gutter 栏距 px（左右两栏缝隙）。 */
export interface Typography {
  scale: number
  lineHeight: number
  spacing: number
  letterSpacing: number
  gutter: number
}
/** 默认排版配置（与后端 Typography() 对齐）。 */
export const DEFAULT_TYPOGRAPHY: Typography = { scale: 1, lineHeight: 1.25, spacing: 1, letterSpacing: 0, gutter: 55 }

/** 后端 /documents/versions 里的一版。 */
interface ResumeVersion {
  id: number
  version: number
  markdown: string
  /** 结构化真身（2026-08-28 排版转向）：生成版的结构化 resume JSON；上传 v1 为空。 */
  resume_json?: string
  /** 渲染快照（2026-08-28）：固定模板从 resume_json 渲染的 HTML；上传 v1 为空。 */
  html: string
  /** 排版自由度配置（2026-09-02 四参数挂版本）：scale/lineHeight/spacing/letterSpacing；上传 v1 为默认。 */
  typography?: Typography
  /** 一句话版本简述（Git 意味，S8 2026-08-14）；旧数据空串。 */
  summary: string
  source: 'upload' | 'generated' | 'rollback'
  original_name: string
  original_ext: string
  created_at: string
}

/** 回看模式状态：某版本当时的文档 + 聊天记录（只读，纯查看不改变状态）。
 * 从当前对话状态解耦——回看不碰 currentVersion/messages/sessionId。 */
interface ReviewState {
  /** 文档唯一身份（2026-08-12 A3 身份锚定）：回滚/取原件按 id，不按可复用的 version。 */
  id: number
  version: number
  source: ResumeVersion['source']
  /** 一句话版本简述（S8）。 */
  summary: string
  markdown: string
  /** 排版层 HTML（该稿当时的排版产物；空 = 无排版层）。 */
  html: string
  originalName: string
  originalExt: string
  resumeUrl: string | null
  resumeExt: string
  /** 该版本所有 session 的消息（回看当时的聊天，可能多个 session 合并）。 */
  messages: ChatMessage[]
}

/** 卡片编辑后提交确认的载荷（含代次防陈旧）。 */
interface ResumeState {
  /** 当前简历文档版本号（v1/v2/…）。0 = 未上传。 */
  currentVersion: number
  /** 当前版本来源（2026-08-24 下载门控用）：upload=上传最初稿（不给下载）/ generated=生成稿 / rollback=回滚稿。0 版本时为 ''。 */
  currentSource: 'upload' | 'generated' | 'rollback' | ''
  /** 当前简历文件名。 */
  resumeName: string
  /** 当前简历原始文件 blob URL（PDF/HTML 左栏原生预览用；重启后由原件重建）。 */
  resumeUrl: string | null
  /** 当前简历文件扩展名（驱动左栏预览分档）。 */
  resumeExt: string
  /** 当前简历上传原件文件名（持久化元数据，重启后仍可原生预览）。 */
  originalName: string
  /** 当前简历上传原件扩展名（驱动预览分档；无原件/生成版为空）。 */
  originalExt: string
  /** 当前简历文档的 Markdown（内容层，左栏预览兜底 + 1.2 锚点）。 */
  previewMarkdown: string
  /** 当前简历文档的排版层 HTML（2026-08-13 双源分层）：排版 agent 渲染，左栏 iframe 展示；空 = 无排版层）。 */
  previewHtml: string
  /** 排版自由度配置（2026-09-02 挂版本，排版参数/数据层）：当前版四参数（字号/行距/段距/字间距）。
   *  调它 = 改内容排版（重排、页数变），防抖回后端重渲染落库，导出关联。与控件 B 整页缩放（纯预览镜头）独立。 */
  typography: Typography
  /** 全部版本列表。 */
  versions: ResumeVersion[]
  /** 版本列表是否已加载。 */
  versionsLoaded: boolean
  messages: ChatMessage[]
  /** 聊天输入框内容（2026-09-09 提升到 store）：停止撤回时原文回填的通道。 */
  draft: string
  /** 当前对话 session id（增量式聊天；版本变更后开新 session）。 */
  sessionId: number | null
  /** 回看模式（2026-08-10，干净轮回 + 历史归档）：非 null = 正在回看某版本。 */
  review: ReviewState | null
  /** 抽取状态机：驱动蒙版/进度条/卡片/通道开关。 */
  extractPhase: ExtractPhase
  /** 当前抽取代次（确认/拒绝防陈旧）。 */
  extractGeneration: number
  /** 抽取候选事实（卡片展示/编辑；确认才入库）。 */
  extractFacts: ExtractCandidate[]
  /** 抽取失败原因。 */
  extractError: string | null
  generating: boolean
  /** 开场白后台生成中（确认入库后 fire-and-forget，§11.8）——驱动聊天框「组织开场」气泡 + 暂停。 */
  openingPending: boolean
  /** 应用建议改写中（「开始改」进行中，S7 统一锁定信号之一，2026-08-14）。
   * 与 generating 并列——apply 期间锁建议操作 + 聊天发送 + 回滚等写动作。 */
  applying: boolean
  /** 待确认的生成预览（agent 生成工具产出暂存预览态，2026-08-12）：非空 = 显示预览 + 「确认保存」。 */
  previewPending: boolean
  /** 确认/带意见重改预览进行中（2026-09-07 统一「出简历」人门）：锁定确认条防重复点。 */
  confirming: boolean
  /** 生成预览内容（agent 工具产出、未写库；确认保存 → 写库新稿 → 清空）。 */
  previewContent: string

  uploadResume: (file: File) => Promise<void>
  cancelUpload: () => void
  confirmExtract: (facts: ExtractCandidate[]) => Promise<void>
  rejectExtract: () => Promise<void>
  retryExtract: () => Promise<void>
  removeResume: () => void
  sendMessage: (text: string) => void
  stopGenerating: (retract?: boolean) => void
  /** 设置聊天输入框内容（2026-09-09 draft 提升 store；停止撤回回填用）。 */
  setDraft: (text: string) => void
  /** 暂停开场白后台生成（2026-08-30）：清气泡 + 停轮询 + 后端取消在途 LLM。 */
  stopOpening: () => void
  /** 应用已确认建议改写简历（S7，2026-08-14；ADR 0015 折进统一图，2026-09-09）：提 `applying` 到 store，
   *  出预览态（previewPending），确认走 confirmGeneration（不再直接落库）。 */
  applySuggestions: () => Promise<void>
  /** 拉当前暂存生成预览（agent 生成工具产出后显示 + 「确认保存」入口，2026-08-12）。 */
  refreshPreview: () => Promise<void>
  /** 确认建议（pending/discussing → confirmed，按 id，2026-08-10 去重）。 */
  acceptSuggestion: (suggestionId: number) => Promise<void>
  /** 拒绝建议（→ rejected + 记偏好，按 id）。 */
  rejectSuggestion: (suggestionId: number, reason?: string) => Promise<void>
  /** 聊一聊（pending → discussing，面板右栏「正在聊」区）。 */
  startDiscuss: (suggestionId: number) => Promise<void>
  loadVersions: () => Promise<void>
  loadCurrent: () => Promise<void>
  /** 调排版自由度（2026-09-02）：本地即时改 typography（预览注入即时变），防抖 500ms 回后端
   *  重渲染落库（html 带新排版变量，导出/重开吃这份）。只挂当前版，上传原件不调（前端已置灰）。 */
  setTypography: (typography: Typography) => void

  /** @param stop 切到新 session 前是否按停按钮（默认 true = 干净轮回边界）。
   *  挂载恢复传 false：此时可能有切页前发起的生成还在后台跑，切回来不该误杀它。 */
  loadChatHistory: (stop?: boolean) => Promise<void>
  /** 悬浮卡片里的待执行建议数（回滚前警告用）。 */
  pendingSuggestionCount: number
  loadPendingCount: () => Promise<void>
  rollbackTo: (documentId: number, includeFacts?: boolean) => Promise<boolean>
  /** 确认生成预览：decision=confirm 写库 vN（版本变更）；decision=revise 带 feedback 重改
   *  （2026-09-07 统一「出简历」人门），重改出新预览不出版本。返回版本号，revise/失败返回 null。 */
  confirmGeneration: (decision?: 'confirm' | 'revise', feedback?: string) => Promise<number | null>
  /** 停止在途「重新生成」（revise 回边，2026-09-08）：点停止 → 后端取消图重改、清草稿。
   *  本地即时置 confirming=false（按钮从「停止」恢复「重新生成」）；in-flight 的
   *  confirmGeneration 正常返回（后端取消后 200 version=null）→ refreshPreview 读到空 →
   *  预览条收起、回落到上一版已保存文档。 */
  stopRegenerating: () => void
  /** 进入回看模式：拉某版本当时的文档原件 + 聊天记录（只读）。 */
  openReview: (documentId: number) => Promise<void>
  /** 退出回看模式：回到当前版本状态（不改变任何后端状态）。 */
  closeReview: () => void
}

/**
 * 「正在工作」信号（2026-08-30）：把 agent 各种在干活的状态统一成一个纯视觉的 kind，
 * 聊天框右下角按钮 + 思考气泡是它的唯一出口，左栏解析卡 / 建议面板遮罩降为投影。
 *
 * 关键：**这是视觉信号，不是锁**。锁（`generating` / `applying` 决定能不能发消息/操作）
 * 与它解耦——所以「解析/抽取」能亮气泡却不禁用输入（后台抽取不耽误聊天）。
 */
export type WorkingKind = 'parsing' | 'extracting' | 'opening' | 'replying' | 'applying'

/** 从既有状态派生当前「正在做什么」（不单独存、不手动同步，避免双真相源）。 */
export function selectWorkingKind(s: Pick<ResumeState, 'applying' | 'generating' | 'openingPending' | 'extractPhase'>): WorkingKind | null {
  if (s.applying) return 'applying'
  if (s.generating) return 'replying'
  if (s.openingPending) return 'opening'
  if (s.extractPhase === 'parsing') return 'parsing'
  if (s.extractPhase === 'extracting') return 'extracting'
  return null
}

/** 当前工作是否可暂停（小飞机变暂停按钮）。仅对话回复 + 开场白可取消；解析/应用建议不可。 */
export function selectWorkingStoppable(s: Pick<ResumeState, 'applying' | 'generating' | 'openingPending' | 'extractPhase'>): boolean {
  const k = selectWorkingKind(s)
  return k === 'replying' || k === 'opening'
}

let seq = 100
const nid = (p: string) => `${p}${seq++}`

/** 排版自由度防抖计时器（模块级）：调排版先本地即时改，停手 500ms 才回后端重渲染落库。 */
let typographyTimer: ReturnType<typeof setTimeout> | null = null

/** 从文件名取扩展名（小写，无点），驱动左栏预览分档。 */
function extOf(name: string): string {
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot + 1).toLowerCase() : ''
}

/** 原生预览格式：走 blob URL（pdf/html 浏览器原生渲染原件，txt/md 读 blob 原文）。
 * 其余（docx/pptx/xlsx/csv）浏览器渲染不了原件 → 显示后端 Markdown 兜底。 */
const NATIVE_PREVIEW_EXTS = new Set(['pdf', 'html', 'txt', 'md'])

/** 追加一条助手文本消息。 */
function pushAssistant(s: ResumeState, content: string): ChatMessage[] {
  return [...s.messages, { id: nid('a'), kind: 'text', role: 'assistant', content }]
}

/** 追加一条本地「错误红条」（ADR 0017 系统气泡·错误档）。
 * 用于同步动作失败的**即时反馈**——后端同一次失败已由全局 handler 落库错误事件
 * （kind=error_*），刷新后由 loadChatHistory 带回同款红条；这里本地先补一条，
 * 让用户立刻看到，不必等刷新。kind='error' 与持久化形态一致（区别于聊天气泡）。 */
function pushError(s: ResumeState, content: string): ChatMessage[] {
  return [...s.messages, { id: nid('x'), kind: 'error', role: 'event', content }]
}

/** 删掉消息流里最后一条 user 消息（撤回即时反馈，2026-09-09）。
 * 后端已真删库（stop_chat retract）；这里本地同步删掉那条 user 气泡，避免它留在流里
 * 直到下次 loadChatHistory 才消失。只删 user 文本，不动 assistant/event。 */
function removeLastUserMessage(msgs: ChatMessage[]): ChatMessage[] {
  const idx = msgs.findLastIndex((m) => m.kind === 'text' && m.role === 'user')
  if (idx < 0) return msgs
  return [...msgs.slice(0, idx), ...msgs.slice(idx + 1)]
}

/** 调真接口上传：文档 v1 立即返回，后台异步抽事实（不阻塞）。 */
async function runUpload(file: File, signal?: AbortSignal): Promise<UploadResponse> {
  const form = new FormData()
  form.append('file', file)
  const { data } = await api.post<UploadResponse>('/v1/resume/parse', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
    signal,
  })
  return data
}

/** 把上传失败（AppApiError）转成给用户的提示文案。 */
function uploadErrorMessage(err: unknown): string {
  // 主动取消（重传/取消按钮）→ 不上报错误消息
  if ((err as { code?: string })?.code === 'ERR_CANCELED') return ''
  if (err instanceof AppApiError) {
    // 具体状态码 → 定向文案（保留原有针对性）；其余走统一契约 message。
    const detail = err.detail
    switch (err.status) {
      case 415:
        return `这个文件格式我处理不了：${detail || '仅支持 TXT/MD/PDF/DOCX/HTML/CSV/PPTX/XLSX'}。`
      case 422:
        return `没能从这份文件里读出文字（${detail || '疑似图片型/扫描件'}）。可以换文本型 PDF，或直接打字告诉我。`
      case 413:
        return '文件太大了（上限 20MB）。'
      case 409:
        return err.message || '已有一份简历，换简历请先重置。'
    }
    return err.message || '上传出了点问题，请再试一次，或换个文件。'
  }
  return userErrorText(err)
}

// 抽取状态轮询句柄（模块级，避免 store 外存 timer）
let pollTimer: ReturnType<typeof setInterval> | null = null
/** 当前上传的 AbortController（取消/重传用）。 */
let uploadAbort: AbortController | null = null

/** 在途聊天作废计数：版本变更（回滚/确认生成/修复排版/重置）或停止时递增。
 * sendMessage 发送时快照、响应返回时比对——期间切了版本说明旧对话已作废，
 * 旧 session 的回复属于旧轮，不得灌进新轮（否则回退后旧回复跑进新版本）。 */
let chatGeneration = 0

// 引导短轮询句柄（确认入库后台引导的兜底拉取，见 pollForGuide）
let guidePollTimer: ReturnType<typeof setInterval> | null = null

/** 确认入库后的引导短轮询：后端把引导生成改成后台 fire-and-forget（§11.8），
 * 引导可能晚于确认响应落库。这里在确认后短轮询几次 loadChatHistory 把它带回来，
 * 一旦「刷新后消息条数仍不变」就停（引导已到 / 已失败 / 已到窗口末尾）。
 * 2026-08-29：上传开场改为聊天 agent 工具循环（先抓一轮优化点再出开场白 = 两次 LLM
 * 往返），落库比旧一次性开场慢（实测 30s+）。窗口从 5×2s 提到 15×3s，仍设上限兜底不永久转。 */
function pollForGuide(): void {
  stopGuidePoll()
  // 开场白在后台生成 → 点亮聊天框「组织开场」气泡 + 暂停（stopOpening 可取消）。
  useResumeStore.setState({ openingPending: true })
  let remaining = 15
  guidePollTimer = setInterval(async () => {
    remaining -= 1
    const before = useResumeStore.getState().messages.length
    await useResumeStore.getState().loadChatHistory(false)
    const after = useResumeStore.getState().messages.length
    // 引导已到（或刷到了更多消息）→ 停；否则到次数上限也停（兜底，不永久转）
    if (after > before || remaining <= 0) stopGuidePoll()
  }, 3000)
}

function stopGuidePoll(): void {
  if (guidePollTimer) {
    clearInterval(guidePollTimer)
    guidePollTimer = null
  }
  // 轮询停（引导已到 / 超时 / 用户暂停）→ 熄掉「组织开场」气泡
  useResumeStore.setState({ openingPending: false })
}

/** 清掉后端文档流（重传/取消/移除前调用），保留事实；失败不阻断后续上传（409 兜底）。 */
async function clearDocumentStream(): Promise<void> {
  try {
    await api.post('/v1/documents/reset', { clear_facts: false })
  } catch {
    // 重置失败不阻断：真正的上传会带 409 等兜底，这里保持静默（避免"清流失败"吓到用户）
  }
}

/** S9-1（2026-08-13）：上传的安全时序——先上传；仅当后端因「通道被占/已有文档流」
 * 拒绝（409）时，才清旧流并重试一次。避免旧实现「先清库后上传」的破坏性窗口：
 * 清成功但上传失败（415/422/网络断）→ 旧简历已删、新的没传上。 */
async function runUploadSafely(file: File, signal: AbortSignal) {
  try {
    return await runUpload(file, signal)
  } catch (err) {
    // 409 = 通道里有旧文档流占位 → 清掉旧流后重试一次（此时上传还没成功，清库是安全的）
    if (err instanceof AppApiError && err.status === 409) {
      await clearDocumentStream()
      return await runUpload(file, signal)
    }
    throw err
  }
}

/** 轮询抽取状态：idle/extracting → extracted/failed 停止；驱动卡片/蒙版。
 * 连续失败超过阈值 → 报错停止轮询（不再永久静默转圈）。 */
const POLL_FAIL_LIMIT = 3

function startPolling(): void {
  stopPolling()
  let failCount = 0
  pollTimer = setInterval(async () => {
    try {
      const { data } = await api.get<ExtractStatusResponse>('/v1/resume/extract-status')
      setExtractPhase(data)
      failCount = 0
      if (data.state === 'extracted' || data.state === 'failed' || data.state === 'confirmed') {
        stopPolling()
      }
    } catch (err) {
      // 连续失败：后端不可达 → 报错停止轮询（给出路：可重试/重传）
      failCount += 1
      if (failCount >= POLL_FAIL_LIMIT) {
        stopPolling()
        // 模块级函数：用 useResumeStore.setState（同 setExtractPhase），set 不在此作用域。
        useResumeStore.setState((s) => ({
          extractPhase: 'failed',
          extractError: userErrorText(err),
          messages: pushError(s, '暂时连不上后端，抽取结果没拿到。可以稍后重新抽取，或重传简历。'),
        }))
      }
    }
  }, 2000)
}

function stopPolling(): void {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

/** 用后端状态刷新 store（轮询回调）。 */
function setExtractPhase(data: ExtractStatusResponse): void {
  useResumeStore.setState((s) => ({
    extractPhase: data.state,
    extractGeneration: data.generation ?? s.extractGeneration,
    extractFacts: data.facts,
    extractError: data.error,
    generating: s.generating,
  }))
}

export const useResumeStore = create<ResumeState>((set, get) => ({
  currentVersion: 0,
  currentSource: '',
  resumeName: '',
  resumeUrl: null,
  resumeExt: '',
  originalName: '',
  originalExt: '',
  previewMarkdown: '',
  previewHtml: '',
  typography: DEFAULT_TYPOGRAPHY,
  versions: [],
  versionsLoaded: false,
  messages: initialWelcomeMessages(),
  draft: '',
  sessionId: null,
  review: null,
  pendingSuggestionCount: 0,
  extractPhase: 'idle',
  extractGeneration: 0,
  extractFacts: [],
  extractError: null,
  generating: false,
  openingPending: false,
  applying: false,
  /** 确认/带意见重改生成预览进行中（2026-09-07 统一「出简历」人门）：锁定确认条防重复点。 */
  confirming: false,
  previewPending: false,
  previewContent: '',

  /** 上传简历：后端转文档 v1（快），左栏显示，后台异步抽事实（状态机驱动）。
   *
   * 重传/取消前先重置后端文档流（清掉旧 v1），让新文件从 v1 重新开始——
   * 否则封闭通道下旧文档流还在，版本号会一直涨。
   */
  uploadResume: async (file) => {
    // 已有抽取在跑：先取消（旧抽取作废，后端代次递增）
    stopPolling()
    uploadAbort?.abort()
    uploadAbort = null
    // 先进入解析态（蒙版 + 进度条），再清后端旧文档流
    const prev = useResumeStore.getState().resumeUrl
    if (prev) URL.revokeObjectURL(prev)
    const originalExt = extOf(file.name)
    // 原生预览格式（pdf/html/txt/md）用 blob 预览；DOCX 等走 Markdown
    const isNativePreview = NATIVE_PREVIEW_EXTS.has(originalExt)
    const url = isNativePreview ? URL.createObjectURL(file) : null
    set({
      resumeName: file.name,
      resumeUrl: url,
      resumeExt: isNativePreview ? originalExt : '',
      originalName: file.name,
      originalExt,
      currentVersion: 0,
      versions: [],
      versionsLoaded: false,
      extractPhase: 'parsing',
      extractFacts: [],
      extractError: null,
    })
    // 不再「先清库后上传」（S9-1）：直接上传，仅当后端 409（旧流占位）才清库重试——
    // 见 runUploadSafely。消除「清成功但上传失败 → 旧简历已删、新的没传上」的破坏性窗口。
    const controller = new AbortController()
    uploadAbort = controller
    try {
      const data = await runUploadSafely(file, controller.signal)
      // 上传成功：文档 v1 就绪，后台抽取开始 → extracting（蒙版 + 进度条）
      // 原件元数据以后端 sanitize 后的为准（防路径穿越），覆盖本地初值
      const serverExt = data.original_ext
      // resumeExt 只驱动原生预览格式（pdf/html/txt/md）；其余格式走 Markdown
      const isNative = NATIVE_PREVIEW_EXTS.has(serverExt)
      set({
        currentVersion: data.version,
        currentSource: 'upload',
        previewMarkdown: data.markdown,
        previewHtml: '', // 上传 v1 无排版层（首次编辑时才有 HTML）
        typography: DEFAULT_TYPOGRAPHY, // 上传原件无排版参数，回默认
        originalName: data.original_name,
        originalExt: serverExt,
        resumeExt: isNative ? serverExt : '',
        extractPhase: 'extracting',
      })
      // 上传成功 = 后端已落库「已上传简历」event 气泡（§11.3）。**不本地 push 上传/转换气泡**
      // （本地 push 与后端 event 是两份不重合的真相源，确认时 loadChatHistory 覆盖会冲掉）。
      // 上传/确认两条 event 灰条都在确认时随 loadChatHistory 一起拉回，干净不重刷。
      // 开始轮询抽取状态 → 到 extracted/failed/confirmed 停止
      startPolling()
    } catch (err) {
      const msg = uploadErrorMessage(err)
      set((s) => ({
        extractPhase: 'idle',
        messages: msg ? pushError(s, msg) : s.messages,
      }))
    }
  },

  /** 取消当前上传（解析窗口内反悔；后端代次作废旧抽取）。清掉后端文档流 v1。 */
  cancelUpload: () => {
    uploadAbort?.abort()
    uploadAbort = null
    stopPolling()
    useResumeStore.getState().stopGenerating() // 取消/清流 = 对话边界作废（把停止按钮按下去）
    void clearDocumentStream()
    set({ extractPhase: 'idle', extractFacts: [], extractError: null, currentVersion: 0, currentSource: '', resumeName: '', resumeUrl: null, resumeExt: '', originalName: '', originalExt: '', previewMarkdown: '', previewHtml: '', typography: DEFAULT_TYPOGRAPHY })
  },

  /** 确认抽取卡片：提交编辑后的事实入库，关闭上传通道（confirmed）。 */
  confirmExtract: async (facts) => {
    const gen = get().extractGeneration
    try {
      await api.post<{ saved: number; state: 'confirmed' }>('/v1/resume/extract/confirm', {
        generation: gen,
        facts,
      })
      stopPolling()
      set({
        extractPhase: 'confirmed',
        extractFacts: [],
        extractError: null,
      })
      // 事实入库 → 资料集角标刷新（事件驱动，2026-08-12）
      emit('resume-data-changed')
      // 确认入库 = 分阶段引导触发点（§11.8）。后端 confirm 已落库「已确认 N 条」event 气泡，
      // 引导是后台异步生成（可能晚于确认响应）。**不再本地 push 确认气泡**（与后端 event 是
      // 两份不重合的真相源，覆盖时会冲掉、且制造「切 session」观感）——直接拉后端历史，
      // 让事件灰条自然显示，再短轮询几次把晚到的引导兜回来。
      await useResumeStore.getState().loadChatHistory(false)
      pollForGuide()
    } catch (err) {
      set((s) => ({ messages: pushError(s, userErrorText(err)) }))
    }
  },

  /** 拒绝抽取卡片：该批不入库，通道重开（可重传）。 */
  rejectExtract: async () => {
    const gen = get().extractGeneration
    try {
      await api.post('/v1/resume/extract/reject', { generation: gen })
      stopPolling()
      // 「已拒绝这批信息」由后端 record_current_event 落库（resume.py），
      // 这里**不本地 push**（本地 push 与后端 event 是两份不重合的真相源，刷新会冲掉）。
      set({
        extractPhase: 'idle',
        currentVersion: 0,
        currentSource: '',
        resumeName: '',
        resumeUrl: null,
        resumeExt: '',
        originalName: '',
        originalExt: '',
        previewMarkdown: '',
        previewHtml: '',
        versions: [],
        versionsLoaded: false,
        extractFacts: [],
        extractError: null,
      })
    } catch (err) {
      set((s) => ({ messages: pushError(s, `操作失败：${userErrorText(err)}`) }))
    }
  },

  /** 重新抽取：对当前文档重抽（不重传、不清文档流）。后端代次 +1 作废旧抽取。 */
  retryExtract: async () => {
    try {
      const { data } = await api.post<{ generation: number }>('/v1/resume/extract/retry')
      set({ extractPhase: 'extracting', extractFacts: [], extractError: null, extractGeneration: data.generation })
      startPolling()
    } catch (err) {
      set((s) => ({ messages: pushError(s, userErrorText(err)) }))
    }
  },

  /** 移除当前简历：放弃这份（解析窗口内），清后端文档流 + 快照 + UI 态。
   *
   * 不清后端的话，移除后重传会 max(version)+1 堆版本（v1→v2→v3…）。
   * 保留事实（候选未入库，已入库旧事实留着）。
   */
  removeResume: async () => {
    uploadAbort?.abort()
    uploadAbort = null
    stopPolling()
    useResumeStore.getState().stopGenerating() // 移除当前简历 = 对话边界作废（把停止按钮按下去）
    void clearDocumentStream()
    const prev = useResumeStore.getState().resumeUrl
    if (prev) URL.revokeObjectURL(prev)
    set({
      currentVersion: 0,
      currentSource: '',
      resumeName: '',
      resumeUrl: null,
      resumeExt: '',
      originalName: '',
      originalExt: '',
      previewMarkdown: '',
      previewHtml: '',
      versions: [],
      versionsLoaded: false,
      extractPhase: 'idle',
      extractFacts: [],
      extractError: null,
      generating: false,
      openingPending: false,
    })
  },

  /** 发消息：增量式 /chat——服务端持有唯一历史；返回该 session 完整历史。
   *
   * 客户端只发一条新消息；服务端读全量历史 → 跑对话 agent（工具循环）→ 持久化回复。
   * 2026-08-10：建议组落库（suggest_improvements 产出即进面板），聊天响应只带
   * suggestion_count（面板待定 + 正在聊的建议数），前端提示「去面板查看」。
   */
  sendMessage: async (text) => {
    // S7 统一锁定：busy（生成中/应用建议改写中）不许发消息——防止新的信息/建议在
    // 改写窗口溜进来，也防止 agent 正在跑时再叠一条消息。
    if (get().generating || get().applying) return
    const prev = useResumeStore.getState().sessionId
    // 快照本轮世代号：响应返回时若已切版本（世代号变了），旧 session 回复作废丢弃
    const generation = chatGeneration
    set((s) => ({
      messages: [...s.messages, { id: nid('u'), kind: 'text', role: 'user', content: text }],
      generating: true,
    }))
    try {
      const { data } = await api.post<ChatSessionResponse>('/v1/chat', {
        session_id: prev,
        message: { role: 'user', content: text },
      })
      // 在途期间发生了版本变更（回滚/确认生成/重置）：旧 session 的回复已属旧轮，
      // 直接丢弃——当前聊天框已是新 session 的事件，不能被旧回复覆盖。
      if (generation !== chatGeneration) return
      // 用服务端返回的完整历史重建（session 可能新建/变更）。
      // ADR 0017：建议提示已由后端落库为 suggestion_hint 事件（在 data.messages 里），
      // 不再前端硬编码 assistant 文本气泡；upload 事件统一落事件灰条（2026-09-08 删去生成卡）。
      const base = toChatMessages(data.messages)
      set({
        sessionId: data.session_id,
        messages: base,
        generating: false,
      })
      // 2026-08-25：agent 落定方向后有未处理岗位待处置 → 就地弹「留/删」确认（岗位处置
      // 与 agent 无关，由后端 commit 后返回的 pending_disposal_count 驱动）。
      if (data.pending_disposal_count && data.pending_disposal_count > 0) {
        confirmDisposeJobs(data.pending_disposal_count, async () => {
          await api.post('/v1/direction/dispose-unprocessed')
        })
      }
      // 2026-08-30：agent 落定了方向 → 刷新 directionStore，让投递页检索控制台标签
      // （关键词组数 · 城市）跨页/最小化也能同步到最新方向。
      // 2026-09-13：一并刷新城市库——面板开过一次后 store.cities 已是非空旧值，不再随面板
      // 挂载兜底拉；若此处不拉，城市库的后续更新不会进下拉（只在首次挂载时拉一次会漏）。
      if (data.direction_changed) {
        void useDirectionStore.getState().load()
        void useDirectionStore.getState().loadCities()
      }
      // 2026-08-12：agent 生成了待确认预览 → 拉预览内容显示（确认保存入口）
      if (data.preview_pending) {
        void useResumeStore.getState().refreshPreview()
      }
      // 2026-08-13：agent 改了简历写了新版本（rewrite_resume 工具）→ 刷新左栏预览
      // + 版本列表 + 切新 session（后端已开新 session + 事件，loadChatHistory 带回来）。
      if (data.version_changed) {
        void useResumeStore.getState().loadCurrent()
        void useResumeStore.getState().loadVersions()
        void useResumeStore.getState().loadChatHistory()
      }
      // 消息往返后 agent 可能记了事实 / 产了建议 → 资料集 & 优化点角标刷新
      emit('resume-data-changed')
    } catch (err) {
      // 在途期间已切版本（回滚/重置）：旧 session 的失败提示同样作废，不得灌进新 session
      if (generation !== chatGeneration) return
      set((s) => ({
        generating: false,
        // 聊天内失败留在聊天流内（assistant 文本）——
        // 这是「助手没能回复」，不是「系统操作」，不走系统气泡红条。
        messages: pushAssistant(s, userErrorText(err)),
      }))
    }
  },

  /** 接受一条建议：pending/discussing → confirmed（右栏已确认）。按 id（2026-08-10 去重）。 */
  acceptSuggestion: async (suggestionId: number) => {
    try {
      await api.post('/v1/optimization/accept', { suggestion_id: suggestionId })
    } catch (err) {
      // 接受失败：不静默——否则建议收了还是没收用户不知道
      set((prev) => ({ messages: pushError(prev, `确认建议失败：${userErrorText(err)}`) }))
    }
  },

  /** 拒绝一条建议：pending/discussing → rejected + 记偏好（同类不再建议）。按 id。 */
  rejectSuggestion: async (suggestionId: number, reason = '') => {
    try {
      await api.post('/v1/optimization/reject', { suggestion_id: suggestionId, reason })
    } catch (err) {
      // 拒绝失败：不静默（但这是轻量动作，提示即可）
      set((prev) => ({ messages: pushError(prev, `记录拒绝失败：${userErrorText(err)}`) }))
    }
  },

  /** 聊一聊：pending → discussing（面板右栏「正在聊」区）。用户在聊天框提问，agent 针对性讨论。 */
  startDiscuss: async (suggestionId: number) => {
    try {
      await api.post('/v1/optimization/discuss', { suggestion_id: suggestionId })
    } catch (err) {
      set((prev) => ({ messages: pushError(prev, `进入聊一聊失败：${userErrorText(err)}`) }))
    }
  },

  /** 挂载恢复 + 版本变更后切新轮：拉**当前 session**（id 最大）的消息。
   *
   * 2026-08-10 决策修订（§11.1「干净轮回 + 历史归档」）：不再聚合全部 session 历史
   * （旧决策「UI 连续显示所有 session 历史 + 分隔标记」已推翻）——版本变更后聊天框
   * 干净开新轮，旧 session 历史走「回看」入口（版本面板）。空 session → 欢迎语。
   */
  loadChatHistory: async (stop = true) => {
    // 切当前 session = 干净轮回边界（回滚/确认生成/修复排版/应用建议都经这里）：
    // 若有在途对话，把停止按钮按下去（真停后端 generation + 作废在途回复），
    // 旧 session 的在途回复不得灌进新 session。无在途时幂等无害。
    // stop=false（挂载恢复）：切页前发起的生成仍在后台跑，切回来不该把它按停——
    // 否则「生成中切走 → 切回来」会被误杀（§2 用户反馈）。
    if (stop) useResumeStore.getState().stopGenerating()
    try {
      const { data } = await api.get<{ sessions: { id: number; document_id: number | null }[] }>('/v1/sessions')
      const list = data.sessions
      if (list.length === 0) {
        // 冷启动（无 session）：静态欢迎语（§11.8 分阶段引导只挂在动作时刻落库，
        // 冷启动无 session 可挂靠，由 i18n 欢迎语兜底）。
        set({ sessionId: null, messages: initialWelcomeMessages() })
        return
      }
      // 当前 session = id 最大那个；只拉它的消息（干净轮回，不聚合历史）。
      // 分阶段引导（§11.8）已由各动作落库为 assistant 消息，这里只「读」不再生成。
      const cur = list[list.length - 1]
      const msgs = await api.get<{ messages: BackendMessage[] }>(`/v1/sessions/${cur.id}/messages`)
      set({ sessionId: cur.id, messages: toChatMessages(msgs.data.messages) })
    } catch (err) {
      // 挂载恢复失败：保持空态 + 出声（否则历史静默消失，用户以为对话是空的）
      notifications.show({
        title: '加载聊天历史失败',
        message: userErrorText(err),
        color: 'red',
      })
    }
  },

  /** 停止生成（真停止，2026-08-14）+ 撤回（2026-09-09）：三件套——
   * 1. 调后端 /chat/stop 取消在途 generation（停烧 token 源头，不只是"不收回复"）；
   * 2. 作废在途回复（世代号递增，旧请求返回时被 sendMessage 比对丢弃）；
   * 3. 清 loading。
   *
   * retract=true（用户点停止按钮）：后端顺带撤回最后一条 user 消息，成功则原文回填
   * 输入框（draft）+ 本地删掉那条 user 气泡（即时反馈，不等刷新）。
   * retract=false/缺省（兜底调用 cancelUpload/removeResume/loadChatHistory）：纯停止，
   * 不撤回、不回填。
   *
   * 停止按钮已接在本函数上（ChatPanel onStop）；版本变更边界也调它——「帮你把停止
   * 按钮按下去」。无在途时后端返回 stopped=false（幂等，不误伤）。
   */
  stopGenerating: (retract = false) => {
    // 先作废在途回复 + 清 loading（立即反馈，不等服务端往返）
    chatGeneration += 1
    set({ generating: false })
    // 后端真停止：cancel 正在跑的 agent 工具循环（不 await，不阻塞 UI）
    void api
      .post<ChatStopResponse>('/v1/chat/stop', retract ? { retract: true } : undefined)
      .then(({ data }) => {
        if (retract && data.retracted) {
          // 撤回成功：原文回填输入框 + 本地删掉那条 user 气泡（DB 已真删，这里同步视觉）
          set((s) => ({ draft: data.retracted_text, messages: removeLastUserMessage(s.messages) }))
        }
      })
      .catch(() => {
        // 停止失败：静默——本地已作废在途回复，后端最坏继续跑完这一轮（不吓用户）
      })
  },

  /** 设置聊天输入框内容（draft 提升 store，2026-09-09）。 */
  setDraft: (text) => {
    set({ draft: text })
  },

  /** 暂停开场白生成（2026-08-30）：确认入库后开场白在后台 fire-and-forget 生成，
   * 用户点暂停 → 本地清气泡 + 停短轮询 + 后端 stop_opening 取消在途 LLM（不落库）。 */
  stopOpening: () => {
    set({ openingPending: false })
    stopGuidePoll()
    void api.post('/v1/chat/opening/stop').catch(() => {
      // 停止失败：静默——本地已清气泡，后端最坏继续跑完这一轮开场（不吓用户）
    })
  },

  /** 应用已确认建议改写简历（S7，2026-08-14；ADR 0015 折进统一图，2026-09-09）：
   * 从 SuggestionBasket 抽到 store，统一 `applying` 信号——apply 期间锁建议操作 + 聊天发送 + 回滚等写动作。
   * 2026-09-09 起「开始改」走统一简历图（POST /resume/generate apply_confirmed=true）→ 出预览、
   * 人门确认才落库（不再直接落库）。成功 = 出预览态（previewPending），确认走 confirmGeneration。 */
  applySuggestions: async () => {
    if (get().applying) return
    set({ applying: true })
    try {
      await api.post('/v1/resume/generate', { apply_confirmed: true })
      // 出预览 → 拉预览内容显示（确认保存条）；版本列表/聊天历史在 confirm 时才刷新
      await useResumeStore.getState().refreshPreview()
      emit('resume-data-changed')
      return
    } catch (err) {
      // 应用失败出声（否则用户以为改了，实际没落库）
      notifications.show({
        color: 'red',
        title: '应用建议',
        message: userErrorText(err),
      })
      return
    } finally {
      set({ applying: false })
    }
  },

  /** 拉当前暂存生成预览（agent 生成工具产出后，前端显示预览 + 「确认保存」入口）。
   *
   * 2026-08-12 生成入口收敛到聊天：agent 调 generate_resume 工具 → 服务端暂存预览态
   * （不写库）。前端经 sendMessage 响应 preview_pending 触发本函数拉取。
   * 2026-08-13 双源分层：预览返回 markdown（内容层）+ html（排版层）——previewContent
   * 承载 html，预览直接显示排版层（用户核心诉求：生成的简历像简历纸）。
   * 非空 → previewPending（ResumePage 显示预览 + 确认保存条）；空 → 清态。
   */
  refreshPreview: async () => {
    try {
      const { data } = await api.get<{ markdown: string; html: string }>('/v1/resume/generate/preview')
      const html = data.html ?? ''
      const md = data.markdown ?? ''
      set({ previewPending: html.trim() !== '' || md.trim() !== '', previewContent: html || md })
    } catch {
      // 拉取失败：静默（preview 是增强，不影响主流程；下次 sendMessage 会再试）
      set({ previewPending: false, previewContent: '' })
    }
  },

  /** 加载全部版本列表。 */
  loadVersions: async () => {
    try {
      const { data } = await api.get<{ versions: ResumeVersion[] }>('/v1/documents/versions')
      set({ versions: data.versions, versionsLoaded: true })
    } catch (err) {
      // 加载失败：出声（否则版本列表静默消失，回滚无处下手）
      set({ versionsLoaded: true })
      notifications.show({
        title: '加载版本列表失败',
        message: userErrorText(err),
        color: 'red',
      })
    }
  },

  /** 拉悬浮卡片待执行建议数（回滚前警告「会清空 N 条」用）。 */
  loadPendingCount: async () => {
    try {
      // 2026-08-10：pending 接口返回三栏（待定/已确认/正在聊）；回滚警告用「未定论」总数
      const { data } = await api.get<{ pending: unknown[]; confirmed: unknown[]; discussing: unknown[] }>('/v1/optimization/pending')
      set({ pendingSuggestionCount: data.pending.length + data.discussing.length })
    } catch (err) {
      // 后端暂不可达：出声（否则卡片悄悄清零，用户以为待执行建议没了）
      notifications.show({
        title: '加载待执行建议数失败',
        message: userErrorText(err),
        color: 'red',
      })
    }
  },

  /** 挂载恢复：拉当前文档，若有流则显示并同步抽取状态；原件重建 blob URL（原生预览）。 */
  loadCurrent: async () => {
    try {
      // 2026-09-09 契约修订：无文档是正常空态 → 200 + document 为 null（不再 404），
      // 前端判空早退即可（浏览器不再为这条打 console 红字）。
      const { data } = await api.get<{ document: ResumeVersion | null }>('/v1/documents/current')
      const doc = data.document
      if (!doc) {
        // 空态：清态回零（组件据此显示拖拽上传提示）。
        // 必须清干净（含 resumeName + 抽取态）——否则「removeResume 清空后端后切页再切回来」
        // 会残留旧文件名/旧抽取 phase（reset 后 current 为 null，但 store 还是上次删前的值）。
        set({
          currentVersion: 0,
          currentSource: '',
          resumeName: '',
          previewMarkdown: '',
          previewHtml: '',
          typography: DEFAULT_TYPOGRAPHY,
          originalName: '',
          originalExt: '',
          resumeUrl: null,
          resumeExt: '',
          extractPhase: 'idle',
          extractFacts: [],
          extractError: null,
        })
        await useResumeStore.getState().loadVersions()
        await useResumeStore.getState().refreshPreview()
        return
      }
      // 原件元数据：上传版才有（original_name 非空）；生成/回滚到生成版为空 → Markdown 预览
      const originalName = doc.original_name ?? ''
      const originalExt = doc.original_ext ?? ''
      let resumeUrl: string | null = null
      let resumeExt = ''
      // 原生预览格式（pdf/html/txt/md）需要 blob：pdf/html 原生渲染，txt/md 读 blob 原文；
      // DOCX/PPTX/XLSX/CSV 浏览器渲染不了原件 → 走 Markdown（不用拉原件）
      if (originalName && NATIVE_PREVIEW_EXTS.has(originalExt)) {
        // 拉原件文件 → blob 重建（浏览器的 blob URL 重启即失效，不能持久化）
        try {
          const resp = await api.get('/v1/documents/original', { responseType: 'blob' })
          resumeUrl = URL.createObjectURL(resp.data)
          resumeExt = originalExt
        } catch (err) {
          // 404 = 真没原件（生成版/文件缺失）→ 优雅降级 Markdown，静默；
          // 其他失败（500 等）= 后端 bug → 出声（否则原件静默消失，用户以为丢了）
          if (err instanceof AppApiError && err.status === 404) {
            // 无原件：落 Markdown 预览，不阻断
          } else {
            notifications.show({
              title: '加载简历原件失败',
              message: userErrorText(err),
              color: 'red',
            })
          }
        }
      }
      // 抽取状态从后端恢复（不是一律 confirmed）：切页/挂载后，failed/extracting/extracted
      // 都得还原——否则抽取失败的简历切页回来会丢「重新抽取/重新上传」入口，且已上传但
      // 没抽到任何信息却显示 confirmed（ready）逻辑不对（2026-08-10 修）。
      let restoredPhase: ExtractPhase = 'confirmed'
      let restoredFacts: ExtractCandidate[] = []
      let restoredError: string | null = null
      let restoredGeneration = get().extractGeneration
      try {
        const status = await api.get<ExtractStatusResponse>('/v1/resume/extract-status')
        // 后端状态优先；idle = 无抽取进行中（历史遗留/上传后未开始）→ 视为 ready
        if (status.data.state !== 'idle') {
          restoredPhase = status.data.state
          restoredFacts = status.data.facts
          restoredError = status.data.error
          restoredGeneration = status.data.generation ?? restoredGeneration
        }
      } catch {
        // extract-status 拉取失败：不阻断，保持 confirmed（下次轮询/动作会纠正）
      }
      set({
        currentVersion: doc.version,
        currentSource: doc.source ?? '',
        previewMarkdown: doc.markdown,
        previewHtml: doc.html ?? '',
        typography: doc.typography ?? DEFAULT_TYPOGRAPHY,
        originalName,
        originalExt,
        resumeUrl,
        resumeExt,
        extractPhase: restoredPhase,
        extractFacts: restoredFacts,
        extractError: restoredError,
        extractGeneration: restoredGeneration,
      })
      // 恢复后若仍在抽取中（extracting）→ 继续轮询（后台还在跑，等它结果）
      if (restoredPhase === 'extracting') startPolling()
      await useResumeStore.getState().loadVersions()
      // 挂载恢复：检查是否有待确认的生成预览（重启后 agent 产出的预览还在服务端）
      await useResumeStore.getState().refreshPreview()
    } catch (err) {
      // 真失败（网络断/500 等）：出声——无文档已是 200 空态，能进 catch 的都是真错误
      notifications.show({
        title: '加载当前简历失败',
        message: userErrorText(err),
        color: 'red',
      })
    }
  },

  /** 控件 A 调字号（2026-09-02）：本地即时改 scale（HtmlPreview 注入即时变，预览不等后端），
   *  防抖 500ms 回后端 POST /documents/current/typography 重渲染落库（html 带新排版变量，导出/重开吃这份）。
   *  只挂当前版；上传原件（无 resume_json）后端会 409——前端已隐藏控件，这里失败静默不扰民。 */
  /** 调排版自由度（2026-09-02）：本地即时改 typography（HtmlPreview 注入即时变，预览不等后端），
   *  防抖 500ms 回后端 POST /documents/current/typography 重渲染落库（html 带新排版变量，导出/重开吃这份）。
   *  只挂当前版；上传原件（无 resume_json）后端会 409——前端已置灰控件，这里失败静默不扰民。 */
  setTypography: (typography) => {
    set({ typography }) // 即时：预览注入跟手
    if (typographyTimer) clearTimeout(typographyTimer)
    typographyTimer = setTimeout(() => {
      typographyTimer = null
      void api
        .post('/v1/documents/current/typography', { typography })
        .then(() => {
          // 落库后预览 html 已带新配置：以 store.typography 驱动即可，无需整页刷新（避免闪烁）
        })
        .catch(() => {
          // 上传原件 / 无文档 → 409/404：前端本不该到这（控件已置灰），静默不打扰
        })
    }, 500)
  },

  /** 回滚到某文档（按 document_id 身份锚定，2026-08-12 A3）；includeFacts=True 时连事实快照一起还原。返回是否成功。
   *
   * 2026-08-10 软作废回滚：目标稿之后全标 superseded（数据保留 UI 不显示），目标稿变当前。
   * 版本变更 = 干净轮回：后端已开新 session + 写入「已回滚」事件（§11.3），loadChatHistory 切回新 session。
   */
  rollbackTo: async (documentId, includeFacts = false) => {
    // 主闸门：对话还没收到回复 / 正在应用建议改写时不许跳版本——否则旧 session 的回复
    // 会串到回退后的新版本，或 apply 改写与新版本互相踩踏。先等 agent 说完再回滚
    // （若被别的方式冲破，下方世代号兜底作废在途旧回复）。
    if (get().generating || get().applying) {
      notifications.show({
        title: '正在处理',
        message: get().applying ? '正在应用建议改写简历，等它完成再回滚。' : 'agent 还在回复上一句，等它说完再回滚。',
        color: 'yellow',
      })
      return false
    }
    try {
      await api.post<{ version: number; facts_restored: boolean }>('/v1/documents/rollback', {
        document_id: documentId,
        include_facts: includeFacts,
      })
      // 回滚后当前文档 = 目标稿；刷新版本列表 + 重载当前文档（原件/预览随稿切换）
      await useResumeStore.getState().loadVersions()
      await useResumeStore.getState().loadCurrent()
      // 版本变更 = 干净轮回：切到新 session（后端已开新 session + 写入回滚事件）
      await useResumeStore.getState().loadChatHistory()
      emit('resume-data-changed')
      return true
    } catch (err) {
      set((s) => ({ messages: pushError(s, `回滚失败：${userErrorText(err)}`) }))
      return false
    }
  },

  /** 确认生成预览：confirm 写库 vN（版本变更）→ 开新 session → 清暂存；
   *  revise 带 feedback 重改（人门回边，2026-09-07），出新预览不出版本。 */
  confirmGeneration: async (decision = 'confirm', feedback = '') => {
    if (get().confirming) return null
    set({ confirming: true })
    try {
      const { data } = await api.post<{ version: number | null }>('/v1/resume/generate/confirm', { decision, feedback })
      if (decision === 'revise') {
        // 重新生成：后端跑机器门+LLM 出新预览（覆盖暂存），不写库不出版本 → 刷新显示
        await get().refreshPreview()
        emit('resume-data-changed')
        return null
      }
      await useResumeStore.getState().loadVersions()
      // 版本变更 = 干净轮回：后端已开新 session + 写入「已保存为新版本」事件（§11.3），
      // loadChatHistory 切到新 session 把事件带回来（旧实现注释承诺清消息但没做，已修）
      await useResumeStore.getState().loadChatHistory()
      // 确认后清待确认预览态（后端已清暂存，本地同步）
      set({ previewPending: false, previewContent: '' })
      emit('resume-data-changed')
      return data.version
    } catch (err) {
      set((s) => ({ messages: pushError(s, `确认失败：${userErrorText(err)}`) }))
      return null
    } finally {
      set({ confirming: false })
    }
  },

  /** 停止在途「重新生成」（revise 回边，2026-09-08）：取消后端图重改 + 本地即时反馈。 */
  stopRegenerating: () => {
    set({ confirming: false })
    // 后端真停止：cancel 正在跑的 revise 回边（图重改 + LLM）。不 await、不阻塞 UI。
    void api.post('/v1/resume/generate/stop').catch(() => {
      // 停止失败：静默——本地已置 confirming=false，后端最坏继续跑完这一轮重改
    })
  },

  /** 进入回看模式：拉某稿当时的文档 + 聊天记录（只读，纯查看）。
   *
   * 回看 = 看「该稿当时的对话 + 当时的简历」（§11.1「干净轮回 + 历史归档」），
   * 不改任何后端状态。文档优先原生预览（pdf/html/txt/md 拉该稿原件重建 blob），
   * 无原件落 Markdown。
   *
   * 聊天按**时间线聚合**（2026-08-10 修）：不用 document_version 过滤——那个字段是
   * 「会话开始时的稿号」，种子会话（NULL）/ 回滚后新开会话（绑定回滚后稿号）都不归
   * 目标稿。改为：稿按 created_at 排序定「生效区间」（[该稿, 下一稿)），会话按
   * created_at 落入所在区间的稿，合并该稿区间内所有 session 的消息。
   */
  openReview: async (documentId) => {
    try {
      // 全部稿（含软作废稿，时间线边界需要）——带 created_at
      const { data: allVersions } = await api.get<{ versions: ResumeVersion[] }>('/v1/documents/versions', {
        params: { all: true },
      })
      // 2026-08-12 身份锚定：按 **document_id** 找稿（id 唯一身份；version 是显示标签
      // 软作废后复用——按 version 会撞同号作废稿，find 到错的那条）。
      const v = allVersions.versions.find((x) => x.id === documentId)
      if (!v) {
        notifications.show({ title: '回看失败', message: `该稿不存在`, color: 'red' })
        return
      }
      const version = v.version  // 显示标签（「第 N 稿」）
      // 该稿文档预览：优先原件（原生格式），无则 Markdown。
      // 按 **document_id** 拉原件（id 唯一身份，不撞同号作废稿）。
      let resumeUrl: string | null = null
      let resumeExt = ''
      if (v.original_name && NATIVE_PREVIEW_EXTS.has(v.original_ext)) {
        try {
          const resp = await api.get('/v1/documents/original', { params: { document_id: v.id }, responseType: 'blob' })
          resumeUrl = URL.createObjectURL(resp.data)
          resumeExt = v.original_ext
        } catch {
          // 该稿原件缺失 → 落 Markdown，不阻断回看
        }
      }
      // 时间线聚合：稿按 created_at 排序，找 target 稿的生效区间 [start, nextStart)
      const sorted = [...allVersions.versions].sort((a, b) => a.created_at.localeCompare(b.created_at))
      const idx = sorted.findIndex((x) => x.id === documentId)
      const start = sorted[idx]?.created_at ?? ''
      const nextStart = sorted[idx + 1]?.created_at ?? null
      // 全部 session（带 created_at）→ 落入区间
      const { data: sessionsData } = await api.get<{ sessions: { id: number; created_at: string }[] }>('/v1/sessions')
      const inRange = sessionsData.sessions.filter((s) => {
        const t = s.created_at
        if (!t) return false
        return t >= start && (nextStart === null || t < nextStart)
      })
      // 合并该稿区间内所有 session 的消息（版本分隔标记串联）
      const merged: ChatMessage[] = []
      for (const s of inRange) {
        const msgs = await api.get<{ messages: BackendMessage[] }>(`/v1/sessions/${s.id}/messages`)
        if (msgs.data.messages.length === 0) continue
        if (merged.length > 0) {
          merged.push({ id: nid('d'), kind: 'divider', version })
        }
        merged.push(...toChatMessages(msgs.data.messages))
      }
      set({
        review: {
          id: v.id,
          version,
          source: v.source,
          summary: v.summary ?? '',
          markdown: v.markdown,
          html: v.html ?? '',
          originalName: v.original_name,
          originalExt: v.original_ext,
          resumeUrl,
          resumeExt,
          messages: merged,
        },
      })
    } catch (err) {
      // 回看加载失败：出声（否则回看界面静默空着，用户以为版本没内容）
      notifications.show({ title: '加载回看失败', message: userErrorText(err), color: 'red' })
    }
  },

  /** 退出回看模式：回到当前版本状态（纯 UI，不改后端）。 */
  closeReview: () => {
    const prev = get().review?.resumeUrl
    if (prev) URL.revokeObjectURL(prev)
    set({ review: null })
  },
}))
