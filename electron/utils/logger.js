const fs = require("fs");
const path = require("path");
const { app } = require("electron");
const { config } = require("../config");

/** 日志文件名日期前缀正则：main-2026-08-09.log 或 2026-08-09.jsonl。 */
const DATE_LOG_RE = /^(main-)?\d{4}-\d{2}-\d{2}(\.jsonl|\.log)$/;

/** 落盘前需要脱敏的模式。 */
const SENSITIVE_PATTERNS = [
  /(api[_-]?key|apikey)\s*[:=]\s*["']?[\w-]{8,}["']?/gi,
  /(auth[_-]?token|access[_-]?token|refresh[_-]?token)\s*[:=]\s*["']?[\w-]{8,}["']?/gi,
  /(password|passwd|pwd)\s*[:=]\s*["']?[^"'\s]+["']?/gi,
  /authorization\s*:\s*bearer\s+[\w-]+/gi,
];

function sanitize(text) {
  return SENSITIVE_PATTERNS.reduce((acc, p) => acc.replace(p, "[REDACTED]"), text);
}

/** 主进程简易文件日志（避免散落 console.log）。按日期分文件 + 保留天数清理。 */
class Logger {
  constructor() {
    this.logPath = null;
    this.buffer = [];
    this.flushTimer = null;
    this.enableStdout = true;
    if (app.isReady()) {
      this._initPath();
    } else {
      app.once("ready", () => this._initPath());
    }
  }

  /** 主进程日志路径初始化：main.js 在 config.log.dir 就绪后调用（ready 事件回调先于
   *  initialize，可能用了默认目录；配置好后再 init 一次切到真实目录）。可重复调用。 */
  init() {
    this._initPath();
  }

  _initPath() {
    try {
      const logsDir = config.log.dir || app.getPath("logs");
      fs.mkdirSync(logsDir, { recursive: true });
      this.logsDir = logsDir;
      this.logPath = this._todayLogPath();
      if (app.isPackaged) this.enableStdout = false;
      // 启动即清一次过期日志（后端 jsonl + Electron 按日期 log 一并清）
      this._purgeOldLogs();
    } catch {
      this.logPath = null;
    }
  }

  /** 今天的日志文件路径（按日期分文件）。 */
  _todayLogPath() {
    const d = new Date();
    const y = d.getFullYear();
    const m = String(d.getMonth() + 1).padStart(2, "0");
    const day = String(d.getDate()).padStart(2, "0");
    return path.join(this.logsDir, `main-${y}-${m}-${day}.log`);
  }

  /** 清理早于保留天数的按日期日志文件（*.jsonl + main-*.log 通吃）。失败跳过不报错。 */
  _purgeOldLogs() {
    if (!this.logsDir || !(config.log.retentionDays > 0)) return;
    const cutoff = Date.now() - config.log.retentionDays * 24 * 60 * 60 * 1000;
    let removed = 0;
    try {
      const entries = fs.readdirSync(this.logsDir, { withFileTypes: true });
      for (const entry of entries) {
        if (!entry.isFile() || !DATE_LOG_RE.test(entry.name)) continue;
        const full = path.join(this.logsDir, entry.name);
        try {
          if (fs.statSync(full).mtimeMs < cutoff) {
            fs.unlinkSync(full);
            removed += 1;
          }
        } catch {
          /* 删不掉（句柄占用等）跳过，下次再清 */
        }
      }
    } catch {
      /* 读目录失败不致命 */
    }
    if (removed > 0) {
      this._log("INFO", `purged_old_logs count=${removed} retention_days=${config.log.retentionDays}`);
    }
  }

  _log(level, message, detail) {
    const ts = new Date().toISOString();
    const detailText = detail !== undefined ? ` ${this._formatDetail(detail)}` : "";
    const line = sanitize(`[${ts}] [${level}] ${message}${detailText}`);
    // 日志全量走 UTF-8（后端 structlog 含中文，原样转发）。现代终端（VS Code / Windows
    // Terminal）默认 UTF-8，可正常显示中文；不再做 GBK→'?' 替换（那会让终端日志全变 '?'）。
    // 完整内容仍以 UTF-8 落 main-{date}.log 文件。
    if (this.enableStdout) {
      process.stdout.write(`${line}\n`);
    }
    if (this.logPath) {
      this.buffer.push(line);
      this._scheduleFlush();
    }
  }

  _formatDetail(detail) {
    if (detail instanceof Error) return detail.stack || detail.message;
    if (typeof detail === "string") return detail;
    try {
      return JSON.stringify(detail);
    } catch {
      return String(detail);
    }
  }

  _scheduleFlush() {
    if (this.flushTimer) return;
    this.flushTimer = setTimeout(() => {
      this.flushTimer = null;
      this._flush();
    }, 250);
    this.flushTimer.unref();
  }

  _flush() {
    if (!this.logPath || this.buffer.length === 0) return;
    // 日期翻转：跨天自动切到新文件（顺带清一次过期日志）。
    const today = this._todayLogPath();
    if (today !== this.logPath) {
      this.logPath = today;
      this._purgeOldLogs();
    }
    const lines = this.buffer.splice(0);
    try {
      // 显式 utf8：Windows 默认编码是 GBK，不指定会把日志里的中文写成乱码。
      fs.appendFileSync(this.logPath, `${lines.join("\n")}\n`, "utf8");
    } catch {
      /* 日志失败不致命 */
    }
  }

  /** 立即把缓冲落盘（退出前调用，避免尾部日志丢失）。 */
  flush() {
    if (this.flushTimer) {
      clearTimeout(this.flushTimer);
      this.flushTimer = null;
    }
    this._flush();
  }

  info(m, d) {
    this._log("INFO", m, d);
  }
  warn(m, d) {
    this._log("WARN", m, d);
  }
  error(m, d) {
    this._log("ERROR", m, d);
  }
}

module.exports = { logger: new Logger() };
