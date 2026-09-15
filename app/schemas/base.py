"""共享响应基类。本地单用户：request_id 直接用 uuid4（无 correlation 中间件）。"""

from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class BaseResponse(BaseModel):
    """所有端点响应的基类。"""

    request_id: UUID = Field(default_factory=uuid4, description="本次请求的唯一标识")
