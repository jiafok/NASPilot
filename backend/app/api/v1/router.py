"""API v1 router — aggregates all endpoint routers."""

from fastapi import APIRouter

from app.api.v1.ai import router as ai_router
from app.api.v1.auth import router as auth_router
from app.api.v1.notifications import router as notif_router
from app.api.v1.plugins import router as plugins_router
from app.api.v1.system import router as system_router
from app.api.v1.tasks import router as tasks_router
from app.api.v1.websocket import router as ws_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(auth_router)
api_router.include_router(tasks_router)
api_router.include_router(plugins_router)
api_router.include_router(notif_router)
api_router.include_router(system_router)
api_router.include_router(ws_router)
api_router.include_router(ai_router)
