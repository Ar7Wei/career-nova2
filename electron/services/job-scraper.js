/**
 * 岗位抓取（阶段二 · 前程无忧读 DOM · 事件驱动一轮，apply.md §8 / ADR 0008）。
 *
 * 2026-08-31 重构（Task #7）：从「手动刷新 + 后台轮巡」改为**事件驱动 kickCrawl**——
 * 前端用户动作触发 `kickCrawl` → 后端跑猎聘一轮（自循环）+ 返回前程无忧只读建议 →
 * 本模块按建议跑**前程无忧一轮**（游标续爬 + 点翻页器）→ 报 `POST /jobs/crawl-state` 记账。
 *
 * 平台收敛为两家（BOSS 已彻底砍）：前程无忧走 DOM（本文件），猎聘走后端 HTTP
 * （app/services/crawl.py 自循环）。前程无忧翻页 = 点翻页器 `.el-pagination .btn-next`
 * （pageNum 在 XHR 不在 URL，改 URL 不翻页）；跳页器不能输页码（手点 + 中间省略号），
 * 故 resume 到游标第 N 页只能连点 N-1 次，靠 cap 默认 10 兜住。
 *
 * 游标/三态统一存后端 crawl_state（唯一真相源）——本模块只报结果（kind/group_idx/page/
 * reason），后端记账推进游标 / 标三态。限流信号（bodyLen=0 / 翻页器点不动）在 Electron
 * 产生，归 kind=throttled。
 */

const { BrowserWindow } = require("electron");
const { logger } = require("../utils/logger");

// 前程无忧搜索页/详情页 settle 时长（SPA 渲染 + XHR 异步加载）。
const SETTLE_MS = 4000;
const LOAD_TIMEOUT_MS = 30000;
const DETAIL_SETTLE_MS = 5500; // job51 详情页 JD 异步加载慢，实测 3500 读不到、5000+ 能读到

// 前程无忧一轮内的限速基座（ms）+ 抖动。事件驱动下「两脚之间天然是人速」，这里仍保留
// 详情页之间的 sleep+抖动（不像机器人）。
const RATE_LIMIT_BASE_MS = 3000;
const RATE_LIMIT_JITTER_MS = 2000;

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

/** 限速抖动：base + [0, jitter) 随机，打散请求节拍（不像机器人）。 */
async function rateLimitSleep(baseMs = RATE_LIMIT_BASE_MS, jitterMs = RATE_LIMIT_JITTER_MS) {
  const jitter = Math.floor(Math.random() * jitterMs);
  await sleep(baseMs + jitter);
}

// 平台城市码（中文名 → 代码）。码表来自 cupid 网关 open/noauth/dictionary/search-job-area
// （HMAC 签名），2026-09-01 实测拉取「热门城市」分组 25 城，全部核对过。
//
// 实测结论（2026-09-01 三轮 BrowserWindow 探测）：
// - 传城市码（如 090200 成都 / 080200 杭州）→ 过滤生效，返回全为该城市岗位。
// - 省略 jobArea → 回落用户 IP 所在城市（实测天津），**不是全国**。
// - jobArea=000000（字典里的「全国」码）→ 同样回落 IP 城市，**不是全国**。
// 即：前程无忧这一路**表达不了「全国」**——留空或 000000 都=IP 城市。这是平台行为，
// 不是码的问题。故城市**强制必填**，空 city 不再允许走 job51。
// 与后端 SUPPORTED_CITIES（app/services/direction.py）保持同 25 城。
const CITY_CODES = {
  job51: {
    北京: "010000",
    上海: "020000",
    广州: "030200",
    深圳: "040000",
    武汉: "180200",
    西安: "200200",
    杭州: "080200",
    南京: "070200",
    成都: "090200",
    重庆: "060000",
    东莞: "030800",
    大连: "230300",
    沈阳: "230200",
    苏州: "070300",
    昆明: "250200",
    长沙: "190200",
    合肥: "150200",
    宁波: "080300",
    郑州: "170200",
    天津: "050000",
    青岛: "120300",
    济南: "120200",
    哈尔滨: "220200",
    长春: "240200",
    福州: "110200",
  },
};

// 前程无忧探测器配置（apply.md §7.3：每平台一份，平台改版只改这里）。
const JOB51 = {
  partition: "persist:careernova-51job",
  searchUrl: (query, page, cityCode) =>
    `https://we.51job.com/pc/search?keyword=${encodeURIComponent(query)}` +
    (cityCode ? `&jobArea=${cityCode}` : "") +
    `&pageNum=${page}`,
  cardSel: "div.joblist-item",
  // job51 卡片无 href，但最外层 .joblist-item-job 有 sensorsdata 埋点含 jobId；
  // 详情 URL = https://jobs.51job.com/all/{jobId}.html（实测可冷开）。
  cardFields: `(card) => {
    const name = card.querySelector('span.jname');
    const salary = card.querySelector('span.sal');
    const company = card.querySelector('span.cname');
    const area = card.querySelector('div.area');
    let jobId = '';
    const el = card.querySelector('[sensorsdata]');
    if (el) {
      try {
        const raw = el.getAttribute('sensorsdata') || '';
        const decoded = raw.replace(/&quot;/g, '"').replace(/&amp;/g, '&');
        jobId = JSON.parse(decoded).jobId || '';
      } catch {}
    }
    return {
      title: (name ? name.textContent : '').trim(),
      url: jobId ? 'https://jobs.51job.com/all/' + jobId + '.html' : '',
      salary_text: (salary ? salary.textContent : '').trim(),
      company: (company ? company.textContent : '').trim(),
      city: (area ? area.textContent : '').trim(),
    };
  }`,
  detailJdSel: ".bmsg.job_msg, [class*='job_msg']",
  // 翻页器「下一页」候选选择器（element-ui el-pagination）。
  nextBtnSel: ".el-pagination .btn-next, .el-pagination__next",
};

/** 从 URL 提取稳定 external_id：去 query/fragment，只留 origin+pathname。 */
function urlToId(url) {
  try {
    const u = new URL(url);
    return u.origin + u.pathname;
  } catch {
    return String(url || "");
  }
}

/** 读详情页 JD：命中 detailJdSel，取最长匹配的 textContent。 */
function jdScript(sel) {
  return `(() => {
    const sel = ${JSON.stringify(sel)};
    if (!sel) return '';
    const els = document.querySelectorAll(sel);
    let best = '';
    for (const el of els) {
      const t = (el.textContent || '').replace(/\\s+/g, ' ').trim();
      if (t.length > best.length) best = t;
    }
    return best;
  })()`;
}

/** GET 某平台已入库的 external_id 集合（去重用）。 */
async function fetchExistingIds(backendPort, source) {
  try {
    const resp = await fetch(`http://127.0.0.1:${backendPort}/api/v1/jobs/external-ids?source=${source}`);
    if (!resp.ok) return new Set();
    const body = await resp.json();
    return new Set(body.ids || []);
  } catch {
    return new Set(); // 查不到就全抓（宁多勿漏，upsert 兜底）
  }
}

/** GET /direction 读当前方向（keywords 查询组 + city）。失败/空 → null（回退兜底）。 */
async function fetchDirection(backendPort) {
  try {
    const resp = await fetch(`http://127.0.0.1:${backendPort}/api/v1/direction`);
    if (!resp.ok) return null;
    const body = await resp.json();
    return body && typeof body === "object" ? body : null;
  } catch {
    return null;
  }
}

/** 搜索词数组：方向 keywords（查询组 list[list[str]]）→ 一维 query 数组（组内空格 join = AND）。
 *  空方向返回空数组（由 kick 门控拒，不应走到这——方向空不爬）。 */
function resolveKeywords(direction) {
  const kws = direction?.keywords;
  if (!Array.isArray(kws)) return [];
  const cleaned = [];
  for (const group of kws) {
    if (Array.isArray(group)) {
      const joined = group.map((w) => String(w).trim()).filter(Boolean).join(" ");
      if (joined) cleaned.push(joined);
    } else {
      const single = String(group).trim();
      if (single) cleaned.push(single);
    }
  }
  return cleaned;
}

/** 逐组城市数组：方向 cities（与 keywords 平行，ADR 0019）→ 与 resolveKeywords 等长同序的
 *  城市名数组（缺项补空串）。不再有方向级单值 city——城市挂在每个查询组上。 */
function resolveCities(direction) {
  const cs = direction?.cities;
  if (!Array.isArray(cs)) return [];
  return cs.map((c) => String(c ?? "").trim());
}

/** 城市中文名 → 前程无忧城市码。空/未知 → 空串（省略 jobArea = 平台回落 IP 城市，非全国）。 */
function resolveCityCode(city) {
  return CITY_CODES.job51?.[city] ?? "";
}

/** 加载 URL（带超时 + 重试）。loadURL 自身 resolve=did-finish-load / reject=did-fail-load。 */
async function loadWithTimeout(win, url, timeoutMs = LOAD_TIMEOUT_MS) {
  const attempt = () =>
    Promise.race([
      win.loadURL(url),
      new Promise((_, reject) => setTimeout(() => reject(new Error(`load timeout ${timeoutMs}ms`)), timeoutMs)),
    ]);
  try {
    return await attempt();
  } catch (err) {
    logger.warn("job_scrape_load_retry", { url, error: String(err) });
    await new Promise((r) => setTimeout(r, 2000));
    return await attempt();
  }
}

/**
 * 读详情页 JD；返回 { jd, status }——status: fetched（读到正文）/ failed（选择器空、body
 * 空、或异常重试尽）。投递页分析靠它区分「深度分析（有 JD）」与「概览（无 JD）」
 * （apply.md §11.7.8：description=="" 分不清真空与抓失败，故显式记状态）。
 */
async function readDetailJd(win, url, jdSel) {
  if (!jdSel) return { jd: "", status: "failed" };
  const JD_ATTEMPTS = 2;
  for (let attempt = 1; attempt <= JD_ATTEMPTS; attempt++) {
    try {
      await loadWithTimeout(win, url);
      await new Promise((r) => setTimeout(r, DETAIL_SETTLE_MS));
      const bodyLen = await win.webContents.executeJavaScript(
        "document.body ? document.body.innerText.length : 0",
        true
      );
      const jd = await win.webContents.executeJavaScript(jdScript(jdSel), true);
      if (bodyLen > 0 || attempt === JD_ATTEMPTS) {
        return { jd: jd || "", status: jd ? "fetched" : "failed" };
      }
      logger.warn("job_scrape_jd_body_empty", { url, attempt });
      await new Promise((r) => setTimeout(r, 3000));
    } catch (err) {
      logger.warn("job_scrape_jd_failed", { url, attempt, error: String(err) });
      if (attempt === JD_ATTEMPTS) return { jd: "", status: "failed" };
      await new Promise((r) => setTimeout(r, 3000));
    }
  }
  return { jd: "", status: "failed" };
}

/** 归一化成 ingest 入库的字段 dict。 */
function makeJob(externalId, card, url, jd, jdStatus, roundId, recall) {
  return {
    source_name: "job51",
    external_id: externalId,
    title: card.title || "",
    company: card.company || "",
    city: card.city || "",
    salary_text: card.salary_text || "",
    experience: "",
    degree: "",
    skills: [],
    job_labels: [],
    welfare: [],
    description: jd || "",
    jd_status: jdStatus || "none",
    source_url: url || "",
    apply_url: url || "",
    liveness: "active",
    platform_updated_at: null,
    // 归属字段（首触即定，后端 upsert 只在插入时采用）：轮次 id + 召回词文本。
    crawl_round_id: roundId ?? null,
    found_by_query: recall || "",
  };
}

/** POST 归一化岗位到后端 /jobs/ingest。返回 {added, updated, aborted}。 */
async function postIngest(backendPort, jobs, dataEpoch) {
  const resp = await fetch(`http://127.0.0.1:${backendPort}/api/v1/jobs/ingest`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jobs, data_epoch: dataEpoch ?? null }),
  });
  if (!resp.ok) {
    throw new Error(`ingest HTTP ${resp.status}`);
  }
  return resp.json();
}

/** POST 前程无忧一轮结果上报 → 后端记账推进游标 + 标三态。 */
async function postCrawlState(backendPort, report) {
  const resp = await fetch(`http://127.0.0.1:${backendPort}/api/v1/jobs/crawl-state`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(report),
  });
  if (!resp.ok) {
    throw new Error(`crawl-state HTTP ${resp.status}`);
  }
  return resp.json();
}

/** 读当前页卡片（前程无忧 job51）。返回 {cards, bodyLen}。 */
async function readCards(win) {
  const cardScript = `(() => {
    const cards = document.querySelectorAll(${JSON.stringify(JOB51.cardSel)});
    const out = [];
    for (const card of cards) {
      const it = (${JOB51.cardFields})(card);
      if (!it.title) continue;
      out.push(it);
    }
    return out;
  })()`;
  const cards = await win.webContents.executeJavaScript(cardScript, true);
  const bodyLen = await win.webContents.executeJavaScript(
    "document.body ? document.body.innerText.length : 0",
    true
  );
  return { cards, bodyLen };
}

/** 点翻页器「下一页」。返回 {clicked, disabled}——disabled=true 表示已到末页（该组到底）。 */
async function clickNext(win) {
  const result = await win.webContents.executeJavaScript(
    `(() => {
      const sel = ${JSON.stringify(JOB51.nextBtnSel)};
      const el = document.querySelector(sel);
      if (!el) return { clicked: false, disabled: true, found: false };
      const disabled = el.disabled || el.classList.contains('is-disabled') || el.classList.contains('disabled');
      if (disabled) return { clicked: false, disabled: true, found: true };
      el.click();
      return { clicked: true, disabled: false, found: true };
    })()`,
    true
  );
  return result;
}

/**
 * 前程无忧一轮：从游标 (group_idx, page) 续爬，组间顺序消费，翻页凑 quota / 撞 cap / 空页。
 *
 * @param {number} backendPort
 * @param {{groupIdx: number, page: number, quota: number, cap: number, dataEpoch: number}} opts
 * @returns {Promise<{ok: boolean, report: object, aborted: boolean}>}
 *   report = { source, kind, group_idx, page, added, updated, reason }（给后端记账）
 */
async function scrapeJob51Round(backendPort, { groupIdx, page, quota, cap, dataEpoch, roundId }) {
  const win = new BrowserWindow({
    show: false,
    width: 1200,
    height: 900,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      partition: JOB51.partition,
    },
  });

  try {
    const direction = await fetchDirection(backendPort);
    const keywords = resolveKeywords(direction);
    if (keywords.length === 0) {
      // 方向空 → 门控拒（kick 已拒，这里双保险不爬）
      return { ok: false, report: null, aborted: false, reason: "no_direction" };
    }
    const cityNames = resolveCities(direction); // 逐组城市（与 keywords 平行，ADR 0019）
    const existing = await fetchExistingIds(backendPort, "job51");
    const seen = new Set(existing);

    let added = 0;
    let updated = 0;
    let pagesScanned = 0;
    const origin = 1; // job51 pageNum 1 起
    let stop = null; // null / "quota" / "cap"
    let finalGi = groupIdx;
    let finalPage = page;

    for (let gi = groupIdx; gi < keywords.length; gi++) {
      const query = keywords[gi];
      // 该组自带城市（逐组，ADR 0019）；缺失/未知 → 不爬这组（后端 commit 已挡，这里双保险）
      const cityName = cityNames[gi] || "";
      const cityCode = resolveCityCode(cityName);
      if (!cityCode) {
        logger.warn("job51_no_city_code", { group: gi, city: cityName });
        return { ok: false, report: null, aborted: false, reason: "no_city" };
      }
      let p = gi === groupIdx ? page : origin;

      // 冷开搜索页（第 1 页）
      await loadWithTimeout(win, JOB51.searchUrl(query, 1, cityCode));
      await new Promise((r) => setTimeout(r, SETTLE_MS));

      // resume：连点 (p-1) 次翻页器到达游标页（跳页器不能输页码，只能连点）。
      // 前程无忧 pageNum 1 起，第 p 页 = 点 (p-1) 次。
      for (let click = 0; click < p - 1; click++) {
        const r = await clickNext(win);
        if (!r.clicked) {
          // 点到 disabled（该组提前到底）→ 该组 exhausted
          logger.info("job51_resume_group_exhausted", { group: gi, targetPage: p, clicks: click });
          return { ok: true, report: { source: "job51", kind: "exhausted", group_idx: gi, page: p, added, updated }, aborted: false };
        }
        await new Promise((r) => setTimeout(r, SETTLE_MS));
      }

      while (true) {
        // 读当前页卡片 + bodyLen（bodyLen=0 = 被限流）
        const { cards, bodyLen } = await readCards(win);
        if (bodyLen === 0) {
          // 被限流 → throttled，游标停本组本页
          logger.warn("job51_throttled", { group: gi, page: p });
          return { ok: true, report: { source: "job51", kind: "throttled", group_idx: gi, page: p, added, updated }, aborted: false };
        }
        if (cards.length === 0) {
          // 空页 → 该组到底（exhausted），移到下一组
          logger.info("job51_group_exhausted", { group: gi, page: p });
          break;
        }
        pagesScanned += 1;

        // 本轮去重：只开没见过的详情
        const fresh = cards.filter((c) => {
          const id = urlToId(c.url);
          return id && !seen.has(id);
        });
        const roundJobs = [];
        const recall = `${query} | ${cityName}`; // 召回词文本（首触即定的归属戳）
        for (const c of fresh) {
          const id = urlToId(c.url);
          seen.add(id);
          const { jd, status } = await readDetailJd(win, c.url, JOB51.detailJdSel);
          roundJobs.push(makeJob(id, c, c.url, jd, status, roundId, recall));
          await rateLimitSleep();
        }

        // 入库（带 epoch 守卫；核爆 → aborted）
        if (roundJobs.length > 0) {
          try {
            const ing = await postIngest(backendPort, roundJobs, dataEpoch);
            if (ing.aborted) {
              logger.warn("job51_ingest_aborted_by_epoch", { dropped: roundJobs.length });
              return { ok: true, report: null, aborted: true };
            }
            added += ing.added ?? 0;
            updated += ing.updated ?? 0;
          } catch (err) {
            logger.error("job51_ingest_failed", { error: String(err) });
          }
        }

        p += 1;

        if (added >= quota) {
          stop = "quota";
          finalGi = gi;
          finalPage = p;
          break;
        }
        if (pagesScanned >= cap) {
          stop = "cap";
          break;
        }

        // 翻下一页
        const r = await clickNext(win);
        if (!r.clicked) {
          // 点到 disabled → 该组到底（exhausted）
          logger.info("job51_group_exhausted_on_next", { group: gi, page: p });
          break;
        }
        await new Promise((r2) => setTimeout(r2, SETTLE_MS));
      }

      if (stop !== null) break;
    }

    if (stop === "quota") {
      logger.info("job51_round_quota", { added, updated, group: finalGi, page: finalPage });
      return { ok: true, report: { source: "job51", kind: "fresh", group_idx: finalGi, page: finalPage, added, updated, reason: "quota" }, aborted: false };
    }
    if (stop === "cap") {
      logger.info("job51_round_cap", { added, updated, pages: pagesScanned });
      return { ok: true, report: { source: "job51", kind: "fresh", group_idx: 0, page: origin, added, updated, reason: "cap" }, aborted: false };
    }
    // 循环自然结束 = 所有组都空页 → 整家 exhausted
    logger.info("job51_round_exhausted", { added, updated });
    return { ok: true, report: { source: "job51", kind: "exhausted", group_idx: 0, page: origin, added, updated }, aborted: false };
  } catch (err) {
    logger.error("job51_round_error", { error: String(err) });
    return { ok: false, report: null, aborted: false, error: String(err) };
  } finally {
    if (!win.isDestroyed()) win.destroy();
  }
}

/**
 * kickCrawl：事件驱动的一脚。后端跑猎聘一轮 + 返回前程无忧建议 → 本模块跑前程无忧一轮。
 *
 * 并发控制（decision 12）：Electron re-entrancy 闸——`job51Running` 布尔，前程无忧一轮
 * 在跑时后续 kick 跳过 DOM 一轮（猎聘侧由后端 _kick_lock 防重入）。
 *
 * @param {() => { backendPort: number }} getPorts
 */
let job51Running = false;

async function kickCrawl(backendPort) {
  const kickResp = await fetch(`http://127.0.0.1:${backendPort}/api/v1/jobs/crawl/kick`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
  });
  if (!kickResp.ok) {
    throw new Error(`kick HTTP ${kickResp.status}`);
  }
  const kick = await kickResp.json();

  const result = { liepin: kick.liepin, job51: null, dataEpoch: kick.data_epoch, gateReason: kick.gate_reason, roundId: kick.round_id ?? null };

  if (kick.job51 && !job51Running) {
    job51Running = true;
    try {
      const round = await scrapeJob51Round(backendPort, {
        groupIdx: kick.job51.group_idx,
        page: kick.job51.page,
        quota: kick.job51_quota,
        cap: kick.cap,
        dataEpoch: kick.data_epoch,
        roundId: kick.job51.round_id,
      });
      if (round.aborted) {
        // 核爆：丢弃本轮，不回写、不报 crawl-state
        logger.warn("kick_job51_aborted");
        result.job51 = null;
      } else if (round.ok && round.report) {
        result.job51 = await postCrawlState(backendPort, round.report);
      } else {
        logger.warn("kick_job51_failed", { error: round.error || "no report" });
        result.job51 = null;
      }
    } catch (err) {
      logger.error("kick_job51_error", { error: String(err) });
      result.job51 = null;
    } finally {
      job51Running = false;
    }
  } else if (kick.job51 && job51Running) {
    logger.info("kick_job51_reentrant_skip");
  }

  return result;
}

/** 注册岗位抓取 IPC（kickCrawl 一处；旧的 jobs:scrape 手动刷新已删）。 */
function registerJobsIpc(ipcMain, getPorts) {
  ipcMain.handle("jobs:kick-crawl", async () => {
    const { backendPort } = getPorts();
    return kickCrawl(backendPort);
  });
}

module.exports = { registerJobsIpc, kickCrawl, scrapeJob51Round };
