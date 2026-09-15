const { Tray, nativeImage, ipcMain } = require("electron");
const path = require("path");
const { logger } = require("../utils/logger");
const { getMainWindow } = require("../windows/main-window");
const { getPorts } = require("../services/process-manager");
const { ASSETS_DIR } = require("../utils/paths");
const { toggleTrayMenu, hideTrayMenu, destroyTrayMenu } = require("./tray-menu-window");

/** @type {Tray | null} */
let tray = null;

/** @type {(() => void) | null} 已注册的菜单动作监听器（重复 createTray 时先解绑，避免叠监听） */
let actionListener = null;

/** @type {(() => void) | null} 已注册的「鼠标移出延迟关闭」监听器（同上，对称解绑） */
let closeListener = null;

/** 托盘图标：用真 logo（electron/assets/tray.png），替换旧的代码画绿方块占位。 */
function buildTrayIcon() {
  const trayPath = path.join(ASSETS_DIR, "tray.png");
  return nativeImage.createFromPath(trayPath);
}

function showWindow() {
  const window = getMainWindow();
  if (window) {
    window.show();
    window.focus();
    return;
  }
  const { createMainWindow } = require("../windows/main-window");
  const ports = getPorts();
  createMainWindow(ports.frontendPort);
}

function createTray(onQuit) {
  if (tray && !tray.isDestroyed()) return;
  tray = new Tray(buildTrayIcon());
  tray.setToolTip("Career Nova");

  // 右键 → 自绘菜单 toggle（已显示则关，否则开；替换老式 Win32 Menu.buildFromTemplate）
  tray.on("right-click", () => {
    toggleTrayMenu(tray.getBounds());
  });
  // 双击 → 直接打开主界面（沿用习惯）
  tray.on("double-click", showWindow);

  // 菜单项动作（自绘菜单 preload 桥发 IPC）：open 打开主界面 / quit 退出。
  // 点击任意菜单项后立即关菜单（不再等 blur——focusable:false 无 blur）。
  // 先解绑旧监听防重复注册（createTray 幂等 + 退出重建场景）。
  if (actionListener) ipcMain.removeListener("tray-menu:action", actionListener);
  actionListener = (_event, id) => {
    hideTrayMenu();
    if (id === "quit") onQuit();
    else showWindow();
  };
  ipcMain.on("tray-menu:action", actionListener);

  // 鼠标移出菜单（menu.html mouseleave 延迟 3s 触发）→ 关菜单。focusable:false 下无 blur，靠这兜底。
  if (closeListener) ipcMain.removeListener("tray-menu:close", closeListener);
  closeListener = () => hideTrayMenu();
  ipcMain.on("tray-menu:close", closeListener);

  logger.info("tray_created");
}

function destroyTray() {
  if (tray && !tray.isDestroyed()) {
    tray.destroy();
    tray = null;
  }
  if (actionListener) {
    ipcMain.removeListener("tray-menu:action", actionListener);
    actionListener = null;
  }
  if (closeListener) {
    ipcMain.removeListener("tray-menu:close", closeListener);
    closeListener = null;
  }
  destroyTrayMenu();
}

module.exports = { createTray, destroyTray };
