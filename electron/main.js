const { app, dialog, session, ipcMain, BrowserWindow } = require("electron");
const fs = require("fs");
const path = require("path");
const { config, autoDetect } = require("./config");
const { startBackend, startFrontend, stopAll, killAllSync, getPorts, setStatusReporter } = require("./services/process-manager");
const { createMainWindow, getMainWindow } = require("./windows/main-window");
const { createTray, destroyTray } = require("./tray/app-tray");
const { createSplash, setSplashStatus, closeSplash } = require("./splash/splash-window");
const { logger } = require("./utils/logger");
const settingsStore = require("./settings-store");
const { migrateDatabase } = require("./services/db-migrate");
const { migrateLogs } = require("./services/log-migrate");
const { registerExportIpc } = require("./services/export");
const { registerApplyIpc } = require("./windows/apply-window");
const { registerJobsIpc } = require("./services/job-scraper");

// 尽早捕获致命启动错误，避免静默退出。
process.on("uncaughtException", (err) => {
  logger.error("uncaught_exception", err);
  dialog.showErrorBox("程序发生致命错误", err?.message || String(err));
  app.exit(1);
});
process.on("unhandledRejection", (reason) => {
  logger.error("unhandled_rejection", reason);
  dialog.showErrorBox("程序发生致命错误", String(reason));
  app.exit(1);
});

let isQuitting = false;

// 固定 userData 目录（2026-08-17）：开发态 app.getName() = "career-nova-electron"、
// 打包态 = "Career Nova2"（build.productName），两态 userData 分家 → 登录态（session partition）
// 与 settings.json 各自存一处，开发登录的号打包后要重登。这里显式统一到 appData/Career Nova2，
// 消除分家。setPath 必须在 app ready 前调用才生效。
//
// ⚠️ 产品名 2026-09-17 改为 "Career Nova2"，但**目录名故意仍留旧的 "Career Nova"**：
// 它是老用户的既有数据位置（settings.json / 登录态 partition / 日志都在那）。跟着改名
// 会让升级用户「设置与登录态凭空消失」（数据还在旧目录，新目录是空的）。这与
// productName 不同步是**有意的**——产品名是给人看的，目录名是给数据用的，两者解耦。
// 若将来真要迁移目录，得照 db-migrate/log-migrate 的写法做一次性搬家，不能直接改这行。
const LEGACY_USERDATA_DIR = app.getPath("userData");
const UNIFIED_USERDATA_DIR = path.join(app.getPath("appData"), "Career Nova");
app.setPath("userData", UNIFIED_USERDATA_DIR);

/** 一次性迁移旧 userData 的 settings.json 到新目录（新目录已有则不覆盖）。
 *  沿用 db-migrate/log-migrate 的迁移模式：只搬 settings.json（纯配置），不搬
 *  Partitions（登录态）——登录态让用户在新目录重登一次（一次性，已接受）。 */
function migrateSettingsFile() {
  const oldPath = path.join(LEGACY_USERDATA_DIR, "settings.json");
  const newPath = path.join(UNIFIED_USERDATA_DIR, "settings.json");
  if (LEGACY_USERDATA_DIR === UNIFIED_USERDATA_DIR) return; // 已一致，无需迁移
  if (fs.existsSync(newPath)) return; // 新目录已有配置，不动
  if (!fs.existsSync(oldPath)) return; // 旧目录没有，无需迁移
  try {
    fs.mkdirSync(UNIFIED_USERDATA_DIR, { recursive: true });
    fs.copyFileSync(oldPath, newPath);
    logger.info("settings_migrated", { from: LEGACY_USERDATA_DIR, to: UNIFIED_USERDATA_DIR });
  } catch (err) {
    logger.error("settings_migrate_failed", { error: String(err) });
  }
}

/** CSP 等安全策略（dev/build 两种加载都覆盖）。 */
function configureSecurity() {
  const { backendPort, frontendPort } = getPorts();
  const frontendOrigin = `http://127.0.0.1:${frontendPort}`;
  const wsFrontendOrigin = `ws://127.0.0.1:${frontendPort}`;
  // 后端固定端口（设置项，不动态自增）——dev 下调用器/健康检查直连它，build 下 fetch 亦同。
  const backendOrigins = `http://127.0.0.1:${backendPort} http://localhost:${backendPort}`;

  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        "Content-Security-Policy": [
          "default-src 'self'; " +
            "script-src 'self' 'unsafe-inline' 'unsafe-eval'; " +
            "style-src 'self' 'unsafe-inline'; " +
            `connect-src 'self' blob: data: ${frontendOrigin} ${wsFrontendOrigin} ${backendOrigins}; ` +
            "img-src 'self' data: blob:; " +
            "font-src 'self' data:; " +
            // pdf.js 在 Web Worker 里解析/渲染 PDF；dev 下 worker 经 vite server、
            // 也可能以内联 blob worker 形式加载，两者都要放行。
            "worker-src 'self' blob:; " +
            "child-src 'self' blob:; " +
            // 简历预览用 blob: objectURL 内嵌渲染 PDF（iframe/object）
            // + https: 允许投递页（阶段二）在弹窗内嵌展示真实岗位页（猎聘等平台，
            //   无 X-Frame-Options 反嵌、CSP frame-src *；iframe 展示平台页是投递引擎地基）
            "frame-src 'self' blob: https:; " +
            "object-src 'self' blob:;",
        ],
      },
    });
  });

  session.defaultSession.setPermissionRequestHandler((_wc, _perm, cb) => cb(false));
}

function performQuit() {
  if (isQuitting) return;
  isQuitting = true;
  logger.info("app_quitting");
  const window = getMainWindow();
  if (window && !window.isDestroyed()) window.destroy();
  destroyTray();
  stopAll()
    .then(() => {
      logger.info("services_stopped_exiting");
      logger.flush(); // 退出前把 250ms 缓冲落盘，避免尾部日志丢失
      app.exit(0);
    })
    .catch((err) => {
      logger.error("stop_services_error", err);
      logger.flush();
      app.exit(1);
    });
}

async function initialize() {
  try {
    autoDetect(app.isPackaged);
    // 迁移旧 userData 的 settings.json 到统一目录（一次性；不搬登录态）。必须在 settingsStore.init 前。
    migrateSettingsFile();
    // Electron 本地设置（端口/关闭行为/数据目录）是这条线的真相源：启动时覆盖 config 默认值。
    settingsStore.init(app.getPath("userData"));
    config.backend.preferredPort = settingsStore.get("backend_port");
    config.window.closeAction = settingsStore.get("close_action");
    config.backend.dataDir = settingsStore.get("data_dir");
    // 日志目录：settings.json log_dir 为空 → 默认 userData/logs，并【写回】物化——
    // 之后前端一打开就看到实际目录（与 data_dir 一致）。保留天数空 → 默认 30。
    const defaultLogDir = path.join(app.getPath("userData"), "logs");
    let configuredLogDir = settingsStore.get("log_dir");
    if (!configuredLogDir) {
      configuredLogDir = defaultLogDir;
      settingsStore.set({ log_dir: defaultLogDir }); // 物化默认值，前端可见
    }
    config.log.dir = configuredLogDir;
    config.log.retentionDays = settingsStore.get("log_retention_days") ?? 30;
    // 日志迁移：log_dir 改变时，把旧目录（current_log_dir）的按日期日志搬到新目录
    //（过期日志不搬；不覆盖已有）。迁移后把新目录记为 current_log_dir。
    const prevLogDir = settingsStore.get("current_log_dir");
    const logMigrate = migrateLogs(config.log.dir, prevLogDir || defaultLogDir, config.log.retentionDays);
    settingsStore.set({ current_log_dir: config.log.dir });
    if (logMigrate.copied > 0) {
      logger.info("logs_migrated", { from: logMigrate.from, to: logMigrate.to, count: logMigrate.copied });
    } else if (logMigrate.reason === "error") {
      logger.error("logs_migrate_failed", { error: logMigrate.error });
    }
    // config.log.dir 就绪后重设日志路径（ready 事件回调早于 initialize，可能用了默认目录）
    logger.init();
    // 启动后端前迁移应用数据：data_dir 改变时把旧目录的 业务库 + checkpoint（含 WAL）
    // + originals/ 复制到新位置（不覆盖）。三项分项判定，逐项记日志。
    // 源 = 上次**实际**用的目录（current_data_dir，同 current_log_dir 的模式）——不能写死
    // 默认 data 目录，否则第二次改目录会从过期的源搬、丢掉中间那次之后的新数据。
    // 默认值用 settings-store 的 defaultDataDir()（开发态项目根 data、打包态 userData/data），
    // 与 data_dir 出厂默认同源，避免两处各写一份漂移。
    const defaultDataDir = settingsStore.DEFAULTS.data_dir;
    let currentDataDir = settingsStore.get("current_data_dir");
    if (!currentDataDir) {
      currentDataDir = defaultDataDir;
      settingsStore.set({ current_data_dir: currentDataDir }); // 物化默认值，下次搬家有源
    }
    const migrateResult = migrateDatabase(config.backend.dataDir, currentDataDir);
    if (migrateResult.copied) {
      const moved = migrateResult.results.filter((r) => r.copied).map((r) => r.name);
      logger.info("db_migrated", { from: migrateResult.from, to: migrateResult.to, moved });
    } else if (migrateResult.reason === "error") {
      logger.error("db_migrate_failed", { error: migrateResult.error });
    } else if (migrateResult.results.length > 1) {
      // 无一项可搬（多为目标已存在）——留痕便于事后核对"数据是否真在新目录"。
      logger.info("db_migrate_skipped", {
        to: migrateResult.to,
        reasons: migrateResult.results.map((r) => `${r.name}:${r.reason}`),
      });
    }
    // 记下这次实际用的目录，供下次搬家当源。
    settingsStore.set({ current_data_dir: config.backend.dataDir });
    logger.info("run_mode", {
      packaged: app.isPackaged,
      frontend: config.frontend.mode,
      backend: config.backend.mode,
      backend_port: config.backend.preferredPort,
      close_action: config.window.closeAction,
      data_dir: config.backend.dataDir,
      log_dir: config.log.dir,
      log_retention_days: config.log.retentionDays,
    });
    // 启动体验：一进关键路径就弹出 splash（logo + 阶段文案 + 不确定进度条），
    // 让用户明确「在启动、没卡死」——否则后端冷启动那几秒屏幕上一个窗都没有，纯黑等。
    createSplash();
    setSplashStatus("launch");
    setStatusReporter(setSplashStatus); // startBackend 内部到「等待后端就绪」节点时推 wait_backend
    await startBackend();
    await startFrontend();
    configureSecurity();
    setSplashStatus("finalize");
    createMainWindow(getPorts().frontendPort);
    createTray(performQuit);
    registerExportIpc(ipcMain); // 简历下载导出（PDF/Markdown/HTML）
    registerApplyIpc(ipcMain); // 投递详情窗口（岗位页内嵌 WebContentsView，独立登录态）
    registerJobsIpc(ipcMain, getPorts); // 事件驱动抓岗位（kickCrawl：猎聘后端 + 前程无忧读 DOM）
  } catch (err) {
    logger.error("startup_failed", err);
    closeSplash(); // 失败先关 splash，避免它一直挂着
    dialog.showErrorBox("程序启动失败", err instanceof Error ? err.message : String(err));
    await stopAll();
    app.exit(1);
  }
}

// 后端绝对源同步供给：preload 在窗口加载时 sendSync 拿（api 层模块加载就要 baseURL，等不起异步）。
// 必须在 createMainWindow 之前注册。返回打包态 file:// 下业务请求要用的绝对地址。
ipcMain.on("backend-origin:get", (event) => {
  event.returnValue = `http://127.0.0.1:${config.backend.preferredPort}`;
});

// 前端读写 Electron 本地设置（端口/关闭行为/数据目录/日志，这条线的真相源）。
ipcMain.handle("electron-settings:get", () => settingsStore.getAll());
ipcMain.handle("electron-settings:set", (_event, patch) => {
  const sanitized = {};
  if (typeof patch?.backend_port === "number") sanitized.backend_port = patch.backend_port;
  if (patch?.close_action === "tray" || patch?.close_action === "quit") {
    sanitized.close_action = patch.close_action;
    config.window.closeAction = patch.close_action; // 关闭行为热生效，无需重启
  }
  if (typeof patch?.data_dir === "string" && patch.data_dir.trim()) {
    sanitized.data_dir = patch.data_dir.trim(); // 仅存，DB 迁移/生效在下次启动
  }
  if (typeof patch?.log_dir === "string" && patch.log_dir.trim()) {
    sanitized.log_dir = patch.log_dir.trim(); // 仅存，重启生效；即时更新 config 供日志组展示
    config.log.dir = patch.log_dir.trim();
  }
  if (typeof patch?.log_retention_days === "number") {
    const days = Math.round(patch.log_retention_days);
    if (days >= 1 && days <= 365) {
      sanitized.log_retention_days = days; // 仅存，重启生效
      config.log.retentionDays = days;
    }
  }
  return settingsStore.set(sanitized); // backend_port / data_dir / log_* 只存，重启后生效
});

// 立即清理日志：删日志目录下的按日期日志文件（后端 {date}.jsonl + Electron main-{date}.log）。
// 后端进程在跑时其 jsonl 句柄可能占着删不掉 → 删掉的计数、失败的跳过，不报错。
ipcMain.handle("logs:clear", () => {
  const logsDir = config.log.dir;
  let removed = 0;
  try {
    const entries = fs.readdirSync(logsDir, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isFile() || !/^(main-)?\d{4}-\d{2}-\d{2}(\.jsonl|\.log)$/.test(entry.name)) continue;
      try {
        fs.unlinkSync(path.join(logsDir, entry.name));
        removed += 1;
      } catch {
        /* 句柄占用删不掉，跳过 */
      }
    }
  } catch {
    /* 目录不存在/读失败：返回 0 */
  }
  logger.info("logs_cleared", { removed });
  return { removed };
});

// 日志统计：返回日志目录下按日期日志文件（与 logs:clear 同一批可删文件）的数量与总字节数。
// 设置页「立即清理」旁展示用。目录不存在/读失败 → 0。
ipcMain.handle("logs:stats", () => {
  const logsDir = config.log.dir;
  let fileCount = 0;
  let totalBytes = 0;
  try {
    const entries = fs.readdirSync(logsDir, { withFileTypes: true });
    for (const entry of entries) {
      if (!entry.isFile() || !/^(main-)?\d{4}-\d{2}-\d{2}(\.jsonl|\.log)$/.test(entry.name)) continue;
      try {
        const stat = fs.statSync(path.join(logsDir, entry.name));
        fileCount += 1;
        totalBytes += stat.size;
      } catch {
        /* 单文件 stat 失败跳过 */
      }
    }
  } catch {
    /* 目录不存在/读失败：返回 0 */
  }
  return { fileCount, totalBytes };
});

// 原生「选择文件夹」对话框：存储目录用。取消返回 null（前端保持原值）。
ipcMain.handle("dialog:pick-directory", async () => {
  const win = BrowserWindow.getFocusedWindow() || getMainWindow();
  const result = await dialog.showOpenDialog(win, {
    title: "选择数据目录",
    properties: ["openDirectory", "createDirectory"],
  });
  return result.canceled || result.filePaths.length === 0 ? null : result.filePaths[0];
});

// 关闭按钮行为：默认缩小到托盘（设置项 closeAction）。
// 点关闭 → 隐藏窗口、后端继续在托盘里跑；托盘右键「退出」才 performQuit 杀进程。
ipcMain.on("window-control", (event, action) => {
  const win = BrowserWindow.fromWebContents(event.sender);
  if (!win) return;
  if (action === "minimize") win.minimize();
  else if (action === "maximize") (win.isMaximized() ? win.unmaximize() : win.maximize());
  else if (action === "close") {
    if (config.window.closeAction === "quit") {
      // E2（2026-08-13）：单一退出入口 = performQuit。不再手动 win.destroy() 去
      // 间接触发 before-quit——performQuit 自己负责销毁窗口，直调即可（清晰、不网状触发）。
      performQuit();
    } else {
      win.hide(); // 默认：缩小到托盘
    }
  }
});

// 单实例锁。
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  // E3（2026-08-13）：拿不到锁 = 已有第一实例在跑。这里**直接退出**，绝不能走
  // app.quit()/performQuit——后者会触发 stopAll → killByPort，按端口反查把
  // **第一实例的后端**误杀（两个实例读同一端口设置）。本实例没起任何子进程，
  // 没有任何东西要清理，立刻 exit 即可。
  logger.warn("already_running_quitting");
  app.exit(0);
} else {
  app.on("second-instance", () => {
    const window = getMainWindow();
    if (window) {
      if (window.isMinimized()) window.restore();
      window.show();
      window.focus();
    }
  });
  app.whenReady().then(initialize);
}

app.on("before-quit", (event) => {
  if (!isQuitting) {
    event.preventDefault();
    performQuit();
  }
});

app.on("window-all-closed", () => {
  // Windows/Linux 留在托盘；不自动退出。
});

app.on("activate", () => {
  const window = getMainWindow();
  if (window) {
    window.show();
    window.focus();
  } else {
    createMainWindow(getPorts().frontendPort);
  }
});

app.on("will-quit", (event) => {
  if (!isQuitting) {
    event.preventDefault();
    performQuit();
  }
});

app.on("web-contents-created", (_event, webContents) => {
  webContents.on("will-navigate", (event, url) => {
    // 只拦主窗口（前端 React 页，通过 preload 标记区分）——防止 dev 模式下主窗口
    // 导航离开本地 dev server。apply-window 打开的第三方岗位页没有 preload，不拦；
    // 否则 job51 详情页加载后的二次导航（追加 timestamp 参数）会被误拦 → 白板。
    const type = webContents.getType();
    if (type !== "window" && type !== "browserView") return; // 非页面类型不管
    if (!webContents.getURL().startsWith("http://127.0.0.1")) {
      // 当前 URL 不是本地前端（第三方岗位页）→ 放行所有导航
      return;
    }
    const { frontendPort } = getPorts();
    const allowed = `http://127.0.0.1:${frontendPort}`;
    if (config.frontend.mode === "dev" && !url.startsWith(allowed)) {
      logger.warn("navigation_blocked", { url, allowed });
      event.preventDefault();
    }
  });
});

function handleSignal(signal) {
  logger.info(`received_signal_${signal}`);
  performQuit();
}
process.on("SIGTERM", handleSignal);
process.on("SIGINT", handleSignal);

// 异常退出兜底：进程无论如何要退出时，尽力同步补杀子进程树，
// 减少「崩溃/强杀导致的孤儿进程」（正常退出仍走 performQuit 的完整 stopAll）。
// E1（2026-08-13）：exit 事件里事件循环已停，async stopAll 的 await 核验/补杀
// 来不及执行——必须走 killAllSync 这条无 await 的纯同步路径（同步 taskkill + 端口反查）。
process.on("exit", () => {
  try {
    killAllSync();
  } catch {
    /* 退出阶段不抛错 */
  }
});

if (process.platform === "win32") {
  app.setAppUserModelId("com.careernova.app");
}
