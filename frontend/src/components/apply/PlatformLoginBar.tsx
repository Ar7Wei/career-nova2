import { useEffect, useState } from 'react'
import { Button } from '@mantine/core'
import { useT } from '@/lib/i18n'
import { JOB_SOURCE_LABELS, LOGIN_SOURCES } from '@/lib/applyLabels'
import { PLATFORM_LOGOS } from './platformLogos'

/**
 * 招聘平台登录快捷入口：一排带 LOGO 的登录按钮 + 登录态点。
 * 解决「不登录抓不到、没岗位登不了」的死循环。
 * 覆盖需登录的平台（前程无忧）+ 猎聘（虽免登录抓取，但点开岗位时可能已登录）。
 * 内嵌于方向面板（Pad）内，作为其中一节。
 */
export function PlatformLoginBar() {
  const t = useT()
  const [loginState, setLoginState] = useState<Record<string, boolean>>({})

  // 挂载时拉一次登录态
  useEffect(() => {
    if (!window.desktop?.getLoginStatus) return
    void Promise.all(
      LOGIN_SOURCES.map(async (s) => {
        const r = await window.desktop!.getLoginStatus!(s)
        return [s, r.loggedIn] as const
      }),
    ).then((pairs) => {
      setLoginState(Object.fromEntries(pairs))
    })
  }, [])

  // 订阅登录变更事件：登录窗口跳离登录页（登录成功）时，刷新该平台状态。
  useEffect(() => {
    if (!window.desktop?.onLoginChanged) return
    const off = window.desktop.onLoginChanged((source) => {
      // 登录成功：直接标记已登录（跳离登录页 = 登录态已建立）
      setLoginState((prev) => ({ ...prev, [source]: true }))
    })
    return off
  }, [])

  const openLogin = async (s: string) => {
    if (!window.desktop?.openLogin) return
    const r = await window.desktop.openLogin(s)
    // 已登录（后端探测到）→ 直接标绿，不再弹登录窗；否则等 onLoginChanged 事件刷新。
    if (r?.loggedIn) {
      setLoginState((prev) => ({ ...prev, [s]: true }))
    }
  }

  return (
    <div className="console-module">
      <div className="console-section-head-static">
        <span className="console-section-title">{t('apply.loginLabel')}</span>
      </div>
      <div className="apply-login-chips">
        {LOGIN_SOURCES.map((s) => (
          <Button
            key={s}
            className="apply-login-chip"
            onClick={() => void openLogin(s)}
            title={loginState[s] ? t('apply.loggedIn') : t('apply.notLoggedIn')}
            leftSection={
              <span className="apply-login-chip-lead">
                <span className={`login-state-dot ${loginState[s] ? 'on' : ''}`} />
                <img className="apply-login-logo" src={PLATFORM_LOGOS[s]} alt="" />
              </span>
            }
          >
            {JOB_SOURCE_LABELS[s]}
          </Button>
        ))}
      </div>
    </div>
  )
}
