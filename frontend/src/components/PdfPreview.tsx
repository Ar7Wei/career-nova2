import { useEffect, useRef, useState } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import 'react-pdf/dist/Page/AnnotationLayer.css'
import 'react-pdf/dist/Page/TextLayer.css'

// 配置 pdf.js worker：Vite 下用 ?url 引入打包后的 worker 文件。
// 这是 react-pdf 在 Vite 里的标准配法，不配则不渲染。
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

interface PdfPreviewProps {
  /** 本地 objectURL（blob:）。 */
  url: string
}

/** PDF 预览：react-pdf 渲染全部页、按容器宽度自适应、可滚动、带页码。套 Alger 皮。 */
export function PdfPreview({ url }: PdfPreviewProps) {
  const [numPages, setNumPages] = useState(0)
  const [error, setError] = useState(false)
  const containerRef = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState(0)

  // 量容器宽度，让 PDF 页自适应左栏宽（留 padding）。
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const measure = () => setWidth(Math.max(el.clientWidth - 32, 200))
    measure()
    const ro = new ResizeObserver(measure)
    ro.observe(el)
    return () => ro.disconnect()
  }, [])

  if (error) {
    return (
      <div className="empty-state resume-empty">
        <p>无法预览此 PDF 文件。</p>
      </div>
    )
  }

  return (
    <div className="pdf-preview" ref={containerRef}>
      <Document
        file={url}
        onLoadSuccess={({ numPages }) => setNumPages(numPages)}
        onLoadError={(err) => {
          console.error('[PdfPreview] load error:', err)
          setError(true)
        }}
        loading={<div className="pdf-loading">加载中…</div>}
      >
        {width > 0 &&
          Array.from({ length: numPages }, (_, i) => (
            <div key={i} className="pdf-page">
              <Page pageNumber={i + 1} renderTextLayer renderAnnotationLayer width={width} />
              <div className="pdf-page-num">
                {i + 1} / {numPages}
              </div>
            </div>
          ))}
      </Document>
    </div>
  )
}
