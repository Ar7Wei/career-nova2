import { Loader2 } from 'lucide-react'

/**
 * 忙碌遮罩（2026-08-14 S7 统一）：busy 时盖住操作点窗口，毛玻璃 + 居中胶囊
 * 显示正在执行的任务。配合外层按钮 disabled——遮罩管「浮窗/面板」这类操作点，
 * 按钮 disabled 管「行内按钮」这类操作点，两者同源（store 的 busy）。
 *
 * 玻璃拟态：backdrop-filter blur + 半透明底，与全局 .glass 三件套同 token。
 */
export function BusyOverlay({ busy, label }: { busy: boolean; label: string }) {
  if (!busy) return null
  return (
    <div className="busy-overlay" role="status" aria-live="polite">
      <div className="busy-overlay-pill">
        <Loader2 size={14} className="spin" />
        <span>{label}</span>
      </div>
    </div>
  )
}
