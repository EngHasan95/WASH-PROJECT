import json
import uuid

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from .models import AccountEvent, Draft

User = get_user_model()


class FoundationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.citizen = User.objects.create_user("citizen", password="test-password-available", role=User.Role.CITIZEN)
        cls.other = User.objects.create_user("other", password="test-password-available", role=User.Role.CITIZEN)
        cls.director = User.objects.create_user("director", password="test-password-available", role=User.Role.DIRECTOR)
        cls.technician = User.objects.create_user("technician", password="test-password-available", role=User.Role.TECHNICIAN)

    def payload(self, owner=None, kind="complaint", **overrides):
        return {"owner_id": (owner or self.citizen).pk, "client_id": str(uuid.uuid4()), "kind": kind,
                "title": "انقطاع المياه", "description": "مسودة أولية في حي الروضة", **overrides}

    def sync(self, payload):
        return self.client.post(reverse("sync_draft"), json.dumps(payload), content_type="application/json")

    def test_public_home_and_offline_shell(self):
        self.assertContains(self.client.get("/"), 'dir="rtl"')
        self.assertContains(self.client.get("/app-shell/"), "مسوداتي")

    def test_anonymous_staff_access_requires_login(self):
        for path in ("/staff/", "/staff/employees/", "/workspace/"):
            self.assertEqual(self.client.get(path).status_code, 302)

    def test_citizen_cannot_access_staff_even_by_direct_url(self):
        self.client.force_login(self.citizen)
        for path in ("/staff/", "/staff/employees/"):
            self.assertEqual(self.client.get(path).status_code, 403)

    def test_all_employee_roles_have_private_staff_workspace(self):
        for role in (User.Role.TECHNICIAN, User.Role.EMPLOYEE, User.Role.SYSTEM_MANAGER,
                     User.Role.SECRETARIAT, User.Role.FOLLOWUP, User.Role.FINANCE):
            employee = User.objects.create_user(f"worker-{role}", role=role)
            self.client.force_login(employee)
            self.assertEqual(self.client.get("/staff/").status_code, 200)
            self.assertEqual(self.client.get("/staff/employees/").status_code, 403)

    def test_director_creates_employee_and_audit(self):
        self.client.force_login(self.director)
        response = self.client.post("/staff/employees/", {
            "first_name": "فني تجريبي", "username": "worker-new", "role": "technician",
            "phone": "", "password1": "Temporary-Marb-82!", "password2": "Temporary-Marb-82!",
        })
        self.assertEqual(response.status_code, 302)
        created = User.objects.get(username="worker-new")
        self.assertEqual(created.role, User.Role.TECHNICIAN)
        self.assertTrue(created.must_change_password)
        self.assertFalse(created.is_superuser)
        self.assertEqual(AccountEvent.objects.get(target=created).actor, self.director)

    def test_employee_cannot_create_other_accounts(self):
        self.client.force_login(self.technician)
        self.assertEqual(self.client.post("/staff/employees/", {}).status_code, 403)

    def test_public_registration_ignores_privilege_fields(self):
        response = self.client.post("/accounts/register/", {
            "first_name": "مستفيد تجريبي", "username": "new-citizen", "phone": "777123456",
            "password1": "Private-Marb-83!", "password2": "Private-Marb-83!",
            "role": "director", "is_staff": "true", "is_superuser": "true",
        })
        self.assertEqual(response.status_code, 302)
        created = User.objects.get(username="new-citizen")
        self.assertEqual(created.role, User.Role.CITIZEN)
        self.assertFalse(created.is_staff or created.is_superuser)

    def test_weak_password_does_not_create_account(self):
        self.client.post("/accounts/register/", {
            "first_name": "اختبار", "username": "weak", "phone": "777123456",
            "password1": "123", "password2": "123",
        })
        self.assertFalse(User.objects.filter(username="weak").exists())

    def test_temporary_password_must_change_before_workspace_or_sync(self):
        self.technician.must_change_password = True
        self.technician.save()
        self.client.force_login(self.technician)
        self.assertRedirects(self.client.get("/workspace/"), "/accounts/password/", fetch_redirect_response=False)
        self.assertEqual(self.sync(self.payload(self.technician, "violation")).status_code, 403)

    def test_password_change_unlocks_employee(self):
        self.technician.must_change_password = True
        self.technician.save()
        self.client.force_login(self.technician)
        response = self.client.post("/accounts/password/", {
            "old_password": "test-password-available", "new_password1": "Private-Marb-97!", "new_password2": "Private-Marb-97!",
        })
        self.assertEqual(response.status_code, 302)
        self.technician.refresh_from_db()
        self.assertFalse(self.technician.must_change_password)
        self.assertEqual(self.client.get("/workspace/").status_code, 200)

    def test_idempotent_sync_prevents_duplicates_and_preserves_content(self):
        self.client.force_login(self.citizen)
        payload = self.payload()
        self.assertEqual(self.sync(payload).status_code, 201)
        self.assertEqual(self.sync(payload).status_code, 200)
        self.assertEqual(Draft.objects.count(), 1)
        self.assertEqual(self.sync({**payload, "title": "محتوى مختلف"}).status_code, 409)
        self.assertEqual(Draft.objects.get().title, payload["title"])

    def test_citizen_cannot_sync_violation(self):
        self.client.force_login(self.citizen)
        self.assertEqual(self.sync(self.payload(kind="violation")).status_code, 403)
        self.assertEqual(Draft.objects.count(), 0)

    def test_employee_can_sync_violation(self):
        self.client.force_login(self.technician)
        self.assertEqual(self.sync(self.payload(self.technician, "violation")).status_code, 201)
        self.assertEqual(Draft.objects.get().owner, self.technician)

    def test_employee_cannot_sync_citizen_draft(self):
        self.client.force_login(self.technician)
        self.assertEqual(self.sync(self.payload(self.technician)).status_code, 403)

    def test_owner_mismatch_blocks_account_switch_misattribution(self):
        self.client.force_login(self.other)
        self.assertEqual(self.sync(self.payload()).status_code, 409)
        self.assertFalse(Draft.objects.exists())

    def test_same_uuid_is_scoped_by_owner(self):
        payload = self.payload()
        self.client.force_login(self.citizen)
        self.assertEqual(self.sync(payload).status_code, 201)
        self.client.force_login(self.other)
        self.assertEqual(self.sync({**payload, "owner_id": self.other.pk}).status_code, 201)
        self.assertEqual(Draft.objects.count(), 2)

    def test_draft_listing_is_private_to_owner(self):
        Draft.objects.create(owner=self.citizen, title="خاص", description="بيانات خاصة", kind="complaint")
        self.client.force_login(self.other)
        self.assertEqual(self.client.get("/api/drafts/").json()["drafts"], [])

    def test_api_requires_authentication(self):
        self.assertEqual(self.client.get("/api/session/").status_code, 401)
        self.assertEqual(self.client.get("/api/drafts/").status_code, 401)
        self.assertEqual(self.sync(self.payload()).status_code, 401)

    def test_csrf_is_enforced(self):
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.citizen)
        self.assertEqual(protected.post("/api/drafts/sync/", json.dumps(self.payload()), content_type="application/json").status_code, 403)

    def test_invalid_inputs_are_rejected_without_drafts(self):
        self.client.force_login(self.citizen)
        for bad in ([], {"title": "only title"}, self.payload(title="x" * 161), self.payload(description=""), self.payload(client_id="bad")):
            self.assertEqual(self.sync(bad).status_code, 400)
        self.assertFalse(Draft.objects.exists())

    def test_wrong_content_type_and_sync_get_are_rejected(self):
        self.client.force_login(self.citizen)
        self.assertEqual(self.client.post("/api/drafts/sync/", "raw", content_type="text/plain").status_code, 415)
        self.assertEqual(self.client.get("/api/drafts/sync/").status_code, 405)

    def test_sensitive_responses_are_not_cacheable(self):
        self.client.force_login(self.director)
        for path in ("/workspace/", "/staff/", "/staff/employees/", "/api/session/", "/api/drafts/"):
            self.assertIn("no-store", self.client.get(path)["Cache-Control"])

    def test_offline_shell_is_identical_and_contains_no_account_data(self):
        public = self.client.get("/app-shell/").content
        self.client.force_login(self.director)
        self.assertEqual(self.client.get("/app-shell/").content, public)
        self.assertNotIn(b"csrfmiddlewaretoken", public)

    def test_password_change_requirement_never_changes_cached_public_shell(self):
        public = self.client.get("/app-shell/").content
        self.technician.must_change_password = True
        self.technician.save()
        self.client.force_login(self.technician)
        response = self.client.get("/app-shell/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, public)
        self.assertEqual(self.client.get("/sw.js").status_code, 200)

    def test_health_uses_real_postgresql(self):
        self.assertEqual(self.client.get("/health/").json(), {"status": "ok", "database": "postgresql"})
