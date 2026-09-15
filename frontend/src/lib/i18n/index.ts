import { create } from 'zustand'
import { zh, type Messages } from './zh'
import { en } from './en'

export type Locale = 'zh' | 'en'

const DICTS: Record<Locale, Messages> = { zh, en }

/**
 * 语言单一事实源：UI 文案与 LLM 输出语言都由它驱动。
 * 当前仅存于前端内存（骨架阶段）；下一阶段接后端 settings 持久化，
 * 并由后端 Agent 读它注入「用中文/英文回答」。
 */
interface I18nState {
  locale: Locale
  /** 是否被用户手动 setLocale 过：seed 的闸门（见 seed 注释）。 */
  localeUserSet: boolean
  setLocale: (locale: Locale) => void
  /**
   * 从后端已存的语种预置 locale：仅在**用户尚未手动切换**时生效。
   * 治整页重载的痛——重载后 locale 先回默认 'zh'，设置请求飞回前会按 zh 排一帧；
   * 调用方可在发请求**之前**用已知语种预置，避免语言闪一下再跳（如核爆成功后 reload）。
   * 用户手动 setLocale 过后本闸门不再放行（不覆盖用户当次选择）。
   */
  seed: (locale: Locale) => void
}

export const useI18n = create<I18nState>((set, get) => ({
  locale: 'zh',
  localeUserSet: false,
  seed: (locale) => {
    if (get().localeUserSet) return
    set({ locale })
  },
  setLocale: (locale) => set({ locale, localeUserSet: true }),
}))

/** 按点路径从词典取值，如 'menu.resume'。缺 key 时返回路径本身便于排查。 */
function lookup(dict: Messages, path: string): string {
  const node: unknown = path.split('.').reduce<unknown>((acc, key) => {
    if (acc && typeof acc === 'object' && key in acc) {
      return (acc as Record<string, unknown>)[key]
    }
    return undefined
  }, dict)
  return typeof node === 'string' ? node : path
}

/** 取当前语言的文案。t('menu.resume')。 */
export function useT() {
  const locale = useI18n((s) => s.locale)
  return (path: string): string => lookup(DICTS[locale], path)
}

/** 非 hook 版本：在 zustand store / 模块函数里按当前语言取词（S9-2 欢迎语 i18n）。 */
export function tNow(path: string): string {
  return lookup(DICTS[useI18n.getState().locale], path)
}
