"""Media subscription (YouTube) schemas."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

#: The legacy YouTube module only serves YouTube — every other source is
#: handled by the unified Media Download Center (/api/media/*).
SubscriptionType = Literal["channel", "playlist"]
#: video → MP4, audio → 纯音频, both → MP4 + 同名音频
MediaMode = Literal["video", "audio", "both"]
#: mp3 = 有损重编码；best = 零转码保留原始流；其余为指定容器
AudioFormat = Literal["mp3", "best", "opus", "m4a", "flac", "wav"]


class SubscriptionBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    url: str = Field(..., min_length=4, max_length=1024)
    type: SubscriptionType = "channel"
    enabled: bool = True
    media_mode: MediaMode = "video"
    # 0 = 跟随全局 YOUTUBE_MAX_HEIGHT；其他值 = 高度上限
    video_height: int = Field(0, ge=0, le=4320)
    audio_format: AudioFormat = "mp3"
    # Deprecated alias — derived from ``media_mode``
    audio_only: bool = False
    download_path: str | None = Field(None, max_length=1024)

    @field_validator("url")
    @classmethod
    def _validate_url(cls, v: str) -> str:
        v = v.strip()
        if not v.startswith(("http://", "https://")):
            raise ValueError("URL 必须以 http:// 或 https:// 开头")
        return v

    @model_validator(mode="after")
    def _sync_media_mode(self):
        """Keep the legacy ``audio_only`` flag consistent with ``media_mode``."""
        if self.audio_only and self.media_mode == "video":
            self.media_mode = "audio"
        self.audio_only = self.media_mode == "audio"
        return self


class SubscriptionCreate(SubscriptionBase):
    pass


class SubscriptionUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    url: str | None = Field(None, min_length=4, max_length=1024)
    type: SubscriptionType | None = None
    enabled: bool | None = None
    media_mode: MediaMode | None = None
    video_height: int | None = Field(None, ge=0, le=4320)
    audio_format: AudioFormat | None = None
    audio_only: bool | None = None  # deprecated alias
    download_path: str | None = Field(None, max_length=1024)

    @model_validator(mode="after")
    def _sync_media_mode(self):
        """Accept either ``media_mode`` or the legacy ``audio_only`` flag."""
        if self.media_mode is not None:
            self.audio_only = self.media_mode == "audio"
        elif self.audio_only is not None:
            self.media_mode = "audio" if self.audio_only else "video"
        return self


class SubscriptionOut(BaseModel):
    id: int
    name: str
    url: str
    type: str
    enabled: bool
    media_mode: str
    video_height: int = 0
    audio_format: str = "mp3"
    audio_only: bool
    download_path: str | None = None
    last_check_time: datetime | None = None
    created_time: datetime
    updated_time: datetime
    download_count: int = 0
    # idle | syncing | disabled
    status: str = "idle"

    model_config = {"from_attributes": True}


class DownloadTaskOut(BaseModel):
    id: int
    subscription_id: int | None = None
    subscription_name: str | None = None
    video_id: str
    title: str | None = None
    status: str
    progress: float = 0
    speed: str | None = None
    downloaded_bytes: int = 0
    total_bytes: int = 0
    eta: str | None = None
    error_message: str | None = None
    created_time: datetime
    updated_time: datetime

    model_config = {"from_attributes": True}


class DownloadHistoryOut(BaseModel):
    id: int
    video_id: str
    title: str | None = None
    channel: str | None = None
    playlist: str | None = None
    publish_date: datetime | None = None
    download_time: datetime | None = None
    file_path: str | None = None
    file_size: int | None = None
    status: str
    media_type: str | None = None
    created_time: datetime
    subscription_id: int | None = None

    model_config = {"from_attributes": True}


class HistoryPage(BaseModel):
    items: list[DownloadHistoryOut]
    total: int
    page: int
    page_size: int


class SyncResult(BaseModel):
    status: str
    subscription_id: int | None = None
    message: str | None = None


class YtdlpHealth(BaseModel):
    ytdlp_installed: bool
    ytdlp_version: str | None = None
    ytdlp_path: str | None = None
    ffmpeg_installed: bool
    ffmpeg_version: str | None = None
    status: str  # ok | degraded | error
    detail: str | None = None
    proxy: str | None = None
