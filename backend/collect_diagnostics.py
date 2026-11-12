"""Collect reproducible backend diagnostics without storing env files or credentials."""
import argparse
import hashlib
import importlib.util
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env',type=Path,required=True)
    parser.add_argument('--kb',type=Path,required=True)
    parser.add_argument('--questions',type=Path,required=True)
    parser.add_argument('--output-root',type=Path,required=True)
    parser.add_argument('--offline',action='store_true',help='Do not call the model')
    parser.add_argument('--answer-ids',help='Comma-separated demo IDs for a targeted answer check; dialogues still run in full')
    args=parser.parse_args()
    root=Path(__file__).resolve().parents[1]
    from dotenv import dotenv_values
    env=os.environ.copy()
    env.update({k:v for k,v in dotenv_values(args.env).items() if v is not None})
    if os.environ.get('TEST_DATABASE_URL'):
        env['TEST_DATABASE_URL']=os.environ['TEST_DATABASE_URL']
    env['PYTHONPATH']=os.pathsep.join([str(root/'backend'),str(root/'backend/tests'),str(root/'rag'),str(root)])
    env['PYTHONUNBUFFERED']='1'
    commit=subprocess.check_output(['git','rev-parse','--short','HEAD'],cwd=root,text=True).strip()
    files = list((root/'backend').glob('*.py')) + list((root/'backend/app').rglob('*.py'))
    files += list((root/'backend/tests').glob('*.py')) + list((root/'rag').glob('*.py'))
    files += list((root/'backend/app/migrations').glob('*.sql'))
    digest=hashlib.sha256()
    digest.update(('answer_ids='+(args.answer_ids or 'all')).encode())
    for file in sorted(files):
        digest.update(str(file.relative_to(root)).encode());digest.update(file.read_bytes())
    for key in ('LLM_MODEL','OPENAI_MODEL','GEN_MODEL','MAX_TOKENS','TEMPERATURE','RAG_MODE','EMB_MODEL','RERANKER_MODEL',
                'LLM_TIMEOUT','LLM_REASONING_EFFORT','TOP_K','RERANKER_TOP_N',
                'RAG_CHUNK_CHARS','RAG_OVERLAP_CHARS','RAG_CANDIDATES'):
        digest.update((key+'='+env.get(key,'')).encode())
    digest.update(args.kb.read_bytes())
    fingerprint=digest.hexdigest()[:10]
    output=args.output_root/f'full-check-{commit}-{fingerprint}' 
    output.mkdir(parents=True,exist_ok=True)
    secrets=[v for k,v in env.items() if v and any(s in k.upper() for s in ('API_KEY','ACCESS_TOKEN','PASSWORD'))]
    db=env.get('TEST_DATABASE_URL') or env.get('DATABASE_URL')
    if db:
        secrets.append(db)
        try:
            password=urlsplit(db).password
            if password:
                secrets.extend([password,unquote(password)])
        except ValueError:
            pass
    def redact(text):
        for secret in sorted(set(secrets),key=len,reverse=True):
            if len(secret)>=4:
                text=text.replace(secret,'[REDACTED]')
        return text
    manifest={'commit':commit,'fingerprint':fingerprint,'python':sys.version.split()[0],
        'model':env.get('LLM_MODEL') or env.get('OPENAI_MODEL') or env.get('GEN_MODEL'),
        'model_config':{k:env.get(k) for k in ('RAG_MODE','MAX_TOKENS','LLM_TIMEOUT','MAX_WORKERS')},
        'packages':{},'stages':[],'status':'running','test_data_only':True,
        'answer_ids':args.answer_ids or 'all'}
    for package in ('pydantic','pytest','fastapi','psycopg','httpx'):
        try:manifest['packages'][package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:manifest['packages'][package]=None
    def save_manifest():
        tmp=output/'manifest.tmp'
        tmp.write_text(redact(json.dumps(manifest,ensure_ascii=False,indent=2)),encoding='utf-8')
        tmp.replace(output/'manifest.json')
    def stage(name,command,cwd=root):
        print(f'=== {name} ===',flush=True)
        start=time.monotonic()
        run_env=env.copy()
        if name.startswith('offline') or name == 'postgres-api':
            # Tests must not inherit real provider access or accidentally call paid models.
            for key in ('LLM_MODEL','OPENAI_MODEL','GEN_MODEL','LLM_BASE_URL','OPENAI_BASE_URL',
                        'LLM_API_KEY','OPENAI_API_KEY'):
                run_env.pop(key,None)
        process=subprocess.Popen(command,cwd=cwd,env=run_env,stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace')
        with (output/f'{name}.log').open('w',encoding='utf-8') as stream:
            try:
                for line in process.stdout:
                    clean=redact(line)
                    stream.write(clean);stream.flush()
                    print(clean,end='',flush=True)
                code=process.wait()
            except KeyboardInterrupt:
                try:process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.terminate();process.wait()
                manifest['status']='interrupted'
                save_manifest()
                raise
        manifest['stages'].append({'name':name,'returncode':code,'seconds':round(time.monotonic()-start,3)})
        save_manifest()
        return code
    save_manifest()
    try:
        # Explicitly select the offline suites; no external model/DB calls.
        test_code=("import unittest; l=unittest.TestLoader(); "
            "s=unittest.TestSuite(l.loadTestsFromName(n) for n in "
            "('test_dialog_memory','test_consultant_core','test_evaluation_limits','test_diagnostic_runner')); "
            "r=unittest.TextTestRunner(verbosity=2).run(s); raise SystemExit(not r.wasSuccessful())")
        if stage('offline-core',[sys.executable,'-c',test_code]):
            manifest['status']='offline_tests_failed';save_manifest();return 1
        if stage('offline-rag',[sys.executable,'-m','unittest','discover','-s','rag/tests','-v']):
            manifest['status']='offline_tests_failed';save_manifest();return 1
        if db and all(importlib.util.find_spec(n) for n in ('pytest','psycopg','fastapi')):
            from psycopg.conninfo import make_conninfo
            env['TEST_DATABASE_URL']=make_conninfo(db,connect_timeout=5)
            # Existing fixtures create/drop only their own temporary schemas.
            import psycopg
            try:
                with psycopg.connect(env['TEST_DATABASE_URL']):
                    pass
            except psycopg.Error as exc:
                (output/'postgres-api.log').write_text(
                    f'DATABASE UNAVAILABLE ({type(exc).__name__}). Start the dedicated test PostgreSQL or set TEST_DATABASE_URL.\n',encoding='utf-8')
                manifest['stages'].append({'name':'postgres-api','status':'unavailable'})
                save_manifest()
                print('PostgreSQL unavailable; API tests have not run.',flush=True)
            else:
                stage('postgres-api',[sys.executable,'-m','pytest','-q','--tb=short'],root/'backend')
        else:
            (output/'postgres-api.log').write_text(
                'SKIPPED: TEST_DATABASE_URL/DATABASE_URL or backend dev dependencies missing.\n',encoding='utf-8')
            print('PostgreSQL/API tests skipped: check postgres-api.log for setup requirements.',flush=True)
            manifest['stages'].append({'name':'postgres-api','status':'skipped'})
            save_manifest()
        if not args.offline:
            common=['--env',str(args.env),'--kb',str(args.kb)]
            for name,script,more in (
                ('answers','evaluate_answers.py',['--questions',str(args.questions),'--trace']
                    + (['--ids',args.answer_ids] if args.answer_ids else [])),
                ('dialogs','evaluate_dialogs.py',[]),
            ):
                code=stage(name,[sys.executable,str(root/'backend'/script),*common,
                    '--output',str(output/f'{name}.json'),*more])
                if code:
                    report_path=output/f'{name}.json'
                    state=json.loads(report_path.read_text()).get('status') if report_path.exists() else 'crashed'
                    if state in {'rate_limited','interrupted','provider_unavailable','crashed'}:
                        manifest['status']=f'{name}_incomplete';save_manifest()
                        print(f'Run stopped. Resume with the same command. Reports: {output}',flush=True)
                        return code
                    # Content/format failures in independent questions do not hide dialogue diagnostics.
                    print(f'{name}: some cases failed; continuing to the next independent stage.',flush=True)
        manifest['status']='complete' if all(s.get('returncode')==0 for s in manifest['stages']) else 'partial'
        save_manifest()
        print(f'Reports ready: {output}',flush=True)
        return 0 if manifest['status']=='complete' else 1
    except KeyboardInterrupt:
        print(f'Stopped. Completed diagnostics retained: {output}',flush=True)
        return 130


if __name__=='__main__':
    raise SystemExit(main())
