/**
 * 跨「整页重载」传话：设置页在 `location.reload()` 前把要补弹的提示 + 当前语言写进
 * sessionStorage，App 启动时消费。抽成独立模块是为了避开 App ⇄ SettingsPage 的循环 import。
 *
 * 为什么需要它：核爆「重新开始」成功后必须整页重载（让各 store 回空态），而 reload 会连
 * **同一帧**弹出的 toast 一起抹掉——用户看不到「已清空」。语言同理：重载后 locale 先回默认
 * 'zh'，设置请求飞回前会按 zh 排一帧。
 */

/** 待补弹提示的存键。 */
export const PENDING_TOAST_KEY = 'career-nova:pending-toast'

/** 重载前用户语言的存键。 */
export const PENDING_LOCALE_KEY = 'career-nova:pending-locale'

/** 待弹提示内容：title/message 存**字典 key**（非已经过语言的文案），启动时按当时的语言查。 */
export interface PendingToast {
  color?: string
  titleKey: string
  messageKey: string
}

/** 存下「重载后补弹」的提示 + 当前语言。sessionStorage 不可用时静默跳过。 */
export function armPendingToast(toast: PendingToast, locale: string) {
  try {
    sessionStorage.setItem(PENDING_TOAST_KEY, JSON.stringify(toast))
    sessionStorage.setItem(PENDING_LOCALE_KEY, locale)
  } catch {
    /* 存不下就算了：提示丢了，不影响核爆本身 */
  }
}

/** 取出并清除待弹提示（取过即消费，不会重复弹）。无 / 坏内容返回 null。 */
export function takePendingToast(): PendingToast | null {
  let raw: string | null
  try {
    raw = sessionStorage.getItem(PENDING_TOAST_KEY)
    sessionStorage.removeItem(PENDING_TOAST_KEY)
  } catch {
    return null
  }
  if (!raw) return null
  try {
    return JSON.parse(raw) as PendingToast
  } catch {
    return null // 旧版本/手改留下的坏内容：丢弃，不值得为提示打断启动
  }
}

/** 取重载前的语言；非 'zh'/'en' 或不可用时返回 null（交给设置响应里的 language 定）。 */
export function takePendingLocale(): 'zh' | 'en' | null {
  let raw: string | null
  try {
    raw = sessionStorage.getItem(PENDING_LOCALE_KEY)
  } catch {
    return null
  }
  return raw === 'zh' || raw === 'en' ? raw : null
}
