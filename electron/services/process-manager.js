const { spawn, execFileSync } = require("child_process");
const http = require("http");
const path = require("path");
const { logger } = require("../utils/logger");
const { findFreePort, waitForPort, isPortFree } = require("../utils/port");
const {
  PROJECT_ROOT,
  FRONTEND_DIR,
  resolveUvBinary,
  resolveViteEntry,
} = require("../utils/paths");
const { config } = require("../config");

/** @typedef {{ process: import("child_process").ChildProcess | null, port: number }} ServiceInfo */

/** @type {ServiceInfo} */
const backend = { process: null, port: 0 };
/** @type {ServiceInfo} */
const frontend = { process: null, port: 0 };

const START_TIMEOUT_MS = 30000;
const KILL_GRACE_MS = 5000;
// 优雅关闭的超时：请求后端自杀 → 等它自己退出。超时则退回 taskkill /F 硬杀。
const GRACEFUL_SHUTDOWN_TIMEOUT_MS = 10000;

/** @type {((key: string) => void) | null} 启动阶段文案回调（splash 用），main.js 注入。可选。 */
let statusReporter = null;

/** 注册启动阶段文案回调（splash-window 的 setSplashStatus）。 */
function setStatusReporter(fn) {
  statusReporter = fn;
}

/** 上报启动阶段（若已注册回调）。内部用，不抛错。 */
function reportStatus(key) {
  try {
    if (statusReporter) statusReporter(key);
  } catch {
    /* 文案上报失败不影响启动 */
  }
}

function useProcessGroup() {
  return process.platform !== "win32";
}

/**
 * 起后端。软切换：uv（开发）↔ exe（日后分发）。
 * 端口固定（设置项）：被占则明确报错，不悄悄换口（换口是孤儿温床）。
 * Windows 下直接 spawn uv 真实二进制（shell:false），spawn PID 即 uv 真身——
 * 这样 taskkill /T 杀的进程树就是完整那棵，不会因 cmd 壳隔离而漏子进程。
 * @returns {Promise<void>}
 */
async function startBackend() {
  const port = config.backend.preferredPort;
  if (!(await isPortFree(port))) {
    throw new Error(
      `backend port ${port} is in use. change it in settings or free the port. (fixed port, no auto-increment)`
    );
  }
  backend.port = port;
  logger.info("backend_starting", { mode: config.backend.mode, port, data_dir: config.backend.dataDir });

  // data_dir 真实接管 DB 路径：绝对化为 sqlite 连接串，注入后端环境变量（Pydantic Settings 优先）。
  const databaseUrl = config.backend.dataDir
    ? `sqlite+aiosqlite:///${path.join(path.resolve(config.backend.dataDir), "career_nova.db").replace(/\\/g, "/")}`
    : undefined;
  // log_dir / log_retention_days 同样注入后端：后端 jsonl 与 Electron 日志落同一目录、同一保留期。
  const logDir = config.log.dir;
  const logRetention = config.log.retentionDays;
  const backendEnv = {
    ...process.env,
    NO_COLOR: "1",
    // 后端 stdout 走 UTF-8：日志含中文，避免 Windows GBK 控制台 UnicodeEncodeError（兜底，
    // 后端 logging.py 里也已 reconfigure stdout 为 UTF-8，双保险）。
    PYTHONIOENCODING: "utf-8",
    ...(databaseUrl ? { DATABASE_URL: databaseUrl } : {}),
    ...(logDir ? { LOG_DIR: logDir } : {}),
    ...(logRetention ? { LOG_RETENTION_DAYS: String(logRetention) } : {}),
  };

  if (config.backend.mode === "exe") {
    const exePath = path.isAbsolute(config.backend.exePath)
      ? config.backend.exePath
      : path.join(PROJECT_ROOT, config.backend.exePath);
    backend.process = spawn(exePath, ["--host", "127.0.0.1", "--port", String(port)], {
      cwd: PROJECT_ROOT,
      stdio: "pipe",
      env: backendEnv,
      shell: false,
      windowsHide: true,
      detached: useProcessGroup(),
    });
  } else {
    // 默认 uv：遵循「后端一律 UV 运行」。uv run 会用项目 .venv。
    const uv = resolveUvBinary();
    backend.process = spawn(
      uv,
      ["run", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(port), "--no-access-log"],
      {
        cwd: PROJECT_ROOT,
        stdio: "pipe",
        env: backendEnv, // 关键：不走 cmd 壳，spawn PID 即 uv 真身
        shell: false,
        windowsHide: true,
        detached: useProcessGroup(),
      }
    );
  }

  backend.process.on("error", (err) => {
    logger.error("backend_spawn_failed", { mode: config.backend.mode, error: err.message });
  });
  attachLogs(backend.process, "backend");
  logger.info("backend_spawned", { pid: backend.process.pid, port });

  // 后端已 spawn，进入冷启动等待（lifespan 建表/装配 checkpointer/对账，数秒）——
  // 这段最长，splash 文案切到「等待后端就绪」让用户知道在干啥。
  reportStatus("wait_backend");
  const ready = await waitForPort(port, START_TIMEOUT_MS);
  if (!ready) throw new Error(`backend did not listen on port ${port} within ${START_TIMEOUT_MS}ms`);
  logger.info("backend_ready", { port });
}

/**
 * 起前端。软切换：dev 起 vite dev server；build 不起（窗口直接载 dist）。
 * @returns {Promise<void>}
 */
async function startFrontend() {
  if (config.frontend.mode === "build") {
    // build 模式不起 dev server，窗口直接加载 dist/index.html（见 main-window）。
    frontend.port = 0;
    logger.info("frontend_build_mode_no_dev_server");
    return;
  }

  const port = await findFreePort(config.frontend.devPort);
  frontend.port = port;
  logger.info("frontend_starting", { port });

  const viteEntry = resolveViteEntry();
  frontend.process = spawn(process.execPath, [viteEntry, "--host", "127.0.0.1", "--port", String(port)], {
    cwd: FRONTEND_DIR,
    stdio: "pipe",
    env: {
      ...process.env,
      CAREERNOVA_BACKEND_PORT: String(backend.port),
      VITE_BACKEND_ORIGIN: `http://127.0.0.1:${backend.port}`,
      FORCE_COLOR: "0",
    },
    shell: false,
    windowsHide: true,
    detached: useProcessGroup(),
  });

  frontend.process.on("error", (err) => {
    logger.error("frontend_spawn_failed", { entry: viteEntry, error: err.message });
  });
  attachLogs(frontend.process, "frontend");

  const ready = await waitForPort(port, START_TIMEOUT_MS);
  if (!ready) throw new Error(`frontend did not listen on port ${port} within ${START_TIMEOUT_MS}ms`);
  logger.info("frontend_ready", { port });
}

function attachLogs(proc, label) {
  proc.stdout?.on("data", (chunk) => logger.info(`${label} stdout`, chunk.toString().trimEnd()));
  proc.stderr?.on("data", (chunk) => logger.warn(`${label} stderr`, chunk.toString().trimEnd()));
}

/** 查进程是否仍存活（不杀，仅探测）。 */
function isAlive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

/**
 * Windows 下按端口反查占用者 PID 并补杀。
 * 注意用 netstat.exe 全路径——裸 `netstat` 在某些会话（PATH 缺 System32）下不可用。
 * 用于兜底：spawn 记录的是父 PID，若真子进程脱离了进程树，仅杀父 PID 可能漏真身。
 */
function killByPort(port, name) {
  if (process.platform !== "win32" || !port) return;
  const systemRoot = process.env.SystemRoot || "C:\\Windows";
  const netstatExe = path.join(systemRoot, "System32", "netstat.exe");
  const taskkillExe = path.join(systemRoot, "System32", "taskkill.exe");
  try {
    const out = execFileSync(netstatExe, ["-ano"], { encoding: "utf8", windowsHide: true });
    const pids = new Set();
    for (const line of out.split(/\r?\n/)) {
      if (!line.includes(`:${port}`) || !/LISTENING/i.test(line)) continue;
      const pid = line.trim().split(/\s+/).pop();
      if (pid && /^\d+$/.test(pid) && pid !== "0") pids.add(pid);
    }
    for (const pid of pids) {
      logger.warn("killing_orphan_by_port", { port, pid, name });
      try {
        execFileSync(taskkillExe, ["/F", "/T", "/PID", String(pid)], { stdio: "ignore", windowsHide: true });
      } catch {
        /* 单个失败不阻断 */
      }
    }
  } catch {
    /* 无占用者或查询失败，不致命 */
  }
}

/**
 * 请求后端优雅关闭（best-effort）：调 `POST /api/v1/system/shutdown`，等它自己退。
 *
 * 为什么不能直接 taskkill 后端：后端在 lifespan 关闭段里折叠 WAL（`checkpoint.py` /
 * `dispose_engine()`）。`taskkill /F` 直接把进程砍掉，那段永远不跑 → 主库停在空壳、
 * 数据悬在 `-wal` 里。先请求它优雅退，让它把该做的收尾做完。
 *
 * 超时/失败一律退回硬杀（调用方接着跑 taskkill）——「关干净」永远优先于「关得优雅」。
 *
 * @returns {Promise<boolean>} 后端是否在超时内自行退出。
 */
async function gracefulShutdownBackend(port) {
  if (!port) return false;
  const ok = await shutdownRequest(port);
  if (!ok) {
    logger.info("backend_graceful_shutdown_unavailable", { port });
    return false;
  }
  logger.info("backend_graceful_shutdown_requested", { port });
  const proc = backend.process;
  const exited = proc ? await waitForExit(proc, GRACEFUL_SHUTDOWN_TIMEOUT_MS) : await waitPortReleased(port);
  if (exited) logger.info("backend_graceful_shutdown_done", { port });
  else logger.warn("backend_graceful_shutdown_timeout", { port }); // 退回硬杀
  return exited;
}

/** POST /api/v1/system/shutdown。连不上/非 2xx → false（不抛）。 */
function shutdownRequest(port) {
  return new Promise((resolve) => {
    const req = http.request(
      { host: "127.0.0.1", port, path: "/api/v1/system/shutdown", method: "POST", timeout: 3000 },
      (res) => {
        res.resume(); // 丢弃响应体
        resolve(res.statusCode >= 200 && res.statusCode < 300);
      }
    );
    req.on("error", () => resolve(false));
    req.on("timeout", () => {
      req.destroy();
      resolve(false);
    });
    req.end();
  });
}

/** 轮询端口直到释放（无 spawn 记录时的兜底判据）。 */
async function waitPortReleased(port, timeoutMs = GRACEFUL_SHUTDOWN_TIMEOUT_MS) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await isPortFree(port)) return true;
    await new Promise((r) => setTimeout(r, 300));
  }
  return false;
}

/** 结束子进程并**确认杀干净**——关闭流程的责任：杀不净不算完。
 *
 * 必杀路径（实测）：从 spawn 记录的真身 PID 起 `taskkill /F /T`，连带整棵
 * 进程树（uv→uvicorn→python→worker 4 层）一次杀净。但这只在树还连着时成立；
 * 真身可能在崩溃/强杀时脱链成孤儿（换 PPID 存活），所以杀后必须核验：
 *   1. 父 PID 是否真死；
 *   2. 端口是否已释放——仍占说明有脱链真身，按端口反查补杀（兜底）。
 * 两步都干净才算完。dev/packaged 的进程树形态不同（见 config backend.mode），
 * 但核验逻辑一致——都是靠"端口是否释放"这个客观事实收口，与树形态无关。
 *
 * graceful=true（仅后端）：先请求它优雅退（折叠 WAL 等收尾），超时才 taskkill。
 */
async function killService(service, name, port, graceful = false) {
  const proc = service.process;
  const pid = proc && !proc.killed ? proc.pid : null;

  // 优雅路径：先请后端自己退（折叠 WAL），退干净就不必动刀；没退成则落到下面的硬杀。
  if (graceful && port) await gracefulShutdownBackend(port);

  // 优雅退出后进程可能仍被 handle 引用（Windows 上有僵尸态），以 Node 的 exitCode 为准：
  // 收到 exit 事件即有值 → 已退，跳过硬杀。脱链孤儿由下面的「核验 2·端口反查」兜。
  const alreadyExited = proc ? proc.exitCode !== null : false;

  if (pid && !alreadyExited) {
    logger.info(`stopping_${name}`, { pid, port });
    try {
      if (process.platform === "win32") {
        execFileSync(
          path.join(process.env.SystemRoot || "C:\\Windows", "System32", "taskkill.exe"),
          ["/F", "/T", "/PID", String(pid)],
          { stdio: "ignore", windowsHide: true }
        );
      } else {
        try {
          process.kill(-pid, "SIGTERM");
        } catch {
          proc.kill("SIGTERM");
        }
        const exited = await waitForExit(proc, KILL_GRACE_MS);
        if (!exited) {
          try {
            process.kill(-pid, "SIGKILL");
          } catch {
            proc.kill("SIGKILL");
          }
        }
      }
    } catch (err) {
      logger.error(`stop_${name}_failed`, err);
    }
  }

  // 核验 1：父 PID 是否真死。
  await new Promise((r) => setTimeout(r, 300));
  if (pid && isAlive(pid)) {
    logger.warn(`${name}_parent_still_alive`, { pid });
  }

  // 核验 2：端口是否释放。仍占 = 有脱链真身，按端口补杀（脱链后只能靠端口找）。
  if (port) {
    const freed = await isPortFree(port);
    if (!freed) {
      logger.warn(`${name}_port_still_occupied_killing`, { port });
      killByPort(port, name);
      // 补杀后再确认一次。
      const freedAfter = await isPortFree(port);
      if (!freedAfter) {
        logger.error(`${name}_port_still_occupied_after_kill`, { port });
      }
    }
  }

  logger.info(`${name}_stopped`, { pid, port });
  service.process = null;
}

function waitForExit(proc, timeoutMs) {
  return new Promise((resolve) => {
    if (proc.killed) {
      resolve(true);
      return;
    }
    const timer = setTimeout(() => resolve(false), timeoutMs);
    proc.once("exit", () => {
      clearTimeout(timer);
      resolve(true);
    });
  });
}

async function stopAll() {
  await killService(frontend, "frontend", frontend.port);
  await killService(backend, "backend", backend.port, true); // 后端先优雅退（折叠 WAL）
}

/**
 * E1（2026-08-13）：纯同步杀路径——专为 `process.on("exit")` 兜底设计。
 *
 * exit 事件里事件循环已停，`stopAll()` 是 async——它的 `await setTimeout/isPortFree`
 * 核验 + 异步补杀**来不及执行**（Promise 不会 settle）。所以 exit 里必须走这条
 * 无 await 的同步路径：同步 taskkill 杀记录的 PID + 同步 netstat 反查端口补杀脱链真身。
 * 只覆盖「崩溃/强杀/异常退出」兜底；正常退出仍走 performQuit 的完整异步 stopAll。
 */
function killAllSync() {
  for (const [service, name] of [[frontend, "frontend"], [backend, "backend"]]) {
    const proc = service.process;
    const pid = proc && !proc.killed ? proc.pid : null;
    if (pid && process.platform === "win32") {
      try {
        execFileSync(
          path.join(process.env.SystemRoot || "C:\\Windows", "System32", "taskkill.exe"),
          ["/F", "/T", "/PID", String(pid)],
          { stdio: "ignore", windowsHide: true }
        );
      } catch {
        /* 退出阶段单点失败不阻断 */
      }
    } else if (pid) {
      try {
        process.kill(-pid, "SIGKILL");
      } catch {
        try {
          proc.kill("SIGKILL");
        } catch {
          /* 已死 */
        }
      }
    }
    // 端口补杀：脱链真身（换 PPID 成孤儿）只能靠端口找——killByPort 本身是同步的。
    if (service.port) killByPort(service.port, name);
  }
}

/** @returns {{ backendPort: number, frontendPort: number }} */
function getPorts() {
  return { backendPort: backend.port, frontendPort: frontend.port };
}

module.exports = { startBackend, startFrontend, stopAll, killAllSync, getPorts, gracefulShutdownBackend, setStatusReporter };
