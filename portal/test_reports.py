import uuid

from django.test import TestCase
from django.urls import reverse

from .models import Complaint, User, Violation


class DirectorReportTests(TestCase):
    def test_counts_are_derived_from_records_and_only_director_can_open(self):
        director = User.objects.create_user("report-director", role=User.Role.DIRECTOR)
        owner = User.objects.create_user("report-owner", role=User.Role.CITIZEN)
        technician = User.objects.create_user("report-tech", first_name="فني الاختبار", role=User.Role.TECHNICIAN)
        Complaint.objects.create(owner=owner, client_id=uuid.uuid4(), payload_digest="a" * 64,
            reporter_name="اختبار", phone="777000000", complaint_type="leak", neighborhood="n11",
            address="عنوان", landmark="معلم", description="وصف", assignee=technician, status=Complaint.Status.IN_PROGRESS)
        Violation.objects.create(reporter=technician, client_id=uuid.uuid4(), payload_digest="b" * 64,
            person_name="اختبار", address="عنوان", area="منطقة", estimated_cubic_meters="5.5",
            estimate_days=30, activity="residential", kind="random_connection")
        self.client.force_login(director)
        result = self.client.get(reverse("director_report"))
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.context["total"], 1)
        self.assertEqual(result.context["violation_total"], 1)
        self.assertContains(result, "فني الاختبار")
        self.client.force_login(owner)
        self.assertEqual(self.client.get(reverse("director_report")).status_code, 403)
        self.client.force_login(technician)
        self.assertEqual(self.client.get(reverse("director_report")).status_code, 403)
