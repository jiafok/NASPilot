"""Plugin endpoints — list, enable/disable, instance CRUD."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import CurrentUser
from app.models import Plugin, PluginInstance
from app.schemas.plugin import PluginInstanceCreate, PluginInstanceOut, PluginInstanceUpdate, PluginOut
from app.plugins.registry import registry

router = APIRouter(prefix="/plugins", tags=["plugins"])


@router.get("", response_model=list[PluginOut], summary="List all plugins")
async def list_plugins(user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(select(Plugin).order_by(Plugin.category, Plugin.name))
    return result.scalars().all()


@router.get("/{plugin_id}", response_model=PluginOut, summary="Get plugin")
async def get_plugin(plugin_id: int, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(select(Plugin).where(Plugin.id == plugin_id))
    plugin = result.scalar_one_or_none()
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")
    return plugin


@router.post("/{plugin_id}/enable", summary="Enable plugin")
async def enable_plugin(plugin_id: int, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(select(Plugin).where(Plugin.id == plugin_id))
    plugin = result.scalar_one_or_none()
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")
    plugin.enabled = True
    await db.commit()
    registry.enable(plugin.slug)
    return {"message": "enabled"}


@router.post("/{plugin_id}/disable", summary="Disable plugin")
async def disable_plugin(plugin_id: int, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    result = await db.execute(select(Plugin).where(Plugin.id == plugin_id))
    plugin = result.scalar_one_or_none()
    if not plugin:
        raise HTTPException(status_code=404, detail="Plugin not found")
    plugin.enabled = False
    await db.commit()
    registry.disable(plugin.slug)
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
    await db.delete(inst)
    await db.commit()
    return {"message": "deleted"}
