import json
import uuid

from django.test import TestCase
from django.urls import reverse

from .models import Complaint, ComplaintEvent, User


class WorkFlowTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("owner-route", role=User.Role.CITIZEN)
        self.other = User.objects.create_user("other-route", role=User.Role.CITIZEN)
        self.director = User.objects.create_user("director-route", role=User.Role.DIRECTOR)
        self.tech = User.objects.create_user("tech-route", role=User.Role.TECHNICIAN)
        self.tech2 = User.objects.create_user("tech-other", role=User.Role.TECHNICIAN)
        self.finance = User.objects.create_user("finance-route", role=User.Role.FINANCE, is_department_responsible=True)
        self.finance2 = User.objects.create_user("finance-other", role=User.Role.FINANCE)
        self.report = self.make_report("leak", Complaint.Department.TECHNICAL)
        self.bill = self.make_report("high_bill", Complaint.Department.FINANCE)

    def make_report(self, kind, department):
        return Complaint.objects.create(owner=self.owner, client_id=uuid.uuid4(), payload_digest="a" * 64,
            reporter_name="مستفيد اختباري", phone="777000000", complaint_type=kind, neighborhood="n11",
            address="عنوان اختبار", landmark="معلم اختبار", description="مشكلة اختبار", department=department)

    def action(self, user, report, action, **fields):
        self.client.force_login(user)
        return self.client.post(reverse("work_action", args=[report.pk]), {"action": action, **fields})

    def test_technical_cycle_and_beneficiary_sees_progress(self):
        self.assertEqual(self.action(self.director, self.report, "assign", assignee=self.tech.pk).status_code, 302)
        self.report.refresh_from_db()
        self.assertEqual((self.report.status, self.report.assignee), (Complaint.Status.ASSIGNED, self.tech))
        self.assertEqual(self.action(self.tech, self.report, "start").status_code, 302)
        self.assertEqual(self.action(self.tech, self.report, "submit", note="أصلحنا التسريب").status_code, 302)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, Complaint.Status.REVIEW)
        self.assertEqual(self.action(self.director, self.report, "return", note="راجع نقطة التسريب").status_code, 302)
        self.assertEqual(self.action(self.tech, self.report, "start").status_code, 302)
        self.assertEqual(self.action(self.tech, self.report, "submit", note="تمت المراجعة").status_code, 302)
        self.assertEqual(self.action(self.director, self.report, "close").status_code, 302)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, Complaint.Status.CLOSED)
        self.assertEqual(list(self.report.events.values_list("action", flat=True)), ["assign", "start", "submit", "return", "start", "submit", "close"])
        self.client.force_login(self.owner)
        result = self.client.get(reverse("complaint_detail", args=[self.report.pk]))
        self.assertContains(result, "مغلق")
        self.assertNotContains(result, "راجع نقطة التسريب")
        self.assertNotContains(result, "أصلحنا التسريب")

    def test_finance_responsible_assigns_and_director_closes(self):
        self.assertEqual(self.action(self.finance, self.bill, "claim").status_code, 403)
        self.assertEqual(self.action(self.finance2, self.bill, "claim").status_code, 404)
        self.assertEqual(self.action(self.finance, self.bill, "assign", assignee=self.finance2.pk).status_code, 302)
        self.assertEqual(self.action(self.finance2, self.bill, "start").status_code, 302)
        self.assertEqual(self.action(self.finance2, self.bill, "submit", note="تمت مراجعة الفاتورة").status_code, 302)
        self.assertEqual(self.action(self.director, self.bill, "close").status_code, 302)
        self.bill.refresh_from_db()
        self.assertEqual((self.bill.status, self.bill.assignee), (Complaint.Status.CLOSED, self.finance2))

    def test_permissions_and_invalid_transitions(self):
        self.assertEqual(self.action(self.tech, self.report, "close").status_code, 404)
        self.assertEqual(self.action(self.finance, self.report, "claim").status_code, 404)
        self.assertEqual(self.action(self.owner, self.report, "assign", assignee=self.tech.pk).status_code, 403)
        self.assertEqual(self.action(self.director, self.report, "assign", assignee=self.finance.pk).status_code, 400)
        self.assertEqual(self.action(self.director, self.report, "assign").status_code, 400)
        self.assertEqual(self.action(self.director, self.report, "assign", assignee=self.tech.pk).status_code, 302)
        self.assertEqual(self.action(self.tech2, self.report, "start").status_code, 404)
        self.assertEqual(self.action(self.tech, self.report, "submit", note="skipped").status_code, 403)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, Complaint.Status.ASSIGNED)

    def test_queue_details_and_photos_are_role_scoped(self):
        self.client.force_login(self.tech)
        self.assertNotContains(self.client.get(reverse("work_queue")), self.report.reference)
        self.assertEqual(self.client.get(reverse("work_detail", args=[self.report.pk])).status_code, 404)
        self.client.force_login(self.finance)
        self.assertContains(self.client.get(reverse("work_queue")), self.bill.reference)
        self.assertNotContains(self.client.get(reverse("work_queue")), self.report.reference)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("work_queue")).status_code, 403)
        self.assertEqual(self.client.get(reverse("work_photo", args=[999])).status_code, 403)

    def test_required_result_and_return_note_are_not_bypassed(self):
        self.action(self.director, self.report, "assign", assignee=self.tech.pk)
        self.action(self.tech, self.report, "start")
        self.assertEqual(self.action(self.tech, self.report, "submit").status_code, 400)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, Complaint.Status.IN_PROGRESS)
        self.action(self.tech, self.report, "submit", note="نتيجة اختبار")
        self.assertEqual(self.action(self.director, self.report, "return").status_code, 400)
        self.report.refresh_from_db()
        self.assertEqual(self.report.status, Complaint.Status.REVIEW)
