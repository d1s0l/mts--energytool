"""Последовательные SQL-миграции с транзакцией и защитой от параллельного запуска."""

import hashlib
from pathlib import Path

from psycopg import AsyncConnection

MIGRATIONS_DIR = Path(__file__).with_name("migrations")
MIGRATION_LOCK_ID = 7241095201


async def migrate(conn: AsyncConnection, directory: Path = MIGRATIONS_DIR) -> None:
    paths = sorted(directory.glob("[0-9][0-9][0-9]_*.sql"))
    if not paths:
        raise RuntimeError("SQL migrations are missing")
    versions = [path.name.split("_", 1)[0] for path in paths]
    if len(versions) != len(set(versions)):
        raise RuntimeError("Duplicate SQL migration version")

    async with conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(%s)", (MIGRATION_LOCK_ID,))
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                name text PRIMARY KEY,
                checksum text NOT NULL,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
        """)
        cursor = await conn.execute("SELECT name, checksum FROM schema_migrations ORDER BY name")
        applied = {row["name"]: row["checksum"] for row in await cursor.fetchall()}
        available = {path.name for path in paths}
        if set(applied) - available:
            raise RuntimeError("An applied SQL migration is missing from the application")

        for path in paths:
            source = path.read_bytes()
            checksum = hashlib.sha256(source).hexdigest()
            if path.name in applied:
                if applied[path.name] != checksum:
                    raise RuntimeError(f"Applied migration was modified: {path.name}")
                continue
            if applied and path.name < max(applied):
                raise RuntimeError(f"Migration added before an applied version: {path.name}")
            await conn.execute(source.decode("utf-8"))
            await conn.execute(
                "INSERT INTO schema_migrations (name, checksum) VALUES (%s, %s)",
                (path.name, checksum),
            )
