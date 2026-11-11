"""Run real model questions and retain answers/evidence without recording credentials.

Example: python evaluate_answers.py --env /path/to/env --kb /path/to/knowledge-base.zip
    --questions /path/to/questions.csv --output /tmp/energytool-results.json
The optional env loader needs python-dotenv (web development dependency).
"""
import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

try:
    from rag_search import Config, KnowledgeSearch
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from rag.rag_search import Config, KnowledgeSearch

from consultant_impl import GroundingReviewer, ModelClient, ModelConfig, EnergyConsultant
from solution import read_questions, split_dialog


class EvaluationClient(ModelClient):
    """Serial diagnostic runner: same requests as production, no hidden concurrency."""
    def __init__(self, config):
        super().__init__(config)
        self.calls = 0

    async def complete(self, messages):
        self.calls += 1
        return self._request(messages)


class EvaluationSearch:
    def __init__(self, engine):
        self.engine = engine
        self.last_result = None

    async def search(self, question, history=None):
        self.last_result = self.engine.search(question, history=history)
        return self.last_result


def save(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', type=Path)
    parser.add_argument('--kb', required=True, type=Path)
    parser.add_argument('--questions', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--baseline', action='store_true', help='Disable semantic review for comparison')
    args = parser.parse_args()
    if args.env:
        from dotenv import dotenv_values
        for key, value in dotenv_values(args.env).items():
            if value is not None:
                os.environ[key] = value
    client = EvaluationClient(ModelConfig.from_env())
    questions = read_questions(args.questions)
    if args.limit > 0:
        questions = questions[:args.limit]
    report = {'model': client.config.model, 'semantic_review': not args.baseline,
              'status': 'running', 'results': []}
    save(args.output, report)
    # Fail fast on DNS/auth errors rather than consuming the whole dataset.
    try:
        client._request([{'role': 'user', 'content': 'Ответь одним словом: готов'}])
    except Exception as exc:
        report.update(status='provider_unavailable', error_type=type(exc).__name__)
        save(args.output, report)
        print('Provider unavailable; no question answers obtained.', file=sys.stderr)
        return 2
    search = EvaluationSearch(KnowledgeSearch(args.kb, Config.from_env()))
    consultant = EnergyConsultant(client, reviewer=None if args.baseline else GroundingReviewer(client))
    for qid, question in questions:
        started = time.monotonic()
        calls_before = client.calls
        row = {'id': qid, 'question': question}
        try:
            text, history = split_dialog(question)
            reply = asyncio.run(consultant(text=text, history=history, search=search))
            row.update(answer=reply.text, state=reply.state, lead=reply.lead,
                       summary=reply.lead_summary, sources=[s.model_dump() for s in reply.sources],
                       word_count=len(reply.text.split()), status='answered')
        except Exception as exc:
            row.update(status='failed', error_type=type(exc).__name__)
        row.update(seconds=round(time.monotonic()-started, 3), model_calls=client.calls-calls_before)
        if search.last_result:
            row['retrieved'] = search.last_result
        report['results'].append(row)
        save(args.output, report)
        print(f"{qid}: {row['status']}", flush=True)
    report['status'] = 'complete'
    report['answered'] = sum(r['status'] == 'answered' for r in report['results'])
    report['total'] = len(report['results'])
    save(args.output, report)
    return 0 if report['answered'] == report['total'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
