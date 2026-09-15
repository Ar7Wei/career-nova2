import { useMemo, useState } from 'react'
import { Button } from '@mantine/core'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import type { Interview } from '@/types/apply'
import { useT } from '@/lib/i18n'

/**
 * 面试日历（自绘月历，参照 beacon activityCalendar，ADR 0006）。
 *
 * - 周一开头的周，7 列网格。
 * - 大格子，格内按上午/下午分区直放场次条目（比 beacon 的「圆点+数量」信息量更大）。
 * - 未来亮标、过去灰底 + 半透明蒙版（可点回看）。
 * - hover 场次条目 → 灰色半透明蒙版，左「详情」右「结果」。
 *
 * 纯展示 + 回调：点击/详情/结果都由父组件（ApplyPage）处理。
 */

/** 按日期分桶：YYYY-MM-DD → 该日场次（按时间升序）。 */
function groupByDay(interviews: Interview[]): Map<string, Interview[]> {
  const map = new Map<string, Interview[]>()
  for (const iv of interviews) {
    const key = dayKey(new Date(iv.scheduledAt))
    if (!map.has(key)) map.set(key, [])
    map.get(key)!.push(iv)
  }
  for (const list of map.values()) list.sort((a, b) => a.scheduledAt.localeCompare(b.scheduledAt))
  return map
}

function dayKey(d: Date): string {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}

function isAM(d: Date): boolean {
  return d.getHours() < 12
}

function fmtTime(iso: string): string {
  const d = new Date(iso)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

interface DayCell {
  key: string
  date: Date
  day: number
  inMonth: boolean
  isToday: boolean
  isPast: boolean
}

interface Props {
  interviews: Interview[]
  /** 点「详情」——打开场次详情弹窗。 */
  onDetail: (iv: Interview) => void
  /** 点「结果」——打开场次结果弹窗。 */
  onResult: (iv: Interview) => void
}

export function InterviewCalendar({ interviews, onDetail, onResult }: Props) {
  const t = useT()
  const [view, setView] = useState<Date>(() => {
    const now = new Date()
    return new Date(now.getFullYear(), now.getMonth(), 1)
  })

  const byDay = useMemo(() => groupByDay(interviews), [interviews])

  const cells = useMemo<DayCell[]>(() => {
    const year = view.getFullYear()
    const month = view.getMonth()
    const first = new Date(year, month, 1)
    // 周一开头：getDay() 0=周日 → 往前推 (getDay()+6)%7 天到周一
    const monday = new Date(first)
    monday.setDate(first.getDate() - ((first.getDay() + 6) % 7))
    const today = new Date()
    const out: DayCell[] = []
    const cur = new Date(monday)
    for (let i = 0; i < 42; i++) {
      const d = new Date(cur)
      const key = dayKey(d)
      out.push({
        key,
        date: d,
        day: d.getDate(),
        inMonth: d.getMonth() === month,
        isToday: key === dayKey(today),
        isPast: key < dayKey(today),
      })
      cur.setDate(cur.getDate() + 1)
    }
    return out
  }, [view])

  const monthTitle = `${view.getFullYear()}年${view.getMonth() + 1}月`
  const todayKey = dayKey(new Date())

  const goPrev = () => setView(new Date(view.getFullYear(), view.getMonth() - 1, 1))
  const goNext = () => setView(new Date(view.getFullYear(), view.getMonth() + 1, 1))
  const goToday = () => {
    const now = new Date()
    setView(new Date(now.getFullYear(), now.getMonth(), 1))
  }

  return (
    <div className="interview-calendar">
      <div className="ivcal-head">
        <div className="ivcal-monthnav">
          <Button variant="default" size="compact-sm" className="ivcal-nav" onClick={goPrev} aria-label={t('apply.calPrevMonth')}>
            <ChevronLeft size={16} />
          </Button>
          <div className="ivcal-title">{monthTitle}</div>
          <Button variant="default" size="compact-sm" className="ivcal-nav" onClick={goNext} aria-label={t('apply.calNextMonth')}>
            <ChevronRight size={16} />
          </Button>
        </div>
        <Button variant="default" size="compact-sm" className="ivcal-today" onClick={goToday}>
          {t('apply.calToday')}
        </Button>
      </div>

      <div className="ivcal-sheet">
        <div className="ivcal-weekdays">
          {['一', '二', '三', '四', '五', '六', '日'].map((w) => (
            <span key={w} className="ivcal-weekday">
              {w}
            </span>
          ))}
        </div>

        <div className="ivcal-grid">
          {cells.map((c, i) => {
            const dayIvs = byDay.get(c.key) ?? []
            const am = dayIvs.filter((iv) => isAM(new Date(iv.scheduledAt)))
            const pm = dayIvs.filter((iv) => !isAM(new Date(iv.scheduledAt)))
            const isToday = c.key === todayKey
            // 格线单元：每行第 7 格去右线、最后一行去下线
            const noR = i % 7 === 6
            const noB = Math.floor(i / 7) === 5
            return (
              <div
                key={c.key}
                className={`ivcal-day${c.inMonth ? '' : ' is-other'}${isToday ? ' is-today' : ''}${c.isPast ? ' is-past' : ''}${noR ? ' no-r' : ''}${noB ? ' no-b' : ''}`}
              >
                <div className="ivcal-day-num">{c.day}</div>
                {dayIvs.length === 0 ? null : (
                  <div className="ivcal-day-items">
                    <DayGroup label={t('apply.calMorning')} list={am} onDetail={onDetail} onResult={onResult} />
                    <DayGroup label={t('apply.calAfternoon')} list={pm} onDetail={onDetail} onResult={onResult} />
                  </div>
                )}
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}

function DayGroup({
  label,
  list,
  onDetail,
  onResult,
}: {
  label: string
  list: Interview[]
  onDetail: (iv: Interview) => void
  onResult: (iv: Interview) => void
}) {
  if (list.length === 0) return null
  return (
    <div className="ivcal-day-group">
      <span className="ivcal-day-group-label">{label}</span>
      {list.map((iv) => (
        <InterviewChip key={iv.id} iv={iv} onDetail={onDetail} onResult={onResult} />
      ))}
    </div>
  )
}

/** 格内一条面试缩略信息：hover 蒙灰色半透明蒙版，左「详情」右「结果」。 */
function InterviewChip({ iv, onDetail, onResult }: { iv: Interview; onDetail: (iv: Interview) => void; onResult: (iv: Interview) => void }) {
  const t = useT()
  return (
    <div className="ivcal-chip">
      <span className="ivcal-chip-time">{fmtTime(iv.scheduledAt)}</span>
      <span className="ivcal-chip-round">{iv.round || '—'}</span>
      <div className="ivcal-chip-overlay">
        <Button variant="default" size="compact-xs" className="ivcal-chip-btn" onClick={() => onDetail(iv)}>
          {t('apply.ivDetail')}
        </Button>
        <Button variant="default" size="compact-xs" className="ivcal-chip-btn" onClick={() => onResult(iv)}>
          {t('apply.ivResult')}
        </Button>
      </div>
    </div>
  )
}
