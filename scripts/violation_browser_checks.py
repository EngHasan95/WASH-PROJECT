"""Exercise technician violation capture during a real browser network outage."""
import json
import secrets
import subprocess
import tempfile
import uuid
import time
from urllib.request import urlopen
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8002"
username = "browser_violation_" + uuid.uuid4().hex[:10]
password = secrets.token_urlsafe(32)
server = None


def django(code, data):
    result = subprocess.run([str(ROOT / ".venv/bin/python"), "-c",
        "import os,json,sys;os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings');import django;django.setup();" + code],
        input=json.dumps(data), text=True, capture_output=True, cwd=ROOT, check=True)
    return json.loads(result.stdout) if result.stdout.strip() else None


try:
    user_id = django("from portal.models import User; d=json.load(sys.stdin);u=User.objects.create_user(d['username'],password=d['password'],first_name='فني اختبار',role='technician');"
        "User.objects.create_user(d['username']+'_finance',password=d['password'],first_name='مالية اختبار',role='finance');"
        "User.objects.create_user(d['username']+'_director',password=d['password'],first_name='مدير اختبار',role='director');"
        "[User.objects.create_user(d['username']+suffix,password=d['password'],first_name='موظف خيالي',role=role) for suffix,role in [('_manager','system_manager'),('_secretary','secretariat'),('_followup','followup')]];print(json.dumps(u.pk))",
        {"username": username, "password": password})
    server = subprocess.Popen([str(ROOT / ".venv/bin/python"), "manage.py", "runserver", "127.0.0.1:8002", "--noreload"],
        cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(100):
        try:
            if urlopen(BASE + "/health/", timeout=.5).status == 200:
                break
        except Exception:
            time.sleep(.1)
    else:
        raise RuntimeError("Test server did not become ready")
    with tempfile.TemporaryDirectory(prefix="wash-violation-", dir=ROOT / ".local") as profile, sync_playwright() as playwright:
        browser = playwright.chromium.launch_persistent_context(profile, executable_path="/usr/bin/chromium",
            headless=True, args=["--no-sandbox"], viewport={"width": 390, "height": 844})
        page = browser.pages[0] if browser.pages else browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(BASE + "/accounts/login/")
        page.get_by_label("اسم المستخدم", exact=True).fill(username)
        page.get_by_label("كلمة المرور", exact=True).fill(password)
        page.get_by_role("button", name="دخول", exact=True).click()
        page.wait_for_url("**/workspace/")
        page.wait_for_function("() => navigator.serviceWorker.controller !== null")
        page.goto(BASE + "/staff/violations/new/")
        page.wait_for_function("() => document.querySelector('#violation-save')?.disabled === false")
        for width in (320, 390, 768, 1365):
            page.set_viewport_size({"width": width, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
        page.set_viewport_size({"width": 390, "height": 844})
        output = ROOT / "test-results"
        output.mkdir(exist_ok=True)
        page.screenshot(path=str(output / "violation-new-phone.png"), full_page=True)
        browser.set_offline(True)
        page.reload()
        page.wait_for_function("() => document.querySelector('#violation-save')?.disabled === false")
        assert page.url == BASE + "/staff/violations/new/"
        page.locator("#v-person").fill("شخص تجريبي")
        page.locator("#v-area").fill("منطقة خيالية")
        page.locator("#v-address").fill("عنوان اختبار")
        page.locator("#v-kind").select_option("random_connection")
        page.locator("#v-activity").select_option("government")
        page.locator("#v-consumption").fill("12.5")
        page.locator("#v-days").fill("30")
        page.locator("#violation-save").click()
        page.wait_for_function("() => document.querySelector('#violation-local-list').textContent.includes('شخص تجريبي')")
        entries = page.evaluate("id => WashOutbox.listViolations(id)", user_id)
        assert len(entries) == 1 and entries[0]["status"] == "pending"
        page.reload()
        page.wait_for_function("() => document.querySelector('#violation-local-list').textContent.includes('شخص تجريبي')")
        browser.set_offline(False)
        page.evaluate("() => WashOutbox.sync()")
        page.wait_for_function("id => WashOutbox.listViolations(id).then(items => items[0]?.status === 'synced')", arg=user_id)
        entries = page.evaluate("id => WashOutbox.listViolations(id)", user_id)
        assert entries[0]["receipt"]["reference"].startswith("V-MRB-")
        page.goto(BASE + "/staff/violations/")
        assert entries[0]["receipt"]["reference"] in page.locator("body").inner_text()
        page.screenshot(path=str(output / "violation-register-phone.png"), full_page=True)
        page.evaluate("() => WashOutbox.setIdentity({id: -1, role: 'citizen', name: 'اختبار'})")
        browser.set_offline(True)
        page.goto(BASE + "/staff/violations/new/")
        page.wait_for_selector("[data-violation-denied]", state="visible")
        assert page.locator("[data-violation-shell]").is_hidden()
        browser.set_offline(False)

        def login(suffix):
            page.goto(BASE + "/accounts/login/")
            page.get_by_label("اسم المستخدم", exact=True).fill(username + suffix)
            page.get_by_label("كلمة المرور", exact=True).fill(password)
            page.get_by_role("button", name="دخول", exact=True).click()
            page.wait_for_url("**/workspace/")
            page.goto(BASE + entries[0]["receipt"]["detail_url"])

        login("_manager")
        page.get_by_label("ملاحظة الإحالة", exact=True).fill("واقعة اختبارية محالة للسكرتارية")
        page.get_by_role("button", name="إحالة للسكرتارية", exact=True).click()
        login("_secretary")
        from datetime import date
        for kind, subject, official_number in [("minutes", "محضر معاينة تجريبي", "TEST-MINUTES-1"), ("letter", "مذكرة إحالة واقعة ميدانية — نموذج تجريبي", "TEST-LETTER-1")]:
            page.get_by_role("link", name="تحرير محضر أو مذكرة", exact=True).click()
            page.get_by_label("نوع المستند", exact=True).select_option(kind)
            page.get_by_label("الموضوع", exact=True).fill(subject)
            page.get_by_label("رقم الصادر أو المحضر", exact=True).fill(official_number)
            page.get_by_label("تاريخ المستند", exact=True).fill(date.today().isoformat())
            page.get_by_label("الجهة المخاطبة", exact=True).fill("جهة أمنية خيالية للاختبار" if kind == "letter" else "")
            page.get_by_label("نص المحضر أو المذكرة", exact=True).fill("إشارة إلى الواقعة الميدانية المسجلة في النظام، نرفق بيانات المعاينة لاتخاذ الإجراءات اللازمة وفق السياسة المعتمدة.\n\nهذا مستند تجريبي ببيانات خيالية، ولا يمثل مخاطبة فعلية لأي جهة.")
            for width in (320, 390, 768, 1365):
                page.set_viewport_size({"width": width, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), ("document form", width)
            page.set_viewport_size({"width": 390, "height": 844})
            page.get_by_role("button", name="حفظ المسودة", exact=True).click()
            page.get_by_role("button", name="إصدار النسخة وحفظها", exact=True).click()
            assert page.get_by_text("صادرة من النظام", exact=True).count() == 1
            if kind == "letter":
                page.screenshot(path=str(output / "memorandum-phone.png"), full_page=True)
                preview_url = BASE + page.get_by_role("link", name="عرض نسخة الطباعة", exact=True).get_attribute("href")
                response = page.request.get(preview_url)
                assert response.status == 200 and response.headers["cache-control"] == "no-store, private"
                preview = browser.new_page()
                preview.goto(preview_url)
                preview.evaluate("() => document.fonts.ready")
                for width in (320, 390, 900):
                    preview.set_viewport_size({"width": width, "height": 1100})
                    assert preview.evaluate("document.documentElement.scrollWidth <= innerWidth"), ("print preview", width)
                preview.screenshot(path=str(output / "memorandum-print.png"), full_page=True)
                preview.pdf(path=str(output / "violation-memorandum.pdf"), format="A4", print_background=True, prefer_css_page_size=True)
                preview.close()
            page.get_by_role("link", name="← ملف المخالفة", exact=True).click()
        page.get_by_label("ملخص المحضر والمراسلات", exact=True).fill("صدر المحضر والمذكرة التجريبيان وتمت مراجعة بيانات الواقعة")
        page.get_by_label("الجهة المخاطبة", exact=True).fill("جهة أمنية خيالية للاختبار")
        page.get_by_label("رقم المذكرة أو المحضر", exact=True).fill("TEST-LETTER-1")
        page.get_by_role("button", name="إحالة لقسم المتابعة", exact=True).click()
        login("_followup")
        page.get_by_label("نتائج النزول والإجراءات الميدانية", exact=True).fill("نتائج تجريبية: تم التعامل مع الواقعة وإعادة النتيجة للفنية والمالية")
        page.get_by_role("button", name="إعادة النتائج للفنية والمالية", exact=True).click()
        login("_finance")
        memo = page.request.get(BASE + "/api/staff/violations/fee-memo/")
        assert memo.status in (200, 404) and memo.headers["cache-control"] == "no-store, private"
        assert page.get_by_text("تعرفة الحكومي المعتمدة وفق رسوم التجاري:", exact=False).count() == 1
        page.get_by_label("كمية المياه المعتمدة للاحتساب (متر مكعب)", exact=True).fill("10.5")
        page.get_by_label("مرجع اعتماد الكمية وأساس التسوية", exact=True).fill("اعتماد تجريبي TEST-1")
        page.get_by_role("button", name="اعتماد الرسوم بواسطة المالية", exact=True).click()
        page.get_by_label("المبلغ المسدد (ريال يمني)", exact=True).fill("329200")
        page.get_by_label("رقم سند التحصيل", exact=True).fill("TEST-RECEIPT-1")
        from datetime import date
        page.get_by_label("تاريخ السداد", exact=True).fill(date.today().isoformat())
        assert page.get_by_role("button", name="إغلاق الملف بواسطة المدير الفني", exact=True).count() == 0
        page.get_by_role("button", name="تأكيد الدفع بواسطة المالية", exact=True).click()
        page.wait_for_selector(".notice.success")
        assert page.get_by_text("أكدت المالية السداد", exact=True).count() >= 1
        login("_director")
        page.get_by_label("نتيجة المعالجة وأساس إغلاق الملف", exact=True).fill("أزيل الربط المخالف، وأُكد السداد، واستكملت المعالجة التجريبية.")
        page.get_by_label("أؤكد استكمال معالجة المخالفة وتطبيق سياسة المؤسسة", exact=True).check()
        for width in (320, 390, 768, 1365):
            page.set_viewport_size({"width": width, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), ("settlement", width)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(output / "violation-settlement-phone.png"), full_page=True)
        page.get_by_role("button", name="إغلاق الملف بواسطة المدير الفني", exact=True).click()
        page.wait_for_selector(".notice.success")
        assert page.get_by_text("مغلقة بعد التسوية", exact=True).count() == 1
        page.screenshot(path=str(output / "violation-closed-phone.png"), full_page=True)
        assert not errors, errors
        browser.close()
    print(json.dumps({"passed": 3, "checks": ["Offline technician violation survives reload and syncs once when online",
        "Secretariat issues printable minutes and memorandum; follow-up returns field results",
        "Finance assesses and confirms payment; technical director alone closes after treatment confirmation"]}, ensure_ascii=False))
finally:
    if server:
        server.terminate()
        server.wait(timeout=10)
    django("from portal.models import User,Violation,Draft;d=json.load(sys.stdin);users=User.objects.filter(username__startswith=d['username']);"
           "Violation.objects.filter(reporter__in=users).delete();Draft.objects.filter(owner__in=users).delete();users.delete()",
           {"username": username})
