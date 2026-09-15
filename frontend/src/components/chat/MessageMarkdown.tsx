import { memo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

/**
 * markdown 渲染（memo 化）。借鉴 chatbot-ui：
 * 流式时只有正在变的那条消息重渲染 markdown，历史消息靠 children 不变跳过。
 */
export const MessageMarkdown = memo(
  function MessageMarkdown({ content }: { content: string }) {
    return (
      <div className="md-body">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{content}</ReactMarkdown>
      </div>
    )
  },
  (prev, next) => prev.content === next.content,
)
