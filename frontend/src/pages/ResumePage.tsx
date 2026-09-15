import { useEffect, useRef, useState } from 'react'
import { Upload, X, Check, RefreshCw, Square } from 'lucide-react'
import { Button, Textarea } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { ChatPanel } from '@/components/chat/ChatPanel'
import { ExtractCard } from '@/components/chat/ExtractCard'
import { SuggestionBasket } from '@/components/chat/SuggestionBasket'
import { EditPanel } from '@/components/EditPanel'
import { ExportMenu } from '@/components/ExportMenu'
import { FactsPanel } from '@/components/FactsPanel'
import { PanelErrorBoundary } from '@/components/PanelErrorBoundary'
import { ResumePreview } from '@/components/ResumePreview'
import { VersionReview } from '@/components/VersionReview'
import { VersionTab } from '@/components/VersionTab'
import { ZoomControl } from '@/components/ZoomControl'
import { useResumeStore, selectWorkingKind, selectWorkingStoppable } from '@/stores/resumeStore'
import { useT } from '@/lib/i18n'

const ACCEPT = '.txt,.md,.pdf,.docx,.html,.csv,.pptx,.xlsx'

/* 左右栏分割（2026-08-24 甲+拖）：比例存 localStorage，默认简历 55 : 聊天 45。
   左=简历预览(保真 A4,多余空间让出)，右=聊天(吃弹性)。两侧各设最小宽度(px)。 */
const SPLIT_STORAGE_KEY = 'resume.splitRatio'
const SPLIT_DEFAULT = 55 // 左栏占比 %
const SPLIT_MIN_LEFT_PX = 360 // 简历预览最小宽
const SPLIT_MIN_RIGHT_PX = 340 // 聊天框最小宽

/** 读持久化的左栏占比（%），非法/越界回退默认。 */
function loadSplitRatio(): number {
  try {
    const raw = localStorage.getItem(SPLIT_STORAGE_KEY)
    if (!raw) return SPLIT_DEFAULT
    const n = Number(raw)
    if (!Number.isFinite(n) || n <= 0 || n >= 100) return SPLIT_DEFAULT
    return n
  } catch {
    return SPLIT_DEFAULT
  }
}

/* 控件 B 整页缩放（2026-09-02，纯预览镜头/视图层）：本次启动内存态、不持久化（重开回自动适应宽度）。
   默认 0 = 「自动适应宽度」哨兵（每次打开预览/拖分割条后重算 预览窗宽÷210mm 铺满）；用户手动调后为具体百分比。 */
const A4_WIDTH_PX = 794 // A4 纸 210mm @96dpi ≈ 794px（与 electron/services/export.js 的 PAGE_WIDTH_PX 一致）

// 左栏预览分档：PDF/HTML/TXT/MD 原生渲染原件（blob URL，重启后由后端原件重建），
// DOCX/PPTX/XLSX/CSV 走后端 Markdown 兜底（previewMarkdown 已存库，重启后同样可用）。
const NATIVE_TEXT_EXTS = new Set(['txt', 'md'])

/** 从 blob URL 读文本内容（TXT/MD 原文预览用）。当次上传是 File 的 blob，重启后是后端原件的 blob。
 * 返回 '' 表示无 blob 或非文本格式（此时父组件走 Markdown 兜底）。 */
function useBlobText(url: string | null, ext: string): string {
  const [text, setText] = useState('')
  useEffect(() => {
    if (!url || !NATIVE_TEXT_EXTS.has(ext)) {
      setText('')
      return
    }
    let cancelled = false
    fetch(url)
      .then((r) => r.text())
      .then((t) => {
        if (!cancelled) setText(t)
      })
      .catch(() => {
        if (!cancelled) setText('')
      })
    return () => {
      cancelled = true
    }
  }, [url, ext])
  return text
}

/** 简历页：左简历预览 + 右聊天框（工具类布局：左是加工对象，右是驱动助手）。 */
export function ResumePage() {
  const t = useT()
  const store = useResumeStore()
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [dragOver, setDragOver] = useState(false)
  const nativeText = useBlobText(store.resumeUrl, store.resumeExt)
  // S7 统一 busy（2026-08-14）：busy = generating || applying。左栏写动作按钮
  // （重置/移除/修复/确认生成/回到初始版本）+ 抽取卡在 busy 时禁用。
  const busy = store.generating || store.applying

  // 左右分割（甲+拖）：leftPct = 左栏占比 %，拖分割条改；grid 模板由内联 style 驱动。
  const layoutRef = useRef<HTMLDivElement>(null)
  const [leftPct, setLeftPct] = useState<number>(loadSplitRatio)
  // 拖动中标记：拖拽时给左栏 iframe 加 pointer-events:none——
  // 否则鼠标一进 iframe（独立文档）就截获 mousemove/mouseup，window 收不到、拖动中断
  // （iframe 铺满预览框后必现：小窗时代鼠标多半不压在 iframe 上，铺满后往左拖必进）。
  const [splitDragging, setSplitDragging] = useState(false)

  // 控件 B 整页缩放（2026-09-02，纯预览镜头）：仅"生成的 HTML 简历"开放（上传原件隐藏，两控件都不给）。
  // pageZoomPct = 0 表「自动适应宽度」（默认）：跟随预览窗宽自动铺满；用户手动调后为具体百分比（本次启动内存）。
  // zoomable = 当前是否有可缩放的生成 HTML 预览。控件 A 字号阶梯挪进「编辑项」面板（EditPanel），与此独立。
  const [pageZoomPct, setPageZoomPct] = useState<number>(0)
  const [autoFit, setAutoFit] = useState(true) // true=自动适应宽度（跟随预览窗宽）；false=用户已手调
  const previewBoxRef = useRef<HTMLDivElement>(null)
  const zoomable = Boolean(store.previewHtml) || Boolean(store.previewPending && store.previewContent)
  // 有简历文档（生成稿或上传原件）才显示排版/缩放控件；空态（没简历）不显示。
  // 生成稿 zoomable=true 可用；上传原件 zoomable=false 置灰（统一「不可用=置灰」2026-09-02 grill）。
  const hasDocument = Boolean(store.previewHtml) || Boolean(store.resumeUrl) || Boolean(store.previewMarkdown)

  /** 自动适应宽度：量预览窗宽 ÷ A4 物理宽 × 100 = 铺满整页缩放 %。 */
  const computeFit = (): number => {
    const w = previewBoxRef.current?.clientWidth
    if (!w) return 100
    return Math.min(150, Math.max(30, Math.round((w / A4_WIDTH_PX) * 100)))
  }

  // 自动档时跟随预览窗宽重算（出现可缩放预览 / 拖分割条 / 窗口缩放都经 ResizeObserver 触发）。
  useEffect(() => {
    if (!zoomable) return
    const el = previewBoxRef.current
    if (!el) return
    const apply = () => {
      if (autoFit) setPageZoomPct(computeFit())
    }
    apply()
    const ro = new ResizeObserver(apply)
    ro.observe(el)
    return () => ro.disconnect()
  }, [zoomable, autoFit])

  /** 手动微调（控件 B 的 −/%/＋）：脱离自动档，记住用户值（本次启动）。 */
  const setPageZoom = (pct: number) => {
    setAutoFit(false)
    setPageZoomPct(pct)
  }
  /** 一键「适应宽度」：回到自动档。 */
  const fitWidth = () => {
    setAutoFit(true)
    setPageZoomPct(computeFit())
  }
  /** 生效的整页缩放倍率。传给 HtmlPreview 注入 html{zoom}。 */
  const effectivePageZoom = pageZoomPct || 100

  /** 拖分割条：以布局容器为参照，把鼠标 x 换算成左栏占比 %，夹取两侧最小宽。 */
  const startSplitDrag = (e: React.MouseEvent) => {
    e.preventDefault()
    const container = layoutRef.current
    if (!container) return
    const rect = container.getBoundingClientRect()
    setSplitDragging(true)
    const onMove = (ev: MouseEvent) => {
      const usable = rect.width // grid 三列，分割条 1px 忽略（占比近似即可）
      let pct = ((ev.clientX - rect.left) / usable) * 100
      // 夹取：左侧不小于 MIN_LEFT、右侧不小于 MIN_RIGHT
      const minLeftPct = (SPLIT_MIN_LEFT_PX / usable) * 100
      const maxLeftPct = 100 - (SPLIT_MIN_RIGHT_PX / usable) * 100
      pct = Math.min(maxLeftPct, Math.max(minLeftPct, pct))
      setLeftPct(pct)
    }
    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
      setSplitDragging(false)
      // 持久化（拖定才写，拖动中不写，减抖动）
      setLeftPct((cur) => {
        try {
          localStorage.setItem(SPLIT_STORAGE_KEY, String(Math.round(cur)))
        } catch {
          /* localStorage 不可用则忽略（纯 UI 手感，丢了不心疼） */
        }
        return cur
      })
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }

  // 进入页面：挂载恢复当前文档（重启后左栏不丢）+ 加载版本列表 + 恢复聊天历史
  useEffect(() => {
    store.loadCurrent()
    store.loadChatHistory(false) // 挂载恢复不按停：切页前发起的生成还在后台跑，切回来不该误杀
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 抽取状态机：蒙版/进度条/卡片/通道开关
  const { extractPhase } = store
  const extracting = extractPhase === 'parsing' || extractPhase === 'extracting'
  const showExtractCard = extractPhase === 'extracted'
  const channelOpen = extractPhase === 'idle' || extractPhase === 'parsing' || extractPhase === 'extracting' || extractPhase === 'extracted' || extractPhase === 'failed'

  const triggerUpload = () => {
    if (!channelOpen) return
    fileInputRef.current?.click()
  }
  const handleFile = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (file && channelOpen) store.uploadResume(file)
  }

  // 拖拽上传（通道关闭时拦截）
  const onDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    if (!channelOpen) return
    const file = e.dataTransfer.files?.[0]
    if (file) store.uploadResume(file)
  }

  /** 确认生成预览写库（2026-08-12：生成入口收敛到聊天，agent 产出暂存预览，这里只确认保存）。 */
  const handleConfirmGenerate = async () => {
    const version = await store.confirmGeneration()
    if (version) {
      notifications.show({
        color: 'green',
        title: t('resume.generateConfirm'),
        message: t('resume.notify.generationConfirmed').replace('{v}', `第${version}稿`),
      })
    }
  }

  /** 重新生成（2026-09-07 统一「出简历」人门 revise 回边，2026-09-08 改「重新生成 + 可选意见」）：
   *  把意见（可空）发给后端重改出新预览（不出版本）。意见框可选——空 = 整份重来，填了 = 定向重改。
   *  确认保存走 handleConfirmGenerate。 */
  const [reviseFeedback, setReviseFeedback] = useState('')
  const handleRegenerate = async () => {
    await store.confirmGeneration('revise', reviseFeedback.trim())
    setReviseFeedback('')
  }

  return store.review ? (
    <VersionReview />
  ) : (
    <div
      className="resume-layout"
      ref={layoutRef}
      style={{ gridTemplateColumns: `minmax(0, ${leftPct}fr) 1px minmax(0, ${100 - leftPct}fr)` }}
    >
      <input ref={fileInputRef} type="file" accept={ACCEPT} style={{ display: 'none' }} onChange={handleFile} />

      {/* 左栏：当前简历预览（支持拖拽上传）+ 版本控制 */}
      <section
        className={`resume-left${dragOver ? ' drag-over' : ''}${splitDragging ? ' split-dragging' : ''}`}
        onDragOver={(e) => {
          e.preventDefault()
          setDragOver(true)
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={onDrop}
      >
        <div className="resume-left-head">
          <h1 className="page-title">{t('page.resume.title')}</h1>
          <div className="resume-left-head-actions">
            {/* 「编辑项」面板（2026-09-02）：控件 A 字号阶梯在此（排版参数，挂版本 + 关联导出）。
                仅生成的简历开放（zoomable = 有生成 HTML 预览）；上传原件隐藏（原件不编辑）。 */}
            {/* 「编辑项」面板（2026-09-02）：排版自由度四参数（挂版本落库 + 关联导出）。
                统一「不可用=置灰」（2026-09-02 grill）：有文档但原件时渲染但禁用，悬停给原因；空态不显示。 */}
            {hasDocument && (
              <PanelErrorBoundary name={t('resume.editPanel')}>
                <EditPanel disabled={!zoomable} />
              </PanelErrorBoundary>
            )}
            {store.currentVersion > 0 && (
              /* 版本入口（2026-08-24）：「第N稿」幽灵触发钮 + 玻璃悬浮面板（ToolTab 原语，
                 与优化点/资料集同款），不再占文档流的内联折叠面板。 */
              <VersionTab />
            )}
            {/* 下载简历（2026-08-13）：PDF/Markdown/HTML；Electron 才可用。
                2026-08-24：只有生成/回滚稿开放下载——上传的最初稿和空态禁用（用户手里本就有原件）。 */}
            <ExportMenu />
            {store.resumeName && channelOpen && (
              <Button className="btn-icon" variant="default" size="sm" aria-label={t('resume.removeResume')} onClick={store.removeResume} disabled={busy}>
                <X size={14} />
              </Button>
            )}
          </div>
        </div>

        <div className="resume-preview" ref={previewBoxRef}>
          {/* 旧文档预览：previewPending（有新预览待确认）时隐藏，只渲染新预览（2026-09-08 修上下屏——
              旧实现两个 ResumePreview 同时渲染、纵向堆叠成上下两屏，本意是「覆盖」不是「对比」）。 */}
          {!store.previewPending && (store.resumeUrl || store.previewMarkdown || store.previewHtml) ? (
            <ResumePreview
              url={store.resumeUrl}
              ext={store.resumeUrl ? store.resumeExt : ''}
              markdown={store.previewMarkdown}
              text={nativeText}
              html={store.previewHtml}
              typography={store.typography}
              pageZoom={effectivePageZoom / 100}
              showPageGuides={zoomable}
            />
          ) : !store.previewPending ? (
            <div className="empty-state resume-empty">
              {/* <Upload size={30} style={{ color: 'var(--color-placeholder)' }} />
              <h3>{t('resume.emptyTitle')}</h3> */}
              <p>{t('resume.dropHint')}</p>
              <div className="resume-empty-actions">
                <Button onClick={triggerUpload} disabled={!channelOpen} leftSection={<Upload size={13} />}>
                  {t('resume.upload')}
                </Button>
              </div>
              <p>{t('resume.emptyChatHint')}</p>
            </div>
          ) : null}

          {/* 生成预览态（2026-08-12 生成入口收敛到聊天）：agent 工具产出暂存预览，
              previewPending 时用预览内容覆盖当前文档显示 + 悬浮「确认保存 / 重新生成」条。
              previewContent 承载排版层 HTML（双源分层）→ iframe 渲染，像简历纸。
              确认保存 → 写库新稿（版本变更）；重新生成 → 带可选意见重改出新预览（人门 revise 回边）。 */}
          {store.previewPending && store.previewContent ? (
            <>
              <ResumePreview url={null} ext="" markdown="" text="" html={store.previewContent} typography={store.typography} pageZoom={effectivePageZoom / 100} showPageGuides />
              <div className="generate-actions">
                <Textarea
                  value={reviseFeedback}
                  onChange={(e) => setReviseFeedback(e.currentTarget.value)}
                  placeholder={t('resume.generateRegeneratePlaceholder')}
                  autosize
                  minRows={1}
                  maxRows={4}
                  disabled={store.confirming}
                  className="generate-revise-input"
                />
                <Button onClick={handleConfirmGenerate} leftSection={<Check size={14} />} disabled={store.confirming} loading={store.confirming}>
                  {t('resume.generateConfirm')}
                </Button>
                {store.confirming ? (
                  <Button variant="default" onClick={store.stopRegenerating} leftSection={<Square size={14} />}>
                    {t('resume.generateStop')}
                  </Button>
                ) : (
                  <Button variant="default" onClick={handleRegenerate} leftSection={<RefreshCw size={14} />}>
                    {t('resume.generateRegenerate')}
                  </Button>
                )}
              </div>
            </>
          ) : null}

          {/* 解析进度（不遮挡简历内容）：右下角悬浮卡片（玻璃拟态），抽取完成直接转信息确认卡 */}
          {extracting && (
            <div className="resume-extract-float glass">
              <div className="resume-extract-progress">
                <div className="resume-extract-bar" />
              </div>
              <div className="resume-extract-text">{t('resume.extracting')}</div>
              <div className="resume-extract-hint">{t('resume.extractingHint')}</div>
            </div>
          )}
          {/* 失败态：右下角悬浮提示 + 重抽/重传 */}
          {extractPhase === 'failed' && (
            <div className="resume-extract-float glass">
              <div className="resume-extract-error">{store.extractError ?? t('resume.extractFailed')}</div>
              <div className="resume-extract-float-actions">
                <Button size="compact-sm" onClick={triggerUpload} disabled={!channelOpen} leftSection={<RefreshCw size={13} />}>
                  {t('resume.retry')}
                </Button>
                {store.currentVersion > 0 && (
                  <Button variant="default" size="compact-sm" onClick={store.retryExtract} leftSection={<RefreshCw size={13} />}>
                    {t('resume.extractRetry')}
                  </Button>
                )}
              </div>
            </div>
          )}
          {/* 抽取信息卡片（候选，可编辑，确认才入库） */}
          {showExtractCard && (
            <ExtractCard
              facts={store.extractFacts}
              onConfirm={store.confirmExtract}
              onReject={store.rejectExtract}
              onRetry={store.retryExtract}
              disabled={busy}
            />
          )}

          {/* 控件 B 整页缩放（仅生成的 HTML 简历开放）：左下角悬浮玻璃，适应宽度 / − / 输入% / ＋。
              上传原件隐藏（两控件都不给）；控件 A 字号阶梯在顶栏「编辑项」面板里，与此独立。 */}
          {/* 控件 B 整页缩放：左下角悬浮玻璃，适应宽度 / − / 输入% / ＋。
              统一「不可用=置灰」（2026-09-02 grill）：有文档但原件时置灰（仍渲染）；空态不显示。 */}
          {hasDocument && (
            <div className="resume-zoom-anchor">
              <ZoomControl value={effectivePageZoom} onChange={setPageZoom} onFitWidth={fitWidth} disabled={!zoomable} disabledReason={t('resume.zoomDisabled')} />
            </div>
          )}
        </div>
      </section>

      {/* 分割条：拖动分配左右栏宽度（细条，hover 显形） */}
      <div
        className="resume-splitter"
        role="separator"
        aria-orientation="vertical"
        aria-label={t('resume.splitter')}
        onMouseDown={startSplitDrag}
      />

      {/* 右栏：聊天框 */}
      <section className="resume-right">
        <ChatPanel
          messages={store.messages}
          workingKind={selectWorkingKind(store)}
          workingStoppable={selectWorkingStoppable(store)}
          onSend={store.sendMessage}
          onStop={selectWorkingKind(store) === 'opening' ? store.stopOpening : () => store.stopGenerating(true)}
          sendDisabled={busy}
          draft={store.draft}
          onDraftChange={store.setDraft}
          header={
            /* 顶部工具条：优化点 + 资料集（ToolTab 标签，与简历窗口上边条同款） */
            <div className="chat-tools">
              {/* 应用建议：走 store.applySuggestions（统一 applying 锁定 + 刷新 + 切新 session） */}
              {/* 局部 ErrorBoundary（2026-09-09）：任一工具坞面板渲染崩只降级该面板，不整页白屏。 */}
              <PanelErrorBoundary name={t('resume.suggestionBasket')}>
                <SuggestionBasket />
              </PanelErrorBoundary>
              <PanelErrorBoundary name={t('resume.factsPanel')}>
                <FactsPanel />
              </PanelErrorBoundary>
            </div>
          }
        />
      </section>
    </div>
  )
}