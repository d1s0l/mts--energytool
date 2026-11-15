"""Release regressions: real packaged entry point with a mocked transport, no paid calls."""

import asyncio
import csv
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, AsyncMock
from zipfile import ZipFile

from app.consultant import ConsultantReply, ConsultantUnavailable
from build_submission import build_submission
from consultant_impl import (
    EnergyConsultant,
    GroundingReviewer,
    ModelClient,
    ModelConfig,
    MODEL_DEADLINE,
)
from dialog_memory import empty_memory, refresh_contact_phone
from evaluate_dialogs import SCENARIOS, check_turn
from verify_submission import audit_archive, validate_csv


class ReleaseTests(unittest.TestCase):
    def test_evening_only_input_does_not_justify_definite_efficiency_verdict(self):
        from consultant_impl import validate_project_claims

        plan = {"field": "daytime_consumption"}
        with self.assertRaises(ValueError):
            validate_project_claims(
                "Сетевая станция при вечерней нагрузке имеет смысл, но её эффективность ниже.", plan
            )
        validate_project_claims(
            "Смысл зависит от дневного потребления; пока нельзя подтвердить выгоду.", plan
        )

    def test_sentence_filter_does_not_bypass_unknown_daytime_load_guard(self):
        class Client:
            async def complete(self, messages):
                return json.dumps(
                    dict(
                        answer="Смысл есть. Другие детали.",
                        kind="product",
                        state="chatting",
                        evidence_ids=["fact"],
                        lead=None,
                        follow_up_question=None,
                    )
                )

        class Search:
            async def search(self, *args, **kwargs):
                return {
                    "hits": [
                        dict(
                            chunk_id="fact",
                            source="solar.md",
                            title="Станция",
                            text="Сетевая станция.",
                            score=1,
                        )
                    ]
                }

        class Reviewer:
            async def check(self, *args, **kwargs):
                return "Смысл есть."

        with self.assertRaises(ConsultantUnavailable):
            asyncio.run(
                EnergyConsultant(Client(), reviewer=Reviewer())(
                    text="У нас солнечная станция, нагрузка вечером. Имеет ли сетевая станция смысл?",
                    history=[],
                    search=Search(),
                )
            )

    def test_numeric_grounding_accepts_inflected_words_and_equivalent_units_without_losing_scale(
        self,
    ):
        from consultant_impl import numbers

        cases = [
            ("около сорока счётчиков", "40 счётчиков"),
            ("на трёх складах", "3 склада"),
            ("сорок пять тысяч", "45 000"),
            ("один миллион двести тысяч", "1 200 000"),
            ("0 тысяч", "0"),
            ("2,0 МВт", "2 МВт"),
        ]
        for words, digits in cases:
            with self.subTest(words=words):
                self.assertEqual(numbers(words), numbers(digits))
        self.assertNotIn("40", numbers("сорок тысяч"))
        self.assertNotIn("3", numbers("один два"))

    def test_unclassified_pending_question_is_not_repeated_after_contact_refusal(self):
        question = "Какой формат отчёта удобен?"

        class Client:
            async def complete(self, messages):
                return json.dumps(
                    dict(
                        answer="Продолжим.",
                        kind="clarify",
                        state="awaiting_contacts",
                        evidence_ids=[],
                        lead=None,
                        follow_up_question=question,
                    )
                )

        class Search:
            async def search(self, *args, **kwargs):
                return {"hits": []}

        consultant = EnergyConsultant(Client())
        first = asyncio.run(
            consultant(text="Обсудим учёт.", history=[], search=Search(), message_id="u1")
        )
        history = [
            dict(role="user", content="Обсудим учёт.", message_id="u1"),
            dict(role="assistant", content=first.text, message_id="a1"),
        ]
        second = asyncio.run(
            consultant(
                text="Телефон пока не готов оставлять.",
                history=history,
                memory=first.dialog_memory,
                search=Search(),
                message_id="u2",
            )
        )
        self.assertNotIn("?", second.text)
        self.assertEqual(second.state, "chatting")

    def test_review_can_only_remove_whole_sentences_from_a_confirmed_remaining_answer(self):
        good = "Платформа собирает показания."
        bad = "Она гарантирует экономию."

        class Client:
            calls = 0

            async def complete(self, messages):
                self.calls += 1
                if self.calls == 1:
                    return json.dumps(
                        dict(
                            answer=good + " " + bad,
                            kind="product",
                            state="chatting",
                            evidence_ids=["fact"],
                            lead=None,
                            follow_up_question=None,
                        )
                    )
                return json.dumps(
                    dict(
                        supported=False,
                        relevant=True,
                        conditions_preserved=True,
                        question_appropriate=True,
                        issues=["Гарантия не подтверждена"],
                        remove_sentences=[bad],
                        remaining_verdict=dict(
                            supported=True,
                            relevant=True,
                            conditions_preserved=True,
                            question_appropriate=True,
                        ),
                    )
                )

        class Search:
            async def search(self, *args, **kwargs):
                return {
                    "hits": [
                        dict(chunk_id="fact", source="kb.md", title="Учёт", text=good, score=1)
                    ]
                }

        client = Client()
        reply = asyncio.run(
            EnergyConsultant(client, reviewer=GroundingReviewer(client))(
                text="Что делает платформа?", history=[], search=Search()
            )
        )
        self.assertEqual(reply.text, good)
        self.assertEqual(client.calls, 2)

    def test_sentence_filter_rejects_partial_deletions_or_lost_conditions(self):
        good = "Ориентир не гарантирует результат."
        for remove, conditions in [
            (["не"], True),
            ([good], False),
            (["Выдуманное предложение."], True),
        ]:

            class Client:
                async def complete(self, messages):
                    return json.dumps(
                        dict(
                            supported=False,
                            relevant=True,
                            conditions_preserved=False,
                            question_appropriate=True,
                            issues=[],
                            remove_sentences=remove,
                            remaining_verdict=dict(
                                supported=True,
                                relevant=True,
                                conditions_preserved=conditions,
                                question_appropriate=True,
                            ),
                        )
                    )

            with self.subTest(remove=remove), self.assertRaises(ValueError):
                asyncio.run(
                    GroundingReviewer(Client()).check(
                        dict(kind="product", evidence_ids=[], answer=good), good, [], {}
                    )
                )

    def test_batch_waits_and_retries_rate_limited_question(self):
        from app.consultant import ModelRateLimited
        from solution import answer_questions

        calls = []

        async def consultant(**kwargs):
            calls.append(kwargs["text"])
            if len(calls) == 1:
                raise ModelRateLimited(2)
            return ConsultantReply(text="Готово.")

        clock = [10000.0]

        async def advance(delay):
            clock[0] += delay

        with (
            patch("solution.asyncio.sleep", new_callable=AsyncMock, side_effect=advance) as sleep,
            patch("solution.time.monotonic", side_effect=lambda: clock[0]),
        ):
            answers = asyncio.run(
                answer_questions(
                    [("1", "Вопрос")], object(), consultant, 1, deadline=time.monotonic() + 120
                )
            )
        self.assertEqual(answers, ["Готово."])
        self.assertEqual(len(calls), 2)
        sleep.assert_awaited_once_with(60)

    def test_batch_does_not_wait_past_global_deadline(self):
        from app.consultant import ModelRateLimited
        from solution import answer_questions

        async def consultant(**kwargs):
            raise ModelRateLimited(60)

        with patch("solution.asyncio.sleep", new_callable=AsyncMock) as sleep:
            answers = asyncio.run(
                answer_questions(
                    [("1", "Вопрос")], object(), consultant, 1, deadline=time.monotonic() + 10
                )
            )
        self.assertIn("технического сбоя", answers[0])
        sleep.assert_not_awaited()

    def test_verified_contact_action_uses_application_acknowledgement_without_llm_review(self):
        class Client:
            async def complete(self, messages):
                return json.dumps(
                    dict(
                        answer="Заявка уже отправлена.",
                        kind="contacts",
                        state="chatting",
                        evidence_ids=[],
                        lead={
                            "full_name": "Тест Тестов",
                            "company": "Альфа",
                            "phone": "+79990000000",
                        },
                        action="create_lead",
                        lead_intent_quote="Оформите заявку",
                        follow_up_question=None,
                    )
                )

        class Search:
            async def search(self, *args, **kwargs):
                return {"hits": []}

        class Reviewer:
            async def check(self, *args, **kwargs):
                raise AssertionError("Unexpected extra model call")

        reply = asyncio.run(
            EnergyConsultant(Client(), reviewer=Reviewer())(
                text="Оформите заявку: Тест Тестов, компания Альфа, телефон +79990000000.",
                history=[],
                search=Search(),
            )
        )
        self.assertEqual(reply.text, "Данные для заявки готовы.")
        self.assertEqual(reply.requested_action, "create_lead")

    def test_direct_contact_request_gets_source_based_summary_without_object_facts(self):
        class Client:
            async def complete(self, messages):
                return json.dumps(
                    dict(
                        answer="Данные готовы.",
                        kind="contacts",
                        state="chatting",
                        evidence_ids=[],
                        lead={
                            "full_name": "Тест Тестов",
                            "company": "Альфа",
                            "phone": "+79990000000",
                        },
                        action="create_lead",
                        lead_intent_quote="Оформите заявку",
                        follow_up_question=None,
                    )
                )

        class Search:
            async def search(self, *args, **kwargs):
                return {"hits": []}

        text = "Оформите заявку: Тест Тестов, компания Альфа, телефон +79990000000."
        reply = asyncio.run(EnergyConsultant(Client())(text=text, history=[], search=Search()))
        self.assertEqual(reply.requested_action, "create_lead")
        self.assertIn(text, reply.lead_summary)

    def test_explicit_request_not_to_create_lead_overrides_previous_intent(self):
        from consultant_impl import contact_intent
        from dialog_memory import refresh_contact_permission

        messages = [
            dict(role="user", message_id="1", content="Оформите заявку"),
            dict(role="user", message_id="2", content="Заявку не оформляйте."),
        ]
        memory = refresh_contact_permission(empty_memory(), messages)
        self.assertEqual(memory["facts"][0]["status"], "declined")
        with self.assertRaises(ValueError):
            contact_intent("Оформите заявку", messages)

    def test_phone_question_is_suppressed_after_explicit_contact_refusal(self):
        class Client:
            async def complete(self, messages):
                return json.dumps(
                    dict(
                        answer="Можем продолжить консультацию.",
                        kind="clarify",
                        state="chatting",
                        evidence_ids=[],
                        lead=None,
                        follow_up_question="Какой телефон оставить для связи?",
                    )
                )

        class Search:
            async def search(self, *args, **kwargs):
                return {"hits": []}

        reply = asyncio.run(
            EnergyConsultant(Client())(
                text="Телефон пока не готов оставлять.", history=[], search=Search()
            )
        )
        self.assertNotIn("?", reply.text)
        self.assertIsNone(reply.lead)

    def test_full_contact_flow_through_production_core_without_optional_summary_metadata(self):
        class Client:
            async def complete(self, messages):
                payload = json.loads(messages[1]["content"])
                current = payload["dialog"][-1]
                index = int(current["message_id"][1:])

                def fact(field, value, quote):
                    return dict(
                        field=field,
                        value=value,
                        quote=quote,
                        scope="client" if field in {"full_name", "company", "phone"} else "default",
                        status="provided",
                        source_message_id=current["message_id"],
                    )

                updates = []
                lead = None
                if index == 0:
                    updates = [
                        fact("company", "Альфа", "компания Альфа"),
                        fact("meter_count", "40", "40 счётчиков"),
                        fact("goal", "автоматизировать сбор", "Хотим автоматизировать сбор"),
                    ]
                if index == 2:
                    updates = [fact("meter_count", "45", "счётчиков 45, а не 40")]
                if index in {4, 5}:
                    lead = dict(
                        full_name="Тест Тестов",
                        company="Альфа",
                        phone="+79990000000" if index == 4 else "+79991111111",
                    )
                    if index == 4:
                        updates = [fact("full_name", "Тест Тестов", "Тест Тестов")]
                return json.dumps(
                    dict(
                        answer="Данные приняты." if index != 6 else "В новой беседе контактов нет.",
                        kind="contacts",
                        state="chatting",
                        evidence_ids=[],
                        follow_up_question=None,
                        lead=lead,
                        action="create_lead" if lead else "none",
                        memory_updates=updates,
                        lead_intent_quote="оформите заявку",
                    )
                )

        class Search:
            async def search(self, *args, **kwargs):
                return {"hits": []}

        memory = empty_memory()
        history = []
        consultant = EnergyConsultant(Client())
        for index, text in enumerate(SCENARIOS["contacts_and_correction"]):
            if index == 6:
                memory = empty_memory()
                history = []
            reply = asyncio.run(
                consultant(
                    text=text,
                    history=history,
                    search=Search(),
                    memory=memory,
                    message_id=f"u{index}",
                )
            )
            check_turn("contacts_and_correction", index, reply)
            memory = reply.dialog_memory
            if reply.requested_action == "create_lead":
                memory["lead"] = {"id": "simulated-test", "status": "saved"}
            history.extend(
                [
                    dict(role="user", content=text, message_id=f"u{index}"),
                    dict(role="assistant", content=reply.text, message_id=f"a{index}"),
                ]
            )

    def test_successful_reply_is_not_enough_when_lead_is_missing(self):
        with self.assertRaisesRegex(AssertionError, "lead_action_missing"):
            check_turn("contacts_and_correction", 4, ConsultantReply(text="Данные приняты."))

    def test_missing_model_summary_quotes_use_verified_object_facts(self):
        class Client:
            async def complete(self, messages):
                source = json.loads(messages[-1]["content"])["dialog"][-1]["message_id"]
                return json.dumps(
                    dict(
                        answer="Данные готовы.",
                        kind="contacts",
                        state="chatting",
                        evidence_ids=[],
                        follow_up_question=None,
                        action="create_lead",
                        lead={
                            "full_name": "Тест Тестов",
                            "company": "Альфа",
                            "phone": "+79990000000",
                        },
                        lead_intent_quote="Оформите заявку",
                        memory_updates=[
                            dict(
                                field="goal",
                                scope="default",
                                status="provided",
                                value="Нужен учёт",
                                quote="Нужен учёт",
                                source_message_id=source,
                            )
                        ],
                    )
                )

        class Search:
            async def search(self, *args, **kwargs):
                return {"hits": []}

        reply = asyncio.run(
            EnergyConsultant(Client())(
                text="Нужен учёт. Оформите заявку: Тест Тестов, компания Альфа, телефон +79990000000.",
                history=[],
                search=Search(),
            )
        )
        self.assertEqual(reply.requested_action, "create_lead")
        self.assertIn("Нужен учёт", reply.lead_summary)

    def test_flat_dialog_phone_correction_is_saved_without_old_memory(self):
        state = refresh_contact_phone(
            empty_memory(),
            [
                dict(
                    role="user",
                    message_id="u",
                    content="Исправление: телефон для заявки +79991111111, замените прежний номер.",
                )
            ],
        )
        self.assertEqual(state["facts"][0]["value"], "+79991111111")

    def test_expired_turn_never_starts_another_http_attempt(self):
        client = ModelClient(ModelConfig("http://provider", "dummy", "test"))
        token = MODEL_DEADLINE.set(time.monotonic() - 1)
        try:
            with patch("urllib.request.urlopen") as call:
                with self.assertRaises(ConsultantUnavailable):
                    client._request([])
                call.assert_not_called()
        finally:
            MODEL_DEADLINE.reset(token)

    def test_request_timeout_is_capped_by_shared_turn_budget(self):
        client = ModelClient(ModelConfig("http://provider", "dummy", "test", timeout=45))
        token = MODEL_DEADLINE.set(time.monotonic() + 2)

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self, *args):
                return b'{"choices":[{"message":{"content":"ok"},"finish_reason":"stop"}]}'

        try:
            with patch("urllib.request.urlopen", return_value=Response()) as call:
                self.assertEqual(client._request([]), "ok")
                self.assertGreater(call.call_args.kwargs["timeout"], 0)
                self.assertLessEqual(call.call_args.kwargs["timeout"], 2)
        finally:
            MODEL_DEADLINE.reset(token)

    def test_submission_validator_rejects_fallback_even_with_valid_csv(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / "submission.csv"
            p.write_text("id,answer\n1,Временный технический сбой. Повторите вопрос.\n")
            with self.assertRaisesRegex(ValueError, "fallback"):
                validate_csv(p, [("1", "Вопрос")])

    def test_default_zip_runs_entire_entry_point_and_serializes_lead(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            archive = root / "submit.zip"
            build_submission(archive)
            self.assertEqual(audit_archive(archive)["status"], "passed")
            standalone = root / "standalone"
            with ZipFile(archive) as z:
                z.extractall(standalone)
            data = standalone / "data"
            data.mkdir()
            with (data / "questions.csv").open("w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                w.writerow(["id", "question"])
                w.writerows(
                    [
                        (
                            "one",
                            "Нужен учёт. Оформите заявку: Тест Тестов, компания Альфа, телефон +79990000000.",
                        ),
                        ("two", "Какое имя я сообщал?"),
                    ]
                )
            with ZipFile(data / "knowledge-base.zip", "w") as z:
                z.writestr("kb.md", "# Учёт\nПлатформа учитывает потребление.")
            bootstrap = r"""
import asyncio, json
from unittest.mock import patch
class Response:
    def __init__(self, content): self.content=content
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def read(self,*args):
        return json.dumps({'choices':[{'message':{'content':self.content},'finish_reason':'stop'}]}).encode()
def request(req, **kwargs):
    payload=json.loads(req.data)
    assert payload['model']=='platform-provided-model'
    messages=payload['messages']
    if 'Ты проверяешь ответ' in messages[0]['content']:
        return Response(json.dumps(dict(supported=True,relevant=True,conditions_preserved=True,question_appropriate=True,issues=[])))
    data=json.loads(messages[1]['content']);current=data['dialog'][-1]
    assert len(data['dialog'])==1
    lead=None;updates=[];answer='Вы ещё не сообщали имя.'
    if 'Оформите заявку' in current['content']:
        lead=dict(full_name='Тест Тестов',phone='+79990000000',company='Альфа')
        updates=[dict(field='goal',scope='default',status='provided',value='Нужен учёт',quote='Нужен учёт',source_message_id=current['message_id'])]
        answer='Данные готовы для заявки.'
    else:
        assert not any(f['field'] in {'full_name','company','phone'} for f in data['dialog_memory']['facts'])
    return Response(json.dumps(dict(answer=answer,kind='contacts',state='chatting',evidence_ids=[],
        follow_up_question=None,lead=lead,action='create_lead' if lead else 'none',memory_updates=updates,
        lead_intent_quote='Оформите заявку')))
async def direct(func,*args,**kwargs): return func(*args,**kwargs)
with patch('urllib.request.urlopen',request), patch('asyncio.to_thread',direct):
    import solution
    raise SystemExit(solution.main())
"""
            env = os.environ.copy()
            env.pop("PYTHONPATH", None)
            env.update(
                LLM_BASE_URL="http://transport-mock",
                LLM_API_KEY="dummy-test-key",
                LLM_MODEL="platform-provided-model",
                RAG_MODE="local",
                MAX_WORKERS="1",
                DATA_DIR="data",
                LLM_MIN_INTERVAL="0",
            )
            run = subprocess.run(
                [sys.executable, "-c", bootstrap],
                cwd=standalone,
                env=env,
                capture_output=True,
                text=True,
                timeout=15,
            )
            self.assertEqual(run.returncode, 0, run.stderr)
            rows = validate_csv(standalone / "submission.csv", [("one", ""), ("two", "")])
            self.assertIn("Заявка для менеджера", rows[0]["answer"])
            self.assertIn("+79990000000", rows[0]["answer"])
            self.assertNotIn("Заявка для менеджера", rows[1]["answer"])
            self.assertNotIn("Тест Тестов", rows[1]["answer"])


if __name__ == "__main__":
    unittest.main()
