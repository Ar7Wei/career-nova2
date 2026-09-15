const { contextBridge, ipcRenderer } = require("electron");

// 启动窗（splash）的最小安全桥：只暴露「订阅阶段文案」一个能力。
// 主进程在启动关键节点发 "splash:status"（payload 为阶段 key），splash.html 翻成中文显示。
contextBridge.exposeInMainWorld("splash", {
  /** @param {(key: string) => void} cb 订阅阶段文案变更。 */
  onStatus: (cb) => {
    const handler = (_event, key) => cb(key);
    ipcRenderer.on("splash:status", handler);
  },
});
