"""File browser — browse directories, read text files on the NAS."""

import os
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse, FileResponse

from app.core.deps import CurrentUser

router = APIRouter(prefix="/files", tags=["files"])

# Only allow browsing Docker-mapped volumes — not the container filesystem
SAFE_ROOTS = [
    "/app/data",       # NASPilot data (config + logs)
    "/volume1",         # NAS docker volumes
    "/volumeUSB1",      # USB backup
    "/scripts",         # User scripts (read-only)
]


# Human-readable labels for the top-level mount points
ROOT_LABELS: dict[str, str] = {
    "/app/data": "📁 NASPilot 数据",
    "/volume1": "💾 NAS 存储池",
    "/volumeUSB1": "🔌 USB 外接存储",
    "/scripts": "📜 脚本目录",
}

def _safe_path(path: str) -> str:
    """Resolve and validate a path against safe roots."""
    real = os.path.realpath(os.path.abspath(path))
    for root in SAFE_ROOTS:
        root_real = os.path.realpath(root)
        if real == root_real or real.startswith(root_real + os.sep):
            return real
    raise HTTPException(status_code=403, detail=f"Access denied: {path}")


@router.get("/list", summary="List directory contents")
async def list_dir(
    user: CurrentUser,
    path: str = Query("/", description="Directory path to list"),
):
    """List files and directories at the given path.
    
    At the root level (/), returns only the Docker-mapped mount points
    (SAFE_ROOTS) instead of the real container filesystem.
    """
    # Virtual root: show only safe mount points that actually exist
    if path == "/":
        entries = []
        for root in SAFE_ROOTS:
            real_root = os.path.realpath(root)
            if os.path.isdir(real_root):
                label = ROOT_LABELS.get(root, os.path.basename(root) or root)
                entries.append({
                    "name": label,
                    "real_path": root,
                    "is_dir": True,
                    "size": 0,
                    "mtime": 0,
                    "is_text": False,
                    "is_root": True,  # frontend uses this to show special styling
                })
        return {"path": "/", "entries": entries}

    try:
        safe = _safe_path(path)
    except HTTPException:
        safe = "/"
        try:
            safe = _safe_path("/")
        except HTTPException:
            return {"path": "/", "entries": [], "error": "Cannot access path"}

    if not os.path.isdir(safe):
        # If it's a file, show its parent directory
        safe = os.path.dirname(safe)

    entries = []
    try:
        for name in sorted(os.listdir(safe)):
            full = os.path.join(safe, name)
            is_dir = os.path.isdir(full)
            try:
                size = os.path.getsize(full) if not is_dir else 0
                mtime = os.path.getmtime(full)
            except OSError:
                size = 0
                mtime = 0
            # Determine if text file
            ext = os.path.splitext(name)[1].lower()
            is_text = ext in (".txt", ".log", ".json", ".yml", ".yaml", ".xml", ".csv",
                             ".md", ".py", ".js", ".ts", ".tsx", ".html", ".css",
                             ".env", ".cfg", ".conf", ".ini", ".sh", ".sql", ".toml")
            entries.append({
                "name": name,
                "real_path": os.path.join(safe, name),
                "is_dir": is_dir,
                "size": size,
                "mtime": mtime,
                "is_text": is_text,
                "is_root": False,
            })
    except PermissionError:
        return {"path": safe, "entries": [], "error": "Permission denied"}

    return {"path": safe, "entries": entries}


@router.get("/read", summary="Read a text file", response_class=PlainTextResponse)
async def read_file(
    user: CurrentUser,
    path: str = Query(..., description="File path to read"),
    limit: int = Query(10000, ge=100, le=100000),
):
    """Read the contents of a text file."""
    safe = _safe_path(path)
    if not os.path.isfile(safe):
        raise HTTPException(status_code=404, detail="File not found")
    size = os.path.getsize(safe)
    # Don't serve binary files
    ext = os.path.splitext(safe)[1].lower()
    text_exts = {".txt", ".log", ".json", ".yml", ".yaml", ".xml", ".csv",
                 ".md", ".py", ".js", ".ts", ".tsx", ".html", ".css",
                 ".env", ".cfg", ".conf", ".ini", ".sh", ".sql", ".toml"}
    if ext not in text_exts and size > 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large or not a text file")
    try:
        with open(safe, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        raise HTTPException(status_code=400, detail="Cannot read file")
    if len(lines) > limit:
        lines = lines[-limit:]
    return PlainTextResponse("".join(lines))


@router.get("/download", summary="Download a file")
async def download_file(
    user: CurrentUser,
    path: str = Query(..., description="File path to download"),
):
    """Download any file."""
    safe = _safe_path(path)
    if not os.path.isfile(safe):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(safe, filename=os.path.basename(safe))
