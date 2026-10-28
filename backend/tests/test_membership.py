import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models
from app.core.database import Base, get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models import LearningMembership, MembershipGrant, RegisteredPhone, RegistrationInvite, SmsChallenge, User
from app.services import membership_service, sms_service


class MembershipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        @event.listens_for(self.engine.sync_engine, "connect")
        def foreign_keys(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async def test_db():
            async with self.sessions() as session:
                yield session
        app.dependency_overrides[get_db] = test_db
        async with self.sessions() as session:
            hashed = hash_password("test-password")
            session.add_all([User(id=1, username="admin", hashed_password=hashed, is_admin=True, is_active=True),
                             User(id=2, username="existing", hashed_password=hashed, is_admin=False, is_active=True)])
            await session.commit()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.admin = {"Authorization": f"Bearer {create_access_token(1)}"}
        self.existing = {"Authorization": f"Bearer {create_access_token(2)}"}
        self.config = {
            "sms_enabled": True, "sms_access_key_id": "test-id", "sms_access_key_secret": "test-secret",
            "sms_sign_name": "Test", "sms_template_code": "SMS_test", "contact_text": "Contact admin",
            "plans": {key: {"enabled": True, "price": "9.90"} for key in ("monthly", "quarterly", "yearly", "lifetime")},
        }

    async def asyncTearDown(self):
        app.dependency_overrides.clear()
        await self.client.aclose()
        await self.engine.dispose()

    async def configure(self):
        response = await self.client.put("/api/v1/membership/admin/settings", headers=self.admin, json=self.config)
        self.assertEqual(response.status_code, 200, response.text)
        return response

    async def issue(self, phone="13900000001"):
        captured = {}
        async def send(config, number, code):
            captured.update(phone=number, code=code)
        with patch.object(sms_service, "dispatch_sms", side_effect=send):
            response = await self.client.post("/api/v1/auth/sms-code", json={"phone": phone})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn(captured["code"], response.text)
        return captured

    async def signup(self, code, phone="13900000001", username="newlearner"):
        return await self.client.post("/api/v1/auth/register", json={"username": username, "password": "test-password", "phone": phone, "verification_code": code})

    async def test_unconfigured_sms_is_closed_and_secrets_never_public(self):
        disabled = await self.client.post("/api/v1/auth/sms-code", json={"phone": "13900000001"})
        self.assertEqual(disabled.status_code, 503)
        configured = await self.configure()
        self.assertNotIn("test-secret", configured.text)
        self.assertTrue(configured.json()["sms_secret_configured"])
        public = await self.client.get("/api/v1/membership/catalog")
        self.assertNotIn("test-secret", public.text)
        self.assertNotIn("test-id", public.text)
        generic = await self.client.get("/api/v1/system/settings", headers=self.admin)
        self.assertNotIn("LEARNING_ACCESS_CONFIG", generic.text)
        changed = {**self.config, "sms_access_key_secret": None}
        saved = await self.client.put("/api/v1/membership/admin/settings", headers=self.admin, json=changed)
        self.assertTrue(saved.json()["sms_secret_configured"])
        denied = await self.client.put("/api/v1/system/settings", headers=self.admin, json=[{"key": "LEARNING_ACCESS_CONFIG", "value": "{}"}])
        self.assertEqual(denied.status_code, 400)

    async def test_verified_unique_phone_and_trial_survives_no_reregistration(self):
        await self.configure()
        captured = await self.issue()
        response = await self.signup(captured["code"])
        self.assertEqual(response.status_code, 201, response.text)
        identifier = response.json()["id"]
        async with self.sessions() as session:
            membership = await session.get(LearningMembership, identifier)
            self.assertEqual(membership.plan, "trial")
            self.assertEqual((membership.expires_at - membership.starts_at).total_seconds(), 3 * 86400)
        duplicate = await self.signup(captured["code"], phone="+8613900000001", username="seconduser")
        self.assertEqual(duplicate.status_code, 409)
        deleted = await self.client.delete(f"/api/v1/users/{identifier}", headers=self.admin)
        self.assertEqual(deleted.status_code, 204)
        async with self.sessions() as session:
            registration = await session.get(RegisteredPhone, "+8613900000001")
            self.assertIsNone(registration.user_id)
        repeat = await self.client.post("/api/v1/auth/sms-code", json={"phone": "+8613900000001"})
        self.assertEqual(repeat.status_code, 409)

    async def test_code_rate_limit_wrong_attempts_and_expiry(self):
        await self.configure()
        captured = await self.issue()
        limited = await self.client.post("/api/v1/auth/sms-code", json={"phone": "13900000001"})
        self.assertEqual(limited.status_code, 429)
        wrong = "000000" if captured["code"] != "000000" else "111111"
        for _attempt in range(5):
            self.assertEqual((await self.signup(wrong)).status_code, 400)
        self.assertEqual((await self.signup(captured["code"])).status_code, 400)
        async with self.sessions() as session:
            challenge = (await session.execute(select(SmsChallenge))).scalar_one()
            self.assertNotEqual(challenge.code_hash, captured["code"])
            self.assertEqual(challenge.attempts, 5)
            challenge.attempts = 0
            challenge.expires_at = membership_service.now_utc() - timedelta(seconds=1)
            await session.commit()
        self.assertEqual((await self.signup(captured["code"])).status_code, 400)

    async def test_sms_failure_does_not_create_usable_code(self):
        await self.configure()
        with patch.object(sms_service, "dispatch_sms", AsyncMock(side_effect=RuntimeError("failure"))):
            response = await self.client.post("/api/v1/auth/sms-code", json={"phone": "13900000001"})
        self.assertEqual(response.status_code, 503)
        async with self.sessions() as session:
            self.assertEqual((await session.execute(select(SmsChallenge))).scalar_one().state, "failed")

    async def test_expiry_blocks_all_learning_routes_but_keeps_account_access(self):
        async with self.sessions() as session:
            session.add(LearningMembership(user_id=2, plan="trial", starts_at=membership_service.now_utc() - timedelta(days=4), expires_at=membership_service.now_utc() - timedelta(days=1)))
            await session.commit()
        for path in ("/api/v1/english/home", "/api/english/home", "/api/media/999", "/api/stream/media/999", "/api/subtitle/999"):
            response = await self.client.get(path, headers=self.existing)
            self.assertEqual(response.status_code, 402, (path, response.text))
        me = await self.client.get("/api/v1/auth/me", headers=self.existing)
        self.assertEqual(me.status_code, 200)
        self.assertFalse(me.json()["membership"]["can_learn"])
        status = await self.client.get("/api/v1/membership/status", headers=self.existing)
        self.assertEqual(status.status_code, 200)
        admin_home = await self.client.get("/api/v1/english/home", headers=self.admin)
        self.assertEqual(admin_home.status_code, 200)

    async def test_manual_grants_calendar_months_prices_and_permanent(self):
        await self.configure()
        base = datetime(2026, 1, 31, 12, tzinfo=timezone.utc)
        with patch.object(membership_service, "now_utc", return_value=base):
            monthly = await self.client.post("/api/v1/membership/admin/users/2/grant", headers=self.admin, json={"plan": "monthly", "confirmed": True})
            self.assertEqual(monthly.status_code, 200, monthly.text)
            self.assertTrue(monthly.json()["expires_at"].startswith("2026-02-28"))
            quarterly = await self.client.post("/api/v1/membership/admin/users/2/grant", headers=self.admin, json={"plan": "quarterly", "confirmed": True})
            self.assertTrue(quarterly.json()["expires_at"].startswith("2026-05-28"))
            yearly = await self.client.post("/api/v1/membership/admin/users/2/grant", headers=self.admin, json={"plan": "yearly", "confirmed": True})
            self.assertTrue(yearly.json()["expires_at"].startswith("2027-05-28"))
            permanent = await self.client.post("/api/v1/membership/admin/users/2/grant", headers=self.admin, json={"plan": "lifetime", "confirmed": True})
            self.assertIsNone(permanent.json()["expires_at"])
            downgrade = await self.client.post("/api/v1/membership/admin/users/2/grant", headers=self.admin, json={"plan": "monthly", "confirmed": True})
            self.assertEqual(downgrade.status_code, 400)
        async with self.sessions() as session:
            grants = (await session.execute(select(MembershipGrant))).scalars().all()
            self.assertEqual(len(grants), 4)
            self.assertEqual([entry.amount_cents for entry in grants], [990] * 4)

    async def test_admin_only_configuration_and_legacy_access(self):
        legacy = await self.client.get("/api/v1/membership/status", headers=self.existing)
        self.assertEqual(legacy.json()["plan"], "legacy")
        self.assertTrue(legacy.json()["can_learn"])
        denied = await self.client.put("/api/v1/membership/admin/settings", headers=self.existing, json=self.config)
        self.assertEqual(denied.status_code, 403)
        denied_grant = await self.client.post("/api/v1/membership/admin/users/2/grant", headers=self.existing, json={"plan": "lifetime", "confirmed": True})
        self.assertEqual(denied_grant.status_code, 403)
        invalid = {**self.config, "contact_url": "javascript:alert(1)"}
        self.assertEqual((await self.client.put("/api/v1/membership/admin/settings", headers=self.admin, json=invalid)).status_code, 422)
        invalid_price = {**self.config, "plans": {key: {"enabled": True, "price": "9.999"} for key in self.config["plans"]}}
        self.assertEqual((await self.client.put("/api/v1/membership/admin/settings", headers=self.admin, json=invalid_price)).status_code, 422)
        unconfirmed = await self.client.post("/api/v1/membership/admin/users/2/grant", headers=self.admin, json={"plan": "monthly", "confirmed": False})
        self.assertEqual(unconfirmed.status_code, 422)

    async def test_official_sdk_sender_contract(self):
        from app.schemas.membership import AccessSettings
        from unittest.mock import MagicMock

        with patch("alibabacloud_dysmsapi20170525.client.Client") as client:
            client.return_value.send_sms_with_options.return_value = MagicMock(body=MagicMock(code="OK"))
            await sms_service.dispatch_sms(AccessSettings.model_validate(self.config), "+8613900000001", "123456")
            request = client.return_value.send_sms_with_options.call_args.args[0]
            self.assertEqual(request.phone_numbers, "13900000001")
            self.assertEqual(json.loads(request.template_param), {"code": "123456"})

    async def create_invite(self, valid_days=7):
        response = await self.client.post("/api/v1/membership/admin/invitations", headers=self.admin, json={"valid_days": valid_days})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def register_invite(self, code, username="invitedlearner"):
        return await self.client.post("/api/v1/auth/register", json={"username": username, "password": "valid-password", "invitation_code": code})

    async def test_invite_registration_needs_no_sms_and_code_is_one_use(self):
        invite = await self.create_invite()
        response = await self.register_invite(invite["code"])
        self.assertEqual(response.status_code, 201, response.text)
        self.assertFalse(response.json()["is_admin"])
        user_id = response.json()["id"]
        async with self.sessions() as session:
            membership = await session.get(LearningMembership, user_id)
            self.assertEqual(membership.plan, "trial")
            self.assertEqual((membership.expires_at - membership.starts_at).total_seconds(), 3 * 86400)
        repeat = await self.register_invite(invite["code"], "anotherlearner")
        self.assertEqual(repeat.status_code, 400)
        protected = await self.client.delete(f"/api/v1/membership/admin/invitations/{invite['id']}", headers=self.admin)
        self.assertEqual(protected.status_code, 400)
        async with self.sessions() as session:
            self.assertIsNotNone(await session.get(RegistrationInvite, invite["id"]))
        await self.client.delete(f"/api/v1/users/{user_id}", headers=self.admin)
        self.assertEqual((await self.register_invite(invite["code"], "afterdeletion")).status_code, 400)
        async with self.sessions() as session:
            record = await session.get(RegistrationInvite, invite["id"])
            self.assertIsNone(record)
        listed = await self.client.get("/api/v1/membership/admin/invitations", headers=self.admin)
        self.assertFalse(any(row["id"] == invite["id"] for row in listed.json()["items"]))
        self.assertNotIn(invite["code"], listed.text)

    async def test_invite_permissions_invalid_expired_and_deleted(self):
        denied = await self.client.post("/api/v1/membership/admin/invitations", headers=self.existing, json={"valid_days": 7})
        self.assertEqual(denied.status_code, 403)
        self.assertEqual((await self.client.get("/api/v1/membership/admin/invitations", headers=self.existing)).status_code, 403)
        self.assertEqual((await self.register_invite("EF-" + "0" * 32)).status_code, 400)
        expired = await self.create_invite()
        async with self.sessions() as session:
            record = await session.get(RegistrationInvite, expired["id"])
            record.expires_at = membership_service.now_utc() - timedelta(seconds=1)
            await session.commit()
        self.assertEqual((await self.register_invite(expired["code"])).status_code, 400)
        revoked = await self.create_invite()
        result = await self.client.delete(f"/api/v1/membership/admin/invitations/{revoked['id']}", headers=self.admin)
        self.assertEqual(result.status_code, 204)
        self.assertEqual((await self.register_invite(revoked["code"])).status_code, 400)
        expired_delete = await self.client.delete(f"/api/v1/membership/admin/invitations/{expired['id']}", headers=self.admin)
        self.assertEqual(expired_delete.status_code, 204)
        async with self.sessions() as session:
            self.assertIsNone(await session.get(RegistrationInvite, expired["id"]))
        bad_expiry = await self.client.post("/api/v1/membership/admin/invitations", headers=self.admin, json={"valid_days": 0})
        self.assertEqual(bad_expiry.status_code, 422)

    async def test_orphaned_used_invite_can_be_cleaned(self):
        invite = await self.create_invite()
        async with self.sessions() as session:
            record = await session.get(RegistrationInvite, invite["id"])
            record.used_at = membership_service.now_utc()
            record.used_by_username = "previously_deleted"
            await session.commit()
        listing = await self.client.get("/api/v1/membership/admin/invitations", headers=self.admin)
        row = next(item for item in listing.json()["items"] if item["id"] == invite["id"])
        self.assertEqual(row["status"], "orphaned")
        result = await self.client.delete(f"/api/v1/membership/admin/invitations/{invite['id']}", headers=self.admin)
        self.assertEqual(result.status_code, 204)

    async def test_failed_registration_does_not_consume_invite(self):
        invite = await self.create_invite()
        duplicate = await self.register_invite(invite["code"], "existing")
        self.assertEqual(duplicate.status_code, 409)
        async with self.sessions() as session:
            record = await session.get(RegistrationInvite, invite["id"])
            self.assertIsNone(record.used_at)
        good = await self.register_invite(" " + invite["code"].lower() + " ")
        self.assertEqual(good.status_code, 201, good.text)

    async def test_invitation_mode_off_and_missing_credentials(self):
        invite = await self.create_invite()
        await self.client.put("/api/v1/membership/admin/settings", headers=self.admin, json={"invite_enabled": False})
        self.assertEqual((await self.register_invite(invite["code"])).status_code, 403)
        missing = await self.client.post("/api/v1/auth/register", json={"username": "nocredential", "password": "valid-password"})
        self.assertEqual(missing.status_code, 422)
        mixed = await self.client.post("/api/v1/auth/register", json={"username": "mixedcredential", "password": "valid-password", "invitation_code": invite["code"], "phone": "13900000001", "verification_code": "123456"})
        self.assertEqual(mixed.status_code, 422)