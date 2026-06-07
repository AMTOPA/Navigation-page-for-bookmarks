from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Edge 收藏导航"
    database_url: str = "sqlite:///./data/bookmarks.db"
    secret_key: str = "change-me-before-production"
    encryption_key: str = ""
    admin_username: str = "admin"
    admin_password: str = ""
    session_days: int = 14
    secure_cookies: bool = False
    frontend_dir: Path = Path(__file__).resolve().parents[2] / "frontend"
    data_dir: Path = Path("./data")
    max_upload_mb: int = 20
    max_attachment_mb: int = 100
    allowed_attachment_extensions: str = (
        ".pdf,.txt,.md,.html,.htm,.doc,.docx,.xls,.xlsx,.ppt,.pptx,"
        ".png,.jpg,.jpeg,.gif,.webp,.svg,.zip,.7z"
    )
    crawl_timeout_seconds: int = 15
    display_timezone: str = "Asia/Shanghai"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8-sig", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings
