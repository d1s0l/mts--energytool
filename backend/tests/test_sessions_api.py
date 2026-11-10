import asyncio
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo

from app.config import Settings
from app.main import create_app
from tests.test_migrations import database

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"), reason="Set TEST_DATABASE_URL"
)


def test_sessions_persist_and_history_is_isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_MODE", "local")
    (tmp_path / "kb.md").write_text("# Учёт\nПлатформа учёта потребления.\n", encoding="utf-8")

    async def run():
        async with database() as conn:
            cursor = await conn.execute("SELECT current_schema() AS name")
            schema = (await cursor.fetchone())["name"]
            settings = Settings(
                database_url=make_conninfo(
                    os.environ["TEST_DATABASE_URL"], options=f"-c search_path={schema}"
                ),
                knowledge_base_path=tmp_path,
            )
            application = create_app(settings)
            with TestClient(application) as client:
                assert application.state.search.engine.info["documents"] == 1
                assert client.get("/api/sessions").json()["total"] == 0
                response = client.post("/api/sessions")
                assert response.status_code == 201
                first = response.json()
                assert first["state"] == "chatting"
                second = client.post("/api/sessions").json()
                assert first["id"] != second["id"]
                await conn.execute(
                    "INSERT INTO messages (session_id, role, text) VALUES (%s, 'user', 'Вопрос')",
                    (first["id"],),
                )
                await conn.execute(
                    "INSERT INTO messages (session_id, role, text, debug) "
                    "VALUES (%s, 'assistant', 'Ответ', '{\"search_ms\": 1}'::jsonb)",
                    (first["id"],),
                )
                history = client.get(f"/api/sessions/{first['id']}").json()["messages"]
                assert [m["text"] for m in history] == ["Вопрос", "Ответ"]
                assert history[1]["debug"] == {"search_ms": 1}
                assert client.get(f"/api/sessions/{second['id']}").json()["messages"] == []
                page = client.get("/api/sessions?limit=1&offset=0").json()
                assert page["total"] == 2
                assert page["items"][0]["id"] == second["id"]
                assert client.get("/api/sessions?offset=100").json()["items"] == []
                missing = client.get(f"/api/sessions/{uuid4()}")
                assert missing.status_code == 404
                assert missing.json()["error"]["code"] == "session_not_found"
                for path in ("/api/sessions/not-a-uuid", "/api/sessions?limit=0"):
                    invalid = client.get(path)
                    assert invalid.status_code == 422
                    assert invalid.json()["error"]["code"] == "validation_error"
            # Новый запуск приложения не теряет сессии и историю.
            with TestClient(create_app(settings)) as client:
                assert client.get("/api/sessions").json()["total"] == 2
                assert len(client.get(f"/api/sessions/{first['id']}").json()["messages"]) == 2

    asyncio.run(run())
