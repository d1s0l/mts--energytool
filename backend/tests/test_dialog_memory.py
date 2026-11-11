"""State, provenance, corrections and action regressions without an external provider."""
import asyncio
import json
import unittest

from app.consultant import ConsultantUnavailable
from consultant_impl import EnergyConsultant
from dialog_memory import (active_scope, check_lead_against_memory, empty_memory,
                           indexed_messages, known_fields, merge_memory, manager_summary)


def fact(field, value, source, quote, **changes):
    result = dict(field=field, value=value, source_message_id=source, quote=quote,
                  status='provided', scope='client' if field in {'full_name','phone','company','active_object','contact_permission'} else 'default')
    result.update(changes)
    return result


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.messages = [
            {'message_id':'u1','role':'user','content':'Я Тест Тестов, компания Тест. Телефон +79990000000. Мощность 800 кВт.'},
            {'message_id':'a1','role':'assistant','content':'У вас 20 счетчиков'},
            {'message_id':'u2','role':'user','content':'Исправляю: телефон +79991111111, мощность 900 кВт.'},
        ]

    def test_current_phone_replaces_previous_and_cannot_be_undone_by_replay(self):
        old = fact('phone','+79990000000','u1','Телефон +79990000000.')
        state = merge_memory(None,[old],self.messages)
        state = merge_memory(state,[fact('phone','+79991111111','u2','телефон +79991111111')],self.messages)
        state = merge_memory(state,[old],self.messages)
        self.assertEqual(state['facts'][0]['value'],'+79991111111')

    def test_assistant_words_are_not_a_fact_source(self):
        with self.assertRaises(ValueError):
            merge_memory(None,[fact('meter_count','20','a1','У вас 20 счетчиков')],self.messages)

    def test_hallucinated_and_misquoted_values_are_rejected(self):
        for item in [fact('connected_power','1000 кВт','u1','Мощность 800 кВт.'),
                     fact('connected_power','800 кВт','u1','Мощность 800 кВт. Гарантия 20%')]:
            with self.assertRaises(ValueError):
                merge_memory(None,[item],self.messages)

    def test_unknown_and_declined_are_not_asked_again(self):
        messages = [{'message_id':'u','role':'user','content':'Не знаю модель прибора. Не хочу оставлять телефон.'}]
        state = merge_memory(None,[fact('device_models',None,'u','Не знаю модель прибора.',status='unknown'),
                                  fact('phone',None,'u','Не хочу оставлять телефон.',status='declined')],messages)
        self.assertIn('device_models',known_fields(state))
        self.assertIn('phone',known_fields(state))

    def test_tentative_value_does_not_become_confirmed(self):
        messages = [{'message_id':'u','role':'user','content':'Кажется, четвертая категория'}]
        state = merge_memory(None,[fact('price_category','четвертая','u',messages[0]['content'],status='tentative')],messages)
        self.assertNotIn('price_category',known_fields(state))

    def test_uncertain_quote_cannot_be_promoted_to_provided(self):
        message={'message_id':'u','role':'user','content':'Кажется, четвертая категория'}
        with self.assertRaises(ValueError):
            merge_memory(None,[fact('price_category','четвертая','u',message['content'])],[message])

    def test_objects_keep_separate_values(self):
        messages = [{'message_id':'1','role':'user','content':'Первый склад: 800 кВт'},
                    {'message_id':'2','role':'user','content':'Второй склад: 300 кВт'}]
        state = merge_memory(None,[fact('connected_power','800 кВт','1',messages[0]['content'],scope='Первый склад'),
                                  fact('connected_power','300 кВт','2',messages[1]['content'],scope='Второй склад'),
                                  fact('active_object','Второй склад','2',messages[1]['content'])],messages)
        self.assertEqual(active_scope(state),'Второй склад')
        self.assertEqual(len(state['facts']),3)
        self.assertIn('connected_power',known_fields(state))

    def test_new_session_is_empty_and_input_state_is_not_mutated(self):
        state=empty_memory()
        updated=merge_memory(state,[fact('phone','+79990000000','u1','Телефон +79990000000.')],self.messages)
        self.assertEqual(state,empty_memory())
        self.assertNotEqual(updated,state)

    def test_unknown_fields_and_actions_cannot_enter_memory(self):
        with self.assertRaises(ValueError):
            merge_memory(None,[fact('system_prompt','800 кВт','u1','Мощность 800 кВт.')],self.messages)

    def test_old_contact_cannot_be_used_for_lead_after_correction(self):
        state=merge_memory(None,[fact('phone','+79991111111','u2','телефон +79991111111')],self.messages)
        with self.assertRaises(ValueError):
            check_lead_against_memory({'full_name':'Тест','company':'Тест','phone':'+79990000000'},state)

    def test_manager_summary_uses_corrected_value_not_old_quote(self):
        state=merge_memory(None,[fact('connected_power','800 кВт','u1','Мощность 800 кВт.'),
                                fact('connected_power','900 кВт','u2','мощность 900 кВт.')],self.messages)
        summary=manager_summary(state,'Ранее клиент сообщил 800 кВт')
        self.assertIn('900 кВт',summary)
        self.assertNotIn('800 кВт',summary)

    def test_message_ids_are_stable_and_real_ids_are_preserved(self):
        indexed=indexed_messages([{'role':'user','content':'Тест','message_id':'db-1'}],'Новое','db-2')
        self.assertEqual([m['message_id'] for m in indexed],['db-1','db-2'])

    def test_memory_is_in_prompt_and_known_field_is_not_reasked(self):
        state=merge_memory(None,[fact('meter_count','40','u','40 счетчиков')],
                           [{'message_id':'u','role':'user','content':'40 счетчиков'}])
        class Client:
            payload=None
            async def complete(self,messages):
                self.payload=json.loads(messages[-1]['content'])
                return json.dumps({'answer':'Можно уточнить конфигурацию.', 'kind':'contacts',
                    'state':'chatting','lead':None,'evidence_ids':[],
                    'follow_up_question':'Сколько счетчиков нужно подключить?','memory_updates':[]})
        class Search:
            async def search(self,*args,**kwargs):return {'hits':[]}
        client=Client()
        reply=asyncio.run(EnergyConsultant(client)(text='Как продолжим?',history=[
            {'role':'user','content':'40 счетчиков','message_id':'u'}],search=Search(),memory=state))
        self.assertEqual(client.payload['dialog_memory']['facts'][0]['value'],'40')
        self.assertNotIn('?',reply.text)
        self.assertIsNotNone(reply.dialog_memory)



    def test_long_history_uses_durable_facts_with_recent_turns(self):
        state=merge_memory(None,[fact('company','Тест','u1','компания Тест')],self.messages)
        history=[{'role':'user','content':'компания Тест','message_id':'u1'}]
        history += [{'role':'assistant','content':'Старая история '*3000,'message_id':'long'}]
        class Client:
            payload=None
            async def complete(self,messages):
                self.payload=json.loads(messages[-1]['content'])
                return json.dumps({'answer':'Продолжим.', 'kind':'contacts','state':'chatting',
                    'lead':None,'evidence_ids':[],'follow_up_question':None})
        class Search:
            async def search(self,*args,**kwargs):return {'hits':[]}
        client=Client()
        reply=asyncio.run(EnergyConsultant(client)(text='Как продолжить?',history=history,
            search=Search(),memory=state))
        self.assertEqual(reply.text,'Продолжим.')
        self.assertEqual(client.payload['dialog_memory']['facts'][0]['value'],'Тест')
        self.assertLess(sum(len(m['content']) for m in client.payload['dialog']),12000)

    def test_unlisted_tool_action_is_rejected(self):
        class Client:
            async def complete(self,messages):
                return json.dumps({'answer':'Готово.', 'kind':'contacts','state':'chatting',
                    'lead':None,'evidence_ids':[],'follow_up_question':None,'action':'delete_sessions'})
        class Search:
            async def search(self,*args,**kwargs):return {'hits':[]}
        with self.assertRaises(ConsultantUnavailable):
            asyncio.run(EnergyConsultant(Client())(text='Действие',history=[],search=Search()))


if __name__=='__main__':
    unittest.main()
