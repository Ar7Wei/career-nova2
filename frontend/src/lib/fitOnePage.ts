import type { Typography } from '@/stores/resumeStore'

/**
 * 自动一页求解器（2026-09-02 grill 定稿）：纯函数，零 DOM/网络，便于测试。
 *
 * 只解 3 个松紧参数（scale/lineHeight/spacing）；gutter/letterSpacing 冻结不动
 * （gutter 是版式结构、letterSpacing 影响宽度不影响高度，都不进求解器）。
 *
 * 算法 = 预估锚点 + 等比例协同小步 + 撞限让位：
 * - 高度近似随 scale 线性，先以 r = 目标/实测 估锚；
 * - 每轮三参数从当前值朝「各自的目标方向」等比例小步逼近（不是机械轮流），
 *   某参数撞限后剩余缺口由其它参数继续补（按实际可调余地让位）；
 * - 收敛到 H ∈ [目标×0.95, 目标] 即成功；三参数都撞限仍不达 → 返回失败原因。
 *
 * 失败不落库（grill 定稿 B）：求解器只产出"建议参数"，落不落库由调用方按成败决定。
 */

/** A4 一页高（px，96dpi，与 HtmlPreview 辅助线同基准）。 */
export const A4_HEIGHT_PX = 1123
/** 目标区间下限比例：H ≥ 目标×0.95 视为"填满"（grill 定稿 0.95，要更满）。 */
export const FIT_MIN_RATIO = 0.95
/** 内容高容差（px）：iframe 量高带亚像素抖动，判定时吸收。 */
const EPS = 2

/** 自动求解范围（⊂ 手动范围，自动比手动保守——grill 定稿）。 */
export interface ParamRange { min: number; max: number }
export const SOLVE_RANGE = {
  scale: { min: 0.5, max: 1.2 }, // 上限 1.2（手动 1.5），放大填充的观感极限
  lineHeight: { min: 1.0, max: 1.5 },
  spacing: { min: 0, max: 1.5 },
} as const

/** 迭代上限（防意外死循环；bracket 二分下正常 20 轮内收敛）。 */
export const MAX_ITER = 40

/** 求解结果。 */
export type SolveResult =
  | { ok: true; typography: Typography }
  | { ok: false; reason: 'too_much' | 'too_little' }

const round2 = (v: number) => Number(v.toFixed(2))
const clamp = (v: number, r: ParamRange) => Math.min(r.max, Math.max(r.min, v))

/**
 * 迭代求解：给一个实测高度的回调，驱动"改参数→量高→再改"直到收敛或撞限。
 * measure(typography) 返回该排版下的内容高（px，Promise——真实量高要等 iframe 重排，
 * 由调用方用隐藏 iframe 提供；测试里给同步值包一层即可）。起始从当前 typography 量起。
 */
export async function solveOnePage(
  start: Typography,
  measure: (t: Typography) => Promise<number> | number,
  targetHeight: number = A4_HEIGHT_PX,
): Promise<SolveResult> {
  const minH = targetHeight * FIT_MIN_RATIO
  const maxH = targetHeight

  // Bracket 二分：hi = 已知"太大"的参数组（H>maxH），lo = 已知"太小"的（H<minH）。
  // 每轮取两 bracket 的中点量测，把解严格夹进区间——三参数同步线性插值 = 协同小步，
  // 中点天然保证收敛不震荡；某参数撞到 range 边界后插值仍在界内 = 撞限让位。
  let hi: Typography | null = null // 太大（需往小压）
  let lo: Typography | null = null // 太小（需往大放）

  /** 三参数在 a、b 之间取中点（gutter/letterSpacing 取 a 的冻结值）。 */
  const mid = (a: Typography, b: Typography): Typography => ({
    ...a,
    scale: round2((a.scale + b.scale) / 2),
    lineHeight: round2((a.lineHeight + b.lineHeight) / 2),
    spacing: round2((a.spacing + b.spacing) / 2),
  })

  // 冷启动：先量起点，确定第一个 bracket 端 + 初始逼近方向
  const h0 = await measure(start)
  if (h0 >= minH - EPS && h0 <= maxH + EPS) return { ok: true, typography: start }
  const dir = h0 > maxH ? -1 : 1
  if (dir < 0) hi = start
  else lo = start
  // 初始大胆一跳：按"高度≈随 scale 线性"估锚，把三参数同步推到 目标/实测 比例处
  const anchor = targetHeight / h0
  let current: Typography = {
    ...start,
    scale: round2(clamp(start.scale * anchor, SOLVE_RANGE.scale)),
    lineHeight: round2(clamp(start.lineHeight * anchor, SOLVE_RANGE.lineHeight)),
    spacing: round2(clamp(start.spacing * anchor, SOLVE_RANGE.spacing)),
  }

  for (let i = 0; i < MAX_ITER; i++) {
    const h = await measure(current)
    if (h >= minH - EPS && h <= maxH + EPS) return { ok: true, typography: current }
    if (h > maxH) hi = current
    else lo = current

    if (hi && lo) {
      // 两端都夹到 → 二分中点
      current = mid(hi, lo)
      continue
    }
    // 只有一端：另一端顶到 range 边界再量
    const edge: Typography = hi
      ? {
          ...start,
          scale: SOLVE_RANGE.scale.min,
          lineHeight: SOLVE_RANGE.lineHeight.min,
          spacing: SOLVE_RANGE.spacing.min,
        }
      : {
          ...start,
          scale: SOLVE_RANGE.scale.max,
          lineHeight: SOLVE_RANGE.lineHeight.max,
          spacing: SOLVE_RANGE.spacing.max,
        }
    const he = await measure(edge)
    if (he >= minH - EPS && he <= maxH + EPS) return { ok: true, typography: edge }
    if (hi) {
      // 已知 hi 太大：边界若仍太大 → too_much；否则边界成 lo
      if (he > maxH) return { ok: false, reason: 'too_much' }
      lo = edge
    } else {
      // 已知 lo 太小：边界若仍太小 → too_little；否则边界成 hi
      if (he < minH) return { ok: false, reason: 'too_little' }
      hi = edge
    }
    current = mid(hi, lo as Typography)
  }
  // 超迭代上限（二分宽度已极窄，正常到不了）：保守判失败，不落库
  const lastH = await measure(current)
  return { ok: false, reason: lastH > maxH ? 'too_much' : 'too_little' }
}
