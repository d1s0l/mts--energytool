"""PostgreSQL integration regressions for ownership and retry semantics."""

import asyncio
import os
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo

from app.config import Settings
from app.consultant import ConsultantReply, ConsultantUnavailable
from app.main import create_app
from tests.test_migrations import database

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="Set TEST_DATABASE_URL")


def test_session_capability_and_idempotent_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_MODE", "local")
    for name in ("LLM_MODEL", "OPENAI_MODEL", "GEN_MODEL"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "kb.md").write_text("# Учет\nУчет объектов.\n")

    async def run():
        async with database() as conn:
            schema = (await (await conn.execute("SELECT current_schema() AS name")).fetchone())[
                "name"
            ]
            calls = []

            async def consultant(*, text, history, search):
                calls.append(text)
                if text.startswith("Сбой") and calls.count(text) == 1:
                    raise ConsultantUnavailable(
                        debug={
                            "raw_response": "не JSON",
                            "parsed_response": None,
                            "validation_errors": ["Attempt 1: JSONDecodeError"],
                            "final_response": None,
                        }
                    )
                return ConsultantReply(
                    text="Проверенный ответ",
                    raw_response='{"answer":"Проверенный ответ"}',
                    parsed_response={"answer": "Проверенный ответ"},
                )

            settings = Settings(
                database_url=make_conninfo(
                    os.environ["TEST_DATABASE_URL"], options=f"-c search_path={schema}"
                ),
                knowledge_base_path=tmp_path,
                api_access_token="reviewer-secret",
            )
            with TestClient(create_app(settings, consultant=consultant)) as client:
                first = client.post("/api/sessions").json()
                other = client.post("/api/sessions").json()
                path = f"/api/sessions/{first['id']}"
                own = {"X-Session-Token": first["access_token"]}
                legacy = client.post("/api/sessions").json()
                await conn.execute(
                    "INSERT INTO messages (session_id, role, text, debug) "
                    "VALUES (%s, 'assistant', 'Старый ответ', %s::jsonb)",
                    (legacy["id"], '{"total_ms": 1, "sources": []}'),
                )
                legacy_debug = client.get(
                    f"/api/sessions/{legacy['id']}/debug",
                    headers={"X-Session-Token": legacy["access_token"]},
                ).json()
                assert legacy_debug["items"][0]["debug"] == {
                    "total_ms": 1,
                    "sources": [],
                }
                assert calls == []
                assert client.get(path + "/debug").status_code == 403
                assert (
                    client.get(
                        path + "/debug", headers={"X-Session-Token": other["access_token"]}
                    ).status_code
                    == 403
                )
                assert client.get(path + "/debug", headers=own).json() == {
                    "session_id": first["id"],
                    "items": [],
                }
                assert client.get(path).status_code == 403
                assert (
                    client.get(path, headers={"X-Session-Token": other["access_token"]}).status_code
                    == 403
                )
                assert client.get("/api/sessions", headers=own).status_code == 403
                assert (
                    client.get(
                        "/api/sessions", headers={"Authorization": "Bearer reviewer-secret"}
                    ).status_code
                    == 200
                )
                request = {"text": "Вопрос", "client_message_id": str(uuid4())}
                initial = client.post(path + "/messages", json=request, headers=own)
                repeated = client.post(path + "/messages", json=request, headers=own)
                assert initial.status_code == repeated.status_code == 200
                assert initial.json() == repeated.json()
                assert "raw_response" not in initial.json()["assistant_message"]["debug"]
                assert (
                    "raw_response_attempts"
                    not in initial.json()["assistant_message"]["debug"]
                )
                assert "parsed_response" not in initial.json()["assistant_message"]["debug"]
                assert "validation_errors" not in initial.json()["assistant_message"]["debug"]
                assert "final_response" not in initial.json()["assistant_message"]["debug"]
                assert calls == ["Вопрос"]
                ordinary_history = client.get(path, headers=own).json()["messages"]
                assert all(
                    "raw_response" not in message["debug"]
                    for message in ordinary_history
                    if message["debug"]
                )
                debug_response = client.get(path + "/debug", headers=own)
                assert debug_response.status_code == 200
                assistant_debug = debug_response.json()["items"][0]
                assert assistant_debug["message_id"] == initial.json()["assistant_message"]["id"]
                assert assistant_debug["debug"]["raw_response"] == '{"answer":"Проверенный ответ"}'
                assert assistant_debug["debug"]["parsed_response"] == {
                    "answer": "Проверенный ответ"
                }
                assert assistant_debug["debug"]["validation_errors"] == []
                assert assistant_debug["debug"]["final_response"] == "Проверенный ответ"
                assert assistant_debug["role"] == "assistant"
                assert calls == ["Вопрос"]
                assert (
                    client.post(
                        path + "/messages", json={**request, "text": "Другой"}, headers=own
                    ).status_code
                    == 409
                )
                failed = {"text": "Сбой", "client_message_id": str(uuid4())}
                assert client.post(path + "/messages", json=failed, headers=own).status_code == 503
                history = client.get(path, headers=own).json()["messages"]
                assert history[-1]["text"] == "Сбой"
                assert history[-1]["status"] == "failed"
                assert history[-1]["debug"] is None
                failure_debug = client.get(path + "/debug", headers=own).json()["items"]
                failed_item = next(
                    item for item in failure_debug if item["message_id"] == history[-1]["id"]
                )
                assert failed_item["debug"]["raw_response"] == "не JSON"
                assert failed_item["debug"]["validation_errors"] == [
                    "Attempt 1: JSONDecodeError"
                ]
                assert failed_item["role"] == "user"
                assert calls == ["Вопрос", "Сбой"]
                assert client.post(path + "/messages", json=failed, headers=own).status_code == 200
                history = client.get(path, headers=own).json()["messages"]
                assert len(history) == 4
                assert all(m["status"] == "completed" for m in history)
                assert calls == ["Вопрос", "Сбой", "Сбой"]
                legacy = {"text": "Сбой без ID"}
                assert client.post(path + "/messages", json=legacy, headers=own).status_code == 503
                before = client.get(path, headers=own).json()["messages"]
                retry = client.post(path + "/messages", json=legacy, headers=own)
                assert retry.status_code == 200
                assert retry.json()["user_message"]["id"] == before[-1]["id"]
                after = client.get(path, headers=own).json()["messages"]
                assert len(after) == len(before) + 1
                assert client.delete(path, headers=own).status_code == 204
                assert client.get(path, headers=own).status_code == 404
                assert client.get(path + "/debug", headers=own).status_code == 404
                assert (
                    client.get(path + "/debug?include_deleted=true", headers=own).status_code == 200
                )
                assert (
                    client.get(
                        f"/api/sessions/{uuid4()}/debug",
                        headers={"Authorization": "Bearer reviewer-secret"},
                    ).status_code
                    == 404
                )
                assert client.get(path + "?include_deleted=true", headers=own).status_code == 200

    asyncio.run(run())
