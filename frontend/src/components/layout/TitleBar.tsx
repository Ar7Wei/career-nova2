import { Button } from '@mantine/core'
import { Minus, Square, X } from 'lucide-react'
import { useT } from '@/lib/i18n'
import logoImg from '@/assets/logo.png'

/**
 * 自绘 TitleBar：左应用名，右窗口控制（最小化/最大化/关闭）。
 * 浏览器开发时窗口控制为占位（Electron 接入后经 preload 桥接真实 win 控制）。
 * 整条可拖动（-webkit-app-region: drag），按钮除外。
 */
export function TitleBar() {
  const t = useT()

  const send = (action: 'minimize' | 'maximize' | 'close') => {
    // Electron preload 暴露 window.desktop?.window(action)；浏览器下为空。
    window.desktop?.window(action)
  }

  return (
    <header className="titlebar">
      <div className="titlebar-drag">
        <img className="titlebar-logo" src={logoImg} alt="" />
        <span className="titlebar-title">{t('app.name')}</span>
      </div>
      <div className="titlebar-controls">
        {/* 窗口控制用 Mantine Button：variant="subtle" 去底色，高度/宽度由 .tb-btn 覆盖
            （Mantine 经内联 style 注入 --button-height，故 CSS 里须用 !important 夺回）。 */}
        <Button variant="subtle" className="tb-btn" aria-label="minimize" onClick={() => send('minimize')}>
          <Minus size={14} />
        </Button>
        <Button variant="subtle" className="tb-btn" aria-label="maximize" onClick={() => send('maximize')}>
          <Square size={12} />
        </Button>
        <Button variant="subtle" className="tb-btn tb-close" aria-label="close" onClick={() => send('close')}>
          <X size={14} />
        </Button>
      </div>
    </header>
  )
}
