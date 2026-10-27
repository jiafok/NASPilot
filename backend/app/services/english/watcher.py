"""自动扫描：新文件放进媒体库 → 自动分类，不需要点按钮。

为什么需要这个模块
------------------
分类本身是自动的（每个文件在 `_upsert_lesson` 里测量自己），但它只在
**一次扫描**中发生。此前扫描只能手动点「扫描媒体库」，于是"新增课时是否
自动分类"的答案是不 —— 不点就一直是旧的。

三类新文件都要覆盖，`watcher` 用同一个机制解决：

1. 媒体中心下载完成（`queue` 归档后主动请求一次）；
2. 用户通过 SMB / SFTP 手动拷入；
3. 外部脚本 / rsync 同步进来。

做法：周期性比较一个**极廉价的指纹**（文件数 + 总字节 + 最新 mtime），
变了才跑一次**增量**扫描（未变动的文件会被跳过，见 `scanner._is_unchanged`）。
因此轮询可以很密（默认 60 秒）而开销几乎为零。

并发保证
--------
`request_scan()` 是单飞的：扫描进行中再来请求只会置一个"待重跑"标记，
结束后补跑一次，绝不会并行跑两次扫描互相踩数据库。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.services.english import scanner

logger = logging.getLogger("naspilot.english.watcher")

#: 上一次成功扫描后的库指纹。
_last_fingerprint: str = ""

#: 后台主循环 / 一次性任务的强引用，避免被 GC 回收。
_loop_task: asyncio.Task[None] | None = None
_scan_task: asyncio.Task[None] | None = None

#: 扫描进行中又收到请求时置位，结束后补跑。
_pending: bool = False
_running: bool = False


def last_fingerprint() -> str:
    return _last_fingerprint


async def _run_scan(reason: str, *, with_vocabulary: bool = True) -> dict[str, Any] | None:
    """Run one incremental scan, guarding against concurrent runs."""
    global _running, _pending, _last_fingerprint

    if _running:
        _pending = True
        logger.debug("English scan already running; queued another (%s)", reason)
        return None

    _running = True
    try:
        from app.core.database import async_session_factory

        async with async_session_factory() as db:
            report = await scanner.scan(db, with_vocabulary=with_vocabulary)
        _last_fingerprint = scanner.fingerprint()
        if report.get("lessons_created") or report.get("lessons_updated"):
            logger.info(
                "Auto-scan (%s): +%s new, %s updated, %s skipped",
                reason,
                report.get("lessons_created"),
                report.get("lessons_updated"),
                report.get("lessons_skipped"),
            )
        else:
            logger.debug("Auto-scan (%s): nothing to do", reason)
        return report
    except Exception:
        logger.warning("Auto-scan (%s) failed", reason, exc_info=True)
        return None
    finally:
        _running = False
        if _pending:
            _pending = False
            # 补跑一次，覆盖扫描期间新落地的文件。
            asyncio.get_running_loop().create_task(_run_scan("pending"))


def request_scan(reason: str = "manual") -> None:
    """Ask for a background scan. Never blocks, never raises.

    下载完成后由 `queue` 调用；调用方不应该因为扫描失败而失败，所以这里
    吞掉所有异常，只记日志。
    """
    global _scan_task
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # 没有事件循环（脚本 / 测试环境）时静默跳过

    if _running:
        global _pending
        _pending = True
        return

    if _scan_task is not None and not _scan_task.done():
        return

    _scan_task = loop.create_task(_run_scan(reason))
    logger.debug("English scan scheduled (%s)", reason)


async def _poll(interval: int) -> None:
    """Watch the library and scan whenever its fingerprint changes."""
    global _last_fingerprint
    # 启动时先记下当前状态，避免开机就白跑一次全量扫描。
    _last_fingerprint = scanner.fingerprint()

    while True:
        try:
            await asyncio.sleep(interval)
            current = scanner.fingerprint()
            if current != _last_fingerprint:
                logger.info("English library changed (%s -> %s)", _last_fingerprint, current)
                await _run_scan("watcher")
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - 轮询不能因为一次异常而停掉
            logger.warning("English watcher iteration failed", exc_info=True)
            await asyncio.sleep(interval)


def start_watcher(interval: int = 60) -> None:
    """Start the periodic watcher (no-op when interval <= 0)."""
    global _loop_task
    if interval <= 0:
        logger.info("English auto-scan disabled (interval=0)")
        return
    if _loop_task is not None and not _loop_task.done():
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # pragma: no cover
        return
    _loop_task = loop.create_task(_poll(interval))
    logger.info("English auto-scan every %ss", interval)


async def stop_watcher() -> None:
    global _loop_task, _scan_task
    for task in (_loop_task, _scan_task):
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
    _loop_task = None
    _scan_task = None


__all__ = [
    "last_fingerprint",
    "request_scan",
    "start_watcher",
    "stop_watcher",
]
