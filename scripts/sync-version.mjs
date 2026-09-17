#!/usr/bin/env node
/**
 * 版本号同步：根 package.json 是唯一真相源（ADR 0022）。
 *
 * 版本号有 5 个消费点，读取时机不同（构建时 / 运行时），所以要复制到 5 个文件：
 *   - electron/package.json        electron-builder 构建时读（决定 Setup.exe 文件名、exe 属性）
 *   - app/core/config.py           后端运行时读（/health、/、OpenAPI docs）
 *   - frontend/package.json        当前无人读，同步是为了不留漂移的坑
 *   - pyproject.toml               uv 构建时读，与打包无关，同步是为了编号一致
 *   - uv.lock                      uv 的 --frozen 会校验它与 pyproject 一致，不同步会挂 CI
 *
 * 为什么不让 CI 直接改、根文件只当摆设：CI 的改动只活在 runner 内存里，不 commit。
 * 那样「高版本号构建的实际内容」与你本地那份就脱节了——tag 指向的 commit 里
 * package.json 写的可能还是旧号。真相源只有落在你本地、能被 review、能被 git 记录，
 * 才算数。CI 只负责「把这一份分发出去」。
 *
 * 用法：
 *   node scripts/sync-version.mjs            从根 package.json 读版本，分发到 4 个文件
 *   node scripts/sync-version.mjs 0.2.0      指定版本，同时改写根 package.json 本身
 *
 * 幂等：内容没变就不写盘（mtime 不变），可安全重复跑。
 */

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// 测试用：SYNC_VERSION_ROOT 覆写到临时沙盒，避免单测真改仓库文件。
const ROOT = process.env.SYNC_VERSION_ROOT
  ? resolve(process.env.SYNC_VERSION_ROOT)
  : resolve(dirname(fileURLToPath(import.meta.url)), "..");

/** 版本号格式铁律：必须 x.y.z（与 CI 的 tag 校验、semver 对齐）。 */
export const VERSION_RE = /^\d+\.\d+\.\d+$/;

export function assertValidVersion(v) {
  if (!VERSION_RE.test(v)) {
    throw new Error(`版本号格式不对: ${v}（要求 x.y.z，如 0.2.0）`);
  }
  return v;
}

/** 改写 JSON 文本里的顶层 "version" 字段，保留原缩进与键序（不用 JSON.parse 重排）。 */
export function bumpJsonVersion(text, version, file = "package.json") {
  const re = /^(\s*)"version"(\s*:\s*)"[^"]*"/m;
  if (!re.test(text)) throw new Error(`${file} 里找不到 "version" 字段`);
  return text.replace(re, `$1"version"$2"${version}"`);
}

/** 改写 Pydantic settings 里的 VERSION 代码默认值。 */
export function bumpConfigVersion(text, version) {
  const re = /^(\s*)VERSION: str = "[^"]*"/m;
  if (!re.test(text)) throw new Error("app/core/config.py 里找不到 VERSION 字段");
  return text.replace(re, `$1VERSION: str = "${version}"`);
}

/**
 * 改写 uv.lock 里本项目的 version（`name = "career-nova"` 那条）。
 *
 * 这条不做的话 CI 会直接挂：CI 跑 `uv sync --frozen`，而 --frozen 要求锁文件
 * 与 pyproject.toml 完全一致。改了 pyproject 的 version 却不更新锁，
 * uv 会报 "lockfile needs to be updated" 并以非零码退出。
 * 只改版本行、不重解依赖——找 `name = "career-nova"` 块后紧跟的 version。
 */
export function bumpLockVersion(text, version) {
  const re = /(\[\[package\]\]\r?\nname = "career-nova"\r?\nversion = )"[^"]*"/;
  if (!re.test(text)) throw new Error('uv.lock 里找不到 career-nova 包的 version');
  return text.replace(re, `$1"${version}"`);
}

/**
 * 只改 [project] 段下的 version，其它段（[tool.mypy] 的 python_version 等）一律不碰。
 * 这是「先切片再替换」而非全文 replace——全文 replace 会误伤 python_version = "3.13"。
 */
export function bumpProjectVersion(text, version) {
  const lines = text.split("\n");
  const start = lines.findIndex((l) => l.trim() === "[project]");
  if (start === -1) throw new Error("pyproject.toml 里找不到 [project] 段");
  let end = lines.length;
  for (let i = start + 1; i < lines.length; i++) {
    if (lines[i].trim().startsWith("[")) {
      end = i;
      break;
    }
  }
  let hit = false;
  for (let i = start + 1; i < end; i++) {
    if (/^version\s*=/.test(lines[i])) {
      lines[i] = lines[i].replace(/"[^"]*"/, `"${version}"`);
      hit = true;
      break;
    }
  }
  if (!hit) throw new Error("pyproject.toml 的 [project] 段里找不到 version");
  return lines.join("\n");
}

/** 读文件 → 变换 → 变了才写盘。返回是否真的改了。 */
function syncFile(relPath, transform) {
  const abs = join(ROOT, relPath);
  const before = readFileSync(abs, "utf8");
  const after = transform(before);
  if (after === before) {
    console.log(`  = ${relPath}  (未变)`);
    return false;
  }
  writeFileSync(abs, after, "utf8");
  console.log(`  ✓ ${relPath}`);
  return true;
}

function main(argv) {
  const explicit = argv[2];

  if (explicit) {
    assertValidVersion(explicit);
    console.log(`[sync-version] 目标版本 ${explicit}（来自命令行参数）`);
    syncFile("package.json", (t) => bumpJsonVersion(t, explicit, "根 package.json"));
  } else {
    const rootText = readFileSync(join(ROOT, "package.json"), "utf8");
    const m = rootText.match(/^\s*"version"\s*:\s*"([^"]*)"/m);
    if (!m) throw new Error("根 package.json 里找不到 version 字段");
    assertValidVersion(m[1]);
    console.log(`[sync-version] 目标版本 ${m[1]}（来自根 package.json）`);
  }

  // 从根文件重新读，保证无论如何都以盘上的真值为准——根文件已经在上一步写对了。
  const version = assertValidVersion(
    readFileSync(join(ROOT, "package.json"), "utf8").match(/^\s*"version"\s*:\s*"([^"]*)"/m)[1]
  );

  // 只统计「下游消费点」的改写数——根 package.json 是源，不计入，
  // 否则「只在根文件改了个号、其它几处本来就对」时会误报成「1 个被改写」。
  let changed = 0;
  changed += syncFile("electron/package.json", (t) => bumpJsonVersion(t, version, "electron/package.json"));
  changed += syncFile("frontend/package.json", (t) => bumpJsonVersion(t, version, "frontend/package.json"));
  changed += syncFile("app/core/config.py", (t) => bumpConfigVersion(t, version));
  changed += syncFile("pyproject.toml", (t) => bumpProjectVersion(t, version));
  changed += syncFile("uv.lock", (t) => bumpLockVersion(t, version));

  console.log(`[sync-version] 完成，下游 ${changed}/5 个文件被改写（目标版本 ${version}）`);
}

// 被 import 时（测试）不执行 main。
if (process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))) {
  main(process.argv);
}
