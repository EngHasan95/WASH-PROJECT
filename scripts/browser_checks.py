"""Real Chromium checks using temporary users and a disposable persistent browser profile.

Run with the environment's python3 (which provides Playwright). A dedicated Django server
is started and stopped to test a real unavailable server, including worker connections.
No fixture passwords are printed or saved. Screenshots are written to ignored test-results/.
"""
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from urllib.error import URLError
from urllib.request import urlopen
import uuid

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
BASE = "http://127.0.0.1:8001"
OUTPUT = ROOT / "test-results"
OUTPUT.mkdir(exist_ok=True)
prefix = "browser_" + uuid.uuid4().hex[:10]
password = secrets.token_urlsafe(32)
test_photo = subprocess.run(
    [str(ROOT / ".venv/bin/python"), "-c", "import io,sys;from PIL import Image;out=io.BytesIO();Image.new('RGB',(100,70),'#60cab6').save(out,format='PNG');sys.stdout.buffer.write(out.getvalue())"],
    capture_output=True, check=True,
).stdout
created_employee = prefix + "_secretary"
users = {}


def django_call(code, payload):
    result = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "-c", "import os,json,sys; os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings'); import django; django.setup(); " + code],
        input=json.dumps(payload), text=True, capture_output=True, cwd=ROOT, check=True,
    )
    return json.loads(result.stdout) if result.stdout.strip() else None


def fixtures():
    return django_call(
        "from portal.models import User; data=json.load(sys.stdin); result={}; "
        "\nfor role,name in [('citizen','مستفيد تجريبي'),('director','مدير فني تجريبي'),('technician','فني تجريبي')]:"
        "\n user=User.objects.create_user(data['prefix']+'_'+role,password=data['password'],first_name=name,role=role); result[role]={'id':user.pk,'username':user.username,'name':name,'role':role}"
        "\nuser=User.objects.create_user(data['prefix']+'_other',password=data['password'],first_name='مستفيد آخر تجريبي',role='citizen'); result['other']={'id':user.pk,'username':user.username,'name':user.first_name,'role':user.role}"
        "\nprint(json.dumps(result))", {"prefix": prefix, "password": password},
    )


def cleanup():
    django_call(
        "from portal.models import User,Draft,AccountEvent,Complaint,ComplaintPhoto; from django.db.models import Q; data=json.load(sys.stdin); "
        "ids=list(User.objects.filter(username__startswith=data['prefix']+'_').values_list('id',flat=True)); "
        "photos=ComplaintPhoto.objects.filter(complaint__owner_id__in=ids); "
        "[photo.image.delete(save=False) for photo in photos]; Complaint.objects.filter(owner_id__in=ids).delete(); "
        "Draft.objects.filter(owner_id__in=ids).delete(); AccountEvent.objects.filter(Q(actor_id__in=ids)|Q(target_id__in=ids)).delete(); "
        "User.objects.filter(id__in=ids).delete()", {"prefix": prefix},
    )


def log_in(page, username, secret=password):
    page.goto(BASE + "/accounts/login/")
    page.get_by_label("اسم المستخدم", exact=True).fill(username)
    page.get_by_label("كلمة المرور", exact=True).fill(secret)
    page.get_by_role("button", name="دخول", exact=True).click()
    page.wait_for_url("**/workspace/")
    expected_id = next(user["id"] for user in users.values() if user["username"] == username)
    wait_until(lambda: page.evaluate("() => window.WashOutbox && WashOutbox.identity()") is not None and page.evaluate("WashOutbox.identity()")["id"] == expected_id)


def save_draft(page, title):
    if not page.locator("#draft-title").is_visible():
        page.locator("[data-legacy-drafts] > summary").click()
    page.get_by_label("عنوان المسودة", exact=True).fill(title) if page.locator("#draft-title").count() and page.get_by_label("عنوان المسودة", exact=True).count() else page.locator("#draft-title").fill(title)
    page.locator("#draft-description").fill("ملاحظات تجريبية للتحقق من الحفظ والمزامنة.")
    page.locator("#save-draft").click()
    page.wait_for_function("() => document.querySelector('#draft-title').value === ''")


def entries(page, owner):
    return page.evaluate("id => WashOutbox.list(id)", owner)


def complaint_entries(page, owner):
    return page.evaluate("id => WashOutbox.listComplaints(id)", owner)


def save_offline_complaint(page):
    page.wait_for_function("() => document.querySelector('#save-complaint').disabled === false")
    page.get_by_label("نوع البلاغ", exact=True).select_option("other")
    page.get_by_label("اكتب نوع البلاغ", exact=True).fill("بلاغ تجريبي عند الانقطاع")
    page.get_by_label("المنطقة أو الحي", exact=True).select_option("other")
    page.get_by_label("اكتب المنطقة أو الحي", exact=True).fill("منطقة اختبار خيالية")
    page.get_by_label("وصف البلاغ", exact=True).fill("تفاصيل تجريبية لاختبار حفظ البيانات والصور دون اتصال.")
    page.get_by_label("الموقع بالتفصيل", exact=True).fill("شارع تجريبي قرب مدرسة الاختبار")
    page.get_by_label("أقرب معلم بارز", exact=True).fill("مدرسة الاختبار الخيالية")
    page.get_by_label("اسم مقدم البلاغ", exact=True).fill("مستفيد تجريبي")
    page.get_by_label("رقم الهاتف", exact=True).fill("777000000")
    page.locator("#complaint-photos").set_input_files({"name": "field-photo.png", "mimeType": "image/png", "buffer": test_photo})
    page.get_by_role("button", name="إضافة موقعي الحالي", exact=True).click()
    page.wait_for_function("() => document.querySelector('#complaint-latitude').value !== ''")
    page.locator("#save-complaint").click()
    page.wait_for_function("() => document.querySelector('#complaint-description').value === ''")


def wait_until(predicate, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(.1)
    raise AssertionError("Expected browser state was not reached within the timeout.")


def completed_sync(page):
    result = {}
    def finished():
        result.update(page.evaluate("WashOutbox.sync()"))
        return result["state"] != "busy"
    wait_until(finished)
    return result


errors = []
checks = []
context = None
test_server = None
server_log = (ROOT / ".local/browser-server.log").open("w")


def start_server():
    global test_server
    test_server = subprocess.Popen(
        [str(ROOT / ".venv/bin/python"), "manage.py", "runserver", "127.0.0.1:8001", "--noreload"],
        cwd=ROOT, stdout=server_log, stderr=subprocess.STDOUT,
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if test_server.poll() is not None:
            raise RuntimeError("Dedicated test server did not start; inspect .local/browser-server.log.")
        try:
            with urlopen(BASE + "/health/", timeout=1) as response:
                if response.status == 200:
                    return
        except (URLError, TimeoutError):
            pass
        time.sleep(.1)
    raise RuntimeError("Dedicated test server failed its readiness check.")


def stop_server():
    global test_server
    if test_server is not None:
        test_server.terminate()
        test_server.wait(timeout=10)
        test_server = None


try:
    users = fixtures()
    start_server()
    with tempfile.TemporaryDirectory(prefix="wash-browser-", dir=ROOT / ".local") as profile, sync_playwright() as playwright:
        def launch(offline=False):
            result = playwright.chromium.launch_persistent_context(
                profile, executable_path="/usr/bin/chromium", headless=True,
                args=["--no-sandbox"], viewport={"width": 1365, "height": 950}, offline=offline,
            )
            result.on("page", lambda new_page: new_page.on("pageerror", lambda error: errors.append(str(error))))
            return result

        context = launch()
        page = context.pages[0] if context.pages else context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        context.route(BASE + "/migration-probe/", lambda route: route.fulfill(body="<!doctype html><title>Storage upgrade check</title>", content_type="text/html"))
        page.goto(BASE + "/migration-probe/")
        page.evaluate("""() => new Promise((resolve,reject) => {
          const request = indexedDB.open('wash-device-drafts',1);
          request.onupgradeneeded = () => {request.result.createObjectStore('drafts',{keyPath:'client_id'});request.result.createObjectStore('settings',{keyPath:'key'});};
          request.onsuccess = () => {const db=request.result;const tx=db.transaction('drafts','readwrite');
            tx.objectStore('drafts').put({client_id:'legacy-upgrade-probe',owner_id:-99,title:'old draft',description:'preserve me',created_at:new Date().toISOString(),status:'pending'});
            tx.oncomplete=()=>{db.close();resolve();};tx.onerror=()=>reject(tx.error);};request.onerror=()=>reject(request.error);
        })""")
        context.unroute(BASE + "/migration-probe/")
        response = page.goto(BASE)
        assert response.status == 200
        page.evaluate("document.fonts.ready"); page.screenshot(path=str(OUTPUT / "01-home-desktop.png"), full_page=True)
        checks.append("Arabic desktop shell")
        wait_until(lambda: page.evaluate("() => window.WashOutbox && WashOutbox.list(-99).then(items => items.length)") == 1)
        checks.append("IndexedDB upgrade preserves stage 1 drafts")
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(OUTPUT / "02-home-phone.png"), full_page=True)
        checks.append("Phone layout without horizontal overflow")

        log_in(page, users["citizen"]["username"])
        page.wait_for_function("() => navigator.serviceWorker.controller !== null")
        wait_until(lambda: page.evaluate("() => caches.keys().then(keys => keys.find(k => k.startsWith('wash-shell-'))).then(key => key && caches.open(key).then(c => c.match('/app-shell/'))).then(Boolean)"))
        stop_server()
        context.set_offline(True)
        page.goto(BASE + "/workspace/")
        page.wait_for_selector("#complaint-form")
        assert page.url == BASE + "/workspace/"
        page.wait_for_function("() => document.querySelector('#save-draft').disabled === false")
        save_draft(page, "مسودة عند انقطاع الاتصال")
        assert entries(page, users["citizen"]["id"])[0]["status"] == "pending"
        page.reload()
        page.wait_for_function("() => document.querySelector('#draft-list').textContent.includes('مسودة عند انقطاع الاتصال')")
        page.screenshot(path=str(OUTPUT / "03-offline-phone.png"), full_page=True)
        checks.append("Offline navigation, local save and reload persistence")
        context.grant_permissions(["geolocation"])
        context.set_geolocation({"latitude": 15.48393, "longitude": 45.32205})
        save_offline_complaint(page)
        wait_until(lambda: len(complaint_entries(page, users["citizen"]["id"])) == 1)
        local = complaint_entries(page, users["citizen"]["id"])[0]
        assert local["status"] == "pending" and "receipt" not in local
        assert local["data"]["subscription_number"] == "" and local["data"]["latitude"] == "15.4839300"
        assert page.evaluate("id => WashOutbox.listComplaints(id).then(items => items[0].photos[0].blob.size)", users["citizen"]["id"]) == len(test_photo)
        page.screenshot(path=str(OUTPUT / "10-intake-offline-phone.png"), full_page=True)
        checks.append("Complete offline intake preserves optional GPS and photo with no premature receipt")

        context.close()
        context = launch(offline=True)
        page = context.pages[0] if context.pages else context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(BASE + "/workspace/")
        page.wait_for_function("() => document.querySelector('#draft-list').textContent.includes('مسودة عند انقطاع الاتصال')")
        assert entries(page, users["citizen"]["id"])[0]["status"] == "pending"
        assert complaint_entries(page, users["citizen"]["id"])[0]["status"] == "pending"
        assert page.evaluate("id => WashOutbox.listComplaints(id).then(items => items[0].photos[0].blob.size)", users["citizen"]["id"]) == len(test_photo)
        checks.append("Local drafts survive a complete browser restart")
        checks.append("Full complaint data and image survive a complete offline browser restart")
        start_server()
        context.set_offline(False)
        try:
            wait_until(lambda: len(entries(page, users["citizen"]["id"])) == 1 and entries(page, users["citizen"]["id"])[0]["status"] == "synced")
        except AssertionError:
            print(json.dumps({"online": page.evaluate("navigator.onLine"), "sync_message": page.locator("#sync-feedback").inner_text(), "locks": page.evaluate("navigator.locks.query()"), "page_errors": errors}, ensure_ascii=False))
            raise
        completed_sync(page)
        assert len(context.request.get(BASE + "/api/drafts/").json()["drafts"]) == 1
        checks.append("Automatic reconnect sync with no duplicate after retry")
        wait_until(lambda: complaint_entries(page, users["citizen"]["id"])[0]["status"] == "synced")
        receipt = context.request.get(BASE + "/api/complaints/").json()["complaints"][0]
        assert receipt["reference"].startswith("MRB-") and receipt["status"] == "received" and len(receipt["photos"]) == 1
        assert len(context.request.get(BASE + receipt["photos"][0]["url"]).body()) > 0
        checks.append("Reconnect uploads complete intake and private photo and assigns one receipt")

        context.route("**/api/drafts/sync/", lambda route: route.abort())
        page.evaluate("() => WashOutbox.save('مسودة خاصة بالحساب الأول', 'مسودة للتحقق من منع الإرسال باسم حساب آخر.')")
        log_in(page, users["technician"]["username"])
        context.unroute("**/api/drafts/sync/")
        assert len(context.request.get(BASE + "/api/drafts/").json()["drafts"]) == 0
        assert "مسودة خاصة بالحساب الأول" not in page.locator("#draft-list").inner_text()
        page.evaluate("user => WashOutbox.setIdentity(user)", users["citizen"])
        assert completed_sync(page)["state"] == "different-account"
        assert context.request.get(BASE + receipt["photos"][0]["url"]).status == 403
        assert len(context.request.get(BASE + "/api/drafts/").json()["drafts"]) == 0
        page.reload()
        wait_until(lambda: page.evaluate("WashOutbox.identity()")["role"] == "technician")
        save_draft(page, "مسودة مخالفة للفني")
        wait_until(lambda: len(entries(page, users["technician"]["id"])) == 1 and entries(page, users["technician"]["id"])[0]["status"] == "synced")
        assert context.request.get(BASE + "/api/drafts/").json()["drafts"][0]["kind"] == "violation"
        checks.append("Account isolation and technician-only violation draft type")
        checks.append("Employee session cannot open beneficiary receipt photo")

        cache_urls = page.evaluate("caches.keys().then(keys => keys.find(k => k.startsWith('wash-shell-'))).then(key => caches.open(key)).then(c => c.keys()).then(keys => keys.map(k => new URL(k.url).pathname))")
        assert all(url == "/app-shell/" or url.startswith("/static/") for url in cache_urls)
        checks.append("Cache contains no authenticated pages or API responses")
        page.get_by_role("button", name="خروج", exact=True).click()
        page.wait_for_url(BASE + "/")
        assert page.evaluate("WashOutbox.identity()") is None
        checks.append("Logout clears the active device identity")

        log_in(page, users["citizen"]["username"])
        assert page.goto(BASE + "/staff/").status == 403
        assert page.goto(BASE + "/staff/employees/").status == 403
        checks.append("Citizen direct URLs cannot open internal workspaces")
        page.goto(BASE + "/workspace/")
        page.wait_for_function("() => document.querySelector('#save-complaint').disabled === false")
        baseline = complaint_entries(page, users["citizen"]["id"])[0]["data"]
        # Lose the reply after the real server accepts the request, then retry the same UUID.
        def lose_acknowledgment(route):
            response = route.fetch()
            assert response.status in (200, 201)
            route.abort()
        context.route("**/api/complaints/sync/", lose_acknowledgment)
        page.evaluate("data => WashOutbox.saveComplaint({...data, description:'بلاغ تجريبي لاختبار ضياع الرد'}, [])", baseline)
        assert completed_sync(page)["state"] == "offline"
        assert len(context.request.get(BASE + "/api/complaints/").json()["complaints"]) == 2
        assert complaint_entries(page, users["citizen"]["id"])[0]["status"] == "pending"
        context.unroute("**/api/complaints/sync/")
        completed_sync(page)
        assert len(context.request.get(BASE + "/api/complaints/").json()["complaints"]) == 2
        assert complaint_entries(page, users["citizen"]["id"])[0]["status"] == "synced"
        checks.append("Lost server acknowledgment retry preserves one official complaint and receipt")
        # Keep rejected input on the device and correct it using the ordinary form.
        page.evaluate("data => WashOutbox.saveComplaint({...data, complaint_type:'invalid', other_type:'', description:'بلاغ تجريبي يحتاج تصحيحًا'}, [])", baseline)
        completed_sync(page)
        assert complaint_entries(page, users["citizen"]["id"])[0]["status"] == "review"
        page.locator("#sync-complaints").click()
        page.get_by_role("button", name="تصحيح البيانات", exact=True).wait_for()
        page.get_by_role("button", name="تصحيح البيانات", exact=True).click()
        page.wait_for_function("() => document.querySelector('#complaint-description').value === 'بلاغ تجريبي يحتاج تصحيحًا'")
        page.get_by_label("نوع البلاغ", exact=True).select_option("leak")
        assert page.locator("#complaint-form").evaluate("form => form.checkValidity()")
        page.locator("#save-complaint").click()
        wait_until(lambda: len(context.request.get(BASE + "/api/complaints/").json()["complaints"]) == 3)
        assert all(item["status"] != "review" for item in complaint_entries(page, users["citizen"]["id"]))
        checks.append("Rejected complaint remains editable with atomic correction on the device")
        page.set_viewport_size({"width": 1365, "height": 950})
        page.screenshot(path=str(OUTPUT / "11-intake-desktop.png"), full_page=True)
        for width in [320, 360, 390, 768, 1024, 1365]:
            page.set_viewport_size({"width": width, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), width
        checks.append("Complete intake layout fits six desktop and phone widths")
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(OUTPUT / "12-intake-phone.png"), full_page=True)
        # An expired server session must leave a complete complaint queued until its owner logs in.
        context.add_cookies([{"name": "sessionid", "value": "expired-intake-test", "url": BASE, "httpOnly": True, "sameSite": "Lax"}])
        page.evaluate("data => WashOutbox.saveComplaint({...data, description:'بلاغ محفوظ أثناء انتهاء الجلسة'}, [])", baseline)
        assert completed_sync(page)["state"] == "login"
        assert complaint_entries(page, users["citizen"]["id"])[0]["status"] == "pending"
        log_in(page, users["citizen"]["username"])
        wait_until(lambda: complaint_entries(page, users["citizen"]["id"])[0]["status"] == "synced")
        assert len(context.request.get(BASE + "/api/complaints/").json()["complaints"]) == 4
        checks.append("Expired session retains full intake until the same beneficiary signs in again")
        # Stage 3: full register, server-authorized detail, local fallback and account isolation.
        page.goto(BASE + "/workspace/complaints/")
        assert page.locator(".register-row").count() == 4
        page.get_by_label("رقم البلاغ، الاسم أو الهاتف", exact=True).fill(receipt["reference"])
        page.get_by_role("button", name="عرض النتائج", exact=True).click()
        assert page.locator(".register-row").count() == 1
        page.locator(".register-filter-details > summary").click()
        page.get_by_label("نوع البلاغ", exact=True).select_option("leak")
        page.get_by_role("button", name="عرض النتائج", exact=True).click()
        assert page.locator(".register-row").count() == 0
        page.get_by_role("link", name="مسح التصفية", exact=True).click()
        assert page.locator(".register-row").count() == 4
        checks.append("Beneficiary register searches reference and combines filters with clear results")
        page.set_viewport_size({"width": 1365, "height": 950})
        page.evaluate("document.fonts.ready")
        page.screenshot(path=str(OUTPUT / "stage3-register-desktop.png"), full_page=True)
        for width in [320, 360, 390, 768, 1024, 1365]:
            page.set_viewport_size({"width": width, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), ("register", width)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(OUTPUT / "stage3-register-phone.png"), full_page=True)
        assert page.goto(BASE + receipt["detail_url"]).status == 200
        assert page.locator(".detail-gallery img").count() == 1
        page.wait_for_function("() => document.querySelector('.detail-gallery img').naturalWidth > 0")
        assert page.locator(".detail-reference").text_content() == receipt["reference"]
        for width in [320, 360, 390, 768, 1024, 1365]:
            page.set_viewport_size({"width": width, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), ("detail", width)
        page.set_viewport_size({"width": 1365, "height": 950})
        page.screenshot(path=str(OUTPUT / "stage3-detail-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(OUTPUT / "stage3-detail-phone.png"), full_page=True)
        checks.append("Private detail photos load and register/detail fit six viewport widths")
        stop_server()
        context.set_offline(True)
        page.goto(BASE + "/workspace/complaints/")
        page.wait_for_function("() => document.querySelector('#local-register-records').children.length === 4")
        assert not page.locator("#complaint-form").is_visible()
        page.locator("#local-search").fill(receipt["reference"])
        page.wait_for_function("() => document.querySelector('#local-register-records').children.length === 1")
        page.locator("#local-register-records a").click()
        page.wait_for_function("() => document.querySelector('#local-register-records .detail-gallery img')?.naturalWidth > 0")
        assert page.url == BASE + receipt["detail_url"]
        assert not page.locator("#complaint-form").is_visible()
        page.screenshot(path=str(OUTPUT / "stage3-detail-local-phone.png"), full_page=True)
        checks.append("Real server outage keeps normal register/detail URLs and own local image available")
        start_server()
        context.set_offline(False)
        page.reload()
        assert page.locator(".detail-reference").text_content() == receipt["reference"]
        log_in(page, users["other"]["username"])
        assert page.goto(BASE + receipt["detail_url"]).status == 404
        assert context.request.get(BASE + receipt["photos"][0]["url"]).status == 404
        page.goto(BASE + "/workspace/complaints/")
        assert page.locator(".register-row").count() == 0
        stop_server()
        context.set_offline(True)
        page.goto(BASE + receipt["detail_url"])
        page.wait_for_selector("#local-register", state="visible")
        page.wait_for_function("() => document.querySelector('#local-register-records').textContent.includes('غير محفوظة')")
        assert page.locator("#local-register-records .receipt-reference").count() == 0
        checks.append("Second beneficiary cannot read another account's server or offline detail and image")
        start_server()
        context.set_offline(False)
        # Additional fictional records exercise real pagination without adding local records.
        django_call(
            "from portal.models import User,Complaint; import uuid; data=json.load(sys.stdin); owner=User.objects.get(pk=data['id']); "
            "\nfor number in range(21): Complaint.objects.create(owner=owner,client_id=uuid.uuid4(),payload_digest='a'*64,reporter_name='مستفيد آخر تجريبي',phone='777111222',complaint_type='leak',neighborhood='n11',address='عنوان خيالي للاختبار',landmark='معلم تجريبي',description='بلاغ تجريبي لفحص التصفح')",
            {"id": users["other"]["id"]},
        )
        log_in(page, users["citizen"]["username"])
        page.goto(BASE + "/workspace/complaints/")
        assert page.locator(".register-row").count() == 4
        checks.append("Beneficiary register count excludes all other accounts after pagination fixtures")
        page.goto(BASE + "/workspace/")
        page.evaluate("document.fonts.ready")
        page.screenshot(path=str(OUTPUT / "05-beneficiary-workspace.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(OUTPUT / "06-beneficiary-phone.png"), full_page=True)
        page.goto(BASE + "/accounts/register/")
        page.goto(BASE + "/accounts/login/")
        page.evaluate("document.fonts.ready")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(OUTPUT / "07-login-phone.png"), full_page=True)
        checks.append("Beneficiary workspace and login fit a phone viewport")

        log_in(page, users["director"]["username"])
        page.goto(BASE + "/staff/complaints/")
        assert page.locator(".register-row").count() == 20
        page.get_by_role("link", name="التالي", exact=True).click()
        assert page.locator(".register-row").count() == 5
        page.get_by_label("رقم البلاغ، الاسم أو الهاتف", exact=True).fill(receipt["reference"])
        page.get_by_role("button", name="عرض النتائج", exact=True).click()
        assert page.locator(".register-row").count() == 1
        page.locator(".record-open").click()
        assert page.url.replace("/staff/", "/workspace/") == BASE + receipt["detail_url"]
        page.wait_for_function("() => document.querySelector('.detail-gallery img')?.naturalWidth > 0")
        assert "/api/staff/complaints/photos/" in page.locator(".detail-gallery img").get_attribute("src")
        checks.append("Director full register paginates and opens a beneficiary detail through private staff photo access")
        page.goto(BASE + "/staff/complaints/?q=" + receipt["reference"])
        page.set_viewport_size({"width": 1365, "height": 950})
        page.screenshot(path=str(OUTPUT / "stage3-director-desktop.png"), full_page=True)
        for width in [320, 360, 390, 768, 1024, 1365]:
            page.set_viewport_size({"width": width, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), ("director register", width)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(OUTPUT / "stage3-director-phone.png"), full_page=True)
        stop_server()
        context.set_offline(True)
        page.reload()
        page.wait_for_selector("#local-register", state="visible")
        page.wait_for_function("() => document.querySelector('#local-register-message').textContent.includes('لا يُحفظ')")
        assert page.locator("#local-register-records").text_content() == ""
        start_server()
        context.set_offline(False)
        cache_urls = page.evaluate("() => caches.keys().then(keys => Promise.all(keys.map(key => caches.open(key).then(c => c.keys())))).then(groups => groups.flat().map(k => new URL(k.url).pathname))")
        assert all(url == "/app-shell/" or url.startswith("/static/") for url in cache_urls)
        checks.append("Director mobile register requires network during outage and no private register, detail or image enters cache")
        page.goto(BASE + "/staff/reports/")
        assert page.get_by_role("heading", name="متابعة العمل", exact=True).count() == 1
        assert page.get_by_text("توزيع البلاغات").count() == 1
        for width in [320, 390, 768, 1365]:
            page.set_viewport_size({"width": width, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), ("director report", width)
        page.set_viewport_size({"width": 390, "height": 844})
        page.screenshot(path=str(OUTPUT / "director-report-phone.png"), full_page=True)
        checks.append("Director operational report shows real counts and fits phone widths")
        page.set_viewport_size({"width": 1365, "height": 950})
        page.goto(BASE + "/staff/employees/")
        page.get_by_label("اسم الموظف", exact=True).fill("موظف سكرتارية تجريبي")
        page.get_by_label("اسم المستخدم", exact=True).fill(created_employee)
        page.get_by_label("الدور الوظيفي", exact=True).select_option("secretariat")
        page.locator("#id_password1").fill(password)
        page.locator("#id_password2").fill(password)
        page.get_by_role("button", name="إنشاء الحساب", exact=True).click()
        page.wait_for_selector(".notice.success")
        assert page.get_by_role("cell", name=created_employee, exact=True).count() == 1
        page.screenshot(path=str(OUTPUT / "04-director-accounts.png"), full_page=True)
        checks.append("Director provisions a personal employee account")
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(OUTPUT / "08-director-phone.png"), full_page=True)
        page.goto(BASE + "/staff/")
        page.screenshot(path=str(OUTPUT / "09-staff-phone.png"), full_page=True)
        checks.append("Employee navigation and accounts remain usable on a phone")

        page.goto(BASE + "/accounts/login/")
        page.get_by_label("اسم المستخدم", exact=True).fill(created_employee)
        page.get_by_label("كلمة المرور", exact=True).fill(password)
        page.get_by_role("button", name="دخول", exact=True).click()
        page.wait_for_url("**/accounts/password/")
        new_password = secrets.token_urlsafe(32)
        page.locator("#id_old_password").fill(password)
        page.locator("#id_new_password1").fill(new_password)
        page.locator("#id_new_password2").fill(new_password)
        page.get_by_role("button", name="حفظ كلمة المرور", exact=True).click()
        page.wait_for_url("**/workspace/")
        assert page.goto(BASE + "/staff/").status == 200
        assert page.goto(BASE + "/staff/employees/").status == 403
        checks.append("First-login password change and secretariat role restrictions")
        assert not errors, "Browser page errors: " + "; ".join(errors)
        context.close()
        context = None
    print(json.dumps({"passed": len(checks), "checks": checks, "screenshots": str(OUTPUT)}, ensure_ascii=False, indent=2))
finally:
    if context is not None:
        try:
            context.close()
        except Exception:
            # Playwright already closes its browser during exception unwinding.
            pass
    cleanup()
    stop_server()
    server_log.close()
