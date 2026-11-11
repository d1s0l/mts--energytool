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


def test_lead_creation_retry_errors_and_retention(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_MODE", "local")
    (tmp_path / "kb.md").write_text("# Учёт\nУчёт потребления.\n", encoding="utf-8")
    contacts = {
        "full_name": "Тестовый Пользователь",
        "phone": "+7 (999) 000-00-00",
        "company": "Тестовая компания",
    }

    async def summarize(*, history):
        assert history == [{"role": "user", "content": "Нужен учёт по складам"}]
        return "Нужен учёт потребления по складам."

    async def timeout(*, history):
        raise TimeoutError

    async def invalid_summary(*, history):
        return " "

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
                session_id = client.post("/api/sessions").json()["id"]
                session = f"/api/sessions/{session_id}"
                path = session + "/lead"
                assert client.get(path).json()["error"]["code"] == "lead_not_found"
                assert client.post(path, json=contacts).json()["error"]["code"] == "empty_dialog"
                await conn.execute(
                    "INSERT INTO messages (session_id, role, text) VALUES (%s, 'user', %s)",
                    (session_id, "Нужен учёт по складам"),
                )
                for bad in ({**contacts, "phone": "abc"}, {**contacts, "company": " "}):
                    assert client.post(path, json=bad).status_code == 422
                assert (
                    client.post(f"/api/sessions/{uuid4()}/lead", json=contacts).status_code == 404
                )
                assert client.post(path, json=contacts).status_code == 503
                for handler, status in ((timeout, 504), (invalid_summary, 503)):
                    application.state.lead_summarizer = handler
                    assert client.post(path, json=contacts).status_code == status
                    assert client.get(path).status_code == 404
                    assert client.get(session).json()["state"] == "chatting"
                application.state.lead_summarizer = summarize
                async with conn.transaction():
                    await conn.execute(
                        "SELECT id FROM sessions WHERE id = %s FOR UPDATE", (session_id,)
                    )
                    assert client.post(path, json=contacts).status_code == 409
                response = client.post(path, json=contacts)
                assert response.status_code == 201
                lead = response.json()
                assert lead["phone"] == "+79990000000"
                assert lead["summary"] == "Нужен учёт потребления по складам."
                assert client.get(path).json() == lead
                assert client.get(session).json()["state"] == "lead_created"
                application.state.lead_summarizer = None
                retry = client.post(path, json={**contacts, "phone": "+79990000000"})
                assert retry.status_code == 200
                assert retry.json() == lead
                assert (
                    client.post(path, json={**contacts, "company": "Другая компания"}).status_code
                    == 409
                )
                corrected = client.put(path, json={**contacts, "company": "Другая компания"})
                assert corrected.status_code == 200
                assert corrected.json()["id"] == lead["id"]
                assert corrected.json()["company"] == "Другая компания"
                lead = corrected.json()
                cursor = await conn.execute(
                    "SELECT count(*) AS count FROM leads WHERE session_id = %s", (session_id,)
                )
                assert (await cursor.fetchone())["count"] == 1
                assert len(client.get(session).json()["messages"]) == 3
                assert client.delete(session).status_code == 204
                assert client.get(path).status_code == 404
                assert client.post(path, json=contacts).status_code == 404
                assert client.get(path + "?include_deleted=true").json() == lead

    asyncio.run(run())
