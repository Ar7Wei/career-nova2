const { BrowserWindow } = require("electron");
const path = require("path");
const { logger } = require("../utils/logger");

/**
 * 启动窗（splash）：启动早期弹出的小窗，显示「正在干什么 + 不确定进度条」，
 * 避免后端冷启动（lifespan 建表/装配 checkpointer/对账，数秒）期间用户对着黑屏干等。
 *
 * 生命周期：initialize 一进关键路径就 createSplash() 亮起；主窗口 ready-to-show 后
 * 由 main.js 调 closeSplash() 关。阶段文案经 setSplashStatus(key) 推送（splash.html 翻中文）。
 *
 * 透明无边框小窗，居中；不抢任务栏、不可聚焦（纯展示，无交互）。样式与 tray/menu.html
 * 同源（半透明白 + 细边 + 圆角 + 柔和投影的玻璃质感）。
 */

/** 窗口尺寸（与 splash.html 卡片内容一致）。 */
const SPLASH_WIDTH = 380;
const SPLASH_HEIGHT = 300;

/** @type {BrowserWindow | null} */
let splashWindow = null;

function createSplash() {
  if (splashWindow && !splashWindow.isDestroyed()) return splashWindow;

  splashWindow = new BrowserWindow({
    width: SPLASH_WIDTH,
    height: SPLASH_HEIGHT,
    show: false, // ready-to-show 再亮，避免白屏闪烁
    frame: false,
    transparent: true, // 透明窗：圆角外露，呈现卡片圆角（同 tray 菜单思路）
    resizable: false,
    movable: true,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    alwaysOnTop: false,
    focusable: false, // 纯展示窗：不抢焦点
    center: true,
    backgroundColor: "#00000000", // 透明底（transparent:true 时需显式透明，防某些平台白底）
    webPreferences: {
      preload: path.join(__dirname, "splash-preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  splashWindow.loadFile(path.join(__dirname, "splash.html")).catch((err) => logger.error("splash_load_failed", err));
  splashWindow.once("ready-to-show", () => {
    if (splashWindow && !splashWindow.isDestroyed()) {
      splashWindow.show();
      logger.info("splash_shown");
    }
  });
  splashWindow.on("closed", () => {
    splashWindow = null;
  });

  return splashWindow;
}

/** 推送阶段文案（key 由 splash.html 翻成中文）。窗口未建/已毁则忽略。 */
function setSplashStatus(key) {
  if (splashWindow && !splashWindow.isDestroyed()) {
    splashWindow.webContents.send("splash:status", key);
  }
}

/** 关闭启动窗（主窗口亮起后调用）。 */
function closeSplash() {
  if (splashWindow && !splashWindow.isDestroyed()) {
    splashWindow.close(); // 触发 "closed" → splashWindow=null
    splashWindow = null;
  }
}

module.exports = { createSplash, setSplashStatus, closeSplash };
