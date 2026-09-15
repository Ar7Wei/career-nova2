const { BrowserWindow, screen } = require("electron");
const path = require("path");
const { logger } = require("../utils/logger");

/**
 * 托盘自绘菜单窗口（替换 Menu.buildFromTemplate 的老式 Win32 菜单）。
 * 独立无边框透明窗口，加载 menu.html（纯白卡片风，贴合应用 .card）。
 *
 * focusable:false（2026-08-23）：菜单 show() 不抢焦点——Windows 托盘溢出浮层
 * 收起的一个触发就是「焦点被抢走」，不抢焦点则浮层有机会保持不缩回 ^。
 * 代价：窗口从未有焦点 → blur 永不触发，故关闭机制不能依赖 blur，改为：
 *   1. 再点一次托盘右键 → toggle 关
 *   2. 点菜单项 → 主动关（menu-preload 发 IPC）
 *   3. 鼠标移出菜单 → menu.html mouseleave 延迟 3s 后发 IPC 关（移回取消）
 */

/** 菜单窗口尺寸（与 menu.html 内容一致：两项×34px + 分隔 9px + 上下 padding 6px×2）。
 * 加项/改字需同步 menu.html 与这里的高度，避免透明窗裁切或留白。 */
const MENU_WIDTH = 180;
const MENU_HEIGHT = 89;
/** 菜单与托盘图标/屏幕边缘的间距（px）。 */
const EDGE_GAP = 8;

/** @type {BrowserWindow | null} */
let menuWindow = null;

function createMenuWindow() {
  if (menuWindow && !menuWindow.isDestroyed()) return menuWindow;

  menuWindow = new BrowserWindow({
    width: MENU_WIDTH,
    height: MENU_HEIGHT,
    show: false,
    frame: false,
    transparent: true, // 透明窗：圆角外露透明，呈现 .menu 的圆角
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    // alwaysOnTop:true 只等价于默认 "normal" 层级，压不过 Win10 托盘溢出浮层（浮层在 Win10
    // 层级更高，菜单被挡在它后面；Win11 浮层层级低所以正常）。Win 上 "pop-up-menu" 是专为
    // 右键菜单设计的更高层级。displayId:-1 沿用主屏。show() 时再重申一次（见 showTrayMenu）。
    alwaysOnTop: true,
    focusable: false, // 不抢焦点：避免托盘溢出浮层因失焦而收起
    hasShadow: false, // 透明窗 + 系统阴影在 Windows 会黑边；阴影由 CSS 画（此处无 box-shadow，纯白卡片靠细边）
    webPreferences: {
      preload: path.join(__dirname, "menu-preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  menuWindow.loadFile(path.join(__dirname, "menu.html")).catch((err) => logger.error("tray_menu_load_failed", err));
  // focusable:false 无 blur 事件，故这里不挂 blur 关闭（关闭改走右键 toggle / 菜单项 / mouseleave）。
  menuWindow.on("closed", () => {
    menuWindow = null;
  });

  return menuWindow;
}

/**
 * 按托盘图标位置算菜单弹出坐标（DIP）。
 * 默认弹在图标上方；上方放不下则弹下方；左右贴边时 clamp 回屏幕可用区内。
 * @param {{x:number,y:number,width:number,height:number}} trayBounds
 * @returns {{x:number,y:number}}
 */
function positionFor(trayBounds) {
  // trayBounds 异常（罕见）时兜底到鼠标所在屏的可用区，不崩溃。
  if (!trayBounds || !trayBounds.width || !trayBounds.height) {
    const cursor = screen.getCursorScreenPoint();
    const wa = screen.getDisplayMatching(cursor).workArea;
    return { x: wa.x + wa.width - MENU_WIDTH - EDGE_GAP, y: wa.y + EDGE_GAP };
  }

  const wa = screen.getDisplayMatching(trayBounds).workArea;

  // 水平：菜单居中于托盘图标，再 clamp 进屏幕可用区（任务栏占位已由 workArea 排除）
  let x = Math.round(trayBounds.x + trayBounds.width / 2 - MENU_WIDTH / 2);
  x = Math.min(Math.max(x, wa.x + EDGE_GAP), wa.x + wa.width - MENU_WIDTH - EDGE_GAP);

  // 垂直：默认图标上方；上方不足则落到下方（任务栏在底部时图标贴近 workArea 底，走上方）
  let y = trayBounds.y - MENU_HEIGHT - EDGE_GAP;
  if (y < wa.y) y = trayBounds.y + trayBounds.height + EDGE_GAP;
  y = Math.min(Math.max(y, wa.y + EDGE_GAP), wa.y + wa.height - MENU_HEIGHT - EDGE_GAP);

  return { x, y };
}

/** 菜单当前是否可见。 */
function isTrayMenuVisible() {
  return !!menuWindow && !menuWindow.isDestroyed() && menuWindow.isVisible();
}

/** 显示菜单（右键托盘调用）。 */
function showTrayMenu(trayBounds) {
  const win = createMenuWindow();
  const { x, y } = positionFor(trayBounds);
  win.setPosition(x, y, false);
  // 抬层级压过 Win10 托盘溢出浮层：pop-up-menu 是 Win 上右键菜单的专用高层级（高于 normal
  // alwaysOnTop）。每次 show 都重申——浮层浮起时可能抢过顶层，重建最稳。moveTop 顶到同级最前。
  win.setAlwaysOnTop(true, "pop-up-menu");
  win.moveTop();
  win.show(); // focusable:false 下 show 不抢焦点，仅显示
  logger.info("tray_menu_shown", { x, y });
}

function hideTrayMenu() {
  if (menuWindow && !menuWindow.isDestroyed()) menuWindow.hide();
}

/** 右键托盘 toggle：已显示则关，否则开。 */
function toggleTrayMenu(trayBounds) {
  if (isTrayMenuVisible()) hideTrayMenu();
  else showTrayMenu(trayBounds);
}

/** 退出/销毁托盘时连菜单窗口一起清。 */
function destroyTrayMenu() {
  if (menuWindow && !menuWindow.isDestroyed()) {
    menuWindow.destroy();
    menuWindow = null;
  }
}

module.exports = { showTrayMenu, hideTrayMenu, toggleTrayMenu, isTrayMenuVisible, destroyTrayMenu };
