/**
 * Electron preload 桥接的类型声明。浏览器开发时 window.desktop 为 undefined，
 * TitleBar 窗口控制与 Electron 本地设置自动退化为占位/禁用。
 */
export {}

/** Electron 本地设置（端口/关闭行为/数据目录/日志），与 electron/settings-store.js 对齐。 */
export interface ElectronSettings {
  backend_port: number
  close_action: 'tray' | 'quit'
  data_dir: string
  /** 上次实际用的数据目录（数据迁移的"从哪迁到哪"参照）。 */
  current_data_dir: string
  log_dir: string
  log_retention_days: number
  /** 上次实际写日志的目录（日志迁移的"从哪迁到哪"参照）。 */
  current_log_dir: string
}

declare global {
  interface Window {
    desktop?: {
      window: (action: 'minimize' | 'maximize' | 'close') => void
      settings: {
        get: () => Promise<ElectronSettings>
        set: (patch: Partial<ElectronSettings>) => Promise<ElectronSettings>
      }
      /** 弹原生「选择文件夹」对话框（存储目录用）。取消返回 null。 */
      pickDirectory: () => Promise<string | null>
      /** 日志维护：立即清理日志目录下的按日期日志文件。 */
      logs: {
        clear: () => Promise<{ removed: number }>
        /** 日志目录下按日期日志文件的数量与总字节数（清理按钮旁展示用）。 */
        stats: () => Promise<{ fileCount: number; totalBytes: number }>
      }
      /** 下载简历：PDF（printToPDF）/ Markdown / HTML。浏览器下不可用。 */
      exportResume: (payload: {
        format: 'pdf' | 'md' | 'html'
        /** 排版层 HTML（已收敛一页 A4；pdf/html 用）。 */
        html: string
        /** 内容层 Markdown（md 用）。 */
        markdown: string
        defaultName: string
      }) => Promise<{ saved: boolean; canceled?: boolean; filePath?: string }>
      /** 打开岗位投递详情窗口（独立窗口 + 每平台独立登录态）。浏览器下不可用。 */
      openJobDetail: (job: {
        source: string
        url: string
        title?: string
        jobId?: number
      }) => Promise<{ ok: boolean }>
      /** 标记完投递结果后真正关闭岗位窗口（跳过 close 拦截）。 */
      closeJobDetail: (jobId: number) => Promise<{ ok: boolean }>
      /** 订阅岗位详情窗口关闭事件（用户点 X → 主进程拦下 → 发事件让前端弹标记框）。返回取消订阅函数。 */
      onDetailClosed: (cb: (payload: { jobId: number | null; source: string; title: string }) => void) => () => void
      /** 打开某平台登录页（登录入口）。已登录则不再弹窗，返回 loggedIn:true。 */
      openLogin: (source: string) => Promise<{ ok: boolean; loggedIn?: boolean }>
      /** 查某平台登录态（导航探测：访问需登录页，看是否被甩到登录页）。 */
      getLoginStatus: (source: string) => Promise<{ loggedIn: boolean }>
      /** 订阅登录变更事件（登录窗口跳离登录页触发）。返回取消订阅函数。 */
      onLoginChanged: (cb: (source: string) => void) => () => void
      /** 事件驱动抓岗位（kickCrawl）：后端猎聘一轮 + 前程无忧读 DOM 一轮。浏览器下不可用。 */
      kickCrawl: () => Promise<{
        liepin: { source: string; kind: string; group_idx: number; page: number; added: number; updated: number; exhausted: boolean } | null
        job51: { source: string; kind: string; group_idx: number; page: number; added: number; updated: number; exhausted: boolean } | null
        dataEpoch: number
        /** 这一脚整体的门控结果（后端 gate_reason）：crawled / apply_mode_off / no_direction / backlog_full / throttled / exhausted。 */
        gateReason: 'crawled' | 'apply_mode_off' | 'no_direction' | 'backlog_full' | 'throttled' | 'exhausted'
        /** 本轮抓取轮次 id（一次 kick 一个、两平台共用）：投递页分析据此取「本轮那批」。 */
        roundId: number | null
      }>
    }
  }
}
