"""Cloudflare Pages deploy plugin based on update_cloudflare.sh behavior."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import html
import ipaddress
import json
import logging
import os
import re
import socket
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from app.plugins.registry import PluginBase, PluginMeta

logger = logging.getLogger("naspilot.plugin.cloudflare_pages")

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



async def _detect_ipv6(iface: str) -> str:
    """Detect a public IPv6 address.

    Resolution order:
    1. Read /proc/net/if_inet6 (only works in host network mode, or bridge
       with IPv6 enabled).
    2. Hostname resolution via getaddrinfo.
    3. Public probe (ipv6.icanhazip.com / api6.ipify.org) — this returns the
       IPv6 source address of the container's egress, which works regardless
       of host vs bridge networking.
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

    async def _probe_public_ipv6() -> str:
        for url in ["https://ipv6.icanhazip.com", "https://api6.ipify.org"]:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    resp = await client.get(url)
                    ip = resp.text.strip()
                    if ip:
                        parsed = ipaddress.IPv6Address(ip)
                        if parsed.is_global:
                            return ip
            except Exception:
                continue
        return ""

    candidates = await asyncio.to_thread(_read_ipv6_addresses)
    if candidates:
        return candidates[0]
    # Bridge networks don't expose the host's global IPv6 in /proc/net/if_inet6,
    # so fall back to a public egress probe to match what DDNS would report.
    return await _probe_public_ipv6()


def _asset_hash(data: bytes) -> str:
    """Stable content hash used as the manifest key.

    Cloudflare treats this as a client-side deduplication key only: the server
    stores files by the hash we choose, and `check-missing` tells us which
    hashes it has not seen. Wrangler happens to use xxhash64, but nothing
    requires that — any deterministic 32-char hex digest works, and using one
    we can compute here removes the Node dependency entirely.
    """
    return hashlib.md5(data).hexdigest()


#: Pages caps a single deployment at 20,000 files / 25 MiB per file.
MAX_ASSETS = 20000
MAX_ASSET_BYTES = 25 * 1024 * 1024

#: Cloudflare rejects oversized asset-upload batches; wrangler uses 40 MiB.
MAX_UPLOAD_BATCH_BYTES = 40 * 1024 * 1024


def _collect_files(outdir: str) -> list[tuple[str, bytes]]:
    """Read every deployable file, keyed by its `/`-prefixed path."""
    files: list[tuple[str, bytes]] = []
    base = Path(outdir)
    for path in sorted(base.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        rel = path.relative_to(base).as_posix()
        data = path.read_bytes()
        if len(data) > MAX_ASSET_BYTES:
            raise RuntimeError(
                f"{rel} 超过 Pages 单文件上限 25 MiB（{len(data)} 字节）"
            )
        files.append((f"/{rel}", data))
    if not files:
        raise RuntimeError("部署目录为空")
    if len(files) > MAX_ASSETS:
        raise RuntimeError(f"文件数 {len(files)} 超过 Pages 上限 {MAX_ASSETS}")
    return files


def _content_type(name: str) -> str:
    import mimetypes

    guessed, _ = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


class _PagesApi:
    """Minimal Cloudflare Pages client for Direct Upload deployments."""

    BASE = "https://api.cloudflare.com/client/v4"

    def __init__(self, token: str, account_id: str, project: str) -> None:
        self._token = token
        self._account = account_id
        self._project = project

    @property
    def _auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    def _project_url(self, suffix: str = "") -> str:
        return f"{self.BASE}/accounts/{self._account}/pages/projects/{self._project}{suffix}"

    @staticmethod
    def _raise_for_errors(payload: dict, context: str) -> dict:
        if payload.get("success") is False:
            errors = payload.get("errors") or []
            detail = "; ".join(
                str(e.get("message") or e) if isinstance(e, dict) else str(e)
                for e in errors
            ) or "unknown error"
            raise RuntimeError(f"{context} 失败: {detail}")
        return payload.get("result") or {}

    async def ensure_project(self) -> dict:
        """Create the Pages project when it does not exist yet."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(self._project_url(), headers=self._auth)
            if resp.status_code == 200:
                return resp.json().get("result") or {}
            if resp.status_code != 404:
                raise RuntimeError(
                    f"查询 Pages 项目失败 HTTP {resp.status_code}: {resp.text[:300]}"
                )
            create = await client.post(
                f"{self.BASE}/accounts/{self._account}/pages/projects",
                headers={**self._auth, "Content-Type": "application/json"},
                json={"name": self._project, "production_branch": "main"},
            )
            if create.status_code == 409:  # created concurrently — fine
                return {}
            if create.status_code >= 400:
                raise RuntimeError(
                    f"创建 Pages 项目失败 HTTP {create.status_code}: {create.text[:300]}"
                )
            return create.json().get("result") or {}

    async def upload_token(self) -> str:
        """Short-lived JWT used to authorise asset transfers."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(
                self._project_url("/upload-token"), headers=self._auth
            )
            payload = resp.json() if resp.content else {}
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"获取上传令牌失败 HTTP {resp.status_code}: {resp.text[:300]}"
                )
            result = self._raise_for_errors(payload, "获取上传令牌")
            token = str(result.get("jwt") or "")
            if not token:
                raise RuntimeError("上传令牌为空")
            return token

    async def check_missing(self, jwt: str, hashes: list[str]) -> set[str]:
        """Ask which hashes still need uploading."""
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{self.BASE}/pages/assets/check-missing",
                headers={"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"},
                json={"hashes": hashes},
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"check-missing 失败 HTTP {resp.status_code}: {resp.text[:300]}"
                )
            result = self._raise_for_errors(resp.json(), "check-missing")
            return set(result if isinstance(result, list) else [])

    async def upload_assets(
        self, jwt: str, items: list[tuple[str, str, bytes]]
    ) -> None:
        """Upload `(hash, content_type, data)` triples in size-bounded batches.

        Cloudflare rejects batches above ~40 MiB, so a single large HTML page
        plus its siblings must be split rather than sent in one request.
        """
        batches: list[list[tuple[str, str, bytes]]] = []
        current: list[tuple[str, str, bytes]] = []
        current_bytes = 0
        for item in items:
            cost = len(item[2]) * 4 // 3 + 256  # base64 inflation + JSON overhead
            if current and current_bytes + cost > MAX_UPLOAD_BATCH_BYTES:
                batches.append(current)
                current, current_bytes = [], 0
            current.append(item)
            current_bytes += cost
        if current:
            batches.append(current)

        headers = {"Authorization": f"Bearer {jwt}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=300) as client:
            for batch in batches:
                payload = [
                    {
                        "key": digest,
                        "value": base64.b64encode(data).decode("ascii"),
                        "metadata": {"contentType": content_type},
                        "base64": True,
                    }
                    for digest, content_type, data in batch
                ]
                resp = await client.post(
                    f"{self.BASE}/pages/assets/upload", headers=headers, json=payload
                )
                if resp.status_code >= 400:
                    raise RuntimeError(
                        f"上传资源失败 HTTP {resp.status_code}: {resp.text[:300]}"
                    )
                self._raise_for_errors(resp.json(), "上传资源")

    async def upsert_hashes(self, jwt: str, hashes: list[str]) -> None:
        """Best-effort: remember these hashes so the next deploy skips them.

        A failure here only costs upload bandwidth next time, never correctness.
        """
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                await client.post(
                    f"{self.BASE}/pages/assets/upsert-hashes",
                    headers={
                        "Authorization": f"Bearer {jwt}",
                        "Content-Type": "application/json",
                    },
                    json={"hashes": hashes},
                )
        except Exception:  # noqa: BLE001 - purely an optimisation
            pass

    async def create_deployment(
        self, manifest: dict[str, str], commit_message: str, branch: str,
        deployment_files: dict[str, bytes] | None = None,
    ) -> dict:
        """Create the deployment from the uploaded manifest."""
        data = {
            "manifest": json.dumps(manifest),
            "branch": branch,
            "commit_message": commit_message,
        }
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                self._project_url("/deployments"),
                headers=self._auth,
                data=data,
                files={name: (name, content) for name, content in (deployment_files or {}).items()},
            )
            if resp.status_code >= 400:
                raise RuntimeError(
                    f"创建部署失败 HTTP {resp.status_code}: {resp.text[:500]}"
                )
            return self._raise_for_errors(resp.json(), "创建部署")


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
                    "export default {\n"
                    "  async fetch(request, env) {\n"
                    f"    const USER = {json.dumps(basic_auth_user)};\n"
                    f"    const PASS = {json.dumps(basic_auth_pass)};\n"
                    "    const auth = request.headers.get('Authorization') || '';\n"
                    "    if (!auth.startsWith('Basic ')) {\n"
                    "      return new Response('Authentication required', { status: 401, headers: { 'WWW-Authenticate': 'Basic realm=\\\"Protected\\\"' } });\n"
                    "    }\n"
                    "    try {\n"
                    "      const decoded = atob(auth.replace('Basic ', ''));\n"
                    "      const [u, p] = decoded.split(':');\n"
                    "      if (u === USER && p === PASS) return env.ASSETS.fetch(request);\n"
                    "    } catch (_) {}\n"
                    "    return new Response('Unauthorized', { status: 401, headers: { 'WWW-Authenticate': 'Basic realm=\\\"Protected\\\"' } });\n"
                    "  }\n"
                    "}\n"
                )
                with open(os.path.join(outdir_path, "_worker.js"), "w", encoding="utf-8") as f:
                    f.write(worker)

        api = _PagesApi(token, account, project)
        started = time.time()

        with tempfile.TemporaryDirectory() as outdir:
            await asyncio.to_thread(_write_deployment_files, outdir)

            # 1) Project must exist before we can ask for an upload token.
            await api.ensure_project()

            # 2) Hash everything and find out what the edge already has.
            files = await asyncio.to_thread(_collect_files, outdir)
            digests: dict[str, tuple[str, bytes]] = {}
            manifest: dict[str, str] = {}
            deployment_files: dict[str, bytes] = {}
            for name, data in files:
                if name in {"/_headers", "/_redirects", "/_routes.json", "/_worker.js"}:
                    deployment_files[name[1:]] = data
                    continue
                digest = _asset_hash(data)
                manifest[name] = digest
                # Same content under two names shares one asset — keep the
                # first content type so the upload stays a single request.
                digests.setdefault(digest, (_content_type(name), data))

            jwt = await api.upload_token()
            missing = await api.check_missing(jwt, list(digests))
            to_upload = [
                (digest, ctype, data)
                for digest, (ctype, data) in digests.items()
                if digest in missing
            ]

            # 3) Push only the missing assets, then confirm the manifest.
            if to_upload:
                await api.upload_assets(jwt, to_upload)
            await api.upsert_hashes(jwt, list(digests))

            # 4) Create the deployment itself.
            deployed = await api.create_deployment(
                manifest,
                commit_message=f"NASPilot 面板 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                branch="main",
                deployment_files=deployment_files,
            )

        deploy_url = str(deployed.get("url") or "")
        if not deploy_url:
            match = PAGES_URL_RE.findall(json.dumps(deployed))
            deploy_url = match[0] if match else ""

        elapsed = time.time() - started
        state["last_run"] = datetime.utcnow().isoformat()
        state["last_ipv6"] = current_ipv6
        state["last_deploy_url"] = deploy_url
        state["last_deployment_id"] = str(deployed.get("id") or "")

        logger.info(
            "Cloudflare Pages 部署完成: project=%s files=%d uploaded=%d url=%s",
            project, len(files), len(to_upload), deploy_url or "-",
        )

        return {
            "status": "ok",
            "deployed": True,
            "ipv6": current_ipv6,
            "previous_ipv6": previous_ipv6,
            "project_name": project,
            "services_count": len([x for x in services if x.get("enabled") is not False]),
            "deploy_url": deploy_url,
            "deployment_id": str(deployed.get("id") or ""),
            "files_total": len(files),
            "files_uploaded": len(to_upload),
            "files_reused": len(digests) - len(to_upload),
            "elapsed_seconds": round(elapsed, 1),
            "manager": "direct-upload",
        }
