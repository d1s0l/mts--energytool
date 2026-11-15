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
from app.schemas import MessageOut
from build_submission import build_submission
from consultant_impl import (
    EnergyConsultant,
    ModelClient,
    ModelConfig,
    contact_intent,
    selected_summary,
    validate_answer_support,
    validate_device_compatibility,
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


class SearchWithHits:
    def __init__(self, hits):
        self.hits = hits

    async def search(self, text, history=None):
        return {"hits": self.hits, "guardrails": [], "search_ms": 1}


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
        "follow_up_question": "Сколько приборов нужно подключить?",
    }
    data.update(changes)
    data.setdefault("answer_support", [{
        "text": data["answer"], "evidence_id": "fact", "quote": HIT["text"],
    }])
    return json.dumps(data, ensure_ascii=False)


class ConsultantTests(unittest.TestCase):
    def test_normal_message_schema_hides_raw_debug_fields(self):
        message = MessageOut.model_validate(
            {
                "id": "11111111-1111-4111-8111-111111111111",
                "role": "assistant",
                "text": "Ответ",
                "created_at": "2026-11-15T09:27:05Z",
                "debug": {
                    "total_ms": 2000,
                    "sources": [],
                    "raw_response": "raw",
                    "raw_response_attempts": ["raw"],
                    "parsed_response": {"answer": "raw"},
                    "validation_errors": [],
                    "final_response": "Ответ",
                },
            }
        )

        self.assertEqual(message.debug, {"total_ms": 2000, "sources": []})

    def test_internal_recommendation_language_is_rewritten(self):
        client = Client([output(answer="Это прямо указано в наших рекомендациях."), output()])
        reply = asyncio.run(EnergyConsultant(client)(text="Цена?", history=[], search=Search()))
        self.assertEqual(client.calls, 2)
        self.assertNotIn("рекомендациях", reply.text)
        self.assertIn("25 000", reply.text)

    def test_follow_up_is_visible_after_answer(self):
        reply = asyncio.run(
            EnergyConsultant(Client([output()]))(text="Цена учета?", history=[], search=Search())
        )
        self.assertTrue(reply.text.endswith("Сколько приборов нужно подключить?"))
        self.assertEqual(reply.text.count("?"), 1)

    def test_missing_follow_up_triggers_targeted_repair(self):
        client = Client([output(kind="clarify", follow_up_question=None), output()])
        reply = asyncio.run(EnergyConsultant(client)(text="Цена?", history=[], search=Search()))
        self.assertEqual(client.calls, 2)
        self.assertTrue(reply.text.endswith("?"))

    def test_complete_product_answer_does_not_require_question(self):
        client = Client([output(follow_up_question=None)])
        reply = asyncio.run(EnergyConsultant(client)(text="Цена?", history=[], search=Search()))
        self.assertEqual(client.calls, 1)
        self.assertNotIn("?", reply.text)

    def test_missing_or_fabricated_evidence_support_is_repaired(self):
        for support in (
            None,
            [],
            [{"text": "Да.", "evidence_id": "fact", "quote": "Неподтверждённая цитата"}],
            [{"text": "Да.", "evidence_id": "invented", "quote": HIT["text"]}],
        ):
            with self.subTest(support=support):
                client = Client([output(answer_support=support), output()])
                asyncio.run(EnergyConsultant(client)(text="Цена?", history=[], search=Search()))
                self.assertEqual(client.calls, 2)

    def test_uncited_answer_text_outside_support_is_omitted(self):
        original = output()
        bad = json.loads(original)
        bad["answer"] += " Монтаж на фасаде дороже."
        bad["follow_up_question"] = None
        client = Client([json.dumps(bad, ensure_ascii=False)])
        reply = asyncio.run(EnergyConsultant(client)(text="Цена?", history=[], search=Search()))
        self.assertEqual(client.calls, 1)
        self.assertNotIn("фасаде", reply.text)
        self.assertEqual(reply.text, bad["answer_support"][0]["text"])

    def test_product_answer_is_composed_from_ordered_support_segments(self):
        segments = [
            {
                "text": "Ориентир — 25 000 рублей в год.",
                "evidence_id": "fact",
                "quote": "Ориентир: 25 000 рублей в год.",
            },
            {
                "text": "Итог зависит от проекта.",
                "evidence_id": "fact",
                "quote": "Итог зависит от проекта.",
            },
        ]
        data = json.loads(
            output(
                answer="Неподтверждённое вступление. Итог зависит от проекта.",
                answer_support=segments,
                follow_up_question=None,
            )
        )
        data.pop("answer")
        reply = asyncio.run(
            EnergyConsultant(Client([json.dumps(data, ensure_ascii=False)]))(
                text="Сколько стоит?", history=[], search=Search()
            )
        )

        self.assertEqual(
            reply.text,
            "Ориентир — 25 000 рублей в год. Итог зависит от проекта.",
        )

    def test_product_answer_keeps_support_order_not_model_answer_order(self):
        segments = [
            {
                "text": "Итог зависит от проекта.",
                "evidence_id": "fact",
                "quote": "Итог зависит от проекта.",
            },
            {
                "text": "Ориентир — 25 000 рублей в год.",
                "evidence_id": "fact",
                "quote": "Ориентир: 25 000 рублей в год.",
            },
        ]
        reply = asyncio.run(
            EnergyConsultant(
                Client(
                    [
                        output(
                            answer="Ориентир — 25 000 рублей в год. Итог зависит от проекта.",
                            answer_support=segments,
                            follow_up_question=None,
                        )
                    ]
                )
            )(text="Сколько стоит?", history=[], search=Search())
        )

        self.assertEqual(
            reply.text,
            "Итог зависит от проекта. Ориентир — 25 000 рублей в год.",
        )

    def test_short_context_answer_uses_only_supported_segments(self):
        roof_hit = {
            "chunk_id": "roof",
            "source": "kb.md",
            "title": "Монтаж",
            "section": "Крыша",
            "text": "Перед монтажом требуется оценить состояние покрытия.",
            "score": 0.02,
        }
        data = {
            "state": "chatting",
            "kind": "product",
            "evidence_ids": ["roof"],
            "answer": "Понял, крыше 2 года. Перед монтажом требуется оценить состояние покрытия.",
            "answer_support": [
                {
                    "text": "Перед монтажом требуется оценить состояние покрытия.",
                    "evidence_id": "roof",
                    "quote": roof_hit["text"],
                }
            ],
            "follow_up_question": None,
        }
        reply = asyncio.run(
            EnergyConsultant(
                Client([json.dumps(data, ensure_ascii=False)])
            )(
                text="2 года",
                history=[{"role": "assistant", "content": "Сколько лет крыше?"}],
                search=SearchWithHits([roof_hit]),
            )
        )

        self.assertEqual(reply.text, "Перед монтажом требуется оценить состояние покрытия.")

    def test_product_follow_up_is_appended_after_composed_answer(self):
        reply = asyncio.run(
            EnergyConsultant(
                Client(
                    [
                        output(
                            answer="Неиспользуемый текст.",
                            answer_support=[
                                {
                                    "text": "Итог зависит от проекта.",
                                    "evidence_id": "fact",
                                    "quote": "Итог зависит от проекта.",
                                }
                            ],
                            follow_up_question="Какой объём потребления?",
                        )
                    ]
                )
            )(text="Сколько стоит?", history=[], search=Search())
        )

        self.assertEqual(
            reply.text,
            "Итог зависит от проекта.\n\nКакой объём потребления?",
        )

    def test_product_answer_can_use_multiple_sources(self):
        hits = [
            {
                "chunk_id": "metering",
                "source": "metering.md",
                "title": "Учёт",
                "section": "",
                "text": "Система собирает данные со счётчиков.",
                "score": 0.01,
            },
            {
                "chunk_id": "reports",
                "source": "reports.md",
                "title": "Отчёты",
                "section": "",
                "text": "Отчётность доступна в личном кабинете.",
                "score": 0.02,
            },
        ]
        data = {
            "state": "chatting",
            "kind": "product",
            "evidence_ids": ["metering", "reports"],
            "answer_support": [
                {
                    "text": "Система собирает данные со счётчиков.",
                    "evidence_id": "metering",
                    "quote": hits[0]["text"],
                },
                {
                    "text": "Отчётность доступна в личном кабинете.",
                    "evidence_id": "reports",
                    "quote": hits[1]["text"],
                },
            ],
            "follow_up_question": None,
        }
        reply = asyncio.run(
            EnergyConsultant(Client([json.dumps(data, ensure_ascii=False)]))(
                text="Какие возможности есть?", history=[], search=SearchWithHits(hits)
            )
        )

        self.assertEqual(
            reply.text,
            "Система собирает данные со счётчиков. Отчётность доступна в личном кабинете.",
        )
        self.assertEqual(
            [source.fragment_id for source in reply.sources],
            ["metering", "reports"],
        )

    def test_number_from_another_claim_cannot_support_current_claim(self):
        client = Client([
            output(answer_support=[{"text": json.loads(output())["answer"],
                                    "evidence_id": "fact", "quote": "Итог зависит от проекта."}]),
            output(),
        ])
        asyncio.run(EnergyConsultant(client)(text="Цена?", history=[], search=Search()))
        self.assertEqual(client.calls, 2)

    def test_user_object_numbers_can_be_repeated_without_kb_claim(self):
        text = "Для ваших 40 приборов итог зависит от проекта."
        client = Client([output(
            answer=text, follow_up_question=None,
            answer_support=[{"text": text, "evidence_id": "fact",
                             "quote": "Итог зависит от проекта."}],
        )])
        reply = asyncio.run(EnergyConsultant(client)(
            text="У нас 40 приборов, сколько стоит?", history=[], search=Search(),
        ))
        self.assertEqual(reply.text, text)

    def test_evidence_markdown_and_wrapping_do_not_reject_a_verbatim_quote(self):
        data = json.loads(output())
        source = {**HIT, "text": "**Ориентир: 25 000 рублей в год.**\nИтог зависит от проекта."}
        validate_answer_support(data, {"fact": source}, ["fact"], [])
        data["answer_support"][0]["quote"] = "Итог не зависит от проекта."
        with self.assertRaises(ValueError):
            validate_answer_support(data, {"fact": source}, ["fact"], [])

    def test_equipment_integration_requires_compatibility_qualification(self):
        for question in (
            "Счётчики с SIM другого оператора надо менять?",
            "Можно подключить наши старые приборы к платформе?",
        ):
            with self.subTest(question=question):
                with self.assertRaises(ValueError):
                    validate_device_compatibility(question, "Да, всё подключается.", "product")
                validate_device_compatibility(
                    question, "Возможность есть; совместимость проверим по моделям приборов.",
                    "product",
                )
        validate_device_compatibility("Год выпуска счётчика распознаётся?", "Да.", "product")

    def test_existing_question_is_not_duplicated(self):
        question = "Сколько приборов нужно подключить?"
        client = Client([output(), output(follow_up_question="Как сейчас собираете показания?")])
        reply = asyncio.run(
            EnergyConsultant(client)(
                text="Сорок счетчиков",
                history=[{"role": "assistant", "content": question}],
                search=Search(),
            )
        )
        self.assertNotIn(question, reply.text)
        self.assertTrue(reply.text.endswith("Как сейчас собираете показания?"))

    def test_user_can_request_answer_without_follow_up(self):
        reply = asyncio.run(
            EnergyConsultant(Client([output(follow_up_question=None)]))(
                text="Сколько стоит? Ответьте без вопросов.", history=[], search=Search()
            )
        )
        self.assertNotIn("?", reply.text)

    def test_real_sources_and_one_generation(self):
        raw = output()
        client = Client([raw])
        reply = asyncio.run(
            EnergyConsultant(client)(text="Сколько стоит?", history=[], search=Search())
        )
        self.assertEqual(client.calls, 1)
        self.assertEqual(reply.sources[0].fragment_id, "fact")
        self.assertIn("зависит от проекта", reply.text)
        self.assertEqual(reply.raw_response, raw)
        self.assertEqual(reply.parsed_response, json.loads(raw))
        self.assertEqual(reply.validation_errors, [])

    def test_invented_number_repaired_once(self):
        rejected = output(answer="Стоимость 999 рублей")
        accepted = output()
        client = Client([rejected, accepted])
        reply = asyncio.run(EnergyConsultant(client)(text="Цена?", history=[], search=Search()))
        self.assertEqual(client.calls, 2)
        self.assertNotIn("999", reply.text)
        self.assertEqual(reply.raw_response, accepted)
        self.assertEqual(reply.raw_response_attempts, [rejected, accepted])
        self.assertEqual(reply.parsed_response, json.loads(accepted))
        self.assertEqual(len(reply.validation_errors), 1)
        self.assertIn(
            "answer_support number is absent from its evidence quote",
            reply.validation_errors[0],
        )

    def test_invalid_json_keeps_raw_response_and_parse_error(self):
        client = Client(["not JSON", "still not JSON"])
        with self.assertRaises(ConsultantUnavailable) as raised:
            asyncio.run(
                EnergyConsultant(client)(text="Цена?", history=[], search=Search())
            )

        self.assertEqual(client.calls, 2)
        self.assertEqual(raised.exception.debug["raw_response"], "still not JSON")
        self.assertEqual(
            raised.exception.debug["raw_response_attempts"],
            ["not JSON", "still not JSON"],
        )
        self.assertIsNone(raised.exception.debug["parsed_response"])
        self.assertEqual(len(raised.exception.debug["validation_errors"]), 2)
        self.assertIn("JSONDecodeError", raised.exception.debug["validation_errors"][-1])

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
        cfg = ModelConfig(
            base_url="http://proxy",
            api_key="secret",
            model="platform-model",
            reasoning_effort="low",
        )

        def response(request, timeout):
            self.assertEqual(json.loads(request.data)["model"], "platform-model")
            self.assertEqual(json.loads(request.data)["reasoning_effort"], "low")
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
                "answer_support": [{
                    "text": "Платформа помогает учитывать потребление по складам.",
                    "evidence_id": payload["evidence"][0]["id"],
                    "quote": "Платформа помогает учитывать потребление по складам.",
                }],
                "follow_up_question": "Сколько складов нужно подключить?",
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
