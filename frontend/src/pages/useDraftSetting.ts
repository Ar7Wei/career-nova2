import { useEffect, useState } from 'react'
import { notifications } from '@mantine/notifications'

/** 校验结果：ok 则携带解析后的值；失败带 message（red toast 展示）。 */
export type DraftValidation<T> = { ok: true; value: T } | { ok: false; message: string }

export interface DraftSettingOptions<T> {
  /** 已提交的真相值（store）。草稿跟随它同步，失败时也回滚到它。 */
  value: T
  /** 校验 + 解析原始输入；失败返回 message（red toast）。 */
  validate: (raw: string) => DraftValidation<T>
  /** 持久化；抛错则回滚草稿 + 失败 toast。 */
  persist: (v: T) => Promise<void>
  /** toast 标题（设置项名）。 */
  title: string
  /** 保存成功文案（含「重启后生效」或「已保存」语义）。 */
  savedMessage: string
  /** 保存失败文案。 */
  failedMessage: string
  /** 保存成功后的副作用（如 base_url 保存成功 → 作废探测结果）。 */
  afterSave?: (v: T) => void
}

/**
 * 设置项「草稿 + 失焦提交」统一封装（2026-08-24）：
 * - 草稿跟随 store 真相值同步（value 变化 → 回填草稿）；
 * - commit（onBlur/回车）时：空 diff → no-op（不写、不弹）；非法 → red toast + 回滚；
 *   持久化抛错 → red toast + 回滚；成功 → green toast。
 * - commitRaw：提交一个显式值（「浏览」选目录即存用，原生对话框抢焦点、blur 不触发）。
 * - 回滚 = 草稿重置为当前 store 值：输入框绝不挂着「没存上」的值。
 */
export function useDraftSetting<T>(opts: DraftSettingOptions<T>) {
  const { value, validate, persist, title, savedMessage, failedMessage, afterSave } = opts
  const [draft, setDraft] = useState(String(value))

  useEffect(() => {
    setDraft(String(value))
  }, [value])

  const commitRaw = async (rawInput: string) => {
    const raw = rawInput.trim()
    // 空 diff：不写、不弹，只把多余空白清回已提交值
    if (raw === String(value)) {
      setDraft(String(value))
      return
    }
    const parsed = validate(raw)
    if (!parsed.ok) {
      notifications.show({ color: 'red', title, message: parsed.message })
      setDraft(String(value)) // 回滚：清掉非法输入
      return
    }
    try {
      await persist(parsed.value)
      notifications.show({ color: 'green', title, message: savedMessage })
      setDraft(String(parsed.value))
      afterSave?.(parsed.value)
    } catch {
      notifications.show({ color: 'red', title, message: failedMessage })
      setDraft(String(value)) // 回滚：后端打回 / 网络断，恢复 store 值
    }
  }

  const commit = () => commitRaw(draft)

  return { draft, setDraft, commit, commitRaw }
}
