const fs = require("fs");
const path = require("path");

/**
 * 日志目录迁移：log_dir 改变后，把旧目录的按日期日志文件复制到新目录。
 *
 * 为什么放 Electron：log_dir 是 Electron 线设置（settings.json），且日志目录在启动时
 * 就决定（logger 写哪、注入后端 LOG_DIR），迁移也在启动前做。
 *
 * 幂等规则（绝不覆盖，照 db-migrate.js）：
 *  - 新旧路径一致 → 不迁移
 *  - 旧目录无日志文件 / 目录不存在 → 不迁移
 *  - 目标已有同名文件 → 跳过不覆盖（可能是用户手动放的）
 *  - 过期日志（超保留天数）不迁移——反正会被自动清理，搬过去也白搬
 * 迁移是纯复制，不删旧文件（用户可回滚/清理）。
 *
 * @param {string} configuredLogDir 设置里的 log_dir
 * @param {string} currentLogDir 上次实际写日志的目录（settings.json current_log_dir）
 * @param {number} retentionDays 保留天数（过期日志不迁移）
 * @returns {{ copied: number, reason: string }} copied=迁移文件数
 */

/** 按日期日志文件名：main-2026-08-09.log（Electron）或 2026-08-09.jsonl（后端）。 */
const DATE_LOG_RE = /^(main-)?\d{4}-\d{2}-\d{2}(\.jsonl|\.log)$/;

function migrateLogs(configuredLogDir, currentLogDir, retentionDays) {
  try {
    const targetDir = path.resolve(configuredLogDir || "");
    const sourceDir = path.resolve(currentLogDir || "");
    if (!targetDir || !sourceDir || targetDir === sourceDir) {
      return { copied: 0, reason: "same-path" };
    }
    let entries;
    try {
      entries = fs.readdirSync(sourceDir, { withFileTypes: true });
    } catch {
      return { copied: 0, reason: "no-source" };
    }
    const files = entries.filter((e) => e.isFile() && DATE_LOG_RE.test(e.name));
    if (files.length === 0) return { copied: 0, reason: "no-source" };

    // 过期文件不迁移（保留天数<=0 = 不过滤）
    const cutoff = retentionDays > 0 ? Date.now() - retentionDays * 24 * 60 * 60 * 1000 : 0;

    fs.mkdirSync(targetDir, { recursive: true });
    let copied = 0;
    for (const f of files) {
      const src = path.join(sourceDir, f.name);
      const dst = path.join(targetDir, f.name);
      try {
        if (cutoff && fs.statSync(src).mtimeMs < cutoff) continue; // 过期跳过
        if (fs.existsSync(dst)) continue; // 不覆盖
        fs.copyFileSync(src, dst);
        copied += 1;
      } catch {
        /* 单文件失败跳过，不阻断整体 */
      }
    }
    return copied > 0
      ? { copied, reason: "copied", from: sourceDir, to: targetDir }
      : { copied: 0, reason: "target-exists", from: sourceDir, to: targetDir };
  } catch (err) {
    return { copied: 0, reason: "error", error: String(err?.message || err) };
  }
}

module.exports = { migrateLogs, DATE_LOG_RE };
