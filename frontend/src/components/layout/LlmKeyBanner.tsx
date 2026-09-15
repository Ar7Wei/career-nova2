import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, CloseButton } from '@mantine/core'
import { TriangleAlert } from 'lucide-react'
import { useSettingsStore } from '@/stores/settingsStore'
import { useT } from '@/lib/i18n'

/**
 * 「LLM key 未配置」顶部横幅（ADR 0021 决定 2：只做提示，不做强制向导）。
 *
 * 触发条件：设置已加载 且 后端 settings 表 llm_api_key 为空（llmKeyConfigured 由
 * GET 掩码响应推断）。识别/优化/对话生成这些 AI 功能都会因缺 key 报错——横幅把
 * 「得先填 key」怼到用户脸上，但不挡逛界面。可关闭（本次会话内不再弹，重启再提醒，
 * 不持久化——不配 key 就该一直提醒）。
 */
export function LlmKeyBanner() {
  const t = useT()
  const navigate = useNavigate()
  const loaded = useSettingsStore((s) => s.loaded)
  const llmKeyConfigured = useSettingsStore((s) => s.llmKeyConfigured)
  const [dismissed, setDismissed] = useState(false)

  if (!loaded || llmKeyConfigured || dismissed) return null

  const goToKeySettings = () => {
    navigate('/settings', { state: { scrollTo: 'group-model' } })
  }

  return (
    <div className="llm-key-banner" role="alert">
      <TriangleAlert size={14} className="llm-key-banner-icon" />
      <span className="llm-key-banner-text">{t('status.llmKeyMissing')}</span>
      <Button
        variant="subtle"
        size="compact-sm"
        className="llm-key-banner-action"
        onClick={goToKeySettings}
      >
        {t('status.llmKeyMissingAction')}
      </Button>
      <CloseButton
        size="sm"
        className="llm-key-banner-close"
        onClick={() => setDismissed(true)}
        aria-label={t('status.llmKeyMissingDismiss')}
      />
    </div>
  )
}
