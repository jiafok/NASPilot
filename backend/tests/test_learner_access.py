import unittest
from unittest.mock import AsyncMock
from datetime import timedelta
from uuid import uuid4

import httpx
from fastapi import HTTPException
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models
from app.core.database import Base, get_db
from app.core.deps import get_current_user_ws
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models import EnglishDailyStat, RefreshToken, SmsChallenge, User


class LearnerAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        @event.listens_for(self.engine.sync_engine, "connect")
        def enable_foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)

        async def test_db():
            async with self.sessions() as session:
                yield session

        app.dependency_overrides[get_db] = test_db
        async with self.sessions() as session:
            admin = User(username="admin", hashed_password=hash_password("test-password"), is_admin=True, is_active=True)
            learner = User(username="learner", hashed_password=hash_password("test-password"), is_admin=False, is_active=True)
            session.add_all([admin, learner])
            await session.commit()
            self.admin_token = create_access_token(admin.id)
            self.learner_token = create_access_token(learner.id)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")

    async def asyncTearDown(self):
        await self.client.aclose()
        app.dependency_overrides.clear()
        await self.engine.dispose()

    async def verified_registration(self, username: str, password: str):
        from app.services.membership_service import now_utc
        from app.services.sms_service import _digest

        identifier = uuid4().hex
        async with self.sessions() as session:
            session.add(SmsChallenge(id=identifier, phone="+8613900000001", ip_hash="test", code_hash=_digest(identifier + ":123456"),
                                     sent_at=now_utc(), expires_at=now_utc() + timedelta(minutes=5), state="sent", attempts=0))
            await session.commit()
        return await self.client.post("/api/v1/auth/register", json={"username": username, "password": password, "phone": "13900000001", "verification_code": "123456"})

    async def test_register_login_and_no_privilege_escalation(self):
        response = await self.verified_registration("newlearner", "learn-password")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertFalse(response.json()["is_admin"])
        login = await self.client.post("/api/v1/auth/login", json={"username": "newlearner", "password": "learn-password"})
        self.assertEqual(login.status_code, 200, login.text)
        self.assertFalse(login.json()["user"]["is_admin"])
        duplicate = await self.client.post("/api/v1/auth/register", json={"username": "newlearner", "password": "learn-password", "phone": "13900000001", "verification_code": "123456"})
        self.assertEqual(duplicate.status_code, 409)
        escalation = await self.client.post("/api/v1/auth/register", json={"username": "badactor", "password": "learn-password", "is_admin": True})
        self.assertEqual(escalation.status_code, 422)
        short_password = await self.client.post("/api/v1/auth/register", json={"username": "otherlearner", "password": "short"})
        self.assertEqual(short_password.status_code, 422)

    async def test_operations_denied_but_learning_allowed(self):
        headers = {"Authorization": f"Bearer {self.learner_token}"}
        for path in ("/api/v1/system/settings", "/api/v1/media/providers/simple", "/api/media/providers/simple", "/health/media", "/api/v1/english/admin/stats", "/api/english/admin/lessons"):
            response = await self.client.get(path, headers=headers)
            self.assertEqual(response.status_code, 403, (path, response.text))
        denied_scan = await self.client.post("/api/v1/english/admin/scan", headers=headers)
        self.assertEqual(denied_scan.status_code, 403)
        home = await self.client.get("/api/v1/english/home", headers=headers)
        self.assertEqual(home.status_code, 200, home.text)
        admin = await self.client.get("/api/v1/system/settings", headers={"Authorization": f"Bearer {self.admin_token}"})
        self.assertEqual(admin.status_code, 200)

    async def test_capacitor_origins_can_preflight_api_requests(self):
        for origin in ("http://localhost", "https://localhost", "capacitor://localhost"):
            response = await self.client.options(
                "/api/v1/auth/login",
                headers={
                    "Origin": origin,
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "content-type,authorization",
                },
            )
            self.assertEqual(response.status_code, 200, (origin, response.text))
            self.assertEqual(response.headers.get("access-control-allow-origin"), origin)

    async def test_websocket_auth_rejects_learners(self):
        from starlette.websockets import WebSocket

        async with self.sessions() as session:
            for token, expected in ((self.learner_token, False), (self.admin_token, True)):
                websocket = WebSocket(
                    {"type": "websocket", "query_string": f"token={token}".encode(), "headers": []},
                    receive=AsyncMock(), send=AsyncMock(),
                )
                user = await get_current_user_ws(websocket, session)
                self.assertEqual(user is not None, expected)

    async def test_user_management_requires_admin(self):
        headers = {"Authorization": f"Bearer {self.learner_token}"}
        for method, path, body in (
            ("GET", "/api/v1/users", None),
            ("POST", "/api/v1/users", {"username": "created", "password": "valid-password"}),
            ("DELETE", "/api/v1/users/1", None),
        ):
            response = await self.client.request(method, path, headers=headers, json=body)
            self.assertEqual(response.status_code, 403, response.text)

    async def test_admin_can_create_search_and_delete(self):
        headers = {"Authorization": f"Bearer {self.admin_token}"}
        body = {"username": "created", "password": "valid-password", "display_name": "Test learner"}
        created = await self.client.post("/api/v1/users", json=body, headers=headers)
        self.assertEqual(created.status_code, 201, created.text)
        self.assertFalse(created.json()["is_admin"])
        self.assertNotIn("hashed_password", created.json())
        duplicate = await self.client.post("/api/v1/users", json=body, headers=headers)
        self.assertEqual(duplicate.status_code, 409)
        listed = await self.client.get("/api/v1/users", params={"q": "Test learner"}, headers=headers)
        self.assertEqual(listed.json()["total"], 1)
        self.assertEqual(listed.json()["items"][0]["username"], "created")
        deleted = await self.client.delete(f"/api/v1/users/{created.json()['id']}", headers=headers)
        self.assertEqual(deleted.status_code, 204, deleted.text)
        missing = await self.client.delete(f"/api/v1/users/{created.json()['id']}", headers=headers)
        self.assertEqual(missing.status_code, 404)

    async def test_deletion_cascades_and_same_name_recreation_does_not_revive_tokens(self):
        headers = {"Authorization": f"Bearer {self.admin_token}"}
        login = await self.client.post("/api/v1/auth/login", json={"username": "learner", "password": "test-password"})
        old = login.json()
        user_id = old["user"]["id"]
        async with self.sessions() as session:
            session.add(EnglishDailyStat(user_id=user_id, day="2026-10-28", listen_seconds=120))
            await session.commit()
        deleted = await self.client.delete(f"/api/v1/users/{user_id}", headers=headers)
        self.assertEqual(deleted.status_code, 204, deleted.text)
        async with self.sessions() as session:
            for model in (EnglishDailyStat, RefreshToken):
                count = (await session.execute(select(func.count()).select_from(model).where(model.user_id == user_id))).scalar()
                self.assertEqual(count, 0)
        replacement = await self.verified_registration("learner", "replacement-password")
        self.assertEqual(replacement.status_code, 201, replacement.text)
        self.assertGreater(replacement.json()["id"], user_id)
        old_access = await self.client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {old['access_token']}"})
        self.assertEqual(old_access.status_code, 401)
        old_refresh = await self.client.post("/api/v1/auth/refresh", json={"refresh_token": old["refresh_token"]})
        self.assertEqual(old_refresh.status_code, 401)

    async def test_self_and_last_admin_deletion_are_blocked(self):
        response = await self.client.delete("/api/v1/users/1", headers={"Authorization": f"Bearer {self.admin_token}"})
        self.assertEqual(response.status_code, 400)
        from app.services.user_service import delete_user

        async with self.sessions() as session:
            with self.assertRaises(HTTPException) as caught:
                await delete_user(session, user_id=1, actor_id=999)
            self.assertEqual(caught.exception.status_code, 400)

    async def test_admin_can_add_another_admin(self):
        response = await self.client.post(
            "/api/v1/users",
            json={"username": "secondadmin", "password": "valid-password", "is_admin": True},
            headers={"Authorization": f"Bearer {self.admin_token}"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertTrue(response.json()["is_admin"])