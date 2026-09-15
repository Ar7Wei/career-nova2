import { useState } from 'react'
import { Check, X, Plus, RefreshCw } from 'lucide-react'
import { Button } from '@mantine/core'
import type { ExtractCandidate } from '@/stores/resumeStore'
import { FactItem, type FactItemData } from '@/components/FactItem'
import { useT } from '@/lib/i18n'

const CATEGORY_ORDER: ExtractCandidate['category'][] = ['basic', 'education', 'work', 'projects', 'skill', 'other']

/** 分类中文标签（可编辑卡片的展示用；后端 label 用 CATEGORY_LABELS）。 */
const CATEGORY_LABELS: Record<ExtractCandidate['category'], string> = {
  basic: '基本信息',
  education: '教育经历',
  work: '工作经历',
  projects: '项目经历',
  skill: '个人技能',
  other: '其他',
}

interface ExtractCardProps {
  facts: ExtractCandidate[]
  onConfirm: (facts: ExtractCandidate[]) => void
  onReject: () => void
  onRetry?: () => void
  /** 外部 busy（store：generating/applying）——busy 时整卡禁用（写事实库的操作点，S7）。 */
  disabled?: boolean
}

/**
 * 抽取信息卡片：总分两段式展示/编辑后台抽取的候选事实，确认才入库。
 * - 2026-08-07 重构：嵌套模型（title + points）+ Mantine Textarea。
 * - 每个总条目 = 一个块（title textarea + 操作钮），其子要点缩进挂在块下（连接线）。
 * - 层级用缩进 + CSS 连接线表达，包含关系一目了然。
 * - 可编辑：改 title/points、删条目/要点、新增条目/要点。
 * - 确认 = 整批提交（后端统一冲突检测后入库）；拒绝 = 该批不入库、通道重开。
 */
export function ExtractCard({ facts, onConfirm, onReject, onRetry, disabled }: ExtractCardProps) {
  const t = useT()
  const [items, setItems] = useState<ExtractCandidate[]>(() => structuredClone(facts))
  const [busy, setBusy] = useState(false)
  const [editingIdx, setEditingIdx] = useState<number | null>(null)
  const locked = busy || disabled

  // 按分类分组（保留 items 真实索引——分组后 filter 会错位，操作须用真实 idx）。
  const grouped = CATEGORY_ORDER.map((cat) => ({
    cat,
    entries: items.map((f, idx) => ({ f, idx })).filter(({ f }) => f.category === cat),
  })).filter((g) => g.entries.length > 0)

  const remove = (idx: number) => {
    setItems((prev) => prev.filter((_, i) => i !== idx))
    setEditingIdx((cur) => (cur === idx ? null : cur))
  }

  const addEntry = (cat: ExtractCandidate['category']) => {
    const next = [...items, { category: cat, title: '', points: [] }]
    setItems(next)
    // 新增条目直接进编辑态（顺手就能写）。
    setEditingIdx(next.length - 1)
  }

  /** 保存某条编辑（父级整条 title+points；FactItem 已滤空 points）。 */
  const saveEntry = (idx: number, next: FactItemData) => {
    setItems((prev) => prev.map((f, i) => (i === idx ? { ...f, ...next } : f)))
    setEditingIdx(null)
  }

  const confirm = () => {
    setBusy(true)
    onConfirm(items.filter((f) => f.title.trim()))
    setBusy(false)
  }

  return (
    <div className="extract-card glass">
      <div className="extract-card-head">
        <span className="extract-card-title">{t('resume.extractCard')}</span>
        <span className="chip chip-green">{t('resume.extractPending')}</span>
      </div>
      <div className="extract-card-body">
        {grouped.length === 0 && <div className="extract-card-empty">{t('resume.extractEmpty')}</div>}
        {grouped.map(({ cat, entries }) => (
          <div key={cat} className="extract-group">
            <div className="extract-group-label">{CATEGORY_LABELS[cat]}</div>
            {entries.map(({ f, idx }) => (
              <FactItem
                key={idx}
                value={{ title: f.title, points: f.points }}
                editing={editingIdx === idx}
                onEdit={() => setEditingIdx(idx)}
                onCancel={() => setEditingIdx(null)}
                onSave={(next) => saveEntry(idx, next)}
                onDelete={() => remove(idx)}
                disabled={locked}
              />
            ))}
            {/* 新增条目：L3 弱操作（原纯文字钮升级为 Mantine subtle，§10.2） */}
            <Button variant="default" size="compact-sm" onClick={() => addEntry(cat)} disabled={locked} leftSection={<Plus size={13} />}>
              {t('resume.extractAddEntry')}
            </Button>
          </div>
        ))}
      </div>
      <div className="extract-card-foot">
        <Button variant="default" size="compact-sm" onClick={onReject} disabled={locked} leftSection={<X size={13} />}>
          {t('resume.extractReject')}
        </Button>
        {onRetry && (
          <Button variant="default" size="compact-sm" onClick={onRetry} disabled={locked} leftSection={<RefreshCw size={13} />}>
            {t('resume.extractRetry')}
          </Button>
        )}
        <Button size="compact-sm" onClick={confirm} disabled={locked} leftSection={<Check size={13} />}>
          {t('resume.extractConfirm')}
        </Button>
      </div>
    </div>
  )
}
