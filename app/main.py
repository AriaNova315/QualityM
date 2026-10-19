from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings, get_settings
from app.database import initialize_database
from app.application.export_service import ExportJobManager
from app.application.project_version_service import recover_project_version_storage
from app.logging_config import configure_logging
from app.routers.health import router as health_router
from app.routers.audits import router as audits_router
from app.routers.imports import router as imports_router
from app.routers.projects import router as projects_router
from app.routers.exports import router as exports_router
from app.routers.templates import router as templates_router


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        application.state.schema_version = await initialize_database(
            resolved_settings.database_path,
            resolved_settings.migrations_dir,
            resolved_settings.bootstrap_seed_path,
            resolved_settings.seed_schema_path,
        )
        application.state.storage_recovery = await recover_project_version_storage(
            resolved_settings.database_path,
            resolved_settings.generated_dir,
        )
        manager = ExportJobManager(
            resolved_settings.database_path,
            resolved_settings.generated_dir,
            resolved_settings.export_queue_max,
        )
        application.state.export_job_manager = manager
        await manager.start()
        try:
            yield
        finally:
            await manager.stop()

    application = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    application.state.settings = resolved_settings
    application.include_router(health_router)
    application.include_router(audits_router)
    application.include_router(imports_router)
    application.include_router(projects_router)
    application.include_router(exports_router)
    application.include_router(templates_router)
    application.mount(
        "/static",
        StaticFiles(directory=resolved_settings.static_dir, check_dir=True),
        name="static",
    )

    @application.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(resolved_settings.static_dir / "index.html")

    return application


app = create_app()
