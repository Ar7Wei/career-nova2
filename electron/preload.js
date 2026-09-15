const { contextBridge, ipcRenderer } = require("electron");

// 后端绝对源（file:// 打包态下业务请求必须用绝对地址，相对 /api 会解析成 file:///api 失败）。
// 用 sendSync 同步拿：api 层模块加载时就要 baseURL，等不起异步 invoke。
// 主进程持 "backend-origin:get" 同步返回 http://127.0.0.1:{port}（端口是设置项，不写死）。
const backendOrigin = ipcRenderer.sendSync("backend-origin:get");

// 暴露给前端的最小安全 API：自绘 TitleBar 窗口控制 + Electron 本地设置读写。
contextBridge.exposeInMainWorld("desktop", {
  /** 后端绝对源（打包态 file:// 下业务请求用；dev 浏览器无此桥、仍走 vite proxy /api）。 */
  backendOrigin,
  /** @param {"minimize"|"maximize"|"close"} action */
  window: (action) => ipcRenderer.send("window-control", action),
  platform: process.platform,
  /** Electron 本地设置（端口/关闭行为/数据目录，这条线的真相源）。浏览器下不可用。 */
  settings: {
    get: () => ipcRenderer.invoke("electron-settings:get"),
    set: (patch) => ipcRenderer.invoke("electron-settings:set", patch),
  },
  /** 弹原生「选择文件夹」对话框（存储目录用）。返回选中路径，取消返回 null。 */
  pickDirectory: () => ipcRenderer.invoke("dialog:pick-directory"),
  /** 日志维护：立即清理日志目录下的按日期日志文件，返回删除数。 */
  logs: {
    clear: () => ipcRenderer.invoke("logs:clear"),
    /** 统计日志目录下按日期日志文件的数量与总字节数（清理按钮旁展示用）。 */
    stats: () => ipcRenderer.invoke("logs:stats"),
  },
  /** 下载简历：PDF（printToPDF）/ Markdown / HTML。返回 {saved, canceled?, filePath?}。 */
  exportResume: (payload) => ipcRenderer.invoke("resume:export", payload),
  /** 打开岗位投递详情窗口（独立窗口 + 每平台独立登录态）。job: { source, url, title, jobId }。 */
  openJobDetail: (job) => ipcRenderer.invoke("apply:open-detail", job),
  /** 标记完投递结果后真正关闭岗位窗口（跳过 close 拦截）。 */
  closeJobDetail: (jobId) => ipcRenderer.invoke("apply:close-detail", jobId),
  /** 订阅岗位详情窗口关闭事件（用户点 X → 主进程拦下 → 发此事件让前端弹标记框）。返回取消订阅函数。 */
  onDetailClosed: (cb) => {
    const handler = (_e, payload) => cb(payload);
    ipcRenderer.on("apply:detail-closed", handler);
    return () => ipcRenderer.removeListener("apply:detail-closed", handler);
  },
  /** 打开某平台登录页（登录入口）。source: liepin/job51。 */
  openLogin: (source) => ipcRenderer.invoke("apply:open-login", source),
  /** 查某平台登录态（导航探测：访问需登录页，看是否被甩到登录页）。返回 { loggedIn }。 */
  getLoginStatus: (source) => ipcRenderer.invoke("apply:login-status", source),
  /** 订阅登录变更事件（登录窗口跳离登录页触发）。返回取消订阅函数。 */
  onLoginChanged: (cb) => {
    const handler = (_e, payload) => cb(payload?.source);
    ipcRenderer.on("apply:login-changed", handler);
    return () => ipcRenderer.removeListener("apply:login-changed", handler);
  },
  /** 事件驱动抓岗位（kickCrawl）：后端猎聘一轮 + 前程无忧读 DOM 一轮。浏览器下不可用。 */
  kickCrawl: () => ipcRenderer.invoke("jobs:kick-crawl"),
});
