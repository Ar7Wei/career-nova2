import { useEffect, useState } from 'react'
import { DEFAULT_TYPOGRAPHY, type Typography } from '@/stores/resumeStore'

/**
 * HTML 简历预览：iframe + blob URL 原生渲染 HTML（保留样式/布局，浏览器引擎还原）。
 *
 * 两种输入，同一渲染机制：
 * - `url`：blob URL（上传原件，URL.createObjectURL 已生成）——原生渲染上传的 HTML 简历。
 * - `html`：**原始 HTML 字符串**（2026-08-28 结构化转向：固定模板从 resume JSON 渲染的快照）。
 *   组件内部 new Blob([html], {type:'text/html'}) → createObjectURL → iframe（与上传原件同一机制，
 *   复用 HtmlPreview 的隔离舱：CSS 只作用于舱内不污染应用外壳）。
 *
 * - 用 blob URL 而非 srcdoc——避免大 HTML 内联转义，且 blob URL 与上传路径一致，零新增机制。
 *
 * 两个独立的缩放概念（2026-09-02 grill 拆分，勿再合并成一个「zoom」）：
 * - `zoom`（控件 A 字号阶梯，排版参数/数据层）：注入 `:root{--scale:Z}` 等，只乘所有
 *   `calc(Npx * var(--scale))` 的字号——只改字号、不改 A4 纸宽，内容重排（页数变）。
 *   后端落库的 html 已带版本自己的排版变量；这里是预览时用户拖参数的即时覆盖（防抖后才回后端落库）。
 * - `pageZoom`（控件 B 整页缩放，预览镜头/视图层）：注入 `html{zoom:Z}`——Chromium 的 zoom
 *   真实重排布局（非 transform:scale），把整个 A4 纸等比缩进预览窗看全貌。只改镜头、不重排内容、与导出无关。
 *
 * A4 分页辅助线（2026-09-02 grill 定稿，**三次修：画进 iframe 文档，不再用外层 overlay**）：
 * - 简历纸宽 210mm = 794px（96dpi），一页 A4 高 = 297mm ≈ 1123px（文档坐标，恒定）。从纸顶每隔
 *   1123px 画一条页界，和导出 PDF 的分页一致。
 * - **画在 iframe 文档里**（往预览 blob 的 DOM 插几个 div），不画在外层 overlay——外层框高度只有一屏、
 *   内容在 iframe 内部滚动，外层 overlay 追不上 iframe 的内部滚动，线会停在可视区外看不见（前两次失败的根因）。
 *   画进同一文档后，线和内容天然一起滚动、一起被 zoom 等比缩放，不用再算坐标、不用乘 pageZoom。
 * - **绝不碰落库 HTML**：辅助线 div 只插进本地预览 blob 的 DOM（内存里的副本），后端存的 html 原封不动，
 *   导出走 printToPDF 用的是落库 html → 辅助线不进产物。
 * - iframe `sandbox="allow-same-origin"`：放开同源让外层往舱内 DOM 插线；**脚本仍禁**（不叠 allow-scripts，
 *   安全舱只是从异源密封降成同源无脚本，恶意 HTML 跑不了 JS）。
 * - 每有内容的一页都画出该页页界：H > (k-1)×A4高+ε 就画第 k 条（页数 = 线数，1 页内容也画出第 1 页底边）。
 * - 仅生成的 HTML 简历开放（html 路径 + showPageGuides）；上传原件（url 路径）不画——原件不可编辑、
 *   没分页语义（PDF 原件走 PdfPreview 本就到不了这）。
 */

/** A4 纸高（210mm 对应宽 794px 的同一 96dpi 基准）：297mm ≈ 1123px（文档坐标，恒定）。 */
const A4_HEIGHT_PX = 1123

/** 剥掉 HTML 里的 <script> 标签（成对 + 自闭合），返回净文档。
    预览舱 sandbox="allow-same-origin" 本就禁脚本（allow-scripts 不叠），但原件内嵌 <script>
    会触发浏览器 "Blocked script execution" console 噪音。反正预览是纯静态、不需要任何脚本，
    进舱前直接剥掉：渲染结果不变（脚本本就跑不了），console 也干净。对生成稿/上传原件同用。 */
function stripScripts(html: string): string {
  return html
    .replace(/<script\b[\s\S]*?<\/script\s*>/gi, '') // 成对 <script>...</script>
    .replace(/<script\b[^>]*\/?\s*>/gi, '') // 自闭合/裸 <script ...>
}
/** 页界判定容差（px）：iframe 量出的 H 带亚像素抖动，内容刚好顶到页界时防线闪进闪出。 */
const EPS = 2
/** 最多画几条页界线（防脏数据/异常高度画出几百条）。 */
const MAX_GUIDES = 20
/** 辅助线容器的 data 标记：重画时按它清掉旧线再插新线。 */
const GUIDE_ATTR = 'data-page-guides'

interface HtmlPreviewProps {
  /** blob URL（上传原件路径）。 */
  url?: string
  /** 原始 HTML 文档字符串（渲染快照路径）。 */
  html?: string
  /** 控件 A 排版自由度配置（默认 Typography()）。仅 html 路径生效；缺省默认不注入。 */
  typography?: Typography
  /** 控件 B 整页缩放倍率（1 = A4 物理大小）。仅 html 路径生效；缺省 1 不注入。 */
  pageZoom?: number
  /** 是否画 A4 分页辅助线（仅生成的 HTML 简历开；上传原件/兜底不传）。 */
  showPageGuides?: boolean
}

/** 往 HTML 字符串 </head> 前注入缩放规则；无 </head> 则前置。
    导出供自动一页的隐藏测量 iframe 复用（量高要带完整排版含 gutter，与真实渲染一致）。 */
export function injectScale(html: string, typography: Typography, pageZoom: number): string {
  // 控件 A 排版自由度（数据层）：注入 :root 排版变量覆盖。默认值不注（模板自带，省字符）。
  // 解构出的字段名与后端 Typography 一致（蛇形，无转换层）；右侧 CSS 变量名
  // （--scale/--lh/--spacing/--ls/--gutter）是模板约定，另起一名，别把两边混为一谈。
  let rules = ''
  const { scale, line_height, spacing, letter_spacing, gutter } = typography
  if (scale !== 1) rules += `--scale:${scale};`
  if (line_height !== 1.25) rules += `--lh:${line_height};`
  if (spacing !== 1) rules += `--spacing:${spacing};`
  if (letter_spacing !== 0) rules += `--ls:${letter_spacing}px;`
  if (gutter !== 55) rules += `--gutter:${gutter}px;`
  if (rules) rules = `:root{${rules}}`
  if (pageZoom !== 1) rules += `html{zoom:${pageZoom}}` // 控件 B 整页缩放（真实重排布局）
  if (!rules) return html
  const tag = `<style id="resume-scale">${rules}</style>`
  const i = html.toLowerCase().indexOf('</head>')
  if (i === -1) return tag + html
  return html.slice(0, i) + tag + html.slice(i)
}

/** 按内容总高（文档坐标）算出要画的页界线文档 Y 数组。
    判定 = 「第 k 页有内容就画第 k 页的页界」：H > (k-1)×A4高+ε 即内容已进入第 k 页 → 画出第 k 条（k×A4 处）。
    故页数 = 线数（1 页内容也画出第 1 页底边线），非「超过页界才画」——后者第一页底边线永远不出现（用户反馈）。 */
export function pageGuideOffsets(contentHeightDoc: number): number[] {
  const offsets: number[] = []
  for (let k = 1; k <= MAX_GUIDES; k++) {
    if (contentHeightDoc > (k - 1) * A4_HEIGHT_PX + EPS) offsets.push(k * A4_HEIGHT_PX)
    else break
  }
  return offsets
}

/** 往 iframe 文档里画 A4 页界线（文档坐标，绝对定位在 body 顶）。
    线与内容同一文档：滚动、zoom 天然一致。先清旧线（重画幂等）。 */
function drawGuides(doc: Document) {
  doc.querySelectorAll(`[${GUIDE_ATTR}]`).forEach((el) => el.remove())
  const h = doc.documentElement.scrollHeight // 内容总高（文档坐标，scrollHeight 不受 html{zoom} 影响）
  const offsets = pageGuideOffsets(h)
  if (!offsets.length) return
  // 装线的容器：盖在纸面（z-index 压内容）、不挡点击/选择。position:absolute 相对 body（body 需 relative）。
  doc.body.style.position = 'relative'
  const box = doc.createElement('div')
  box.setAttribute(GUIDE_ATTR, '')
  box.style.cssText = 'position:absolute;top:0;left:0;right:0;height:0;pointer-events:none;z-index:9999;'
  offsets.forEach((docY, i) => {
    const line = doc.createElement('div')
    line.style.cssText = `position:absolute;left:0;right:0;top:${docY}px;border-top:1.5px dashed rgba(34,197,94,0.65);`
    const tag = doc.createElement('span')
    tag.textContent = `P${i + 2}` // 翻过这条进第 i+2 页
    tag.style.cssText =
      'position:absolute;right:8px;top:0;transform:translateY(-50%);padding:0 6px;font-size:11px;line-height:1.5;color:#fff;background:rgba(34,197,94,0.92);border-radius:4px;'
    line.appendChild(tag)
    box.appendChild(line)
  })
  doc.body.appendChild(box)
}

export function HtmlPreview({ url, html, typography = DEFAULT_TYPOGRAPHY, pageZoom = 1, showPageGuides = false }: HtmlPreviewProps) {
  const [src, setSrc] = useState<string | null>(url ?? null)
  const [loaded, setLoaded] = useState(false)
  const [iframeEl, setIframeEl] = useState<HTMLIFrameElement | null>(null)

  useEffect(() => {
    if (html) {
      // 原始 HTML 字符串（剥 <script> + 注入排版变量 + 整页缩放）→ blob（text/html）→ 进 sandbox iframe
      const blob = new Blob([injectScale(stripScripts(html), typography, pageZoom)], { type: 'text/html' })
      const u = URL.createObjectURL(blob)
      setSrc(u)
      setLoaded(false)
      return () => URL.revokeObjectURL(u)
    }
    if (url) {
      // 上传原件 blob：fetch 回文本 → 剥 <script> → 重建净 blob（原件可能内嵌脚本，拦了也吵，直接剥）。
      // 失败（blob 失效等）→ 回退原 url，预览照样出（脚本仍被 sandbox 拦，只是 console 留噪音）。
      let revoked: string | null = null
      let cancelled = false
      fetch(url)
        .then((r) => r.text())
        .then((text) => {
          if (cancelled) return
          const u = URL.createObjectURL(new Blob([stripScripts(text)], { type: 'text/html' }))
          revoked = u
          setSrc(u)
          setLoaded(false)
        })
        .catch(() => {
          if (!cancelled) setSrc(url)
        })
      return () => {
        cancelled = true
        if (revoked) URL.revokeObjectURL(revoked)
      }
    }
    setSrc(null)
  }, [url, html, typography, pageZoom])

  // 加载完成 / 排版·整页缩放变化后画辅助线（排版改了内容重排、页界位置变）。内容可能随字体晚到再长：
  // 同帧 + 一帧后再各画一次，收敛到稳定页界。lines 画在 iframe 文档里，随内容滚动、随 zoom 缩放。
  useEffect(() => {
    if (!loaded || !showPageGuides || !iframeEl) return
    const doc = iframeEl.contentDocument
    if (!doc) return
    drawGuides(doc)
    const raf = requestAnimationFrame(() => drawGuides(doc))
    return () => cancelAnimationFrame(raf)
  }, [loaded, typography, pageZoom, showPageGuides, iframeEl])

  return (
    <div className="resume-preview-html">
      {!loaded && <div className="resume-preview-loading">正在渲染 HTML…</div>}
      {src && (
        <iframe
          ref={setIframeEl}
          src={src}
          // allow-same-origin：放开同源让外层往舱内 DOM 画辅助线；不叠 allow-scripts——脚本仍禁，安全舱不拆。
          sandbox="allow-same-origin"
          title="HTML 简历预览"
          onLoad={() => setLoaded(true)}
        />
      )}
    </div>
  )
}
