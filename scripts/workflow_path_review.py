"""Run path review against disposable portable supervisor and PostgreSQL."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
if os.name == 'nt':
    raise SystemExit('This isolated harness validates workflows on Linux, not native Windows execution.')
sys.path.insert(0, str(ROOT))
from portable_windows.build_support import copy_application, manifest
OUTPUT = ROOT / 'test-results/workflow-path-review'
OUTPUT.mkdir(parents=True, exist_ok=True)
ENV = {**os.environ, 'WASH_PORTABLE_TEST': '1', 'WASH_PORTABLE_NO_BROWSER': '1',
    'LD_LIBRARY_PATH': str(ROOT / '.local/postgres/usr/lib/x86_64-linux-gnu'), 'PYTHONTZPATH': ''}
PG = ROOT / '.local/postgres/usr/lib/postgresql/17/bin'
PYTHON = str(ROOT / '.venv/bin/python')

with tempfile.TemporaryDirectory(prefix='workflow-path-review-', dir=ROOT / '.local') as temp:
    stage = Path(temp) / 'WASH Review'
    stage.mkdir()
    copy_application(ROOT, stage)
    binaries = stage / 'runtime/postgres/bin'
    binaries.mkdir(parents=True)
    for name in ('postgres', 'pg_ctl', 'initdb', 'pg_dump', 'pg_restore', 'psql'):
        wrapper = binaries / name
        wrapper.write_text('#!/bin/sh\nexec ' + shlex.quote(str(PG / name)) + ' "$@"\n')
        wrapper.chmod(0o700)
    manifest(stage, versions={'validation': 'Linux isolated workflow review'}, provenance=[])
    launch = [PYTHON, '-I', str(stage / 'portable_windows/launcher.py')]
    output = (OUTPUT / 'supervisor.log').open('w')
    supervisor = subprocess.Popen([*launch, 'start'], env=ENV, stdout=output, stderr=output)
    try:
        for _ in range(400):
            if supervisor.poll() is not None:
                raise RuntimeError('Workflow review supervisor failed; see its isolated log.')
            live = stage / 'data/runtime.json'
            if live.exists() and (stage / 'data/last-backup.json').exists():
                port = json.loads(live.read_text())['port']
                try:
                    with urllib.request.urlopen(f'http://127.0.0.1:{port}/health/', timeout=1) as response:
                        if response.status == 200:
                            break
                except Exception:
                    pass
            time.sleep(.1)
        else:
            raise RuntimeError('Review startup timeout')
        print('Ready: isolated native PostgreSQL and portable supervisor', flush=True)
        subprocess.run(['python3', str(ROOT / 'scripts/workflow_path_browser_checks.py'), f'http://127.0.0.1:{port}',
            str(stage), str(OUTPUT)], env=ENV, check=True)
        private = json.loads((stage / 'data/settings.json').read_text())
        from urllib.parse import quote
        appenv = {**ENV, 'DJANGO_SETTINGS_MODULE': 'portable_windows.settings',
            'WASH_COMMITTEE_DEMO': '1', 'WASH_COMMITTEE_STATE': str(stage / 'data'), 'PGSSLMODE': 'disable',
            'DATABASE_URL': 'postgresql://wash:' + quote(private['password']) + '@127.0.0.1:'
                + str(private['db_port']) + '/wash_committee_windows'}
        # Inspect saved audit records independently of browser assertions.
        audit = '''import json,sys,os
from pathlib import Path
stage=Path(sys.argv[1]);sys.path[:0]=[str(stage),str(stage/'app')]
import django;django.setup()
from portal.models import Complaint,Violation,ViolationSettlement
result=json.loads(Path(sys.argv[2]).read_text())
for record in result['complaints']:
 c=Complaint.objects.get(pk=record['id']);assert c.status=='closed'
 actions=['routed','assign','start','submit']
 if c.complaint_type in ('leak','high_bill'):actions+=['return','start','submit']
 actions+=['close'];events=list(c.events.all());assert [e.action for e in events]==actions
 assert events[0].actor_id is None and all(e.actor_id for e in events[1:])
 assert events[-1].actor.role=='director' and c.owner.role=='citizen'
 assert all(a.created_at<=b.created_at for a,b in zip(events,events[1:]))
 record['saved_events']=actions;record['closed_by_role']=events[-1].actor.role
 assert Complaint.objects.filter(owner=c.owner,client_id=record['client_id']).count()==1
for record in result['violations']:
 v=Violation.objects.get(pk=record['id']);s=v.settlement;assert v.status=='closed'
 actions=list(v.events.values_list('action',flat=True))
 core=[a for a in actions if not a.startswith('document_')]
 assert core==['received','to_secretariat','to_followup','return_results','assess','pay','close']
 assert s.assessed_by.role=='finance' and s.payment_confirmed_by.role=='finance' and s.closed_by.role=='director'
 assert str(s.approved_units)=='10.50' and str(v.estimated_cubic_meters)=='99.99'
 assert s.total==s.payment_amount and str(s.payment_amount)==record['paid_amount']+'.00'
 assert Violation.objects.filter(reporter=v.reporter,client_id=record['client_id']).count()==1
 record['saved_events']=actions;record['financial_and_closure_roles_verified']=True
Path(sys.argv[2]).write_text(json.dumps(result,ensure_ascii=False,indent=2))
print('PASS: saved event ordering, actors, uniqueness, fee snapshots, finance payment and director closure')
'''
        subprocess.run([PYTHON, '-c', audit, str(stage), str(OUTPUT / 'browser-results.json')], env=appenv, check=True)
        with (OUTPUT / 'django-tests.log').open('w') as log:
            tests = subprocess.run([PYTHON, '-c',
                'import sys;sys.path[:0]=[sys.argv[1],sys.argv[1]+"/app"];from django.core.management import execute_from_command_line;execute_from_command_line(["manage.py","test","portal.test_complaints","portal.test_workflow","portal.test_registers","portal.test_violations","portal.test_settlements","portal.test_documents","--noinput"])',
                str(stage)], env=appenv, stdout=log, stderr=log)
        assert tests.returncode == 0, 'Relevant Django checks failed; see isolated django-tests.log'
        print('PASS: relevant existing Django workflow and authorization suites', flush=True)
    finally:
        if supervisor.poll() is None:
            subprocess.run([*launch, 'stop'], env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            try:
                supervisor.wait(timeout=90)
            except subprocess.TimeoutExpired:
                supervisor.terminate()
                supervisor.wait(timeout=30)
        subprocess.run([str(PG / 'pg_ctl'), '-D', str(stage / 'data/database'), '-m', 'fast', '-w', 'stop'],
            env=ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        output.close()
