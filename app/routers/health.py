from __future__ import annotations

import sqlite3
from pathlib import Path

import aiosqlite
from fastapi import APIRouter, Request

router = APIRouter(prefix="/api", tags=["system"])


async def _database_health(database_path: Path) -> dict[str, object]:
    if not database_path.is_file():
        return {"status": "not_initialized", "exists": False}

    uri = f"file:{database_path.as_posix()}?mode=ro"
    try:
        async with aiosqlite.connect(uri, uri=True) as connection:
            cursor = await connection.execute("PRAGMA quick_check")
            row = await cursor.fetchone()
    except (aiosqlite.Error, sqlite3.Error, OSError) as exc:
        return {
            "status": "unavailable",
            "exists": True,
            "detail": type(exc).__name__,
        }

    check_result = row[0] if row else "unknown"
    return {
        "status": "ready" if check_result == "ok" else "degraded",
        "exists": True,
        "integrity": check_result,
    }


@router.get("/health")
async def health(request: Request) -> dict[str, object]:
    settings = request.app.state.settings
    database = await _database_health(settings.database_path)
    return {
        "status": "ok" if database["status"] != "unavailable" else "degraded",
        "application": settings.app_name,
        "version": settings.app_version,
        "database": database,
    }
