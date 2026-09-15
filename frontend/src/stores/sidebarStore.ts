import { create } from 'zustand'

/**
 * 侧栏折叠态（单一真相源）。
 * 折叠按钮在 Sidebar 里，但折叠要带动 .app-body 的列宽收窄（App 里的主网格），
 * 所以状态放 store 而非 Sidebar 局部 useState——Sidebar 只读写 store。
 * 会话内内存态，不持久化。
 */
interface SidebarState {
  collapsed: boolean
  toggle: () => void
}

export const useSidebarStore = create<SidebarState>((set) => ({
  collapsed: false,
  toggle: () => set((s) => ({ collapsed: !s.collapsed })),
}))
