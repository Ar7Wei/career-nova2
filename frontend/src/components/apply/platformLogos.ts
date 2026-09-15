import liepin from '@/assets/platforms/liepin.png'
import job51 from '@/assets/platforms/job51.png'
import type { JobSource } from '@/types/apply'

/** 招聘平台 LOGO（登录入口 + 平台筛选共用）。
 *  来源：各平台站点 favicon（猎聘 img.liepin.com/favicon.ico、前程无忧 51job.com），
 *  本地化到 assets 避免运行时外链。 */
export const PLATFORM_LOGOS: Record<JobSource, string> = {
  liepin,
  job51,
}
