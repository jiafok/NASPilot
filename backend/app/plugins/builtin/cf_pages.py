"""Cloudflare Pages Deploy Plugin — ports update_cloudflare.sh for Home Control Panel."""

import asyncio
import json
import logging
from typing import Any

from app.plugins.registry import PluginBase, PluginMeta

logger = logging.getLogger("naspilot.plugins.cf_pages")


class CloudflarePagesPlugin(PluginBase):
    META = PluginMeta(
        slug="cloudflare_pages",
        name="Cloudflare Pages Deploy",
        description="Generate home control panel HTML and deploy to Cloudflare Pages with Basic Auth. Port of update_cloudflare.sh.",
        version="1.0.0",
        category="network",
    )

    async def on_enable(self) -> None:
        pass

    async def on_disable(self) -> None:
        pass

    async def run(self, **kwargs: Any) -> Any:
        """Generate services HTML and deploy to Cloudflare Pages using wrangler."""
        import traceback

        try:
            return await self._run_impl(**kwargs)
        except Exception as exc:
            logger.exception("Cloudflare Pages run failed")
            return {"deployed": False, "errors": [str(exc)]}

    async def _run_impl(self, **kwargs: Any) -> Any:
        """Generate services HTML and deploy to Cloudflare Pages using wrangler."""
        result: dict[str, Any] = {"deployed": False, "errors": []}

        services = self.config.get("services", {})
        auth_user = self.config.get("auth_user", "")
        auth_pass = self.config.get("auth_pass", "")
        cf_token = self.config.get("cf_api_token", "")
        cf_account = self.config.get("cf_account_id", "")
        project = self.config.get("cf_project", "home-panel")

        if not cf_token or not cf_account:
            result["errors"].append("Missing cf_api_token or cf_account_id in config")
            return result

        # Generate simple services HTML
        html_lines = ["<!DOCTYPE html><html><head><meta charset='utf-8'><title>Home Panel</title>",
                      "<style>body{font-family:system-ui;max-width:800px;margin:2em auto;padding:0 1em}",
                      "a{display:block;padding:12px 16px;margin:4px 0;background:#f5f5f5;border-radius:8px;",
                      "text-decoration:none;color:#333;font-weight:500}a:hover{background:#e0e0e0}</style></head><body>",
                      "<h1>🏠 Home Control Panel</h1>"]
        for name, url in services.items():
            html_lines.append(f'<a href="{url}" target="_blank">{name}</a>')
        html_lines.extend(["</body></html>"])

        result["html"] = "".join(html_lines)
        result["services_count"] = len(services)

        # Write HTML and deploy with wrangler
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            html_path = os.path.join(tmp, "index.html")
            worker_path = os.path.join(tmp, "_worker.js")
            toml_path = os.path.join(tmp, "wrangler.toml")

            with open(html_path, "w", encoding="utf-8") as f:
                f.write(result["html"])

            # Basic Auth worker — reads credentials from env (set via wrangler / CF dashboard)
            worker_js = """
export default {
  async fetch(request, env) {
    const auth = request.headers.get('Authorization');
    const expected = 'Basic ' + btoa(env.AUTH_USER + ':' + env.AUTH_PASS);
    if (!auth || auth !== expected) {
      return new Response('Unauthorized', { status: 401, headers: { 'WWW-Authenticate': 'Basic realm="Home Panel"' } });
    }
    return env.ASSETS.fetch(request);
  }
};
"""
            with open(worker_path, "w", encoding="utf-8") as f:
                f.write(worker_js)

            # Wrangler config with env var placeholders
            toml_content = f"""name = "{project}"
compatibility_date = "2026-01-01"

[vars]
AUTH_USER = "{auth_user}"
AUTH_PASS = "{auth_pass}"
"""
            with open(toml_path, "w", encoding="utf-8") as f:
                f.write(toml_content)

            env = {**os.environ, "CLOUDFLARE_API_TOKEN": cf_token, "CLOUDFLARE_ACCOUNT_ID": cf_account}
            try:
                proc = await asyncio.create_subprocess_exec(
                    "npx", "wrangler", "pages", "deploy", tmp, "--project-name", project,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    env=env, cwd=tmp,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
                result["stdout"] = stdout.decode()[:2000]
                result["stderr"] = stderr.decode()[:2000] if stderr else None
                result["deployed"] = proc.returncode == 0
                result["exit_code"] = proc.returncode
            except asyncio.TimeoutError:
                result["errors"].append("wrangler deploy timed out after 60s")
            except Exception as e:
                result["errors"].append(str(e))

        return result
