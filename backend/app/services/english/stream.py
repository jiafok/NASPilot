"""HTTP media streaming with Range support.

The Flutter client never touches the filesystem — it asks NASPilot for
`/api/stream/media/{lesson_id}` and gets a normal HTTP resource it can seek
inside. Range handling is implemented explicitly because a NAS user's player
(video seek, resume) depends on `206 Partial Content` being correct.
"""

from __future__ import annotations

import logging
import mimetypes
import os
import re
from pathlib import Path
from typing import Iterator

from fastapi import HTTPException, Request
from fastapi.responses import PlainTextResponse, Response, StreamingResponse

logger = logging.getLogger("naspilot.english.stream")

CHUNK_SIZE = 256 * 1024

_RANGE = re.compile(r"bytes=(?P<start>\d*)-(?P<end>\d*)")

_EXT_MIME = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".flac": "audio/flac",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".opus": "audio/opus",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def guess_mime(path: Path) -> str:
    return _EXT_MIME.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"


def _iter_file(path: Path, start: int, length: int) -> Iterator[bytes]:
    remaining = length
    with path.open("rb") as handle:
        handle.seek(start)
        while remaining > 0:
            chunk = handle.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def stream_file(path: Path, request: Request) -> Response:
    """Serve ``path`` honouring a single-range ``Range`` header."""
    if not path.is_file():
        raise HTTPException(status_code=404, detail="媒体文件不存在")

    size = path.stat().st_size
    mime = guess_mime(path)
    raw_range = request.headers.get("range") or request.headers.get("Range")

    if not raw_range:
        headers = {
            "Content-Length": str(size),
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, max-age=0, must-revalidate",
        }
        return StreamingResponse(
            _iter_file(path, 0, size), media_type=mime, headers=headers, status_code=200
        )

    match = _RANGE.search(raw_range)
    if not match:
        raise HTTPException(
            status_code=416,
            detail="Range 格式不支持",
            headers={"Content-Range": f"bytes */{size}"},
        )

    start_raw, end_raw = match.group("start"), match.group("end")
    if start_raw == "" and end_raw == "":
        raise HTTPException(
            status_code=416, detail="Range 无效", headers={"Content-Range": f"bytes */{size}"}
        )

    if start_raw == "":
        # suffix range: last N bytes
        length = min(int(end_raw), size)
        start = max(size - length, 0)
        end = size - 1
    else:
        start = int(start_raw)
        end = int(end_raw) if end_raw else size - 1
        end = min(end, size - 1)

    if start > end or start >= size:
        raise HTTPException(
            status_code=416,
            detail="Range 超出文件范围",
            headers={"Content-Range": f"bytes */{size}"},
        )

    length = end - start + 1
    headers = {
        "Content-Range": f"bytes {start}-{end}/{size}",
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Cache-Control": "private, max-age=0, must-revalidate",
    }
    return StreamingResponse(
        _iter_file(path, start, length), media_type=mime, headers=headers, status_code=206
    )


def subtitle_text(path: Path) -> PlainTextResponse:
    """Return subtitle content as UTF-8 text (the client parses it itself)."""
    if not path.is_file():
        raise HTTPException(status_code=404, detail="字幕文件不存在")
    from app.services.english.subtitles import read_text

    body = read_text(path)
    return PlainTextResponse(
        content=body,
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "private, max-age=0, must-revalidate"},
    )


def ensure_readable(path: Path) -> None:
    """Reject directories / unreadable files early with a clear message."""
    if not path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    if not os.access(path, os.R_OK):
        raise HTTPException(status_code=403, detail="文件不可读（检查挂载权限）")


__all__ = ["ensure_readable", "guess_mime", "stream_file", "subtitle_text"]
