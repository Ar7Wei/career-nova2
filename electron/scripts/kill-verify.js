/**
 * 必杀路径验证：起后端 → taskkill /T 杀 → 核验端口释放 + 无树残留。
 * 用法: npm run verify:kill   （或 node electron/scripts/kill-verify.js）
 * 退出码 0 = 杀干净；1 = 有残留（必杀路径退化，需修）。
 *
 * 这是开发阶段 UV 启动的进程管理验证。打包后（exe）进程树形态不同，
 * 届时需对 exe 模式单独验证（见 config backend.mode）。
 *
 * 说明（D5 去留判断，2026-09-11）：本脚本不 import services/process-manager 的
 * killByPort，而是自带一份 pidsOnPort 实现——刻意为之，不是待收敛的重复：验证器
 * 若复用被测代码，被测逻辑写错时验证器跟着错，就验证不出东西了（拿秤验秤）。
 * 输出格式（✓/✗ 报告 + 退出码）是它作为验证器的核心价值，不宜降级成库函数。
 */
const { spawn, execFileSync } = require("child_process");
const path = require("path");
const net = require("net");

const PROJECT_ROOT = path.resolve(__dirname, "..", "..");
const PORT = Number(process.env.KILL_VERIFY_PORT || 8765);
const systemRoot = process.env.SystemRoot || "C:\\Windows";
const taskkill = path.join(systemRoot, "System32", "taskkill.exe");
const netstat = path.join(systemRoot, "System32", "netstat.exe");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function portListening(port) {
  return new Promise((resolve) => {
    const socket = new net.Socket();
    socket.setTimeout(800);
    const done = (ok) => {
      socket.destroy();
      resolve(ok);
    };
    socket.once("connect", () => done(true));
    socket.once("timeout", () => done(false));
    socket.once("error", () => done(false));
    socket.connect(port, "127.0.0.1");
  });
}

async function waitListening(port, timeoutMs = 25000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await portListening(port)) return true;
    await sleep(400);
  }
  return false;
}

function pidsOnPort(port) {
  try {
    const out = execFileSync(netstat, ["-ano"], { encoding: "utf8", windowsHide: true });
    const pids = new Set();
    for (const line of out.split(/\r?\n/)) {
      if (line.includes(`:${port}`) && /LISTENING/i.test(line)) {
        const pid = line.trim().split(/\s+/).pop();
        if (pid && /^\d+$/.test(pid) && pid !== "0") pids.add(pid);
      }
    }
    return [...pids];
  } catch {
    return [];
  }
}

function resolveUv() {
  const home = process.env.USERPROFILE || "";
  const cands = [path.join(home, ".local", "bin", "uv.exe"), path.join(home, ".cargo", "bin", "uv.exe")];
  const fs = require("fs");
  for (const c of cands) if (fs.existsSync(c)) return c;
  return "uv";
}

(async () => {
  console.log(`[1] spawn uv 后端 (shell:false, port ${PORT})...`);
  const uv = resolveUv();
  const proc = spawn(uv, ["run", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", String(PORT), "--no-access-log"], {
    cwd: PROJECT_ROOT,
    stdio: "ignore",
    shell: false,
    windowsHide: true,
  });
  const pid = proc.pid;
  console.log(`    spawn 记录的真身 PID = ${pid}`);

  const up = await waitListening(PORT);
  if (!up) {
    console.log(`    ✗ 后端 ${PORT} 未在超时内监听，无法验证`);
    try { execFileSync(taskkill, ["/F", "/T", "/PID", String(pid)], { stdio: "ignore", windowsHide: true }); } catch {}
    process.exit(1);
  }
  console.log(`[2] 后端已监听 ${PORT}（占用者 PID: ${pidsOnPort(PORT).join(",")}）`);

  console.log(`[3] taskkill /F /T /PID ${pid}（必杀路径）...`);
  try {
    const out = execFileSync(taskkill, ["/F", "/T", "/PID", String(pid)], { encoding: "utf8", windowsHide: true });
    const killed = (out.match(/terminated/gi) || []).length;
    console.log(`    连带终结 ${killed} 个进程`);
  } catch (e) {
    console.log(`    taskkill 返回非零（可能部分已退）: ${String(e).slice(0, 80)}`);
  }

  await sleep(800);
  console.log(`[4] 核验...`);
  const stillListening = await portListening(PORT);
  const survivors = pidsOnPort(PORT);

  if (!stillListening && survivors.length === 0) {
    console.log(`    ✓ 端口 ${PORT} 已释放，无残留 —— 必杀路径有效`);
    process.exit(0);
  }
  console.log(`    ✗ 残留：listening=${stillListening} 占用 PID=${survivors.join(",")}`);
  console.log(`    必杀路径退化，需检查进程树/补杀逻辑`);
  process.exit(1);
})();
