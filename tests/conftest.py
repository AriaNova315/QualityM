from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    project_root = tmp_path / "qualitym-test"
    return Settings(
        app_name="QualityM",
        app_version="0.1.0",
        host="127.0.0.1",
        port=8003,
        reload=False,
        project_root=project_root,
        database_path=project_root / "qualitym.db",
        generated_dir=project_root / "generated",
        static_dir=Path(__file__).resolve().parents[1] / "app" / "static",
        migrations_dir=Path(__file__).resolve().parents[1] / "migrations",
        bootstrap_seed_path=(
            Path(__file__).resolve().parents[1] / "seeds" / "checklist-library.json"
        ),
        seed_schema_path=(
            Path(__file__).resolve().parents[1]
            / "seeds"
            / "schema"
            / "checklist-library.schema.json"
        ),
        import_temp_dir=project_root / "var" / "imports",
        upload_max_bytes=25 * 1024 * 1024,
        import_preview_ttl_seconds=3600,
    )
