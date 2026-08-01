# DEAD CODE AUDIT

Project freeze status: report only, no code removal.

## Confirmed Orphaned Surface

### Legacy Cloudflare DDNS page

- [frontend/src/pages/CloudflareDDNS.tsx](frontend/src/pages/CloudflareDDNS.tsx#L13) is not routed by the app and is not imported by the current route map.
- The active route uses [frontend/src/pages/CloudflareDDNSPage.tsx](frontend/src/pages/CloudflareDDNSPage.tsx#L13) from [frontend/src/App.tsx](frontend/src/App.tsx#L13).
- This is the clearest dead-code candidate in the repo: it is a duplicate screen with the same feature intent, but only one copy is reachable.

## Low-Value Redundancy

- [backend/app/services/task_service.py](backend/app/services/task_service.py#L32) assigns env_vars but the local variable is redundant because the execution path later uses the merged environment directly. This is small, but it is dead local state.

## Stale Or Non-Product Weight

- [frontend/README.md](frontend/README.md) is still the stock Vite template rather than a NASPilot-specific frontend guide. It is not dead code, but it is stale project weight.
- Some plugin and page pairs are intentionally duplicated by naming style, so they are not dead code unless they are unreferenced by routing or imports.

## Not Classified As Dead

- Builtin backend plugins such as btrfs_cleanup and rclone_mount are still registered by the plugin registry and surfaced in the plugin list, so they remain active even if they do not have dedicated standalone pages.
- The backend SPA fallback is deliberate and remains part of the deployment model.

## Dead-Code Verdict

The repo has one clear orphaned frontend screen and one small redundant local variable. Everything else reviewed in this pass is either live, intentionally duplicated for compatibility, or deployment support code.