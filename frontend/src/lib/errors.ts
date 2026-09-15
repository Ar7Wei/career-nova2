import { AppApiError } from '@/lib/api'

/**
 * 把未知错误归一成给用户看的文案 + 是否可重试。
 *
 * 统一错误契约（2026-08-08）：
 * - AppApiError：用后端契约的 message（含出路提示）；detail 是真因（聊天内可附上）。
 * - 其他未知错误：给通用文案。
 */
export function userErrorText(err: unknown): string {
  if (err instanceof AppApiError) {
    const base = err.message || '出错了'
    return err.detail && err.detail !== err.message ? `${base}（${err.detail}）` : base
  }
  return '出了点问题，请再试一次。'
}
