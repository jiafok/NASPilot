"""Plugin endpoints — list, enable/disable, instance CRUD."""

import json
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.database import get_db
from app.core.deps import CurrentUser
from app.models import Plugin, PluginInstance
from app.schemas.plugin import PluginInstanceCreate, PluginInstanceOut, PluginInstanceUpdate, PluginOut
from app.plugins.registry import registry
from app.services.notification_service import notify_default_channels

logger = logging.getLogger("naspilot")

router = APIRouter(prefix="/plugins", tags=["plugins"])


@router.get("", response_model=list[PluginOut], summary="List all plugins")
async def list_plugins(user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    from sqlalchemy import func

    result = await db.execute(
        select(
            Plugin,
            func.count(PluginInstance.id).label("instance_count"),
        )
        .outerjoin(PluginInstance, PluginInstance.plugin_id == Plugin.id)
        .group_by(Plugin.id)
        .order_by(Plugin.category, Plugin.name)
    )
    plugins = []
    for p, count in result.all():
        setattr(p, 'instance_count', count)
        plugins.append(p)
    return plugins


@router.get("/{plugin_id}", response_model=PluginOut, summary="Get plugin")
async def get_plugin(plugin_id: int, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    from sqlalchemy import func

    result = await db.execute(
        select(
            Plugin,
            func.count(PluginInstance.id).label("instance_count"),
        )
        .outerjoin(PluginInstance, PluginInstance.plugin_id == Plugin.id)
        .where(Plugin.id == plugin_id)
        .group_by(Plugin.id)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Plugin not found")
    plugin, count = row
    setattr(plugin, 'instance_count', count)
    return plugin


@router.post("/{plugin_id}/enable", summary="Enable plugin")
async def enable_plugin(plugin_id: int, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(select(Plugin).where(Plugin.id == plugin_id))
    plugin = result.scalar_one_or_none()
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")
    plugin.enabled = True
    await db.commit()
    return {"message": "enabled"}


@router.post("/{plugin_id}/disable", summary="Disable plugin")
async def disable_plugin(plugin_id: int, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(select(Plugin).where(Plugin.id == plugin_id))
    plugin = result.scalar_one_or_none()
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")
    plugin.enabled = False
    await db.commit()
    return {"message": "disabled"}


@router.post("/{plugin_id}/run", summary="Run plugin action")
async def run_plugin(plugin_id: int, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(select(Plugin).where(Plugin.id == plugin_id))
    plugin = result.scalar_one_or_none()
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")

    instance_result = await db.execute(
        select(PluginInstance).where(PluginInstance.plugin_id == plugin_id, PluginInstance.enabled.is_(True)).order_by(PluginInstance.id)
    )
    instance = instance_result.scalars().first()

    plugin_cls = None
    for slug, cls in registry.list_all():
        if slug == plugin.slug:
            plugin_cls = cls
            break
    if plugin_cls is None:
        raise HTTPException(status_code=400, detail="Plugin runtime is not registered")

    runtime = plugin_cls(instance.config if instance else None)
    result_payload = await runtime.run()

    # Save plugin config + run history back to DB
    if instance:
        now_iso = datetime.now(timezone.utc).isoformat()
        state = runtime.config.setdefault("state", {})
        history: list = state.setdefault("run_history", [])
        history.insert(0, {
            "time": now_iso,
            "status": result_payload.get("status", "ok"),
            "added": result_payload.get("added", 0),
            "error": result_payload.get("error", ""),
            "summary": json.dumps({k: v for k, v in result_payload.items()
                if k not in ("added_messages", "failed_messages", "deleted_messages", "skipped_messages")},
                ensure_ascii=False, default=str),
        })
        state["run_history"] = history[:50]  # keep last 50 runs
        instance.config = runtime.config
        flag_modified(instance, "config")  # SQLAlchemy JSON column needs explicit dirty flag
        await db.commit()

    # Log execution result
    logger.info(f"Plugin [{plugin.slug}] run result: {json.dumps({k: str(v)[:200] for k, v in result_payload.items() if k != 'added_messages' and k != 'failed_messages' and k != 'deleted_messages' and k != 'skipped_messages'}, ensure_ascii=False)}")

    return {"message": "run started", "result": result_payload}


# ── Plugin Instances ────────────────────────────────────────────────────


@router.get("/{plugin_id}/instances", response_model=list[PluginInstanceOut], summary="List instances")
async def list_instances(plugin_id: int, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(
        select(PluginInstance).where(PluginInstance.plugin_id == plugin_id).order_by(PluginInstance.id)
    )
    return result.scalars().all()


@router.post(
    "/{plugin_id}/instances", response_model=PluginInstanceOut, status_code=201, summary="Create instance"
)
async def create_instance(
    plugin_id: int,
    body: PluginInstanceCreate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(Plugin).where(Plugin.id == plugin_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Plugin not found")
    inst = PluginInstance(plugin_id=plugin_id, **body.model_dump())
    db.add(inst)
    await db.commit()
    await db.refresh(inst)
    return inst


@router.put(
    "/instances/{instance_id}", response_model=PluginInstanceOut, summary="Update instance"
)
async def update_instance(
    instance_id: int,
    body: PluginInstanceUpdate,
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    result = await db.execute(select(PluginInstance).where(PluginInstance.id == instance_id))
    inst = result.scalar_one_or_none()
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        if k == "config" and isinstance(v, dict):
            # Deep-merge: preserve runtime state (processed, run_history, daily)
            existing = inst.config or {}
            inst.config = {**existing, **v}
            # Nested merge for "state" key to avoid losing runtime data
            if "state" in existing and "state" in v:
                inst.config["state"] = {**existing["state"], **v["state"]}
            flag_modified(inst, "config")  # SQLAlchemy JSON column needs explicit dirty flag
        else:
            setattr(inst, k, v)
    await db.commit()
    await db.refresh(inst)

    # Reschedule plugin if config contains schedule settings
    from app.scheduler.scheduler_service import get_scheduler, upsert_plugin_schedule
    upsert_plugin_schedule(get_scheduler(), inst.plugin_id, inst.id, inst.config or {})

    return inst


@router.delete("/instances/{instance_id}", summary="Delete instance")
async def delete_instance(instance_id: int, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(select(PluginInstance).where(PluginInstance.id == instance_id))
    inst = result.scalar_one_or_none()
    if not inst:
        raise HTTPException(status_code=404, detail="Instance not found")
    plugin_name = inst.plugin_id
    await db.delete(inst)
    await db.commit()
    # ── Notify ──
    await notify_default_channels(
        db,
        title="🗑️ 插件实例已删除",
        message=f"插件「{plugin_name}」实例(ID:{instance_id}) 已被删除",
        level="warn",
        event_type="plugin_deleted",
    )
    return {"message": "deleted"}
