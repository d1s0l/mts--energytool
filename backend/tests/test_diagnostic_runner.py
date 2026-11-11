"""No-network checks for diagnostic tracing, resume, and reset."""
import asyncio
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.consultant import ConsultantReply, ModelRateLimited
from consultant_impl import ModelConfig
from dialog_memory import empty_memory
from evaluate_answers import EvaluationClient
from evaluate_dialogs import run


class DiagnosticRunnerTests(unittest.TestCase):
    def test_trace_contains_prompt_and_output_but_not_http_credentials(self):
        client=EvaluationClient(ModelConfig('http://provider','never-record-this-key','test'),interval=0,trace=True)
        with patch.object(client,'_request',return_value='Final answer'):
            asyncio.run(client.complete([{'role':'user','content':'Test input'}]))
        trace=json.dumps(client.traces)
        self.assertIn('Test input',trace)
        self.assertIn('Final answer',trace)
        self.assertNotIn('never-record-this-key',trace)
        self.assertNotIn('Authorization',trace)

    def test_failed_turn_resumes_without_losing_memory_or_duplicating_history(self):
        calls=[]
        async def consultant(**kwargs):
            calls.append(kwargs['text'])
            if kwargs['text']=='second' and calls.count('second')==1:
                raise ModelRateLimited(60)
            return ConsultantReply(text='Answer',dialog_memory=copy.deepcopy(kwargs['memory']))
        report={'status':'running','scenarios':{}}
        client=SimpleNamespace(calls=0,traces=[])
        search=SimpleNamespace(last_result=None)
        with tempfile.TemporaryDirectory() as tmp, patch('evaluate_dialogs.SCENARIOS',{'example':['first','second']}):
            output=Path(tmp)/'report.json'
            self.assertEqual(run(report,output,consultant,client,search),1)
            self.assertEqual(report['status'],'rate_limited')
            self.assertEqual(len(report['scenarios']['example']['history']),2)
            self.assertEqual(run(report,output,consultant,client,search),0)
            self.assertEqual(len(report['scenarios']['example']['history']),4)
            self.assertEqual(calls,['first','second','second'])

    def test_explicit_new_dialog_does_not_receive_old_state(self):
        received=[]
        async def consultant(**kwargs):
            received.append((copy.deepcopy(kwargs['memory']),list(kwargs['history'])))
            state=empty_memory();state['facts']=[{'example':'old private data'}]
            return ConsultantReply(text='Answer',dialog_memory=state)
        report={'status':'running','scenarios':{}}
        with tempfile.TemporaryDirectory() as tmp, patch('evaluate_dialogs.SCENARIOS',
                {'contacts_and_correction':[str(n) for n in range(7)]}):
            run(report,Path(tmp)/'report.json',consultant,
                SimpleNamespace(calls=0,traces=[]),SimpleNamespace(last_result=None))
        self.assertEqual(received[-1],(empty_memory(),[]))


if __name__=='__main__':
    unittest.main()
