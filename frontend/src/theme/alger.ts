import { ActionIcon, Button, createTheme, Input, Notification, rem, type MantineColorsTuple } from '@mantine/core'

/**
 * Alger 主题（借鉴 AlgerMusicPlayer，单主题 light）。
 * 把 frontend/src/index.css 的 @theme token 映射到 Mantine theme——
 * 视觉：绿主色 #22c55e + light 灰阶、玻璃拟态弹层、柔和圆角阴影。
 *
 * 注意：本文件是 Mantine 组件的主题；index.css 里自绘部分（布局壳/聊天框）
 * 仍用自己的 CSS 变量（--color-*），两轨并存，值同源。
 */

// Mantine 需要每个色板 10 档（0-9），用 Alger 主色按明暗生成。
// 0-9：从浅到深。以 #22c55e 为中心，hover=#16a34a 在 7，active=#15803d 在 8。
const green: MantineColorsTuple = [
  '#e9fbf0', // 0 极浅（primary-soft 近）
  '#c9f4dc',
  '#a3eac3',
  '#74dda3',
  '#4bd087',
  '#22c55e', // 5 = 主色
  '#1db357',
  '#16a34a', // 7 = hover
  '#15803d', // 8 = active
  '#0e5c2e', // 9
]

// light 灰阶（Alger）：canvas / panel / soft / border / 文字
const gray: MantineColorsTuple = [
  '#f8f9fa', // 0 = canvas
  '#eef1f3', // 1 = border-light
  '#e9ecef', // 2 = soft
  '#dee2e6', // 3 = border
  '#ced4da',
  '#adb5bd', // 5 = placeholder
  '#868e96',
  '#6c757d', // 7 = muted
  '#495057',
  '#212529', // 9 = ink
]

/** 状态色（12~18% 透明底 + 实色文字，Alger 规范） */
const red: MantineColorsTuple = ['#fef2f2', '#fee2e2', '#fecaca', '#fca5a5', '#f87171', '#ef4444', '#dc2626', '#b91c1c', '#991b1b', '#7f1d1d']
const yellow: MantineColorsTuple = ['#fffbeb', '#fef3c7', '#fde68a', '#fcd34d', '#fbbf24', '#f59e0b', '#d97706', '#b45309', '#92400e', '#78350f']
const blue: MantineColorsTuple = ['#eff6ff', '#dbeafe', '#bfdbfe', '#93c5fd', '#60a5fa', '#3b82f6', '#2563eb', '#1d4ed8', '#1e40af', '#1e3a8a']

export const algerTheme = createTheme({
  primaryColor: 'green',
  colors: {
    green,
    gray,
    red,
    yellow,
    blue,
  },

  // 圆角（柔和）
  defaultRadius: 'md', // 10px（卡片容器）
  radius: {
    xs: rem(6),
    sm: rem(6),
    md: rem(10),
    lg: rem(14),
    xl: rem(20),
  },

  // 字体（与 index.css --font-sans 同源：改字体必须两处一起改）
  fontFamily: "system-ui, -apple-system, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif",

  // 柔和阴影（与 index.css --shadow-card/pop 对齐）
  shadows: {
    xs: '0 1px 2px rgba(16, 24, 40, 0.05)',
    sm: '0 1px 2px rgba(16, 24, 40, 0.05), 0 1px 3px rgba(16, 24, 40, 0.08)',
    md: '0 4px 12px rgba(16, 24, 40, 0.1)',
    lg: '0 8px 30px rgba(16, 24, 40, 0.12)',
    xl: '0 12px 40px rgba(16, 24, 40, 0.16)',
  },

  // 自定义 token（自绘 CSS 经 CSS 变量读取；Mantine 组件不用但可经 useMantineTheme().other 取）
  other: {
    // ⚠️ 与 styles/base.css 的 --glass-bg 同源：改这个值必须两处一起改。
    glassBg: 'rgba(255, 255, 255, 0.85)',
    glassBorder: 'rgba(255, 255, 255, 0.6)',
    easeOut: 'cubic-bezier(0.16, 1, 0.3, 1)',
  },

  // 全局：禁暗色（单主题 light）——v9 经 MantineProvider defaultColorScheme 设置（main.tsx）。

  // 组件注入：恢复按钮原视觉（2026-08-08 双轨收敛后修正回归）。
  // subtle 变体 = 原 .btn-ghost 幽灵钮：透明底、**无边框**（2026-08-24 修：幽灵不该有灰边，
  // 否则和 default 白底钮傻傻分不清），深灰文字。
  // Mantine 默认 subtle 文字色是 primary 的 light 色（绿），需覆盖回 --color-ink。
  // 字号（2026-08-24 全局降一号）：30px 档(sm)=12px、compact-sm=11px（原 13.5/12 偏大）。
  // 与 --font-size-sm(12)/--font-size-xs(11) 对齐。
  // 圆角（§10.1）：可点击件统一 6px（--radius-sm），卡片容器才用 10/14px。
  // hover 反馈走 Mantine 默认 --button-hover（内联 style 不支持 &hover 伪类，不写）。
  //
  // 高度收敛（2026-08-24）：`sm` 档从 Mantine 默认 36px 降到 30px（原定 26 偏矮，加高到 30，
  // 恰等于原生 xs 档高度）。compact-sm(26px)/微型 20px 不受影响。
  // 组件高度都走 varsResolver 的 getSize(size,'xxx-height') → 落到 --xxx-height（内联样式），
  // 在主题 vars（函数，经 mergeVars 合并成内联、盖过组件类里默认 36px）里覆盖即可全站生效，
  // 调用点（size="sm"/默认）零改动。Input 是 TextInput/Textarea/Select/TagsInput 基类，一处覆盖全部。
  // 20px 微型图标钮（.btn-icon-sm，非 Mantine 尺寸档）不受影响，保持更紧凑一档。
  components: {
    Button: Button.extend({
      vars: (_theme, { size }) => ({
        root: { '--button-height': size === undefined || size === 'sm' ? '30px' : undefined },
      }),
      styles: (_theme, { variant, size }) => ({
        root: {
          fontSize: size === 'compact-sm' ? 11 : 12,
          borderRadius: 'var(--radius-sm)',
          ...(variant === 'subtle'
            ? {
                background: 'transparent',
                border: '1px solid transparent',
                color: 'var(--color-ink)',
              }
            : {}),
        },
      }),
    }),
    Input: Input.extend({
      vars: (_theme, { size }) => ({
        // sm 收 30（= 原生 xs 档高度）；xs 原生即 30，与 sm 天然同高，无需再动。
        wrapper: { '--input-height': size === 'sm' ? '30px' : undefined },
      }),
      styles: {
        // 底色收敛白底：Mantine default 输入框是 gray-0 浅灰，叠在玻璃浮窗上发灰显脏；
        // 面板内其他可点件（触发钮 / 日期 pill）都是 --color-panel 白。Input 是
        // TextInput/TagsInput/NumberInput/Select 共同基类，一处覆盖全部输入控件。
        input: { background: 'var(--color-panel)' },
      },
    }),
    ActionIcon: ActionIcon.extend({
      vars: (_theme, { size }) => ({
        root: { '--ai-size': size === undefined || size === 'md' ? '30px' : undefined },
      }),
    }),
    // Toast（@mantine/notifications 的 Notification 基类）：玻璃拟态弹层。
    // 出厂视觉是白底实心卡（Notification.module.css 的 background:white + --mantine-shadow-lg），
    // 与弹窗/悬浮卡不像一家；这里**只覆背景/边框/投影**，换成 .glass 三件套（全局弹层规范）。
    // 形状（圆角走 --notification-radius ← theme.defaultRadius=md）与内边距保持 Mantine 默认，
    // 更稳（色条 ::before 用 padding-inline-start:22px 避让、closeButton 右缘对齐都依赖它）。
    // 不用 .glass 类（它在 styles/ 里，theme 注入不该反向依赖应用 CSS）；气泡 Tooltip 未注入，
    // 仍吃 Mantine 默认深底——toast 却该跟「白玻璃弹层」（modal/悬浮卡）一家，故此处特化。
    Notification: Notification.extend({
      styles: {
        root: {
          background: 'var(--glass-bg)',
          backdropFilter: 'var(--glass-blur)',
          WebkitBackdropFilter: 'var(--glass-blur)',
          border: '1px solid var(--glass-border)',
          boxShadow: 'var(--shadow-pop)',
        },
      },
    }),
  },
})
