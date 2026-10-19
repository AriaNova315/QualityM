from __future__ import annotations

import hashlib
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import AsyncIterator

import aiosqlite


MIGRATION_PATTERN = re.compile(r"^(?P<version>[0-9]{3})_(?P<name>[a-z0-9_]+)\.sql$")


class MigrationError(RuntimeError):
    """Raised when migration discovery or application is unsafe."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    path: Path
    sql: str
    checksum: str


def discover_migrations(migrations_dir: Path) -> tuple[Migration, ...]:
    if not migrations_dir.is_dir():
        raise MigrationError(f"Migration directory does not exist: {migrations_dir}")

    migrations: list[Migration] = []
    for path in sorted(migrations_dir.glob("*.sql")):
        match = MIGRATION_PATTERN.fullmatch(path.name)
        if match is None:
            raise MigrationError(f"Invalid migration filename: {path.name}")
        raw_sql = path.read_bytes()
        migrations.append(
            Migration(
                version=int(match.group("version")),
                name=match.group("name"),
                path=path,
                sql=raw_sql.decode("utf-8"),
                checksum=hashlib.sha256(raw_sql).hexdigest(),
            )
        )

    if not migrations:
        raise MigrationError("No migrations found")

    versions = [migration.version for migration in migrations]
    expected = list(range(1, len(versions) + 1))
    if versions != expected:
        raise MigrationError(
            f"Migration versions must be contiguous from 1; found {versions}, expected {expected}"
        )
    return tuple(migrations)


async def configure_connection(connection: aiosqlite.Connection) -> None:
    await connection.execute("PRAGMA foreign_keys = ON")
    await connection.execute("PRAGMA busy_timeout = 5000")
    cursor = await connection.execute("PRAGMA foreign_keys")
    row = await cursor.fetchone()
    if row is None or row[0] != 1:
        raise RuntimeError("SQLite foreign key enforcement could not be enabled")


@asynccontextmanager
async def connect(database_path: Path) -> AsyncIterator[aiosqlite.Connection]:
    connection = await aiosqlite.connect(database_path)
    connection.row_factory = aiosqlite.Row
    try:
        await configure_connection(connection)
        yield connection
    finally:
        await connection.close()


@asynccontextmanager
async def transaction(connection: aiosqlite.Connection) -> AsyncIterator[None]:
    if connection.in_transaction:
        raise RuntimeError("Nested transactions are not supported")
    await connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        await connection.rollback()
        raise
    else:
        await connection.commit()


def _migration_script(migration: Migration) -> str:
    applied_at = datetime.now(UTC).isoformat()
    checksum = migration.checksum.replace("'", "''")
    name = migration.name.replace("'", "''")
    return (
        "BEGIN IMMEDIATE;\n"
        f"{migration.sql.rstrip()}\n"
        "INSERT INTO schema_migrations(version, name, checksum, applied_at) "
        f"VALUES ({migration.version}, '{name}', '{checksum}', '{applied_at}');\n"
        "COMMIT;"
    )


async def migrate(database_path: Path, migrations_dir: Path) -> int:
    migrations = discover_migrations(migrations_dir)
    database_path.parent.mkdir(parents=True, exist_ok=True)

    async with connect(database_path) as connection:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )
        await connection.commit()
        cursor = await connection.execute(
            "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
        )
        applied_rows = await cursor.fetchall()
        applied = {int(row["version"]): row for row in applied_rows}
        applied_versions = sorted(applied)
        if applied_versions != list(range(1, len(applied_versions) + 1)):
            raise MigrationError(f"Applied migration versions contain a gap: {applied_versions}")

        unknown_versions = sorted(set(applied) - {item.version for item in migrations})
        if unknown_versions:
            raise MigrationError(f"Database contains unknown migration versions: {unknown_versions}")

        for migration in migrations:
            existing = applied.get(migration.version)
            if existing is not None:
                if existing["checksum"] != migration.checksum or existing["name"] != migration.name:
                    raise MigrationError(
                        f"Migration {migration.version:03d}_{migration.name} checksum or name changed"
                    )
                continue
            try:
                await connection.executescript(_migration_script(migration))
            except aiosqlite.Error as exc:
                if connection.in_transaction:
                    await connection.rollback()
                raise MigrationError(
                    f"Migration {migration.version:03d}_{migration.name} failed"
                ) from exc

        cursor = await connection.execute("SELECT COUNT(*) FROM schema_migrations")
        row = await cursor.fetchone()
        return int(row[0])


async def initialize_database(
    database_path: Path,
    migrations_dir: Path,
    bootstrap_seed_path: Path,
    seed_schema_path: Path | None = None,
) -> int:
    schema_version = await migrate(database_path, migrations_dir)
    from app.seeds import import_seed

    await import_seed(database_path, bootstrap_seed_path, seed_schema_path)
    return schema_version


async def foreign_key_violations(database_path: Path) -> list[aiosqlite.Row]:
    async with connect(database_path) as connection:
        cursor = await connection.execute("PRAGMA foreign_key_check")
        return list(await cursor.fetchall())
