/**
 * 运行模式：软切换（软 = 改一处/一个环境变量即切，默认开发态）。
 *
 * 前端 frontend.mode:
 *   dev   → 起 vite dev server，窗口指向它（热更新，开发用）
 *   build → 不起 dev server，窗口直接加载前端产物 dist/frontend/index.html（日后上线）
 *
 * 后端 backend.mode:
 *   uv   → spawn `uv run uvicorn app.main:app`（开发；遵循「后端一律 UV 运行」）
 *   exe  → spawn 打包好的后端可执行文件（日后分发，用户无需装 Python/uv）
 *
 * 覆盖方式：环境变量 CAREERNOVA_FRONTEND_MODE / CAREERNOVA_BACKEND_MODE，
 * 或打包后经 app.isPackaged 自动落到 build/exe（见 autoDetect）。
 */
const { app } = require("electron");
const { BACKEND_EXE_REL } = require("./utils/paths");

const config = {
  frontend: {
    /** @type {"dev" | "build"} */
    mode: process.env.CAREERNOVA_FRONTEND_MODE || "dev",
    devPort: Number(process.env.CAREERNOVA_FRONTEND_PORT || 5173),
  },
  backend: {
    /** @type {"uv" | "exe"} */
    mode: process.env.CAREERNOVA_BACKEND_MODE || "uv",
    /**
     * 后端固定端口（设置项，默认 8765——避开 8000/8080/3000/5000 等主流占用）。
     * 固定不换口：启动时若被占，明确报错让用户去设置里改，不悄悄自增（换口是孤儿温床）。
     */
    preferredPort: Number(process.env.CAREERNOVA_BACKEND_PORT || 8765),
    /**
     * exe 模式下的后端可执行文件路径（相对项目根或绝对）。
     * 打包态默认 resources/backend/backend.exe（extraResources 裸放，process-manager 用
     * PROJECT_ROOT=resourcesPath join 解析）；开发态为空（不经过 exe 分支）。
     * 环境变量 CAREERNOVA_BACKEND_EXE 可覆盖（含打包态）。
     */
    exePath: process.env.CAREERNOVA_BACKEND_EXE || (app.isPackaged ? BACKEND_EXE_REL : ""),
    /** SQLite 数据库所在目录（settings.json data_dir，启动前注入后端 DATABASE_URL）。 */
    dataDir: process.env.CAREERNOVA_DATA_DIR || "",
  },
  window: {
    /** 关闭按钮行为：'tray' 缩小到托盘（默认）| 'quit' 直接退出。设置项。 */
    closeAction: process.env.CAREERNOVA_CLOSE_ACTION || "tray",
  },
  log: {
    /** 日志目录（settings.json log_dir）：空 = 默认 userData/logs，main.js 解析。 */
    dir: process.env.CAREERNOVA_LOG_DIR || "",
    /** 日志保留天数（settings.json log_retention_days），后端经 env LOG_RETENTION_DAYS 注入。 */
    retentionDays: Number(process.env.CAREERNOVA_LOG_RETENTION_DAYS || 30),
  },
};

/**
 * 打包后自动切到产物模式（除非环境变量已显式指定）。
 * @param {boolean} isPackaged
 */
function autoDetect(isPackaged) {
  if (!process.env.CAREERNOVA_FRONTEND_MODE && isPackaged) {
    config.frontend.mode = "build";
  }
  if (!process.env.CAREERNOVA_BACKEND_MODE && isPackaged) {
    config.backend.mode = "exe";
  }
}

module.exports = { config, autoDetect };
