import { useState, useEffect } from 'react'
import { History, X, RotateCcw } from 'lucide-react'
import { Button } from '@mantine/core'
import { modals } from '@mantine/modals'
import { ResumePreview } from '@/components/ResumePreview'
import { MessageMarkdown } from '@/components/chat/MessageMarkdown'
import type { ChatMessage } from '@/types/resume'
import { useResumeStore } from '@/stores/resumeStore'
import { useT } from '@/lib/i18n'

/** 从 blob URL 读文本内容（TXT/MD 原文预览用）。与 ResumePage 的 useBlobText 同构。 */
function useBlobText(url: string | null, ext: string): string {
  const [text, setText] = useState('')
  useEffect(() => {
    if (!url || !(ext === 'txt' || ext === 'md')) {
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

/**
 * 回看模式（2026-08-10，§11.1「干净轮回 + 历史归档」）：主区整体切换到某版本当时的样子。
 *
 * - 左栏 = 该版本当时的简历文档（原生预览优先，无原件落 Markdown）。
 * - 右栏 = 该版本当时的聊天记录（只读，复用消息渲染：event 气泡 / 文本气泡 / 分隔标记）。
 * - 顶栏 = 「回滚到当前」+「退出回看」。
 *
 * 回看是纯读状态：不碰当前对话（messages/sessionId），退出后回到当前版本。
 * 「回滚到当前」= 走现有回滚流程（数据操作 + 新 session + 聊天清空，store.rollbackTo）。
 */
export function VersionReview() {
  const t = useT()
  const review = useResumeStore((s) => s.review)
  const currentVersion = useResumeStore((s) => s.currentVersion)
  const rollbackTo = useResumeStore((s) => s.rollbackTo)
  const beginRollback = useResumeStore((s) => s.beginRollback)
  const closeReview = useResumeStore((s) => s.closeReview)
  const loadPendingCount = useResumeStore((s) => s.loadPendingCount)
  // S7（2026-08-14）统一锁定：正在回复 / 应用建议改写中，回滚按钮灰掉（写动作禁用，
  // 回看本身仍放开——纯读不锁）。rollbackTo 里的 toast 是冲破限制时的兜底。
  // 2026-09-24：回滚进行中（rollingBack）也灰掉——同一次回看的按钮不该被连点第二遍。
  const generating = useResumeStore((s) => s.generating)
  const applying = useResumeStore((s) => s.applying)
  const rollingBack = useResumeStore((s) => s.rollingBack)
  const rollbackDisabled = generating || applying || rollingBack
  const nativeText = useBlobText(review?.resumeUrl ?? null, review?.resumeExt ?? '')

  if (!review) return null

  /** 回滚到此版：走回滚流程（数据操作 + 新 session + 聊天清空，store.rollbackTo）。
   *  2026-09-24：整份恢复该版**开始时**的工作台（资料集 + 改动记录），不再有「连事实一起」
   *  开关——「只回文档不回工作台」会留下锚在旧内容上的待办，是半吊子状态。
   *  还有未定论的点时先弹一道警告（它们会随工作台回到目标版开始的样子）。 */
  const handleRollback = async () => {
    await loadPendingCount()
    const pending = useResumeStore.getState().pendingSuggestionCount
    const doRollback = () => {
      // 乐观回退（2026-09-24）：先切界面（进乐观态 → 关回看后由 ResumePage 按 rollbackTarget
      // 渲染目标稿 + 「正在回滚…」），再发**很慢**的回滚请求——否则用户点完要干等后端，像「没反应」。
      // 顺序：beginRollback → closeReview（把 blob URL 移交给 rollbackTarget，不 revoke）→ 发请求。
      beginRollback()
      closeReview()
      void rollbackTo(review.id)
    }
    modals.openConfirmModal({
      title: pending > 0 ? t('resume.versionHistory') : t('resume.rollback'),
      centered: true,
      children: (
        <p style={{ whiteSpace: 'pre-wrap' }}>
          {pending > 0 ? t('resume.rollbackClearPending').replace('{n}', String(pending)) : t('resume.rollbackConfirm')}
        </p>
      ),
      labels: { confirm: t('common.confirm'), cancel: t('common.cancel') },
      confirmProps: { color: 'green' },
      onConfirm: doRollback,
    })
  }

  return (
    <div className="resume-layout">
      {/* 左栏：该版本当时的文档预览 */}
      <section className="resume-left">
        <div className="resume-left-head">
          <span className="resume-left-title">
            <History size={14} /> {t('resume.versionReview')} 第{review.version}稿
          </span>
          <span className="chip chip-gray">{review.summary || (review.source === 'upload' ? t('resume.srcUpload') : review.source === 'generated' ? t('resume.srcGenerated') : t('resume.srcRollback'))}</span>
          {/* 回滚/退出入口（S8 4a）：从右栏聊天框挪到左栏简历展示框——操作对象在左栏，按钮就放在左栏 */}
          <div className="resume-left-head-actions">
            {review.version !== currentVersion && (
              <Button size="compact-sm" leftSection={<RotateCcw size={13} />} onClick={handleRollback} disabled={rollbackDisabled}>
                {t('resume.rollbackToCurrent')}
              </Button>
            )}
            <Button variant="default" size="compact-sm" leftSection={<X size={13} />} onClick={closeReview}>
              {t('resume.exitReview')}
            </Button>
          </div>
        </div>
        <div className="resume-preview">
          {review.resumeUrl || review.markdown || review.html ? (
            <ResumePreview
              url={review.resumeUrl}
              ext={review.resumeUrl ? review.resumeExt : ''}
              markdown={review.markdown}
              text={nativeText}
              html={review.html}
            />
          ) : (
            <div className="empty-state resume-empty">
              <p>{t('resume.versionReviewEmpty')}</p>
            </div>
          )}
        </div>
      </section>

      {/* 右栏：该版本当时的聊天记录（只读） */}
      <section className="resume-right">
        <div className="chat-panel">
          <div className="review-chat-head">
            <span className="review-chat-title">{t('resume.versionReviewChat')}</span>
          </div>
          <div className="chat-messages review-chat-messages">
            {review.messages.length === 0 ? (
              <div className="chat-empty-hint">{t('resume.versionReviewNoChat')}</div>
            ) : (
              review.messages.map((m) => <ReviewMessage key={m.id} m={m} />)
            )}
          </div>
        </div>
      </section>
    </div>
  )
}

/** 回看消息渲染：event → 系统气泡，divider → 版本分隔，text → 文本气泡。（建议走右侧优化点面板，聊天流内不渲染卡片，见 ADR 0017。） */
function ReviewMessage({ m }: { m: ChatMessage }) {
  if (m.kind === 'event') {
    return (
      <div className="chat-row event">
        <span className="chat-event-bubble">{m.content}</span>
      </div>
    )
  }
  if (m.kind === 'divider') {
    return (
      <div className="chat-divider">
        <span className="chat-divider-line" />
        <span className="chat-divider-label">{`第${m.version}稿`}</span>
        <span className="chat-divider-line" />
      </div>
    )
  }
  return (
    <div className={`chat-row ${m.role}`}>
      <div className="chat-bubble">
        {m.role === 'assistant' ? <MessageMarkdown content={m.content} /> : <div className="chat-text">{m.content}</div>}
      </div>
    </div>
  )
}
