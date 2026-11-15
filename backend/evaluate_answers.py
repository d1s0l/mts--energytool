"""Resumable serial model evaluation. Credentials are never recorded in reports."""
import argparse
import asyncio
import copy
import json
import math
import os
import signal
import sys
import time
from pathlib import Path

try:
    from rag_search import Config, KnowledgeSearch
except ModuleNotFoundError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from rag.rag_search import Config, KnowledgeSearch

from app.consultant import ConsultantUnavailable, ModelRateLimited
from consultant_impl import EnergyConsultant, GroundingReviewer, ModelClient, ModelConfig
from solution import read_questions, split_dialog


class EvaluationClient(ModelClient):
    def __init__(self, config, interval=15, trace=False):
        super().__init__(config)
        self.calls = 0
        self.interval = interval
        self.last_call = None
        self.outputs = []
        self.trace = trace
        self.traces = []

    async def complete(self, messages):
        # Yield before every model call, including repair/review, so cancellation is delivered.
        await asyncio.sleep(0)
        if self.last_call is not None:
            time.sleep(max(0, self.interval - (time.monotonic() - self.last_call)))
        self.last_call = time.monotonic()
        self.calls += 1
        started = time.monotonic()
        record = {"messages": copy.deepcopy(messages), "status": "started"} if self.trace else None
        cached_before=self.cache_hits
        try:
            content = self._request(messages)
            self.outputs.append(content)
            if record is not None:
                record.update(output=content, status="answered",cache_hit=self.cache_hits>cached_before)
            return content
        except Exception as exc:
            if record is not None:
                record.update(status="failed", error_type=type(exc).__name__)
            raise
        finally:
            if record is not None:
                record["seconds"] = round(time.monotonic()-started, 3)
                self.traces.append(record)


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


def evaluate(questions, consultant, client, search, report, output):
    """Preserve completed rows and stop immediately on throttling or interruption."""
    known = {r['id']: r for r in report.get('results', [])}
    report['status'] = 'running'
    failures = 0
    try:
        for qid, question in questions:
            if known.get(qid, {}).get('status') == 'answered' and known[qid].get('question') == question:
                print(f'{qid}: already answered', flush=True)
                continue
            started = time.monotonic()
            calls_before = client.calls
            output_start = len(client.outputs)
            trace_start = len(getattr(client, "traces", []))
            search.last_result = None
            row = {'id': qid, 'question': question}
            stop = False
            try:
                text, history = split_dialog(question)
                reply = asyncio.run(consultant(text=text, history=history, search=search))
                row.update(answer=reply.text, state=reply.state, lead=reply.lead,
                           summary=reply.lead_summary, dialog_memory=reply.dialog_memory, action=reply.requested_action, memory_warnings=reply.memory_warnings, sources=[s.model_dump() for s in reply.sources],
                           word_count=len(reply.text.split()), status='answered')
                failures = 0
            except ModelRateLimited as exc:
                row.update(status='rate_limited', retry_after_seconds=math.ceil(exc.retry_after))
                report.update(status='rate_limited', retry_at=time.time()+exc.retry_after)
                stop = True
            except Exception as exc:
                row.update(status='failed', error_type=type(exc).__name__)
                failures += 1
                if failures >= 2:
                    report['status'] = 'stopped_after_errors'
                    stop = True
            row.update(seconds=round(time.monotonic()-started, 3), model_calls=client.calls-calls_before,
                       model_outputs=client.outputs[output_start:])
            if getattr(client, "trace", False):
                row["request_traces"] = client.traces[trace_start:]
            if search.last_result:
                row['retrieved'] = search.last_result
            known[qid] = row
            report['results'] = list(known.values())
            save(output, report)
            print(f"{qid}: {row['status']}", flush=True)
            if stop:
                break
        else:
            report['status'] = 'complete'
    except KeyboardInterrupt:
        report['status'] = 'interrupted'
        print('Stopped by user; completed answers retained.', file=sys.stderr)
    finally:
        report['results'] = list(known.values())
        report['answered'] = sum(r.get('status') == 'answered' for r in known.values())
        report['total'] = len(questions)
        save(output, report)
    return 0 if report['status'] == 'complete' and report['answered'] == len(questions) else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', type=Path)
    parser.add_argument('--kb', required=True, type=Path)
    parser.add_argument('--questions', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--limit', type=int, default=0)
    parser.add_argument('--ids', help='Comma-separated input IDs for a targeted rerun; use a new output')
    parser.add_argument('--interval', type=float, default=15,
                        help='Minimum seconds between model calls, including review/repair')
    parser.add_argument('--restart', action='store_true')
    parser.add_argument('--trace', action='store_true', help='Record model messages and outputs; test data only')
    parser.add_argument('--baseline', action='store_true', help='Disable semantic review for comparison')
    args = parser.parse_args()
    if not math.isfinite(args.interval) or args.interval < 0:
        parser.error('--interval must be a finite nonnegative number')
    if args.env:
        from dotenv import dotenv_values
        for key, value in dotenv_values(args.env).items():
            if value is not None:
                os.environ[key] = value
    client = EvaluationClient(ModelConfig.from_env(), interval=args.interval, trace=args.trace)
    questions = read_questions(args.questions)
    if args.ids:
        chosen = {item.strip() for item in args.ids.split(',') if item.strip()}
        unknown = chosen - {qid for qid, _ in questions}
        if not chosen or unknown:
            parser.error('--ids contains unknown or empty IDs')
        questions = [(qid, question) for qid, question in questions if qid in chosen]
    if args.limit > 0:
        questions = questions[:args.limit]
    report = {'model': client.config.model, 'semantic_review': not args.baseline,
              'status': 'running', 'results': []}
    if args.output.exists() and not args.restart:
        old = json.loads(args.output.read_text(encoding='utf-8'))
        if old.get('model') != report['model'] or old.get('semantic_review') != report['semantic_review']:
            parser.error('Report belongs to another model/mode; choose a new output or --restart')
        if any(r['id'] not in {qid for qid, _ in questions} for r in old.get('results', [])):
            parser.error('Report contains other IDs; choose a new output for a targeted rerun')
        report = old
        delay = report.get('retry_at', 0) - time.time()
        if delay > 0:
            print(f'Provider cooldown: retry in {math.ceil(delay)} seconds.', file=sys.stderr)
            return 2
    report.pop('retry_at', None)
    save(args.output, report)
    # A raised KeyboardInterrupt works even while a synchronous urllib request is active.
    def interrupt(signum, frame):
        raise KeyboardInterrupt
    original_handler = signal.signal(signal.SIGINT, interrupt)
    try:
        search = EvaluationSearch(KnowledgeSearch(args.kb, Config.from_env()))
        consultant = EnergyConsultant(client, reviewer=None if args.baseline else GroundingReviewer(client))
        return evaluate(questions, consultant, client, search, report, args.output)
    except KeyboardInterrupt:
        report['status'] = 'interrupted'
        save(args.output, report)
        return 130
    except ConsultantUnavailable as exc:
        report.update(status='provider_unavailable', error_type=type(exc).__name__)
        save(args.output, report)
        return 2
    finally:
        signal.signal(signal.SIGINT, original_handler)


if __name__ == '__main__':
    raise SystemExit(main())
