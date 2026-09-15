const fs = require("fs");
const path = require("path");
const { app } = require("electron");

// 打包态分流：app.isPackaged 时 __dirname 指进 asar（只读、且外部产物不在里面），
// 后端 onedir / 前端 dist 都走 extraResources 裸放在 process.resourcesPath 下，
// 故「项目根」在打包态 = resourcesPath；开发态 = electron/ 上一级（项目根）。
const PROJECT_ROOT = app.isPackaged ? process.resourcesPath : path.resolve(__dirname, "..", "..");
const FRONTEND_DIR = path.join(PROJECT_ROOT, "frontend");
// 统一产物根（仅开发态用）：backend/ + frontend/ + installer/ 都落这（见 vite.config.ts
// build.outDir / backend.spec --distpath / electron-builder directories.output）。
const DIST_ROOT = path.join(PROJECT_ROOT, "dist");
// 前端入口 index.html：打包态 = resources/frontend/dist/（extraResources 裸放，布局不变）；
// 开发态 = 产物根下的 dist/frontend（npm run build 产物）。
const FRONTEND_DIST_INDEX = app.isPackaged
  ? path.join(process.resourcesPath, "frontend", "dist", "index.html")
  : path.join(DIST_ROOT, "frontend", "index.html");
// 图标资产目录（electron/assets：icon.ico/png、tray.png）。dev/build 两态共用。
// 打包后 assets 仍在 app.asar 内，__dirname 可读（纯静态文件，无需 unpack），故不分流。
const ASSETS_DIR = path.join(__dirname, "..", "assets");
// 后端 onedir 相对 resources 根的路径（config.js 打包态 exePath 默认值用）。
const BACKEND_EXE_REL = path.join("backend", "backend.exe");

/**
 * 解析 uv 可执行文件的真实路径（不走 cmd 壳）。
 * Windows 下 uv 装在 ~/.local/bin/uv.exe 或 %USERPROFILE%\.cargo\bin 等；
 * 返回真实 .exe 路径以便 spawn 时 shell:false、PID 即 uv 真身（进程树单链、可干净杀）。
 * @returns {string}
 */
function resolveUvBinary() {
  const home = process.env.USERPROFILE || process.env.HOME || "";
  const candidates =
    process.platform === "win32"
      ? [
          path.join(home, ".local", "bin", "uv.exe"),
          path.join(home, ".cargo", "bin", "uv.exe"),
          path.join(PROJECT_ROOT, ".venv", "Scripts", "uv.exe"),
        ]
      : [path.join(home, ".local", "bin", "uv"), path.join(home, ".cargo", "bin", "uv"), "/usr/local/bin/uv"];
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  // 兜底：寄希望于 PATH（win 下若仍是 .cmd 会退化，但多数情况 uv.exe 在 PATH）。
  return "uv";
}

/**
 * 解析 Vite 入口（dev server 用 node 直跑 vite.js，避免 Windows .cmd shim 问题）。
 * @returns {string}
 */
function resolveViteEntry() {
  const candidate = path.join(FRONTEND_DIR, "node_modules", "vite", "bin", "vite.js");
  if (!fs.existsSync(candidate)) {
    throw new Error(`未找到 Vite：${candidate}。请先在 frontend/ 下 npm install。`);
  }
  return candidate;
}

/**
 * build 模式下的前端入口（vite 打包产物）。
 * @returns {string}
 */
function resolveFrontendDist() {
  if (!fs.existsSync(FRONTEND_DIST_INDEX)) {
    throw new Error(`未找到前端产物：${FRONTEND_DIST_INDEX}。请先 npm run build。`);
  }
  return FRONTEND_DIST_INDEX;
}

module.exports = {
  PROJECT_ROOT,
  FRONTEND_DIR,
  FRONTEND_DIST_INDEX,
  ASSETS_DIR,
  BACKEND_EXE_REL,
  PRELOAD_PATH: path.join(__dirname, "..", "preload.js"),
  resolveUvBinary,
  resolveViteEntry,
  resolveFrontendDist,
};
