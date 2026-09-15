/**
 * 后端用户偏好类型，与后端 app/schemas/settings.py 的 AppSettings 对齐（SQLite 真相源）。
 * 边界：端口/关闭行为/数据目录归 Electron 本地不在这（data_dir 见 desktop.d.ts）。
 * llm_api_key：GET 只回掩码（只写不回读）；PATCH 传明文给后端存储。
 */
export interface AppSettings {
  language: 'zh' | 'en'
  llm_model: string
  llm_base_url: string
  llm_api_key: string
  /** 「开启求职之路」总开关（apply.md §11.1）：后台爬的门控。 */
  apply_mode: boolean
  /** 每平台一轮抓几个新岗（apply.md §8②，取代旧 crawl_batch_size）。 */
  crawl_quota: { liepin: number; job51: number }
  /** 每轮最多扫几页（全局一个数，用户手动调）。 */
  crawl_cap: number
  /** 触发值 = 保留的未处理底仓（≤ 此值就补下一批，默认 5）。 */
  crawl_threshold: number
}
