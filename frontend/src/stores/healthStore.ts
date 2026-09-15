import { create } from 'zustand'
import axios from 'axios'

export type BackendStatus = 'checking' | 'online' | 'offline'

interface HealthState {
  status: BackendStatus
  /** 业务请求成功 → 标记在线并停止探活。 */
  markOnline: () => void
  /** 业务请求网络层失败 → 标记离线并启动探活轮询（直到重新连上）。 */
  markOffline: () => void
  /** 启动探活轮询（幂等）。仅在离线时有意义。 */
  startPolling: () => void
  /** 停止探活轮询。 */
  stopPolling: () => void
}

/** 后端地址：Electron 态用 preload 注入的绝对源（打包 file:// 必须绝对地址）；
 *  纯浏览器 dev 无 desktop 桥，回退 localhost:8765（vite dev 后端固定端口）。 */
const BASE =
  (window as { desktop?: { backendOrigin?: string } }).desktop?.backendOrigin ??
  import.meta.env.VITE_BACKEND_ORIGIN ??
  'http://localhost:8765'
const POLL_INTERVAL_MS = 2500
const PROBE_TIMEOUT_MS = 3000

let pollTimer: ReturnType<typeof setInterval> | null = null

/**
 * 后端连接状态（底部状态条用）——「业务流量驱动 + 断线才探活」。
 *
 * 不做常设轮询：在线时一个心跳都不发，状态完全由真实业务调用的成败反映——
 * 使用中无时不刻在调后端，调得通 = 后端在跑，调不通 = 连不上。
 * 只有进入 offline 后才启动探活轮询（打 /health），一通就切回 online 并停轮询；
 * 直到下次业务调用再失败、再次掉回 offline 才重启轮询。
 *
 * 为什么不再用「有 2xx 应答就 online」：那会在 8765 被旧后端/别的服务占用时
 * 误判已连接（本次启动的后端其实没起来）。现在以真实业务调用成败为准。
 */
export const useHealthStore = create<HealthState>((set, get) => ({
  status: 'checking',

  markOnline: () => {
    get().stopPolling()
    if (get().status !== 'online') set({ status: 'online' })
  },

  markOffline: () => {
    if (get().status !== 'offline') set({ status: 'offline' })
    get().startPolling()
  },

  startPolling: () => {
    if (pollTimer) return // 已在轮询，幂等
    const probe = async () => {
      try {
        await axios.get(`${BASE}/health`, { timeout: PROBE_TIMEOUT_MS })
        get().markOnline() // 探活成功 → 在线并停轮询
      } catch {
        // 仍连不上，保持 offline，等下一轮
      }
    }
    void probe() // 立即探一次，不等首个间隔
    pollTimer = setInterval(probe, POLL_INTERVAL_MS)
  },

  stopPolling: () => {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  },
}))
