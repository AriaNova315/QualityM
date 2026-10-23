from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import tempfile
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.database import foreign_key_violations, migrate
from app.seeds import analyze_seed_quality, import_seed, validate_seed_payload


DEFAULT_SEED = ROOT / "seeds" / "checklist-library.json"


async def validate(seed_path: Path) -> dict[str, int | str]:
    payload = json.loads(seed_path.read_text(encoding="utf-8"))
    settings = get_settings()
    validate_seed_payload(payload, settings.seed_schema_path)
    quality = analyze_seed_quality(payload)
    if quality["findings"]:
        raise RuntimeError(
            f"Seed content quality validation failed with {quality['finding_count']} findings"
        )
    with tempfile.TemporaryDirectory(prefix="qualitym-seed-") as temp_dir:
        database_path = Path(temp_dir) / "qualitym.db"
        await migrate(database_path, settings.migrations_dir)
        result = await import_seed(database_path, seed_path, settings.seed_schema_path)
        violations = await foreign_key_violations(database_path)
        if violations:
            raise RuntimeError(f"Foreign key violations after seed import: {violations}")
        with closing(sqlite3.connect(database_path)) as connection:
            counts = {
                "checklists": connection.execute(
                    "SELECT COUNT(*) FROM checklist_templates"
                ).fetchone()[0],
                "groups": connection.execute("SELECT COUNT(*) FROM checklist_groups").fetchone()[0],
                "items": connection.execute("SELECT COUNT(*) FROM checklist_items").fetchone()[0],
                "issue_templates": connection.execute(
                    "SELECT COUNT(*) FROM issue_templates"
                ).fetchone()[0],
            }
    return {
        "seed_version": result.seed_version,
        **counts,
        "minimum_issue_templates_per_item": min(
            (int(value) for value, count in quality["template_count_distribution"].items() if count),
            default=0,
        ),
        "maximum_issue_templates_per_item": max(
            (int(value) for value, count in quality["template_count_distribution"].items() if count),
            default=0,
        ),
        "quality_findings": quality["finding_count"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate QualityM checklist seed library")
    parser.add_argument("seed", nargs="?", type=Path, default=DEFAULT_SEED)
    args = parser.parse_args()
    result = asyncio.run(validate(args.seed))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
