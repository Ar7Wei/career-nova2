import { Send, Square } from 'lucide-react'
import { Button, Textarea } from '@mantine/core'
import type { ChatMessage } from '@/types/resume'
import type { WorkingKind } from '@/stores/resumeStore'
import { MessageMarkdown } from './MessageMarkdown'
import { useAutoScroll } from './useAutoScroll'
import { useT } from '@/lib/i18n'

interface ChatPanelProps {
  messages: ChatMessage[]
  /** 当前「正在做什么」（统一工作信号，见 resumeStore.selectWorkingKind）；null = 空闲。 */
  workingKind: WorkingKind | null
  /** 当前工作是否可暂停（小飞机变暂停按钮）。 */
  workingStoppable: boolean
  onSend: (text: string) => void
  onStop: () => void
  /** 应用建议改写中（S7，2026-08-14）：禁发送——apply 窗口不许新信息溜进来。 */
  sendDisabled?: boolean
  /** 输入框内容（受控，提升到 store，2026-09-09 停止撤回回填通道）。 */
  draft: string
  onDraftChange: (text: string) => void
  /** 顶部工具条内容（资料集 / 优化点等工具坞，2026-08-08）。 */
  header?: React.ReactNode
}

/** 各 working kind → i18n key（气泡文案）。解析/抽取/应用建议复用现有 key，开场白新增。 */
const WORKING_TEXT_KEY: Record<WorkingKind, string> = {
  parsing: 'resume.workingParsing',
  extracting: 'resume.workingExtracting',
  opening: 'resume.workingOpening',
  replying: 'resume.generatingReply',
  applying: 'resume.busyApplying',
}

/** 简历页右栏聊天框：消息列表 + 输入区。结构借鉴 chatbot-ui / lobe-chat。 */
export function ChatPanel({
  messages,
  workingKind,
  workingStoppable,
  onSend,
  onStop,
  sendDisabled,
  draft,
  onDraftChange,
  header,
}: ChatPanelProps) {
  const t = useT()
  const { containerRef, handleScroll } = useAutoScroll(messages)

  // 单条消息上限与后端 Message.content 一致（app/schemas/chat.py，20000）。
  // 前端先拦住：超长禁发 + 就地提示，不再让请求撞 422。
  const MAX_LEN = 20000
  const tooLong = draft.length > MAX_LEN

  const submit = () => {
    const text = draft.trim()
    if (!text || tooLong || workingStoppable || sendDisabled) return
    onSend(text)
    onDraftChange('')
  }

  return (
    <div className="chat-panel">
      {header && <div className="chat-panel-head">{header}</div>}
      <div className="chat-messages" ref={containerRef} onScroll={handleScroll}>
        {messages.map((m) =>
          m.kind === 'divider' ? (
            <Divider key={m.id} version={m.version} />
          ) : m.kind === 'event' ? (
            <EventBubble key={m.id} content={m.content} />
          ) : m.kind === 'error' ? (
            <ErrorBubble key={m.id} content={m.content} />
          ) : (
            <TextMessage key={m.id} role={m.role} content={m.content} />
          ),
        )}
        {workingKind && (
          <div className="chat-row assistant">
            <div className="chat-bubble">
              <span className="chat-typing" />
              <span className="chat-typing-text">{t(WORKING_TEXT_KEY[workingKind])}</span>
            </div>
          </div>
        )}
      </div>

      <div className="chat-input-area">
        {tooLong && (
          <div className="chat-toolong-hint" role="alert">
            {t('resume.chatTooLong').replace('{max}', String(MAX_LEN)).replace('{count}', String(draft.length))}
          </div>
        )}
        <Textarea
          className="chat-input"
          classNames={{ input: 'chat-input-textarea' }}
          placeholder={t('resume.chatPlaceholder')}
          value={draft}
          onChange={(e) => onDraftChange(e.currentTarget.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              submit()
            }
          }}
          autosize
          minRows={1}
          maxRows={6}
        />
        {workingStoppable ? (
          <Button className="btn-icon btn-icon-stop" variant="default" size="sm" aria-label={t('common.cancel')} onClick={onStop}>
            <Square size={14} />
          </Button>
        ) : (
          <Button className="btn-icon btn-icon-send" variant="default" size="sm" aria-label={t('dev.send')} onClick={submit} disabled={!draft.trim() || tooLong || sendDisabled}>
            <Send size={14} />
          </Button>
        )}
      </div>
    </div>
  )
}

/** 普通文本气泡：用户靠右（主色底），助手靠左（面板底）。 */
function TextMessage({ role, content }: { role: 'user' | 'assistant'; content: string }) {
  return (
    <div className={`chat-row ${role}`}>
      <div className="chat-bubble">
        {role === 'assistant' ? <MessageMarkdown content={content} /> : <div className="chat-text">{content}</div>}
      </div>
    </div>
  )
}

/** 版本分隔标记：版本变更处提示"新一轮对话从这里开始"（§11.1 决策一，段落感）。 */
function Divider({ version }: { version: number | null }) {
  const t = useT()
  const label = version != null ? t('resume.versionDivider').replace('{v}', String(version)) : t('resume.versionDividerEmpty')
  return (
    <div className="chat-divider">
      <span className="chat-divider-line" />
      <span className="chat-divider-label">{label}</span>
      <span className="chat-divider-line" />
    </div>
  )
}

/** 系统气泡·事件灰条（上传/确认/回滚/建议提示等，role=event）：居中灰条，区分用户/助手气泡（§11.3）。 */
function EventBubble({ content }: { content: string }) {
  return (
    <div className="chat-row event">
      <span className="chat-event-bubble">{content}</span>
    </div>
  )
}

/** 系统气泡·错误红条（上传失败/抽取失败/连不上后端等，kind=error_*）：居中红条，与事件灰条同族但红色区分（ADR 0017）。 */
function ErrorBubble({ content }: { content: string }) {
  return (
    <div className="chat-row event">
      <span className="chat-event-bubble chat-event-error">{content}</span>
    </div>
  )
}
