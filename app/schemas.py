from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    date_start: date
    date_end: date

    @model_validator(mode="after")
    def _validate_date_range(self):
        if self.date_start > self.date_end:
            raise ValueError("date_start 不能晚于 date_end")
        return self


class MPFavoriteUpdateRequest(BaseModel):
    is_favorite: bool


class MPAutoSyncUpdateRequest(BaseModel):
    enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=30, le=10080)
    lookback_days: int | None = Field(default=None, ge=1, le=365)
    overlap_hours: int | None = Field(default=None, ge=0, le=72)
    run_immediately: bool = False

    @model_validator(mode="after")
    def _validate_patch(self):
        has_patch = any(
            value is not None
            for value in (
                self.enabled,
                self.interval_minutes,
                self.lookback_days,
                self.overlap_hours,
            )
        )
        if not has_patch and not self.run_immediately:
            raise ValueError("至少需要提供一项自动同步配置")
        if self.enabled is False and self.run_immediately:
            raise ValueError("关闭自动同步时不能立即执行")
        return self


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
    is_favorite: bool = False
    use_count: int = 0
    last_used_at: datetime | None = None
    last_sync_at: datetime | None = None
    auto_sync_enabled: bool = False
    auto_sync_interval_minutes: int = 1440
    auto_sync_lookback_days: int = 3
    auto_sync_overlap_hours: int = 6
    auto_sync_next_run_at: datetime | None = None
    auto_sync_last_success_at: datetime | None = None
    auto_sync_last_error: str | None = None
    auto_sync_consecutive_failures: int = 0
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


class AutoSyncEnabledUpdateRequest(BaseModel):
    enabled: bool


class DBRowCreateRequest(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class DBRowUpdateRequest(BaseModel):
    pk: dict[str, Any] = Field(default_factory=dict)
    values: dict[str, Any] = Field(default_factory=dict)


class DBRowDeleteRequest(BaseModel):
    pk: dict[str, Any] = Field(default_factory=dict)
