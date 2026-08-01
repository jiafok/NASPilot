"""Cloudflare Pages deploy plugin based on update_cloudflare.sh behavior."""

from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import re
import ipaddress
import socket
import tempfile
from datetime import datetime
from typing import Any

from app.plugins.registry import PluginBase, PluginMeta

logger = logging.getLogger("naspilot.plugin.cloudflare_pages")

WRANGLER_VERSION = "3.78.12"
PAGES_URL_RE = re.compile(r"https://[a-zA-Z0-9._-]+\.pages\.dev")


def _first_str(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _normalize_services(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        try:
            parsed = json.loads(value)
            if isinstance(parsed, list):
                return [x for x in parsed if isinstance(x, dict)]
        except Exception:
            return []
    if isinstance(value, dict):
        # Compatibility mode: {"name": "url"}
        return [
            {"group": "Services", "name": str(k), "url": str(v), "enabled": True}
            for k, v in value.items()
        ]
    return []


def _make_service_url(item: dict[str, Any], ipv6: str) -> str:
    if isinstance(item.get("url"), str) and item.get("url"):
        return str(item["url"])
    ssl = bool(item.get("ssl", False))
    proto = "https" if ssl else "http"
    port = int(item.get("port", 80))
    path = str(item.get("path") or "")
    if path and not path.startswith("/"):
        path = f"/{path}"
    return f"{proto}://[{ipv6}]:{port}{path}"


def _service_groups(services: list[dict[str, Any]]) -> list[tuple[str, list[dict[str, Any]]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in services:
        if item.get("enabled") is False:
            continue
        group = str(item.get("group") or "Other")
        groups.setdefault(group, []).append(item)
    return list(groups.items())


def _build_html(services: list[dict[str, Any]], ipv6: str) -> str:
    now_cn = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts = [
        "<!doctype html>",
        "<html lang='zh-CN'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>",
        "<title>家庭 NAS 控制面板</title>",
        "<style>",
        ":root{--bg:#0b1018;--panel:#0f1520;--accent:#60a5fa;--border:#263041;--text:#e6edf3;--sub:#8b949e;--radius:14px}",
        "*{box-sizing:border-box} body{margin:0;background:linear-gradient(180deg,#0b1018 0%,#111827 100%);color:var(--text);font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial}",
        ".wrap{max-width:1100px;margin:0 auto;padding:36px 20px} h1{margin:0 0 8px;font-size:34px}",
        ".meta{display:flex;gap:10px;flex-wrap:wrap;color:var(--sub);font-size:13px;margin-bottom:20px}",
        ".tag{border:1px solid #115e59;background:#064e3b22;border-radius:999px;padding:2px 8px;color:#a7f3d0}",
        ".group{margin:26px 0 10px;font-size:16px;font-weight:600;color:#cbd5e1}",
        ".grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px}",
        ".card{display:block;text-decoration:none;color:inherit;background:rgba(255,255,255,.04);border:1px solid var(--border);border-radius:var(--radius);padding:14px;box-shadow:0 10px 30px rgba(0,0,0,.28);transition:all .15s}",
        ".card:hover{border-color:var(--accent);transform:translateY(-3px)} .card-title{font-size:16px;margin-bottom:6px}",
        ".card-url{font-size:12px;color:var(--sub);word-break:break-all}",
        "</style></head><body><div class='wrap'>",
        "<h1>家庭 NAS 控制面板</h1>",
        f"<div class='meta'><span class='tag'>IPv6: [{html.escape(ipv6)}]</span><span class='tag'>更新时间: {html.escape(now_cn)}</span></div>",
    ]
    for group, items in _service_groups(services):
        parts.append(f"<div class='group'>{html.escape(group)}</div><div class='grid'>")
        for item in items:
            name = html.escape(str(item.get("name") or "Unnamed"))
            url = _make_service_url(item, ipv6)
            safe_url = html.escape(url, quote=True)
            parts.append(
                f"<a class='card' href='{safe_url}' target='_blank' rel='noopener'>"
                f"<div class='card-title'>{name}</div><div class='card-url'>{safe_url}</div></a>"
            )
        parts.append("</div>")
    parts.append("</div></body></html>")
    return "".join(parts)


async def _run_cmd(
    cmd: list[str],
    timeout_s: int,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", f"Command timed out after {timeout_s}s"
    return proc.returncode, stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace")


async def _detect_ipv6(iface: str) -> str:
    """Detect a public IPv6 address without requiring the ``ip`` utility.

    Minimal container images often do not include iproute2. Linux exposes
    interface IPv6 addresses through /proc/net/if_inet6, so use that first and
    fall back to hostname resolution for non-Linux environments.
    """

    def _read_ipv6_addresses() -> list[str]:
        candidates: list[str] = []
        try:
            with open("/proc/net/if_inet6", encoding="ascii") as f:
                for line in f:
                    fields = line.split()
                    if len(fields) < 6:
                        continue
                    address_hex, scope, interface = fields[0], fields[3], fields[5]
                    # Scope 00 is global; scope 20 is link-local.
                    if iface and interface != iface:
                        continue
                    if scope != "00":
                        continue
                    # /proc/net/if_inet6 stores addresses as 32 hex chars
                    # without colons — reformat to IPv6 colon notation
                    raw = address_hex.strip()
                    if len(raw) >= 32:
                        parts = [raw[i:i+4] for i in range(0, 32, 4)]
                        normalized = ":".join(parts)
                    else:
                        normalized = raw
                    address = str(ipaddress.IPv6Address(normalized))
                    if ipaddress.IPv6Address(address).is_global:
                        candidates.append(address)
        except (FileNotFoundError, OSError, ValueError):
            pass

        if candidates:
            return candidates

        try:
            hostname = socket.gethostname()
            for item in socket.getaddrinfo(hostname, None, socket.AF_INET6):
                address = item[4][0].split("%", 1)[0]
                parsed = ipaddress.IPv6Address(address)
                if parsed.is_global and address not in candidates:
                    candidates.append(address)
        except (OSError, ValueError):
            pass
        return candidates

    candidates = await asyncio.to_thread(_read_ipv6_addresses)
    return candidates[0] if candidates else ""


async def _pick_wrangler(timeout_s: int) -> list[str]:
    checks = [
        ["wrangler", "--version"],
        ["npx", "-y", f"wrangler@{WRANGLER_VERSION}", "--version"],
        ["npx", "wrangler", "--version"],
    ]
    for cmd in checks:
        code, _, _ = await _run_cmd(cmd, timeout_s=timeout_s)
        if code == 0:
            return cmd[:-1]
    return []


class CloudflarePagesPlugin(PluginBase):
    META = PluginMeta(
        slug="cloudflare_pages",
        name="Cloudflare Pages Deploy",
        description="Generate home control panel and deploy to Cloudflare Pages. Port of update_cloudflare.sh.",
        version="1.1.0",
        category="network",
        entrypoint="app.plugins.builtin.cf_pages",
    )

    @property
    def default_config(self) -> dict[str, Any]:
        return {
            "cloudflare_api_token": "",
            "cloudflare_account_id": "",
            "project_name": "nas",
            "iface": "",
            "basic_auth_enabled": True,
            "basic_auth_user": "",
            "basic_auth_pass": "",
            "auth_mode": "worker",
            "timeout_check": 120,
            "timeout_deploy": 600,
            "services_json": "[]",
            "state": {},
        }

    def get_config_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "cloudflare_api_token": {"type": "string", "title": "Cloudflare API Token"},
                "cloudflare_account_id": {"type": "string", "title": "Cloudflare Account ID"},
                "project_name": {"type": "string", "title": "CF Pages Project"},
                "iface": {"type": "string", "title": "IPv6 Interface (optional)"},
                "basic_auth_enabled": {"type": "boolean", "title": "Enable Basic Auth"},
                "basic_auth_user": {"type": "string", "title": "Basic Auth User"},
                "basic_auth_pass": {"type": "string", "title": "Basic Auth Password"},
                "services_json": {"type": "string", "title": "Services JSON"},
            },
        }

    async def on_enable(self) -> None:
        logger.info("Cloudflare Pages plugin enabled")

    async def on_disable(self) -> None:
        logger.info("Cloudflare Pages plugin disabled")

    async def run(self, **kwargs: Any) -> dict[str, Any]:
        try:
            return await self._run_impl()
        except Exception as exc:
            logger.exception("Cloudflare Pages run failed")
            return {"status": "error", "deployed": False, "error": str(exc)}

    async def _run_impl(self) -> dict[str, Any]:
        token = _first_str(self.config.get("cloudflare_api_token"), self.config.get("cf_api_token"))
        account = _first_str(self.config.get("cloudflare_account_id"), self.config.get("cf_account_id"))
        project = _first_str(self.config.get("project_name"), self.config.get("cf_project"), "nas")
        iface = _first_str(self.config.get("iface"))
        timeout_check = int(self.config.get("timeout_check", 120) or 120)
        timeout_deploy = int(self.config.get("timeout_deploy", 600) or 600)
        basic_auth_enabled = bool(self.config.get("basic_auth_enabled", True))
        basic_auth_user = _first_str(self.config.get("basic_auth_user"), self.config.get("auth_user"))
        basic_auth_pass = _first_str(self.config.get("basic_auth_pass"), self.config.get("auth_pass"))
        auth_mode = _first_str(self.config.get("auth_mode"), "worker").lower()

        if not token or not account:
            return {"status": "failed", "deployed": False, "error": "Missing cloudflare_api_token or cloudflare_account_id"}

        services = _normalize_services(self.config.get("services_json", self.config.get("services")))
        if not services:
            return {"status": "failed", "deployed": False, "error": "No services configured"}

        current_ipv6 = await _detect_ipv6(iface)
        if not current_ipv6:
            return {"status": "failed", "deployed": False, "error": "No global IPv6 detected"}

        state = self.config.setdefault("state", {})
        previous_ipv6 = str(state.get("last_ipv6") or "")
        if previous_ipv6 and previous_ipv6 == current_ipv6:
            return {
                "status": "skipped",
                "deployed": False,
                "reason": "ipv6_unchanged",
                "ipv6": current_ipv6,
                "services_count": len([x for x in services if x.get("enabled") is not False]),
            }

        wrangler_cmd = await _pick_wrangler(timeout_s=timeout_check)
        if not wrangler_cmd:
            return {"status": "failed", "deployed": False, "error": "Unable to resolve wrangler command"}

        html_doc = _build_html(services, current_ipv6)
        
        def _write_deployment_files(outdir_path: str) -> None:
            """Write all deployment files to directory (synchronous)."""
            with open(os.path.join(outdir_path, "index.html"), "w", encoding="utf-8") as f:
                f.write(html_doc)
            with open(os.path.join(outdir_path, "404.html"), "w", encoding="utf-8") as f:
                f.write(
                    "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                    "<title>404</title><body><h1>404</h1><a href='/'>Back</a></body>"
                )
            with open(os.path.join(outdir_path, "_headers"), "w", encoding="utf-8") as f:
                f.write(
                    "/*\n"
                    "  X-Frame-Options: DENY\n"
                    "  X-Content-Type-Options: nosniff\n"
                    "  Referrer-Policy: strict-origin-when-cross-origin\n"
                    "  Cache-Control: no-store\n"
                )
            with open(os.path.join(outdir_path, "_redirects"), "w", encoding="utf-8") as f:
                f.write("/*    /index.html   200\n")
            with open(os.path.join(outdir_path, "_routes.json"), "w", encoding="utf-8") as f:
                f.write('{"version": 1, "include": ["/*"], "exclude": []}')

            if basic_auth_enabled and auth_mode == "worker":
                worker = (
                    "export default {\\n"
                    "  async fetch(request, env) {\\n"
                    f"    const USER = {json.dumps(basic_auth_user)};\\n"
                    f"    const PASS = {json.dumps(basic_auth_pass)};\\n"
                    "    const auth = request.headers.get('Authorization') || '';\\n"
                    "    if (!auth.startsWith('Basic ')) {\\n"
                    "      return new Response('Authentication required', { status: 401, headers: { 'WWW-Authenticate': 'Basic realm=\\\"Protected\\\"' } });\\n"
                    "    }\\n"
                    "    try {\\n"
                    "      const decoded = atob(auth.replace('Basic ', ''));\\n"
                    "      const [u, p] = decoded.split(':');\\n"
                    "      if (u === USER && p === PASS) return env.ASSETS.fetch(request);\\n"
                    "    } catch (_) {}\\n"
                    "    return new Response('Unauthorized', { status: 401, headers: { 'WWW-Authenticate': 'Basic realm=\\\"Protected\\\"' } });\\n"
                    "  }\\n"
                    "}\\n"
                )
                with open(os.path.join(outdir_path, "_worker.js"), "w", encoding="utf-8") as f:
                    f.write(worker)
        
        with tempfile.TemporaryDirectory() as outdir:
            await asyncio.to_thread(_write_deployment_files, outdir)

            env = {
                **os.environ,
                "CI": "1",
                "NO_COLOR": "1",
                "WRANGLER_TELEMETRY_DISABLE": "1",
                "WRANGLER_SEND_METRICS": "0",
                "CLOUDFLARE_API_TOKEN": token,
                "CLOUDFLARE_ACCOUNT_ID": account,
            }
            deploy_cmd = [
                *wrangler_cmd,
                "pages",
                "deploy",
                outdir,
                f"--project-name={project}",
            ]
            code, stdout, stderr = await _run_cmd(deploy_cmd, timeout_s=timeout_deploy, env=env, cwd=outdir)
            deploy_url_match = PAGES_URL_RE.findall(stdout + "\n" + stderr)
            deploy_url = deploy_url_match[0] if deploy_url_match else ""

            state["last_run"] = datetime.utcnow().isoformat()
            state["last_ipv6"] = current_ipv6
            state["last_deploy_url"] = deploy_url
            state["last_exit_code"] = code

            result = {
                "status": "ok" if code == 0 else "error",
                "deployed": code == 0,
                "exit_code": code,
                "ipv6": current_ipv6,
                "previous_ipv6": previous_ipv6,
                "project_name": project,
                "services_count": len([x for x in services if x.get("enabled") is not False]),
                "deploy_url": deploy_url,
                "wrangler_cmd": " ".join(wrangler_cmd),
                "stdout": stdout[-4000:],
                "stderr": stderr[-4000:],
            }
            if code != 0:
                result["error"] = "Wrangler deploy failed"
            return result
