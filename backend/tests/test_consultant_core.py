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
    GroundingReviewer,
    consultation_plan,
    validate_project_claims,
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
        for support in (None, [], [{"text": "Да.", "evidence_id": "fact",
                                    "quote": "Неподтверждённая цитата"}]):
            with self.subTest(support=support):
                client = Client([output(answer_support=support), output()])
                asyncio.run(EnergyConsultant(client)(text="Цена?", history=[], search=Search()))
                self.assertEqual(client.calls, 2)

    def test_uncited_additional_sentence_is_repaired(self):
        original = output()
        bad = json.loads(original)
        bad["answer"] += " Монтаж на фасаде дороже."
        client = Client([json.dumps(bad, ensure_ascii=False), original])
        reply = asyncio.run(EnergyConsultant(client)(text="Цена?", history=[], search=Search()))
        self.assertEqual(client.calls, 2)
        self.assertNotIn("фасаде", reply.text)

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
        self.assertEqual(client.calls, 1)
        self.assertTrue(reply.text.strip())

    def test_user_can_request_answer_without_follow_up(self):
        reply = asyncio.run(
            EnergyConsultant(Client([output(follow_up_question=None)]))(
                text="Сколько стоит? Ответьте без вопросов.", history=[], search=Search()
            )
        )
        self.assertNotIn("?", reply.text)

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



class GroundingReviewTests(unittest.TestCase):
    @staticmethod
    def verdict(**changes):
        result = {"supported": True, "relevant": True, "conditions_preserved": True,
                  "question_appropriate": True, "issues": []}
        result.update(changes)
        return json.dumps(result, ensure_ascii=False)

    def test_semantic_rejection_repairs_otherwise_valid_quote(self):
        # A valid citation and no invented numbers do not establish entailment.
        bad = output(answer="Стоимость фиксирована для любого проекта.")
        client = Client([bad, self.verdict(supported=False,
            issues=["Источник говорит, что итог зависит от проекта, а не фиксирован."]),
            output(), self.verdict()])
        reply = asyncio.run(EnergyConsultant(client, reviewer=GroundingReviewer(client))(
            text="Сколько стоит?", history=[], search=Search()))
        self.assertEqual(client.calls, 4)
        self.assertNotIn("фиксирована", reply.text)
        self.assertIn("зависит", reply.text)

    def test_missing_context_question_is_repaired_by_review(self):
        client = Client([output(follow_up_question=None),
            self.verdict(question_appropriate=False, issues=["Уточни количество приборов."]),
            output(), self.verdict()])
        reply = asyncio.run(EnergyConsultant(client, reviewer=GroundingReviewer(client))(
            text="Хочу подобрать учет для склада. С чего начать?", history=[], search=Search()))
        self.assertTrue(reply.text.endswith("Сколько приборов нужно подключить?"))

    def test_closed_faq_can_end_without_unnecessary_question(self):
        client = Client([output(follow_up_question=None), self.verdict()])
        reply = asyncio.run(EnergyConsultant(client, reviewer=GroundingReviewer(client))(
            text="Какая ориентировочная стоимость?", history=[], search=Search()))
        self.assertEqual(client.calls, 2)
        self.assertNotIn("?", reply.text)

    def test_rejected_answers_are_not_returned_after_failed_repair(self):
        rejected = self.verdict(relevant=False, issues=["Ответ на другую тему."])
        client = Client([output(), rejected, output(), rejected])
        with self.assertRaises(ConsultantUnavailable):
            asyncio.run(EnergyConsultant(client, reviewer=GroundingReviewer(client))(
                text="Вопрос", history=[], search=Search()))

    def test_review_must_return_booleans_not_truthy_strings(self):
        client = Client([self.verdict(supported="false")])
        with self.assertRaises(ConsultantUnavailable):
            asyncio.run(GroundingReviewer(client).check(
                json.loads(output()), "Ответ", [], {"fact": HIT}))

    def test_semantic_review_allows_equivalent_support_formatting(self):
        draft = output(answer_support=[{"text": "Ориентир: 25 000 рублей в год. Итог зависит от проекта.",
                                       "evidence_id": "fact", "quote": HIT["text"]}])
        client = Client([draft, self.verdict()])
        reply = asyncio.run(EnergyConsultant(client, reviewer=GroundingReviewer(client))(
            text="Цена?", history=[], search=Search()))
        self.assertEqual(client.calls, 2)
        self.assertIn("25 000", reply.text)

    def test_production_generation_does_not_need_duplicated_quotes(self):
        draft = json.loads(output())
        draft.pop("answer_support")
        client = Client([json.dumps(draft, ensure_ascii=False), self.verdict()])
        reply = asyncio.run(EnergyConsultant(client, reviewer=GroundingReviewer(client))(
            text="Цена?", history=[], search=Search()))
        self.assertEqual(client.calls, 2)
        self.assertIn("25 000", reply.text)

    def test_factory_enables_review_by_default(self):
        from consultant_impl import create_consultant
        with patch.dict(os.environ, {"LLM_BASE_URL": "http://provider", "LLM_API_KEY": "test-only",
                                    "LLM_MODEL": "platform-model"}, clear=True):
            consultant = create_consultant()
        self.assertIsInstance(consultant.reviewer, GroundingReviewer)



class FollowUpAndTariffRegressionTests(unittest.TestCase):
    def test_correction_succeeds_without_repeating_pending_model_question(self):
        draft=output(answer='Принято, 45 счётчиков.', kind='clarify', evidence_ids=[],
                     follow_up_question='Какие модели счётчиков стоят на складах?')
        client=Client([draft])
        history=[{'role':'user','content':'40 счётчиков'},
                 {'role':'assistant','content':'Какие модели счётчиков установлены на складах?'}]
        reply=asyncio.run(EnergyConsultant(client)(text='Исправление: 45, а не 40.',
            history=history,search=Search()))
        self.assertEqual(reply.text,'Принято, 45 счётчиков.')
        self.assertEqual(client.calls,1)

    def test_correction_allows_null_question_when_previous_slot_is_pending(self):
        draft=output(answer='Принято, 45 счётчиков.', kind='clarify', evidence_ids=[],follow_up_question=None)
        reply=asyncio.run(EnergyConsultant(Client([draft]))(text='Исправление: 45, а не 40.',
            history=[{'role':'assistant','content':'Какие модели счётчиков установлены?'}],search=Search()))
        self.assertEqual(reply.text,'Принято, 45 счётчиков.')

    def test_pending_question_for_other_object_does_not_suppress_current_question(self):
        from consultant_impl import reconcile_follow_up
        memory={'facts':[{'field':'active_object','scope':'client','status':'provided','value':'Второй склад'}],
                'asked':[{'field':'device_models','scope':'Первый склад'}]}
        data={'answer':'Уточним приборы.', 'follow_up_question':'Какие модели счётчиков установлены?'}
        self.assertFalse(reconcile_follow_up(data,[{'role':'user','content':'Второй склад'}],memory))
        self.assertIsNotNone(data['follow_up_question'])

    def test_price_category_is_not_single_or_dual_tariff(self):
        from consultant_impl import validate_tariff_claims
        with self.assertRaisesRegex(ValueError,'single/dual'):
            validate_tariff_claims('Сравним счета.', 'Какая у вас ценовая категория — двухтарифная или однотарифная?',
                                   [{'role':'user','content':'Счёт вырос'}])

    def test_tentative_price_category_requires_conditional_explanation(self):
        from consultant_impl import validate_tariff_claims
        messages=[{'role':'user','content':'Кажется, четвёртая. Что проверить?'}]
        with self.assertRaisesRegex(ValueError,'tentative'):
            validate_tariff_claims('В четвёртой категории мощность оплачивается отдельно.', None,messages)
        validate_tariff_claims('Если четвёртая категория подтвердится, проверьте плату за мощность.',None,messages)

    def test_evening_hours_alone_do_not_establish_expensive_tariff(self):
        from consultant_impl import validate_tariff_claims
        messages=[{'role':'user','content':'Стали работать вечером, счёт вырос.'}]
        with self.assertRaisesRegex(ValueError,'Evening'):
            validate_tariff_claims('Если стали работать вечером, вероятно, нагрузка перенеслась в дорогие часы.',None,messages)
        validate_tariff_claims('Если по вашему тарифу вечерние часы дороже, это может влиять на счёт.',None,messages)

    def test_reviewer_sees_only_cited_fragments(self):
        class RecordingClient:
            async def complete(self,messages):
                self.payload=json.loads(messages[-1]['content'])
                return GroundingReviewTests.verdict()
        client=RecordingClient()
        asyncio.run(GroundingReviewer(client).check(json.loads(output()),'Ответ',[],
                    {'fact':HIT,'other':{**HIT,'text':'Другой продукт'}}))
        self.assertEqual([e['id'] for e in client.payload['evidence']],['fact'])


class ConsultationPlanningTests(unittest.TestCase):
    def test_roof_age_is_asked_but_not_when_already_given(self):
        plan = consultation_plan([{"role": "user", "content": "У нас старая шиферная крыша"}])
        self.assertEqual(plan["field"], "roof_age")
        self.assertIsNone(consultation_plan([
            {"role": "user", "content": "У нас старая шиферная крыша, ей 20 лет"}]))

    def test_model_question_is_selected_for_operator_compatibility(self):
        plan = consultation_plan([{"role": "user", "content": "В счетчиках симки другого оператора. Их надо менять?"}])
        self.assertEqual(plan["field"], "device_models")

    def test_completed_slot_is_not_asked_again(self):
        messages = [{"role": "user", "content": "Симки другого оператора надо менять?"},
                    {"role": "assistant", "content": "Какие у вас модели счётчиков?"},
                    {"role": "user", "content": "Не знаю модель, подскажите, где посмотреть"}]
        self.assertIsNone(consultation_plan(messages))

    def test_night_and_evening_project_needs_daytime_data(self):
        messages = [{"role": "user", "content": "Хотим солнечную станцию, потребление в основном вечером"},
                    {"role": "user", "content": "У сетевой станции есть смысл?"}]
        plan = consultation_plan(messages)
        self.assertEqual(plan["field"], "daytime_consumption")
        with self.assertRaises(ValueError):
            validate_project_claims("Смысл есть, совпадение будет небольшим.", plan)
        validate_project_claims("Может иметь смысл, если есть потребление днём.", plan)

    def test_unsupported_workshop_design_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_project_claims("Для учёта нужно, чтобы на каждый цех стоял свой прибор учёта.", None)
        validate_project_claims("Можно видеть потребление по цехам. Схему подключения проверит специалист.", None)

    def test_tenant_plan_uses_existing_history_and_asks_meter_count(self):
        messages = [{"role": "user", "content": "Хотим учет по арендаторам"},
                    {"role": "user", "content": "Приборы есть. Часть присылает фото, часть обходит техник. С чего начать?"}]
        self.assertEqual(consultation_plan(messages)["field"], "meter_count")
        messages.append({"role": "user", "content": "Всего 40 счетчиков, с чего начать подключение?"})
        self.assertEqual(consultation_plan(messages)["field"], "device_models")

    def test_loss_data_split_is_not_inferred_from_archive_length(self):
        messages = [{"role": "user", "content": "Мы электросетевая компания, ищем потери"},
                    {"role": "user", "content": "Архив два года, суточные данные. Хватит для начала?"}]
        self.assertEqual(consultation_plan(messages)["field"], "day_night_split")

    def test_missing_follow_up_is_rendered_from_plan_without_extra_generation(self):
        client = Client([output(answer="Нужно уточнить состояние кровли.", kind="clarify",
                                follow_up_question=None)])
        reply = asyncio.run(EnergyConsultant(client)(
            text="У нас старая шиферная крыша", history=[], search=Search()))
        self.assertTrue(reply.text.endswith("Сколько лет вашей кровле?"))
        self.assertEqual(client.calls, 1)

    def test_no_question_request_takes_precedence_over_plan(self):
        client = Client([output(answer="Нужно уточнить состояние кровли.", kind="clarify",
                                follow_up_question=None)])
        reply = asyncio.run(EnergyConsultant(client)(
            text="У нас старая шиферная крыша. Ответьте без вопросов.", history=[], search=Search()))
        self.assertNotIn("?", reply.text)

    def test_power_category_question_is_not_mistaken_for_sim(self):
        self.assertIsNone(consultation_plan([{"role": "user", "content":
            "У нас максимальная мощность 800 кВт. Можно менять категорию?"}]))

    def test_closed_ocr_fact_does_not_get_generic_sales_question(self):
        self.assertIsNone(consultation_plan([
            {"role": "user", "content": "Распознается ли год производства счетчика?"}]))


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
        if isinstance(payload, dict) and "answer" in payload and "dialog" in payload:
            return GroundingReviewTests.verdict()
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
