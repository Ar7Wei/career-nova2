const { contextBridge, ipcRenderer } = require("electron");

// 托盘自绘菜单的最小安全桥：菜单项点击发动作 id；鼠标移出延迟 3s 后发 close。
contextBridge.exposeInMainWorld("trayMenu", {
  /** @param {"open" | "quit"} id */
  action: (id) => ipcRenderer.send("tray-menu:action", id),
  /** 鼠标移出菜单（menu.html mouseleave 延迟 3s 触发）→ 关闭菜单。 */
  close: () => ipcRenderer.send("tray-menu:close"),
});
