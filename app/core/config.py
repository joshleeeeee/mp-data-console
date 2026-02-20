from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "we-mp-mini"
    api_prefix: str = "/api/v1"
    debug: bool = False

    host: str = "0.0.0.0"
    port: int = 18011

    database_url: str = "sqlite:///./data/wechat_mini.db"

    data_dir: str = "data"
    qr_dir: str = "data/qr"
    qr_file: str = "data/qr/login.png"
    export_dir: str = "data/exports"

    user_agent: str = Field(
        default=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        )
    )
    request_timeout: int = 20
    verify_ssl: bool = True

    playwright_browser: str = "chromium"
    playwright_headless: bool = True
    playwright_timeout_ms: int = 30000

    auto_sync_enabled: bool = True
    auto_sync_tick_seconds: int = 45
    auto_sync_scan_limit: int = 10
    auto_sync_dispatch_jitter_seconds: int = 180
    auto_sync_failure_backoff_base_minutes: int = 15
    auto_sync_failure_backoff_max_minutes: int = 360

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    def model_post_init(self, __context) -> None:
        data_dir = Path(self.data_dir)
        qr_dir = Path(self.qr_dir)
        export_dir = Path(self.export_dir)

        data_dir.mkdir(parents=True, exist_ok=True)
        qr_dir.mkdir(parents=True, exist_ok=True)
        export_dir.mkdir(parents=True, exist_ok=True)

        if self.database_url.startswith("sqlite:///"):
            db_path = Path(self.database_url.removeprefix("sqlite:///"))
            if db_path.parent:
                db_path.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
