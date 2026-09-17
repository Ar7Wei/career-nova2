<div align="center">

<img src="electron/assets/icon.png" alt="Career Nova" width="120">

# Career Nova

**本地优先 · 单用户 · 开源的求职桌面应用**

把简历、投递、面试跟进串成一条线，数据全部躺在你自己硬盘上的一个 SQLite 文件里。

<p>
  <a href="README.en.md">English</a> · <b>简体中文</b>
</p>

<p>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.13-3776AB?logo=python&logoColor=white">
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-0.121-009688?logo=fastapi&logoColor=white">
  <img alt="LangGraph" src="https://img.shields.io/badge/LangGraph-1.0-1C3C3C">
  <img alt="React" src="https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=black">
  <img alt="Electron" src="https://img.shields.io/badge/Electron-35-47848F?logo=electron&logoColor=white">
  <img alt="Mantine" src="https://img.shields.io/badge/Mantine-9-339AF0?logo=mantine&logoColor=white">
  <img alt="SQLite" src="https://img.shields.io/badge/SQLite-aiosqlite-003B57?logo=sqlite&logoColor=white">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-green">
</p>

</div>

<!-- 📸 截图占位：后续补应用截图 -->

---

## 这是什么

Career Nova 是一个跑在本机的求职工作台——不是 SaaS，不是多用户，不上云。

它替你做三件事：

- **简历**——读你的旧简历、从聊天里挖出你的经历，改写成新的版本；
- **投递**——从招聘平台抓岗位、筛完在应用内打开，跟进面试到录用；
- **市场**——想知道「我这个方向还缺什么」，直接问它，它现场去招聘网站查真实数据。

它不替你投简历，也不替你决定去哪家。**筛岗位、投简历、做决定的始终是你**——它只是把机械的那部分接过去。

## 下载与安装

到 [**Releases**](https://github.com/Ar7Wei/career-nova2/releases) 页面下载最新版本（目前仅 Windows）：

| 文件 | 说明 |
|---|---|
| `Career Nova Setup x.y.z.exe` | **NSIS 安装器**（推荐）— 可选安装目录、建开始菜单/桌面快捷方式 |
| `Career Nova x.y.z.exe` | **免安装版** — 单文件直接运行，适合放 U 盘或临时试用 |

装好后直接双击启动，**无需安装 Python、Node 或 uv**——后端已随安装包一起冻结。

> ⚠️ **Windows SmartScreen 提示**：安装包目前**没有代码签名证书**，首次运行可能弹出「Windows 已保护你的电脑」。点「更多信息 → 仍要运行」即可。这是未签名开源软件的常见情况，不是病毒警告。
>
> 🕐 **首次启动稍慢**：程序要先拉起本地后端服务（冻结的 Python 运行时），冷启动可能等几秒，属正常。

## 使用引导

1. **启动**：双击图标打开。点关闭按钮默认**收进系统托盘**（后端继续在托盘跑）；要真正退出，右键托盘图标 →「退出」。
2. **配 LLM（可选，但推荐）**：左侧菜单 → **设置** → 填 `base_url` 和 API key → 点「测试连接并拉取模型」→ 从下拉里选模型。
   - API key 存在你本地的 SQLite 里，**不上传任何地方**；
   - **不配 key 也能用**——只是「简历优化」「对话生成」「查市场」这些 AI 功能不可用，手动管理岗位、跟进投递照常。
   - 支持任意 **OpenAI 兼容端点**（官方 API、DeepSeek、本地 Ollama / vLLM 等）。
3. **导入简历**：**简历**页上传旧的 PDF / DOCX / Markdown，后台自动抽取经历进「信息库」；也可以在聊天里直接说你的经历。
4. **抓岗位**：**投递**页选平台（猎聘 / 前程无忧）→ 配筛选条件 → 抓取 → 去重排序后进统一列表。
5. **改数据目录 / 端口**：**设置**页可改数据目录（整个 SQLite 快照搬家）、后端端口、关闭行为。

> 数据都在你本机——想备份，拷走设置里显示的那个数据目录即可。

## 功能

| 模块 | 状态 | 说明 |
|---|---|---|
| **简历** | ✅ 已完成 | 上传 PDF/DOCX/MD → MarkItDown 转文档 → 后台抽事实进「信息库」；对话式生成/改写；人确认后才落库；版本化 + 回滚；HTML/PDF/Markdown 导出 |
| **投递** | ✅ 已完成 | 岗位聚合（跨平台统一列表，猎聘 + 前程无忧）、去重、排序；投递跟进状态线；面试日历 |
| **市场调研** | 🟡 基础可用 | 简历 agent 的 `query_market` 工具：现场查岗位量 / 城市 / 学历 / 年限 / 薪资分布 |

> **更多在路上。** 尚未落地的方向见下方 [**未来展望**](#未来展望)——那是计划，不是现状。

## 设计原则

**数据是你的。** 简历、事实、岗位、聊天记录全在本地一个 SQLite 文件里，没有账号，没有服务器，没有埋点。想备份就拷那个文件。

**AI 是可选项，不是前提。** 应用没有 API key 也能启动——只在你打开 LLM 功能（对话生成、后台抽取、查市场）时才会用到。不配置，应用照常可用。

**错误要出声。** 后端统一的错误契约（`code` / `message` / `retryable` / `action`）让每次失败都带上「为什么」和「怎么办」，前端不再吞掉真因。

**配置归你。** 端口、数据目录、关闭行为、模型、语言——都在设置面板里，不埋在代码里。

> ⚠️ **AI 功能会发送数据。** 「简历优化」「对话生成」「查市场」会把简历内容和对话上下文发给你配置的 LLM 服务（OpenAI 兼容端点）。这是本地优先应用的边界：**数据存储是本地的，但 AI 功能必然要联网**。不需要 AI 时就不要配 key，或指向本地模型端点。

## 架构

```
┌─────────── Electron 壳 ───────────┐
│  托盘 · 窗口管理 · 子进程生命周期 │
│  DOM 抓取（招聘平台） · PDF 导出  │
└───────────────┬───────────────────┘
                │  spawn（shell:false · 固定端口 8765）
┌───────────────▼───────────────────┐
│        FastAPI 本地服务（UV）     │
│  Router → Service → Graph → Node  │
│              → Agent → Prompt     │
│           Repository → SQLite     │
└───────────────┬───────────────────┘
                │  HTTP
┌───────────────▼───────────────────┐
│   React 前端（Mantine v9 · Vite） │
└───────────────────────────────────┘
```

**技术栈**

| 层 | 选型 |
|---|---|
| 壳 | Electron 35 — 托盘驻留、启动画面、子进程管理（`taskkill /F /T` 整树杀 + 端口核验）、招聘平台 DOM 抓取、`printToPDF` 导出 |
| 前端 | React 19 + TypeScript + Mantine v9 + zustand + Vite |
| 后端 | FastAPI + LangGraph（编排）+ SQLModel + aiosqlite + structlog |
| LLM | 任意 OpenAI 兼容端点（`base_url` + key 在设置面板配置，模型名从探测接口拉取） |
| 存储 | SQLite（业务数据）+ sqlite-vec（仅向量，留给 Chatter）+ 独立 `checkpoints.db`（LangGraph 挂起态） |

**文档提取**用 [MarkItDown](https://github.com/microsoft/markitdown)（PDF / DOCX / PPTX / XLSX / HTML / CSV）。

## 快速开始（开发者）

> 最终用户请直接走上面的 [**下载与安装**](#下载与安装)。以下是**从源码跑**的步骤。

**前置**：[uv](https://docs.astral.sh/uv/) · Node.js 20+ · Python 3.13（uv 会自动装）

```bash
git clone https://github.com/Ar7Wei/career-nova2.git
cd career-nova2

# 1. 后端依赖（含测试 / 开发工具）
uv sync --group test --group dev

# 2. 前端依赖
cd frontend && npm install && cd ..

# 3. Electron 依赖
cd electron && npm install
```

**启动**

```bash
cd electron && npm start
```

Electron 会自动拉起 Vite 开发服务器（动态找空闲端口）和 FastAPI 后端（固定 `8765`），然后开窗口。

**配置 LLM**：打开应用 → **设置** → 填 `base_url` 和 API key → 点「测试连接并拉取模型」→ 从下拉里选模型。API key 存在本地 SQLite，不回传任何地方。

## 常用命令

```bash
uv run pytest                    # 跑测试
uv run ruff check .              # lint
uv run mypy .                    # 类型检查
uv run fastapi dev app/main.py   # 只起后端（默认 8000；Electron 下用 8765）
```

## 自己打包（Windows 安装包）

版本号的唯一真相源是仓库根的 `package.json`。打之前先把它分发到各消费点（后端 `/health`、安装包文件名等），否则产物带的是上次的版本号：

```bash
npm run sync-version                      # 0. 版本号 → electron / frontend / 后端 / pyproject / uv.lock
uv run pyinstaller backend.spec --clean   # 1. 冻结后端 → dist/backend/
cd frontend && npm run build && cd ..     # 2. 前端产物 → dist/frontend/
cd electron && npm run build              # 3. 出安装包 → dist/installer/
```

产物统一在 `dist/` 下：

| 路径 | 内容 |
|---|---|
| `dist/backend/` | 冻结的 FastAPI 后端（onedir） |
| `dist/frontend/` | 前端构建产物（vite） |
| `dist/installer/` | **NSIS 安装器**（`Career Nova Setup *.exe`，主）+ Portable（`Career Nova *.exe`，免安装） |

> 要干净重打：先 `rm -rf dist build`（`build/` 是 PyInstaller 的工作目录），再跑上面四步。
>
> 发版（自动打包 + 出 Release）走 CI，只需改根 `package.json` 的版本号并 push 到 `main`——详见 [docs/RELEASE.md](docs/RELEASE.md)。

## 未来展望

以下是规划中的方向，**尚未实现**：

- **投递自动化**——岗位预填、自动投递、结果自动回收。当前版本是手动挡，自动化会作为渐进升级逐步接入。
- **Chatter（行业文档 RAG）**——把你手里的行业文档喂进来，基于本地向量检索做问答。
- **市场广度探测**——回答「哪个方向在涨」这类跨方向、跨时间的问题；现在的市场工具只做单次查询。
- **Growth**——更长期的成长规划方向，设计未定。

## 免责声明

- **招聘平台抓取**：本项目的岗位抓取功能依赖各招聘平台（如猎聘、前程无忧）的**公开网页结构**，仅供**个人求职**使用。请自行遵守目标网站的服务条款与 `robots.txt`；**请勿用于批量采集、商业分发或任何形式的滥用**。平台改版会导致抓取失效，恕不另行通知。
- **AI 输出仅供参考**：简历改写、岗位分析、市场数据等由大语言模型生成，**可能存在错误或偏差**。请在使用前自行核实，不要盲目照搬。因使用本项目内容造成的任何后果（包括但不限于错失机会、信息泄露），作者不承担责任。
- **数据安全自负**：数据存储在你本机，备份与保管由你负责。启用 AI 功能时，相关内容会发送至**你自行配置的 LLM 服务**，其隐私政策与数据处理方式由该服务商决定。
- **按原样提供**：本项目以 MIT 许可证「按原样」提供，不附带任何明示或暗示的担保。

## 参与

Issues 和 PR 都欢迎。

> 注意：本仓库**不公开测试代码**（`tests/` 与 `*.test.ts` 在 `.gitignore` 中）。

## 许可证

[MIT](LICENSE) © 2026 Ar7Wei
