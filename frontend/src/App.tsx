import { lazy, Suspense, useEffect } from 'react'
import { HashRouter, Routes, Route } from 'react-router-dom'
import { notifications } from '@mantine/notifications'
import { TitleBar } from './components/layout/TitleBar'
import { Sidebar } from './components/layout/Sidebar'
import { StatusBar } from './components/layout/StatusBar'
import { LlmKeyBanner } from './components/layout/LlmKeyBanner'
import { ResumePage } from './pages/ResumePage'
import { ApplyPage } from './pages/ApplyPage'
import { GrowthPage } from './pages/GrowthPage'
import { ChatterPage } from './pages/ChatterPage'
import { SettingsPage } from './pages/SettingsPage'
import { useSettingsStore } from './stores/settingsStore'
import { useSidebarStore } from './stores/sidebarStore'
import { tNow, useI18n } from './lib/i18n'
import { takePendingLocale, takePendingToast } from './lib/pendingToast'

// 组件试衣间是纯开发工具（原语一览 + 接口调用器），**只在 dev 编译进来**：
// 用 import.meta.env.DEV 守卫的动态 import，生产构建下 Rollup 直接摘掉这个分支，
// 整个 pages/dev/ 子树（含 ApiTester 的 axios 直连）不进产物。
const DevComponentsPage = import.meta.env.DEV
  ? lazy(() => import('./pages/dev/DevComponentsPage').then((m) => ({ default: m.DevComponentsPage })))
  : null

/**
 * 启动时消费重载前留下的提示 + 语言。
 *
 * 为什么要跨重载：核爆「重新开始」成功后必须 `location.reload()`（让各 store 回空态），
 * 而 reload 会连**同一帧**弹出的 toast 一起抹掉——用户根本看不到「已清空」。故设置页
 * 把文案（字典 key）和当前语言写进 sessionStorage，这里在应用挂载后按用户语言补弹。
 */
function consumePendingToast() {
  const pending = takePendingToast()
  if (!pending) return
  notifications.show({
    color: pending.color,
    title: tNow(pending.titleKey),
    message: tNow(pending.messageKey),
  })
}

function App() {
  // 启动时载入设置，同步语言到 i18n。重载场景下用重载前存的 locale 先预置，
  // 避免「按默认 zh 排一帧再跳到用户语言」的闪烁。
  const load = useSettingsStore((s) => s.load)
  useEffect(() => {
    const seedLocale = takePendingLocale()
    if (seedLocale) useI18n.getState().seed(seedLocale)
    consumePendingToast()
    void load({ seedLocale: seedLocale ?? undefined })
  }, [load])

  const sidebarCollapsed = useSidebarStore((s) => s.collapsed)

  return (
    <HashRouter>
      <div className="app-shell">
        <TitleBar />
        <div className={`app-body${sidebarCollapsed ? ' sidebar-collapsed' : ''}`}>
          <Sidebar />
          <main className="app-main">
            <LlmKeyBanner />
            <Routes>
              <Route path="/" element={<ResumePage />} />
              <Route path="/apply" element={<ApplyPage />} />
              <Route path="/growth" element={<GrowthPage />} />
              <Route path="/chatter" element={<ChatterPage />} />
              <Route path="/settings" element={<SettingsPage />} />
              {/* 隐藏试衣间：不进菜单，且仅 dev 注册（DevComponentsPage 为 null 时这条根本不挂） */}
              {DevComponentsPage && (
                <Route
                  path="/dev/components"
                  element={
                    <Suspense fallback={null}>
                      <DevComponentsPage />
                    </Suspense>
                  }
                />
              )}
            </Routes>
          </main>
        </div>
        <StatusBar />
      </div>
    </HashRouter>
  )
}

export default App
