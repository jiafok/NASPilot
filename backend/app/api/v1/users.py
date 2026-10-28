"""Administrator-only user management."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.deps import AdminUser
from app.schemas.auth import UserOut
from app.schemas.user import UserCreate, UserPage
from app.services import user_service


router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=UserPage)
async def users(
    user: AdminUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    q: str = Query("", max_length=128),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    return await user_service.list_users(db, q=q, page=page, page_size=page_size)


@router.post("", response_model=UserOut, status_code=201)
async def add_user(body: UserCreate, user: AdminUser, db: Annotated[AsyncSession, Depends(get_db)]):
    return await user_service.create_user(db, body, admin=body.is_admin)


@router.delete("/{user_id}", status_code=204)
async def remove_user(user_id: int, user: AdminUser, db: Annotated[AsyncSession, Depends(get_db)]):
    await user_service.delete_user(db, user_id, user.id)
    return Response(status_code=204)