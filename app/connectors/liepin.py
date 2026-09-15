"""猎聘 connector（阶段二 v1）——免登录官方 web JSON API 路（curl_cffi Chrome 指纹）。

路径：猎聘的 web 搜索 JSON API（api-c.liepin.com），**免登录**，但普通 requests/httpx
会被软屏蔽（TLS/JA3 指纹不对）——必须用 curl_cffi 的 `impersonate="chrome"`（Chrome TLS
指纹）。流程（jobfindsme 实测 ~0.9s 返回 40+ 岗位）：
1. GET www.liepin.com/zhaopin/?key={kw}&dqs={city} 一次，拿 `XSRF-TOKEN` cookie。
2. POST api-c.liepin.com/api/com.liepin.searchfront4c.pc-search-job，带 X-Fscp-* headers
   + X-XSRF-TOKEN，body 是 mainSearchPcConditionForm。
3. 响应 data.data.jobCardList，每条含 job / comp 子对象。

- 本模块只做「平台 → 统一 schema」的规范化，不碰 DB / LLM（分层红线）。
- 风控识别：XSRF-TOKEN 缺失 / 返回非 JSON / flag != 1 → 抛 LiepinBlockedError（上层出声）。
- parse_payload 是纯函数（可单测）；fetch_liepin 走真实 HTTP（可注入 session_factory）。
"""

import json
import uuid

from typing import Any, Awaitable, Callable

from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from urllib.parse import quote

# 猎聘城市代码（2026-09-01 实测：逐城抓城市页 $CONFIG.dqCode 并与 adDqName 核对）。
# 覆盖 25 热门城市（与 app/services/direction.py SUPPORTED_CITIES 同源）。
# 原杭州 080020 指向合肥（错码），已修正为 070020。
LIEPIN_CITY_CODES: dict[str, str] = {
    "北京": "010",
    "上海": "020",
    "天津": "030",
    "重庆": "040",
    "广州": "050020",
    "东莞": "050040",
    "深圳": "050090",
    "南京": "060020",
    "苏州": "060080",
    "杭州": "070020",
    "宁波": "070030",
    "合肥": "080020",
    "福州": "090020",
    "郑州": "150020",
    "哈尔滨": "160020",
    "武汉": "170020",
    "长沙": "180020",
    "长春": "190020",
    "沈阳": "210020",
    "大连": "210040",
    "济南": "250020",
    "青岛": "250070",
    "西安": "270020",
    "成都": "280020",
    "昆明": "310020",
}
_LIEPIN_API = "https://api-c.liepin.com/api/com.liepin.searchfront4c.pc-search-job"
_TIMEOUT = 12
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


class LiepinError(Exception):
    """猎聘 connector 基类异常。"""


class LiepinBlockedError(LiepinError):
    """被风控/拒绝：XSRF 缺失、非 JSON、flag != 1——上层出声，不静默空结果。"""


def _city_code(city: str) -> str:
    """城市中文名 → 猎聘城市码；空/未知 → 空串（全国，不传 dqs）。"""
    return LIEPIN_CITY_CODES.get(city, "")


def parse_payload(payload: dict) -> list[dict]:
    """把猎聘 API 响应规范化为统一 schema（纯函数，可单测）。

    输入：POST 的 JSON 响应（flag/data.data.jobCardList）。
    输出：统一 schema 的岗位 dict 列表（对齐 app/schemas/jobs.py Job）。
    """
    if payload.get("flag") != 1:
        raise LiepinBlockedError(
            f"猎聘接口拒绝：flag={payload.get('flag')} code={payload.get('code')}"
        )
    cards = (payload.get("data") or {}).get("data", {}).get("jobCardList") or []
    jobs: list[dict] = []
    for card in cards:
        if not isinstance(card, dict):
            continue
        job = card.get("job") or {}
        comp = card.get("comp") or {}
        title = str(job.get("title", ""))
        if not title:
            continue
        jobs.append(normalize_item(job, comp))
    return jobs


def parse_total_counts(payload: dict) -> int | None:
    """从响应取岗位规模估值（`data.pagination.totalCounts`）。缺失/非整数 → None。

    **真计数、但封顶在 ~800**（2026-09-13 实测，见 ADR 0020）：昆明 224 / 大连 349 / 济南 675
    这类**低值可信**（说明这小市场岗少）；北京/上海/成都这类**高值全压在 800 附近、互相不可比**
    （猎聘故意不让看出大城市间的差别）。语义解释归 market service，本函数只负责取值。
    """
    pag = (payload.get("data") or {}).get("pagination") or {}
    n = pag.get("totalCounts")
    return n if isinstance(n, int) else None



def normalize_item(job: dict, comp: dict) -> dict:
    """单条猎聘岗位（job/comp 子对象）→ 统一 schema。字段缺失给默认值，不抛。"""
    job_id = str(job.get("jobId", ""))
    link = str(job.get("link", ""))
    labels = job.get("labels") or []
    return {
        "source_name": "liepin",
        "external_id": job_id or link or str(job.get("title", "")),
        "title": str(job.get("title", "")),
        "company": str(comp.get("compName", "")),
        "city": str(job.get("dq", "")),
        "salary_text": str(job.get("salary", "")),
        "experience": str(job.get("requireWorkYears", "")),
        "degree": str(job.get("requireEduLevel", "")),
        "skills": [str(x) for x in labels[:8]] if isinstance(labels, list) else [],
        "job_labels": [],
        "welfare": [],
        "source_url": link,
        "apply_url": link,
        # 刚抓到 = 此刻在搜索结果里 = active（fe-api 不提供可靠 liveness 信号）
        "liveness": "active",
        "platform_updated_at": None,
    }


def _retryable_liepin_error(exc: BaseException) -> bool:
    """只重试临时网络类失败；风控（LiepinBlockedError）不重试——退了还是被拦，白撞。"""
    return isinstance(exc, LiepinError) and not isinstance(exc, LiepinBlockedError)


@retry(
    retry=retry_if_exception(_retryable_liepin_error),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, min=0.5, max=4),
    reraise=True,
)
async def _fetch_liepin_payload(
    query: str,
    city: str = "",
    *,
    page: int = 0,
    session_factory: Callable[[], Any] | None = None,
) -> dict:
    """POST 猎聘搜索接口，返回**原始 payload**（未规范化）——fetch_liepin / fetch_liepin_page 共用。

    - 用 curl_cffi 的 Chrome TLS/JA3 指纹（普通 httpx 会被软屏蔽）。
    - page：currentPage（0 起，实测真分页——0/1/2/3 各 42 条零重叠）。续页游标靠它推进。
    - 被风控抛 LiepinBlockedError。
    - 可注入 session_factory（测试用 mock）；默认 curl_cffi 的 AsyncSession。
    - 真异步：AsyncSession 不阻塞事件循环。外层 tenacity 对**临时网络失败**指数退避重试 3 次；
      风控（LiepinBlockedError）直接穿透、不重试。
    """
    # 延迟 import：curl_cffi 是可选依赖（browser 场景），避免无它时 import 崩溃
    try:
        from curl_cffi import requests as curl_requests
    except ImportError as e:  # pragma: no cover - 仅在缺依赖时触发
        raise LiepinError("curl_cffi 未安装：猎聘连接器需要它（uv add curl_cffi）") from e

    make_session = session_factory or (
        lambda: curl_requests.AsyncSession(impersonate="chrome")
    )
    dq = _city_code(city)
    search_url = f"https://www.liepin.com/zhaopin/?key={quote(query)}"
    if dq:
        search_url += f"&dqs={dq}"

    session = make_session()
    try:
        await session.get(search_url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
        xsrf = session.cookies.get("XSRF-TOKEN", "")
        if not xsrf:
            raise LiepinBlockedError("猎聘落地页没设置 XSRF-TOKEN cookie（疑似被风控）")
        response = await session.post(
            _LIEPIN_API,
            headers={
                "User-Agent": _UA,
                "Content-Type": "application/json;charset=UTF-8",
                "X-Client-Type": "web",
                "X-Requested-With": "XMLHttpRequest",
                "X-Fscp-Bi-Stat": json.dumps({"location": search_url}),
                "X-Fscp-Fe-Version": "",
                "X-Fscp-Std-Info": json.dumps({"client_id": "40108"}),
                "X-Fscp-Trace-Id": str(uuid.uuid4()),
                "X-Fscp-Version": "1.1",
                "X-XSRF-TOKEN": xsrf,
                "Origin": "https://www.liepin.com",
                "Referer": search_url,
            },
            json={
                "data": {
                    "mainSearchPcConditionForm": {
                        "city": dq,
                        "dq": dq,
                        "pubTime": "",
                        "currentPage": page,
                        "pageSize": 40,
                        "key": query,
                        "suggestTag": "",
                        "workYearCode": "0",
                        "compId": "",
                        "compName": "",
                        "compTag": "",
                        "industry": "",
                        "salaryCode": "",
                        "jobKind": "",
                        "compScale": "",
                        "compKind": "",
                        "compStage": "",
                        "eduLevel": "",
                        "salaryLow": "",
                        "salaryHigh": "",
                    },
                    "passThroughForm": {
                        "scene": "input",
                        "skId": uuid.uuid4().hex,
                        "fkId": uuid.uuid4().hex,
                        "ckId": uuid.uuid4().hex,
                    },
                },
            },
            timeout=_TIMEOUT,
        )
        try:
            return response.json()
        except Exception as e:
            raise LiepinBlockedError(
                f"猎聘返回非 JSON（status={response.status_code}，疑似风控页）"
            ) from e
    except LiepinError:
        raise
    except Exception as e:
        raise LiepinError(f"猎聘网络/传输失败：{e}") from e
    finally:
        close = getattr(session, "close", None)
        if close is not None:
            result = close()
            if isinstance(result, Awaitable):
                await result


async def fetch_liepin(
    query: str,
    city: str = "",
    *,
    page: int = 0,
    session_factory: Callable[[], Any] | None = None,
) -> list[dict]:
    """查猎聘岗位（免登录 web JSON API），返回统一 schema 岗位列表。

    见 `_fetch_liepin_payload`（HTTP 细节）。本函数 = 发请求 + 规范化。
    """
    payload = await _fetch_liepin_payload(query, city, page=page, session_factory=session_factory)
    return parse_payload(payload)


async def fetch_liepin_page(
    query: str,
    city: str = "",
    *,
    page: int = 0,
    session_factory: Callable[[], Any] | None = None,
) -> tuple[list[dict], int | None]:
    """查猎聘岗位 **+ 岗位规模估值**（totalCounts），返回 `(岗位列表, 规模估值或 None)`。

    market 逐城对比用——同一次请求里既拿岗位、又拿该 (词,城) 的规模估值，省一次往返。
    估值语义见 `parse_total_counts`（真计数但封顶）。
    """
    payload = await _fetch_liepin_payload(query, city, page=page, session_factory=session_factory)
    return parse_payload(payload), parse_total_counts(payload)

