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
                    raise ConsultantUnavailable(debug={"raw_response": "broken", "validation_errors": ["JSONDecodeError"]})
                return ConsultantReply(text="Проверенный ответ", raw_response='{"answer":"Проверенный ответ"}', parsed_response={"answer":"Проверенный ответ"})

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
                assert calls == ["Вопрос"]
                debug_response = client.get(path + "/debug", headers=own)
                assert debug_response.status_code == 200
                item = debug_response.json()["items"][0]
                assert item["role"] == "assistant"
                assert item["message_id"] == initial.json()["assistant_message"]["id"]
                assert item["debug"]["raw_response"] == '{"answer":"Проверенный ответ"}'
                assert item["debug"]["parsed_response"] == {"answer":"Проверенный ответ"}
                assert item["debug"]["final_response"] == "Проверенный ответ"
                assert "raw_response" not in initial.json()["assistant_message"]["debug"]
                assert "parsed_response" not in client.get(path, headers=own).json()["messages"][-1]["debug"]
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
                failed_debug = client.get(path + "/debug", headers=own).json()["items"][-1]
                assert failed_debug["role"] == "user"
                assert failed_debug["debug"]["raw_response"] == "broken"

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


def test_structured_memory_and_create_lead_action_are_committed_together(tmp_path, monkeypatch):
    import json
    from consultant_impl import EnergyConsultant
    monkeypatch.setenv('RAG_MODE', 'local')
    (tmp_path / 'kb.md').write_text('# Учёт\nПлатформа учитывает потребление.')

    class Client:
        payloads = []
        async def complete(self, messages):
            payload = json.loads(messages[-1]['content'])
            self.payloads.append(payload)
            current = payload['dialog'][-1]
            text, mid = current['content'], current['message_id']
            if 'Меня зовут' in text:
                field, value, question = 'full_name', 'Тест Тестов', 'Как называется компания?'
            elif 'Компания' in text:
                field, value, question = 'company', 'ТестКомпания', 'Какой телефон оставить для связи?'
            else:
                field, value, question = 'phone', '+79990000000', None
            updates = [{'field':field,'value':value,'quote':value,'scope':'client',
                        'status':'provided','source_message_id':mid}]
            lead = None if field != 'phone' else {
                'full_name':'Тест Тестов','company':'ТестКомпания','phone':value}
            return json.dumps(dict(answer='Спасибо, данные приняты.', kind='contacts',
                state='chatting' if lead else 'awaiting_contacts', evidence_ids=[],
                follow_up_question=question, memory_updates=updates, lead=lead,
                action='create_lead' if lead else 'none', lead_intent_quote='Оформите заявку.',
                lead_facts=['Меня зовут Тест Тестов','Компания ТестКомпания']),ensure_ascii=False)

    async def run():
        async with database() as conn:
            schema = (await (await conn.execute('SELECT current_schema() AS name')).fetchone())['name']
            settings = Settings(database_url=make_conninfo(os.environ['TEST_DATABASE_URL'],
                options=f'-c search_path={schema}'), knowledge_base_path=tmp_path, api_access_token='test-access')
            model = Client()
            with TestClient(create_app(settings,consultant=EnergyConsultant(model)),
                            headers={'Authorization':'Bearer test-access'}) as client:
                session = client.post('/api/sessions').json()['id']
                path = f'/api/sessions/{session}/messages'
                assert client.post(path,json={'text':'Меня зовут Тест Тестов'}).status_code == 200
                assert client.post(path,json={'text':'Компания ТестКомпания'}).status_code == 200
                assert any(f['field']=='full_name' for f in model.payloads[1]['dialog_memory']['facts'])
                request = {'text':'Оформите заявку. Телефон +79990000000.', 'client_message_id':str(uuid4())}
                result = client.post(path,json=request)
                assert result.status_code == 200
                assert result.json()['lead_id']
                assert 'Заявка сохранена' in result.json()['assistant_message']['text']
                assert client.post(path,json=request).json() == result.json()
                state = await (await conn.execute('SELECT dialog_memory, dialog_revision FROM sessions WHERE id=%s',
                                                 (session,))).fetchone()
                assert state['dialog_revision'] == 3
                assert {f['field'] for f in state['dialog_memory']['facts']} >= {'full_name','phone','company'}
                count = await (await conn.execute('SELECT count(*) AS n FROM leads WHERE session_id=%s',(session,))).fetchone()
                assert count['n'] == 1
                other = client.post('/api/sessions').json()['id']
                fresh = await (await conn.execute('SELECT dialog_memory FROM sessions WHERE id=%s',(other,))).fetchone()
                assert fresh['dialog_memory']['facts'] == []
    asyncio.run(run())
