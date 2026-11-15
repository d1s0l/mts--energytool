"""No-network regressions for rate limiting, report resume and interruption."""
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.consultant import ConsultantReply, ModelRateLimited
from consultant_impl import ModelClient, ModelConfig
from evaluate_answers import evaluate


class EvaluationLimitTests(unittest.TestCase):
    def test_429_is_not_retried_and_cooldown_blocks_next_http_call(self):
        client = ModelClient(ModelConfig('http://provider', 'test-key', 'test-model'))
        error = urllib.error.HTTPError('http://provider', 429, 'limit', {'Retry-After': '120'}, None)
        with patch('urllib.request.urlopen', side_effect=error) as request:
            with self.assertRaises(ModelRateLimited) as result:
                client._request([])
            self.assertEqual(result.exception.retry_after, 120)
            with self.assertRaises(ModelRateLimited):
                client._request([])
            self.assertEqual(request.call_count, 1)

    def test_http_date_retry_after_is_supported(self):
        client = ModelClient(ModelConfig('http://provider', 'test-key', 'test-model'))
        error = urllib.error.HTTPError('http://provider', 429, 'limit',
            {'Retry-After': 'Wed, 11 Nov 2026 00:01:00 GMT'}, None)
        with patch('urllib.request.urlopen', side_effect=error), patch('time.time', return_value=1794355200):
            with self.assertRaises(ModelRateLimited) as result:
                client._request([])
        self.assertEqual(result.exception.retry_after, 60)

    def run_evaluation(self, handler, report=None):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'report.json'
            report = report or {'results': []}
            result = evaluate([('1', 'Первый'), ('2', 'Второй'), ('3', 'Третий')], handler,
                SimpleNamespace(calls=0, outputs=[]), SimpleNamespace(last_result=None), report, output)
            return result, json.loads(output.read_text())

    def test_rate_limit_stops_whole_run_immediately(self):
        calls = []
        async def consultant(**kwargs):
            calls.append(kwargs['text'])
            if len(calls) == 1:
                return ConsultantReply(text='Первый ответ')
            raise ModelRateLimited(90)
        result, report = self.run_evaluation(consultant)
        self.assertEqual(calls, ['Первый', 'Второй'])
        self.assertEqual(report['status'], 'rate_limited')
        self.assertEqual(report['answered'], 1)
        self.assertEqual(report['results'][1]['retry_after_seconds'], 90)
        self.assertNotEqual(result, 0)

    def test_resume_does_not_regenerate_completed_answer(self):
        calls = []
        async def consultant(**kwargs):
            calls.append(kwargs['text'])
            return ConsultantReply(text='Ответ')
        result, report = self.run_evaluation(consultant, {'results': [
            {'id': '1', 'question': 'Первый', 'answer': 'Сохраненный', 'status': 'answered'}]})
        self.assertEqual(calls, ['Второй', 'Третий'])
        self.assertEqual(report['results'][0]['answer'], 'Сохраненный')
        self.assertEqual(result, 0)

    def test_keyboard_interrupt_stops_before_next_question(self):
        calls = []
        async def consultant(**kwargs):
            calls.append(kwargs['text'])
            raise KeyboardInterrupt
        result, report = self.run_evaluation(consultant)
        self.assertEqual(calls, ['Первый'])
        self.assertEqual(report['status'], 'interrupted')
        self.assertNotEqual(result, 0)


if __name__ == '__main__':
    unittest.main()
