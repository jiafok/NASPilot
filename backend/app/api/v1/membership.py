"""Public catalog, account status and administrator-only membership settings."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AdminUser, CurrentUser
from app.schemas.membership import AccessSettings, GrantRequest, InviteRequest
from app.services import invitation_service, membership_service


router = APIRouter(prefix="/membership", tags=["membership"])


@router.get("/admin/invitations")
async def invitations(user: AdminUser, db: Annotated[AsyncSession, Depends(get_db)],
                      page: int = Query(1, ge=1), page_size: int = Query(10, ge=1, le=100)):
    return await invitation_service.list_invites(db, page, page_size)


@router.post("/admin/invitations", status_code=201)
async def create_invitation(body: InviteRequest, user: AdminUser, db: Annotated[AsyncSession, Depends(get_db)]):
    return await invitation_service.generate(db, user.id, body.valid_days)


@router.delete("/admin/invitations/{identifier}", status_code=204)
async def delete_invitation(identifier: str, user: AdminUser, db: Annotated[AsyncSession, Depends(get_db)]):
    await invitation_service.delete_invite(db, identifier)
    return Response(status_code=204)


@router.get("/catalog")
async def catalog(db: Annotated[AsyncSession, Depends(get_db)]):
    return await membership_service.catalog(db)


@router.get("/status")
async def status(user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    return await membership_service.status_for(db, user)


@router.get("/admin/settings")
async def settings(user: AdminUser, db: Annotated[AsyncSession, Depends(get_db)]):
    return await membership_service.admin_settings(db)


@router.put("/admin/settings")
async def update_settings(body: AccessSettings, user: AdminUser, db: Annotated[AsyncSession, Depends(get_db)]):
    return await membership_service.save_settings(db, body)


@router.post("/admin/users/{user_id}/grant")
async def grant(user_id: int, body: GrantRequest, user: AdminUser, db: Annotated[AsyncSession, Depends(get_db)]):
    return await membership_service.grant(db, user_id, user, body)