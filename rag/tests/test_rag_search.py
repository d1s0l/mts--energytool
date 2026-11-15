import concurrent.futures
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch

from rag_search import Config, KnowledgeSearch


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'solar.md').write_text('# Солнце\n\n## Установка\nПанели ставят на восточный и западный скаты.\n\n## Безопасность\nСетевая станция отключается вместе с сетью.\n', encoding='utf-8')
        (self.root / 'meter.md').write_text('# Учёт\n\n## Данные\nПлатформа показывает потребление по цехам и арендаторам.\n', encoding='utf-8')
        self.engine = KnowledgeSearch(self.root, Config())

    def tearDown(self):
        self.tmp.cleanup()

    def test_factual_fragment_and_source(self):
        hit = self.engine.search('Панели на восточный скат')['hits'][0]
        self.assertEqual(hit['source'], 'solar.md')
        self.assertIn('западный', hit['text'])

    def test_standalone_run_emits_index_and_search_metrics_to_stderr(self):
        script = (
            "from rag_search import Config, KnowledgeSearch\n"
            "engine = KnowledgeSearch(__import__('sys').argv[1], Config())\n"
            "engine.search('панели')\n"
        )
        result = subprocess.run(
            [sys.executable, '-c', script, str(self.root)],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True, text=True, check=True,
        )
        self.assertEqual(result.stdout, '')
        self.assertIn('rag_index_ready documents=2', result.stderr)
        self.assertIn('chunks=', result.stderr)
        self.assertIn('total_ms=', result.stderr)
        self.assertIn('rag_search_complete', result.stderr)
        self.assertIn('hits=', result.stderr)
        self.assertNotIn('Панели ставят', result.stderr)

    def test_source_lines_are_exact(self):
        for c in self.engine.chunks:
            lines = (self.root/c.source).read_text().splitlines(keepends=True)
            self.assertEqual(''.join(lines[c.line_start-1:c.line_end]).strip(), c.text)

    def test_zip_folder_equivalence(self):
        archive = self.root/'kb.zip'
        with zipfile.ZipFile(archive,'w') as z:
            for p in self.root.glob('*.md'):
                z.write(p,p.name)
        other = KnowledgeSearch(archive,Config())
        self.assertEqual(self.engine.chunks, other.chunks)

    def test_zip_traversal_rejected(self):
        archive = self.root/'bad.zip'
        with zipfile.ZipFile(archive,'w') as z:
            z.writestr('../escape.md','secret')
        with self.assertRaises(ValueError):
            KnowledgeSearch(archive,Config())
        self.assertFalse((self.root.parent/'escape.md').exists())

    def test_byte_limit(self):
        with self.assertRaises(ValueError):
            KnowledgeSearch(self.root,Config(max_input_bytes=2))

    def test_missing_and_invalid_archive_are_distinguished(self):
        missing = self.root/'missing.zip'
        with self.assertLogs('rag_search', level='ERROR') as logs:
            with self.assertRaisesRegex(FileNotFoundError, 'knowledge base not found'):
                KnowledgeSearch(missing, Config())
        self.assertIn('reason=knowledge_base_not_found', '\n'.join(logs.output))

        invalid = self.root/'invalid.zip'
        invalid.write_text('not a zip', encoding='utf-8')
        with self.assertLogs('rag_search', level='ERROR') as logs:
            with self.assertRaisesRegex(ValueError, 'not a readable ZIP'):
                KnowledgeSearch(invalid, Config())
        self.assertIn('reason=invalid_zip', '\n'.join(logs.output))

    def test_document_read_error_names_source_without_content(self):
        broken = self.root/'broken.md'
        broken.write_bytes(b'\xff\xfe\xfa')
        with self.assertLogs('rag_search', level='ERROR') as logs:
            with self.assertRaisesRegex(ValueError, 'broken.md'):
                KnowledgeSearch(self.root, Config())
        joined = '\n'.join(logs.output)
        self.assertIn('source=broken.md', joined)
        self.assertIn('reason=invalid_utf8', joined)

    def test_empty_base_and_zero_chunks_fail_at_startup(self):
        empty = self.root/'empty'
        empty.mkdir()
        (empty/'blank.md').write_text('  \n', encoding='utf-8')
        with self.assertLogs('rag_search', level='ERROR') as logs:
            with self.assertRaisesRegex(ValueError, 'no nonempty'):
                KnowledgeSearch(empty, Config())
        self.assertIn('reason=no_nonempty_documents', '\n'.join(logs.output))

        headings = self.root/'headings'
        headings.mkdir()
        (headings/'only-headings.md').write_text('# Раздел\n\n---\n', encoding='utf-8')
        with self.assertLogs('rag_search', level='ERROR') as logs:
            with self.assertRaisesRegex(ValueError, 'no searchable text fragments'):
                KnowledgeSearch(headings, Config())
        self.assertIn('reason=no_searchable_chunks', '\n'.join(logs.output))

    def test_empty_and_unknown_query(self):
        for q in ['', '   ', 'zxqvblorp']:
            result = self.engine.search(q)
            self.assertEqual(result['hits'], [])
            self.assertTrue(result['needs_review'])

    def test_invalid_configuration(self):
        for args in [{'top_k':0},{'mode':'oops'},{'timeout':0},{'overlap_chars':2000}]:
            with self.assertRaises(ValueError):
                Config(**args)
        with self.assertRaises(ValueError):
            self.engine.search('тест',0)

    def test_parallel_queries_are_isolated(self):
        questions = ['Панели восточный скат','потребление арендаторов']*10
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(self.engine.search,questions))
        self.assertEqual([r['query'] for r in results],questions)
        self.assertTrue(all(r['hits'] for r in results))

    def test_dialog_and_serialization(self):
        r = self.engine.search('А по цехам?',history=[{'role':'user','content':'Нужен учёт арендаторов'}])
        self.assertEqual(r['hits'][0]['source'],'meter.md')
        json.dumps(r,ensure_ascii=False)
        # No session state is retained.
        self.assertEqual(self.engine.search('zxqvblorp')['hits'],[])

    def test_all_body_lines_covered(self):
        text = '# Тема\n\n' + '\n\n'.join('Строка %d: '%i+'данные '*30 for i in range(40))
        (self.root/'long.md').write_text(text,encoding='utf-8')
        engine = KnowledgeSearch(self.root,Config(chunk_chars=500,overlap_chars=100))
        covered = set()
        for c in engine.chunks:
            if c.source=='long.md':
                covered.update(range(c.line_start,c.line_end+1))
        lines=text.splitlines()
        for i,line in enumerate(lines,1):
            if line.strip() and not line.startswith('#'):
                self.assertIn(i,covered)

    def test_embeddings_reordered_and_reranker(self):
        calls=[]
        def api(engine,endpoint,body):
            calls.append((endpoint,body))
            if endpoint=='/embeddings':
                return {'data':[{'index':i,'embedding':[1,i+1]} for i in reversed(range(len(body['input'])))]}
            return {'results':[{'index':i} for i in reversed(range(len(body['documents'])))]}
        cfg=Config(mode='hybrid',base_url='http://localhost',api_key='test',reranker_model='test-rerank')
        with patch.object(KnowledgeSearch,'_post',api):
            r=KnowledgeSearch(self.root,cfg)
            result=r.search('панели')
            self.assertEqual(result['mode'],'hybrid')
            self.assertTrue(result['reranked'])
            self.assertEqual(result['warnings'],[])
            self.assertEqual(calls[0][1]['model'],'BAAI/bge-m3')

    def test_api_failure_falls_back_without_secrets(self):
        cfg=Config(mode='hybrid',base_url='http://localhost',api_key='secret-never-print')
        with self.assertLogs('rag_search',level='WARNING') as logs, \
             patch.object(KnowledgeSearch,'_post',side_effect=TimeoutError('secret-never-print')):
            engine=KnowledgeSearch(self.root,cfg)
            r=engine.search('панели')
        self.assertTrue(r['hits'])
        self.assertEqual(r['mode'],'local')
        self.assertTrue(r['warnings'])
        self.assertIn('fallback_local:embedding_index_unavailable',r['warnings'])
        diagnostics=json.dumps({'result':r,'info':engine.info,'logs':logs.output})
        self.assertNotIn('secret-never-print',diagnostics)
        self.assertIn('fallback=local',diagnostics)

    def test_missing_embedding_configuration_reports_local_fallback(self):
        with self.assertLogs('rag_search',level='WARNING') as logs:
            engine=KnowledgeSearch(self.root,Config(mode='auto'))
        self.assertEqual(engine.info['effective_mode'],'local')
        self.assertIn('embedding_credentials_missing',engine.info['warnings'])
        self.assertIn('fallback_local:embedding_credentials_missing',engine.info['warnings'])
        self.assertIn('missing=base_url,api_key', '\n'.join(logs.output))

    def test_local_fallback_does_not_fill_results_with_zero_similarity(self):
        isolated=self.root/'isolated'
        isolated.mkdir()
        (isolated/'solar.md').write_text(
            '# Solar\nПанели устанавливают на крыше.\n',encoding='utf-8',
        )
        (isolated/'unrelated.md').write_text(
            '# HR\nОтпуск сотрудников оформляется заявлением.\n',encoding='utf-8',
        )
        (isolated/'other.md').write_text(
            '# Food\nРецепт супа содержит морковь.\n',encoding='utf-8',
        )
        result=KnowledgeSearch(isolated,Config()).search('солнечные панели',top_k=6)
        self.assertEqual(result['sources'],['solar.md'])
        self.assertEqual(len(result['hits']),1)

    def test_malformed_query_embeddings_fall_back(self):
        cfg=Config(mode='hybrid',base_url='http://localhost',api_key='test')
        def api(engine,endpoint,body):
            if len(body['input'])==1:
                return {'data':[{'index':0,'embedding':[1,2,3]}]}
            return {'data':[{'index':i,'embedding':[1,2]} for i in range(len(body['input']))]}
        with patch.object(KnowledgeSearch,'_post',api):
            engine=KnowledgeSearch(self.root,cfg)
            result=engine.search('панели')
        self.assertEqual(result['mode'],'local')
        self.assertIn('query_embedding_unavailable:ValueError',result['warnings'])

    def test_401_is_not_retried(self):
        error=urllib.error.HTTPError('http://localhost/embeddings',401,'unauthorized',{},None)
        r=KnowledgeSearch(self.root,Config(mode='local',base_url='http://localhost',api_key='test'))
        with patch('urllib.request.urlopen',side_effect=error) as request:
            with self.assertRaises(urllib.error.HTTPError):
                r._post('/embeddings',{'input':['test']})
        self.assertEqual(request.call_count,1)

    def test_429_retry_is_bounded(self):
        error=urllib.error.HTTPError('http://localhost/embeddings',429,'busy',{},None)
        r=KnowledgeSearch(self.root,Config(mode='local',base_url='http://localhost',api_key='test',retries=1))
        with patch('urllib.request.urlopen',side_effect=error) as request, patch('time.sleep'):
            with self.assertRaises(urllib.error.HTTPError):
                r._post('/embeddings',{'input':['test']})
        self.assertEqual(request.call_count,2)

    def test_bad_reranker_preserves_candidates(self):
        cfg=Config(mode='auto',base_url='http://localhost',api_key='test',reranker_model='test')
        def api(engine,endpoint,body):
            if endpoint=='/embeddings':
                raise TimeoutError()
            return {'results':[{'index':0},{'index':0}]}
        with self.assertLogs('rag_search',level='WARNING') as logs, \
             patch.object(KnowledgeSearch,'_post',api):
            result=KnowledgeSearch(self.root,cfg).search('панели')
        self.assertTrue(result['hits'])
        self.assertFalse(result['reranked'])
        self.assertIn('reranker_unavailable:ValueError',result['warnings'])
        self.assertIn('fallback_candidates:reranker_unavailable',result['warnings'])
        self.assertIn('fallback=candidate_order','\n'.join(logs.output))

    def test_config_repr_does_not_expose_key(self):
        self.assertNotIn('hidden-secret',repr(Config(api_key='hidden-secret')))

    def test_stage_timings_are_json_and_total_matches(self):
        for query in ['панели', '']:
            result=self.engine.search(query)
            self.assertEqual(result['search_ms'],result['timing_ms']['total_ms'])
            self.assertTrue(all(v >= 0 for v in result['timing_ms'].values()))
            self.assertEqual(result['timing_ms']['embeddings_ms'],0)
            self.assertEqual(result['timing_ms']['reranker_ms'],0)

    def test_build_metrics_and_ready_log_include_counts(self):
        with self.assertLogs('rag_search',level='INFO') as logs:
            engine=KnowledgeSearch(self.root,Config())
        self.assertEqual(engine.info['documents'],2)
        self.assertGreater(engine.info['chunks'],0)
        self.assertGreater(engine.info['input_bytes'],0)
        self.assertEqual(engine.info['build_ms'],engine.info['timing_ms']['total_ms'])
        self.assertEqual(
            set(engine.info['timing_ms']),
            {'load_ms','chunk_ms','local_index_ms','embeddings_ms','total_ms'},
        )
        joined='\n'.join(logs.output)
        self.assertIn('rag_index_ready',joined)
        self.assertIn('documents=2',joined)
        self.assertIn('chunks=',joined)

    def test_domain_prior_does_not_require_original_filenames(self):
        (self.root/'renamed-01.md').write_text('# Солнечные электростанции\n\n## Фасад\nВозможен монтаж на вертикальные поверхности здания.\n',encoding='utf-8')
        engine=KnowledgeSearch(self.root,Config())
        self.assertIn('renamed-01.md',engine.search('солнечные панели на фасад')['sources'])

    def test_synonym_groups_merge_instead_of_overwriting(self):
        (self.root/'technical.txt').write_text('Измерительный прибор учитывает расход электричества.',encoding='utf-8')
        engine=KnowledgeSearch(self.root,Config())
        self.assertIn('technical.txt',engine.search('электросчетчик')['sources'])


if __name__=='__main__':
    unittest.main()
