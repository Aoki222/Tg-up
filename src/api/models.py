"""跨路由共用的请求体。各路由自己的请求体留在对应文件里。"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class FailedIdsBody(BaseModel):
    ids: list[int] = Field(default_factory=list)


class OversizedIdsBody(BaseModel):
    """ids 为空表示当前所有过大源文件。"""

    ids: list[int] = Field(default_factory=list)


class SliceBody(BaseModel):
    parts: int = Field(ge=2, le=30)


class ChatIdBody(BaseModel):
    chat_id: int


class IdentityPayload(BaseModel):
    api_id: int = Field(ge=1)
    api_hash: str = ""

    @field_validator("api_hash")
    @classmethod
    def normalize_hash(cls, value: str) -> str:
        return (value or "").strip()
