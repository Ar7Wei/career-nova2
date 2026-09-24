import { useEffect, useState } from 'react'
import { FolderOpen, X, Plus, Check } from 'lucide-react'
import { Button, TextInput, Select, Skeleton } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import api from '@/lib/api'
import { on } from '@/lib/events'
import { userErrorText } from '@/lib/errors'
import { useT } from '@/lib/i18n'
import { ToolTab } from '@/components/ToolTab'
import { BusyOverlay } from '@/components/BusyOverlay'
import { FactItem, type FactItemData } from '@/components/FactItem'
import { useResumeStore } from '@/stores/resumeStore'

/** 信息库事实（后端 /facts 返回，嵌套 title+points）。 */
interface Fact {
  id: number
  category: 'basic' | 'education' | 'work' | 'projects' | 'skill' | 'other'
  title: string
  points: string[]
  source: 'resume_upload' | 'chat' | 'manual'
  status: 'active' | 'superseded'
  on_resume: boolean
}

/** 分类中文标签（与抽取卡片一致）。 */
const CATEGORY_ORDER: Fact['category'][] = ['basic', 'education', 'work', 'projects', 'skill', 'other']
const CATEGORY_LABELS: Record<Fact['category'], string> = {
  basic: '基本信息',
  education: '教育经历',
  work: '工作经历',
  projects: '项目经历',
  skill: '个人技能',
  other: '其他',
}

/**
 * 信息库编辑入口（docs/design/resume.md §10「信息库入口」补落地）：
 * - 复用 `ToolTab`（标签 pill 点击展开 + 点击外部收起），挂聊天框右上工具坞。
 * - 浮窗 fixed 左铺盖预览区上空（560px，单列），查看/编辑/删除/新增事实。
 * - 事实来源：上传抽取 / 对话挖掘 / 手填；编辑后 PATCH、删除走 DELETE。
 */
export function FactsPanel() {
  const t = useT()
  // S7 统一 busy：资料集是「写事实库」的操作点——busy 期间禁增删改 + 浮窗盖毛玻璃。
  // 2026-09-24：回滚正在整份换资料集，写入必须锁（否则改动会被恢复覆盖掉）。
  const applying = useResumeStore((s) => s.applying)
  const generating = useResumeStore((s) => s.generating)
  const rollingBack = useResumeStore((s) => s.rollingBack)
  const busy = applying || generating || rollingBack
  const busyLabel = rollingBack ? t('resume.rollingBack') : applying ? t('resume.busyApplying') : t('resume.busyAgentWorking')
  const [facts, setFacts] = useState<Fact[]>([])
  const [loading, setLoading] = useState(false)
  const [editingId, setEditingId] = useState<number | null>(null)
  const [adding, setAdding] = useState(false)
  const [newFact, setNewFact] = useState<{ category: Fact['category']; title: string }>({ category: 'basic', title: '' })

  const load = async () => {
    setLoading(true)
    try {
      const { data } = await api.get<{ facts: Fact[] }>('/v1/facts', { params: { status: 'active' } })
      setFacts(data.facts)
    } catch (err) {
      // 加载失败：出声（否则资料集静默空着，用户以为没有事实）
      notifications.show({ color: 'red', title: t('resume.factsPanel'), message: userErrorText(err) })
    } finally {
      setLoading(false)
    }
  }

  // 挂载拉一次 + 订阅跨组件数据变更（确认抽取/发消息后刷新角标，2026-08-12）
  useEffect(() => {
    void load()
    return on('resume-data-changed', () => {
      void load()
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const saveEdit = async (f: Fact, next: FactItemData) => {
    try {
      await api.patch(`/v1/facts/${f.id}`, { title: next.title, points: next.points })
      setEditingId(null)
      void load()
    } catch (err) {
      notifications.show({ color: 'red', title: t('common.save'), message: userErrorText(err) })
    }
  }

  const removeFact = async (f: Fact) => {
    try {
      await api.delete(`/v1/facts/${f.id}`)
      void load()
    } catch (err) {
      notifications.show({ color: 'red', title: t('common.delete'), message: userErrorText(err) })
    }
  }

  const toggleResume = async (f: Fact) => {
    try {
      await api.patch(`/v1/facts/${f.id}`, { on_resume: !f.on_resume })
      void load()
    } catch (err) {
      notifications.show({ color: 'red', title: t('common.save'), message: userErrorText(err) })
    }
  }

  const addFact = async () => {
    if (!newFact.title.trim()) return
    try {
      await api.post('/v1/facts/confirm', {
        facts: [{ category: newFact.category, title: newFact.title.trim(), points: [] }],
      })
      setAdding(false)
      setNewFact({ category: 'basic', title: '' })
      void load()
    } catch (err) {
      notifications.show({ color: 'red', title: t('resume.factsAdd'), message: userErrorText(err) })
    }
  }

  const grouped = CATEGORY_ORDER.map((cat) => ({
    cat,
    entries: facts.filter((f) => f.category === cat),
  })).filter((g) => g.entries.length > 0)

  return (
    <ToolTab
      icon={FolderOpen}
      label={t('resume.factsPanel')}
      badge={{ count: facts.length, color: 'gray' }}
      onOpen={load}
      panelClassName="facts-panel-window"
    >
      <div className="facts-panel-head">
        <span className="facts-panel-title">{t('resume.factsPanel')}</span>
        <span className="facts-panel-count">{facts.length}</span>
      </div>
      <BusyOverlay busy={busy} label={busyLabel} />

      <div className="facts-panel-body">
        {/* 加载态：Mantine Skeleton 骨架行（2026-08-18 批次 4 §9.4，替代干巴巴"加载中…"） */}
        {loading && (
          <div className="facts-skeleton">
            {[0, 1, 2].map((i) => (
              <div key={i} className="facts-skeleton-item">
                <Skeleton height={14} width="55%" radius="sm" />
                <Skeleton height={10} width="85%" radius="sm" mt={8} />
                <Skeleton height={10} width="70%" radius="sm" mt={6} />
              </div>
            ))}
          </div>
        )}
        {!loading && facts.length === 0 && <div className="facts-panel-empty">{t('resume.factsEmpty')}</div>}
        {grouped.map(({ cat, entries }) => (
          <div key={cat} className="facts-group">
            <div className="facts-group-label">{CATEGORY_LABELS[cat]}</div>
            {entries.map((f) => (
              <FactItem
                key={f.id}
                value={{ title: f.title, points: f.points }}
                editing={editingId === f.id}
                onEdit={() => setEditingId(f.id)}
                onCancel={() => setEditingId(null)}
                onSave={(next) => void saveEdit(f, next)}
                onDelete={() => void removeFact(f)}
                onResume={f.on_resume}
                onToggleResume={() => void toggleResume(f)}
                disabled={busy}
              />
            ))}
              </div>
            ))}
          </div>

          {/* 新增事实 */}
          {adding ? (
            <div className="facts-add">
              <Select
                data={CATEGORY_ORDER.map((c) => ({ value: c, label: CATEGORY_LABELS[c] }))}
                value={newFact.category}
                onChange={(v) => v && setNewFact({ ...newFact, category: v as Fact['category'] })}
                size="sm"
                className="facts-add-category"
                // 下拉渲染在面板内（withinPortal=false）：否则点击下拉选项在 body portal，
                // ToolTab 的 useClickOutside 会误判为「点击外部」把面板收回（bug 修复）。
                comboboxProps={{ withinPortal: false }}
              />
              <TextInput
                value={newFact.title}
                onChange={(e) => setNewFact({ ...newFact, title: e.currentTarget.value })}
                placeholder={t('resume.factsNewPlaceholder')}
                size="sm"
                onKeyDown={(e) => {
                  if (e.key === 'Enter') addFact()
                }}
              />
              <Button size="sm" onClick={addFact} disabled={!newFact.title.trim() || busy} leftSection={<Check size={12} />}>
                {t('common.save')}
              </Button>
              <Button variant="default" size="sm" onClick={() => setAdding(false)}>
                <X size={12} />
              </Button>
            </div>
          ) : (
            <div className="facts-panel-foot">
              <Button variant="default" size="sm" onClick={() => setAdding(true)} disabled={busy} leftSection={<Plus size={13} />}>
                {t('resume.factsAdd')}
              </Button>
            </div>
          )}
    </ToolTab>
  )
}
