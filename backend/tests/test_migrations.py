"""Проверки на реальном PostgreSQL в отдельной временной схеме."""

import asyncio
import os
from contextlib import asynccontextmanager
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.rows import dict_row

from app.migrate import MIGRATIONS_DIR, migrate

DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="Set TEST_DATABASE_URL")


@asynccontextmanager
async def database():
    schema = "test_" + uuid4().hex
    async with await psycopg.AsyncConnection.connect(
        DATABASE_URL, autocommit=True, row_factory=dict_row
    ) as conn:
        await conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            await conn.execute("SELECT set_config('search_path', %s, false)", (schema,))
            yield conn
        finally:
            await conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_repeat_migration_preserves_history_and_relations():
    async def run():
        async with database() as conn:
            await migrate(conn)
            cursor = await conn.execute("INSERT INTO sessions DEFAULT VALUES RETURNING id")
            session_id = (await cursor.fetchone())["id"]
            for role in ("user", "assistant"):
                await conn.execute(
                    "INSERT INTO messages (session_id, role, text) VALUES (%s, %s, %s)",
                    (session_id, role, "Тестовый текст"),
                )
            await conn.execute(
                "INSERT INTO leads (session_id, full_name, phone, company, summary) "
                "VALUES (%s, 'Тест', '+79990000000', 'Тестовая компания', 'Тестовое резюме')",
                (session_id,),
            )
            await conn.execute("INSERT INTO sessions DEFAULT VALUES")
            await migrate(conn)
            for table, expected in (
                ("sessions", 2),
                ("messages", 2),
                ("leads", 1),
                ("schema_migrations", 1),
            ):
                cursor = await conn.execute(
                    sql.SQL("SELECT count(*) AS count FROM {}").format(sql.Identifier(table))
                )
                assert (await cursor.fetchone())["count"] == expected
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                await conn.execute("DELETE FROM sessions WHERE id = %s", (session_id,))
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                await conn.execute(
                    "INSERT INTO messages (session_id, role, text) VALUES (%s, 'user', 'test')",
                    (uuid4(),),
                )
            with pytest.raises(psycopg.errors.CheckViolation):
                await conn.execute(
                    "INSERT INTO messages (session_id, role, text) VALUES (%s, 'user', '   ')",
                    (session_id,),
                )

    asyncio.run(run())


def test_modified_migration_fails_and_broken_migration_rolls_back(tmp_path):
    async def run():
        initial = MIGRATIONS_DIR / "001_initial.sql"
        copied = tmp_path / initial.name
        copied.write_bytes(initial.read_bytes())
        async with database() as conn:
            await migrate(conn, tmp_path)
            copied.write_text(copied.read_text() + "\n-- changed\n")
            with pytest.raises(RuntimeError, match="modified"):
                await migrate(conn, tmp_path)
            copied.write_bytes(initial.read_bytes())
            (tmp_path / "002_broken.sql").write_text(
                "CREATE TABLE should_rollback (id int); SELECT * FROM missing_test_table;"
            )
            with pytest.raises(psycopg.errors.UndefinedTable):
                await migrate(conn, tmp_path)
            cursor = await conn.execute("SELECT to_regclass('should_rollback') AS name")
            assert (await cursor.fetchone())["name"] is None
            cursor = await conn.execute("SELECT count(*) AS count FROM schema_migrations")
            assert (await cursor.fetchone())["count"] == 1

    asyncio.run(run())
