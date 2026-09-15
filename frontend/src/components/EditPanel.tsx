import { useState } from 'react'
import { SlidersHorizontal, Wand2 } from 'lucide-react'
import { Slider, NumberInput, Button } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { ToolTab } from './ToolTab'
import { useResumeStore, type Typography } from '@/stores/resumeStore'
import { solveOnePageByMeasure } from '@/lib/fitOnePageMeasure'
import { useT } from '@/lib/i18n'

/**
 * 「编辑项」面板（2026-09-02 grill 定稿）：预览区顶栏的调节入口，点击展开玻璃浮窗（ToolTab 原语，
 * 与优化点/资料集同款）。承载**排版自由度五参数**（数据层，挂版本落库 + 关联导出）：
 *   字号 scale / 行距 lineHeight / 段距 spacing / 字间距 letterSpacing / 栏距 gutter。
 *
 * 每行形态 = Slider 粗调 + 右侧 NumberInput 精调/直输，两者绑同一个值（2026-09-02 grill (a)）。
 * 改动本地即时预览（HtmlPreview 注入排版变量跟手），防抖 500ms 回后端重渲染落库（store.setTypography）。
 * 每行 NumberInput 右侧带单位（倍率=× / 字间距=px），面板底注各项可调范围（2026-09-02 反馈）。
 *
 * 仅生成的简历开放（上传原件不可编辑——父组件 ResumePage 控制显隐/置灰，这里不判断）。
 */

/** 一个排版参数的定义：key、取值范围、步进、小数位、显示单位后缀、默认值。 */
interface ParamSpec {
  key: keyof Typography
  labelKey: 'resume.fontSize' | 'resume.lineHeight' | 'resume.spacing' | 'resume.letterSpacing' | 'resume.gutter'
  min: number
  max: number
  step: number
  /** 小数位（NumberInput 精度 + 显示取整）。 */
  decimals: number
  /** 单位后缀：倍率项 = '倍'，字间距（绝对 px）= 'px'。 */
  unit: string
  /** 默认值（显示在底注，告诉用户「不调时的出厂值」；与后端 Typography 字段默认值一致）。 */
  def: number
}

const PARAMS: ParamSpec[] = [
  { key: 'scale', labelKey: 'resume.fontSize', min: 0.5, max: 1.5, step: 0.01, decimals: 2, unit: '倍', def: 1 },
  // 字间距紧跟字号（2026-09-02 反馈）：两者都管「字」，放一起调节更顺手。
  { key: 'letterSpacing', labelKey: 'resume.letterSpacing', min: -1, max: 3, step: 0.1, decimals: 1, unit: 'px', def: 0 },
  { key: 'lineHeight', labelKey: 'resume.lineHeight', min: 1.0, max: 2.0, step: 0.05, decimals: 2, unit: '倍', def: 1.25 },
  // 段距下限放宽到 0（2026-09-02 反馈「还能再缩更小」）：0 = 段间无额外留白，内容压到最挤。
  { key: 'spacing', labelKey: 'resume.spacing', min: 0, max: 2.0, step: 0.05, decimals: 2, unit: '倍', def: 1 },
  // 栏距（绝对 px，2026-09-02）：左右两栏缝隙，主两栏 flex 化后由 column-gap 单值控制。默认 55 = 原 5+50 padding。
  { key: 'gutter', labelKey: 'resume.gutter', min: 0, max: 120, step: 1, decimals: 0, unit: 'px', def: 55 },
]

const clamp = (v: number, s: ParamSpec) => Math.min(s.max, Math.max(s.min, v))
const roundTo = (v: number, decimals: number) => Number(v.toFixed(decimals))

/** 单条参数的范围文案：「0.5~1.5倍」/「-1~3px」（单位直接拼，GBK 安全——只用 ~倍px，禁用 ↔）。 */
const rangeText = (spec: ParamSpec) => `${spec.min}~${spec.max}${spec.unit}`
/** 默认值文案：「默认 1倍」/「默认 0px」。 */
const defaultText = (spec: ParamSpec) => `默认 ${spec.def}${spec.unit}`

export function EditPanel({ disabled = false }: { disabled?: boolean }) {
  const t = useT()
  const typography = useResumeStore((s) => s.typography)
  const setTypography = useResumeStore((s) => s.setTypography)
  const previewHtml = useResumeStore((s) => s.previewHtml)
  const [fitting, setFitting] = useState(false)

  /** 改单个参数：钳取 + 量化到精度，合并进 typography（其余参数不变）。 */
  const setParam = (spec: ParamSpec, raw: number | string) => {
    const n = typeof raw === 'number' ? raw : Number(raw)
    if (!Number.isFinite(n)) return
    setTypography({ ...typography, [spec.key]: roundTo(clamp(n, spec), spec.decimals) })
  }

  /** 自动一页（2026-09-02 grill 定稿）：隐藏 iframe 多轮量高驱动求解器，把三参数压到/撑到一页。
   *  成功 → setTypography 一次性应用最终参数（本地即时预览 + 防抖落库）；
   *  失败（太多/太少）→ 不落库、不动现状，只出声告知（grill 定稿 B）。 */
  const autoFitOnePage = async () => {
    if (fitting || !previewHtml) return
    setFitting(true)
    try {
      const result = await solveOnePageByMeasure(previewHtml, typography)
      if (result.ok) {
        setTypography(result.typography) // 一次性应用：预览一步到位刷新 + 防抖落库
        notifications.show({ color: 'green', title: t('resume.autoOnePage'), message: t('resume.autoOnePageDone') })
      } else {
        // 失败不落库：现状不动，只说原因 + 建议
        notifications.show({
          color: 'yellow',
          title: t('resume.autoOnePage'),
          message: result.reason === 'too_much' ? t('resume.autoOnePageTooMuch') : t('resume.autoOnePageTooLittle'),
        })
      }
    } catch {
      notifications.show({ color: 'red', title: t('resume.autoOnePage'), message: t('resume.autoOnePageTooMuch') })
    } finally {
      setFitting(false)
    }
  }

  return (
    <ToolTab
      icon={SlidersHorizontal}
      label={t('resume.editPanel')}
      panelClassName="edit-panel-window"
      align="right"
      disabled={disabled}
      disabledReason={disabled ? t('resume.editPanelDisabled') : undefined}
      closeOnIframeClick
    >
      <div className="edit-panel-body">
        {PARAMS.map((spec) => (
          /* 每项一个 grid：行1=标签|滑块|数值+单位，行2=小字落在滑块列（第2列）——
             小字左缘跟着滑块列宽走，天然与滑块对齐，不用 calc 估算列宽（估算会被
             Slider 轨道内边距 / NumberInput 实际宽度偏差带偏，导致文字超出控件）。 */
          <div className="edit-panel-item" key={spec.key}>
            <label className="edit-panel-label">{t(spec.labelKey)}</label>
            <Slider
              className="edit-panel-slider"
              value={typography[spec.key]}
              min={spec.min}
              max={spec.max}
              step={spec.step}
              onChange={(v) => setParam(spec, v)}
              label={null}
              size="sm"
            />
            <div className="edit-panel-value">
              <NumberInput
                className="edit-panel-number"
                value={typography[spec.key]}
                min={spec.min}
                max={spec.max}
                step={spec.step}
                decimalScale={spec.decimals}
                onChange={(v) => setParam(spec, v)}
                size="xs"
                hideControls
                aria-label={t(spec.labelKey)}
              />
              <span className="edit-panel-unit">{spec.unit}</span>
            </div>
            {/* 该项的范围 + 默认值（与 PARAMS 同源），小字落在滑块列，与滑块左缘平齐。 */}
            <div className="edit-panel-meta">
              {rangeText(spec)}
              <em className="edit-panel-def">{defaultText(spec)}</em>
            </div>
          </div>
        ))}
        {/* 自动一页（2026-09-02 grill）：隐藏求解，成功才应用；失败不动现状只提示。 */}
        <Button
          className="edit-panel-autofit"
          variant="light"
          size="sm"
          fullWidth
          leftSection={<Wand2 size={14} />}
          loading={fitting}
          disabled={disabled || !previewHtml}
          onClick={autoFitOnePage}
        >
          {fitting ? t('resume.autoOnePageRunning') : t('resume.autoOnePage')}
        </Button>
      </div>
    </ToolTab>
  )
}
