import { useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button } from '@mantine/core'
import { PanelLeftClose, PanelLeftOpen } from 'lucide-react'
import { useHealthStore } from '@/stores/healthStore'
import { useSettingsStore } from '@/stores/settingsStore'
import { useSidebarStore } from '@/stores/sidebarStore'
import { useT } from '@/lib/i18n'

/** 底部固定状态条：左「折叠开关 + 后端连接状态」；右「LLM 模型（点击跳转设置页模型区）」。 */
export function StatusBar() {
  const t = useT()
  const navigate = useNavigate()
  const { status, startPolling, stopPolling } = useHealthStore()
  // 模型名读设置 store（App.tsx 启动时已 load）：与设置面板选的真实模型一致，不再写死
  const model = useSettingsStore((s) => s.llm_model)
  const collapsed = useSidebarStore((s) => s.collapsed)
  const toggle = useSidebarStore((s) => s.toggle)

  // 启动时探一次活：应用刚开还没发业务请求，得先落地一个真实状态（通则 online 停轮询，
  // 不通则 offline 继续探活直到连上）。之后状态由业务流量驱动，无常设轮询。
  useEffect(() => {
    startPolling()
    return () => stopPolling()
  }, [startPolling, stopPolling])

  const dotClass = status === 'online' ? 'green' : status === 'offline' ? 'gray' : 'gray'
  const label =
    status === 'online'
      ? t('status.backendConnected')
      : status === 'offline'
        ? t('status.backendDisconnected')
        : t('status.checking')

  const ToggleIcon = collapsed ? PanelLeftOpen : PanelLeftClose

  // 点模型 → 跳设置页并滚到「模型」分组（group-model）。
  // HashRouter 下不能用锚点 hash（会当路由路径），改用 navigate state 传递目标 id。
  const goToModelSettings = () => {
    navigate('/settings', { state: { scrollTo: 'group-model' } })
  }

  return (
    <footer className={`statusbar${collapsed ? ' sidebar-collapsed' : ''}`}>
      {/* 左列只放收起开关：与侧栏同宽（200px / 收起 56px），hover 整列高亮，右缘分隔线对齐侧栏轮廓 */}
      <div className="statusbar-left">
        <Button
          variant="subtle"
          size="compact-sm"
          className="statusbar-toggle"
          onClick={toggle}
          title={t(collapsed ? 'menu.expand' : 'menu.collapse')}
          aria-label={t(collapsed ? 'menu.expand' : 'menu.collapse')}
          leftSection={<ToggleIcon size={13} />}
        >
          <span className="statusbar-toggle-label">{t(collapsed ? 'menu.expand' : 'menu.collapse')}</span>
        </Button>
      </div>
      {/* 右列：连接状态（左）+ LLM 模型（贴右端） */}
      <div className="statusbar-right">
        <span className={`status-dot ${dotClass}`} />
        <span className="statusbar-text">{label}</span>
        <Button
          variant="subtle"
          size="compact-sm"
          className="statusbar-model"
          onClick={goToModelSettings}
          title={t('settings.llmModel')}
        >
          {model || t('status.modelUnconfigured')}
        </Button>
      </div>
    </footer>
  )
}
