"""Live multi-turn diagnostics. Lead actions are simulated, not written to a CRM/DB."""
import argparse
import asyncio
import copy
import json
import math
import os
import signal
import time
from pathlib import Path

from app.consultant import ModelRateLimited
from consultant_impl import EnergyConsultant, GroundingReviewer, ModelConfig
from dialog_memory import empty_memory
from evaluate_answers import EvaluationClient, EvaluationSearch, Config, KnowledgeSearch, save

SCENARIOS = {
    'contacts_and_correction': [
        'Мы тестовая компания Альфа: три склада, всего 40 счётчиков. Показания снимают вручную. Хотим автоматизировать сбор.',
        'Я уже сообщил количество складов и счётчиков. Что ещё вам нужно для оценки?',
        'Исправление: счётчиков 45, а не 40.',
        'Телефон пока не готов оставлять. Объясните, с чего начать без звонка.',
        'Передумал, оформите заявку: Тест Тестов, компания Альфа, телефон +79990000000.',
        'В заявке ошибочный телефон. Замените на +79991111111.',
        'Какое имя, компанию и телефон я сообщал?',
    ],
    'separate_objects': [
        'Первый склад в Москве: присоединённая мощность 800 кВт, около 100000 кВт·ч в месяц. Хотим проверить возможность выхода на оптовый рынок.',
        'Второй склад в Туле: 300 кВт, 50000 кВт·ч в месяц. Это отдельная площадка, с первым складом электрически не связана.',
        'Вернёмся к первому складу: какая у него мощность и какие данные вы уже знаете?',
        'Какую мощность я ранее назвал для первого склада?',
    ],
}


def run(report, output, consultant, client, search):
    for name, turns in SCENARIOS.items():
        scenario = report.setdefault('scenarios', {}).setdefault(name, {'turns': [], 'history': [], 'memory': empty_memory()})
        for index, text in enumerate(turns):
            if index < len(scenario['turns']) and scenario['turns'][index]['status'] == 'answered':
                continue
            # Exercise the explicit reset separately from ordinary topic changes.
            if (name == 'separate_objects' and index == 3) or (name == 'contacts_and_correction' and index == 6):
                scenario['history'] = []
                scenario['memory'] = empty_memory()
            before = copy.deepcopy(scenario['memory'])
            start_calls, start_traces = client.calls, len(client.traces)
            start = time.monotonic()
            row = {'index': index, 'user': text, 'memory_before': before}
            search.last_result = None
            try:
                mid = f'{name}:user:{index}'
                reply = asyncio.run(consultant(text=text, history=scenario['history'], search=search,
                                              memory=before, message_id=mid))
                scenario['memory'] = reply.dialog_memory or before
                # Explicitly simulated action receipt; actual SQL is tested by backend pytest.
                if reply.requested_action == 'create_lead':
                    scenario['memory']['lead'] = {'id': f'diagnostic:{name}', 'status': 'saved'}
                scenario['history'].extend([
                    {'role': 'user', 'content': text, 'message_id': mid},
                    {'role': 'assistant', 'content': reply.text, 'message_id': f'{name}:assistant:{index}'},
                ])
                row.update(status='answered', answer=reply.text, memory_after=scenario['memory'],
                           action=reply.requested_action, memory_warnings=reply.memory_warnings, lead=reply.lead, summary=reply.lead_summary,
                           sources=[s.model_dump() for s in reply.sources])
            except ModelRateLimited as exc:
                row.update(status='rate_limited', retry_after_seconds=math.ceil(exc.retry_after))
                report.update(status='rate_limited', retry_at=time.time()+exc.retry_after)
            except Exception as exc:
                row.update(status='failed', error_type=type(exc).__name__)
                report['status'] = 'stopped_after_error'
            row.update(model_calls=client.calls-start_calls, seconds=round(time.monotonic()-start, 3),
                       request_traces=client.traces[start_traces:], retrieved=search.last_result)
            scenario['turns'] = scenario['turns'][:index] + [row]
            save(output, report)
            print(f'{name}/{index+1}: {row["status"]}', flush=True)
            if row['status'] != 'answered':
                return 1
    report['status'] = 'complete'
    save(output, report)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', required=True, type=Path)
    parser.add_argument('--kb', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    from dotenv import dotenv_values
    for key,value in dotenv_values(args.env).items():
        if value is not None:
            os.environ[key] = value
    client = EvaluationClient(ModelConfig.from_env(), interval=15, trace=True)
    report = {'status': 'running', 'model': client.config.model, 'actions_simulated': True, 'scenarios': {}}
    if args.output.exists():
        report = json.loads(args.output.read_text())
        delay = report.get('retry_at',0)-time.time()
        if delay > 0:
            print(f'Provider cooldown: {math.ceil(delay)} seconds.')
            return 2
    def interrupt(signum,frame):
        raise KeyboardInterrupt
    previous = signal.signal(signal.SIGINT,interrupt)
    try:
        report.update(status='running')
        report.pop('retry_at',None)
        search = EvaluationSearch(KnowledgeSearch(args.kb, Config.from_env()))
        return run(report,args.output,EnergyConsultant(client,reviewer=GroundingReviewer(client)),client,search)
    except KeyboardInterrupt:
        report['status'] = 'interrupted'
        save(args.output,report)
        return 130
    finally:
        signal.signal(signal.SIGINT,previous)


if __name__ == '__main__':
    raise SystemExit(main())
