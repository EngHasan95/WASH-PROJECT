"""Check retained-data protection with a fake CLI; never touch any Docker engine."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def main():
    results = []
    for scenario in ('retained-volume', 'retained-container', 'first-start'):
        with tempfile.TemporaryDirectory(prefix='committee-guard-', dir=ROOT / '.local') as folder:
            base = Path(folder)
            stage = base / 'committee'
            stage.mkdir()
            shutil.copyfile(ROOT / 'committee/run.sh', stage / 'run.sh')
            (stage / 'compose.yaml').write_text('services: {}\n')
            (stage / 'images.tar').write_bytes(b'fictional CLI fixture archive')
            (stage / 'images.sha256').write_text(hashlib.sha256((stage / 'images.tar').read_bytes()).hexdigest() + '  images.tar\n')
            required = 'wash-fixture:1 sha256:fictional'
            (stage / 'images.ids').write_text(required + '\n')
            sentinel = base / 'retained-data'
            sentinel.write_bytes(b'Never alter retained data')
            cli = base / 'docker'
            cli.write_text('''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
args=sys.argv[1:]
if args[:1]==['--host']:args=args[2:]
with Path(os.environ['GUARD_TRACE']).open('a') as f:f.write(json.dumps(args)+'\\n')
mode=os.environ['GUARD_SCENARIO']
if args[0]=='info':print('linux')
elif args[:2]==['compose','version']:print('Mock compose')
elif args[:2]==['volume','ls']:
 if mode=='retained-volume':print('wash-committee_committee-db')
elif args[0]=='ps':
 if mode=='retained-container':print('retained-committee-container')
elif args[:2]==['image','ls']:print('wash-fixture:1 sha256:fictional')
elif args[0]=='compose':pass
else:raise SystemExit('Unexpected fake Docker command')
''')
            cli.chmod(0o700)
            env = {**os.environ, 'PATH': str(base) + os.pathsep + os.environ['PATH'],
                   'DOCKER_HOST': 'unix:///fictional-guard-engine.sock', 'WASH_COMMITTEE_NO_BROWSER': '1',
                   'GUARD_SCENARIO': scenario, 'GUARD_TRACE': str(base / 'trace.jsonl')}
            run = subprocess.run(['bash', str(stage / 'run.sh'), 'start'], env=env, capture_output=True, text=True)
            calls = [json.loads(line) for line in (base / 'trace.jsonl').read_text().splitlines()]
            assert sentinel.read_bytes() == b'Never alter retained data'
            if scenario == 'first-start':
                assert run.returncode == 0, run.stderr
                assert (stage / '.committee.env').exists()
                assert any('up' in call for call in calls)
            else:
                assert run.returncode != 0 and 'Existing committee data' in run.stderr
                assert not (stage / '.committee.env').exists()
                assert not any('up' in call or 'load' in call for call in calls)
            results.append({'scenario': scenario, 'passed': True, 'retained_data_unchanged': True})
    out = ROOT / 'test-results/committee-launcher-guard.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({'checks': results, 'powershell_execution': 'not performed'}, indent=2))
    print(json.dumps(results))


if __name__ == '__main__':
    main()
