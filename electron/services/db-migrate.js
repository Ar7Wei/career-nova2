const fs = require("fs");
const path = require("path");
/**
 * 应用数据迁移：data_dir 改变后，把旧位置的全部应用数据复制到新位置。
 *
 * 为什么放 Electron：data_dir 是 Electron 线设置（settings.json），且数据放哪由
 * DATABASE_URL 决定——启动后端前就知道，迁移也在启动后端前做。
 *
 * data_dir 的语义 = **应用数据目录**（不是"业务库所在目录"）：一个目录 = 一份完整
 * 数据快照（拷一个目录即整份搬家/备份）。故迁移覆盖三块，缺一不可：
 *  - career_nova.db              业务库（简历/版本/资料集/settings 表/聊天原文）
 *  - checkpoints.db[.-wal/-shm]  LangGraph checkpointer：agent 工作记忆 + 编排挂起态。
 *    派生自 DB 同目录（app/graphs/checkpoint.py），**不是独立设置项**；WAL 由
 *    AsyncSqliteSaver.setup() 主动打开（langgraph-checkpoint-sqlite aio.py），
 *    故必须连 -wal/-shm 一起搬——只搬主库会丢 WAL 里未 checkpoint 的对话。
 *  - originals/                  上传原件（PDF/HTML）。文件名按版本号隔离
 *    （v{n}_{name}，见 app/repositories/originals.py），跨库版本号仍唯一，整目录搬安全。
 *
 * 幂等规则（绝不覆盖）：
 *  - 新旧路径一致 → 整体跳过
 *  - 源不存在 → 该项不迁移（首次启动或本就无数据）
 *  - 目标已存在 → 该项不迁移（可能是用户手动放的新数据，绝不覆盖）
 *  - DB 与 checkpoint 分项判定：业务库已在目标位置时，checkpoint/originals 仍会被
 *    补齐（它们未必同步搬过）
 * 迁移是纯复制，不删旧文件（用户可回滚/清理）。
 *
 * @returns {{ copied: boolean, results: Array<{name: string, copied: boolean, reason: string}> }}
 *   copied=true 表示至少搬了一项。
 */

const DB_FILENAME = "career_nova.db";
const CHECKPOINT_FILENAME = "checkpoints.db";
const ORIGINALS_DIRNAME = "originals";
// checkpoint WAL 伴生文件：与主库同前缀，缺一不可。
const CHECKPOINT_SIDECARS = ["-wal", "-shm"];

function dbPath(dataDir) {
  return path.join(dataDir, DB_FILENAME);
}

/** 复制单个文件，保持「绝不覆盖」语义。 */
function copyFileIfAbsent(src, dst) {
  if (!fs.existsSync(src)) return { copied: false, reason: "no-source" };
  if (fs.existsSync(dst)) return { copied: false, reason: "target-exists" };
  fs.mkdirSync(path.dirname(dst), { recursive: true });
  fs.copyFileSync(src, dst);
  return { copied: true, reason: "copied" };
}

/** 复制一个目录里的全部文件（不递归子目录，与 log-migrate 同粒度）。 */
function copyDirIfAbsent(srcDir, dstDir) {
  let entries;
  try {
    entries = fs.readdirSync(srcDir, { withFileTypes: true });
  } catch {
    return { copied: false, reason: "no-source" };
  }
  const files = entries.filter((e) => e.isFile());
  if (files.length === 0) return { copied: false, reason: "no-source" };
  fs.mkdirSync(dstDir, { recursive: true });
  let copied = 0;
  for (const f of files) {
    try {
      // 目标同名文件不覆盖（可能是用户手动放的）。
      if (copyFileIfAbsent(path.join(srcDir, f.name), path.join(dstDir, f.name)).copied) copied += 1;
    } catch {
      /* 单文件失败跳过，不阻断整体 */
    }
  }
  return copied > 0 ? { copied: true, reason: "copied" } : { copied: false, reason: "target-exists" };
}

/**
 * 启动前迁移应用数据。不抛错（迁移失败不阻断启动，后端 ensure_data_dir 会兜底建新库）。
 *
 * @param {string} configuredDataDir 设置里的 data_dir（可能相对路径，先绝对化）
 * @param {string} currentDataDir 上次**实际**用的 data_dir（settings.json current_data_dir）。
 *   必须传真实的上次位置，不能写死默认 data 目录——否则第二次改目录会从过期的源搬，
 *   丢掉中间那次搬家之后的新数据。
 */
function migrateDatabase(configuredDataDir, currentDataDir) {
  try {
    const targetDir = path.resolve(configuredDataDir);
    const sourceDir = path.resolve(currentDataDir);

    // 路径一致 → 无事可做（三项都在原地）。
    if (targetDir === sourceDir) {
      return { copied: false, results: [{ name: "all", copied: false, reason: "same-path" }] };
    }

    // 业务库（单文件）。
    const db = copyFileIfAbsent(dbPath(sourceDir), dbPath(targetDir));

    // checkpoint 主库 + WAL 伴生文件：一起搬，缺一份都是坏库。
    const checkpointResults = [CHECKPOINT_FILENAME, ...CHECKPOINT_SIDECARS.map((s) => CHECKPOINT_FILENAME + s)].map((f) => {
      const r = copyFileIfAbsent(path.join(sourceDir, f), path.join(targetDir, f));
      return { name: f, copied: r.copied, reason: r.reason };
    });

    // 上传原件目录。
    const originals = copyDirIfAbsent(path.join(sourceDir, ORIGINALS_DIRNAME), path.join(targetDir, ORIGINALS_DIRNAME));

    const results = [
      { name: DB_FILENAME, copied: db.copied, reason: db.reason },
      ...checkpointResults,
      { name: `${ORIGINALS_DIRNAME}/`, copied: originals.copied, reason: originals.reason },
    ];
    return { copied: results.some((r) => r.copied), results, from: sourceDir, to: targetDir };
  } catch (err) {
    // 迁移失败不阻断启动：新位置会建空库，数据仍在旧位置（用户可手动处理）。
    return { copied: false, results: [], reason: "error", error: String(err?.message || err) };
  }
}

module.exports = { migrateDatabase, dbPath, DB_FILENAME, CHECKPOINT_FILENAME, ORIGINALS_DIRNAME };
