import { create } from 'zustand'
import api from '@/lib/api'
import { useI18n, type Locale } from '@/lib/i18n'
import type { AppSettings } from '@/types/settings'

/** LLM 探测（测试连接 + 拉模型）结果三态。 */
export interface LlmProbeResult {
  /** 连接是否通过（失败则不再拉模型，message 为原因）。 */
  ok: boolean
  /** 是否成功拉到模型列表（连接过但拉不到时为 false，可手输模型）。 */
  models_ok: boolean
  /** 拉到的真实模型列表（models_ok=false 时为空）。 */
  models: string[]
  message: string
}

/**
 * LLM 探测的覆盖参数：只允许探测字段（model/base_url/key），
 * 与后端 LlmProbeRequest 对齐。不继承 Partial<AppSettings>
 * ——language/data_dir 等设置字段不属于探测语义，禁止混进请求体。
 */
export interface LlmConfigOverrides {
  llm_model?: string
  llm_base_url?: string
  llm_api_key?: string
}

/**
 * 后端用户偏好 store（SQLite 真相源）：语言、LLM 模型/base_url/API key、数据目录。
 * 读写真接口 GET/PATCH /settings。语言同步 i18n（驱动 UI 与后端 LLM 输出语言）。
 *
 * 注意边界：端口/关闭行为不在这里（走 electronSettingsStore 的 Electron IPC）。
 * llm_api_key：GET 只回掩码（★★★★★），表单里占位，不覆盖用户已填/已存的 key；
 * PATCH 传明文给后端存储。
 *
 * llm_model：本地不存"出厂默认模型名"（2026-08-12）——模型名只来自探测接口
 * 拉取的真实列表。DEFAULTS 为空；若加载到旧版出厂值 gpt-5-mini（历史遗留），
 * 自动清空并写回后端（见 load）。
 */
interface SettingsState extends AppSettings {
  loaded: boolean
  /** 后端是否已配置 API key（由掩码响应推断：非空=已配置）。只写不回读。 */
  llmKeyConfigured: boolean
  /**
   * 加载设置。seedLocale：可选的后端已知语种——重载前先预置 UI 语言，
   * 避免「首帧按默认 zh 排、响应回来才跳」的闪烁（见 i18n seed）。
   */
  load: (opts?: { seedLocale?: Locale }) => Promise<void>
  update: (patch: Partial<AppSettings>) => Promise<void>
  /** 探测 LLM：先测连接再拉模型列表（可用当前保存设置，也可传覆盖值）。 */
  probeLlm: (overrides?: LlmConfigOverrides) => Promise<LlmProbeResult>
}

/** 旧版出厂默认模型名（历史遗留）：加载到它说明用户从未真正选过模型，清空处理。 */
const LEGACY_DEFAULT_MODEL = 'gpt-5-mini'

const DEFAULTS: AppSettings = {
  language: 'zh',
  llm_model: '',
  llm_base_url: '',
  llm_api_key: '',
  apply_mode: false,
  crawl_quota: { liepin: 40, job51: 20 },
  crawl_cap: 10,
  crawl_threshold: 5,
}

export const useSettingsStore = create<SettingsState>((set) => ({
  ...DEFAULTS,
  llmKeyConfigured: false,
  loaded: false,

  load: async (opts = {}) => {
    // 若调用方已知后端语种（整页重载前存的），先预置 locale——设置请求飞回前的一帧
    // 不该按默认 zh 排（见 i18n seed）。未知则跳过，等响应里 language 再定。
    if (opts.seedLocale) useI18n.getState().seed(opts.seedLocale)
    const { data } = await api.get<AppSettings>('/v1/settings')
    // 历史遗留的旧出厂默认模型名（gpt-5-mini）不算真实选择：本地清空 + 写回后端，
    // 模型名只应来自探测接口拉取的真实列表。
    const llmModel = data.llm_model === LEGACY_DEFAULT_MODEL ? '' : data.llm_model
    if (llmModel !== data.llm_model) {
      void api.patch('/v1/settings', { llm_model: '' })
    }
    set({
      ...data,
      llm_model: llmModel,
      // 不回读明文/掩码：输入框保持空，只靠 llmKeyConfigured 提示"已配置"
      llm_api_key: '',
      llmKeyConfigured: data.llm_api_key !== '',
      loaded: true,
    })
    useI18n.getState().setLocale(data.language)
  },

  update: async (patch) => {
    // 空 key 不发送（保留已存的 key）：只写不回读，用户不填就不动它
    if (patch.llm_api_key === '') {
      delete patch.llm_api_key
    }
    const { data } = await api.patch<AppSettings>('/v1/settings', patch)
    set({
      ...data,
      llm_api_key: '',
      llmKeyConfigured: data.llm_api_key !== '',
    })
    if (data.language) {
      useI18n.getState().setLocale(data.language)
    }
  },

  probeLlm: async (overrides = {}) => {
    const { data } = await api.post<LlmProbeResult>('/v1/settings/llm/probe', overrides)
    return data
  },
}))
