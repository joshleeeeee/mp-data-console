from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ApiResponse(BaseModel):
    ok: bool = True
    message: str = "ok"
    data: Any = None


class QRCodeData(BaseModel):
    qr_image_url: str
    qr_file: str
    uuid: str | None = None


class AuthStatusData(BaseModel):
    status: str
    token: str | None = None
    account_name: str | None = None
    account_avatar: str | None = None
    last_error: str | None = None


class MPCreateRequest(BaseModel):
    fakeid: str = Field(min_length=1)
    nickname: str = Field(min_length=1)
    alias: str | None = None
    avatar: str | None = None
    intro: str | None = None
    biz: str | None = None


class MPSyncRequest(BaseModel):
    pages: int = Field(default=1, ge=1, le=300)
    fetch_content: bool = True
    target_count: int | None = Field(default=None, ge=1, le=250)


class MPOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    fakeid: str
    biz: str | None = None
    nickname: str
    alias: str | None = None
    avatar: str | None = None
    intro: str | None = None
    enabled: bool
    last_sync_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    aid: str | None = None
    mp_id: str
    title: str
    url: str
    cover_url: str | None = None
    digest: str | None = None
    author: str | None = None
    publish_ts: int | None = None
    content_html: str | None = None
    content_text: str | None = None
    images_json: str | None = None
    created_at: datetime
    updated_at: datetime


class ExportRequest(BaseModel):
    format: Literal["markdown", "html", "pdf"] = "markdown"


class BatchExportRequest(BaseModel):
    article_ids: list[str]
    format: Literal["markdown", "html", "pdf"] = "markdown"


class QuickSyncRequest(BaseModel):
    keyword: str = Field(min_length=1)
    pages: int = Field(default=1, ge=1, le=50)
    fetch_content: bool = True
    pick_index: int = Field(default=0, ge=0, le=20)
