"""connectors 包：招聘平台 → 统一 schema 的来源适配器（阶段二 v1）。

每个平台一个 connector，`fetch(query, city) -> list[dict]`（统一 schema）。
两条路（apply.md §3）：
- 免登录 JSON API（猎聘 fe-api）：纯 HTTP，不登录。
- Electron 读 DOM（Boss / 前程无忧）：隐藏 BrowserWindow + 复用登录态（见
  electron/services/job-scraper.js，不在本包）。智联已放弃（风控）。
本包只做「平台 → 统一 schema」，不碰 DB / LLM（分层红线）。
"""
