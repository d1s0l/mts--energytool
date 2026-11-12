import asyncio
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo

from app.config import Settings
from app.consultant import ConsultantReply
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
                api_access_token="test-access",
                database_url=make_conninfo(
                    os.environ["TEST_DATABASE_URL"], options=f"-c search_path={schema}"
                ),
                knowledge_base_path=tmp_path,
            )
            application = create_app(settings)
            with TestClient(application, headers={"Authorization": "Bearer test-access"}) as client:
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
                assert page["items"][0]["first_message"] is None
                await conn.execute(
                    "INSERT INTO messages (session_id, role, text) "
                    "VALUES (%s, 'user', 'Следующий вопрос')",
                    (first["id"],),
                )
                titled = client.get("/api/sessions?limit=1&offset=1").json()["items"][0]
                assert titled["id"] == first["id"]
                assert titled["first_message"] == "Вопрос"
                assert client.get("/api/sessions?offset=100").json()["items"] == []
                missing = client.get(f"/api/sessions/{uuid4()}")
                assert missing.status_code == 404
                assert missing.json()["error"]["code"] == "session_not_found"
                for path in ("/api/sessions/not-a-uuid", "/api/sessions?limit=0"):
                    invalid = client.get(path)
                    assert invalid.status_code == 422
                    assert invalid.json()["error"]["code"] == "validation_error"
            # Новый запуск приложения не теряет сессии и историю.
            with TestClient(
                create_app(settings), headers={"Authorization": "Bearer test-access"}
            ) as client:
                assert client.get("/api/sessions").json()["total"] == 2
                assert len(client.get(f"/api/sessions/{first['id']}").json()["messages"]) == 3

    asyncio.run(run())


def test_send_message_saves_turn_and_rolls_back_on_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_MODE", "local")
    (tmp_path / "kb.md").write_text("# Учёт\nУчёт потребления.\n", encoding="utf-8")

    async def consultant(*, text, history, search):
        assert search.engine.info["documents"] == 1
        assert history == [{"role": "user", "content": "Предыдущий вопрос"}]
        if text == "Тайм-аут":
            raise TimeoutError
        assert text == "Новый вопрос"
        return ConsultantReply(text="Ответ", state="awaiting_contacts", generation_ms=25)

    async def run():
        async with database() as conn:
            schema = (await (await conn.execute("SELECT current_schema() AS name")).fetchone())[
                "name"
            ]
            settings = Settings(
                api_access_token="test-access",
                database_url=make_conninfo(
                    os.environ["TEST_DATABASE_URL"], options=f"-c search_path={schema}"
                ),
                knowledge_base_path=tmp_path,
            )
            application = create_app(settings)
            with TestClient(application, headers={"Authorization": "Bearer test-access"}) as client:
                session_id = client.post("/api/sessions").json()["id"]
                detail = f"/api/sessions/{session_id}"
                path = f"{detail}/messages"
                assert client.post(path, json={"text": " "}).status_code == 422
                assert (
                    client.post(
                        f"/api/sessions/{uuid4()}/messages", json={"text": "Вопрос"}
                    ).status_code
                    == 404
                )
                missing = client.post(path, json={"text": "Вопрос"})
                assert missing.status_code == 503
                assert missing.json()["error"]["code"] == "consultant_unavailable"
                assert client.get(detail).json()["messages"][0]["status"] == "failed"

                await conn.execute(
                    "INSERT INTO messages (session_id, role, text) VALUES (%s, 'user', %s)",
                    (session_id, "Предыдущий вопрос"),
                )
                application.state.consultant = consultant
                async with conn.transaction():
                    await conn.execute(
                        "SELECT id FROM sessions WHERE id = %s FOR UPDATE", (session_id,)
                    )
                    assert client.post(path, json={"text": "Вопрос"}).status_code == 409
                timed_out = client.post(path, json={"text": "Тайм-аут"})
                assert timed_out.status_code == 504
                before = client.get(detail).json()
                assert before["state"] == "chatting"
                assert len(before["messages"]) == 3

                response = client.post(path, json={"text": "  Новый вопрос  "})
                assert response.status_code == 200
                turn = response.json()
                assert turn["state"] == "awaiting_contacts"
                assert turn["user_message"]["text"] == "Новый вопрос"
                assert turn["assistant_message"]["text"] == "Ответ"
                assert turn["assistant_message"]["debug"]["generation_ms"] == 25
                saved = client.get(detail).json()
                assert saved["state"] == turn["state"]
                assert saved["messages"][-2:] == [turn["user_message"], turn["assistant_message"]]

    asyncio.run(run())


def test_delete_session_hides_chat_and_preserves_records(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_MODE", "local")
    (tmp_path / "kb.md").write_text("# Учёт\nУчёт потребления.\n", encoding="utf-8")

    async def run():
        async with database() as conn:
            cursor = await conn.execute("SELECT current_schema() AS name")
            schema = (await cursor.fetchone())["name"]
            settings = Settings(
                api_access_token="test-access",
                database_url=make_conninfo(
                    os.environ["TEST_DATABASE_URL"], options=f"-c search_path={schema}"
                ),
                knowledge_base_path=tmp_path,
            )
            with TestClient(
                create_app(settings), headers={"Authorization": "Bearer test-access"}
            ) as client:
                session_id = client.post("/api/sessions").json()["id"]
                path = f"/api/sessions/{session_id}"
                await conn.execute(
                    "INSERT INTO messages (session_id, role, text) VALUES (%s, 'user', 'Вопрос')",
                    (session_id,),
                )
                await conn.execute(
                    "INSERT INTO leads (session_id, full_name, phone, company, summary) "
                    "VALUES (%s, 'Тест', '+79990000000', 'Компания', 'Резюме')",
                    (session_id,),
                )
                async with conn.transaction():
                    await conn.execute(
                        "SELECT id FROM sessions WHERE id = %s FOR UPDATE", (session_id,)
                    )
                    assert client.delete(path).status_code == 409
                response = client.delete(path)
                assert response.status_code == 204
                assert response.content == b""
                assert client.delete(path).status_code == 204
                assert client.get("/api/sessions").json()["total"] == 0
                assert client.get(path).status_code == 404
                assert client.post(path + "/messages", json={"text": "Вопрос"}).status_code == 404
                retained = client.get(path + "?include_deleted=true").json()
                assert retained["deleted_at"]
                assert retained["messages"][0]["text"] == "Вопрос"
                assert client.get("/api/sessions?include_deleted=true").json()["total"] == 1
                cursor = await conn.execute(
                    "SELECT count(*) AS count FROM leads WHERE session_id = %s", (session_id,)
                )
                assert (await cursor.fetchone())["count"] == 1
                assert client.delete(f"/api/sessions/{uuid4()}").status_code == 404
                assert client.delete("/api/sessions/not-a-uuid").status_code == 422

    asyncio.run(run())
