"""settings 传输模型：后端用户偏好（SQLite 真相源）。

归属边界（三真相源）：
- 启动前就要的（backend_port/close_action）→ Electron 本地 settings.json，不经后端。
- 这里只放普通用户偏好：语言、LLM 模型/base_url/API key。
  （数据目录 data_dir 已挪到 Electron 线 settings.json——启动前就要读、且驱动 DATABASE_URL，
  与本表分属不同真相源，勿混。）
- API key 也放这里（本地单用户，SQLite 与本机同处）；对外响应一律掩码，
  前端只写不回读。**key 唯一来源 = 此表**（N1，2026-08-13 删 .env 兜底——
  打包 exe 无 .env，env 兜底是死设计）。

掩码约定：读写走 AppSettings / AppSettingsPatch（含明文 key，只进后端内部）；
HTTP 响应走 AppSettingsPublic（key 一律掩码为「★×12」）。
"""

from typing import Literal

from pydantic import BaseModel, Field

MASKED_KEY = "★" * 12
_MASK_LENGTH = 12


def _mask_key(key: str) -> str:
    """掩码 API key：空则留空；非空则一律掩成 MASKED_KEY（不透露长度/前缀）。"""
    return key if not key else MASKED_KEY


class CrawlQuota(BaseModel):
    """每平台一轮抓几个新岗（apply.md §8② / ADR 0008 决策 5）。

    每平台各自 quota，不搞全局 batch_size——两平台翻页机制各异、每页条数不同
    （猎聘 42/页、前程无忧 20/页），硬套全局「分摊」会让猎聘深爬量被次源拖住。
    默认对齐各平台一页条数。
    """

    model_config = {"extra": "ignore"}

    liepin: int = 40
    job51: int = 20


class AppSettings(BaseModel):
    """后端用户偏好。默认值即"出厂值"：无记录时以这些为准。

    llm_model 默认空（不再内置 gpt-5-mini 等默认模型名）——模型名只能来自
    探测接口拉取的真实列表，本地不存"出厂默认模型"，避免没配模型时给用户
    显示一个不相干的模型名。
    """

    model_config = {"extra": "ignore"}

    language: Literal["zh", "en"] = "zh"
    llm_model: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""
    # 「开启求职之路」总开关（apply.md §11.1）：后台爬的门控，钥匙交给用户。
    apply_mode: bool = False
    # 每平台一轮抓几个新岗（apply.md §8②，取代旧 crawl_batch_size）。
    crawl_quota: CrawlQuota = Field(default_factory=CrawlQuota)
    # 每轮最多扫几页（全局一个数，用户手动调；apply.md §8④「用户想省就调小 cap」）。
    crawl_cap: int = 10
    # 触发值 = 保留的未处理底仓（≤ 此值就补下一批，默认 5；apply.md §8①）。
    crawl_threshold: int = 5


class AppSettingsPublic(BaseModel):
    """对外响应模型：llm_api_key 一律掩码，只写不回读。"""

    model_config = {"extra": "ignore"}

    language: Literal["zh", "en"]
    llm_model: str
    llm_base_url: str
    llm_api_key: str
    apply_mode: bool
    crawl_quota: CrawlQuota
    crawl_cap: int
    crawl_threshold: int


def mask_settings(s: AppSettings) -> AppSettingsPublic:
    """把内部 AppSettings 转成对外 AppSettingsPublic（key 掩码）。"""
    return AppSettingsPublic(
        language=s.language,
        llm_model=s.llm_model,
        llm_base_url=s.llm_base_url,
        llm_api_key=_mask_key(s.llm_api_key),
        apply_mode=s.apply_mode,
        crawl_quota=s.crawl_quota,
        crawl_cap=s.crawl_cap,
        crawl_threshold=s.crawl_threshold,
    )


class AppSettingsPatch(BaseModel):
    """PATCH 部分更新：全字段可选，只更新传入的键。"""

    model_config = {"extra": "ignore"}

    language: Literal["zh", "en"] | None = None
    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    apply_mode: bool | None = None
    crawl_quota: CrawlQuota | None = None
    crawl_cap: int | None = None
    crawl_threshold: int | None = None

