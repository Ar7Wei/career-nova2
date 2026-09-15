/**
 * 常见 LLM 提供方预设：选预设自动填 base_url。
 *
 * 模型名的唯一来源是各家 OpenAI 兼容端点的 `GET /models`（探测按钮拉取）。
 * 预设不内置任何模型名——本地不存"出厂默认模型"，避免没探测时给用户
 * 显示不相干的模型（2026-08-12）。
 */
export interface LlmPreset {
  id: string
  /** 提供方名（i18n key，settings.llmProviderName.<id>） */
  labelKey: string
  baseUrl: string
}

export const LLM_PRESETS: LlmPreset[] = [
  {
    id: 'openai',
    labelKey: 'settings.llmProviderName.openai',
    baseUrl: 'https://api.openai.com/v1',
  },
  {
    id: 'deepseek',
    labelKey: 'settings.llmProviderName.deepseek',
    baseUrl: 'https://api.deepseek.com',
  },
  {
    id: 'moonshot',
    labelKey: 'settings.llmProviderName.moonshot',
    baseUrl: 'https://api.moonshot.cn/v1',
  },
  {
    id: 'zhipu',
    labelKey: 'settings.llmProviderName.zhipu',
    baseUrl: 'https://open.bigmodel.cn/api/paas/v4',
  },
  {
    id: 'qwen',
    labelKey: 'settings.llmProviderName.qwen',
    baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1',
  },
]

/** 把当前 base_url 匹配到某个预设；匹配不到（自定义）返回 undefined。 */
export function matchPreset(baseUrl: string): LlmPreset | undefined {
  const url = baseUrl.trim().replace(/\/+$/, '')
  return LLM_PRESETS.find((p) => p.baseUrl.replace(/\/+$/, '') === url)
}
