const { BrowserWindow } = require("electron");
const path = require("path");
const { ASSETS_DIR } = require("../utils/paths");
const { logger } = require("../utils/logger");
const { getMainWindow } = require("./main-window");

/**
 * 投递详情窗口：独立窗口内加载岗位详情页（真实平台页），供用户「先登录 → 再操作」。
 *
 * 这是投递执行（docs/design/apply.md §7）的地基：
 * - 每平台独立持久 session partition（登录态各存各的、互不串味、重启不丢）——
 *   这是 WebContentsView spike 验证过的关键结论（五平台级1渲染 + 级2读 DOM 全过）。
 * - 窗口 = 第三方页面的宿主；webContents 自带 executeJavaScript / did-navigate，
 *   即投递执行「读/写 DOM + 观察跳转」的全部能力（独立窗口无需 WebContentsView 手动 setBounds）。
 * - 同平台共用一个窗口：再点同平台岗位，复用窗口切换 URL（不重复开窗）。
 *
 * v1 只做「展示」；投递引擎（预填/点投递/捕捉反馈）等级3「点得动」验证后接入。
 */

// source_name → session partition。与 spike 验证的一致，两平台独立登录态（BOSS 已砍 2026-08-31）。
const SOURCE_PARTITIONS = {
  liepin: "persist:careernova-liepin",
  job51: "persist:careernova-51job",
};

// 各平台登录页 URL（登录入口用；登录完 cookie 落到对应 partition，抓取即生效）。
// 前程无忧真实登录页 = login.51job.com/login.php（之前 we.51job.com/pc/my/login 白屏，是错 URL）。
const PLATFORM_LOGIN_URLS = {
  liepin: "https://www.liepin.com/login/",
  job51: "https://login.51job.com/login.php?url=https%3A%2F%2Fwww.51job.com%2F",
};

// 登录态探测（随用随测，不轮询）：访问一个「必须登录才能停留」的页面，
// 若最终被甩到登录页（url 匹配 loginPattern）→ 未登录；否则已登录。
const LOGIN_PROBES = {
  liepin: {
    url: "https://www.liepin.com/user/",
    loginPattern: /^https:\/\/www\.liepin\.com\/?$/, // 未登录跳回首页
  },
  job51: {
    url: "https://we.51job.com/pc/my/myjob",
    loginPattern: /login\.51job\.com|passport/i,
  },
};

// 各平台登录页本身的 URL 特征（登录成功后跳离它 = 登录成功）。
const LOGIN_PAGE_PATTERNS = {
  liepin: /\/login\//,
  job51: /login\.51job\.com|passport/i,
};

// 各平台登录入口显示名（前端按钮文案 + 窗口标题）。
const PLATFORM_LOGIN_NAMES = {
  liepin: "猎聘",
  job51: "前程无忧",
};

/** @type {Map<number, BrowserWindow>} jobId → 窗口（一岗一窗，用于关窗标记后反查 destroy）。 */
const applyWindows = new Map();

// 统一缩放比率（2026-08-17 用户定稿）：所有招聘站固定 zoom=0.8，不设窗口宽度、不逐站算
// 比例。窗口宽 = 页面内容宽 × 0.8，由内容自动撑开（猎聘 1200→960、拉勾 1250→1000）。
const ZOOM = 0.8;
// 打开瞬间的初始窗口宽（加载完会被 fitWindowToContent 收到内容宽×0.8）。
const INITIAL_WIDTH = 1000;

// 注入第三方岗位页的滚动条样式（与 frontend/src/index.css 全局窄滚动条同款）：
// 底部横向滚动条不显示（height:0）；纵向 6px 圆角、默认透明、hover 淡入。
// 平台页自带系统粗滚动条，注入后统一成项目默认视觉。insertCSS 每次导航后需重注
//（did-finish-load 里做），正好覆盖「同平台复用窗口切 URL」的场景。
const SCROLLBAR_CSS = `
  ::-webkit-scrollbar { width: 6px; height: 0; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: transparent; border-radius: 999px; }
  *:hover::-webkit-scrollbar-thumb { background: rgba(15, 23, 42, 0.18); }
  ::-webkit-scrollbar-thumb:hover { background: rgba(15, 23, 42, 0.32); }
`;

/** 加载完成后量页面内容宽，把窗口收到「内容宽 × ZOOM」并固定 zoom=0.8。
 *
 *  时序坑（2026-08-17 实测）：猎聘/Boss 等 React SPA 在 did-finish-load 时内容未渲染完，
 *  scrollWidth 读到的是空页面宽。故延迟采样多次，读到稳定（连续两次相同）或达上限后一次性
 *  应用，避免设了 zoom 后再读数形成反馈环。固定宽布局下 scrollWidth 稳定，很快收敛。
 *
 *  兜底（2026-08-18）：scrollWidth 恒 0（页面空/未渲染）时 samples 也递增，避免死循环不 show。
 *  读不到有效宽就按默认宽 show，保证窗口不黑着。 */
function fitWindowToContent(win) {
  let best = 0;
  let stable = 0;
  let samples = 0;
  const MAX_SAMPLES = 6;
  const step = () => {
    win.webContents
      .executeJavaScript("document.documentElement.scrollWidth", true)
      .then((sw) => {
        const num = Number(sw);
        if (Number.isFinite(num) && num > 0) {
          if (num > best) {
            best = num;
            stable = 0;
          } else if (num === best) {
            stable += 1;
          }
          samples += 1;
        } else {
          // 读不到有效宽（页面空/未渲染）也计数，避免死循环——兜底按上次 best show。
          samples += 1;
        }
        if (stable >= 2 || samples >= MAX_SAMPLES) {
          const winWidth = Math.max(Math.round(best * ZOOM), INITIAL_WIDTH);
          win.webContents.setZoomFactor(ZOOM);
          win.setContentSize(winWidth, win.getContentBounds().height);
          // 就位后显示（同平台复用窗口切 URL 时 fit 会再跑，show 幂等）。
          if (!win.isVisible()) {
            win.show();
            win.focus();
          }
          logger.info("apply_window_fit", { source: win._cnSource, contentWidth: best, zoom: ZOOM, windowWidth: winWidth });
        } else {
          setTimeout(step, 350);
        }
      })
      .catch((e) => {
        logger.warn("apply_window_fit_read_failed", { error: String(e) });
        // 读失败也兜底 show，不黑窗。
        if (!win.isVisible()) {
          win.setZoomFactor(ZOOM);
          win.show();
          win.focus();
        }
      });
  };
  step();
}

/** 登录态探测（导航探测，随用随测）：隐藏窗口访问「需登录才能停留」的页面，
 *  看最终 URL 是否被甩到登录页。甩过去 = 未登录；停留 = 已登录。
 *  这是唯一可靠的登录态判断——cookie 标志不可靠（BOSS 登录后 wbg 仍为 0、
 *  猎聘登录后 liepin_login_valid 仍为 0，都会误判）。 */
async function probeLoginStatus(source) {
  const probe = LOGIN_PROBES[source];
  if (!probe) return false;
  const win = new BrowserWindow({
    show: false,
    width: 1000,
    height: 800,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      partition: SOURCE_PARTITIONS[source],
    },
  });
  try {
    await win.loadURL(probe.url);
    await new Promise((r) => setTimeout(r, 2500)); // 等 SPA 重定向落定
    const finalUrl = win.webContents.getURL();
    const loggedIn = !probe.loginPattern.test(finalUrl);
    logger.info("apply_login_probe", { source, finalUrl, loggedIn });
    return loggedIn;
  } catch (err) {
    logger.warn("apply_login_probe_failed", { source, error: String(err) });
    return false;
  } finally {
    if (!win.isDestroyed()) win.destroy();
  }
}

/**
 * 打开某岗位的投递详情窗口（一岗一窗，不复用）。
 * @param {{source: string, url: string, title?: string, jobId?: number, isLogin?: boolean, onLoginChanged?: (source: string) => void}} job
 */
function openJobDetailWindow(job) {
  const source = String(job?.source || "");
  const url = String(job?.url || "").trim();
  if (!url) {
    logger.warn("apply_window_empty_url", { source });
    return;
  }
  const partition = SOURCE_PARTITIONS[source] || "persist:careernova-generic";
  const isLogin = Boolean(job?.isLogin);
  const onLoginChanged = job?.onLoginChanged;
  const jobId = job?.jobId != null ? Number(job.jobId) : null;

  const win = new BrowserWindow({
    width: INITIAL_WIDTH,
    height: 820,
    minWidth: 400, // 不锁死宽度：内容宽×0.8 决定最终宽，只给个下限防极端
    minHeight: 600,
    title: job?.title ? `${job.title} · 投递` : "投递",
    icon: path.join(ASSETS_DIR, "icon.png"), // 任务栏图标（替换 Electron 默认）
    autoHideMenuBar: true,
    backgroundColor: "#f8f9fa",
    show: false,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      partition, // 每平台独立登录态（登录态靠 partition 持久化，与窗口无关）
    },
  });
  win._cnSource = source; // 供 fitWindowToContent 日志用（避免闭包传参）
  win._cnJobId = jobId; // 关窗标记反查用
  win._cnIsApplyDetail = !isLogin; // 只有岗位详情窗口拦关窗弹标记框；登录窗口不拦

  // 缩放从第一帧就固定 0.8（加载前设），页面不会先按 1.0 显示再被缩——消除缩放闪变。
  // setZoomFactor 是同步 void（无 Promise），别挂 .catch。
  win.webContents.on("dom-ready", () => {
    win.webContents.setZoomFactor(ZOOM);
  });

  // 关窗拦截（岗位详情窗口）：点原生 X → preventDefault 保持开着 → 发事件给前端
  // 弹标记框 → 用户选完前端调 closeJobDetail 才真正 destroy。
  // 登录窗口（isLogin）不拦——登录成功自动 close() 走这条，拦了会死循环。
  if (!isLogin) {
    win.on("close", (event) => {
      if (win._cnClosingByFrontend) return; // 前端 closeJobDetail 触发，放行
      event.preventDefault();
      const sender = getMainWindow();
      if (sender && !sender.isDestroyed()) {
        sender.show();
        sender.focus();
        sender.webContents.send("apply:detail-closed", { jobId, source, title: job?.title || "" });
      } else {
        // 主窗口都没了：直接销毁（无人收标记，别卡着窗口）
        win._cnClosingByFrontend = true;
        win.destroy();
      }
      logger.info("apply_window_close_intercepted", { source, jobId });
    });
  }

  // 跳转日志：观察「跳登录/验证网关/投递成功页」等（投递反馈收集的地基）。
  // 登录窗口：登录成功后平台会跳离登录页——检测到就回传「登录成功」并关窗。
  // 用 did-navigate（整页跳转）+ did-navigate-in-page（SPA 内路由跳转）双监听，
  // 因为部分登录页是 React SPA，登录成功是 SPA 内跳转（不触发 did-navigate）。
  const checkLoginSuccess = (navigatedUrl) => {
    logger.info("apply_window_navigate", { source, url: navigatedUrl });
    if (!isLogin || !onLoginChanged) return;
    const pattern = LOGIN_PAGE_PATTERNS[source];
    // 判定：还停在登录页（匹配登录页特征）→ 未成功；跳离登录页 → 登录成功。
    if (pattern && !pattern.test(navigatedUrl)) {
      logger.info("apply_login_success", { source, to: navigatedUrl });
      onLoginChanged(source);
      // 登录成功 → 自动关掉登录窗口（BOSS 登录后不自动跳转会僵住）
      if (!win.isDestroyed()) win.close();
    }
  };
  win.webContents.on("did-navigate", (_event, navigatedUrl) => checkLoginSuccess(navigatedUrl));
  win.webContents.on("did-navigate-in-page", (_event, navigatedUrl) => checkLoginSuccess(navigatedUrl));
  // 加载失败兜底：ERR_FAILED(-2)（job51 详情页偶发限流）重载一次；
  // 再失败才 show 空窗兜底，避免用户看到永久白板。
  let loadFailedReloaded = false;
  win.webContents.on("did-fail-load", (_event, code, desc, failedUrl, isMainFrame) => {
    if (!isMainFrame) return;
    logger.error("apply_window_load_failed", { source, code, desc, url: failedUrl });
    if (!loadFailedReloaded && code === -2) {
      // ERR_FAILED：job51 对高频请求的临时限流，退避重载一次通常能过。
      loadFailedReloaded = true;
      logger.warn("apply_window_load_failed_reload", { source, code });
      setTimeout(() => {
        if (!win.isDestroyed()) win.webContents.reload();
      }, 2000);
      return;
    }
    // 兜底：主框架加载失败也保证窗口出现（不卡后台黑窗）。
    if (!win.isVisible()) {
      win.show();
      win.focus();
    }
  });
  // 加载完成后：量内容宽 → 窗口收到 contentWidth×0.8 → zoom 固定 0.8。
  win.webContents.on("did-finish-load", () => {
    // 注入项目滚动条样式（每次导航重注，覆盖同平台复用窗口切 URL 的场景）。
    win.webContents.insertCSS(SCROLLBAR_CSS).catch((e) => logger.warn("apply_window_css_failed", { source, error: String(e) }));
    fitWindowToContent(win);
  });

  // 不再 ready-to-show 显示：交给 fitWindowToContent 就位后再 show()，
  // 避免「先原生尺寸显示 → 再缩 → 再换滚动条」的可见转变。
  win.on("closed", () => {
    if (jobId != null) applyWindows.delete(jobId);
  });

  if (jobId != null) applyWindows.set(jobId, win);
  win.loadURL(url).catch((err) => logger.error("apply_window_load_failed", { source, url, error: String(err) }));
  logger.info("apply_window_opened", { source, url, jobId });
}

/** 前端标记完投递结果后调用：真正销毁对应岗位窗口（跳过 close 拦截）。 */
function closeJobDetailWindow(jobId) {
  const win = applyWindows.get(Number(jobId));
  if (!win || win.isDestroyed()) {
    logger.warn("apply_window_close_not_found", { jobId });
    return { ok: false };
  }
  win._cnClosingByFrontend = true;
  win.destroy();
  return { ok: true };
}

/**
 * 注册投递详情窗口的 IPC（与 services/export.js 同构：业务抽 services，不堆 main.js）。
 * @param {import("electron").IpcMain} ipcMain
 */
function registerApplyIpc(ipcMain) {
  ipcMain.handle("apply:open-detail", (_event, job) => {
    openJobDetailWindow(job);
    return { ok: true };
  });

  // 前端标记完投递结果后调用：真正销毁岗位窗口（跳过 close 拦截）。
  ipcMain.handle("apply:close-detail", (_event, jobId) => {
    return closeJobDetailWindow(jobId);
  });

  // 登录入口：打开某平台登录页（复用独立窗口 + partition 登录态）。
  // 先探测登录态——已登录就返回 loggedIn:true 不再弹窗（否则已登录用户点入口
  // 又看到一个登录表单，让人以为"没登录"或"登录失效"）。
  ipcMain.handle("apply:open-login", async (event, source) => {
    const src = String(source || "");
    const url = PLATFORM_LOGIN_URLS[src];
    if (!url) {
      logger.warn("apply_login_unknown_source", { source: src });
      return { ok: false, error: "unknown source" };
    }
    const alreadyLoggedIn = await probeLoginStatus(src);
    if (alreadyLoggedIn) {
      logger.info("apply_login_skip_already_logged_in", { source: src });
      return { ok: true, loggedIn: true };
    }
    const sender = event.sender; // 发起请求的前端 webContents（登录成功回推给它）
    openJobDetailWindow({
      source: src,
      url,
      title: `登录 ${PLATFORM_LOGIN_NAMES[src] || src}`,
      isLogin: true,
      onLoginChanged: (s) => {
        if (!sender.isDestroyed()) {
          sender.send("apply:login-changed", { source: s });
        }
      },
    });
    return { ok: true, loggedIn: false };
  });

  // 登录态探测（随用随测）：隐藏窗口访问「需登录才能停留」的页面，
  // 看最终 URL 是否被甩到登录页。甩过去 = 未登录；停留 = 已登录。
  ipcMain.handle("apply:login-status", async (_event, source) => {
    const src = String(source || "");
    return { loggedIn: await probeLoginStatus(src) };
  });
}

module.exports = { registerApplyIpc, openJobDetailWindow, closeJobDetailWindow };
