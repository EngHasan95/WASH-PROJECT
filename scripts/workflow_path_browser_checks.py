"""Exercise new reports from UI intake through final closure on an isolated demo."""
from datetime import date
import json
from pathlib import Path
import sys
import time
from playwright.sync_api import sync_playwright

BASE, STAGE, OUTPUT = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
OUTPUT.mkdir(parents=True, exist_ok=True)
DATA = json.loads((STAGE / 'data/committee.json').read_text())
RESULT = {'complaints': [], 'violations': [], 'denied_requests': [], 'offline_intake': []}
INTERNAL_NOTE = 'ملاحظة داخلية خيالية غير متاحة للمستفيد'


def wait_js(target, expression, *, arg=None, timeout=30000):
    # evaluate awaits promises; poll their resolved boolean explicitly.
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        if target.evaluate(expression, arg):
            return
        target.wait_for_timeout(50)
    target.screenshot(path=str(OUTPUT / 'failure.png'), full_page=True)
    (OUTPUT / 'failure.txt').write_text(target.locator('body').inner_text())
    raise AssertionError('Resolved browser condition remained false: ' + expression)


with sync_playwright() as playwright:
    browser = playwright.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
    context = browser.new_context(viewport={'width': 390, 'height': 844})
    page = context.new_page()
    watcher_context = browser.new_context(viewport={'width': 390, 'height': 844})
    watcher = watcher_context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    watcher.on('pageerror', lambda error: errors.append(str(error)))

    def role(code, target=page):
        target.goto(BASE + '/committee/')
        target.locator('form[data-session-entry]').filter(has=target.locator(f'input[value="{code}"]')).locator('button').click()
        target.wait_for_url('**/workspace/' if code == 'citizen' else '**/staff/')
        wait_js(target, 'code => window.WashOutbox && WashOutbox.identity().then(u => u && u.role === code)', arg=code)

    def post(path, fields):
        token = next(c['value'] for c in context.cookies() if c['name'] == 'csrftoken')
        return page.request.post(BASE + path, form=fields, headers={'X-CSRFToken': token}, max_redirects=0)

    def denied(path, fields, statuses=(403, 404)):
        response = post(path, fields)
        assert response.status in statuses, (path, response.status)
        RESULT['denied_requests'].append({'path': path, 'status': response.status, 'action': fields.get('action')})

    def observe(record, state):
        deadline = time.monotonic() + 15
        while True:
            receipt = next(c for c in watcher.request.get(BASE + '/api/complaints/').json()['complaints']
                if c['reference'] == record['reference'])
            if receipt['status'] == state or time.monotonic() >= deadline:
                break
            watcher.wait_for_timeout(50)
        assert receipt['status'] == state, (receipt['reference'], receipt['status'], state)
        watcher.goto(BASE + receipt['detail_url'])
        assert watcher.locator('.status-pill').inner_text() == receipt['status_label']
        assert INTERNAL_NOTE not in watcher.locator('body').inner_text()
        record['observed_states'].append(state)

    def submit_result(record):
        page.get_by_label('نتيجة المعالجة (مطلوبة)', exact=True).fill(INTERNAL_NOTE)
        if record['kind'] == 'leak':
            page.locator('#work-result-photos').set_input_files(str(STAGE / 'institution-logo.png'))
        page.get_by_role('button', name='إرسال النتيجة للمراجعة', exact=True).click()
        wait_js(page, '() => { const card = JSON.parse(document.querySelector("#work-task-card").textContent); return WashOutbox.listWorkResults(card.owner_id, card.complaint_id).then(items => items.some(i => i.assignment_event_id === card.assignment_event_id && i.status === "synced")); }', timeout=45000)

    role('citizen', watcher)
    role('citizen')
    for kind in ('leak', 'no_water', 'illegal_connection', 'water_quality', 'high_bill', 'weak_water', 'other'):
        page.goto(BASE + '/workspace/')
        wait_js(page, '() => !document.querySelector("#save-complaint").disabled')
        owner = page.evaluate('async () => (await WashOutbox.identity()).id')
        before = len(page.evaluate('id => WashOutbox.listComplaints(id)', owner))
        page.get_by_label('نوع البلاغ', exact=True).select_option(kind)
        if kind == 'other':
            page.locator('[name=other_type]').fill('نوع خيالي للمراجعة')
        page.get_by_label('المنطقة أو الحي', exact=True).select_option('n11')
        page.get_by_label('وصف البلاغ', exact=True).fill('بلاغ خيالي لاختبار المسار الكامل ' + kind)
        page.get_by_label('الموقع بالتفصيل', exact=True).fill('عنوان خيالي')
        page.get_by_label('أقرب معلم بارز', exact=True).fill('معلم خيالي')
        page.get_by_label('اسم مقدم البلاغ', exact=True).fill('مستفيد خيالي')
        page.get_by_label('رقم الهاتف', exact=True).fill('000000000')
        if kind == 'leak':
            page.locator('#complaint-photos').set_input_files(str(STAGE / 'institution-logo.png'))
            page.evaluate('() => navigator.serviceWorker.ready')
            context.set_offline(True)
        page.locator('#save-complaint').click()
        wait_js(page, 'n => WashOutbox.identity().then(u => WashOutbox.listComplaints(u.id)).then(items => items.length === n)', arg=before + 1)
        if kind == 'leak':
            local = page.evaluate('id => WashOutbox.listComplaints(id)', owner)
            assert sum(i['status'] != 'synced' for i in local) == 1
            page.reload()
            wait_js(page, '() => window.WashOutbox')
            local = page.evaluate('id => WashOutbox.listComplaints(id)', owner)
            assert len(local) == before + 1 and local[-1 if local[0]['status'] == 'synced' else 0]['photos']
            context.set_offline(False)
            page.evaluate('WashOutbox.sync()')
            RESULT['offline_intake'].append('complaint with photo: save, reload, reconnect')
        wait_js(page, 'n => WashOutbox.identity().then(u => WashOutbox.listComplaints(u.id)).then(items => items.length === n && items.every(i => i.status === "synced"))', arg=before + 1, timeout=45000)
        item = next(i for i in page.evaluate('id => WashOutbox.listComplaints(id)', owner) if i['data']['complaint_type'] == kind)
        receipt = item['receipt']
        record = {'kind': kind, 'reference': receipt['reference'], 'id': int(receipt['detail_url'].strip('/').split('/')[-1]),
                  'client_id': item['client_id'], 'observed_states': [], 'department': receipt['department']}
        assert receipt['department'] == ('القسم المالي' if kind == 'high_bill' else 'الشؤون الفنية')
        observe(record, 'received')
        RESULT['complaints'].append(record)
    page.evaluate('WashOutbox.sync()')
    assert len(page.evaluate('id => WashOutbox.listComplaints(id)', owner)) == 7
    for record in RESULT['complaints']:
        path = f"/staff/tasks/{record['id']}/"
        action = path + 'action/'
        role('citizen')
        denied(action, {'action': 'assign', 'assignee': DATA['users']['technician']})
        role('director')
        denied(action, {'action': 'close'})
        if record['kind'] == 'high_bill':
            page.goto(BASE + path)
            assert page.locator('button[value=assign]').count() == 0
            role('finance')
            page.goto(BASE + path)
            page.get_by_label('المختص المالي المسؤول', exact=True).select_option(str(DATA['users']['finance']))
            page.get_by_role('button', name='إسناد إلى المختص المالي', exact=True).click()
            record['assignment_method'] = 'financial department responsible assigns specialist'
        else:
            page.goto(BASE + path)
            page.get_by_label('الفني المسؤول', exact=True).select_option(str(DATA['users']['technician']))
            page.get_by_label('ملاحظة الإجراء', exact=True).fill(INTERNAL_NOTE)
            page.get_by_role('button', name='إسناد إلى الفني', exact=True).click()
            role('technician')
            page.goto(BASE + path)
            record['assignment_method'] = 'technical director assigns technician'
        observe(record, 'assigned')
        denied(action, {'action': 'submit', 'note': 'تجاوز البدء'})
        page.get_by_role('button', name='بدء المعالجة', exact=True).click()
        observe(record, 'in_progress')
        denied(action, {'action': 'close'})
        denied(action, {'action': 'submit', 'note': ''}, statuses=(400,))
        submit_result(record)
        observe(record, 'review')
        role('director')
        page.goto(BASE + path)
        denied(action, {'action': 'return', 'note': ''}, statuses=(400,))
        if record['kind'] in ('leak', 'high_bill'):
            page.get_by_label('ملاحظة الإجراء (مطلوبة للنتيجة أو الإعادة)', exact=True).fill(INTERNAL_NOTE)
            page.get_by_role('button', name='إعادة للمعالجة', exact=True).click()
            observe(record, 'returned')
            role('finance' if record['kind'] == 'high_bill' else 'technician')
            page.goto(BASE + path)
            page.get_by_role('button', name='بدء المعالجة', exact=True).click()
            observe(record, 'in_progress')
            submit_result(record)
            observe(record, 'review')
            role('director')
            page.goto(BASE + path)
        page.get_by_role('button', name='اعتماد وإغلاق', exact=True).click()
        observe(record, 'closed')
        denied(action, {'action': 'close'})
        watcher.goto(BASE + '/workspace/complaints/?q=' + record['reference'] + '&status=closed')
        assert watcher.get_by_text(record['reference'], exact=True).count() >= 1
    watcher.goto(BASE + f"/workspace/complaints/{RESULT['complaints'][0]['id']}/")
    watcher.screenshot(path=str(OUTPUT / 'beneficiary-closed.png'), full_page=True)
    role('technician')
    page.goto(BASE + f"/staff/tasks/{RESULT['complaints'][0]['id']}/")
    page.screenshot(path=str(OUTPUT / 'complaint-complete-history.png'), full_page=True)

    for activity in ('residential', 'commercial', 'government'):
        role('technician')
        page.goto(BASE + '/staff/violations/new/')
        wait_js(page, '() => !document.querySelector("#violation-save").disabled')
        technician = DATA['users']['technician']
        previous = len(page.evaluate('id => WashOutbox.listViolations(id)', technician))
        page.get_by_label('اسم المخالف', exact=True).fill('اسم خيالي ' + activity)
        page.get_by_label('المنطقة', exact=True).fill('منطقة خيالية')
        page.get_by_label('العنوان التفصيلي', exact=True).fill('عنوان خيالي')
        page.get_by_label('نوع المخالفة', exact=True).select_option('random_connection')
        page.get_by_label('نوع النشاط', exact=True).select_option(activity)
        page.get_by_label('المياه المستهلكة تقديريًا (متر مكعب)', exact=True).fill('99.99')
        page.get_by_label('مدة التقدير (أيام)', exact=True).fill('30')
        if activity == 'residential':
            page.evaluate('() => navigator.serviceWorker.ready')
            context.set_offline(True)
        page.locator('#violation-save').click()
        wait_js(page, 'n => WashOutbox.identity().then(u => WashOutbox.listViolations(u.id)).then(items => items.length === n)', arg=previous + 1)
        if activity == 'residential':
            page.reload()
            wait_js(page, '() => window.WashOutbox')
            assert len(page.evaluate('id => WashOutbox.listViolations(id)', technician)) == previous + 1
            context.set_offline(False)
            page.evaluate('WashOutbox.sync()')
            RESULT['offline_intake'].append('violation: save, reload, reconnect')
        wait_js(page, 'n => WashOutbox.identity().then(u => WashOutbox.listViolations(u.id)).then(items => items.length === n && items.every(i => i.status === "synced"))', arg=previous + 1, timeout=45000)
        item = next(i for i in page.evaluate('id => WashOutbox.listViolations(id)', technician) if i['data']['activity'] == activity)
        path = item['receipt']['detail_url']
        record = {'id': int(path.strip('/').split('/')[-1]), 'reference': item['receipt']['reference'], 'activity': activity,
            'client_id': item['client_id'], 'states': ['system_manager']}
        def violation_state(state):
            page.goto(BASE + path)
            labels = {'secretariat': 'لدى السكرتارية', 'followup': 'لدى قسم المتابعة',
                'results_returned': 'عادت النتائج للفنية والمالية', 'awaiting_payment': 'بانتظار السداد',
                'paid': 'أكدت المالية السداد', 'closed': 'مغلقة بعد التسوية'}
            assert page.locator('.status-pill').inner_text() == labels[state]
            record['states'].append(state)
        role('citizen')
        assert page.request.get(BASE + path).status == 403
        role('system_manager')
        page.goto(BASE + path)
        denied(path + 'action/', {'action': 'return_results', 'note': 'تجاوز السكرتارية'})
        page.get_by_label('ملاحظة الإحالة', exact=True).fill('إحالة خيالية')
        page.get_by_role('button', name='إحالة للسكرتارية', exact=True).click()
        violation_state('secretariat')
        role('secretariat')
        page.goto(BASE + path)
        denied(path + 'action/', {'action': 'to_followup', 'note': 'لا مرجع'}, statuses=(400,))
        if activity == 'residential':
            page.get_by_role('link', name='تحرير محضر أو مذكرة', exact=True).click()
            page.get_by_label('نوع المستند', exact=True).select_option('letter')
            page.get_by_label('الموضوع', exact=True).fill('مذكرة خيالية للمراجعة')
            page.get_by_label('رقم الصادر أو المحضر', exact=True).fill('TEST-WORKFLOW-LETTER')
            page.get_by_label('تاريخ المستند', exact=True).fill(date.today().isoformat())
            page.get_by_label('الجهة المخاطبة', exact=True).fill('جهة خيالية')
            page.get_by_label('نص المحضر أو المذكرة', exact=True).fill('مراسلة تجريبية لا ترسل إلى أي جهة')
            page.get_by_role('button', name='حفظ المسودة', exact=True).click()
            page.get_by_role('button', name='إصدار النسخة وحفظها', exact=True).click()
            page.get_by_role('link', name='← ملف المخالفة', exact=True).click()
        page.get_by_label('ملخص المحضر والمراسلات', exact=True).fill('إحالة خيالية للمتابعة')
        page.get_by_label('الجهة المخاطبة', exact=True).fill('جهة خيالية')
        page.get_by_label('رقم المذكرة أو المحضر', exact=True).fill('TEST-WORKFLOW-' + activity)
        page.get_by_role('button', name='إحالة لقسم المتابعة', exact=True).click()
        violation_state('followup')
        role('followup')
        page.goto(BASE + path)
        page.get_by_label('نتائج النزول والإجراءات الميدانية', exact=True).fill('نتائج خيالية للفنية والمالية')
        page.get_by_role('button', name='إعادة النتائج للفنية والمالية', exact=True).click()
        violation_state('results_returned')
        settlement = path + 'settlement/'
        role('director')
        denied(settlement + 'close/', {'policy_applied': 'on', 'closure_note': 'إغلاق قبل الدفع'})
        denied(settlement + 'pay/', {'payment_amount': '1', 'payment_receipt': 'TEST', 'payment_date': date.today().isoformat()})
        role('finance')
        page.goto(BASE + path)
        page.get_by_label('كمية المياه المعتمدة للاحتساب (متر مكعب)', exact=True).fill('10.5')
        page.get_by_label('مرجع اعتماد الكمية وأساس التسوية', exact=True).fill('اعتماد خيالي مستقل عن تقدير الفني')
        page.get_by_role('button', name='اعتماد الرسوم بواسطة المالية', exact=True).click()
        violation_state('awaiting_payment')
        amount = '228150' if activity == 'residential' else '329200'
        denied(settlement + 'pay/', {'payment_amount': '1', 'payment_receipt': 'TEST', 'payment_date': date.today().isoformat()}, statuses=(400,))
        denied(settlement + 'close/', {'policy_applied': 'on', 'closure_note': 'إغلاق المالية'})
        page.get_by_label('المبلغ المسدد (ريال يمني)', exact=True).fill(amount)
        page.get_by_label('رقم سند التحصيل', exact=True).fill('TEST-WORKFLOW-' + activity)
        page.get_by_label('تاريخ السداد', exact=True).fill(date.today().isoformat())
        page.get_by_role('button', name='تأكيد الدفع بواسطة المالية', exact=True).click()
        violation_state('paid')
        role('director')
        page.goto(BASE + path)
        denied(settlement + 'close/', {'closure_note': 'بدون تأكيد السياسة'}, statuses=(400,))
        page.get_by_label('نتيجة المعالجة وأساس إغلاق الملف', exact=True).fill('معالجة خيالية مكتملة')
        page.get_by_label('أؤكد استكمال معالجة المخالفة وتطبيق سياسة المؤسسة', exact=True).check()
        page.get_by_role('button', name='إغلاق الملف بواسطة المدير الفني', exact=True).click()
        violation_state('closed')
        record['paid_amount'] = amount
        RESULT['violations'].append(record)
    page.screenshot(path=str(OUTPUT / 'violation-complete-history.png'), full_page=True)
    assert not errors, errors
    RESULT['browser_errors'] = errors
    (OUTPUT / 'browser-results.json').write_text(json.dumps(RESULT, ensure_ascii=False, indent=2))
    browser.close()
print(json.dumps({'new_complaints_closed': len(RESULT['complaints']), 'new_violations_closed': len(RESULT['violations']),
    'forbidden_or_invalid_requests_checked': len(RESULT['denied_requests']), 'offline_intakes': RESULT['offline_intake']}))
