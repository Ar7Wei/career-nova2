import { useEffect, useRef } from 'react'

/**
 * 自动滚动 scroll-lock（借鉴 chatbot-ui / lobe-chat 的共识做法）：
 * - 用户上翻 → 锁定，不再被拽回底部；
 * - 区分程序滚动与用户滚动，避免误判；
 * - 仅当用户本就在底部时才跟随新消息吸底。
 */
export function useAutoScroll<T>(items: T[]) {
  const containerRef = useRef<HTMLDivElement>(null)
  const userScrolledUp = useRef(false)
  const isAutoScrolling = useRef(false)

  const scrollToBottom = () => {
    const el = containerRef.current
    if (!el) return
    isAutoScrolling.current = true
    el.scrollTop = el.scrollHeight
    // 程序滚动结束后复位标志，避免被 onScroll 误判为用户滚动
    setTimeout(() => {
      isAutoScrolling.current = false
    }, 100)
  }

  const handleScroll = () => {
    const el = containerRef.current
    if (!el || isAutoScrolling.current) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24
    userScrolledUp.current = !atBottom
  }

  useEffect(() => {
    if (!userScrolledUp.current) {
      scrollToBottom()
    }
  }, [items])

  return { containerRef, handleScroll }
}
