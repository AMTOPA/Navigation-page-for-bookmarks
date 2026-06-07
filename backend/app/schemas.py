from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


Importance = Literal["high", "medium", "normal", "low"]


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    source_type: str
    external_id: str
    folder_path: str
    favorited_at: datetime | None
    is_active: bool


class BookmarkCreate(BaseModel):
    url: str = Field(min_length=1, max_length=8192)
    title: str = ""
    description: str = ""
    favicon: str = ""
    image: str = ""
    category: str = ""
    tags: list[str] = Field(default_factory=list)
    importance: Importance = "normal"
    notes: str = ""

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("URL 不能为空")
        return value


class BookmarkUpdate(BaseModel):
    url: str | None = None
    title: str | None = None
    description: str | None = None
    favicon: str | None = None
    image: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    importance: Importance | None = None
    notes: str | None = None
    unlock_fields: list[str] = Field(default_factory=list)


class BookmarkOut(BaseModel):
    id: str
    url: str
    title: str
    description: str
    favicon: str
    image: str
    display_image_url: str
    ai_category: str
    manual_category: str
    final_category: str
    tags: list[str]
    ai_importance: str
    manual_importance: str
    importance: str
    notes: str
    summary: str
    outline: list[str]
    keywords: list[str]
    key_info: dict
    manual_override_fields: list[str]
    is_crawled: bool
    is_ai_classified: bool
    crawl_status: str
    ai_status: str
    crawl_error: str
    ai_error: str
    favorited_at: datetime | None
    created_at: datetime
    updated_at: datetime
    last_crawled_at: datetime | None
    last_ai_processed_at: datetime | None
    deleted_at: datetime | None
    local_copy_url: str
    local_copy_name: str
    local_copy_mime: str
    local_copy_size: int
    sources: list[SourceOut]


class LoginRequest(BaseModel):
    username: str
    password: str


class AccountUpdate(BaseModel):
    current_password: str
    username: str = Field(min_length=3, max_length=100)
    new_password: str = Field(default="", max_length=256)

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, value: str) -> str:
        if value and len(value) < 10:
            raise ValueError("新密码至少需要 10 个字符")
        return value


class BatchRequest(BaseModel):
    ids: list[str]
    action: Literal["delete", "restore", "crawl", "ai", "search_index"]
    confirm: bool = False


class TokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(default_factory=lambda: ["bookmarks:read", "bookmarks:write", "sync"])


class AISettingsUpdate(BaseModel):
    base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    model: str = "glm-4-flash"
    api_key: str = ""


class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=8, max_length=2048)
    api_key: str = ""
    enabled: bool = True


class ProviderUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    enabled: bool | None = None


class ModelCreate(BaseModel):
    provider_id: str
    display_name: str = Field(min_length=1, max_length=100)
    model_name: str = Field(min_length=1, max_length=200)
    capabilities: list[Literal["chat", "classification", "summary", "embedding"]]
    enabled: bool = True


class ModelUpdate(BaseModel):
    display_name: str | None = None
    model_name: str | None = None
    capabilities: list[Literal["chat", "classification", "summary", "embedding"]] | None = None
    enabled: bool | None = None


class RouteUpdate(BaseModel):
    routes: dict[Literal["default", "classification", "summary", "embedding", "ai_search"], str | None]


class ModelTestRequest(BaseModel):
    provider_id: str
    model_id: str | None = None
    model_name: str | None = None
    capability: Literal["chat", "classification", "summary", "embedding"] = "chat"
    api_key: str = ""


class ProviderTestRequest(BaseModel):
    provider_id: str | None = None
    base_url: str = ""
    api_key: str = ""


class EdgeSyncItem(BaseModel):
    guid: str
    url: str
    title: str = ""
    folder_path: str = ""
    favorited_at: datetime | None = None


class EdgeSyncRequest(BaseModel):
    batch_id: str
    snapshot_hash: str
    upserts: list[EdgeSyncItem] = Field(default_factory=list)
    removed_guids: list[str] = Field(default_factory=list)


class AISearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    context: str = Field(default="", max_length=2000)
    model_id: str | None = None
    group_by: Literal["", "category", "folder", "importance"] = ""
    group: str = Field(default="", max_length=300)
    limit: int = Field(default=20, ge=1, le=50)
