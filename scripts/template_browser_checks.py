"""Exercise user-added DOCX templates, approval, private snapshots and A4 layout."""
import json
from pathlib import Path
import re
import secrets
import subprocess
import tempfile
import time
import uuid
from urllib.request import urlopen
import zipfile

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BASE = 'http://127.0.0.1:8003'
username = 'browser_template_' + uuid.uuid4().hex[:10]
password = secrets.token_urlsafe(32)
server = None


def django(code, data):
    result = subprocess.run([str(ROOT / '.venv/bin/python'), '-c',
        "import os,json,sys;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');import django;django.setup();" + code],
        input=json.dumps(data), text=True, capture_output=True, cwd=ROOT, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


try:
    fixtures = django("from portal.models import User,Violation,Complaint;import uuid;d=json.load(sys.stdin);"
        "users={role:User.objects.create_user(d['username']+'_'+role,password=d['password'],first_name='موظف خيالي',role=role) for role in ['secretariat','director','technician','citizen']};"
        "v=Violation.objects.create(reporter=users['technician'],client_id=uuid.uuid4(),payload_digest='a'*64,person_name='شخص خيالي',address='عنوان اختبار',area='منطقة خيالية',estimated_cubic_meters='10.50',estimate_days=30,activity='government',kind='random_connection',status='secretariat');"
        "c=Complaint.objects.create(owner=users['citizen'],client_id=uuid.uuid4(),payload_digest='b'*64,reporter_name='مستفيد خيالي',phone='000000000',complaint_type='leak',neighborhood='other',other_neighborhood='حي اختبار',address='عنوان خيالي',landmark='معلم خيالي',description='تسريب خيالي',assignee=users['technician']);"
        "print(json.dumps({'violation':v.pk,'complaint':c.pk}))", {'username': username, 'password': password})
    server = subprocess.Popen([str(ROOT / '.venv/bin/python'), 'manage.py', 'runserver', '127.0.0.1:8003', '--noreload'],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            if urlopen(BASE + '/health/', timeout=.5).status == 200:
                break
        except Exception:
            time.sleep(.1)
    else:
        raise RuntimeError('Template test server did not become ready')
    output = ROOT / 'test-results'
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='wash-template-browser-', dir=ROOT / '.local') as temporary, sync_playwright() as playwright:
        browser = playwright.chromium.launch_persistent_context(temporary, executable_path='/usr/bin/chromium', headless=True,
            args=['--no-sandbox'], viewport={'width': 390, 'height': 844})
        page = browser.pages[0]
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))

        def login(role):
            page.goto(BASE + '/accounts/login/')
            page.get_by_label('اسم المستخدم', exact=True).fill(username + '_' + role)
            page.get_by_label('كلمة المرور', exact=True).fill(password)
            page.get_by_role('button', name='دخول', exact=True).click()
            page.wait_for_url('**/workspace/')

        def check_widths(target, name):
            for width in (320, 390, 768, 1365):
                target.set_viewport_size({'width': width, 'height': 844})
                assert target.evaluate('document.documentElement.scrollWidth <= innerWidth'), (name, width)
            target.set_viewport_size({'width': 390, 'height': 844})

        login('secretariat')
        page.goto(BASE + '/staff/report-templates/new/')
        page.get_by_label('اسم النموذج', exact=True).fill('نموذج تحقيق جديد لم يكن موجودًا')
        page.get_by_label('عنوان التقرير', exact=True).fill('تقرير تحقيق واقعة ميدانية {{رقم الملف}}')
        page.get_by_label('حقول إضافية', exact=True).fill('سبب الإجراء')
        page.get_by_label('حقول جدول البيانات وترتيبها', exact=True).fill('المنطقة\nنوع النشاط\nالاستهلاك التقديري\nسبب الإجراء')
        page.get_by_label('نص النموذج', exact=True).fill('الموقع: ')
        page.get_by_role('button', name='المنطقة', exact=True).click()
        assert page.get_by_label('نص النموذج', exact=True).input_value() == 'الموقع: {{المنطقة}}'
        page.locator('#custom-field-tokens').get_by_role('button', name='سبب الإجراء', exact=True).click()
        assert '{{سبب الإجراء}}' in page.get_by_label('نص النموذج', exact=True).input_value()
        page.get_by_label('نص النموذج', exact=True).fill('')
        page.get_by_label('اتجاه الصفحة', exact=True).select_option('landscape')
        page.get_by_label('هوامش الصفحة', exact=True).select_option('wide')
        page.get_by_label('لون التفاصيل', exact=True).select_option('olive')
        page.get_by_label('موضع الشعار', exact=True).select_option('side')
        docx = Path(temporary) / 'fictional-new-form.docx'
        with zipfile.ZipFile(docx, 'w') as archive:
            archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>تقرير تحقيق تجريبي بشأن {{اسم المخالف}}.</w:t></w:r></w:p><w:p><w:r><w:t>سبب الإجراء: {{سبب الإجراء}}. هذه بيانات خيالية للاختبار.</w:t></w:r></w:p></w:body></w:document>')
        page.get_by_label('استيراد نص من Word (اختياري)', exact=True).set_input_files(str(docx))
        check_widths(page, 'template editor')
        page.screenshot(path=str(output / 'template-editor-phone.png'), full_page=True)
        with page.expect_popup() as popup:
            page.get_by_role('button', name='معاينة قبل الحفظ', exact=True).click()
        preview = popup.value
        preview.wait_for_load_state()
        assert 'معاينة نموذج ببيانات خيالية' in preview.locator('body').inner_text()
        preview.close()
        page.get_by_role('button', name='حفظ النموذج للمراجعة', exact=True).click()
        page.wait_for_url(re.compile(r'.*/staff/report-templates/\d+/$'))
        template_url = page.url
        assert page.get_by_role('button', name='اعتماد وتفعيل هذا الإصدار', exact=True).count() == 0
        login('director')
        page.goto(template_url)
        page.get_by_role('button', name='اعتماد وتفعيل هذا الإصدار', exact=True).click()
        assert 'الإصدار 1 هو المعتمد حاليًا' in page.locator('body').inner_text()
        page.goto(BASE + '/staff/report-templates/')
        check_widths(page, 'template library')
        page.screenshot(path=str(output / 'template-library-phone.png'), full_page=True)
        login('technician')
        page.goto(BASE + f"/staff/violations/{fixtures['violation']}/")
        page.get_by_role('link', name='تقارير الملف ونماذجه', exact=True).click()
        page.get_by_role('link', name='استخدام هذا النموذج', exact=True).click()
        page.get_by_label('رقم المستند', exact=True).fill('TEST-CUSTOM-1')
        page.get_by_label('الجهة المخاطبة', exact=True).fill('جهة خيالية')
        page.get_by_label('سبب الإجراء', exact=True).fill('طلب تدقيق الواقعة الميدانية')
        check_widths(page, 'report generation')
        page.get_by_role('button', name='حفظ التقرير ومعاينته', exact=True).click()
        page.wait_for_url('**/print/')
        saved_url = page.url
        original_text = page.locator('.official-paper').inner_text()
        assert 'تقرير تحقيق تجريبي بشأن شخص خيالي' in original_text
        assert 'طلب تدقيق الواقعة الميدانية' in original_text
        assert '{{اسم المخالف}}' not in original_text
        assert '10.50 متر مكعب' in original_text
        check_widths(page, 'custom report print')
        page.screenshot(path=str(output / 'custom-report-phone.png'), full_page=True)
        page.evaluate('() => document.fonts.ready')
        page.pdf(path=str(output / 'custom-report-landscape.pdf'), format='A4', print_background=True, prefer_css_page_size=True)
        pdf_info = subprocess.run(['pdfinfo', str(output / 'custom-report-landscape.pdf')], capture_output=True, text=True, check=True).stdout
        dimensions = re.search(r'Page size:\s*([\d.]+) x ([\d.]+)', pdf_info)
        assert dimensions and float(dimensions[1]) > float(dimensions[2]), pdf_info
        assert re.search(r'Pages:\s*1\b', pdf_info), 'Short sample must fit one page: ' + pdf_info
        response = page.request.get(saved_url)
        assert response.headers['cache-control'] == 'no-store, private'
        login('director')
        page.goto(template_url)
        page.get_by_role('link', name='تعديل وحفظ إصدار جديد', exact=True).first.click()
        page.get_by_label('نص النموذج', exact=True).fill('نص جديد بعد التعديل {{اسم المخالف}} {{سبب الإجراء}}')
        page.get_by_label('موضع الشعار', exact=True).select_option('hidden')
        page.get_by_role('button', name='حفظ النموذج للمراجعة', exact=True).click()
        assert 'الإصدار 1 هو المعتمد حاليًا' in page.locator('body').inner_text()
        page.get_by_role('button', name='اعتماد وتفعيل هذا الإصدار', exact=True).click()
        assert 'الإصدار 2 هو المعتمد حاليًا' in page.locator('body').inner_text()
        login('technician')
        page.goto(saved_url)
        assert page.locator('.official-paper').inner_text() == original_text
        cached = page.evaluate('''async () => {
            const paths=[]; for(const name of await caches.keys()) for(const request of await (await caches.open(name)).keys()) paths.push(new URL(request.url).pathname);return paths;
        }''')
        assert not any(path.startswith('/staff/') or path.startswith('/api/') for path in cached)
        login('citizen')
        assert page.request.get(saved_url).status == 403
        assert page.request.get(BASE + '/staff/report-templates/').status == 403
        assert not errors, errors
        browser.close()
    print(json.dumps({'passed': 4, 'checks': ['User adds a new DOCX-backed model with extra fields and director approval',
        'Approved model fills private record data and custom fields', 'New template versions preserve previously saved reports',
        'Mobile layouts, landscape A4 PDF, cache privacy and beneficiary denial']}))
finally:
    if server:
        server.terminate()
        server.wait(timeout=10)
    django("from portal.models import User,Violation,Complaint,SavedReport,ReportTemplate;d=json.load(sys.stdin);users=User.objects.filter(username__startswith=d['username']);"
        "SavedReport.objects.filter(created_by__in=users).delete();ReportTemplate.objects.filter(created_by__in=users).delete();"
        "Violation.objects.filter(reporter__in=users).delete();Complaint.objects.filter(owner__in=users).delete();users.delete()", {'username': username})
