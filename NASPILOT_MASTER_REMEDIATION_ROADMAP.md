# NASPILOT MASTER REMEDIATION ROADMAP

Project freeze status: remediation only, no new features.

## Priority 0: Stabilize Core Behavior

| Priority | Item | Why it matters |
|---|---|---|
| P0 | Keep the frontend build path aligned with the backend SPA mount path | The runtime image depends on the built SPA being copied into [backend/frontend/dist](Dockerfile#L60), so release packaging must stay exact. |
| P0 | Preserve plugin registration and startup ordering | The lifecycle in [backend/app/lifespan.py](backend/app/lifespan.py#L30) is the main boot contract for the whole product. |
| P0 | Validate the task execution contract after every backend change | Task execution is the product's core automation primitive. |

## Priority 1: Remove Confusion And Duplication

| Priority | Item | Why it matters |
|---|---|---|
| P1 | Retire or merge [frontend/src/pages/CloudflareDDNS.tsx](frontend/src/pages/CloudflareDDNS.tsx#L13) | It is the only clearly orphaned screen and creates feature duplication. |
| P1 | Normalize route and page naming across tool screens | The current naming pattern mixes page, tool, and plugin labels and is easy to misread. |
| P1 | Remove stale template material such as the frontend README boilerplate | This lowers maintenance noise and makes the repo feel intentional. |

## Priority 2: Improve Product Coherence

| Priority | Item | Why it matters |
|---|---|---|
| P2 | Consolidate the visual system into a shared token and spacing language | The UI currently mixes gradients, inline colors, and default surfaces. |
| P2 | Replace icon-only or tooltip-dependent actions with clearer labeled controls where practical | Operator tools should optimize for speed without sacrificing clarity. |
| P2 | Standardize login and dashboard presentation so they feel like one product | These are the most visible surfaces and currently feel the most different. |

## Priority 3: Harden The Operational Model

| Priority | Item | Why it matters |
|---|---|---|
| P3 | Expand regression coverage around route reachability, plugin listing, and task execution | These are the highest-value interactions for the product. |
| P3 | Document external prerequisites for Docker, FUSE, wrangler, Cloudflare, and AI features | Most user friction in this product comes from environment setup, not the UI. |
| P3 | Audit the repo for remaining stale templates, duplicate screens, and unused helper scripts | Freeze should end with less maintenance surface, not more. |

## Recommended Execution Order

1. Fix the packaging/runtime contract.
2. Remove duplicate and stale surfaces.
3. Tighten UI consistency.
4. Backfill tests around the stabilized flows.

## Freeze-Appropriate Definition Of Done

- No new product features.
- Fewer duplicate screens.
- Clearer deployment path.
- More consistent UI language.
- Better regression confidence on the core automation flows.