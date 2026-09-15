import { NavLink } from 'react-router-dom'
import { FileText, Send, TrendingUp, Mic, Settings } from 'lucide-react'
import { useT } from '@/lib/i18n'
import { useSidebarStore } from '@/stores/sidebarStore'

interface NavItem {
  to: string
  key: string
  icon: typeof FileText
}

/** 主导航：按真实操作流程排（简历→投递→Growth→Chatter）。 */
const MAIN_NAV: NavItem[] = [
  { to: '/', key: 'menu.resume', icon: FileText },
  { to: '/apply', key: 'menu.apply', icon: Send },
  { to: '/growth', key: 'menu.growth', icon: TrendingUp },
  { to: '/chatter', key: 'menu.chatter', icon: Mic },
]

/** 设置单独沉底靠下。 */
const SYSTEM_NAV: NavItem[] = [{ to: '/settings', key: 'menu.settings', icon: Settings }]

export function Sidebar() {
  const t = useT()
  // 侧栏折叠态（放 store：折叠要联动 App 主网格列宽收窄；开关在底部状态栏）。
  const collapsed = useSidebarStore((s) => s.collapsed)

  const renderItem = ({ to, key, icon: Icon }: NavItem) => (
    <NavLink
      key={to}
      to={to}
      end={to === '/'}
      className={({ isActive }) => `nav-item${isActive ? ' active' : ''}`}
      title={collapsed ? t(key) : undefined}
    >
      <Icon size={18} className="nav-icon" />
      {!collapsed && <span>{t(key)}</span>}
    </NavLink>
  )

  return (
    <aside className={`sidebar${collapsed ? ' collapsed' : ''}`}>
      <nav className="sidebar-main">{MAIN_NAV.map(renderItem)}</nav>
      <nav className="sidebar-system">{SYSTEM_NAV.map(renderItem)}</nav>
    </aside>
  )
}
