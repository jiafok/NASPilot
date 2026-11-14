import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.plugins.builtin import cf_pages
from app.plugins.builtin.rclone_mount import RcloneMountPlugin


class RcloneMountTests(unittest.IsolatedAsyncioTestCase):
    async def test_mount_creates_directory_and_checks_writability(self):
        with tempfile.TemporaryDirectory() as directory:
            mount = Path(directory) / "mount"
            plugin = RcloneMountPlugin({"mount_point": str(mount)})
            plugin._preflight = MagicMock(return_value=[])
            plugin._is_mounted = AsyncMock(return_value=False)
            plugin._write_config = AsyncMock(side_effect=RuntimeError("config reached"))

            result = await plugin.run(action="mount")

            self.assertEqual(result["error"], "config reached")
            self.assertTrue(mount.is_dir())
            plugin._write_config.assert_awaited_once()

    def test_mount_point_cannot_be_a_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "file"
            path.write_text("keep", encoding="utf-8")
            writable, error = RcloneMountPlugin._dir_writable(path)
            self.assertFalse(writable)
            self.assertTrue(error)
            self.assertEqual(path.read_text(encoding="utf-8"), "keep")


class CloudflarePagesTests(unittest.IsolatedAsyncioTestCase):
    async def test_deploy_sends_worker_as_deployment_file(self):
        seen = []

        def respond(request):
            seen.append((request.method, request.url.path))
            if request.url.path.endswith("/upload-token"):
                self.assertEqual(request.method, "GET")
                result = {"jwt": "upload-token"}
            elif request.url.path.endswith("/check-missing"):
                result = json.loads(request.content)["hashes"]
            elif request.url.path.endswith("/deployments"):
                self.assertIn("multipart/form-data", request.headers["content-type"])
                self.assertIn(b'name="_worker.js"', request.content)
                self.assertIn(b'const PASS = "secret"', request.content)
                self.assertIn(b'name="_headers"', request.content)
                self.assertNotIn(b'"/_worker.js"', request.content)
                result = {"id": "123", "url": "https://test.pages.dev"}
            else:
                result = {}
            return httpx.Response(200, json={"success": True, "result": result})

        client_class = httpx.AsyncClient

        def make_client(*args, **kwargs):
            return client_class(*args, transport=httpx.MockTransport(respond), **kwargs)

        config = {
            "cloudflare_api_token": "token",
            "cloudflare_account_id": "account",
            "project_name": "test",
            "services_json": [{"name": "app", "url": "https://example.org"}],
            "basic_auth_enabled": True,
            "basic_auth_user": "user",
            "basic_auth_pass": "secret",
        }
        with (
            patch.object(cf_pages.httpx, "AsyncClient", side_effect=make_client),
            patch.object(cf_pages, "_detect_ipv6", return_value="2001:4860:4860::8888"),
        ):
            result = await cf_pages.CloudflarePagesPlugin(config).run()

        self.assertEqual(result["status"], "ok")
        self.assertIn(("GET", "/client/v4/accounts/account/pages/projects/test/upload-token"), seen)