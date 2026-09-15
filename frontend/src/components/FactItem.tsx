import { useState } from 'react'
import { Check, Eye, EyeOff, Pencil, Plus, Trash2, X } from 'lucide-react'
import { Button, Textarea, Tooltip } from '@mantine/core'
import { useT } from '@/lib/i18n'

/**
 * 事实条目（FactItem）——FactsPanel（确认后）与抽取卡（确认前）共用的双态条目。
 * 2026-08-18 视觉迭代批次 1（docs/frontend-styles.md §11）。
 *
 * 数据结构不动：一条事实 = `{ title, points[] }`（父=title，子=points 数组，
 * 层级在一条记录内）。编辑以**父级为单位**（整条 title+points 一起改），
 * 不做全编辑开关、不做单子要点编辑。
 *
 * 双态（§11.1）：
 * - 显示态（默认）：纯文本（title 一行 + points 列表 + 树干连接线），无输入框/常驻按钮；
 *   编辑/删除做成**浮右上角、压文字上、hover 才浮现**（§11.2）。
 * - 编辑态（点编辑，单条）：title + 每行一个子要点的 Textarea（autosize 多行，
 *   修「一行框难写长文」）；保存仍按换行拆回 points 数组，JSON 结构不变（§11.4）。
 *
 * 添加子要点（§11.5）：树杈中间 hover 浮现 + 插入（插队）+ 列表末尾**幽灵空行**垫底
 * （往里打字即新增、自己清空继续垫底；不输入不采集——保存时 filter(Boolean) 滤空）。
 */

export interface FactItemData {
  title: string
  points: string[]
}

interface FactItemProps {
  value: FactItemData
  /** 是否处于编辑态（受控：由父组件管 editingId）。 */
  editing: boolean
  /** 进入编辑。 */
  onEdit: () => void
  /** 取消编辑（不保存）。 */
  onCancel: () => void
  /** 保存编辑（父级整条：title + points 已滤空）。 */
  onSave: (next: FactItemData) => void
  /** 删除本条。 */
  onDelete: () => void
  /** 外部 busy（写事实库期间禁用操作）。 */
  disabled?: boolean
  /** 该不该上简历（信息库开关，ADR 0011）；默认 true。 */
  onResume?: boolean
  /** 切换上简历开关（信息库页才提供；抽取卡无需）。 */
  onToggleResume?: () => void
}

export function FactItem({ value, editing, onEdit, onCancel, onSave, onDelete, disabled, onResume = true, onToggleResume }: FactItemProps) {
  const t = useT()
  // 编辑态草稿：title + points 数组（每行一个子要点，独立 Textarea）。
  const [draft, setDraft] = useState<FactItemData>(value)
  // 幽灵末行：永远垫底的空输入框（不属于真实 points）；往里打字即新增一条。
  const [ghost, setGhost] = useState('')

  // 进入编辑时同步草稿（显示态的最新值）。
  const startEdit = () => {
    setDraft(value)
    setGhost('')
    onEdit()
  }

  const updatePoint = (i: number, v: string) => {
    setDraft((d) => ({ ...d, points: d.points.map((p, j) => (j === i ? v : p)) }))
  }
  const removePoint = (i: number) => {
    setDraft((d) => ({ ...d, points: d.points.filter((_, j) => j !== i) }))
  }
  /** 在第 i 条之后插入一条空要点（树杈中间 +）。 */
  const insertPoint = (i: number) => {
    setDraft((d) => ({ ...d, points: [...d.points.slice(0, i + 1), '', ...d.points.slice(i + 1)] }))
  }
  /** 幽灵行打字：累积输入（不清空），回车/保存时才提交成一条要点。 */
  const onGhostChange = (v: string) => setGhost(v)

  /** 提交幽灵行：非空则 push 成一条正式要点，幽灵行清空继续垫底（永远有空行）。 */
  const commitGhost = () => {
    const v = ghost.trim()
    if (!v) return
    setDraft((d) => ({ ...d, points: [...d.points, v] }))
    setGhost('')
  }

  const save = () => {
    // 已提交要点 + 幽灵行里还没回车的草稿（一起折叠进 points，不丢）。
    const ghostVal = ghost.trim()
    const points = [...draft.points.map((s) => s.trim()), ...(ghostVal ? [ghostVal] : [])].filter(Boolean)
    onSave({ title: draft.title.trim(), points })
  }

  if (!editing) {
    // 显示态：纯文本 + 连接线；编辑/删除浮右上 hover 浮现。
    return (
      <div className="fact-item">
        <div className="fact-item-head">
          <div className="fact-item-title">{value.title}</div>
          <div className="fact-item-actions">
            {onToggleResume && (
              <Tooltip
                label={onResume ? t('resume.factHideResume') : t('resume.factShowResume')}
                withArrow
              >
                <Button
                  className="btn-icon btn-icon-sm"
                  variant={onResume ? 'default' : 'filled'}
                  color={onResume ? 'gray' : 'green'}
                  size="compact-sm"
                  aria-label={onResume ? t('resume.factHideResume') : t('resume.factShowResume')}
                  onClick={onToggleResume}
                  disabled={disabled}
                >
                  {onResume ? <Eye size={12} /> : <EyeOff size={12} />}
                </Button>
              </Tooltip>
            )}
            <Button className="btn-icon btn-icon-sm" variant="default" size="compact-sm" aria-label={t('common.edit')} onClick={startEdit} disabled={disabled}>
              <Pencil size={12} />
            </Button>
            <Button className="btn-icon btn-icon-sm" variant="default" size="compact-sm" aria-label={t('common.delete')} onClick={onDelete} disabled={disabled}>
              <Trash2 size={12} />
            </Button>
          </div>
        </div>
        {!onResume && (
          <div className="fact-item-off-resume">{t('resume.factOffResume')}</div>
        )}
        {value.points.length > 0 && (
          <ul className="fact-child-list">
            {value.points.map((p, i) => (
              <li key={i} className="fact-child">
                {p}
              </li>
            ))}
          </ul>
        )}
      </div>
    )
  }

  // 编辑态：父 title + 每行子要点（树杈插入 + 幽灵末行）+ 保存/取消。
  return (
    <div className="fact-item is-editing">
      <Textarea
        className="fact-input fact-title-input"
        value={draft.title}
        // 先取 value 再进 updater：React 19 在函数式 setState 异步执行时已把 e.currentTarget 置 null，
        // updater 内读 e.currentTarget.value 会抛 TypeError 白屏（2026-09-09 资料集编辑白屏修复）。
        onChange={(e) => {
          const v = e.currentTarget.value
          setDraft((d) => ({ ...d, title: v }))
        }}
        placeholder={t('resume.extractEntryPlaceholder')}
        autosize
        minRows={1}
        disabled={disabled}
      />
      <div className="fact-child-list is-editing">
        {draft.points.map((p, i) => (
          <div key={i} className="fact-child-edit">
            <Textarea
              className="fact-input"
              value={p}
              onChange={(e) => updatePoint(i, e.currentTarget.value)}
              placeholder={t('resume.extractChildPlaceholder')}
              autosize
              minRows={1}
              disabled={disabled}
            />
            <Button className="btn-icon btn-icon-sm fact-child-del" variant="default" size="compact-sm" aria-label={t('common.delete')} onClick={() => removePoint(i)} disabled={disabled}>
              <Trash2 size={12} />
            </Button>
            {/* 树杈中间插入：本条与下一条之间的连接线上 hover 浮现 + */}
            <Button
              variant="default"
              size="compact-xs"
              className="fact-child-insert"
              aria-label={t('resume.extractInsertChild')}
              title={t('resume.extractInsertChild')}
              onClick={() => insertPoint(i)}
              disabled={disabled}
            >
              <Plus size={12} />
            </Button>
          </div>
        ))}
        {/* 幽灵末行：永远垫底的空输入框，回车（非 IME 组合态）或保存时新增一条 */}
        <div className="fact-child-edit is-ghost">
          <Textarea
            className="fact-input"
            value={ghost}
            onChange={(e) => onGhostChange(e.currentTarget.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
                e.preventDefault()
                commitGhost()
              }
            }}
            placeholder={t('resume.extractGhostChildPlaceholder')}
            autosize
            minRows={1}
            disabled={disabled}
          />
        </div>
      </div>
      <div className="fact-item-edit-actions">
        <Button size="compact-sm" onClick={save} disabled={disabled} leftSection={<Check size={12} />}>
          {t('common.save')}
        </Button>
        <Button variant="default" size="compact-sm" onClick={onCancel} disabled={disabled} leftSection={<X size={12} />}>
          {t('common.cancel')}
        </Button>
      </div>
    </div>
  )
}
