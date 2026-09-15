import { useResumeStore } from '@/stores/resumeStore'
import { ToolTab } from '@/components/ToolTab'
import { BusyOverlay } from '@/components/BusyOverlay'
import { Button } from '@mantine/core'
import { useT } from '@/lib/i18n'

/** 版本创建日期格式化（版本行显示「第N稿 · summary · 日期」）。
 * 后端 created_at 是 ISO 字符串（UTC）；转本地 YYYY-MM-DD。无效则空串。 */
function formatVersionDate(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/**
 * 版本历史浮窗（2026-08-24）：左栏顶条「第N稿」触发钮 + 玻璃悬浮面板。
 *
 * 复用 ToolTab 原语（与优化点/资料集同款）：幽灵触发钮 + fixed 玻璃浮窗盖在内容上方，
 * 不再是占文档流的内联折叠面板。触发钮是纯文字「第N稿」（无图标）。
 *
 * 打开时重新拉版本列表 + 待执行建议数（上传后版本变了也不脱节）。
 */
export function VersionTab() {
  const t = useT()
  const store = useResumeStore()
  const busy = store.generating || store.applying
  const busyLabel = store.applying ? t('resume.busyApplying') : t('resume.busyAgentWorking')

  /** 展开浮窗时刷新：版本列表 + 待执行建议数。 */
  const loadOnOpen = () => {
    void store.loadVersions()
    void store.loadPendingCount()
  }

  return (
    <ToolTab label={`第 ${store.currentVersion} 稿`} onOpen={loadOnOpen} panelClassName="version-panel" ghost>
      <div className="suggestion-panel-head">
        <span className="suggestion-panel-title">{t('resume.versionHistory')}</span>
      </div>
      <BusyOverlay busy={busy} label={busyLabel} />
      <div className="version-panel-body">
        {store.versions.map((v) => (
          <div key={v.id} className="resume-version-row">
            <span className={`chip ${v.version === store.currentVersion ? 'chip-green' : 'chip-gray'}`}>
              第{v.version}稿
            </span>
            <span className="resume-version-source" title={v.summary || v.source}>
              {v.summary ||
                (v.source === 'upload'
                  ? t('resume.srcUpload')
                  : v.source === 'generated'
                    ? t('resume.srcGenerated')
                    : t('resume.srcRollback'))}
            </span>
            <span className="resume-version-date">{formatVersionDate(v.created_at)}</span>
            {/* 回看入口：点版本 = 主区切回看模式（当时文档 + 当时聊天 + 「回滚到当前」）。
                当前稿行不显示「回看」（不能回看自己）。 */}
            {v.version !== store.currentVersion && (
              <Button variant="default" size="compact-sm" onClick={() => void store.openReview(v.id)}>
                {t('resume.versionReview')}
              </Button>
            )}
          </div>
        ))}
      </div>
    </ToolTab>
  )
}
