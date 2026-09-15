import { Crosshair, Loader2 } from 'lucide-react'
import { Tooltip } from '@mantine/core'
import { useDirectionStore } from '@/stores/directionStore'
import { useApplyStore, type CrawlGateReason } from '@/stores/applyStore'
import { useSettingsStore } from '@/stores/settingsStore'
import { useT } from '@/lib/i18n'
import { ToolTab } from '@/components/ToolTab'
import { DirectionPanel } from './DirectionPanel'

/**
 * 检索控制台（唯一入口，2026-09-01 整合定稿）：ToolTab 标签 + 浮窗。
 * 标签 = 三态状态点（最左，爬虫信号）+ Crosshair + 一行缩略（关键词组数 · 城市）。
 * 浮窗内容 = DirectionPanel（检索条件 / 岗位获取 / 登录平台 三块）。
 * 原「求职之路」指示器（CrawlIndicator）已并入本标签，其设置弹窗内容迁入浮窗「岗位获取」块。
 */

/** 状态点三态 → CSS 类 + 语义（idle/restricted/running）。 */
function indicatorState(kickRunning: boolean, gateReason: CrawlGateReason): 'running' | 'restricted' | 'idle' {
  if (kickRunning) return 'running'
  if (gateReason === 'throttled' || gateReason === 'exhausted') return 'restricted'
  return 'idle'
}

export function ConsoleTrigger() {
  const t = useT()
  const direction = useDirectionStore((s) => s.direction)
  const applyMode = useSettingsStore((s) => s.apply_mode)
  const kickRunning = useApplyStore((s) => s.kickRunning)
  const gateReason = useApplyStore((s) => s.gateReason)

  const state = indicatorState(kickRunning, gateReason)

  const groupCount = (direction?.keywords ?? []).filter((g) => g.length > 0).length
  const citySummary =
    Array.from(new Set((direction?.cities ?? []).filter(Boolean))).join('、') || t('apply.modeCityAny')
  const summary = [
    t('apply.directionSummaryGroups').replace('{n}', String(groupCount)),
    citySummary,
  ].join(' · ')

  // 状态点文案（tooltip）：running 优先，受限细分，idle 分「未开启/空闲」。
  const statusLabel =
    state === 'running'
      ? t('apply.crawlStatusRunning')
      : state === 'restricted'
        ? gateReason === 'throttled'
          ? t('apply.crawlStatusThrottled')
          : t('apply.crawlStatusExhausted')
        : applyMode
          ? t('apply.crawlStatusIdle')
          : t('apply.crawlStatusOff')

  return (
    <Tooltip label={statusLabel} withArrow position="bottom">
      <ToolTab
        align="left"
        panelClassName="console-panel"
        onOpen={() => {
          // 打开即预取城市库 + 当前方向：下拉数据来自后端 /direction/cities，只在
          // DirectionPanel 挂载时拉既不及时也拿不到——故收敛到 onOpen（面板打开的显式信号）。
          // 打开瞬间置 citiesLoading，配合 QueryGroupsEditor 的挂载兜底拉，加载中下拉置灰，
          // 数据到位后再放开（避免首帧空下拉被误读成「没有 25 城可选」）。
          void useDirectionStore.getState().loadCities()
        }}
        label={
          <span className="console-trigger-label">
            <span className={`crawl-indicator-dot crawl-indicator-dot-${state}`}>
              {state === 'running' ? <Loader2 size={12} className="crawl-indicator-spin" /> : null}
            </span>
            <Crosshair size={13} />
            <span>{summary}</span>
          </span>
        }
      >
        <DirectionPanel />
      </ToolTab>
    </Tooltip>
  )
}
