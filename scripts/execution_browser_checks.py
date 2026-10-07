"""Browser proof of local-first work results on a disposable fictional database.

No development/demo records are touched. Linux browser evidence, not native Windows.
"""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import uuid
import urllib.request

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from portable_windows.build_support import copy_application

PYTHON = str(ROOT / '.venv/bin/python')
OUTPUT = ROOT / 'test-results/execution-offline-validation'
OUTPUT.mkdir(parents=True, exist_ok=True)
DB_NAME = 'wash_committee_execution_' + uuid.uuid4().hex
SOCKET = str(ROOT / '.local/pgsocket')

def db_action(command):
    script = '''import psycopg, sys
from psycopg import sql
with psycopg.connect(dbname='postgres', user='agent', host=sys.argv[1], port=55432, autocommit=True) as conn:
    if sys.argv[3] == 'drop':
        conn.execute('SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=%s AND pid<>pg_backend_pid()', [sys.argv[2]])
    conn.execute(sql.SQL(('DROP' if sys.argv[3]=='drop' else 'CREATE') + ' DATABASE {}').format(sql.Identifier(sys.argv[2])))
'''
    subprocess.run([PYTHON, '-c', script, SOCKET, DB_NAME, command], check=True, stdout=subprocess.DEVNULL)


def wait(page, expression, arg=None, timeout=30000):
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        if page.evaluate(expression, arg):
            return
        page.wait_for_timeout(100)
    page.screenshot(path=str(OUTPUT / 'failure.png'), full_page=True)
    (OUTPUT / 'failure.txt').write_text(page.locator('body').inner_text())
    raise AssertionError(expression)


def main():
    temp = tempfile.TemporaryDirectory(prefix='execution browser ', dir=ROOT / '.local')
    stage = Path(temp.name) / 'stage'
    stage.mkdir()
    copy_application(ROOT, stage)
    env = os.environ.copy()
    env.update(DJANGO_SETTINGS_MODULE='config.committee', WASH_COMMITTEE_DEMO='1', WASH_COMMITTEE_STATE=str(stage / 'data'),
        DATABASE_URL='postgresql://agent@' + SOCKET.replace('/', '%2F') + ':55432/' + DB_NAME, PGSSLMODE='disable')
    process = None
    db_created = False
    output = (OUTPUT / 'server.log').open('w')
    results = []
    try:
        db_action('create'); db_created = True
        for command in ('migrate', 'seed_committee', 'collectstatic'):
            subprocess.run([PYTHON, str(stage / 'app/manage.py'), command, '--noinput'] if command != 'seed_committee'
                else [PYTHON, str(stage / 'app/manage.py'), command], cwd=stage / 'app', env=env, check=True, stdout=output, stderr=output)
        with socket.socket() as s:
            s.bind(('127.0.0.1', 0)); port = s.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        process = subprocess.Popen([PYTHON, '-c', f'from waitress import serve; from config.wsgi import application; serve(application, host="127.0.0.1", port={port}, threads=4)'], cwd=stage / 'app', env=env, stdout=output, stderr=output)
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError('Server did not start; see server.log')
            try:
                if urllib.request.urlopen(base + '/health/', timeout=1).status == 200:
                    break
            except Exception:
                time.sleep(.1)
        fixture = json.loads((stage / 'data/committee.json').read_text())
        second_tech = int(subprocess.check_output([PYTHON, str(stage / 'app/manage.py'), 'shell', '--no-imports', '-c',
            'from portal.models import User; u=User.objects.create_user("committee_extra_tech", role=User.Role.TECHNICIAN); print(u.pk)'], cwd=stage / 'app', env=env).decode().strip())
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
            context = browser.new_context(viewport={'width': 390, 'height': 844})
            page = context.new_page()
            errors = []
            page.on('pageerror', lambda error: errors.append(str(error)))

            def role(code):
                page.goto(base + '/committee/')
                page.locator('form[data-session-entry]').filter(has=page.locator(f'input[value="{code}"]')).locator('button').click()
                page.wait_for_url('**/workspace/' if code == 'citizen' else '**/staff/')
                wait(page, 'code => WashOutbox.identity().then(u => u?.role === code)', code)

            def post(path, data):
                token = next(c['value'] for c in context.cookies() if c['name'] == 'csrftoken')
                return page.request.post(base + path, form=data, headers={'X-CSRFToken': token}, max_redirects=0)

            cid = fixture['complaints'][0]
            task = f'/staff/tasks/{cid}/'
            role('director')
            assert post(task + 'action/', {'action': 'assign', 'assignee': fixture['users']['technician']}).status == 302
            role('technician')
            assert post(task + 'action/', {'action': 'start'}).status == 302
            page.goto(base + task)
            wait(page, '() => window.washWorkResultReady === true')
            page.evaluate('() => navigator.serviceWorker.ready')
            context.set_offline(True)
            page.get_by_label('نتيجة المعالجة (مطلوبة)').fill('نتيجة خيالية محفوظة بلا اتصال')
            page.locator('#work-result-photos').set_input_files(str(stage / 'institution-logo.png'))
            page.locator('[data-work-result-save]').click()
            wait(page, '() => WashOutbox.identity().then(u => WashOutbox.listWorkResults(u.id)).then(items => items.length === 1 && items[0].status === "pending" && items[0].photos[0].blob.size > 0)')
            page.reload()
            wait(page, '() => window.washWorkResultReady === true')
            assert page.locator('[data-work-result-list]').inner_text().find('نتيجة خيالية محفوظة بلا اتصال') >= 0
            assert page.locator('[data-work-result-list] img').count() == 1
            assert not page.locator('[data-intake-shell]').is_visible()
            page.screenshot(path=str(OUTPUT / 'task-offline.png'), full_page=True)
            results.append('local first result/photo survives offline reload inside task URL')
            context.set_offline(False)
            page.evaluate('WashOutbox.sync()')
            wait(page, '() => WashOutbox.identity().then(u => WashOutbox.listWorkResults(u.id)).then(items => items[0].status === "synced")')
            page.goto(base + task)
            assert page.locator('.status-pill').inner_text() == 'بانتظار مراجعة المدير'
            assert page.get_by_alt_text('صورة تنفيذ 1').count() == 1
            image_url = page.get_by_alt_text('صورة تنفيذ 1').get_attribute('src')
            photo = page.request.get(base + image_url)
            assert photo.status == 200 and photo.headers['cache-control'] == 'no-store, private'
            # Return an acknowledged local result to pending to simulate a lost response.
            # A second send must return the same receipt without duplicating an event or photo.
            page.evaluate('''async () => {
                const user = await WashOutbox.identity(); const [item] = await WashOutbox.listWorkResults(user.id);
                item.status = 'pending';
                await new Promise((resolve, reject) => { const req = indexedDB.open('wash-device-drafts', 4);
                  req.onsuccess = () => {const db=req.result; const tx=db.transaction('execution_results','readwrite'); tx.objectStore('execution_results').put(item); tx.oncomplete=()=>{db.close(); resolve()}; tx.onerror=()=>reject(tx.error)};
                }); return WashOutbox.sync();
            }''')
            wait(page, '() => WashOutbox.identity().then(u => WashOutbox.listWorkResults(u.id)).then(items => items[0].status === "synced")')
            page.reload()
            assert page.get_by_alt_text('صورة تنفيذ 1').count() == 1
            assert page.locator('.workflow-events li').filter(has_text='رُفع للمراجعة').count() == 1
            results.append('reconnect normalizes private photo and idempotent lost-response replay does not duplicate')

            # The director asks for another round; the same employee can prepare a new revision.
            role('director')
            assert post(task + 'action/', {'action': 'return', 'note': 'مراجعة خيالية'}).status == 302
            role('technician')
            assert post(task + 'action/', {'action': 'start'}).status == 302
            page.goto(base + task)
            wait(page, '() => window.washWorkResultReady && !document.querySelector("[data-work-result-save]").disabled')
            context.set_offline(True)
            page.get_by_label('نتيجة المعالجة (مطلوبة)').fill('نتيجة الجولة الثانية الخيالية')
            page.locator('#work-result-photos').set_input_files(str(stage / 'institution-logo.png'))
            page.locator('[data-work-result-save]').click()
            wait(page, '() => WashOutbox.identity().then(u => WashOutbox.listWorkResults(u.id)).then(items => items.length === 2)')
            # A second browser moves the assignment while the technician remains offline.
            admin_context = browser.new_context()
            admin = admin_context.new_page()
            admin.goto(base + '/committee/')
            admin.locator('form[data-session-entry]').filter(has=admin.locator('input[value="director"]')).locator('button').click()
            admin.wait_for_url('**/staff/')
            token = next(c['value'] for c in admin_context.cookies() if c['name'] == 'csrftoken')
            bad = admin.request.post(base + task + 'action/', form={'action': 'reassign', 'assignee': fixture['users']['technician'], 'note': 'same assignee rejected'}, headers={'X-CSRFToken': token}, max_redirects=0)
            assert bad.status == 400, bad.status
            moved = admin.request.post(base + task + 'action/', form={'action': 'reassign', 'assignee': second_tech, 'note': 'تغيير المختص أثناء انقطاع الجهاز'}, headers={'X-CSRFToken': token}, max_redirects=0)
            assert moved.status == 302, moved.status
            admin_context.close()
            context.set_offline(False)
            page.evaluate('WashOutbox.sync()')
            wait(page, '() => WashOutbox.identity().then(u => WashOutbox.listWorkResults(u.id)).then(items => items.some(i => i.status === "review" && i.photos[0].blob.size > 0))')
            results.append('changed assignment preserves rejected result and photograph for review')
            role('citizen')
            assert page.request.get(base + image_url).status == 404
            page.goto(base + task)
            assert 'نتيجة الجولة الثانية الخيالية' not in page.locator('body').inner_text()
            # Generic shell cannot reveal this task from a beneficiary's local identity.
            context.set_offline(True)
            page.goto(base + task)
            wait(page, '() => window.washAppReady === true')
            assert 'نتيجة الجولة الثانية الخيالية' not in page.locator('body').inner_text()
            assert not page.locator('[data-work-result-save]').is_enabled()
            results.append('beneficiary cannot fetch execution photograph or reveal employee drafts offline')
            context.set_offline(False)
            keys = page.evaluate('''async () => {const rows=[]; for(const name of await caches.keys()) for(const req of await (await caches.open(name)).keys()) rows.push(new URL(req.url).pathname); return rows;}''')
            assert all(path == '/app-shell/' or path.startswith('/static/') for path in keys), keys
            assert '/static/js/work-results.js' in keys
            assert not errors, errors
            results.append('CacheStorage contains only public shell/static assets and no authenticated page, token, API or photograph')
            browser.close()
        (OUTPUT / 'results.json').write_text(json.dumps({'checks': results, 'native_windows': 'not tested'}, ensure_ascii=False, indent=2))
        for result in results:
            print('PASS:', result, flush=True)
    finally:
        if process:
            process.terminate()
            try: process.wait(timeout=20)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
        output.close()
        if db_created: db_action('drop')
        temp.cleanup()


if __name__ == '__main__':
    main()
