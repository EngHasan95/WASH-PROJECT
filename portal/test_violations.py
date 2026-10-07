import json
import uuid

from django.test import TestCase
from django.urls import reverse

from .models import User, Violation, ViolationEvent


class ViolationFlowTests(TestCase):
    def setUp(self):
        self.tech = User.objects.create_user("v-tech", role=User.Role.TECHNICIAN)
        self.other_tech = User.objects.create_user("v-other-tech", role=User.Role.TECHNICIAN)
        self.manager = User.objects.create_user("v-manager", role=User.Role.SYSTEM_MANAGER)
        self.secretary = User.objects.create_user("v-secretary", role=User.Role.SECRETARIAT)
        self.followup = User.objects.create_user("v-followup", role=User.Role.FOLLOWUP)
        self.director = User.objects.create_user("v-director", role=User.Role.DIRECTOR)
        self.finance = User.objects.create_user("v-finance", role=User.Role.FINANCE)
        self.beneficiary = User.objects.create_user("v-beneficiary", role=User.Role.CITIZEN)
        self.payload = {"owner_id": self.tech.pk, "client_id": str(uuid.uuid4()),
            "person_name": "شخص اختبار", "address": "عنوان خيالي", "area": "حي تجريبي",
            "estimated_cubic_meters": "12.50", "estimate_days": "30", "activity": "commercial",
            "kind": "random_connection", "description": "وصف تجريبي"}

    def sync(self, user, payload=None):
        self.client.force_login(user)
        return self.client.post(reverse("violation_sync"), json.dumps(payload or self.payload), content_type="application/json")

    def handoff(self, user, item, action, **details):
        self.client.force_login(user)
        return self.client.post(reverse("violation_handoff", args=[item.pk]), {"action": action, "note": "بيان تجريبي", **details})

    def test_field_entry_is_idempotent_and_rejects_other_accounts(self):
        first = self.sync(self.tech)
        self.assertEqual(first.status_code, 201)
        self.assertRegex(first.json()["reference"], r"^V-MRB-\d+$")
        self.assertEqual(self.sync(self.tech).status_code, 200)
        self.assertEqual(Violation.objects.count(), 1)
        changed = {**self.payload, "area": "منطقة ثانية"}
        self.assertEqual(self.sync(self.tech, changed).status_code, 409)
        self.assertEqual(self.sync(self.other_tech).status_code, 409)
        self.assertEqual(self.sync(self.beneficiary).status_code, 403)
        self.assertEqual(self.sync(self.manager).status_code, 403)
        self.assertEqual(ViolationEvent.objects.count(), 1)

    def test_route_and_handoffs_are_role_scoped(self):
        self.sync(self.tech)
        item = Violation.objects.get()
        self.client.force_login(self.other_tech)
        self.assertEqual(self.client.get(reverse("violation_detail", args=[item.pk])).status_code, 404)
        self.client.force_login(self.beneficiary)
        self.assertEqual(self.client.get(reverse("violation_register")).status_code, 403)
        self.assertEqual(self.client.get(reverse("violation_detail", args=[item.pk])).status_code, 403)
        self.assertEqual(self.handoff(self.secretary, item, "to_followup").status_code, 404)
        self.assertEqual(self.handoff(self.manager, item, "to_secretariat").status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, Violation.Status.SECRETARIAT)
        self.assertEqual(self.handoff(self.manager, item, "to_secretariat").status_code, 403)
        self.assertEqual(self.handoff(self.secretary, item, "to_followup").status_code, 400)
        self.assertEqual(self.handoff(self.secretary, item, "to_followup", recipient="جهة أمنية تجريبية", document_reference="TEST-1").status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, Violation.Status.FOLLOWUP)
        self.assertEqual(self.handoff(self.followup, item, "return_results").status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, Violation.Status.RESULTS_RETURNED)
        self.assertEqual(list(item.events.values_list("action", flat=True)), ["received", "to_secretariat", "to_followup", "return_results"])
        for user in (self.director, self.finance):
            self.client.force_login(user)
            self.assertContains(self.client.get(reverse("violation_detail", args=[item.pk])), "TEST-1")
        self.assertEqual(self.handoff(self.finance, item, "return_results").status_code, 403)

    def test_validation_rejects_invalid_estimates_and_unsafe_fields(self):
        for change in ({"estimated_cubic_meters": "-2"}, {"estimate_days": "0"},
            {"activity": "unknown"}, {"kind": "unknown"}, {"person_name": ""}):
            with self.subTest(change=change):
                self.assertEqual(self.sync(self.tech, {**self.payload, **change}).status_code, 400)
        self.assertEqual(Violation.objects.count(), 0)

    def test_document_fields_and_notes_are_escaped(self):
        self.sync(self.tech)
        item = Violation.objects.get()
        self.handoff(self.manager, item, "to_secretariat")
        self.handoff(self.secretary, item, "to_followup", recipient="<script>alert(1)</script>", document_reference="TEST-2")
        self.client.force_login(self.secretary)
        response = self.client.get(reverse("violation_detail", args=[item.pk]))
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>alert(1)</script>")
