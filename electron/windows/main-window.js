const { BrowserWindow } = require("electron");
const path = require("path");
const { PRELOAD_PATH, ASSETS_DIR, resolveFrontendDist } = require("../utils/paths");
const { logger } = require("../utils/logger");
const { config } = require("../config");
const { closeSplash } = require("../splash/splash-window");

/** @type {BrowserWindow | null} */
let mainWindow = null;

const READY_TO_SHOW_TIMEOUT_MS = 10000;

/**
 * 创建主窗口（无边框，自绘 TitleBar）。
 * 软切换：dev 载 vite server URL；build 载 dist/index.html。
 * @param {number} frontendPort dev 模式的前端端口（build 模式忽略）
 * @returns {BrowserWindow}
 */
function createMainWindow(frontendPort) {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.show();
    return mainWindow;
  }

  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 960,
    minHeight: 600,
    title: "Career Nova",
    icon: path.join(ASSETS_DIR, "icon.png"), // 任务栏/Alt-Tab/窗口图标（替换 Electron 默认）
    frame: false, // 自绘 TitleBar
    autoHideMenuBar: true,
    show: false,
    backgroundColor: "#f8f9fa", // 单主题 light 底色
    webPreferences: {
      preload: PRELOAD_PATH,
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
    },
  });

  let shown = false;
  const showWindow = () => {
    if (shown || !mainWindow || mainWindow.isDestroyed()) return;
    shown = true;
    // 主窗亮起即关启动窗（splash 的使命到此为止）。
    closeSplash();
    mainWindow.show();
    mainWindow.focus();
    logger.info("main_window_shown");
  };
  mainWindow.once("ready-to-show", showWindow);
  const fallbackTimer = setTimeout(() => {
    logger.warn("ready_to_show_fallback");
    showWindow();
  }, READY_TO_SHOW_TIMEOUT_MS);
  fallbackTimer.unref();

  mainWindow.on("closed", () => {
    clearTimeout(fallbackTimer);
    mainWindow = null;
  });

  mainWindow.webContents.setWindowOpenHandler(() => ({ action: "deny" }));

  if (config.frontend.mode === "build") {
    const distIndex = resolveFrontendDist();
    mainWindow.loadFile(distIndex).catch((err) => logger.error("load_dist_failed", err));
  } else {
    const frontendUrl = `http://127.0.0.1:${frontendPort}`;
    mainWindow.loadURL(frontendUrl).catch((err) => logger.error("load_url_failed", err));
  }

  return mainWindow;
}

/** @returns {BrowserWindow | null} */
function getMainWindow() {
  return mainWindow && !mainWindow.isDestroyed() ? mainWindow : null;
}

module.exports = { createMainWindow, getMainWindow };
