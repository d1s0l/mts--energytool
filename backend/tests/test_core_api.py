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
                    raise ConsultantUnavailable()
                return ConsultantReply(text="Проверенный ответ")

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
                assert client.post(path + "/messages", json=failed, headers=own).status_code == 200
                history = client.get(path, headers=own).json()["messages"]
                assert len(history) == 4
                assert all(m["status"] == "completed" for m in history)
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
                assert client.get(path + "?include_deleted=true", headers=own).status_code == 200

    asyncio.run(run())
