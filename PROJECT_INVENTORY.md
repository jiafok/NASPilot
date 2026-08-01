# PROJECT INVENTORY

Project freeze status: report only, no feature work.

## What Exists

NASPilot is a two-part monorepo with a Python backend and a React frontend, wrapped by Docker for deployment.

## Top-Level Assets

- [README.md](README.md) describes the product, supported tools, and Docker-first run path.
- [Dockerfile](Dockerfile) builds the frontend, installs backend dependencies, and copies the built SPA into the runtime image.
- [docker-compose.yml](docker-compose.yml) is the production deployment entry point.
- [docker-compose.test.yml](docker-compose.test.yml) is the test deployment variant.
- [run_backup.py](run_backup.py), [check_backup.py](check_backup.py), [check_dups.py](check_dups.py), [check_sched.py](check_sched.py), and [fix_backup_config.py](fix_backup_config.py) are repo-level operational helpers.
- [test_alist.py](test_alist.py) and [test_functional.py](test_functional.py) are root-level validation scripts.

## Backend Inventory

- [backend/app/main.py](backend/app/main.py#L46) is the FastAPI entry point.
- [backend/app/lifespan.py](backend/app/lifespan.py#L30) bootstraps the application lifecycle.
- [backend/app/api/v1/router.py](backend/app/api/v1/router.py) aggregates the API surface.
- [backend/app/core](backend/app/core) contains configuration, database, logging, security, metrics, and dependency wiring.
- [backend/app/models](backend/app/models) contains SQLAlchemy models for users, tasks, logs, notifications, and plugins.
- [backend/app/schemas](backend/app/schemas) contains Pydantic request and response schemas.
- [backend/app/services](backend/app/services) contains domain services for auth, tasks, system, notifications, Docker, and user-facing operations.
- [backend/app/plugins/registry.py](backend/app/plugins/registry.py) implements plugin discovery and lifecycle coordination.
- [backend/app/plugins/builtin](backend/app/plugins/builtin) contains the shipped plugins.
- [backend/app/scheduler/scheduler_service.py](backend/app/scheduler/scheduler_service.py) synchronizes DB-backed schedules into APScheduler.

## Builtin Plugin Set

- [pt_rss](backend/app/plugins/builtin/pt_rss.py)
- [alist_upload](backend/app/plugins/builtin/alist_upload.py)
- [cloudflare_ddns](backend/app/plugins/builtin/cloudflare_ddns.py)
- [docker_backup](backend/app/plugins/builtin/docker_backup.py)
- [log_cleanup](backend/app/plugins/builtin/log_cleanup.py)
- [btrfs_cleanup](backend/app/plugins/builtin/btrfs_cleanup.py)
- [rclone_mount](backend/app/plugins/builtin/rclone_mount.py)
- [cf_pages](backend/app/plugins/builtin/cf_pages.py)

## Frontend Inventory

- [frontend/src/App.tsx](frontend/src/App.tsx) defines the route map and global app shell.
- [frontend/src/layouts/MainLayout.tsx](frontend/src/layouts/MainLayout.tsx) provides the authenticated shell, navigation, and language switcher.
- [frontend/src/pages](frontend/src/pages) contains the routed screens for dashboard, tasks, tools, logs, plugins, files, notifications, settings, and AI.
- [frontend/src/components](frontend/src/components) contains shared UI widgets such as auth, logs, resource monitoring, and plugin configuration forms.
- [frontend/src/hooks/useAuth.tsx](frontend/src/hooks/useAuth.tsx) manages auth state in the frontend.
- [frontend/src/i18n](frontend/src/i18n) contains locale bootstrapping and translations.
- [frontend/src/utils/api.ts](frontend/src/utils/api.ts) centralizes HTTP calls.

## Runtime And Build Outputs

- The production image serves the SPA from [backend/frontend/dist](Dockerfile#L60), which is populated during image build.
- The backend writes data to [data](data) and log output under [data/logs](data/logs).
- The repository also includes generated or persisted runtime artifacts such as [backend/naspilot.egg-info](backend/naspilot.egg-info) and [data/naspilot.db.test](data/naspilot.db.test).

## Inventory Summary

The active product surface is concentrated in the backend API, builtin plugin framework, and the React admin console. Most other files are support assets, deployment helpers, tests, or generated data.