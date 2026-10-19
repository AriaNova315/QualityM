from __future__ import annotations

import importlib
import asyncio
import sqlite3
from pathlib import Path

import httpx

from app.config import Settings
from app.main import create_app


async def _get(app, path: str) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(path)


def test_health_reports_version_without_creating_database(test_settings: Settings) -> None:
    app = create_app(test_settings)
    response = asyncio.run(_get(app, "/api/health"))

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "application": "QualityM",
        "version": "0.1.0",
        "database": {"status": "not_initialized", "exists": False},
    }
    assert not test_settings.database_path.exists()
    assert not test_settings.generated_dir.exists()


def test_index_loads_es_module_shell(test_settings: Settings) -> None:
    app = create_app(test_settings)
    response = asyncio.run(_get(app, "/"))
    module_response = asyncio.run(_get(app, "/static/app.js"))

    assert response.status_code == 200
    assert "QualityM" in response.text
    assert "检查单与问题模板" in response.text
    assert "previewTemplateImport" in module_response.text
    assert 'type="module"' in response.text
    assert module_response.status_code == 200
    assert "qualityMApp" in module_response.text


def test_health_checks_an_existing_database(test_settings: Settings) -> None:
    test_settings.database_path.parent.mkdir(parents=True)
    with sqlite3.connect(test_settings.database_path) as connection:
        connection.execute("CREATE TABLE scaffold_check (id INTEGER PRIMARY KEY)")

    app = create_app(test_settings)
    response = asyncio.run(_get(app, "/api/health"))

    assert response.status_code == 200
    assert response.json()["database"] == {
        "status": "ready",
        "exists": True,
        "integrity": "ok",
    }


def test_importing_main_has_no_filesystem_side_effects(
    monkeypatch,
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state" / "qualitym.db"
    generated_dir = tmp_path / "exports"
    monkeypatch.setenv("QUALITYM_DATABASE_PATH", str(database_path))
    monkeypatch.setenv("QUALITYM_GENERATED_DIR", str(generated_dir))

    import app.config as config_module
    import app.main as main_module

    config_module.get_settings.cache_clear()
    importlib.reload(main_module)

    assert not database_path.exists()
    assert not generated_dir.exists()
    assert not database_path.parent.exists()

    config_module.get_settings.cache_clear()


def test_default_port_is_reserved_for_qualitym(test_settings: Settings) -> None:
    assert test_settings.port == 8003
