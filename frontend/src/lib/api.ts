import axios from 'axios'
import { useHealthStore } from '@/stores/healthStore'

/**
 * 集中 API 层。
 *
 * 注意：/dev/components 的接口调用器不走这里——它始终打真接口验真。
 *
 * 连接状态联动（healthStore）：业务流量驱动连接状态——请求成功 = 后端在跑（markOnline）；
 * 网络层失败 = 连不上（markOffline，触发断线探活轮询）。不设常设心跳。
 *
 * 统一错误契约（2026-08-08，后端 app/core/errors.py 对齐）：
 * 后端 AppError 经全局 handler 转 {code, message, detail, retryable, action}，
 * 拦截器读一次转成 AppApiError，全站生效——业务 catch 从"5 行解析"变 2 行：
 *   catch (e) { if (e instanceof AppApiError) … }
 * 非 AppError 的失败（网络断/超时/后端兜底 500）→ network_error AppApiError。
 */
// baseURL 按运行形态分流：
//  - Electron 态（dev 打包通用，preload 注入 window.desktop.backendOrigin）：用绝对地址。
//    打包态前端是 file:// 加载，相对 /api 会解析成 file:///api → 永远到不了后端（这就是
//    「后端在跑、前端却连不上」的根因）。Electron dev 态用绝对地址也OK（CORS 后端已放行）。
//  - 纯浏览器 dev（无 desktop 桥）：用相对 /api，吃 vite proxy 转发到后端。
const electronOrigin = (window as { desktop?: { backendOrigin?: string } }).desktop?.backendOrigin
const api = axios.create({ baseURL: electronOrigin ? `${electronOrigin}/api` : '/api' })

/** 后端 AppError 响应体形态（app/core/errors.py register_error_handlers）。 */
interface AppErrorBody {
  code: string
  message: string
  detail?: string
  retryable?: boolean
  action?: string | null
}

/** 统一错误类型：后端 AppError 或网络/兜底错误都归一成它。 */
export class AppApiError extends Error {
  /** 机器可读错误码（backend code，或 network_error）。 */
  code: string
  /** HTTP 状态码（网络错误为 0）。 */
  status: number
  /** 真因（可含技术细节，展示/记日志用）。 */
  detail: string
  /** 能否重试（驱动"重试"按钮）。 */
  retryable: boolean
  /** 恢复动作：open_settings / retry / reupload / confirm / cancel … */
  action: string | null

  constructor(message: string, code: string, status: number, detail = '', retryable = false, action: string | null = null) {
    super(message)
    this.code = code
    this.status = status
    this.detail = detail
    this.retryable = retryable
    this.action = action
  }
}

/** 网络层错误（请求根本没到后端 / 断了）——统一收敛成可读信息。 */
const NETWORK_MESSAGE = '连不上本地服务，检查后端是否在运行。'

api.interceptors.response.use(
  (response) => {
    // 业务请求打通 = 后端在跑 → 在线（并停掉断线探活）。
    useHealthStore.getState().markOnline()
    return response
  },
  (error) => {
    const status: number = error?.response?.status ?? 0
    const body: AppErrorBody | undefined = error?.response?.data

    // 网络层失败（请求没到后端 / 连接断）：标记离线并启动断线探活轮询。
    // 注意放在 AppError 分支之前——AppError 意味着后端回了响应（在线），只有
    // status===0 才是真·连不上。
    if (status === 0 && !axios.isCancel(error) && error?.code !== 'ERR_CANCELED') {
      useHealthStore.getState().markOffline()
    } else {
      // 后端给了响应（含 4xx/5xx）：连接是通的 → 在线。
      useHealthStore.getState().markOnline()
    }

    // 后端 AppError：读契约 body
    if (body?.code) {
      throw new AppApiError(
        body.message || '出错了',
        body.code,
        status,
        body.detail ?? '',
        body.retryable ?? false,
        body.action ?? null,
      )
    }

    // 请求被主动取消（AbortController）——不当作错误抛出
    if (axios.isCancel(error) || error?.code === 'ERR_CANCELED') {
      throw error
    }

    // 后端兜底错误（非 AppError 形态，如 500 兜底/校验 422）：给通用文案
    const detail = body?.detail || error?.message || ''
    const isNetwork = status === 0
    throw new AppApiError(
      isNetwork ? NETWORK_MESSAGE : `请求失败（${status}），请再试一次。`,
      isNetwork ? 'network_error' : 'http_error',
      status,
      detail,
      isNetwork || status >= 500, // 网络断 / 服务端错可重试
      null,
    )
  },
)

export default api
