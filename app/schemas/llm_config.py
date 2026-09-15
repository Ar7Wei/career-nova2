"""LLM 配置辅助 schema：合并的「测试连接 + 拉模型列表」探测。

⚠️ 刻意不继承 AppSettingsPatch：探测只有「验证」语义，允许覆盖 model/base_url/key
（默认用已保存设置），但绝不接受 language/data_dir 等设置字段——继承会让请求体
被当成 PATCH 误用。后端在 probe_llm 里先验连接、再拉模型，返回三态。
"""

from pydantic import BaseModel, Field


class LlmProbeRequest(BaseModel):
    """探测请求：可选覆盖 model/base_url/key（默认用已保存设置）。"""

    llm_model: str | None = None
    llm_base_url: str | None = None
    llm_api_key: str | None = None


class LlmProbeResult(BaseModel):
    """探测结果三态：

    - ok=False            → 连接失败（不拉模型，message 为失败原因）。
    - ok=True, models_ok=False → 连接过但拉不到列表（models 为空，可手输模型）。
    - ok=True, models_ok=True  → 全成功（models 为真实列表）。
    """

    ok: bool
    models: list[str] = Field(default_factory=list)
    models_ok: bool = False
    message: str = ""
