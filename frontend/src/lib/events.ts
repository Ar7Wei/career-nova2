/**
 * 轻量跨组件事件总线（2026-08-12，胶囊角标事件驱动刷新）。
 *
 * 场景：资料集 / 优化点胶囊的计数在「关键操作后」要刷新，但操作发生在别的组件
 * （resumeStore 确认抽取 / 发送消息 / 确认生成）。不引状态管理库——用最小 pub/sub：
 * - emit：某数据变更时广播（resumeStore 调）。
 * - on：订阅刷新（FactsPanel / SuggestionBasket 调）。
 *
 * 事件：
 * - 'resume-data-changed'：简历相关数据变更（抽取确认 / 消息发送 / 版本变更）——
 *   资料集与优化点计数都该刷新。
 */

type EventName = 'resume-data-changed'

const listeners = new Map<EventName, Set<() => void>>()

export function on(event: EventName, fn: () => void): () => void {
  if (!listeners.has(event)) listeners.set(event, new Set())
  listeners.get(event)!.add(fn)
  return () => {
    listeners.get(event)?.delete(fn)
  }
}

export function emit(event: EventName): void {
  listeners.get(event)?.forEach((fn) => fn())
}
