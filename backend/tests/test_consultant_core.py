"""Offline regressions for grounding, contact provenance and batch isolation."""

import asyncio
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rag.rag_search import Config, KnowledgeSearch

from app.consultant import ConsultantReply, ConsultantUnavailable
from build_submission import build_submission
from consultant_impl import (
    EnergyConsultant,
    ModelClient,
    ModelConfig,
    contact_intent,
    selected_summary,
    validated_lead,
)
from solution import answer_questions, write_submission

HIT = {
    "chunk_id": "fact",
    "source": "kb.md",
    "title": "Учёт",
    "section": "Цена",
    "text": "Ориентир: 25 000 рублей в год. Итог зависит от проекта.",
    "score": 0.02,
}


class Search:
    async def search(self, text, history=None):
        return {"hits": [HIT], "guardrails": [], "search_ms": 1}


class Client:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.calls = 0

    async def complete(self, messages):
        self.calls += 1
        return next(self.outputs)


def output(**changes):
    data = {
        "answer": "Ориентир — 25 000 рублей в год, итог зависит от проекта.",
        "state": "chatting",
        "kind": "product",
        "evidence_ids": ["fact"],
        "lead": None,
    }
    data.update(changes)
    return json.dumps(data, ensure_ascii=False)


class ConsultantTests(unittest.TestCase):
    def test_real_sources_and_one_generation(self):
        client = Client([output()])
        reply = asyncio.run(
            EnergyConsultant(client)(text="Сколько стоит?", history=[], search=Search())
        )
        self.assertEqual(client.calls, 1)
        self.assertEqual(reply.sources[0].fragment_id, "fact")
        self.assertIn("зависит от проекта", reply.text)

    def test_invented_number_repaired_once(self):
        client = Client([output(answer="Стоимость 999 рублей"), output()])
        reply = asyncio.run(EnergyConsultant(client)(text="Цена?", history=[], search=Search()))
        self.assertEqual(client.calls, 2)
        self.assertNotIn("999", reply.text)

    def test_unknown_sources_and_missing_citations_fail_closed(self):
        for bad in [output(evidence_ids=["invented"]), output(evidence_ids=[]), "broken JSON"]:
            with self.subTest(bad=bad):
                with self.assertRaises(ConsultantUnavailable):
                    asyncio.run(
                        EnergyConsultant(Client([bad, bad]))(
                            text="Цена?", history=[], search=Search()
                        )
                    )

    def test_contacts_must_be_in_user_messages(self):
        contacts = {"full_name": "Тест Тестов", "phone": "+79990000000", "company": "Компания"}
        with self.assertRaises(ValueError):
            validated_lead(contacts, [{"role": "assistant", "content": str(contacts)}])
        messages = [{"role": "user", "content": "Тест Тестов, Компания, +7 (999) 000-00-00"}]
        self.assertEqual(validated_lead(contacts, messages), contacts)

    def test_lead_has_verified_contacts_intent_and_summary(self):
        text = "Оформите заявку. Тест Тестов, Компания, +79990000000. Нужен учет склада."
        client = Client(
            [
                output(
                    answer="Данные готовы для заявки.",
                    kind="contacts",
                    evidence_ids=[],
                    lead={
                        "full_name": "Тест Тестов",
                        "company": "Компания",
                        "phone": "+79990000000",
                    },
                    lead_intent_quote="Оформите заявку.",
                    lead_facts=["Нужен учет склада."],
                )
            ]
        )
        reply = asyncio.run(EnergyConsultant(client)(text=text, history=[], search=Search()))
        self.assertEqual(reply.lead["phone"], "+79990000000")
        self.assertIn("Нужен учет склада.", reply.lead_summary)
        self.assertNotIn("сохранена", reply.text)

    def test_refusal_survives_prior_interest(self):
        messages = [
            {"role": "user", "content": "Оформите заявку"},
            {"role": "user", "content": "Не хочу оставлять телефон"},
        ]
        with self.assertRaises(ValueError):
            contact_intent("Оформите заявку", messages)

    def test_summary_rejects_assistant_inventions(self):
        history = [
            {"role": "user", "content": "Нужен учет"},
            {"role": "assistant", "content": "Мощность 800 кВт"},
        ]
        with self.assertRaises(ValueError):
            selected_summary(["Мощность 800 кВт"], history)

    def test_summary_falls_back_on_provider_failure(self):
        class Offline:
            async def complete(self, messages):
                raise ConsultantUnavailable()

        summary = asyncio.run(
            EnergyConsultant(Offline()).summarize(
                history=[{"role": "user", "content": "Нужен учет склада"}]
            )
        )
        self.assertIn("Нужен учет склада", summary)

    def test_model_settings_aliases_and_no_fixed_generator(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ConsultantUnavailable):
                ModelConfig.from_env()
        with patch.dict(
            os.environ,
            {
                "OPENAI_BASE_URL": "http://proxy/v1/",
                "OPENAI_API_KEY": "secret",
                "GEN_MODEL": "platform-model",
            },
            clear=True,
        ):
            cfg = ModelConfig.from_env()
            self.assertEqual(cfg.model, "platform-model")
            self.assertNotIn("secret", repr(cfg))
            self.assertEqual(cfg.base_url, "http://proxy/v1")

    def test_gateway_uses_platform_model_and_rejects_truncation(self):
        cfg = ModelConfig(base_url="http://proxy", api_key="secret", model="platform-model")

        def response(request, timeout):
            self.assertEqual(json.loads(request.data)["model"], "platform-model")
            return io.BytesIO(
                json.dumps(
                    {"choices": [{"finish_reason": "length", "message": {"content": "partial"}}]}
                ).encode()
            )

        with patch("urllib.request.urlopen", side_effect=response):
            with self.assertRaises(ConsultantUnavailable):
                ModelClient(cfg)._request([])

    def test_single_failure_does_not_abort_other_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "kb.md").write_text("# Учет\nУчет склада.")
            engine = KnowledgeSearch(root, Config(mode="local"))

            async def consultant(*, text, **kwargs):
                if text == "fail":
                    raise TimeoutError("secret-never-log")
                return ConsultantReply(text="Ответ: " + text)

            checkpoints = []
            answers = asyncio.run(
                answer_questions(
                    [("1", "first"), ("2", "fail"), ("3", "last")],
                    engine,
                    consultant,
                    2,
                    checkpoint=lambda result: checkpoints.append(list(result)),
                )
            )
            self.assertEqual(answers[0], "Ответ: first")
            self.assertEqual(answers[2], "Ответ: last")
            self.assertNotIn("secret", answers[1])
            self.assertEqual(len(checkpoints), 3)
            write_submission(root / "submission.csv", [("1", "a"), ("2", "b"), ("3", "c")], answers)
            self.assertTrue((root / "submission.csv").exists())

    def test_default_archive_includes_shared_implementation(self):
        from zipfile import ZipFile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "submission.zip"
            self.assertTrue(build_submission(path))
            with ZipFile(path) as z:
                self.assertIn("consultant_impl.py", z.namelist())
                self.assertTrue(all("/" not in name for name in z.namelist()))


if __name__ == "__main__":
    unittest.main()


def test_default_consultant_connects_to_existing_session_and_lead_api(tmp_path, monkeypatch):
    import pytest
    from fastapi.testclient import TestClient
    from psycopg.conninfo import make_conninfo

    from app.config import Settings
    from app.main import create_app
    from tests.test_migrations import database

    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("Set TEST_DATABASE_URL")
    for key, value in {
        "RAG_MODE": "local",
        "LLM_BASE_URL": "http://provider.test/v1",
        "LLM_API_KEY": "test-only",
        "LLM_MODEL": "test-model",
    }.items():
        monkeypatch.setenv(key, value)
    (tmp_path / "kb.md").write_text(
        "# Учёт\nПлатформа помогает учитывать потребление по складам.\n", encoding="utf-8"
    )

    def request(client, messages):
        payload = json.loads(messages[-1]["content"])
        if isinstance(payload, list):
            return json.dumps({"facts": [payload[0]["content"]]}, ensure_ascii=False)
        return json.dumps(
            {
                "answer": "Платформа помогает учитывать потребление по складам.",
                "kind": "product",
                "state": "awaiting_contacts",
                "lead": None,
                "evidence_ids": [payload["evidence"][0]["id"]],
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(ModelClient, "_request", request)

    async def run():
        async with database() as conn:
            cursor = await conn.execute("SELECT current_schema() AS name")
            schema = (await cursor.fetchone())["name"]
            settings = Settings(
                database_url=make_conninfo(
                    os.environ["TEST_DATABASE_URL"], options=f"-c search_path={schema}"
                ),
                knowledge_base_path=tmp_path,
                api_access_token="test-access",
            )
            application = create_app(settings)
            with TestClient(application, headers={"Authorization": "Bearer test-access"}) as api:
                assert api.get("/api/health").status_code == 200
                session_id = api.post("/api/sessions").json()["id"]
                session = f"/api/sessions/{session_id}"
                assert api.get("/api/sessions").status_code == 200
                response = api.post(session + "/messages", json={"text": "Нужен учёт склада"})
                assert response.status_code == 200
                assert response.json()["assistant_message"]["debug"]["sources"]
                assert len(api.get(session).json()["messages"]) == 2
                lead = api.post(
                    session + "/lead",
                    json={
                        "full_name": "Тест Тестов",
                        "phone": "+79990000000",
                        "company": "Компания",
                    },
                )
                assert lead.status_code == 201
                assert "Нужен учёт склада" in lead.json()["summary"]
                assert api.get(session).json()["state"] == "lead_created"

    asyncio.run(run())
