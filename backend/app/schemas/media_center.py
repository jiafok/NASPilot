"""Media Download Center — pydantic schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

Provider = Literal[
    "youtube", "bilibili", "podcast", "soundcloud", "vimeo",
    "twitch", "x", "ximalaya", "xiaoyuzhou", "custom",
]
SubType = Literal["video", "playlist", "channel"]
MediaMode = Literal["video", "audio", "both"]
AudioFormat = Literal["mp3", "best", "m4a", "opus", "flac"]
ScheduleType = Literal["manual", "hourly", "every_6h", "every_12h", "daily", "cron"]
NetworkMode = Literal["system", "direct", "http", "socks5", "docker_vpn"]


class SubscriptionBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    provider: Provider = "custom"
    url: str = Field(..., min_length=4, max_length=1024)
    type: SubType = "video"
    enabled: bool = True

    media_mode: MediaMode = "video"
    audio_only: bool = False  # derived alias
    video_height: int = Field(480, ge=0, le=4320)
    audio_format: AudioFormat = "mp3"
    download_path: str | None = Field(None, max_length=1024)
    number_prefix: bool = False

    schedule_type: ScheduleType = "every_6h"
    cron_expr: str | None = Field(None, max_length=128)
    max_items_per_sync: int = Field(20, ge=1, le=500)
    rate_limit: str | None = Field(None, max_length=32)

    network_mode: NetworkMode = "system"
    proxy_url: str | None = Field(None, max_length=512)
    proxy_username: str | None = Field(None, max_length=128)
    proxy_password: str | None = Field(None, max_length=128)
    vpn_container: str | None = Field(None, max_length=128)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL 必须以 http:// 或 https:// 开头")
        return v

    @model_validator(mode="after")
    def _derive(self):
        if self.audio_only and self.media_mode == "video":
            self.media_mode = "audio"
        self.audio_only = self.media_mode == "audio"

        if self.schedule_type == "cron" and not (self.cron_expr or "").strip():
            raise ValueError("schedule_type=cron 时必须提供 cron_expr")
        if self.network_mode in ("http", "socks5") and not (self.proxy_url or "").strip():
            raise ValueError("network_mode 为 http/socks5 时必须填写 proxy_url")
        if self.network_mode == "docker_vpn" and not (self.vpn_container or "").strip():
            raise ValueError("network_mode=docker_vpn 时必须填写 vpn_container")
        if self.proxy_url:
            p = self.proxy_url.strip()
            if not p.startswith(("http://", "https://", "socks5://", "socks5h://")):
                raise ValueError("proxy_url 必须以 http:// 或 socks5:// 开头")
        return self


class SubscriptionCreate(SubscriptionBase):
    pass


class SubscriptionUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    provider: Provider | None = None
    url: str | None = Field(None, min_length=4, max_length=1024)
    type: SubType | None = None
    enabled: bool | None = None

    media_mode: MediaMode | None = None
    audio_only: bool | None = None
    video_height: int | None = Field(None, ge=0, le=4320)
    audio_format: AudioFormat | None = None
    download_path: str | None = Field(None, max_length=1024)
    number_prefix: bool | None = None

    schedule_type: ScheduleType | None = None
    cron_expr: str | None = Field(None, max_length=128)
    max_items_per_sync: int | None = Field(None, ge=1, le=500)
    rate_limit: str | None = Field(None, max_length=32)

    network_mode: NetworkMode | None = None
    proxy_url: str | None = Field(None, max_length=512)
    proxy_username: str | None = Field(None, max_length=128)
    proxy_password: str | None = Field(None, max_length=128)
    vpn_container: str | None = Field(None, max_length=128)

    @model_validator(mode="after")
    def _derive(self):
        if self.media_mode is not None:
            self.audio_only = self.media_mode == "audio"
        elif self.audio_only is not None:
            self.media_mode = "audio" if self.audio_only else "video"
        if self.proxy_url:
            p = self.proxy_url.strip()
            if p and not p.startswith(("http://", "https://", "socks5://", "socks5h://")):
                raise ValueError("proxy_url 必须以 http:// 或 socks5:// 开头")
        return self


class SubscriptionOut(BaseModel):
    id: int
    name: str
    provider: str
    url: str
    type: str
    enabled: bool
    media_mode: str
    audio_only: bool
    video_height: int
    audio_format: str
    download_path: str | None = None
    number_prefix: bool = False
    schedule_type: str
    cron_expr: str | None = None
    max_items_per_sync: int = 20
    rate_limit: str | None = None
    network_mode: str = "system"
    proxy_url: str | None = None
    proxy_username: str | None = None
    vpn_container: str | None = None
    last_check_time: datetime | None = None
    last_error: str | None = None
    created_time: datetime
    updated_time: datetime
    # computed
    download_count: int = 0
    status: str = "idle"  # idle | syncing | disabled | error

    model_config = {"from_attributes": True}


class TaskOut(BaseModel):
    id: int
    subscription_id: int | None = None
    subscription_name: str | None = None
    provider: str
    media_id: str
    title: str | None = None
    source_url: str | None = None
    status: str
    progress: float = 0
    speed: str | None = None
    downloaded_bytes: int = 0
    total_bytes: int = 0
    eta: str | None = None
    error_message: str | None = None
    attempts: int = 0
    created_time: datetime
    updated_time: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None

    model_config = {"from_attributes": True}


class HistoryOut(BaseModel):
    id: int
    provider: str
    media_id: str
    title: str | None = None
    channel_name: str | None = None
    playlist_name: str | None = None
    publish_date: datetime | None = None
    file_path: str | None = None
    file_size: int | None = None
    download_time: datetime | None = None
    status: str
    media_type: str | None = None
    duration: int | None = None
    source_url: str | None = None
    subscription_id: int | None = None
    created_time: datetime

    model_config = {"from_attributes": True}


class HistoryPage(BaseModel):
    items: list[HistoryOut]
    total: int
    page: int
    page_size: int


class SyncResult(BaseModel):
    status: str
    subscription_id: int | None = None
    message: str | None = None


class ProviderInfo(BaseModel):
    provider: str
    display_name: str
    implemented: bool
    supported_types: list[str]
    url_examples: list[str]
    note: str | None = None


class MediaHealth(BaseModel):
    ytdlp_installed: bool
    ytdlp_version: str | None = None
    ffmpeg_installed: bool
    ffmpeg_version: str | None = None
    status: str
    detail: str | None = None
    providers: list[ProviderInfo]
    network: dict
    directories: dict
    active_downloads: int = 0
    queued_tasks: int = 0


class MediaServerOut(BaseModel):
    kind: str
    configured: bool
    base_url: str | None = None
    library_paths: list[str]
    sample_links: list[dict]


class UrlDetectOut(BaseModel):
    provider: str
    type: str
    valid: bool
    message: str | None = None
    name_hint: str | None = None
