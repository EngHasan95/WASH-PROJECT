"""Exercise the real isolated container demo. Only its fictional fixture DB is changed."""
from datetime import date
import json
from pathlib import Path
import subprocess
import time

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BASE = 'http://127.0.0.1:8765'
DOCKER = ['docker', '--host=unix:///var/run/docker.sock']
OUTPUT = ROOT / 'test-results'

def wait_js(page, expression, *, arg=None, timeout=30000):
    deadline = time.monotonic() + timeout / 1000
    while time.monotonic() < deadline:
        if page.evaluate(expression, arg):
            return
        page.wait_for_timeout(50)
    raise AssertionError('Browser condition remained false: ' + expression)

def control(*args):
    subprocess.run([*DOCKER, *args], check=True, stdout=subprocess.DEVNULL)

def fixture():
    return json.loads(subprocess.check_output([*DOCKER, 'exec', 'wash-committee-app-1', 'python', '-c',
        "from pathlib import Path;print(Path('/committee/committee.json').read_text())"], text=True))

def main():
    data = fixture()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path='/usr/bin/chromium', headless=True, args=['--no-sandbox'])
        context = browser.new_context(viewport={'width':390,'height':844})
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        def role(code):
            page.goto(BASE + '/committee/')
            page.locator('form[data-session-entry]').filter(has=page.locator(f'input[value="{code}"]')).locator('button').click()
            page.wait_for_url('**/workspace/' if code == 'citizen' else '**/staff/')
            wait_js(page, '() => window.WashOutbox')

        page.goto(BASE + '/committee/')
        assert page.locator('form[data-session-entry]').count() == 7
        for width in (320,390,768,1365):
            page.set_viewport_size({'width':width,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), ('guide width',width)
        page.screenshot(path=str(OUTPUT / 'committee-guide-desktop.png'), full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(OUTPUT / 'committee-guide-phone.png'), full_page=True)
        role('citizen')
        assert page.request.get(BASE + '/staff/violations/').status == 403
        page.goto(BASE + '/workspace/')
        wait_js(page, 'async () => (await WashOutbox.identity()) !== null && !document.querySelector("#save-complaint").disabled')
        page.evaluate('() => navigator.serviceWorker.ready')
        owner = page.evaluate('async () => (await WashOutbox.identity()).id')
        control('stop', 'wash-committee-app-1')
        try:
            page.get_by_label('نوع البلاغ', exact=True).select_option('leak')
            page.get_by_label('المنطقة أو الحي', exact=True).select_option('other')
            page.get_by_label('اكتب المنطقة أو الحي', exact=True).fill('حي انقطاع تجريبي')
            page.get_by_label('وصف البلاغ', exact=True).fill('بلاغ خيالي محفوظ أثناء توقف خادم اللجنة')
            page.get_by_label('الموقع بالتفصيل', exact=True).fill('عنوان خيالي أثناء الانقطاع')
            page.get_by_label('أقرب معلم بارز', exact=True).fill('معلم خيالي')
            page.get_by_label('اسم مقدم البلاغ', exact=True).fill('مستفيد خيالي')
            page.get_by_label('رقم الهاتف', exact=True).fill('000000000')
            page.locator('#save-complaint').click()
            wait_js(page, '() => document.querySelector("#complaint-description").value === ""')
            entries = page.evaluate('id => WashOutbox.listComplaints(id)', owner)
            assert len(entries) == 1 and entries[0]['status'] != 'synced'
            page.reload()
            wait_js(page, '() => window.WashOutbox')
            assert len(page.evaluate('id => WashOutbox.listComplaints(id)', owner)) == 1
        finally:
            control('start', 'wash-committee-app-1')
        for _ in range(60):
            try:
                if page.request.get(BASE + '/health/').status == 200: break
            except Exception: pass
            time.sleep(.25)
        else: raise AssertionError('Demo failed to restart')
        page.evaluate('WashOutbox.sync()')
        wait_js(page, 'id => WashOutbox.listComplaints(id).then(items => items.length === 1 && items[0].status === "synced")', arg=owner, timeout=40000)
        uploaded = page.evaluate('id => WashOutbox.listComplaints(id)', owner)
        page.evaluate('WashOutbox.sync()')
        assert len(page.evaluate('id => WashOutbox.listComplaints(id)', owner)) == 1
        role('director')
        page.goto(BASE + f"/staff/tasks/{data['complaints'][0]}/")
        page.get_by_label('الفني المسؤول', exact=True).select_option(str(data['users']['technician']))
        page.get_by_label('ملاحظة الإجراء', exact=True).fill('ملاحظة داخلية خيالية للجنة')
        page.get_by_role('button', name='إسناد إلى الفني', exact=True).click()
        role('technician')
        page.goto(BASE + f"/staff/tasks/{data['complaints'][0]}/")
        page.get_by_role('button', name='بدء المعالجة', exact=True).click()
        page.get_by_label('نتيجة المعالجة (مطلوبة)', exact=True).fill('أصلح التسريب في تجربة اللجنة')
        page.get_by_role('button', name='إرسال النتيجة للمراجعة', exact=True).click()
        wait_js(page, '() => { const c = JSON.parse(document.querySelector("#work-task-card").textContent); return WashOutbox.listWorkResults(c.owner_id, c.complaint_id).then(items => items.some(i => i.assignment_event_id === c.assignment_event_id && i.status === "synced")); }', timeout=45000)
        role('director')
        page.goto(BASE + f"/staff/tasks/{data['complaints'][0]}/")
        page.get_by_role('button', name='اعتماد وإغلاق', exact=True).click()
        role('citizen')
        page.goto(BASE + f"/workspace/complaints/{data['complaints'][0]}/")
        assert 'ملاحظة داخلية خيالية للجنة' not in page.locator('body').inner_text()
        violation_path = f"/staff/violations/{data['violation']}/"
        role('system_manager')
        page.goto(BASE + violation_path)
        page.get_by_label('ملاحظة الإحالة', exact=True).fill('إحالة خيالية للسكرتارية')
        page.get_by_role('button', name='إحالة للسكرتارية', exact=True).click()
        role('secretariat')
        page.goto(BASE + violation_path)
        page.get_by_role('link', name='تحرير محضر أو مذكرة', exact=True).click()
        page.get_by_label('نوع المستند', exact=True).select_option('letter')
        page.get_by_label('الموضوع', exact=True).fill('مذكرة خيالية لتجربة اللجنة')
        page.get_by_label('رقم الصادر أو المحضر', exact=True).fill('TEST-COMMITTEE-1')
        page.get_by_label('تاريخ المستند', exact=True).fill(date.today().isoformat())
        page.get_by_label('الجهة المخاطبة', exact=True).fill('جهة خيالية')
        page.get_by_label('نص المحضر أو المذكرة', exact=True).fill('مراسلة خيالية للتحقق من إصدار الوثائق. لا ترسل إلى أي جهة.')
        page.get_by_role('button', name='حفظ المسودة', exact=True).click()
        page.get_by_role('button', name='إصدار النسخة وحفظها', exact=True).click()
        page.get_by_role('link', name='← ملف المخالفة', exact=True).click()
        page.get_by_label('ملخص المحضر والمراسلات', exact=True).fill('صدرت مذكرة الاختبار')
        page.get_by_label('الجهة المخاطبة', exact=True).fill('جهة خيالية')
        page.get_by_label('رقم المذكرة أو المحضر', exact=True).fill('TEST-COMMITTEE-1')
        page.get_by_role('button', name='إحالة لقسم المتابعة', exact=True).click()
        role('followup')
        page.goto(BASE + violation_path)
        page.get_by_label('نتائج النزول والإجراءات الميدانية', exact=True).fill('نتائج خيالية أعيدت للفنية والمالية')
        page.get_by_role('button', name='إعادة النتائج للفنية والمالية', exact=True).click()
        role('finance')
        page.goto(BASE + violation_path)
        assert page.request.get(BASE + '/api/staff/violations/fee-memo/').status == 200
        page.get_by_label('كمية المياه المعتمدة للاحتساب (متر مكعب)', exact=True).fill('10.5')
        page.get_by_label('مرجع اعتماد الكمية وأساس التسوية', exact=True).fill('مرجع خيالي TEST-001')
        page.get_by_role('button', name='اعتماد الرسوم بواسطة المالية', exact=True).click()
        page.get_by_label('المبلغ المسدد (ريال يمني)', exact=True).fill('329200')
        page.get_by_label('رقم سند التحصيل', exact=True).fill('TEST-COMMITTEE-RECEIPT')
        page.get_by_label('تاريخ السداد', exact=True).fill(date.today().isoformat())
        assert page.get_by_role('button', name='إغلاق الملف بواسطة المدير الفني', exact=True).count() == 0
        page.get_by_role('button', name='تأكيد الدفع بواسطة المالية', exact=True).click()
        role('director')
        page.goto(BASE + violation_path)
        page.get_by_label('نتيجة المعالجة وأساس إغلاق الملف', exact=True).fill('تجربة خيالية استكملت المعالجة والسداد')
        page.get_by_label('أؤكد استكمال معالجة المخالفة وتطبيق سياسة المؤسسة', exact=True).check()
        page.get_by_role('button', name='إغلاق الملف بواسطة المدير الفني', exact=True).click()
        assert page.get_by_text('مغلقة بعد التسوية', exact=True).count() == 1
        page.get_by_role('link', name='تقارير الملف ونماذجه', exact=True).click()
        page.get_by_role('link', name='استخدام هذا النموذج', exact=True).click()
        page.get_by_label('رقم المستند', exact=True).fill('TEST-REPORT-1')
        page.get_by_label('الجهة المخاطبة', exact=True).fill('لجنة تجريبية')
        page.get_by_role('button', name='حفظ التقرير ومعاينته', exact=True).click()
        page.wait_for_url('**/print/')
        assert '329,200' in page.locator('body').inner_text() or '329200' in page.locator('body').inner_text()
        page.pdf(path=str(OUTPUT / 'committee-violation-report.pdf'), format='A4', print_background=True, prefer_css_page_size=True)
        assert not errors, errors
        cache_keys = page.evaluate('async () => (await Promise.all((await caches.keys()).map(async name => (await (await caches.open(name)).keys()).map(r => new URL(r.url).pathname)))).flat()')
        assert all(not key.startswith(('/committee/','/staff/','/api/','/workspace/')) for key in cache_keys), cache_keys
        browser.close()
    print(json.dumps({'passed':6,'checks':['Guide role switching and responsive layout','Actual server outage: persist, reload, restart, sync once','Complaint assignment, execution and review; beneficiary privacy','All violation handoffs, financial payment and director closure','Official memo and approved report generation/PDF','Private pages excluded from offline caches']}, ensure_ascii=False))

if __name__ == '__main__': main()
