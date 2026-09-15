import { useEffect, useRef, useState, type ReactNode } from 'react'
import { useClickOutside } from '@mantine/hooks'
import { Button } from '@mantine/core'
import type { LucideIcon } from 'lucide-react'

/** 角标颜色（2026-08-12 优化点优先级级联：红=待定 / 蓝=正在聊 / 绿=已确认）。 */
export type BadgeColor = 'red' | 'blue' | 'green' | 'gray'

/** 右上角角标：数量 + 颜色（优先级信号）。null/undefined = 不显示。 */
export interface ToolBadge {
  count: number
  color: BadgeColor
}

interface ToolTabProps {
  /** 标签图标（lucide）。仅传 ReactNode 标签（如「第N稿」纯文字）时可省略。 */
  icon?: LucideIcon
  /** 标签文案：字符串（配 icon）或任意 ReactNode（纯文字/自定义，此时 icon 省略）。 */
  label: ReactNode
  /** 可选计数徽标（如建议条数 / 事实条数）。 */
  count?: number
  /** 右上角角标（2026-08-12）：数量 + 颜色，替代 count 徽标做优先级信号。 */
  badge?: ToolBadge | null
  /** 浮窗内容。 */
  children: ReactNode
  /** 展开回调（父组件可在此加载数据）。 */
  onOpen?: () => void
  /** 浮窗自定义类（尺寸覆写，如信息库固定宽度）。 */
  panelClassName?: string
  /** 浮窗是否用 fixed 视口定位（默认 true——脱离容器 overflow 裁切，盖内容上方）。 */
  fixed?: boolean
  /** 浮窗水平锚点（2026-08-24）：'right' = 浮窗右缘对齐标签右缘、向左铺（默认，
   *  优化点/资料集挂在聊天框右上，向左铺不溢视口）；'left' = 浮窗左缘对齐标签左缘、
   *  向右铺（检索控制台触发器在行首左侧，向右铺不溢左缘）。 */
  align?: 'left' | 'right'
  /** 触发钮形态（2026-08-24）：true = 真幽灵（subtle 透明无描边，仅「第N稿」用）；
   *  false（默认）= default 白底描边钮（优化点/资料集等工具条按钮保持可见可点感）。 */
  ghost?: boolean
  /** 禁用（2026-09-02 统一「不可用=置灰」）：置灰 + 不响应点击；reason 给悬停提示。 */
  disabled?: boolean
  /** 禁用原因（悬停 title，解释为何不可用）。 */
  disabledReason?: string
  /** 点击预览 iframe 内部也收起（2026-09-02 编辑项反馈）：预览区是 iframe（独立文档），
   *  useClickOutside 只监听主文档 mousedown，点 iframe 里的简历收不到 → 面板不收。
   *  为 true 时，展开期间额外监听左栏 iframe 的 contentDocument mousedown 一并收起。
   *  仅「编辑项」用（它贴着预览区、用户顺手点简历想收面板）；其它面板不贴 iframe，不必开。 */
  closeOnIframeClick?: boolean
}

/**
 * 可复用「标签 pill 展开浮窗」工具（2026-08-08）。
 *
 * 工具坞统一原语（优化点 / 资料集 / 未来更多）：
 * - 触发钮：Mantine 幽灵 Button（subtle，2026-08-24 弃手写 .tool-tab）+ 图标 + 文案 + 可选计数角标。
 * - 点击标签展开浮窗；**点击任何其他位置收起**（useClickOutside，用其返回的 ref）。
 * - 浮窗定位：fixed 相对视口（从标签右下弹出，盖在内容上方）——脱离容器 overflow
 *   裁切，挂聊天框上边条内也不会被 chat-panel 的 overflow:hidden 裁掉。
 *   信息库经 panelClassName 覆写为更宽/别的坐标。
 *
 * onOpen 稳定性：effect 只依赖 open（不把 onOpen/fixed 塞进依赖数组）——
 * onOpen 经 onOpenRef 读取，每次渲染都是最新的，展开瞬间恰好触发一次。
 * 否则若父组件传每次渲染新建的箭头函数（内联/依赖 useT 闭包），effect 会在
 * 每次重渲染重跑 onOpen，埋死循环（SuggestionBasket 事故同源）。
 */
export function ToolTab({ icon: Icon, label, count, badge, children, onOpen, panelClassName, fixed = true, align = 'right', ghost = false, disabled = false, disabledReason, closeOnIframeClick = false }: ToolTabProps) {
  const [open, setOpen] = useState(false)
  const wrapRef = useRef<HTMLDivElement>(null)
  const [pos, setPos] = useState<{ top: number; right?: number; left?: number } | null>(null)
  const clickOutsideRef = useClickOutside(() => setOpen(false))
  // 最新 onOpen 存 ref（latest-ref 模式：在 effect 里同步，不渲染期写 ref）。
  // effect 读 ref 做「展开瞬间触发一次」，onOpen 不参与依赖比较——
  // 父组件每次渲染传新函数引用也不至于让展开 effect 重跑（SuggestionBasket 事故同源）。
  const onOpenRef = useRef(onOpen)
  useEffect(() => {
    onOpenRef.current = onOpen
  })

  // 展开时计算标签的视口坐标（fixed 定位锚点）；fixed=false 时走 CSS 类（absolute）
  const prevOpen = useRef(open)
  useEffect(() => {
    if (open && !prevOpen.current) {
      onOpenRef.current?.()
      if (fixed && wrapRef.current) {
        const r = wrapRef.current.getBoundingClientRect()
        // 浮窗右缘对齐标签右缘（right = 视口宽 - 标签右缘），向左展开不溢视口。
        // 资料集 560px 从右往左铺，正好盖到预览区上空。
        // align='left'：左缘对齐标签左缘、向右铺（检索控制台在行首左侧）。
        setPos(
          align === 'left'
            ? { top: r.bottom + 8, left: r.left }
            : { top: r.bottom + 8, right: window.innerWidth - r.right },
        )
      }
    }
    prevOpen.current = open
    // 依赖留 open + fixed + align（均为布尔/值，稳定）：onOpen 经 ref 读取（渲染期最新）
  }, [open, fixed, align])

  // 点预览 iframe 内部也收起（closeOnIframeClick）：iframe 是独立文档，useClickOutside 监不到它的
  // mousedown。展开期间挂一次 iframe contentDocument 的 mousedown 监听，触发即收起；收起/卸载时摘下。
  useEffect(() => {
    if (!open || !closeOnIframeClick) return
    const doc = document.querySelector<HTMLIFrameElement>('.resume-preview-html iframe')?.contentDocument
    if (!doc) return
    const close = () => setOpen(false)
    doc.addEventListener('mousedown', close)
    return () => doc.removeEventListener('mousedown', close)
  }, [open, closeOnIframeClick])

  return (
    <div ref={wrapRef} className="tool-tab-anchor">
      <div ref={clickOutsideRef} className="tool-tab-wrap">
        {/* 工具条触发钮：默认 default 白底描边钮（优化点/资料集保持可见可点感）；
            ghost=true（仅「第N稿」）→ 真幽灵 subtle 透明无描边。
            open 态：default 钮用 light 浅底显形；ghost 钮保持透明（CSS 盖背景），只把文字变绿。 */}
        <Button
          variant={ghost ? 'subtle' : open ? 'light' : 'default'}
          size="sm"
          color="gray"
          disabled={disabled}
          title={disabled && disabledReason ? disabledReason : undefined}
          onClick={() => setOpen((v) => !v)}
          leftSection={Icon ? <Icon size={13} /> : undefined}
          className={`tool-tab-btn${ghost ? ' tool-tab-btn-ghost' : ''}${open ? ' is-open' : ''}`}
        >
          {label}
          {/* 角标优先（优先级级联）；无 badge 时退回 count 徽标 */}
          {badge && badge.count > 0 ? (
            <span className={`tool-tab-badge badge-${badge.color}`}>{badge.count}</span>
          ) : count != null && count > 0 ? (
            <span className="tool-tab-count">{count}</span>
          ) : null}
        </Button>
        {open && fixed && pos && (
          <div
            className={`tool-panel glass${panelClassName ? ` ${panelClassName}` : ''}`}
            style={{ top: pos.top, right: pos.right, left: pos.left }}
          >
            {children}
          </div>
        )}
        {open && !fixed && (
          <div className={`tool-panel glass${panelClassName ? ` ${panelClassName}` : ''}`}>{children}</div>
        )}
      </div>
    </div>
  )
}
