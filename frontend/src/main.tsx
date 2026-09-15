import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { MantineProvider } from '@mantine/core'
import { ModalsProvider } from '@mantine/modals'
import { Notifications } from '@mantine/notifications'
import { DatesProvider } from '@mantine/dates'
import 'dayjs/locale/zh-cn'
import '@mantine/core/styles.css'
import '@mantine/notifications/styles.css'
import '@mantine/dates/styles.css'
import '@mantine/charts/styles.css'
import './index.css'
import App from './App.tsx'
import { algerTheme } from './theme/alger'
import { useI18n } from './lib/i18n'

/** 日期控件本地化：i18n 语言 → dayjs locale（Mantine dates 内部用 dayjs 渲染月份/星期）。 */
function LocaleProvider({ children }: { children: React.ReactNode }) {
  const locale = useI18n((s) => s.locale)
  const dayjsLocale = locale === 'zh' ? 'zh-cn' : 'en'
  return <DatesProvider settings={{ locale: dayjsLocale }}>{children}</DatesProvider>
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <MantineProvider theme={algerTheme} defaultColorScheme="light">
      <LocaleProvider>
        <Notifications position="top-right" notificationMaxHeight="none" />
        <ModalsProvider>
          <App />
        </ModalsProvider>
      </LocaleProvider>
    </MantineProvider>
  </StrictMode>,
)
