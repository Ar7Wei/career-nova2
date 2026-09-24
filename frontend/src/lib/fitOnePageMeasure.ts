import type { Typography } from '@/stores/resumeStore'
import { injectScale } from '@/components/HtmlPreview'
import { solveOnePage, type SolveResult } from './fitOnePage'

/**
 * 自动一页的隐藏测量闭环（2026-09-02 grill 定稿「隐藏式求解」）：
 * 在**屏幕外不可见 iframe**里多轮「注入排版 → 量 scrollHeight」，驱动 solveOnePage 逼近到一页。
 *
 * 与可见预览完全解耦——中间逼近态（字号忽大忽小）用户看不见；收敛后由调用方一次性应用最终
 * 参数到可见预览，保证「失败不落库、可见预览零抖动」（grill 定稿 A/B 的物理基础）。
 *
 * 量高要点：
 * - 注入走 injectScale（含 gutter 等全部排版变量），与可见预览/导出渲染**同一份规则**，量出来的
 *   高度才等于真落库后的高度。
 * - iframe 宽度必须等于简历纸宽（210mm ≈ 794px），否则换行数不对、量出假高度。
 * - scrollHeight 不受 html{zoom} 影响（这里也不用 pageZoom），文档坐标即导出坐标。
 * - sandbox="allow-same-origin"：放开同源让外层读 contentDocument；不叠 allow-scripts，脚本仍禁。
 */

/** 简历纸宽（210mm @ 96dpi）。测量 iframe 必须这个宽，量出的换行/高度才与真实一致。 */
const PAPER_WIDTH_PX = 794
/** 单次量高等待重排的帧数：注入后等 2 帧让浏览器完成 layout 再量（防量到旧高度）。 */
const SETTLE_FRAMES = 2

/** 等 N 帧（浏览器完成重排）。 */
function waitFrames(n: number): Promise<void> {
  return new Promise((resolve) => {
    const step = (left: number) => (left <= 0 ? resolve() : requestAnimationFrame(() => step(left - 1)))
    step(n)
  })
}

/** 量一组排版下的内容高：往隐藏 iframe 注入该排版的 HTML，等重排后读 .content 的真实高度。
    关键（2026-09-02 修「缩成一团」bug）：必须量 **.content 元素**的高度，不能量
    documentElement.scrollHeight——隐藏 iframe 有固定 height，documentElement.scrollHeight 只会
    等于视口高（约一屏），不是内容真实高，会让求解器误判"当前正好一页"后疯狂下压。.content 是
    简历纸根元素，它的 getBoundingClientRect().height 才是随排版真实变化的内容高。 */
async function measureInFrame(iframe: HTMLIFrameElement, html: string, typography: Typography): Promise<number> {
  const doc = iframe.contentDocument
  if (!doc) throw new Error('measure iframe not ready')
  doc.open()
  doc.write(injectScale(html, typography, 1))
  doc.close()
  await waitFrames(SETTLE_FRAMES)
  const content = doc.querySelector('.content') ?? doc.documentElement
  return content.getBoundingClientRect().height
}

/**
 * 跑自动一页求解：用隐藏 iframe 量高驱动 solveOnePage。
 * @param html 当前简历的排版层 HTML（落库快照，含完整模板 + CSS）
 * @param start 当前排版（求解起点；gutter/letter_spacing 会被冻结保留进结果）
 * @returns 求解结果：成功带最终 typography；失败带原因（too_much/too_little）
 */
export async function solveOnePageByMeasure(html: string, start: Typography): Promise<SolveResult> {
  // 屏幕外 iframe：挪到视窗外（不用 display:none——不渲染就没法量高度；不用 visibility:hidden 同理）
  const iframe = document.createElement('iframe')
  iframe.setAttribute('sandbox', 'allow-same-origin')
  iframe.setAttribute('aria-hidden', 'true')
  iframe.style.cssText = `position:fixed;left:-10000px;top:0;width:${PAPER_WIDTH_PX}px;height:2000px;border:0;visibility:hidden;`
  document.body.appendChild(iframe)
  try {
    // 等 iframe 初次就位（contentDocument 可用）
    await waitFrames(1)
    return await solveOnePage(start, (t) => measureInFrame(iframe, html, t))
  } finally {
    iframe.remove() // 测量舱一次性用完即拆，不留 DOM
  }
}
