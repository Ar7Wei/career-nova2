import { Construction } from 'lucide-react'
import { useT } from '@/lib/i18n'

/**
 * 未开发模块的页面占位（菜单标签保留可点，内容挡一层）。
 *
 * 视觉上刻意做成「按不动」：整块铺满主区余高、淡灰底、虚线边、无任何可点元素
 * ——跟业务空态（实线白卡 + 有动作）拉开差距，用户扫一眼就知道这块还没做，
 * 而不是误读成「我的数据是空的」。
 *
 * 正中那块橙色的是「施工牌」，**只包住标题这一行**；说明文字排在牌下方的灰底上，
 * 不跟着上橙底——橙底铺满整块会让说明也吃进施工牌的语义里，主次也糊了。
 * 斜纹用**灰半透明**而非黑：牌面文字是深色，黑条会跟文字抢明度、把小字读糊。
 */
export function ComingSoonCard() {
  const t = useT()
  return (
    <div className="coming-soon-card">
      <div className="construction-sign">
        <Construction size={14} />
        {t('common.comingSoon')}
      </div>
      <p className="coming-soon-desc">{t('common.comingSoonDesc')}</p>
    </div>
  )
}
