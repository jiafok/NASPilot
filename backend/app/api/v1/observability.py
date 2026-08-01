"""Observability endpoints — unified execution results and cross-domain status."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.core.database import get_db
from app.core.deps import CurrentUser
from app.models import Plugin, PluginInstance, Task, TaskExecution
from app.schemas.observability import (
    ActivityEventType,
    ActivityTimeline,
    ActivityTimelineEntry,
    ApplicationDomainStatus,
    ContainerDomainStatus,
    ExecutionCounters,
    FileDomainStatus,
    ObservabilityOverview,
    TaskDomainStatus,
    UnifiedExecutionFeed,
    UnifiedExecutionResult,
)
from app.services.docker_service import list_containers
from app.services.system_service import get_system_stats

router = APIRouter(prefix="/observability", tags=["observability"])


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _to_counters(payload: dict[str, Any], record: dict[str, Any] | None = None) -> ExecutionCounters:
    record = record or {}
    failed_messages = payload.get("failed_messages")
    skipped_messages = payload.get("skipped_messages")
    deleted_messages = payload.get("deleted_messages")
    added_messages = payload.get("added_messages")

    added = int(payload.get("added", record.get("added", 0)) or 0)
    deleted = int(payload.get("deleted", 0) or 0)
    uploaded = int(payload.get("uploaded", 0) or 0)
    skipped = int(payload.get("skipped", 0) or 0)
    failed = int(payload.get("failed", 0) or 0)
    unchanged = int(payload.get("unchanged", 0) or 0)
    pending = int(payload.get("pending", 0) or 0)

    if isinstance(added_messages, list) and added == 0:
        added = len(added_messages)
    if isinstance(deleted_messages, list) and deleted == 0:
        deleted = len(deleted_messages)
    if isinstance(skipped_messages, list) and skipped == 0:
        skipped = len(skipped_messages)
    if isinstance(failed_messages, list) and failed == 0:
        failed = len(failed_messages)

    # Compatibility: Cloudflare DDNS exposes updated as the main changed counter.
    if added == 0:
        added = int(payload.get("updated", 0) or 0)

    return ExecutionCounters(
        added=added,
        deleted=deleted,
        uploaded=uploaded,
        skipped=skipped,
        failed=failed,
        unchanged=unchanged,
        pending=pending,
    )


def _normalize_app_status(status: str) -> str:
    s = (status or "").lower()
    if s in {"error", "failed"}:
        return "failed"
    if s in {"timeout"}:
        return "timeout"
    if s in {"skip", "skipped"}:
        return "skipped"
    if s in {"running"}:
        return "running"
    return "ok"


@router.get("/executions/unified", response_model=UnifiedExecutionFeed, summary="Unified execution feed")
async def unified_execution_feed(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    hours: int = Query(24, ge=1, le=168),
    limit: int = Query(200, ge=20, le=2000),
):
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    items: list[UnifiedExecutionResult] = []

    # Task executions
    task_rows = await db.execute(
        select(TaskExecution)
        .options(joinedload(TaskExecution.task))
        .where(TaskExecution.start_time >= since)
        .order_by(TaskExecution.start_time.desc())
        .limit(limit)
    )
    for e in task_rows.scalars().all():
        task_name = e.task.name if e.task else f"task-{e.task_id}"
        status = "failed" if e.status == "failed" else e.status
        if status not in {"ok", "warning", "failed", "timeout", "skipped", "running"}:
            status = "ok" if e.status == "success" else "warning"
        failure_reasons = [e.error_message] if e.error_message else []
        event_type = _infer_event_type("task", f"task_{e.task_id}", status, ExecutionCounters(failed=1 if e.status in {"failed", "timeout"} else 0, pending=1 if e.status in {"pending", "running"} else 0))
        items.append(
            UnifiedExecutionResult(
                execution_id=f"task:{e.id}",
                domain="task",
                source_slug=f"task_{e.task_id}",
                source_name=task_name,
                trigger=e.triggered_by or "unknown",
                status=status,  # type: ignore[arg-type]
                event_type=event_type,
                started_at=e.start_time,
                ended_at=e.end_time,
                duration_ms=e.duration_ms,
                counters=ExecutionCounters(
                    failed=1 if e.status in {"failed", "timeout"} else 0,
                    pending=1 if e.status in {"pending", "running"} else 0,
                ),
                failure_reasons=failure_reasons,
                evidence_refs=[f"/api/v1/tasks/{e.task_id}/log"],
            )
        )

    # Application/plugin executions from run_history
    plugin_rows = await db.execute(
        select(PluginInstance, Plugin)
        .join(Plugin, Plugin.id == PluginInstance.plugin_id)
        .order_by(PluginInstance.id)
    )
    for inst, plugin in plugin_rows.all():
        cfg = inst.config or {}
        state = cfg.get("state") if isinstance(cfg, dict) else {}
        if not isinstance(state, dict):
            continue
        history = state.get("run_history")
        if not isinstance(history, list):
            continue
        for i, rec in enumerate(history):
            if not isinstance(rec, dict):
                continue
            dt = _parse_dt(rec.get("time"))
            if dt is None or dt < since:
                continue

            summary_raw = rec.get("summary")
            summary_obj: dict[str, Any] = {}
            if isinstance(summary_raw, dict):
                summary_obj = summary_raw
            elif isinstance(summary_raw, str) and summary_raw.strip():
                try:
                    parsed = json.loads(summary_raw)
                    if isinstance(parsed, dict):
                        summary_obj = parsed
                except json.JSONDecodeError:
                    summary_obj = {}

            counters = _to_counters(summary_obj, rec)
            status = _normalize_app_status(str(rec.get("status") or summary_obj.get("status") or "ok"))
            error_text = str(rec.get("error") or summary_obj.get("error") or "").strip()

            items.append(
                UnifiedExecutionResult(
                    execution_id=f"app:{inst.id}:{i}:{int(dt.timestamp())}",
                    domain="application",
                    source_slug=plugin.slug,
                    source_name=plugin.name,
                    trigger=str(rec.get("trigger") or "manual"),
                    status=status,  # type: ignore[arg-type]
                    event_type=_infer_event_type("application", plugin.slug, status, counters),
                    started_at=dt,
                    counters=counters,
                    skip_reasons=[error_text] if status == "skipped" and error_text else [],
                    failure_reasons=[error_text] if status in {"failed", "timeout"} and error_text else [],
                    evidence_refs=[f"plugin_instance:{inst.id}", f"plugin_slug:{plugin.slug}"],
                )
            )

            # File-domain projection — Phase 2: expanded to all file-contributing plugins
            if plugin.slug in _FILE_DOMAIN_SLUGS:
                file_deleted = counters.deleted if plugin.slug == "log_cleanup" else counters.deleted
                file_status = "ok" if counters.failed == 0 else "failed"
                if counters.skipped > 0:
                    file_status = "warning"
                file_counters = ExecutionCounters(
                    uploaded=counters.uploaded,
                    failed=counters.failed,
                    skipped=counters.skipped,
                    deleted=counters.deleted,
                )
                items.append(
                    UnifiedExecutionResult(
                        execution_id=f"file:{inst.id}:{i}:{int(dt.timestamp())}",
                        domain="file",
                        source_slug=plugin.slug,
                        source_name=plugin.name,
                        trigger=str(rec.get("trigger") or "manual"),
                        status=file_status,  # type: ignore[arg-type]
                        event_type=_infer_event_type("file", plugin.slug, file_status, file_counters),
                        started_at=dt,
                        counters=file_counters,
                        failure_reasons=[error_text] if counters.failed > 0 and error_text else [],
                        evidence_refs=[f"plugin_instance:{inst.id}", f"source:{plugin.slug}"],
                    )
                )

    # Current container status snapshot as execution-style records (domain standardization)
    try:
        containers = await asyncio.to_thread(list_containers, include_all=True)
    except Exception:
        containers = []
    snapshot_time = now
    for c in containers:
        if not isinstance(c, dict):
            continue
        running = bool(c.get("running", False))
        state = str(c.get("state") or c.get("status") or "").lower()
        abnormal = (not running) or ("unhealthy" in state) or (state in {"dead", "restarting", "exited"})
        status = "warning" if abnormal else "ok"
        items.append(
            UnifiedExecutionResult(
                execution_id=f"container:{c.get('id','unknown')}:{int(snapshot_time.timestamp())}",
                domain="container",
                source_slug=str(c.get("name") or c.get("id") or "container"),
                source_name=str(c.get("name") or c.get("id") or "container"),
                trigger="system",
                status=status,  # type: ignore[arg-type]
                event_type="container_abnormal" if abnormal else "execution_succeeded",
                started_at=snapshot_time,
                counters=ExecutionCounters(failed=1 if abnormal else 0),
                failure_reasons=[f"state={state}"] if abnormal else [],
                evidence_refs=[f"container_id:{c.get('id','')}", "api:/api/v1/system/docker/containers"],
            )
        )

    items.sort(key=lambda x: x.started_at, reverse=True)
    final_items = items[:limit]
    return UnifiedExecutionFeed(
        generated_at=now,
        hours=hours,
        total=len(final_items),
        items=final_items,
    )


@router.get("/overview", response_model=ObservabilityOverview, summary="Cross-domain observability overview")
async def observability_overview(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    hours: int = Query(24, ge=1, le=168),
):
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)

    # Task domain
    task_exec_rows = await db.execute(
        select(TaskExecution.status)
        .where(TaskExecution.start_time >= since)
    )
    success_24h = failed_24h = timeout_24h = running_now = 0
    for (status,) in task_exec_rows.all():
        if status == "success":
            success_24h += 1
        elif status == "failed":
            failed_24h += 1
        elif status == "timeout":
            timeout_24h += 1
        elif status == "running":
            running_now += 1

    pending_rows = await db.execute(
        select(Task.id).where(
            and_(
                Task.enabled.is_(True),
                Task.cron_expr.is_not(None),
                Task.next_run_at.is_not(None),
                Task.next_run_at >= now,
            )
        )
    )
    pending_count = len(pending_rows.all())

    task_status = TaskDomainStatus(
        success_24h=success_24h,
        failed_24h=failed_24h,
        timeout_24h=timeout_24h,
        running_now=running_now,
        pending_count=pending_count,
    )

    # Application domain + file projection
    app_ok = app_failed = app_skipped = 0
    app_counters = ExecutionCounters()
    file_uploaded_ok = file_uploaded_failed = file_deleted = 0
    plugin_rows = await db.execute(
        select(PluginInstance, Plugin)
        .join(Plugin, Plugin.id == PluginInstance.plugin_id)
    )

    for inst, plugin in plugin_rows.all():
        cfg = inst.config or {}
        state = cfg.get("state") if isinstance(cfg, dict) else {}
        if not isinstance(state, dict):
            continue
        history = state.get("run_history")
        if not isinstance(history, list):
            continue
        for rec in history:
            if not isinstance(rec, dict):
                continue
            dt = _parse_dt(rec.get("time"))
            if dt is None or dt < since:
                continue
            summary_obj: dict[str, Any] = {}
            summary_raw = rec.get("summary")
            if isinstance(summary_raw, dict):
                summary_obj = summary_raw
            elif isinstance(summary_raw, str) and summary_raw.strip():
                try:
                    parsed = json.loads(summary_raw)
                    if isinstance(parsed, dict):
                        summary_obj = parsed
                except json.JSONDecodeError:
                    pass

            counters = _to_counters(summary_obj, rec)
            app_counters.added += counters.added
            app_counters.deleted += counters.deleted
            app_counters.uploaded += counters.uploaded
            app_counters.skipped += counters.skipped
            app_counters.failed += counters.failed
            app_counters.unchanged += counters.unchanged
            app_counters.pending += counters.pending

            status = _normalize_app_status(str(rec.get("status") or summary_obj.get("status") or "ok"))
            if status == "ok":
                app_ok += 1
            elif status in {"failed", "timeout"}:
                app_failed += 1
            elif status == "skipped":
                app_skipped += 1

            if plugin.slug in _FILE_DOMAIN_SLUGS:
                file_uploaded_ok += counters.uploaded
                file_uploaded_failed += counters.failed
                file_deleted += counters.deleted

    app_status = ApplicationDomainStatus(
        ok_24h=app_ok,
        failed_24h=app_failed,
        skipped_24h=app_skipped,
        counters_24h=app_counters,
    )

    # Container domain
    abnormal_names: list[str] = []
    running = stopped = error = 0
    try:
        containers = await asyncio.to_thread(list_containers, include_all=True)
    except Exception:
        containers = []
    for c in containers:
        if not isinstance(c, dict):
            continue
        is_running = bool(c.get("running", False))
        state = str(c.get("state") or c.get("status") or "").lower()
        if is_running:
            running += 1
        else:
            stopped += 1
        if (not is_running) or ("unhealthy" in state) or (state in {"dead", "restarting", "exited"}):
            error += 1
            abnormal_names.append(str(c.get("name") or c.get("id") or "unknown"))

    container_status = ContainerDomainStatus(
        running=running,
        stopped=stopped,
        error=error,
        abnormal_containers=abnormal_names[:20],
    )

    # File domain
    stats = await asyncio.to_thread(get_system_stats)
    disk_percent = float(stats.get("disk_percent", 0.0) or 0.0)
    file_status = FileDomainStatus(
        storage_usage_percent=disk_percent,
        uploaded_success_24h=file_uploaded_ok,
        uploaded_failed_24h=file_uploaded_failed,
        deleted_24h=file_deleted,
        recent_changes_24h=file_uploaded_ok + file_uploaded_failed + file_deleted,
    )

    return ObservabilityOverview(
        generated_at=now,
        task=task_status,
        application=app_status,
        container=container_status,
        file=file_status,
    )


def _infer_event_type(domain: str, slug: str, status: str, counters: ExecutionCounters) -> ActivityEventType | None:
    """Phase 2: Infer explicit activity timeline event type from domain + result."""
    if status == "failed" or status == "timeout":
        if domain == "task":
            return "task_failed"
        if domain == "application":
            return "plugin_failed"
        if domain == "container":
            return "container_abnormal"
        return "execution_failed"
    if counters.added > 0:
        return "item_added"
    if counters.uploaded > 0:
        return "item_uploaded"
    if counters.deleted > 0:
        return "item_deleted"
    if counters.skipped > 0:
        return "item_skipped"
    if status == "ok":
        return "execution_succeeded"
    if status == "running":
        return "execution_started"
    return None


# File-domain contributing plugins (Phase 2: expand beyond just alist_upload)
_FILE_DOMAIN_SLUGS = {"alist_upload", "log_cleanup", "docker_backup", "btrfs_cleanup"}
# ── Phase 2: Activity Timeline ────────────────────────────────────────────

@router.get("/timeline", response_model=ActivityTimeline, summary="Activity Timeline (Phase 2)")
async def activity_timeline(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    hours: int = Query(24, ge=1, le=168),
    limit: int = Query(100, ge=10, le=500),
    domain: str | None = Query(None, description="Filter by domain"),
):
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    events: list[ActivityTimelineEntry] = []

    if not domain or domain == "task":
        task_rows = await db.execute(
            select(TaskExecution)
            .options(joinedload(TaskExecution.task))
            .where(TaskExecution.start_time >= since)
            .order_by(TaskExecution.start_time.desc())
            .limit(limit)
        )
        for e in task_rows.scalars().all():
            task_name = e.task.name if e.task else f"task-{e.task_id}"
            st = "ok" if e.status == "success" else ("failed" if e.status in ("failed", "timeout") else e.status)
            et: ActivityEventType = "task_failed" if e.status in ("failed", "timeout") else "execution_succeeded"
            summary = task_name
            if e.error_message:
                summary = f"{task_name}: {e.error_message[:120]}"
            events.append(ActivityTimelineEntry(
                id=f"task:{e.id}", timestamp=e.start_time or now, event_type=et,
                domain="task", source=task_name, summary=summary,
                counters=ExecutionCounters(failed=1 if e.status in ("failed", "timeout") else 0),
            ))

    if not domain or domain in ("application", "file"):
        plugin_rows = await db.execute(
            select(PluginInstance, Plugin)
            .join(Plugin, Plugin.id == PluginInstance.plugin_id)
        )
        for inst, plugin in plugin_rows.all():
            cfg = inst.config or {}
            state = cfg.get("state") if isinstance(cfg, dict) else {}
            history = state.get("run_history") if isinstance(state, dict) else None
            if not isinstance(history, list):
                continue
            for i, rec in enumerate(history):
                if not isinstance(rec, dict):
                    continue
                dt2 = _parse_dt(rec.get("time"))
                if dt2 is None or dt2 < since:
                    continue
                summary_obj = _resolve_summary(rec)
                counters = _to_counters(summary_obj, rec)
                status = _normalize_app_status(str(rec.get("status") or summary_obj.get("status") or "ok"))
                et = _infer_event_type("application", plugin.slug, status, counters)
                if et is None:
                    et = "execution_succeeded"
                summary = _simple_summary(plugin.slug, plugin.name, counters, status)
                events.append(ActivityTimelineEntry(
                    id=f"app:{inst.id}:{i}", timestamp=dt2, event_type=et,
                    domain="application", source=plugin.name, summary=summary,
                    counters=counters,
                ))

    if not domain or domain == "container":
        try:
            containers2 = await asyncio.to_thread(list_containers, include_all=True)
        except Exception:
            containers2 = []
        for c in containers2:
            if not isinstance(c, dict):
                continue
            running = bool(c.get("running", False))
            state = str(c.get("state") or c.get("status") or "").lower()
            abnormal = (not running) or ("unhealthy" in state) or (state in {"dead", "restarting", "exited"})
            if abnormal:
                events.append(ActivityTimelineEntry(
                    id=f"container:{c.get('id','unknown')}",
                    timestamp=now,
                    event_type="container_abnormal",
                    domain="container",
                    source=str(c.get("name") or c.get("id") or "container"),
                    summary=f"state={state}",
                    counters=ExecutionCounters(failed=1),
                ))

    events.sort(key=lambda x: x.timestamp, reverse=True)
    return ActivityTimeline(generated_at=now, hours=hours, total=len(events), events=events[:limit])


def _resolve_summary(rec: dict[str, Any]) -> dict[str, Any]:
    raw = rec.get("summary")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {}


def _simple_summary(slug: str, name: str, counters: ExecutionCounters, status: str) -> str:
    parts: list[str] = []
    if counters.added > 0:
        parts.append(f"新增 {counters.added}")
    if counters.uploaded > 0:
        parts.append(f"上传 {counters.uploaded}")
    if counters.deleted > 0:
        parts.append(f"删除 {counters.deleted}")
    if counters.skipped > 0:
        parts.append(f"跳过 {counters.skipped}")
    if counters.failed > 0:
        parts.append(f"失败 {counters.failed}")
    if counters.unchanged > 0:
        parts.append(f"未变化 {counters.unchanged}")
    if parts:
        return f"{name}: {'，'.join(parts)}"
    if status == "failed":
        return f"{name}: 执行失败"
    if status == "skipped":
        return f"{name}: 已跳过"
    if status == "running":
        return f"{name}: 执行中"
    return f"{name}: 执行完成"
