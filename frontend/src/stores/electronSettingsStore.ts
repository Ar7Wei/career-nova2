import { create } from 'zustand'
import type { ElectronSettings } from '@/types/desktop'

/**
 * Electron 本地设置 store（Electron 线真相源）：backend_port、close_action、data_dir。
 * 经 IPC 读写 Electron 的 userData/settings.json。浏览器开发时 window.desktop
 * 不存在，退化为默认值且写操作无效（界面对应控件应禁用）。
 */
interface ElectronSettingsState extends ElectronSettings {
  available: boolean
  loaded: boolean
  load: () => Promise<void>
  update: (patch: Partial<ElectronSettings>) => Promise<void>
}

export const useElectronSettingsStore = create<ElectronSettingsState>((set) => ({
  backend_port: 8765,
  close_action: 'tray',
  data_dir: '',
  current_data_dir: '',
  log_dir: '',
  log_retention_days: 30,
  current_log_dir: '',
  available: typeof window !== 'undefined' && !!window.desktop,
  loaded: false,

  load: async () => {
    if (!window.desktop) {
      set({ loaded: true, available: false })
      return
    }
    const data = await window.desktop.settings.get()
    set({ ...data, loaded: true, available: true })
  },

  update: async (patch) => {
    if (!window.desktop) return
    const data = await window.desktop.settings.set(patch)
    set(data)
  },
}))
