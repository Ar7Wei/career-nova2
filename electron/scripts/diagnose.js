/**
 * 进程管理诊断：实测「起 Electron → 彻底关闭」后哪些进程还活着。
 * 用 WMI 查进程树（PID/PPID/命令行），看清 uv/cmd/uvicorn/vite 的父子关系。
 * 用法: node electron/scripts/diagnose.js
 */
const { spawn, execFileSync, execSync } = require("child_process");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** 查与本应用相关的进程（uv/uvicorn/vite/node/electron），返回 PID/PPID/命令行。 */
function queryProcesses() {
  const ps = `
    Get-CimInstance Win32_Process |
      Where-Object { $_.CommandLine -match 'uvicorn|vite|app.main|electron' -or $_.Name -match '^(uv|node|python|electron)' } |
      Select-Object ProcessId, ParentProcessId, Name, CommandLine |
      ConvertTo-Json
  `;
  try {
    const out = execFileSync("powershell", ["-NoProfile", "-Command", ps], {
      encoding: "utf8",
      windowsHide: true,
      maxBuffer: 1024 * 1024 * 8,
    });
    const parsed = JSON.parse(out || "[]");
    const arr = Array.isArray(parsed) ? parsed : [parsed];
    return arr.map((p) => ({
      pid: p.ProcessId,
      ppid: p.ParentProcessId,
      name: p.Name,
      cmd: (p.CommandLine || "").slice(0, 90),
    }));
  } catch (e) {
    return [{ error: String(e).slice(0, 200) }];
  }
}

function printProcesses(label) {
  console.log(`\n=== ${label} ===`);
  const procs = queryProcesses();
  if (procs.length === 0) {
    console.log("(无相关进程)");
    return new Set();
  }
  const pids = new Set();
  for (const p of procs) {
    if (p.error) {
      console.log("查询失败:", p.error);
      continue;
    }
    pids.add(p.pid);
    console.log(`PID=${p.pid} PPID=${p.ppid} ${p.name} :: ${p.cmd}`);
  }
  return pids;
}

(async () => {
  console.log("步骤 0：起 Electron 前的基线");
  const baseline = printProcesses("基线（起前）");

  console.log("\n步骤 1：起 Electron（后台）...");
  const appProc = spawn("cmd", ["/c", "npm", "start"], {
    cwd: require("path").resolve(__dirname, ".."),
    stdio: "ignore",
    windowsHide: true,
  });
  console.log("npm start wrapper PID =", appProc.pid);

  await sleep(15000); // 等后端+前端+窗口起来
  const running = printProcesses("运行中（起后 15s）");

  console.log("\n步骤 2：彻底关闭（杀 npm start 进程树，模拟退出）...");
  try {
    execSync(`taskkill /F /T /PID ${appProc.pid}`, { stdio: "ignore", windowsHide: true });
  } catch {}
  // 也尝试杀 electron 主进程（窗口进程）
  try {
    execSync(`taskkill /F /IM electron.exe /T`, { stdio: "ignore", windowsHide: true });
  } catch {}

  await sleep(4000);
  const after = printProcesses("关闭后 4s");

  console.log("\n=== 诊断结论 ===");
  const orphans = [...after].filter((pid) => !baseline.has(pid));
  if (orphans.length === 0) {
    console.log("✓ 关闭后无新增存活进程，进程管理干净。");
  } else {
    console.log(`✗ 关闭后仍有 ${orphans.length} 个进程活着（疑似孤儿）:`, orphans.join(", "));
    console.log("这些就是 v1 那种『看着关了其实没关』的残留。看上面 PPID 链条定位孤儿源头。");
  }
  process.exit(0);
})();
