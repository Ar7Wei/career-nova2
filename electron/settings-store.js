const fs = require("fs");
const path = require("path");
const { app } = require("electron");

/**
 * Electron 本地设置（真相源之一）：启动前就要读、且归 Electron 主进程管的项。
 * 存 userData/settings.json。项：
 *  - backend_port：后端固定端口（Electron 拉起后端前就要读，故不能放后端 SQLite/.env）
 *  - close_action：关闭按钮行为 tray|quit（纯窗口行为，与后端无关）
 *  - data_dir：应用数据目录（启动前就要知道数据放哪、注入后端 DATABASE_URL，
 *    故也不能放后端 SQLite——那存在鸡生蛋：DB 路径由 data_dir 决定，但 data_dir 存在 DB 里）。
 *    一个目录 = 一份完整数据快照（业务库 + checkpoint + originals/），见 db-migrate.js。
 *  - current_data_dir：上次**实际**用的数据目录（数据迁移的「从哪迁到哪」参照）。空 = 未物化。
 *  - log_dir / log_retention_days / current_log_dir：日志线（同构，current_log_dir 为迁移参照）。
 * 敏感配置（API key）与用户偏好（语言/模型等）都在后端 SQLite settings 表，不经 Electron。
 */

// data_dir 默认值：开发态 = 项目根 data 目录（老用户零迁移：默认即当前 DB 所在）；
// 打包态 = userData/data——__dirname 在打包后落进只读安装目录（asar/Program Files），
// 写 DB 会炸，故切到可写的 userData（ADR 0021）。
function defaultDataDir() {
  if (app.isPackaged) return path.join(app.getPath("userData"), "data");
  return path.join(__dirname, "..", "data");
}

const DEFAULTS = {
  backend_port: 8765,
  close_action: "tray",
  data_dir: defaultDataDir(),
  // 上次实际用的数据目录：数据迁移的"从哪迁到哪"参照（同 current_log_dir）。
  // 空 = 尚无记录，main.js 解析为默认 data 目录并【写回】物化（避免暴露绝对路径为出厂默认）。
  current_data_dir: "",
  // 日志目录：空 = 用默认（main.js 解析为 userData/logs 并写回，物化后前端可见）。
  log_dir: "",
  // 日志保留天数：启动时按它清过期日志（后端经 env LOG_RETENTION_DAYS 注入）。
  log_retention_days: 30,
  // 上次实际写日志的目录：日志迁移的"从哪迁到哪"参照（data_dir 迁移靠默认 data 目录，
  // 但 log 旧位置会随用户改 log_dir 而变，故需要显式记住）。
  current_log_dir: "",
};

let _filePath = null;
let _cache = null;

function init(userDataDir) {
  _filePath = path.join(userDataDir, "settings.json");
}

function _read() {
  if (_cache) return _cache;
  let data = {};
  try {
    let text = fs.readFileSync(_filePath, "utf8");
    // 容错：某些编辑器/工具（含 PowerShell Set-Content -Encoding utf8）会写 UTF-8 BOM，
    // JSON.parse 遇 BOM 直接失败 → 配置全丢默认值。去掉 BOM 再解析。
    if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
    data = JSON.parse(text);
  } catch {
    data = {};
  }
  _cache = { ...DEFAULTS, ...data };
  return _cache;
}

function get(key) {
  return _read()[key];
}

function getAll() {
  return { ..._read() };
}

function set(patch) {
  const next = { ..._read(), ...patch };
  _cache = next;
  try {
    fs.mkdirSync(path.dirname(_filePath), { recursive: true });
    fs.writeFileSync(_filePath, JSON.stringify(next, null, 2), "utf8");
  } catch {
    /* 写失败不致命，下次启动用默认 */
  }
  return next;
}

module.exports = { init, get, getAll, set, DEFAULTS };
