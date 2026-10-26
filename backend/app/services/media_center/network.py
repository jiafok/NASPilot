"""Per-subscription network policy resolution.

Modes
-----
``system``      inherit the container environment (``HTTPS_PROXY`` …) or the
                global default; equivalent to "just work"
``direct``      force a direct connection (``--proxy ""``), overriding any env
``http``        use ``proxy_url`` (``http://host:port``)
``socks5``      use ``proxy_url`` (``socks5://host:port``)
``docker_vpn``  traffic is already routed by a VPN sidecar container
                (``network_mode: container:gluetun``); we do not set a proxy
"""

from __future__ import annotations

import os
from dataclasses import dataclass

NETWORK_MODES = ("system", "direct", "http", "socks5", "docker_vpn")

_ENV_VARS = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy")


@dataclass(slots=True)
class NetworkPolicy:
    mode: str
    proxy: str | None = None       # resolved proxy URL (None → direct / no flag)
    force_direct: bool = False     # must be passed as ``--proxy ""``
    vpn_container: str | None = None
    warning: str | None = None

    def ytdlp_args(self) -> list[str]:
        """Extra yt-dlp arguments implementing this policy."""
        if self.force_direct:
            # Explicitly disable any inherited proxy.
            return ["--proxy", ""]
        if self.proxy:
            return ["--proxy", self.proxy]
        return []


def _env_proxy() -> str | None:
    for name in _ENV_VARS:
        value = (os.getenv(name) or "").strip()
        if value:
            return value
    return None


def _with_credentials(url: str, username: str | None, password: str | None) -> str:
    """Inject ``user:pass@`` into a proxy URL when credentials are configured."""
    if not username:
        return url
    from urllib.parse import quote, urlsplit, urlunsplit

    parts = urlsplit(url)
    if parts.username:  # already embedded
        return url
    auth = quote(username, safe="")
    if password:
        auth += ":" + quote(password, safe="")
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, f"{auth}@{host}", parts.path, parts.query, parts.fragment))


def resolve_policy(subscription) -> NetworkPolicy:
    """Build the effective network policy for one subscription."""
    from app.core.config import settings

    mode = (getattr(subscription, "network_mode", None) or "system").lower()
    if mode not in NETWORK_MODES:
        mode = "system"

    proxy_url = (getattr(subscription, "proxy_url", None) or "").strip() or None
    user = (getattr(subscription, "proxy_username", None) or "").strip() or None
    pwd = getattr(subscription, "proxy_password", None) or None
    vpn = (getattr(subscription, "vpn_container", None) or "").strip() or None

    if mode == "direct":
        return NetworkPolicy(mode=mode, force_direct=True)

    if mode in ("http", "socks5"):
        if not proxy_url:
            return NetworkPolicy(
                mode=mode,
                warning="未填写 proxy_url，已回退为直连",
                force_direct=True,
            )
        return NetworkPolicy(mode=mode, proxy=_with_credentials(proxy_url, user, pwd))

    if mode == "docker_vpn":
        return NetworkPolicy(
            mode=mode,
            vpn_container=vpn,
            warning=None if vpn else "未填写 VPN 容器名；请确保容器已通过 network_mode 接入",
        )

    # system → global setting, then environment
    global_proxy = (settings.MEDIA_PROXY_URL or settings.YOUTUBE_PROXY or "").strip() or None
    proxy = global_proxy or _env_proxy()
    return NetworkPolicy(mode="system", proxy=proxy)


def resolve_proxy(subscription) -> str | None:
    """Convenience wrapper used by :func:`base.resolve_options`."""
    policy = resolve_policy(subscription)
    return policy.proxy
