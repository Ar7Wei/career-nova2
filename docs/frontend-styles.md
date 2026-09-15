# 前端样式规范

> 本文件是前端视觉规范的**可执行详版**：写前端样式前先读。
> 违反本文件的样式问题，属于「不统一」——review 时按这里打回。
> 技术栈：React 19 + Mantine v9 + 手写 `frontend/src/index.css`（全局 CSS 编排，CSS 变量双轨）。
> 物理结构（2026-08-24 起）：`index.css` 只做 `@import` 编排，实际样式分层在 `frontend/src/styles/`（见 §0）。

## 0. 物理结构与页面骨架（2026-08-24 grill 定稿）

### 0.1 样式分层：地基三层 + 每页一层
`index.css` 只做 `@import` 编排，实际样式在 `frontend/src/styles/`：

| 文件 | 层 | 装什么 |
|---|---|---|
| `base.css` | **地基** | 设计 token(`:root`) + reset/body + 全局滚动条 + 过渡曲线 |
| `primitives.css` | **地基** | 产品级通用件：card/chip/表格/空态/glass/页头/状态点 |
| `layout.css` | **地基** | 布局骨架：app-shell / TitleBar / Sidebar / StatusBar |
| `resume.css` | 页面 | 简历页 + 聊天框 + 抽取卡 + 预览 + facts/suggestion |
| `settings.css` | 页面 | 设置面板 |
| `apply.css` | 页面 | 投递页（岗位聚合） |

**归层规则（语义优先，不按当前引用计数）**：
- 跨页面的"**产品级通用件**"（card/chip/glass/页头/状态点这类任何页面都该有的词汇）→ 进地基，**位置稳定**，不随引用变化来回搬。
- 绑定具体页面结构的类（`.resume-left`/`.apply-job-row`/`.setting-row`）→ 归各自页面文件，哪怕将来被引用也不轻易上提。
- 一个类被**第二个页面真正复用**时，才考虑抽成原语上提 primitives。
- **不许页面随便开新 CSS 文件**——新页面在 `styles/` 加一个 `<page>.css` 并在 `index.css` 编排里登记，不散落到组件目录。
- 待办：`.chat-panel` 等聊天框语义上是产品级通用件（Chatter 页即聊天），将来上提 primitives。

### 0.2 两种页面骨架（不强行统一布局）
布局差异是**合法的两种骨架**，不是"某页乱来"。

| 骨架 | 特征 | 用在 |
|---|---|---|
| **A 工具台 (workbench)** | 加工**一个**对象：左对象右助手，无页头分栏面板，面板自滚 | 简历 |
| **B 文档页 (document)** | 浏览/管理**一串**条目：22px 页头 h1+副标题，单列文档流，外层滚动 | 设置、投递 |

**判定**：加工一个对象 → A；扫一串条目 → B。两骨架的**零件层**（hover/边框/圆角/按钮变体/浮层）必须共用同一套 token 与原语，只是骨架不同。

## 1. 分层铁律（谁管什么）

| 类别 | 归属 | 例子 |
|---|---|---|
| **交互组件** | **一律 Mantine** | Modal / 表单输入 / 下拉 / Tree / toast / **按钮** |
| **布局原语** | 手写 `styles/primitives.css`（token 在 `styles/base.css`） | card / chip / 表格 / 空态 / 聊天框 / 悬浮卡 |
| **颜色** | CSS 变量（`--color-*`） | 见 §3 |
| **弹层视觉** | `.glass` 原语 | 玻璃拟态弹层 |

**交互组件不许手写**：原生 `<select>`/`<input>`/`<textarea>` 一律用 Mantine；**`<button>` 一律用 `<Button>`，无例外**（2026-09-11 全量迁移后，仓库内原生 `<button>` 已清零）。
唯一例外：隐藏文件选择器 `<input type="file" style={{display:'none'}}>`（无 Mantine 对等物）。

## 2. 按钮铁律（2026-08 双轨收敛后）

**按钮默认一律 `<Button>`（Mantine）**，禁手写 `.btn`/`.chat-icon-btn` 类（已删，不得复活）。

> **✅ 全量迁移（2026-09-11）**：此前「结构化图标钮/自定义形态可手写」的豁免**已推翻**——仓库内原生 `<button>` 清零，**全部走 Mantine `<Button>`**（唯一例外：隐藏文件选择器）。
> 原先被当作「有意例外」的那批（`ZoomControl` 的 `.zoom-btn`、`TitleBar` 的 `.tb-btn`、`SuggestionCard`/`SuggestionBasket` 的 `.suggestion-toggle`/`.suggestion-retract`、`ApplyPage` 的 `.apply-overlay-btn`、`SettingsPage` 的 `.settings-nav-item` 等）现在**都是 Mantine `<Button>` + 覆盖类**：类还在，但只保留 Mantine 表达不了的部分（绝对定位 / `-webkit-app-region` 拖拽豁免 / 固定槽宽 / 特殊圆角形态 / hover 覆盖），背景、边框、内边距、字号交回主题层。
> **迁移三坑（新增按钮类前必读）**：
> 1. **`--button-height` 是 Mantine 注入的内联 style**，主题层 `vars` 改不动它——CSS 里覆盖尺寸必须 `!important`（如 `.tb-btn { --button-height: 100% !important }`）。
> 2. **Button 的 label 已是 `display:flex`**——别用 `display:contents` 想把 label/section 摊平成一行（右段会被挤到次行）。要整行撑满、右段带内容，把内容塞 `leftSection`，让 label 自然撑开（`.console-section-head` 即此法）。
> 3. **`className` 落在 wrapper 上**，要作用到内部 `<input>` 必须 `classNames={{ input: ... }}`（见 `.zoom-input` / `.apply-mark-note`）。

| 场景 | 写法 |
|---|---|
| 主操作 | `<Button>`（默认 filled，绿色底） |
| 次操作 | `<Button variant="subtle">` |
| 小号 | `size="compact-sm"` |
| 带图标 | `<Button leftSection={<Icon size={14}/>}>`（lucide） |
| 图标-only | `<Button variant="subtle" size="compact-sm" aria-label="…">`（仅图标，用 aria-label 不写 title） |

**尺寸取舍（2026-08-24 高度收敛）**：`sm` 档从 36px 降到 30px（主题层 `alger.ts` 覆盖 `--button-height`/`--input-height`/`--ai-size` 变量，全站生效；原定 26 偏矮加高到 30，恰等于原生 xs 档）。**与输入框同排的按钮用 `size="sm"`（30px）**，跟输入框（默认 sm=30px）齐平——如设置页端口/数据目录行的「保存/浏览」、探测按钮。`compact-sm` 为 26px（水平更紧凑 padding-x 8px vs 18px、字号 11px vs 12px），用于卡片内动作。判断标准：按钮旁边有没有输入框，有就 `sm` 齐平；独立卡片内紧凑动作 `compact-sm`。

**三级高度体系（2026-08-24 收敛）**：**表单控件 & 工具条入口 = 30px**（Mantine `sm` 档，主题层从 36 收敛到 30）；**紧凑钮 = `compact-sm`(26px)**（卡片内动作，水平更紧）；**微型图标钮 = 20px**（`.btn-icon-sm`，仅纯图标无文字）。统一原则：凡带文字的按钮/入口一律 30px，卡片内紧凑动作 26px，仅图标-only 的低调辅助钮 20px。

按钮变体映射已统一：原来 `.btn-primary`→默认 filled、`.btn-ghost`→`subtle`、`.btn-sm`→`compact-sm`。

## 3. 颜色铁律

- **一律 `var(--color-*)`**，禁十六进制、禁裸 rgba 值。
- 状态色用 token：`--color-error` / `--color-warning` / `--color-success` / `--color-info`。
- 主色半透明底用 `--color-primary-soft`（不要写 `rgba(34,197,94,0.1)` 这种近似值）。
- 例外：Markdown 代码块 `pre` 的深色底 `#1e1e1e`（内容排版，非状态色）——可保留，但新增一律走变量。

## 4. 玻璃拟态

- 弹层/提示/悬浮卡的玻璃效果**一律 `.glass` 原语**（已封装 `--glass-bg` + blur + `--glass-border` + `--shadow-pop`）。
- 禁手写 `backdrop-filter: blur(16px) saturate(1.8)` + border 组合（除非有特化几何——如 suggestion-tab 右缘无边框，允许手写但**玻璃三件套（bg/blur/border）仍用变量**）。

## 5. 圆角

- 用 `--radius` / `--radius-sm` / `--radius-lg` token，禁裸 rem/px。
- Mantine 组件圆角由 `algerTheme.radius` 接管，不需要手动设。

## 5.5 字体（族/字号/字重/行高）

**字体族单一真相源**：`--font-sans` / `--font-mono`（`styles/base.css` `:root`）与 `algerTheme.fontFamily` **同源**——改字体必须两处一起改（CSS 变量与 Mantine theme 双轨，无法单一源码，靠注释+约定保证同步）。

**字号 6 档**（2026-08-23 起；真实用途演进：4 档 → §9.1 加 `title` → 加 `md`）：
| token | 值 | 用途 |
|---|---|---|
| `--font-size-xs` | 11px | 最小标签/徽标/计数 |
| `--font-size-sm` | 12px | 辅助/提示/次要文字 |
| `--font-size-lg` | 13.5px | 正文/控件/标题类（消 0.5px 步进） |
| `--font-size-md` | 14px | 导航专用（2026-08-23 加，介于 title 与 lg 之间） |
| `--font-size-title` | 15px | 面板标题档（§9.1） |
| `--font-size-heading` | 22px | 卡片 h2/页头 h1 |

**字重**：`--font-weight-normal(400)` / `--font-weight-medium(500)` / `--font-weight-semibold(600)` / `--font-weight-bold(700)`。
**行高**：`--line-height-tight(1.4)` / `--line-height-normal(1.5)` / `--line-height-relaxed(1.6)` / `--line-height-loose(1.7)`。

**新增字号一律用 token**，禁裸数字（尤其禁 0.5px 步进的 12.5/13px 这种）。

## 5.6 间距

`--space-*` scale（5 档，与布局 `--spacing-*` 语义隔离）：
| token | 值 | 用途 |
|---|---|---|
| `--space-1` | 0.25rem | 最小间隙 |
| `--space-2` | 0.5rem | 常规 |
| `--space-3` | 0.75rem | 区块 |
| `--space-4` | 1rem | 卡片内 |
| `--space-5` | 1.5rem | 区块间/大间隔 |

**新增间距一律用 token**；精细微调（0.35rem 等）允许但优先 token。禁裸 px 间距（2/4/6px 等）。

## 6. 内联 style

- **禁布局内联**（`display:flex`/`gap`/`alignItems` 组合）：布局走 CSS 类（如 `.setting-inline`、`.dev-row`）。
- 单个间距数值（marginTop 等）也优先走 CSS 类；确需内联时用现有 token 值。
- 新增重复布局 → 抽 CSS 类（命名 BEM 风格：`块-子-状态`），不散落内联。

## 7. 命名与清理

- 类名 BEM 风格：`chat-suggest-accept`（块-子-状态），不加 BEM 修饰符前缀。
- **孤儿类即删**：定义了但无引用的类（`.dev-grid`/`.model-dropdown` 等已被清理）不许堆积。
- 状态类（`.sev-*` 等）颜色走 §3 token，不硬编码。

## 8. 新增原语流程

1. 先看 `styles/primitives.css` 有没有现成原语可复用（`.glass`/`.chip`/`.card`/`.setting-*`）。
2. 没有 → 抽新类，命名 BEM，颜色走变量，圆角走 token。
3. 交互组件新需求 → 先查 Mantine 文档，用 Mantine，别手写。

## 本次治理记录（2026-08-08）

- **按钮双轨收敛**：手写 `.btn`/`.chat-icon-btn`（29 处）全部迁到 Mantine `<Button>`，删对应 CSS 类。
- **孤儿类清理**：`.btn-icon-only`/`.dev-grid`/`.model-dropdown`/`.model-custom-input`/`.resume-frame`/`.tab` 删除。
- **硬编码色修复**：`.sev-*` 三个 hex、`.chat-suggest-type`/`.suggestion-item-type`/`.suggestion-tab-count` 的 `rgba(34,197,94,…)` 全走变量。
- **玻璃拟态**：suggestion-tab/panel/generate-actions 因特化几何（圆角 0.75rem、右缘无边框）保留手写，但玻璃三件套仍用变量——**未**强行复用 `.glass`（圆角语义不同，强塞会改外观）。
- **字体 token 化**：新建 `--font-size-*`（4 档）/ `--font-weight-*` / `--line-height-*`，全量替换 index.css 59 处字号 + 8 处字重 + 6 处行高为 token。`--font-sans` 与 alger.ts 同源注释化。
- **间距 token 化**：新建 `--space-1~5`（5 档），全量替换 134 处 margin/padding/gap 为 token。**过程事故**：批量替换脚本 bug 把 108 处替换成 `var(--space-X)` 占位符、原值丢失，经用户确认按语义档位恢复（非 100% 还原原值，但符合 token 化收敛目标）。教训：**CSS 批量替换先备份**。
- **Mantine 其他组件**（Modal/TextInput/Select）：走默认视觉，未细调——后续按需注入 theme。Button 已注入（subtle 灰边幽灵钮 + 字号 + 方块图标钮）。
- **悬浮工具坞统一**（2026-08-08 追加）：改进建议（原右缘竖条 `suggestion-tab`）+ 信息库（原右下圆角按钮）统一成**「标签 pill」形态**（`.tool-tab` 共享类：玻璃底 + 图标 + 文案 + 计数，active 主色底），并排挂**聊天框右上**（`.resume-tools` 坞）。原 `suggestion-tab`/`suggestion-panel` 右缘竖条定位删除，面板改为从标签下方弹出。**孤儿类已清**：`.suggestion-tab`/`.suggestion-tab-count`/`.facts-panel-trigger` 无引用即删。
- **ToolTab 可复用原语**（2026-08-08 晚）：抽成 `src/components/ToolTab.tsx`（标签 pill + 点击展开 + 点击外部收起，`useClickOutside`）。建议/信息库共用；浮窗基类 `.tool-panel`（玻璃底 + flex），工具经 `panelClassName` 覆写尺寸/定位（如信息库 `.facts-panel-window` fixed 左铺盖预览区 560px）。**以后加类似工具直接复用 ToolTab**。
- **ToolTab 面板样式统一**（2026-08-08 末）：建议/信息库面板的 head/title/count/empty 样式统一（title=lg semibold、count=胶囊、empty=placeholder+space-4）；**Select 下拉 `withinPortal: false`**——否则下拉渲染在 body portal，ToolTab 的 useClickOutside 误判「点击外部」把面板收回（bug 修复）。**聊天框**：上边框加深（`--color-border`）+ 隐藏右侧滚动条（`.chat-messages` scrollbar-width:none + webkit-scrollbar display:none，保留可滚动）。**改名**：待改进建议→优化点、信息库→资料集（i18n + 聊天内「优化建议」硬编码转 i18n）。
- **聊天框上边条 + ToolTab fixed 定位**（2026-08-08 末）：ChatPanel 加 `chat-panel-head`（与 resume-left-head 同款：padding + border-bottom），优化点/资料集工具坞**移入上边条**（不再悬浮盖聊天框）。**ToolTab 浮窗改为 `position: fixed` + 组件算 top/left**（getBoundingClientRect 从标签右下弹出）——脱离容器 overflow 裁切（chat-panel 是 overflow:hidden，absolute 会被裁）。资料集浮窗 `overflow: visible`（Select 下拉渲染在面板内不被裁），内部 `.facts-panel-body` 独立滚动。
- **浮窗样式合规审查**（2026-08-08 末）：对照 §3/§5/§5.5 修两个弹窗——圆角裸值改 token（`.suggestion-item/row` 0.5rem→`--radius-sm`、`.suggestion-item-type` 4px→`--radius-sm`）；type 绿色胶囊**弱化灰色**（`--color-primary-soft`底→`--color-soft`底、`--color-primary-active`字→`--color-muted`字，去掉最扎眼的绿块）；`.suggestion-detail-reason` 去 `font-style: italic`（不在 Alger 主题词汇）；**资料集新增/编辑行控件从 `size="xs"` 改 `size="sm"`**（36px，与同排按钮齐平，遵守按钮铁律「与输入框同排用 sm」）。
- **工具胶囊改实底按钮**（2026-08-08 末）：`.tool-tab` 从玻璃胶囊（`--glass-bg` + 999px 全圆角 + 绿 active）改**实底工具按钮**（`--color-panel` 底 + `--color-border-light` 边框 + `--radius-sm` 圆角；active = `--color-soft` 底 + `--color-border`；count 徽标改灰调）。理由：工具条常驻按钮不是弹层，用玻璃会「糊」、999px 过圆、绿 active 与主题选中态（soft 底）不符。**资料集「新增信息」按钮 `compact-sm`→`sm`**（与展开后保存/取消 36px 齐平，治「新增/保存两套样式」）。

---

## 9. 视觉迭代规范（2026-08-18 grill 定稿 · **批次 3 已落地**）

> 这一轮不是改 bug，是**把"克制工具感（A 路线）"做精**：修层级塌、分离度弱、交互件不统一。
> 大方向已否决：B 强设计感（上展示字体/强对比/深度）、手绘风（Rough.js 等）——均不做。
> 加载态不自研"人字履带"，统一用组件库现成件。
> **落地**：加 `--font-size-title:15px`；画布 `#f8f9fa→#f1f3f5`；4 处面板边框 `border-light→border` + `.card` 去投影去过渡；区段标题应用 title 档；tabular-nums 给版本行/计数/日期/端口；**顺手修 4 个幽灵变量**（`--bg-color`/`--text-color`/`--radius-full`/`--line-height-sm` 引用了但未定义，样式曾静默失效）。

### 9.1 字号：加第 5 档 `title`
- 新增 `--font-size-title: 15px`（字重 600），**专给区段/卡片标题**（`.settings-group h2`/`.suggestion-panel-title`/`.extract-card-title`/`.card h2` 非页头时）。
- `--font-size-heading: 22px` 严格限定**页头 h1 / 卡片主标题**。
- 梯度：`heading 22 → title 15 → lg 13.5(正文/控件) → sm 12(次要) → xs 11(最小)`。title 档**只给标题，正文/控件不许碰**，写进本文件锁死。

### 9.2 分离度：Linear 路线（卡片不加投影）
- 卡片/面板**不加投影**——靠 ①边框 `--color-border-light(#eef1f3)` → `--color-border(#dee2e6)` 加深 + ②画布 `--color-canvas` 压暗（`#f8f9fa` → `#f1f3f5`）让面板立起来。
- 投影只留给 `.glass` 弹层（`--shadow-pop`）——弹层与底层面板拉开高度差，悬浮层级不乱。

### 9.3 数字等宽（局部）
- `font-variant-numeric: tabular-nums` **只给数字密集处**：版本行、计数徽标、日期、端口号。**正文不开**（正文数字等宽生硬）。

### 9.4 动效（**批次 4 已落地**）
- **修复**：去掉 `.card` 的 `all` 过渡（卡片瞬时响应，只交互元素 button/a/input/select 有 hover 过渡）——治"卡片蠕动"。
- **加载态**：资料集加载换 **Mantine `<Skeleton>` 骨架行**（模拟 fact-item 形状，替代"加载中…"）。
- **微反馈**：建议卡四栏换栏两段式（源栏 `.leaving` 淡出+微下移 → 目标栏 `.entering` 淡入+微上移，`transitionMove` 编排）+ 目标栏计数 `.pulse` 缩放回弹。**仅这四栏**；其他确认动作保持 toast，不做物理滑动。

## 10. 交互组件规范（2026-08-18 grill 定稿 · **批次 2 已落地**）

> 治"一个按钮一个样"：尺寸/字号/圆角/形态各处 drift，且与旁边文字/控件不对齐。
> **落地**：`alger.ts` 注入尺寸网格（杀 12.5 + 圆角统一 6px）；建议/提议/冲突卡手写按钮全迁 Mantine（删 `.suggestion-action-*`/`.chat-suggest-*` 孤儿类）；三动作配色=栏色 light 版；危险确认实心红→subtle 红。`.suggestion-toggle`/`.suggestion-retract` 为专用/微型组件，保留手写。**（2026-09-11 更正：这两个也已迁 Mantine `<Button>` + 覆盖类，见 §2 全量迁移）**
>
> **2026-08-24 追改（grill "按钮太傻"）**：`.tool-tab` 手写重按钮（白底+投影+边框）也废了——`ToolTab` 触发钮改 **Mantine `variant="subtle" size="sm"` 幽灵钮**（active 态用 `default` 浅底显形），去白底投影。优化点/资料集图标换 `Sparkles`/`FolderOpen`。建议卡三动作（接受/拒绝/讨论）改**紧凑纯图标 `btn-icon compact-sm` + Mantine `<Tooltip>` 释义**（hover 出气泡，去文字）。**hover 释义统一用 Mantine `<Tooltip>`，不用原生 `title`**（样式能跟玻璃风统一）。

### 10.1 尺寸网格（3 档高度 + 2 档字号 + 统一圆角）
- **高度 3 档**：`30px` 标准（主按钮/工具条入口/表单控件，Mantine `sm` 从 36 收敛）｜`26px` 紧凑（卡片内动作：接受/拒绝/撤回/回看，`compact-sm`）｜`20px` 微型（**仅纯图标、无文字**：卡片内 ▾ 折叠、撤回等低调辅助钮，`.btn-icon-sm`）。
- **主界面 vs 展开面板（2026-08-24 补）**：主界面顶条按钮一律 `sm`(30px)（第N稿/下载/移除/发送/优化点/资料集）；展开面板/卡片内的动作用 `compact-sm`(26px)（建议卡三动作、卡片内增删）。
- **字号 2 档**：30px 档（`sm`）= 12px；`compact-sm` 档 = 11px（alger.ts `--font-size-sm`/`--font-size-xs` 对齐）。**杀掉 alger.ts 里的 `12.5`**（违反 §5.5 的 0.5px 步进禁令）。
- **圆角统一**：可点击件一律 `--radius-sm(6px)`（Mantine 主按钮从默认 10px 收进来；卡片容器才用 10/14px）。

### 10.2 形态 4 级合同（什么级别动作用什么形态）
| 级别 | Mantine variant | 用在哪 |
|---|---|---|
| **L1 主操作** | `filled`（实心绿） | 一屏最多 1 个真正主行动（上传/确认生成/发送） |
| **L2 次操作** | `default`（白底灰边） | 常规可点（第N稿/增删行/图标钮/保存浏览） |
| **L3 弱操作** | `subtle`（透明，hover 显形） | 辅助/回看/取消 |
| **L4 危险** | `subtle color="red"`（**降级，不用实心红**） | 删除/清空/重置 |
- **杀所有手写按钮**：`.suggestion-action-*`/`.chat-suggest-*`/`.tool-tab` 按钮部分/`.extract-add-entry` 全迁 Mantine `<Button>`，按上表对号入座（接 §2 按钮铁律未执行完的部分）。
- **"纯文字按钮"**（extract-add-entry 这类）：升级为 L3 subtle，不许"看着像正文其实是按钮"。

### 10.3 建议卡三动作：颜色 = 目标栏色（清晰版）
- **接受 → 绿**（已确认栏）｜**聊一聊 → 蓝**（正在聊栏）｜**拒绝 → 灰**（已拒绝栏）。
- 三颗统一用**对应栏色的 light/outline 清晰版**（淡底或描边），**非实心填满**——颜色即"点了会去哪一栏"的导航预告；栏是淡底、按钮是同色清晰版，同色系不同强度。

### 10.4 行内对齐合同
- **行内控件同高**：一行里的按钮+输入框+下拉强制同档（全 30 或全 26）。资料集新增行已达标（全 sm），推广到所有行内组合。
- **图标钮垂直居中**：30px 图标钮与更高元素（autosize 输入框/标题行）同排时 `align-items:center`，不顶对齐。
- **文字行旁按钮**：标题/正文行（行高~20px）旁用 26px compact-sm 并居中，不放 30px（会撑高行）。

## 11. 事实条目统一规范（2026-08-18 grill 定稿 · **批次 1 已落地**）

> FactsPanel（确认后）与抽取卡（确认前）收敛成**同一套"事实条目"设计语言**。
> 数据结构不动：`{category, title, points[]}`，父=title、子=points 数组，层级在一条记录内。
> **落地**：新公共组件 `src/components/FactItem.tsx`（FactsPanel + 抽取卡共用）；CSS 统一 `.fact-item` 系（删 `.facts-item`/`.extract-*` 旧条目类）。

### 11.1 双态：显示态纯文本 / 点击才进编辑
- **显示态（默认）**：每条 = 纯文本（title 一行 + points 一个 `<ul>`），无输入框/滚动条/角标/常驻按钮。抽取卡**复刻 FactsPanel 此模式**（删掉满屏 Textarea）。
- **编辑态（点编辑，单条）**：该条变输入框 + 保存/取消；只此一条进编辑，其余保持文本。
- **编辑粒度 = 父级为单位**（整条 title+points 一起改）：不要"全编辑"开关、不要单子要点编辑。

### 11.2 操作按钮：浮右上角 + hover 浮现
- 编辑(Pencil)/删除(Trash2)做成**浮在条目右上角、压文字上方、hover 才浮现**（`facts-item` relative + actions absolute + opacity 过渡），不占独立行。长标题加右 padding 预留位/浮标半透明底防遮挡。

### 11.3 父子关系：树干连接线
- 复用抽取卡的连接线（`extract-child-list::before` 树干 + 横枝 + 圆点）到 FactsPanel——父子有明确视觉连接，两处视觉统一。

### 11.4 编辑态输入框：多行 autosize（修一行框难写长文）
- title/points 从单行 `TextInput` 改 **`Textarea` autosize**（现状 points 是 `join('\n')` 塞单行框，4 行要点只看得见 1 行——bug）。
- 每条子要点一个独立 Textarea（autosize，长高无上限），随内容全显示。
- **删 `resize: vertical` 拖动角标**（与 autosize 矛盾）+ **删内部 `overflow-y:auto` 滚动条**（去掉 maxRows 天花板，滚动交给卡片容器 `.extract-card-body`，避免双层滚动）。
- 保存仍 `split('\n')` + `filter(Boolean)` 拆回 points 数组——**JSON 结构不变**。

### 11.5 添加子要点：树杈插入 + 幽灵末行
- **树杈中间插入**：相邻子要点连接线之间 hover 浮现 +，点击在该位置插入一条。
- **幽灵末行**：列表最后**永远挂一个空输入框**（不属于真实 points 的 UI 垫底），往里打字 = 新增一条（push 进 points、自己清空继续垫底），不输入不采集（保存 `filter(Boolean)` 滤空）。两个交互并存：树杈插入负责"插队"，幽灵行负责"末尾续写"。

### 11.6 编辑/保存按钮统一
- 编辑态的保存/取消、各处的编辑/取消，**全应用统一规格**（接 §10.2 形态合同），不许 FactsPanel 一套、别处另一套。

## 12. 浮窗原语：ToolTab 唯一（2026-08-24 grill 定稿）

- **"标签 pill + 点击展开浮窗"是 ToolTab 一家的活**，不许为某个工具单开第二条相似但不同的浮窗路。
- 触发标签 = **Mantine `variant="subtle" size="sm"` 幽灵钮**（active 态用 `default` 浅底显形）——`ToolTab` 统一渲染（**2026-08-24 追改，见 §治理记录**：手写 `.tool-tab` 重按钮已废，`.tool-tab` 类不再存在）。
- **`align` 参数**：`align:'right'`（默认）浮窗右缘对齐标签右缘、向左铺（优化点/资料集挂聊天框右上）；`align:'left'` 浮窗左缘对齐标签左缘、向右铺（检索控制台在行首左侧）。宽度/尺寸经 `panelClassName` 覆写（资料集 560px、检索控制台 620px）。
- 浮窗 = `.tool-panel glass`，`position:fixed` 视口定位（脱离容器 overflow 裁切），`useClickOutside` 收起。
- **下钻/辅助信息该用哪种容器**：贴合上下文、随手关的小信息 → ToolTab 浮窗或内联 glass；一组要专注编辑的设置/完整对象 → 可用聚焦容器（设置页即此类）。岗位详情的容器形态**冻结待定**（线头）。

## 13. 简历工作台：一个 Pad + 竖线分割 + A4 保真 + 真缩放（2026-08-24 grill 定稿）

### 13.1 一个 Pad
- 左简历预览 + 右聊天框拼成**同一张卡片**：外框/圆角/白底在 `.resume-layout` 一层（`border + radius-lg + panel + overflow:hidden`），左右栏**融入**（`.resume-left`/`.chat-panel` 去各自边框/圆角/背景）。
- 中间分割 = **一条 1px 竖线**（`.resume-splitter`，border-light），伪元素向两侧各扩 3px 透明热区好抓，hover/active 变主色绿。**不要椭圆灰条、不要多余指示箭头**。

### 13.2 可拖分割
- grid 三列 `左 | 1px | 右`，列模板由内联 style 驱动（拖拽改 fr 比例）；两侧各设最小宽（360/340px）。
- **比例存 localStorage**（`resume.splitRatio`），默认 55:45。
- **iframe 拖拽坑**：iframe 铺满后会截获 mousemove/mouseup 致拖拽中断——拖拽时必须给左栏 iframe 加 `pointer-events:none`（`.resume-left.split-dragging iframe`）。

### 13.3 A4 保真（预览框是一个整体）
- iframe **铺满**整个预览框，**不开"框里小窗"**（不给 iframe 加 max-width/阴影/圆角/margin——那些会把框割裂）。
- A4 比例由排版层 `.a4-sheet` 自理（210mm + margin:auto，纸在 iframe 内居中），栏再宽纸不变形。
- 文本/Markdown 预览统一**白底**（与 HTML 简历一致）；灰凹槽（canvas）只留空态/拖拽上传。

### 13.4 真缩放（zoom，仅生成的 HTML 简历）
- **原理 = 浏览器缩放同款 CSS `zoom`**（Chromium）：布局按倍率重算、真实占更大布局盒，超出 iframe 视口自然出横向滚动条。**禁 `transform:scale()`**（视觉放大但布局盒不变、不出横滚）。
- **注入**：sandbox iframe 跨文档改不了内部 CSS，故生成 blob 前往 `</head>` 前插 `<style>html{zoom:Z}</style>`，zoom 变即重包 blob。不动后端/隔离机制。
- **基准 100% = A4 物理大小**（210mm≈794px），**与框宽解耦**——框随便拖，缩放比例不重调（两个自由度不互相牵扯）。
- **范围**：仅"生成的 HTML 简历"（previewHtml / previewContent）开放；用户上传原件（PDF/DOCX 等 url 路径）**不缩放**、不显示控件。
- **控件**：左下悬浮玻璃 `− / 输入% / ＋`（`.zoom-control glass`），步进 25%，范围 50~200%，手动输入回车/失焦生效（非法还原）。**比例存 localStorage**（`resume.zoomPct`），默认 100%。

## 14. 通知 / toast 规范（2026-09-13 定稿）

**toast 的样式唯一出口 = `alger.ts` 的 `Notification.extend`**（`@mantine/core` 的 `Notification` 是 `@mantine/notifications` 的渲染基类，一处注入全站 toast 生效，与 Button/Input 同路数）。

- **玻璃拟态**：`Notification` 的 root 已注入 `.glass` 三件套（`--glass-bg`/`--glass-blur`/`--glass-border`）+ `--shadow-pop`——与弹窗/悬浮卡（白玻璃弹层）一家。**覆盖的只有背景/边框/投影**；形状（圆角走 `--notification-radius` ← `theme.defaultRadius`，内边距）保持 Mantine 默认——色条 `::before` 的避让、closeButton 右缘对齐都依赖它。
  - 例外：气泡 **Tooltip 未注入**，仍吃 Mantine 默认深底（悬浮卡白玻璃、气泡深底，是有意的对比）。
  - 不用 `.glass` 类（在 `styles/` 里）：theme 注入不该反向依赖应用 CSS。
- **不设高度上限**：`main.tsx` 的 `<Notifications notificationMaxHeight="none">`。Mantine 默认 `200`（内联 `max-height`）+ `overflow:hidden` 会**静默裁掉**超长文案——信息肉眼可见地丢，本地单用户应用宁可长一点。极长的文案（如核爆确认）超一屏时**改用 Modal**，别硬塞 toast。
- **跨整页重载的提示**：`reload()` 会连**同一帧**的 toast 一起抹掉。需要「reload 后仍要看到」的提示走 `lib/pendingToast.ts`——reload 前 `armPendingToast()` 把**字典 key**（非已经过语言的文案）+ 当前语言存 sessionStorage，`App` 启动时补弹。存 key 不存文案：重载后按**当时**的语言查（设置响应还没回来，App 已先用预存 locale `seed` i18n，避免首帧按默认 zh 排再跳）。


