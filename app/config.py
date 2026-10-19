from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _path_from_env(name: str, default: Path) -> Path:
    raw_value = os.getenv(name)
    return Path(raw_value).expanduser().resolve() if raw_value else default.resolve()


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str
    app_version: str
    host: str
    port: int
    reload: bool
    project_root: Path
    database_path: Path
    generated_dir: Path
    static_dir: Path
    migrations_dir: Path
    bootstrap_seed_path: Path
    seed_schema_path: Path
    import_temp_dir: Path
    upload_max_bytes: int
    import_preview_ttl_seconds: int
    export_queue_max: int = 2


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        app_name="QualityM",
        app_version="0.1.0",
        host=os.getenv("QUALITYM_HOST", "127.0.0.1"),
        port=int(os.getenv("QUALITYM_PORT", "8003")),
        reload=os.getenv("QUALITYM_RELOAD", "1") not in {"0", "false", "False"},
        project_root=PROJECT_ROOT,
        database_path=_path_from_env("QUALITYM_DATABASE_PATH", PROJECT_ROOT / "qualitym.db"),
        generated_dir=_path_from_env("QUALITYM_GENERATED_DIR", PROJECT_ROOT / "generated"),
        static_dir=_path_from_env("QUALITYM_STATIC_DIR", PROJECT_ROOT / "app" / "static"),
        migrations_dir=_path_from_env("QUALITYM_MIGRATIONS_DIR", PROJECT_ROOT / "migrations"),
        bootstrap_seed_path=_path_from_env(
            "QUALITYM_BOOTSTRAP_SEED_PATH",
            PROJECT_ROOT / "seeds" / "checklist-library.json",
        ),
        seed_schema_path=_path_from_env(
            "QUALITYM_SEED_SCHEMA_PATH",
            PROJECT_ROOT / "seeds" / "schema" / "checklist-library.schema.json",
        ),
        import_temp_dir=_path_from_env(
            "QUALITYM_IMPORT_TEMP_DIR", PROJECT_ROOT / "var" / "imports"
        ),
        upload_max_bytes=int(os.getenv("QUALITYM_UPLOAD_MAX_BYTES", str(25 * 1024 * 1024))),
        import_preview_ttl_seconds=int(os.getenv("QUALITYM_IMPORT_PREVIEW_TTL_SECONDS", "3600")),
        export_queue_max=int(os.getenv("QUALITYM_EXPORT_QUEUE_MAX", "2")),
    )
