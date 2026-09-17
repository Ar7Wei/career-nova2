<div align="center">

<img src="electron/assets/icon.png" alt="Career Nova2" width="120">

# Career Nova2

**Local-first, single-user, open-source desktop app for job hunting**

Resumes, applications, and interview follow-ups in one place — all of it in a SQLite file on your own disk.

<p>
  <b>English</b> · <a href="README.md">简体中文</a>
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

<p>
  <img src="docs/assets/screenshots/hero-main.png" alt="Career Nova2 main window" width="800">
</p>

---

## What it is

Career Nova2 is a job-hunting workbench that runs entirely on your machine — not SaaS, not multi-user, no cloud.

It does three things for you:

- **Resume** — reads your old resume, digs your experience out of a conversation, rewrites it into new versions;
- **Applications** — scrapes job postings from job boards, filters them, opens them in-app, tracks interviews through to an offer;
- **Market** — ask what your target role actually demands, and it queries live job-board data to answer.

It does *not* apply to jobs for you, and it does *not* decide where you go. **You filter, you apply, you decide** — it just takes the mechanical part off your hands.

<p>
  <img src="docs/assets/screenshots/resume-chat.png" alt="Conversational resume generation: chat + live preview" width="800">
</p>

## Download & install

Grab the latest build from the [**Releases**](https://github.com/Ar7Wei/career-nova2/releases) page (Windows only for now):

| File | What it is |
|---|---|
| `Career Nova2 Setup x.y.z.exe` | **NSIS installer** (recommended) — choose install dir, creates Start Menu / desktop shortcuts |
| `Career Nova2 x.y.z.exe` | **Portable** — single file, runs in place; good for a USB stick or a quick try |

Just double-click to launch. **No Python, Node, or uv required** — the backend ships frozen inside the installer.

> ⚠️ **Windows SmartScreen warning**: the installers are **not code-signed**, so the first run may show "Windows protected your PC". Click **More info → Run anyway**. This is normal for unsigned open-source software, not a virus warning.
>
> 🕐 **First launch is slow**: the app has to bring up the local backend (a frozen Python runtime). A cold start can take a few seconds — that's expected.

## Usage

1. **Launch** — double-click the icon. The close button **minimizes to the system tray** by default (the backend keeps running there). To really quit: right-click the tray icon → **Quit**.
2. **Configure the LLM** (optional, recommended) — left menu → **Settings** → enter `base_url` and an API key → click "Test connection and fetch models" → pick a model.
   - The key is stored in your local SQLite and **is never uploaded anywhere**;
   - **Works without a key** — only the AI features (resume optimization, conversational generation, market lookups) are unavailable. Managing postings and tracking applications still work.
   - Any **OpenAI-compatible endpoint** works (official API, DeepSeek, a local Ollama / vLLM, etc.).
3. **Import a resume** — on the **Resume** page, upload your old PDF / DOCX / Markdown; experience is extracted into the knowledge base in the background. You can also just describe your experience in chat.

<p>
  <img src="docs/assets/screenshots/resume-list.png" alt="Resume page: uploaded resumes and extraction status" width="800">
</p>
<p>
  <img src="docs/assets/screenshots/resume-export.png" alt="Resume preview and export (PDF / Markdown / HTML)" width="800">
</p>
4. **Scrape jobs** — on the **Applications** page, pick a board (Liepin / 51job) → set filters → scrape → dedup and sort into one unified list.
5. **Change the data directory / port** — the **Settings** page lets you move the data directory (relocates the whole SQLite snapshot), change the backend port, and set close behavior.

> Everything lives on your machine — to back up, just copy the data directory shown in Settings.

## Features

| Module | Status | Notes |
|---|---|---|
| **Resume** | ✅ Done | Upload PDF/DOCX/MD → MarkItDown converts to a document → facts extracted in the background into a knowledge base; conversational generation and rewriting; nothing is saved until you confirm; versioning + rollback; HTML/PDF/Markdown export |
| **Applications** | ✅ Done | Job aggregation (one cross-platform list — Liepin + 51job), dedup, sorting; application follow-up timeline; interview calendar |
| **Market research** | 🟡 Functional | The resume agent's `query_market` tool: live counts by city / degree / experience / salary bands |

<p>
  <img src="docs/assets/screenshots/apply-list.png" alt="Applications page: one cross-platform job list" width="800">
</p>
<p>
  <img src="docs/assets/screenshots/apply-analysis.png" alt="Application analysis: charts + mixed text/figure report" width="800">
</p>

> **More is on the way.** For directions that aren't built yet, see [**Roadmap**](#roadmap) below — that's the plan, not the current state.

## Design principles

**Your data is yours.** Resumes, facts, job postings, chat history — all in a single local SQLite file. No account, no server, no telemetry. To back up, copy that file.

**AI is optional, not a prerequisite.** The app starts fine without an API key — it's only needed when you use an LLM feature (conversational generation, background extraction, market lookups).

**Errors speak up.** A unified backend error contract (`code` / `message` / `retryable` / `action`) means every failure carries a *why* and a *what to do next*. The frontend never swallows the real cause.

**You own the config.** Ports, data directory, close behavior, model, language — all in the settings panel, none buried in code.

> ⚠️ **AI features send data.** "Resume optimization", "conversational generation", and "market lookups" send your resume content and conversation context to the LLM endpoint you configure (any OpenAI-compatible endpoint). That's the boundary of a local-first app: **storage is local, but AI features inherently need the network.** Don't configure a key if you don't need AI, or point it at a local model endpoint.

## Architecture

```
┌────────── Electron shell ─────────┐
│  Tray · window mgmt · child procs │
│  DOM scraping (job boards) · PDF  │
└───────────────┬───────────────────┘
                │  spawn (shell:false · fixed port 8765)
┌───────────────▼───────────────────┐
│       FastAPI local service       │
│  Router → Service → Graph → Node  │
│              → Agent → Prompt     │
│           Repository → SQLite     │
└───────────────┬───────────────────┘
                │  HTTP
┌───────────────▼───────────────────┐
│    React frontend (Mantine · Vite)│
└───────────────────────────────────┘
```

**Stack**

| Layer | Choice |
|---|---|
| Shell | Electron 35 — tray residency, splash screen, child-process lifecycle (`taskkill /F /T` full-tree kill + port verification), job-board DOM scraping, `printToPDF` export |
| Frontend | React 19 + TypeScript + Mantine v9 + zustand + Vite |
| Backend | FastAPI + LangGraph (orchestration) + SQLModel + aiosqlite + structlog |
| LLM | Any OpenAI-compatible endpoint (`base_url` + key configured in settings; model names fetched from a probe endpoint) |
| Storage | SQLite (business data) + sqlite-vec (vectors only, reserved for Chatter) + a separate `checkpoints.db` (LangGraph suspended state) |

**Document extraction** uses [MarkItDown](https://github.com/microsoft/markitdown) (PDF / DOCX / PPTX / XLSX / HTML / CSV).

## Getting started (developers)

> End users should use [**Download & install**](#download--install) above. The steps below are for **running from source**.

**Prerequisites:** [uv](https://docs.astral.sh/uv/) · Node.js 20+ · Python 3.13 (uv installs it for you)

```bash
git clone https://github.com/Ar7Wei/career-nova2.git
cd career-nova2

# 1. Backend deps (including test / dev tools)
uv sync --group test --group dev

# 2. Frontend deps
cd frontend && npm install && cd ..

# 3. Electron deps
cd electron && npm install
```

**Run**

```bash
cd electron && npm start
```

Electron brings up the Vite dev server (on a free port) and the FastAPI backend (fixed port `8765`), then opens the window.

**Configure the LLM:** open the app → **Settings** → enter `base_url` and an API key → click "Test connection and fetch models" → pick a model from the dropdown. The key lives in your local SQLite and never leaves your machine.

## Commands

```bash
uv run pytest                    # run tests
uv run ruff check .              # lint
uv run mypy .                    # type-check
uv run fastapi dev app/main.py   # backend only (default 8000; 8765 under Electron)
```

## Build it yourself (Windows installer)

Produce the three inputs in order, then hand off to electron-builder. All three land in the single output root `dist/`:

```bash
uv run pyinstaller backend.spec --clean   # 1. freeze backend → dist/backend/
cd frontend && npm run build && cd ..     # 2. frontend bundle → dist/frontend/
cd electron && npm run build              # 3. installers → dist/installer/
```

Everything lands under `dist/`:

| Path | Contents |
|---|---|
| `dist/backend/` | the frozen FastAPI backend (onedir) |
| `dist/frontend/` | the frontend bundle (vite) |
| `dist/installer/` | **NSIS installer** (`Career Nova2 Setup *.exe`, primary) + Portable (`Career Nova2 *.exe`, no install) |

> For a clean rebuild: `rm -rf dist build` first (`build/` is PyInstaller's work directory), then run the three steps.

## Roadmap

Directions that are planned but **not implemented yet**:

- **Application automation** — prefill, auto-apply, auto-detect outcomes. The current release is manual; automation will land incrementally.
- **Chatter (industry-doc RAG)** — feed it your own industry documents and ask questions, backed by local vector search.
- **Broad market probing** — answering "which direction is growing" across roles and over time. Today's market tool only does one-shot lookups.
- **Growth** — a longer-term career-growth direction; design is still open.

## Disclaimer

- **Job-board scraping** — the job-scraping feature relies on the **public page structure** of job boards (e.g. Liepin, 51job) and is intended for **personal job hunting only**. You are responsible for complying with each site's terms of service and `robots.txt`. **Do not use it for bulk collection, commercial redistribution, or any form of abuse.** Site redesigns will break scraping without notice.
- **AI output is a reference only** — resume rewrites, job analyses, and market figures are produced by a large language model and **may be wrong or biased**. Verify before relying on them. The author accepts no liability for consequences of using this project's output (including but not limited to missed opportunities or information disclosure).
- **Your data, your responsibility** — data is stored on your machine; backing it up and keeping it safe is up to you. When AI features are enabled, the relevant content is sent to **the LLM service you configure yourself**, whose privacy policy and data handling are governed by that provider.
- **Provided as-is** — this project is provided under the MIT License, without warranty of any kind, express or implied.

## Contributing

Issues and PRs are welcome.

> Note: **test code is not published** in this repo (`tests/` and `*.test.ts` are in `.gitignore`).

## License

[MIT](LICENSE) © 2026 Ar7Wei
