import { useEffect, useMemo, useState, type FocusEvent } from 'react'
import { useLocation } from 'react-router-dom'
import { PlugZap, Loader2, Check, X, AlertTriangle, FolderOpen } from 'lucide-react'
import { ActionIcon, Button, Select, TextInput, Tooltip } from '@mantine/core'
import { modals } from '@mantine/modals'
import { notifications } from '@mantine/notifications'
import api from '@/lib/api'
import { userErrorText } from '@/lib/errors'
import { useSettingsStore, type LlmConfigOverrides } from '@/stores/settingsStore'
import { useElectronSettingsStore } from '@/stores/electronSettingsStore'
import { useI18n, useT, type Locale } from '@/lib/i18n'
import { LLM_PRESETS, matchPreset } from '@/lib/llmPresets'
import { useDraftSetting } from './useDraftSetting'
import { armPendingToast } from '@/lib/pendingToast'

/** 设置页分组（二级导航项 + 对应 section id）。顺序即导航展示顺序。 */
const SETTINGS_GROUPS = [
  { id: 'group-general', key: 'settings.groupGeneral' },
  { id: 'group-model', key: 'settings.groupModel' },
  { id: 'group-storage', key: 'settings.groupStorage' },
  { id: 'group-log', key: 'settings.groupLog' },
  { id: 'group-danger', key: 'settings.groupDanger' },
] as const

/** 设置面板：左分组导航 + 右侧「标题+描述+控件」行。三条真相源各管各的控件。 */
export function SettingsPage() {
  const t = useT()
  const location = useLocation()
  const settings = useSettingsStore()
  const eSettings = useElectronSettingsStore()
  const locale = useI18n((s) => s.locale)

  // 失焦即存（2026-08-24）：以下 5 项统一走 useDraftSetting —— 草稿同步 store 真相值，
  // 失焦/回车提交；空 diff no-op；非法/失败回滚 + red toast；成功 green toast。
  // 端口/数据目录/日志目录/日志天数重启生效；上下文阈值即时生效。
  const port = useDraftSetting({
    value: eSettings.backend_port,
    validate: (raw) => {
      const n = Number(raw)
      return Number.isInteger(n) && n >= 1024 && n <= 65535
        ? { ok: true, value: n }
        : { ok: false, message: t('settings.backendPortInvalid') }
    },
    persist: (v) => eSettings.update({ backend_port: v }),
    title: t('settings.backendPort'),
    savedMessage: t('settings.savedRestart'),
    failedMessage: t('settings.saveFailed'),
  })

  const dataDir = useDraftSetting({
    value: eSettings.data_dir,
    validate: (raw) => ({ ok: true, value: raw }), // 目录非空字符串，不额外校验
    persist: (v) => eSettings.update({ data_dir: v }),
    title: t('settings.dataDir'),
    savedMessage: t('settings.savedRestart'),
    failedMessage: t('settings.saveFailed'),
  })

  const logDir = useDraftSetting({
    value: eSettings.log_dir,
    validate: (raw) => ({ ok: true, value: raw }),
    persist: (v) => eSettings.update({ log_dir: v }),
    title: t('settings.logDir'),
    savedMessage: t('settings.savedRestart'),
    failedMessage: t('settings.saveFailed'),
  })

  const logRetention = useDraftSetting({
    value: eSettings.log_retention_days,
    validate: (raw) => {
      const n = Number(raw)
      return Number.isInteger(n) && n >= 1 && n <= 365
        ? { ok: true, value: n }
        : { ok: false, message: t('settings.logRetentionInvalid') }
    },
    persist: (v) => eSettings.update({ log_retention_days: v }),
    title: t('settings.logRetentionDays'),
    savedMessage: t('settings.savedRestart'),
    failedMessage: t('settings.saveFailed'),
  })

  // Base URL：失焦/回车保存（不再逐字符 PATCH），保存成功后才作废探测结果。
  const baseUrl = useDraftSetting({
    value: settings.llm_base_url,
    validate: (raw) => ({ ok: true, value: raw }),
    persist: (v) => settings.update({ llm_base_url: v }),
    title: t('settings.llmBaseUrl'),
    savedMessage: t('settings.saved'),
    failedMessage: t('settings.saveFailed'),
    afterSave: () => {
      setSelectedProvider(null) // 手动改 URL → 不再锁定旧厂商选中项
      setConfigDirty(true)
      invalidateProbe()
    },
  })

  // 日志统计（立即清理旁展示）：logs:stats 返回文件数 + 总字节数；Electron 下加载，浏览器下不显示。
  const [logStats, setLogStats] = useState<{ fileCount: number; totalBytes: number } | null>(null)

  /** 字节数人性化格式（<1MB 显 KB，其余 MB 一位小数）。 */
  const formatBytes = (bytes: number) => {
    if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
    if (bytes >= 1024) return `${Math.round(bytes / 1024)} KB`
    return `${bytes} B`
  }

  // API key 本地草稿：onChange 只改草稿，失焦/回车才提交，避免每敲一个字符就 PATCH 被掩码清空。
  const [apiKeyDraft, setApiKeyDraft] = useState('')
  const [apiKeyDirty, setApiKeyDirty] = useState(false)

  // LLM 探测（测试连接 + 拉模型）合并为一个状态机：
  // idle=未探测 | probing=探测中 | ok=全成功（拉到 models）| partial=连接过但拉不到 | fail=连接失败
  const [probeState, setProbeState] = useState<'idle' | 'probing' | 'ok' | 'partial' | 'fail'>('idle')
  const [probeMessage, setProbeMessage] = useState('')
  const [fetchedModels, setFetchedModels] = useState<string[]>([])
  // fetchedModels 属于哪个 base_url；换供应商时清空，避免张冠李戴
  const [fetchedForBaseUrl, setFetchedForBaseUrl] = useState('')
  // 模型行（ok 态下拉）：自定义模型名入口是否展开；展开时的输入草稿（失焦保存）
  const [showCustomModel, setShowCustomModel] = useState(false)
  const [customModelDraft, setCustomModelDraft] = useState('')
  // 厂商下拉显式选中项：默认跟随 base_url 匹配的预设；用户选「自定义」后记住 custom，
  // 不再被 base_url 推导弹回（Custom 不改 base_url，纯靠推导永远选不中）。
  const [selectedProvider, setSelectedProvider] = useState<string | null>(null)
  // 配置是否已变更：变更后「已配置」chip 应隐藏（配置不再是探测时的那份），
  // 探测成功后恢复显示。
  const [configDirty, setConfigDirty] = useState(false)

  useEffect(() => {
    if (!settings.loaded) void settings.load()
    if (!eSettings.loaded) void eSettings.load()
    void loadLogStats()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // 状态栏「模型名」点击 → navigate('/settings', {state:{scrollTo}})。挂载后滚到对应分组。
  // 用 setTimeout 等数据加载/首帧渲染完再滚；若目标仍不存在（分组尚未渲染）则静默跳过。
  useEffect(() => {
    const targetId = (location.state as { scrollTo?: string } | null)?.scrollTo
    if (!targetId) return
    const timer = setTimeout(() => {
      document.getElementById(targetId)?.scrollIntoView({ behavior: 'smooth' })
    }, 0)
    return () => clearTimeout(timer)
  }, [location.state])

  // LLM 预设：当前 base_url 匹配到哪个预设，下拉就选中它（自定义则无选中）。
  const activePreset = matchPreset(settings.llm_base_url)
  // 拉到的模型所属的 base_url：与当前一致才算"拉到过"，防串供应商。
  const currentBaseUrl = settings.llm_base_url.trim().replace(/\/+$/, '')
  const fetchedMatchCurrent = fetchedForBaseUrl && fetchedForBaseUrl === currentBaseUrl

  // 浏览文件夹：原生对话框选目录，选中即存（对话框抢焦点、失焦不触发，故选完直接提交）。
  const pickDataDir = async () => {
    if (!window.desktop?.pickDirectory) return
    const picked = await window.desktop.pickDirectory()
    if (picked) void dataDir.commitRaw(picked)
  }

  // 浏览日志目录：复用原生「选择文件夹」对话框，选中即存。
  const pickLogDir = async () => {
    if (!window.desktop?.pickDirectory) return
    const picked = await window.desktop.pickDirectory()
    if (picked) void logDir.commitRaw(picked)
  }

  // 拉取日志统计（文件数 + 总字节数）：设置页挂载 / 清理后刷新；浏览器下无 IPC，跳过。
  // 用 function 声明（提升）而非 const 箭头——挂载 useEffect 在函数体前段调用它，
  // 避免 TDZ（react-hooks/immutability）。只捕获稳定 setter 与 window.desktop，无易变依赖。
  function loadLogStats() {
    if (!window.desktop?.logs?.stats) return
    void window.desktop.logs.stats().then(setLogStats).catch(() => setLogStats(null))
  }

  // 立即清理日志：调 Electron IPC 删日志目录下按日期日志文件。
  const clearLogs = async () => {
    if (!window.desktop?.logs?.clear) return
    try {
      const { removed } = await window.desktop.logs.clear()
      void loadLogStats()
      notifications.show({
        color: removed > 0 ? 'green' : 'gray',
        title: t('settings.logClear'),
        message: removed > 0 ? t('settings.logClearDone').replace('{n}', String(removed)) : t('settings.logClearEmpty'),
      })
    } catch (err) {
      notifications.show({
        color: 'red',
        title: t('settings.logClear'),
        message: userErrorText(err),
      })
    }
  }

  // 核爆（「重新开始」）：确认后调后端 /reset/all 全量清空求职数据（配置保留），再刷新前端。
  const doResetAll = async () => {
    try {
      await api.post('/v1/reset/all')
      // reload 会抹掉同一帧的 toast，故把提示 + 当前语言存起来，交给 App 启动时补弹
      //（见 lib/pendingToast.ts）。language 后端 reset_all 保留，存的是真值不是猜的。
      armPendingToast(
        { color: 'green', titleKey: 'settings.resetAll', messageKey: 'settings.resetAllDone' },
        locale,
      )
      // 数据全清后整页重载：让简历/投递/对话各 store 回到空态（比逐 store 复位可靠）。
      window.location.reload()
    } catch (err) {
      notifications.show({ color: 'red', title: t('settings.resetAll'), message: userErrorText(err) })
    }
  }

  // 危险确认：列出将删内容，确认按钮红色。
  const confirmResetAll = () => {
    modals.openConfirmModal({
      title: t('settings.resetAllConfirmTitle'),
      children: <div style={{ whiteSpace: 'pre-line' }}>{t('settings.resetAllConfirmBody')}</div>,
      labels: { confirm: t('settings.resetAllConfirmBtn'), cancel: t('common.cancel') },
      confirmProps: { color: 'red' },
      onConfirm: () => void doResetAll(),
    })
  }

  // 选预设：只自动填 base_url + 清空模型名（不预填示例模型——未探测前 model 保持空，
  // 等探测到真实列表再选；预设里那些示例模型不该出现在 UI 上）。
  // 选「自定义」：清空 base_url（Custom 无映射，URL 由用户自己填，不留旧预设值误导）。
  const applyPreset = (id: string) => {
    if (id === 'custom') {
      void settings.update({ llm_base_url: '', llm_model: '' })
      return
    }
    const preset = LLM_PRESETS.find((p) => p.id === id)
    if (!preset) return
    void settings.update({ llm_base_url: preset.baseUrl, llm_model: '' })
  }

  // 提交 API key 草稿：非空才发；失焦/回车后清草稿（store 回掩码，不回填）。
  // 若是"聚焦查看掩码后失焦"（dirty=false），只清掉本地掩码，不触后端。
  const commitApiKey = () => {
    if (apiKeyDirty && apiKeyDraft.trim()) {
      void settings.update({ llm_api_key: apiKeyDraft.trim() })
    }
    setApiKeyDraft('')
    setApiKeyDirty(false)
  }

  // 已配置 key 时视觉可见：输入框填掩码星号（不像"没配置"）；聚焦全选，输入即替换。
  const MASKED_KEY_HINT = '★'.repeat(12)
  // 显示值与草稿分离：已配置且没在输入时，显示值恒为掩码（失焦也可见）；
  // 一旦聚焦/输入，显示用户正在敲的草稿。
  const apiKeyDisplay = apiKeyDraft !== '' ? apiKeyDraft : settings.llmKeyConfigured ? MASKED_KEY_HINT : ''
  const handleApiKeyFocus = (e: FocusEvent<HTMLInputElement>) => {
    if (settings.llmKeyConfigured) e.target.select()
  }

  // 探测：先测连接再拉模型（一个按钮、一次请求）。带未保存的 key 草稿一起发。
  // 三态：fail=连接失败 | partial=连接过但拉不到列表 | ok=全成功。
  // 探测成功 → 配置已验证，「已配置」chip 恢复显示。
  const runProbe = async () => {
    setProbeState('probing')
    setProbeMessage('')
    const overrides: LlmConfigOverrides = {}
    if (apiKeyDirty && apiKeyDraft.trim()) overrides.llm_api_key = apiKeyDraft.trim()
    // 探测不预填模型（2026-08-12）：本地不存默认模型名。已配模型时后端用它做连接测试；
    // 未配模型时后端直接拉模型列表（列表本身即连接验证），拉到后从这里选。
    const res = await settings.probeLlm(overrides)
    if (!res.ok) {
      setProbeState('fail')
      setProbeMessage(res.message)
      setFetchedModels([])
      return
    }
    setConfigDirty(false) // 连接成功：当前配置有效，恢复「已配置」标记
    if (res.models_ok) {
      setFetchedModels(res.models)
      setFetchedForBaseUrl(currentBaseUrl)
      setProbeState('ok')
    } else {
      setProbeState('partial')
      setProbeMessage(res.message)
      setFetchedModels([])
    }
  }

  // 探测按钮可用性：已存 key 或草稿填了新 key 才能探测（不填 key 必然失败）。
  // 配置变更会作废探测结果，回到 idle（需重新探测）。
  const probeEnabled = settings.llmKeyConfigured || (apiKeyDirty && !!apiKeyDraft.trim())
  // 作废探测：状态回 idle + 清掉拉到的模型列表与归属 base_url（防旧列表残留，
  // 换供应商/改 URL 后模型下拉误可用）。fetchedForBaseUrl 置空使 fetchedMatchCurrent 恒 false。
  const invalidateProbe = () => {
    setProbeState('idle')
    setProbeMessage('')
    setFetchedModels([])
    setFetchedForBaseUrl('')
  }

  // 改 key：探测结果作废 + 配置变更，需重测。「已配置」chip 隐藏。
  const onApiKeyChange = (v: string) => {
    setApiKeyDraft(v)
    setApiKeyDirty(true)
    setConfigDirty(true)
    invalidateProbe()
  }
  // 从拉到的模型下拉里选：直接套用模型名。列表就是从当前 base_url 拉的，
  // 所以不需要再补 base_url（拉到的模型天然属于当前供应商）。
  const applyFetchedModel = (model: string) => {
    if (!model) return
    void settings.update({ llm_model: model })
  }

  // 模型下拉候选：仅当成功探测且拉到的是当前供应商时，才有真实列表可选项；
  // 未探测/未连接 → 无可选项（模型行禁用，只显示已存值，不能选）。
  const modelCandidates = useMemo(() => {
    if (fetchedMatchCurrent && fetchedModels.length > 0) return fetchedModels
    return []
  }, [fetchedMatchCurrent, fetchedModels])

  // 自定义模型名：失焦/回车保存草稿并收起入口；空则收起（不修改）。
  const commitCustomModel = () => {
    if (customModelDraft.trim()) {
      void settings.update({ llm_model: customModelDraft.trim() })
    }
    setCustomModelDraft('')
    setShowCustomModel(false)
  }
  const openCustomModel = () => {
    setCustomModelDraft(settings.llm_model)
    setShowCustomModel(true)
  }

  // 探测结果提示（轻量内联行：小图标 + 状态色文字，不铺底色）。
  // probeMessage 来自后端原始错误串（可能长/英文），不能走 i18n 词条，直接渲染。
  const probeHint =
    probeState === 'ok' ? (
      <div className="hint-inline hint-ok">
        <Check size={13} /> {t('settings.llmProbeOk')}
      </div>
    ) : probeState === 'partial' ? (
      <div className="hint-inline hint-warn">
        <AlertTriangle size={13} /> {probeMessage || t('settings.llmProbePartial')}
      </div>
    ) : probeState === 'fail' ? (
      <div className="hint-inline hint-error">
        <X size={13} /> {probeMessage || t('settings.llmProbeFail')}
      </div>
    ) : null

  // 探测中/失败/部分成功 → 探测按钮文案显示「重试」
  const probeBtnLabel =
    probeState === 'idle' || probeState === 'probing' ? t('settings.llmProbe') : t('settings.llmProbeRetry')

  // 点击导航跳转：平滑滚动到对应分组（无高亮，仅跳转）。
  const scrollToGroup = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth' })
  }

  return (
    <div className="settings-page">
      <div className="settings-layout">
        <nav className="settings-nav">
          {/* 标题+副标题缩进左栏（方案 B：与投递页一致，去掉横贯 sticky 页头） */}
          <div className="subnav-head">
            <h1 className="page-title">{t('page.settings.title')}</h1>
            <p className="page-subtitle">{t('page.settings.desc')}</p>
          </div>
          {/* 分组跳转用 button + scrollIntoView，不用 <a href="#…"> 锚点——应用是 HashRouter，
              hash 变化会被当作路由路径匹配，落到不存在的路由导致整页白屏。
              无滚动高亮（2026-08-23 决定去掉 scrollspy，属低价值 UX，不再折腾）。 */}
          {SETTINGS_GROUPS.map((g) => (
            <Button
              key={g.id}
              variant="subtle"
              className="settings-nav-item"
              onClick={() => scrollToGroup(g.id)}
            >
              {t(g.key)}
            </Button>
          ))}
        </nav>

        <div className="settings-body">
          <section id="group-general" className="card settings-group">
            <h2>{t('settings.groupGeneral')}</h2>
            <div className="setting-row">
              <div className="setting-text">
                <div className="setting-title">{t('settings.language')}</div>
                <div className="setting-desc">{t('settings.languageDesc')}</div>
              </div>
              <div className="setting-control">
                <Select
                  data={[
                    { value: 'zh', label: '中文' },
                    { value: 'en', label: 'English' },
                  ]}
                  value={locale}
                  onChange={(v) => {
                    if (v) settings.update({ language: v as Locale })
                  }}
                />
              </div>
            </div>
            <div className="setting-row">
              <div className="setting-text">
                <div className="setting-title">{t('settings.closeAction')}</div>
                <div className="setting-desc">{t('settings.closeActionDesc')}</div>
              </div>
              <div className="setting-control">
                <Select
                  data={[
                    { value: 'tray', label: t('settings.closeToTray') },
                    { value: 'quit', label: t('settings.closeQuit') },
                  ]}
                  value={eSettings.close_action}
                  disabled={!eSettings.available}
                  onChange={(v) => {
                    if (v) eSettings.update({ close_action: v as 'tray' | 'quit' })
                  }}
                />
              </div>
            </div>
            <div className="setting-row">
              <div className="setting-text">
                <div className="setting-title">{t('settings.backendPort')}</div>
                <div className="setting-desc">{t('settings.backendPortDesc')}</div>
              </div>
              <div className="setting-control">
                <TextInput
                  className="port-input"
                  type="number"
                  value={port.draft}
                  disabled={!eSettings.available}
                  onChange={(e) => port.setDraft(e.currentTarget.value)}
                  onBlur={port.commit}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') port.commit()
                  }}
                />
              </div>
            </div>
          </section>

          <section id="group-model" className="card settings-group">
            <h2>{t('settings.groupModel')}</h2>
            <div className="setting-row">
              <div className="setting-text">
                <div className="setting-title">{t('settings.llmProviderRow')}</div>
                <div className="setting-desc">{t('settings.llmProviderDesc')}</div>
              </div>
              <div className="setting-control">
                <Select
                  data={[
                    { value: 'custom', label: t('settings.llmProviderCustom') },
                    ...LLM_PRESETS.map((p) => ({ value: p.id, label: t(p.labelKey) })),
                  ]}
                  value={selectedProvider ?? activePreset?.id ?? 'custom'}
                  allowDeselect={false}
                  onChange={(v) => {
                    if (!v) return
                    setSelectedProvider(v)
                    setConfigDirty(true)
                    applyPreset(v)
                    invalidateProbe()
                  }}
                />
              </div>
            </div>
            <div className="setting-row">
              <div className="setting-text">
                <div className="setting-title">{t('settings.llmBaseUrl')}</div>
                <div className="setting-desc">{t('settings.llmBaseUrlDesc')}</div>
              </div>
              <div className="setting-control">
                <TextInput
                  value={baseUrl.draft}
                  placeholder="https://api.openai.com/v1"
                  onChange={(e) => baseUrl.setDraft(e.currentTarget.value)}
                  onBlur={baseUrl.commit}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') baseUrl.commit()
                  }}
                />
              </div>
            </div>
            <div className="setting-row">
              <div className="setting-text">
                <div className="setting-title">{t('settings.llmApiKey')}</div>
                <div className="setting-desc">{t('settings.llmApiKeyDesc')}</div>
              </div>
              <div className="setting-control">
                <TextInput
                  className="api-key-input"
                  type="password"
                  value={apiKeyDisplay}
                  placeholder={settings.llmKeyConfigured ? t('settings.llmApiKeySetHint') : undefined}
                  onChange={(e) => onApiKeyChange(e.currentTarget.value)}
                  onFocus={handleApiKeyFocus}
                  onBlur={commitApiKey}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') commitApiKey()
                  }}
                  rightSectionWidth="auto"
                  rightSection={
                    settings.llmKeyConfigured && !configDirty ? (
                      <span className="chip chip-green input-affix">
                        <Check size={12} /> {t('settings.llmApiKeyConfigured')}
                      </span>
                    ) : null
                  }
                />
              </div>
            </div>
            <div className="setting-row">
              <div className="setting-text">
                <div className="setting-title">{t('settings.llmProbe')}</div>
                <div className="setting-desc">{t('settings.llmProbeDesc')}</div>
              </div>
              <div className="setting-control">
                <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                  <Button
                    variant="default"
                    size="sm"
                    onClick={runProbe}
                    disabled={!probeEnabled || probeState === 'probing'}
                    leftSection={probeState === 'probing' ? <Loader2 size={13} className="spin" /> : <PlugZap size={13} />}
                  >
                    {probeBtnLabel}
                  </Button>
                </div>
                {probeHint}
                {!probeEnabled && probeState === 'idle' && (
                  <div className="hint-inline">{t('settings.llmProbeHintDisabled')}</div>
                )}
              </div>
            </div>
            <div className="setting-row">
              <div className="setting-text">
                <div className="setting-title">{t('settings.llmModel')}</div>
                <div className="setting-desc">{t('settings.llmModelDesc')}</div>
              </div>
              <div className="setting-control">
                {showCustomModel ? (
                  <TextInput
                    value={customModelDraft}
                    placeholder="my-model-name"
                    autoFocus
                    onChange={(e) => setCustomModelDraft(e.currentTarget.value)}
                    onBlur={commitCustomModel}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') commitCustomModel()
                    }}
                  />
                ) : (
                  <Select
                    data={[
                      // 已存模型不在探测列表里（自定义）：直接显示模型名，不加「（自定义）」标记
                      ...(settings.llm_model && !modelCandidates.includes(settings.llm_model)
                        ? [{ value: settings.llm_model, label: settings.llm_model }]
                        : []),
                      ...modelCandidates.map((m) => ({ value: m, label: m })),
                      { value: '__custom__', label: t('settings.llmModelCustomEntry') },
                    ]}
                    value={settings.llm_model}
                    disabled={!(probeState === 'ok' && fetchedMatchCurrent)}
                    placeholder={t('settings.llmSelectModelPlaceholder')}
                    allowDeselect={false}
                    onChange={(v) => {
                      if (!v) return
                      if (v === '__custom__') {
                        openCustomModel()
                        return
                      }
                      applyFetchedModel(v)
                    }}
                  />
                )}
                {probeState === 'ok' && fetchedMatchCurrent && (
                  <div className="hint-inline">{t('settings.llmModelProbedHint')}</div>
                )}
              </div>
            </div>
          </section>

          <section id="group-storage" className="card settings-group">
            <h2>{t('settings.groupStorage')}</h2>
            <div className="setting-row">
              <div className="setting-text">
                <div className="setting-title">{t('settings.dataDir')}</div>
                <div className="setting-desc">{t('settings.dataDirDesc')}</div>
              </div>
              <div className="setting-control">
                <TextInput
                  className="data-dir-input"
                  value={dataDir.draft}
                  placeholder={t('settings.dataDirPlaceholder')}
                  disabled={!eSettings.available}
                  onChange={(e) => dataDir.setDraft(e.currentTarget.value)}
                  onBlur={dataDir.commit}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') dataDir.commit()
                  }}
                  rightSection={
                    <Tooltip label={t('settings.dataDirBrowse')} withArrow>
                      <ActionIcon
                        variant="subtle"
                        color="gray"
                        className="input-affix"
                        disabled={!eSettings.available}
                        onClick={pickDataDir}
                        aria-label={t('settings.dataDirBrowse')}
                      >
                        <FolderOpen size={14} />
                      </ActionIcon>
                    </Tooltip>
                  }
                />
              </div>
            </div>
          </section>

          <section id="group-log" className="card settings-group">
            <h2>{t('settings.groupLog')}</h2>
            <div className="setting-row">
              <div className="setting-text">
                <div className="setting-title">{t('settings.logDir')}</div>
                <div className="setting-desc">{t('settings.logDirDesc')}</div>
              </div>
              <div className="setting-control">
                <TextInput
                  className="data-dir-input"
                  value={logDir.draft}
                  placeholder={t('settings.logDirPlaceholder')}
                  disabled={!eSettings.available}
                  onChange={(e) => logDir.setDraft(e.currentTarget.value)}
                  onBlur={logDir.commit}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') logDir.commit()
                  }}
                  rightSection={
                    <Tooltip label={t('settings.logDirBrowse')} withArrow>
                      <ActionIcon
                        variant="subtle"
                        color="gray"
                        className="input-affix"
                        disabled={!eSettings.available}
                        onClick={pickLogDir}
                        aria-label={t('settings.logDirBrowse')}
                      >
                        <FolderOpen size={14} />
                      </ActionIcon>
                    </Tooltip>
                  }
                />
              </div>
            </div>

            <div className="setting-row">
              <div className="setting-text">
                <div className="setting-title">{t('settings.logRetentionDays')}</div>
                <div className="setting-desc">{t('settings.logRetentionDaysDesc')}</div>
              </div>
              <div className="setting-control">
                <TextInput
                  type="number"
                  className="data-dir-input"
                  value={logRetention.draft}
                  disabled={!eSettings.available}
                  onChange={(e) => logRetention.setDraft(e.currentTarget.value)}
                  onBlur={logRetention.commit}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') logRetention.commit()
                  }}
                />
              </div>
            </div>

            <div className="setting-row">
              <div className="setting-text">
                <div className="setting-title">{t('settings.logClear')}</div>
                <div className="setting-desc">
                  {/* 统计顶替原描述文字：显示当前日志文件数 + 总占用空间（2026-08-12） */}
                  {logStats
                    ? t('settings.logStats').replace('{n}', String(logStats.fileCount)).replace('{size}', formatBytes(logStats.totalBytes))
                    : t('settings.logClearDesc')}
                </div>
              </div>
              <div className="setting-control">
                {/* 清理日志：中等危险，空心红（outline）——可见但不至于像核爆那么重 */}
                <Button variant="outline" size="sm" color="red" disabled={!eSettings.available} onClick={clearLogs}>
                  {t('settings.logClearBtn')}
                </Button>
              </div>
            </div>
          </section>

          <section id="group-danger" className="card settings-group">
            <h2>{t('settings.groupDanger')}</h2>
            <div className="setting-row">
              <div className="setting-text">
                <div className="setting-title">{t('settings.resetAll')}</div>
                <div className="setting-desc">{t('settings.resetAllDesc')}</div>
              </div>
              <div className="setting-control">
                {/* 重新开始：最高危险（清空一切），实心红（filled）——全站最重的一颗钮 */}
                <Button variant="filled" size="sm" color="red" onClick={confirmResetAll}>
                  {t('settings.resetAllBtn')}
                </Button>
              </div>
            </div>
          </section>
        </div>
      </div>
    </div>
  )
}
