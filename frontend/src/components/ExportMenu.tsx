import { Download } from 'lucide-react'
import { Button, Menu } from '@mantine/core'
import { notifications } from '@mantine/notifications'
import { useT } from '@/lib/i18n'
import { useResumeStore } from '@/stores/resumeStore'

/**
 * 下载简历按钮 + 菜单（PDF / Markdown / HTML）。
 *
 * 2026-08-13：简历已通过排⻚闭环收敛到正好一页 A4（resume_documents.html 含 A4 外壳），
 * PDF = Electron 隐藏窗口 printToPDF（矢量直转，不截图）；Markdown/HTML = 存原文。
 * Electron 才可用（window.desktop.exportResume 存在）；浏览器 dev → 禁用 + "桌面版可用"提示。
 */

type ExportFormat = 'pdf' | 'md' | 'html'

export function ExportMenu() {
  const t = useT()
  const isDesktop = typeof window !== 'undefined' && !!window.desktop?.exportResume
  const previewHtml = useResumeStore((s) => s.previewHtml)
  const previewMarkdown = useResumeStore((s) => s.previewMarkdown)
  const currentSource = useResumeStore((s) => s.currentSource)
  // 只有「我们自己生成的」稿才给下载（2026-08-24）：上传的最初稿（upload）和没简历的空态
  // 都不提供——用户手里本就有原件，下载它没意义。generated / rollback 稿才开放。
  const downloadable = currentSource === 'generated' || currentSource === 'rollback'
  const hasContent = downloadable && (!!previewHtml || !!previewMarkdown)

  const doExport = async (format: ExportFormat) => {
    if (!window.desktop?.exportResume) return
    try {
      const res = await window.desktop.exportResume({
        format,
        html: previewHtml,
        markdown: previewMarkdown,
        defaultName: `简历-第${useResumeStore.getState().currentVersion}稿`,
      })
      if (res.saved) {
        notifications.show({
          color: 'green',
          title: t('resume.download'),
          message: res.filePath ?? '',
        })
      }
      // canceled → 静默（用户取消保存对话框）
    } catch (err) {
      notifications.show({
        color: 'red',
        title: t('resume.download'),
        message: err instanceof Error ? err.message : String(err),
      })
    }
  }

  return (
    <Menu position="bottom-end" withinPortal>
      <Menu.Target>
        <Button
          variant="default"
          size="sm"
          disabled={!isDesktop || !hasContent}
          leftSection={<Download size={14} />}
          title={isDesktop ? undefined : t('resume.downloadDesktopOnly')}
        >
          {t('resume.download')}
        </Button>
      </Menu.Target>
      <Menu.Dropdown>
        <Menu.Item disabled={!previewHtml} onClick={() => doExport('pdf')}>
          PDF
        </Menu.Item>
        <Menu.Item disabled={!previewHtml} onClick={() => doExport('html')}>
          HTML
        </Menu.Item>
        <Menu.Item onClick={() => doExport('md')}>Markdown</Menu.Item>
      </Menu.Dropdown>
    </Menu>
  )
}
