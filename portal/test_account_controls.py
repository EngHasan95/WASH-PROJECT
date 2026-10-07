import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import AccountEvent, Complaint, ComplaintEvent, LoginThrottle, User


class ControlFixtures:
    def setUp(self):
        self.director = User.objects.create_user("controls-director", password="Secret-Controls-82!", role=User.Role.DIRECTOR)
        self.tech = User.objects.create_user("controls-tech", password="Secret-Controls-83!", role=User.Role.TECHNICIAN)
        self.tech2 = User.objects.create_user("controls-tech2", role=User.Role.TECHNICIAN)
        self.owner = User.objects.create_user("controls-owner", role=User.Role.CITIZEN)
        self.client.force_login(self.director)

    def fields(self, user=None, **extra):
        user = user or self.tech
        return {"first_name": "موظف اختباري", "phone": "", "role": user.role,
            "is_active": "on", "reason": "سبب اختباري موثق", **extra}

    def update(self, user=None, **extra):
        user = user or self.tech
        return self.client.post(reverse("employee_manage", args=[user.pk]), self.fields(user, **extra))

    def report(self, assignee=None, department=Complaint.Department.TECHNICAL):
        return Complaint.objects.create(owner=self.owner, client_id=uuid.uuid4(), payload_digest="c" * 64,
            reporter_name="مستفيد تجريبي", phone="777000000", complaint_type="leak", neighborhood="n11",
            address="عنوان اختبار", landmark="معلم", description="تجربة", department=department,
            assignee=assignee, status=Complaint.Status.IN_PROGRESS if assignee else Complaint.Status.RECEIVED)



class AccountControlTests(ControlFixtures, TestCase):
    def test_disable_enable_role_and_audit_preserve_user(self):
        from django.test import Client
        old_session = Client()
        old_session.force_login(self.tech)
        self.assertEqual(self.update(is_active="").status_code, 302)
        self.tech.refresh_from_db()
        self.assertFalse(self.tech.is_active)
        self.assertEqual(old_session.get(reverse("workspace")).status_code, 302)
        self.assertNotIn("_auth_user_id", old_session.session)
        self.assertFalse(self.client.login(username=self.tech.username, password="Secret-Controls-83!"))
        self.client.force_login(self.director)
        self.assertEqual(self.update(role=User.Role.EMPLOYEE).status_code, 302)
        self.tech.refresh_from_db()
        self.assertTrue(self.tech.is_active)
        self.assertEqual(self.tech.role, User.Role.EMPLOYEE)
        event = AccountEvent.objects.filter(target=self.tech).latest("pk")
        self.assertEqual((event.previous_role, event.new_role), (User.Role.TECHNICIAN, User.Role.EMPLOYEE))
        self.assertIn("سبب اختباري", event.note)
        self.assertEqual(event.actor, self.director)

    def test_password_reset_invalidates_existing_session_and_forces_change(self):
        from django.test import Client
        employee_client = Client()
        employee_client.force_login(self.tech)
        self.assertEqual(self.update(password1="Temporary-Controls-97!", password2="Temporary-Controls-97!").status_code, 302)
        self.tech.refresh_from_db()
        self.assertTrue(self.tech.must_change_password)
        self.assertTrue(self.tech.check_password("Temporary-Controls-97!"))
        self.assertRedirects(employee_client.get(reverse("workspace")), reverse("login") + "?next=" + reverse("workspace"), fetch_redirect_response=False)
        self.assertTrue(employee_client.login(username=self.tech.username, password="Temporary-Controls-97!"))
        self.assertRedirects(employee_client.get(reverse("workspace")), reverse("password_change"), fetch_redirect_response=False)
        event = AccountEvent.objects.get(target=self.tech)
        self.assertNotIn("Temporary-Controls-97!", event.note)

    def test_cannot_change_role_or_disable_pending_worker_until_reassigned(self):
        report = self.report(self.tech)
        self.assertEqual(self.update(is_active="").status_code, 200)
        self.assertEqual(self.update(role=User.Role.EMPLOYEE).status_code, 200)
        self.tech.refresh_from_db()
        self.assertTrue(self.tech.is_active)
        self.assertEqual(self.tech.role, User.Role.TECHNICIAN)
        self.assertEqual(self.client.post(reverse("work_action", args=[report.pk]), {"action": "reassign", "assignee": self.tech2.pk, "note": "إجازة الفني"}).status_code, 302)
        self.assertEqual(self.update(is_active="").status_code, 302)
        event = report.events.get(action="reassign")
        self.assertEqual((event.previous_assignee, event.new_assignee), (self.tech, self.tech2))
        self.assertEqual(event.note, "إجازة الفني")

    def test_no_self_disable_no_citizen_edit_no_non_director_permission(self):
        self.assertEqual(self.update(self.director, is_active="").status_code, 200)
        self.director.refresh_from_db()
        self.assertTrue(self.director.is_active)
        self.assertEqual(self.update(self.owner).status_code, 404)
        self.client.force_login(self.tech)
        self.assertEqual(self.update().status_code, 403)

    def test_reason_password_strength_finance_responsibility_validation(self):
        for changes in ({"reason": ""}, {"password1": "123", "password2": "123"}, {"is_department_responsible": "on"}):
            response = self.update(**changes)
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context["form"].errors)
        self.assertFalse(AccountEvent.objects.exists())

    def test_last_finance_responsible_must_be_replaced_when_pending(self):
        responsible = User.objects.create_user("controls-finance", role=User.Role.FINANCE, is_department_responsible=True)
        self.report(department=Complaint.Department.FINANCE)
        self.assertEqual(self.update(responsible, is_active="").status_code, 200)
        responsible.refresh_from_db()
        self.assertTrue(responsible.is_active)
        User.objects.create_user("controls-finance2", role=User.Role.FINANCE, is_department_responsible=True)
        self.assertEqual(self.update(responsible, is_active="").status_code, 302)


class AssignmentControlTests(ControlFixtures, TestCase):
    def test_reassignment_requires_reason_distinct_active_right_role_and_revokes_old_access(self):
        report = self.report(self.tech)
        previous_event = ComplaintEvent.objects.create(complaint=report, actor=self.tech, action="start", note="بدء المعالجة السابق")
        User.objects.create_user("controls-off", role=User.Role.TECHNICIAN, is_active=False)
        financial = User.objects.create_user("controls-ordinary-finance", role=User.Role.FINANCE)
        for assignee, note in ((self.tech2, ""), (self.tech, "نقل"), (financial, "نقل"), (User.objects.get(username="controls-off"), "نقل")):
            self.assertEqual(self.client.post(reverse("work_action", args=[report.pk]), {"action": "reassign", "assignee": assignee.pk, "note": note}).status_code, 400)
        self.assertEqual(self.client.post(reverse("work_action", args=[report.pk]), {"action": "reassign", "assignee": self.tech2.pk, "note": "نقل مهمة"}).status_code, 302)
        report.refresh_from_db()
        self.assertEqual((report.status, report.assignee), (Complaint.Status.ASSIGNED, self.tech2))
        self.assertTrue(report.events.filter(pk=previous_event.pk, note="بدء المعالجة السابق").exists())
        self.client.force_login(self.tech)
        self.assertEqual(self.client.get(reverse("work_detail", args=[report.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("work_action", args=[report.pk]), {"action": "submit", "note": "نتيجة قديمة"}).status_code, 404)

    def test_finance_only_responsible_can_assign_and_ordinary_cannot_self_claim(self):
        report = self.report(department=Complaint.Department.FINANCE)
        manager = User.objects.create_user("controls-fin-manager", role=User.Role.FINANCE, is_department_responsible=True)
        worker = User.objects.create_user("controls-fin-worker", role=User.Role.FINANCE)
        self.client.force_login(worker)
        self.assertEqual(self.client.get(reverse("work_detail", args=[report.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse("work_action", args=[report.pk]), {"action": "claim"}).status_code, 404)
        self.client.force_login(manager)
        self.assertEqual(self.client.post(reverse("work_action", args=[report.pk]), {"action": "assign", "assignee": worker.pk}).status_code, 302)
        self.client.force_login(worker)
        self.assertEqual(self.client.post(reverse("work_action", args=[report.pk]), {"action": "reassign", "assignee": manager.pk, "note": "تجاوز"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("work_action", args=[report.pk]), {"action": "start"}).status_code, 302)
        self.assertEqual(self.client.post(reverse("work_action", args=[report.pk]), {"action": "submit", "note": "نتيجة مالية"}).status_code, 302)
        self.assertEqual(self.client.post(reverse("work_action", args=[report.pk]), {"action": "close"}).status_code, 403)
        self.client.force_login(self.director)
        self.assertEqual(self.client.post(reverse("work_action", args=[report.pk]), {"action": "close"}).status_code, 302)


class AuthenticationThrottleTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("login-tested", password="Real-Login-Password-97!")

    def login_attempt(self, password="invalid", **headers):
        return self.client.post(reverse("login"), {"username": self.user.username, "password": password}, **headers)

    def test_failed_attempts_block_auth_even_correct_password_until_expiry(self):
        for _ in range(5):
            self.assertEqual(self.login_attempt().status_code, 200)
        with patch("django.contrib.auth.forms.authenticate") as auth:
            response = self.login_attempt("Real-Login-Password-97!")
            self.assertEqual(response.status_code, 429)
            auth.assert_not_called()
        self.assertGreater(int(response["Retry-After"]), 0)
        self.assertNotIn("_auth_user_id", self.client.session)
        future = timezone.now() + timedelta(minutes=6)
        with patch("portal.auth_views.timezone.now", return_value=future):
            self.assertEqual(self.login_attempt("Real-Login-Password-97!").status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), self.user.pk)

    def test_forwarded_headers_do_not_bypass_but_different_real_address_is_independent(self):
        for index in range(5):
            self.assertEqual(self.login_attempt(HTTP_X_FORWARDED_FOR=f"192.0.2.{index}").status_code, 200)
        self.assertEqual(self.login_attempt(HTTP_X_FORWARDED_FOR="198.51.100.77").status_code, 429)
        self.assertEqual(self.login_attempt("Real-Login-Password-97!", REMOTE_ADDR="192.0.2.200").status_code, 302)

    def test_success_resets_user_bucket_without_storing_plain_username_or_address(self):
        self.login_attempt()
        self.assertEqual(self.login_attempt("Real-Login-Password-97!").status_code, 302)
        self.client.logout()
        for _ in range(5):
            self.assertEqual(self.login_attempt().status_code, 200)
        self.assertEqual(self.login_attempt().status_code, 429)
        for key in LoginThrottle.objects.values_list("key", flat=True):
            self.assertEqual(len(key), 64)
            self.assertNotIn(self.user.username, key)

    def test_address_limit_prevents_new_user_bucket_creation_after_block(self):
        # A shared network budget must still apply when the username changes.
        for index in range(60):
            self.assertEqual(self.client.post(reverse("login"), {"username": f"nonexistent-{index}", "password": "invalid"}).status_code, 200)
        before = LoginThrottle.objects.count()
        self.assertEqual(self.client.post(reverse("login"), {"username": "blocked-new-username", "password": "invalid"}).status_code, 429)
        self.assertEqual(LoginThrottle.objects.count(), before)

    def test_registration_rate_limited(self):
        for _ in range(20):
            self.assertEqual(self.client.post(reverse("register"), {"username": "invalid"}).status_code, 200)
        self.assertEqual(self.client.post(reverse("register"), {"username": "invalid"}).status_code, 429)
        self.assertFalse(User.objects.filter(username="invalid").exists())
