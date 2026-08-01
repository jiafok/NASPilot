# ARCHITECTURE AUDIT

Project freeze status: report only, no architecture expansion.

## Architecture In One Sentence

NASPilot is a monolithic FastAPI backend plus React SPA, packaged for Docker, with a plugin registry and APScheduler providing the main extensibility and job execution model.

## Layering

- Presentation layer: React SPA in [frontend/src](frontend/src), routed through [frontend/src/App.tsx](frontend/src/App.tsx#L30).
- API layer: FastAPI routers aggregated under [backend/app/api/v1/router.py](backend/app/api/v1/router.py).
- Service layer: domain services in [backend/app/services](backend/app/services).
- Persistence layer: async SQLAlchemy in [backend/app/core/database.py](backend/app/core/database.py).
- Plugin layer: builtin plugins discovered via [backend/app/plugins/registry.py](backend/app/plugins/registry.py).
- Scheduling layer: APScheduler integration in [backend/app/scheduler/scheduler_service.py](backend/app/scheduler/scheduler_service.py).

## Startup And Boot Sequence

The app lifecycle is explicit and deterministic:

1. Logging is configured.
2. Database tables are created and legacy SQLite migration cleanup runs.
3. The initial admin user is bootstrapped.
4. Builtin plugins are imported and registered.
5. Builtin plugins are synced into the database.
6. The scheduler starts and jobs are restored.
7. The metrics collector starts.

That flow is implemented in [backend/app/lifespan.py](backend/app/lifespan.py#L30) and is the effective control plane for the system.

## Request And Runtime Flow

- The API is mounted under /api/v1, while health and docs live in [backend/app/main.py](backend/app/main.py#L22).
- The same backend also serves the compiled SPA when the image build has populated [backend/frontend/dist](Dockerfile#L60).
- SPA fallback is intentionally disabled when the built frontend is absent, which is appropriate for source-only runs but means the backend is not fully self-contained without the frontend build artifact.

## Plugin Architecture

- Builtin plugins are imported by module path and discovered by class metadata.
- Plugin records are synchronized into the plugins table so the API and UI can enumerate them.
- Scheduled plugin runs are persisted in instance config and converted into scheduler jobs.
- Manual and scheduled plugin execution share the same plugin runtime contract, which keeps behavior aligned.

## Data And Operations

- SQLite is the default persistence target, with async access via SQLAlchemy.
- Task execution writes logs to disk and records execution history in the database.
- Notification delivery is centralized through the notification service.
- Docker access is assumed for container-oriented features and therefore remains an external operational dependency.

## Architectural Strengths

- Clear boot order and lifecycle management.
- Plugin registry gives a single extensibility point.
- API, scheduler, and UI are all aligned around the same core feature model.
- Docker packaging makes deployment repeatable.

## Architectural Risks

- The product is tightly coupled to a single monolith and a shared SQLite database by default.
- Feature modules are numerous, so inconsistencies in naming and route mapping can accumulate quickly.
- Source runs depend on the frontend build artifact being present at the path expected by the backend.
- Some flows still rely on external CLIs or host capabilities, which is normal for NAS automation but increases deployment sensitivity.

## Architecture Verdict

The architecture is pragmatic and appropriate for a single-node NAS automation appliance. The main concern is not structural complexity but operational consistency: build artifacts, plugin discoverability, and contract alignment need to stay tight.