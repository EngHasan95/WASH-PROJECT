"""Browser regression checks for validated administrative scope and corrupt DOCX UX."""
import io
import json
from pathlib import Path
import re
import secrets
import struct
import subprocess
import tempfile
import time
import uuid
from urllib.request import urlopen
import zipfile

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BASE = 'http://127.0.0.1:8027'
username = 'browser_scope_' + uuid.uuid4().hex[:10]
password = secrets.token_urlsafe(32)
server = None


def django(code, data):
    result = subprocess.run([str(ROOT / '.venv/bin/python'), '-c',
        "import os,json,sys;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');import django;django.setup();" + code],
        input=json.dumps(data), text=True, capture_output=True, cwd=ROOT, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


try:
    fixtures = django("from portal.models import User,Complaint,ReportTemplate,ReportTemplateVersion;from portal.test_report_templates import template_data;from django.utils import timezone;import uuid;d=json.load(sys.stdin);"
        "users={role:User.objects.create_user(d['username']+'_'+role,password=d['password'],first_name='موظف خيالي',role=role) for role in ['director','technician','citizen']};"
        "c=Complaint.objects.create(owner=users['citizen'],client_id=uuid.uuid4(),payload_digest='b'*64,reporter_name='مستفيد خيالي',phone='000000000',complaint_type='leak',neighborhood='n11',address='عنوان خيالي',landmark='معلم خيالي',description='تسريب خيالي',assignee=users['technician'],status='closed');"
        "spec=template_data();[spec.pop(k) for k in ['client_id','expected_revision','name','source']];spec.update(title='ملخص نطاق خيالي',body='عدد البلاغات: {{إجمالي البلاغات}}',custom_fields=[],table_fields=['البلاغات المغلقة']);"
        "r=ReportTemplate.objects.create(created_by=users['director'],source='summary');v=ReportTemplateVersion.objects.create(template=r,number=1,name=d['username'],spec=spec,created_by=users['director'],approved_by=users['director'],approved_at=timezone.now());r.active_version=v;r.save();"
        "print(json.dumps({'complaint':c.pk,'technician':users['technician'].pk,'version':v.pk,'date':timezone.localdate().isoformat()}))", {'username': username, 'password': password})
    server = subprocess.Popen([str(ROOT / '.venv/bin/python'), 'manage.py', 'runserver', '127.0.0.1:8027', '--noreload'],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            if urlopen(BASE + '/health/', timeout=.5).status == 200:
                break
        except Exception:
            time.sleep(.1)
    else:
        raise RuntimeError('Scope test server did not become ready')
    output = ROOT / 'test-results'
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='wash-scope-browser-', dir=ROOT / '.local') as temporary, sync_playwright() as playwright:
        browser = playwright.chromium.launch_persistent_context(temporary, executable_path='/usr/bin/chromium', headless=True,
            args=['--no-sandbox'], viewport={'width': 390, 'height': 844})
        page = browser.pages[0]
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(BASE + '/accounts/login/')
        page.get_by_label('اسم المستخدم', exact=True).fill(username + '_director')
        page.get_by_label('كلمة المرور', exact=True).fill(password)
        page.get_by_role('button', name='دخول', exact=True).click()
        page.wait_for_url('**/workspace/')
        page.goto(BASE + '/staff/reports/')
        for width in (320, 390, 768, 1365):
            page.set_viewport_size({'width': width, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), ('report scope', width)
        page.set_viewport_size({'width': 390, 'height': 844})
        page.get_by_label('الاستلام من تاريخ', exact=True).fill(fixtures['date'])
        page.get_by_label('الاستلام حتى تاريخ', exact=True).fill(fixtures['date'])
        page.get_by_label('حي البلاغ', exact=True).select_option('n11')
        page.get_by_label('نوع البلاغ', exact=True).select_option('leak')
        page.get_by_label('حالة البلاغ الحالية', exact=True).select_option('closed')
        page.get_by_label('الفني (المسند إليه للبلاغ / المبلغ للمخالفة)', exact=True).select_option(str(fixtures['technician']))
        page.get_by_role('button', name='تطبيق نطاق التقرير', exact=True).click()
        assert page.get_by_role('heading', name='1 بلاغ مستلم', exact=True).is_visible()
        page.screenshot(path=str(output / 'report-scope-phone.png'), full_page=True)
        page.get_by_role('link', name='التقارير الإدارية بنماذج معتمدة', exact=True).click()
        version_link = page.locator(f'a[href*="/versions/{fixtures["version"]}/"]')
        # URL route is authoritative, without assuming the position in the shared library.
        if version_link.count() == 0:
            version_link = page.locator('section').filter(has=page.get_by_role('heading', name=username, exact=True)).get_by_role('link', name='استخدام هذا النموذج', exact=True)
        version_link.click()
        assert page.get_by_label('حي البلاغ', exact=True).input_value() == 'n11'
        assert page.get_by_label('الفني (المسند إليه للبلاغ / المبلغ للمخالفة)', exact=True).input_value() == str(fixtures['technician'])
        page.get_by_role('button', name='حفظ التقرير ومعاينته', exact=True).click()
        page.wait_for_url(re.compile(r'.*/staff/generated-reports/\d+/print/$'))
        saved_url = page.url
        text = page.locator('.official-paper').inner_text()
        assert 'عدد البلاغات: 1' in text and 'نطاق التقرير وقت الإصدار' in text and fixtures['date'] in text
        django("from portal.models import Complaint;d=json.load(sys.stdin);Complaint.objects.filter(pk=d['complaint']).update(status='received')", fixtures)
        page.reload()
        assert page.locator('.official-paper').inner_text() == text
        assert page.request.get(saved_url).headers['cache-control'] == 'no-store, private'
        assert page.request.get(BASE + '/staff/reports/?date_to=invalid').status == 400
        page.goto(BASE + '/staff/report-templates/new/')
        page.get_by_label('اسم النموذج', exact=True).fill('نموذج ملف تالف خيالي')
        page.get_by_label('عنوان التقرير', exact=True).fill('عنوان محفوظ')
        page.get_by_label('نص النموذج', exact=True).fill('نص باق في المحرر')
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('word/document.xml', '<document>fictional</document>')
        payload = bytearray(stream.getvalue())
        name_size, extra_size = struct.unpack_from('<HH', payload, 26)
        offset = 30 + name_size + extra_size
        payload[offset] = (payload[offset] & ~7) | 7
        page.get_by_label('استيراد نص من Word (اختياري)', exact=True).set_input_files({'name': 'broken.docx', 'mimeType': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'buffer': bytes(payload)})
        with page.expect_response(lambda response: response.url.endswith('/staff/report-templates/new/') and response.request.method == 'POST') as response:
            page.get_by_role('button', name='حفظ النموذج للمراجعة', exact=True).click()
        assert response.value.status == 400
        assert 'تعذر قراءة نص النموذج' in page.locator('body').inner_text()
        assert page.get_by_label('نص النموذج', exact=True).input_value() == 'نص باق في المحرر'
        assert not errors, errors
        browser.close()
    print(json.dumps({'passed': 4, 'checks': ['Scope filters pass through live report, chooser and generation',
        'Saved scope and counts remain immutable after workflow changes', 'Invalid scope and broken deflate return 400 with editor fields preserved',
        'Report filters are usable at 320/390/768/1365px and private prints are not cached']}))
finally:
    if server:
        server.terminate()
        server.wait(timeout=10)
    django("from portal.models import User,Complaint,SavedReport,ReportTemplate;d=json.load(sys.stdin);users=User.objects.filter(username__startswith=d['username']);"
        "SavedReport.objects.filter(created_by__in=users).delete();ReportTemplate.objects.filter(created_by__in=users).delete();"
        "Complaint.objects.filter(owner__in=users).delete();users.delete()", {'username': username})
