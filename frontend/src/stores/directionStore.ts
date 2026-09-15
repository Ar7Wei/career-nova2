import { create } from 'zustand'
import api from '@/lib/api'
import { useApplyStore } from '@/stores/applyStore'
import type { Direction } from '@/types/apply'

/**
 * 投递方向 store（apply.md §11.2）：读/改方向 + 岗位处置。
 *
 * 方向 = 派生事实（后端存 user_facts）。role（岗位标签，写简历/展示用，不参与搜索）与
 * keywords（二维查询组，组内 AND、组间 OR，只管搜索）是两份独立数据，互不派生。
 *
 * 2026-08-20 定稿：方向由**聊天 agent 自动生成**（refine_direction/commit_direction 工具，
 * 走后端 service 真源，不经 HTTP refine）。
 * 2026-08-25 方向口头化：commit 只写方向（返回 pending_disposal_count），岗位处置拆到
 * disposeUnprocessed——前端按 pending_disposal_count 就地弹「留/删」确认，选「清」才调。
 */

interface DirectionState {
  /** 当前已存方向（无则 null）。 */
  direction: Direction | null
  /** 是否正在拍板（commit）。 */
  committing: boolean
  /** 可选城市库（25 热门城市，下拉数据源）。 */
  cities: string[]
  /** 城市库是否正在拉取（2026-09-13）：面板挂载时拉取未回，下拉此刻 data=[] 会误显示成
   *  「没有 25 城可选」——置灰 + 占位提示，数据到位后再放开。 */
  citiesLoading: boolean

  load: () => Promise<void>
  loadCities: () => Promise<void>
  /** 拍板方向：POST /direction/commit，返回待处置的未处理岗位数（>0 调用方就地弹确认）。
   *  cities 与 keywords 平行等长、逐组必填（ADR 0019）。 */
  commit: (role: string, keywords: string[][], cities: string[]) => Promise<number>
  /** 清空未处理岗位：POST /direction/dispose-unprocessed，返回删除数。 */
  disposeUnprocessed: () => Promise<number>
}

export const useDirectionStore = create<DirectionState>((set) => ({
  direction: null,
  committing: false,
  cities: [],
  citiesLoading: false,

  load: async () => {
    try {
      const { data } = await api.get<{ role: string; keywords: string[][]; cities: string[] }>('/v1/direction')
      set({
        direction:
          data.keywords.length > 0
            ? { role: data.role, keywords: data.keywords, cities: data.cities ?? [] }
            : null,
      })
    } catch {
      // 挂载恢复失败：静默留空（方向为空时面板引导用户去定，不阻塞）
    }
  },

  loadCities: async () => {
    set({ citiesLoading: true })
    try {
      const { data } = await api.get<{ cities: string[] }>('/v1/direction/cities')
      set({ cities: data.cities ?? [] })
    } catch {
      // 拉城市库失败：静默留空（下拉空，用户仍可提交已有方向；后端会挡非法城市）
    } finally {
      set({ citiesLoading: false })
    }
  },

  commit: async (role, keywords, cities) => {
    set({ committing: true })
    try {
      const { data } = await api.post<{ role: string; keywords: string[][]; cities: string[]; pending_disposal_count: number }>(
        '/v1/direction/commit',
        { role, keywords, cities },
      )
      const direction: Direction = { role: data.role, keywords: data.keywords, cities: data.cities ?? [] }
      set({ direction })
      // 改方向 = 触发点②（apply.md §8①）：游标已重置、新方向立刻有货，值得踢一次。
      void useApplyStore.getState().kick()
      return data.pending_disposal_count
    } finally {
      set({ committing: false })
    }
  },

  disposeUnprocessed: async () => {
    const { data } = await api.post<{ deleted_jobs: number }>('/v1/direction/dispose-unprocessed')
    return data.deleted_jobs
  },
}))
