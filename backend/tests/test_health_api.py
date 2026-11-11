import asyncio
import os

import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import make_conninfo

from app.config import Settings
from app.db import create_pool
from app.main import create_app
from tests.test_migrations import database


def test_health_reports_unconfigured_dependencies():
    client = TestClient(create_app())
    response = client.get("/api/health")
    assert response.status_code == 503
    assert response.json() == {
        "status": "not_ready",
        "checks": {
            "database": "not_configured",
            "rag": "not_configured",
            "consultant": "not_configured",
            "lead_summarizer": "not_configured",
        },
    }


@pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL"), reason="Set TEST_DATABASE_URL")
def test_health_checks_database_and_never_calls_models(tmp_path, monkeypatch):
    monkeypatch.setenv("RAG_MODE", "local")
    (tmp_path / "kb.md").write_text("# Учёт\nУчёт потребления.\n", encoding="utf-8")

    async def model(**kwargs):
        raise AssertionError("Health must not invoke the model")

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
                missing = client.get("/api/health")
                assert missing.status_code == 503
                assert missing.json()["checks"]["database"] == "ready"
                assert missing.json()["checks"]["rag"] == "ready"
                application.state.consultant = model
                # Для полной готовности необходим и генератор резюме.
                assert client.get("/api/health").status_code == 503
                application.state.lead_summarizer = model
                ready = client.get("/api/health")
                assert ready.status_code == 200
                assert ready.json()["status"] == "ready"
                assert set(ready.json()["checks"].values()) == {"ready"}
                # Реальный закрытый пул должен приводить к 503, а не исключению API.
                application.state.db_pool = create_pool(settings.database_url)
                failed = client.get("/api/health")
                assert failed.status_code == 503
                assert failed.json()["checks"]["database"] == "not_ready"

    asyncio.run(run())
