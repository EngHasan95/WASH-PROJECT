import uuid
import tempfile
from pathlib import Path
from datetime import timedelta
from decimal import Decimal

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import User, Violation, ViolationSettlement


class SettlementTests(TestCase):
    def setUp(self):
        self.tech = User.objects.create_user("settlement-tech", role=User.Role.TECHNICIAN)
        self.finance = User.objects.create_user("settlement-finance", role=User.Role.FINANCE)
        self.director = User.objects.create_user("settlement-director", role=User.Role.DIRECTOR)
        self.owner = User.objects.create_user("settlement-owner", role=User.Role.CITIZEN)
        self.item = self.record("residential")
        self.assessment = {"approved_units": "10.5",
                           "assessment_basis": "كمية معتمدة بمستند TEST-1"}

    def record(self, activity):
        return Violation.objects.create(reporter=self.tech, client_id=uuid.uuid4(), payload_digest="a" * 64,
            person_name="اسم خيالي", address="عنوان اختبار", area="منطقة اختبار", estimated_cubic_meters="99.99",
            estimate_days=30, activity=activity, kind="random_connection", status=Violation.Status.RESULTS_RETURNED)

    def act(self, user, action, data, item=None):
        self.client.force_login(user)
        return self.client.post(reverse("violation_settlement", args=[(item or self.item).pk, action]), data)

    def payment(self, amount="228150.00"):
        return {"payment_amount": amount, "payment_receipt": "TEST-RECEIPT-1", "payment_date": timezone.localdate().isoformat()}

    def test_residential_finance_payment_then_technical_closure(self):
        self.assertEqual(self.act(self.finance, "assess", {**self.assessment, "fixed_fee": "1", "unit_rate": "1", "unit_description": "لتر"}).status_code, 302)
        settlement = ViolationSettlement.objects.get()
        self.assertEqual(settlement.fixed_fee, Decimal("225000"))
        self.assertEqual(settlement.unit_rate, Decimal("300"))
        self.assertEqual(settlement.water_charge, Decimal("3150"))
        self.assertEqual(settlement.total, Decimal("228150"))
        self.assertEqual(settlement.assessed_by, self.finance)
        self.assertEqual(settlement.unit_description, "متر مكعب")
        self.assertEqual(settlement.approved_units, Decimal("10.5"))
        self.assertEqual(self.act(self.finance, "pay", self.payment()).status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, Violation.Status.PAID)
        self.assertEqual(self.act(self.director, "close", {"policy_applied": "on", "closure_note": "أزيل الربط المخالف وسويت الحالة"}).status_code, 302)
        settlement.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, Violation.Status.CLOSED)
        self.assertEqual(settlement.payment_confirmed_by, self.finance)
        self.assertEqual(settlement.closed_by, self.director)
        self.assertEqual(list(self.item.events.values_list("action", flat=True)), ["assess", "pay", "close"])

    def test_government_has_commercial_tariff_and_can_complete_settlement(self):
        commercial = self.record("commercial")
        self.assertEqual(self.act(self.finance, "assess", self.assessment, commercial).status_code, 302)
        self.assertEqual(commercial.settlement.total, Decimal("329200"))
        government = self.record("government")
        self.assertEqual(self.act(self.finance, "assess", self.assessment, government).status_code, 302)
        self.assertEqual(government.settlement.fixed_fee, commercial.settlement.fixed_fee)
        self.assertEqual(government.settlement.unit_rate, commercial.settlement.unit_rate)
        self.assertEqual(government.settlement.total, Decimal("329200"))
        self.assertEqual(self.act(self.finance, "pay", self.payment("329200"), government).status_code, 302)
        self.assertEqual(self.act(self.director, "close", {"policy_applied": "on", "closure_note": "تمت معالجة المخالفة الحكومية"}, government).status_code, 302)
        government.refresh_from_db()
        self.assertEqual(government.status, Violation.Status.CLOSED)

    def test_clarification_preserves_previous_assessment_snapshot(self):
        old = ViolationSettlement.objects.create(violation=self.item, policy_code="institution-memo-412262-v1",
            fixed_fee="225000", unit_rate="300", approved_units="10.5", unit_description="وحدة حسب المستند القديم",
            assessment_basis=self.assessment["assessment_basis"], assessed_by=self.finance)
        self.item.status = Violation.Status.AWAITING_PAYMENT
        self.item.save()
        self.assertEqual(self.act(self.finance, "assess", self.assessment).status_code, 302)
        self.assertEqual(self.act(self.finance, "pay", self.payment()).status_code, 302)
        old.refresh_from_db()
        self.assertEqual(old.unit_description, "وحدة حسب المستند القديم")
        self.assertEqual(old.policy_code, "institution-memo-412262-v1")

    def test_roles_cannot_cross_payment_and_closure_boundaries(self):
        for role in User.Role.values:
            if role == User.Role.FINANCE:
                continue
            user = User.objects.create_user("settlement-blocked-" + role, role=role)
            self.assertEqual(self.act(user, "assess", self.assessment).status_code, 403)
            self.assertEqual(self.act(user, "pay", self.payment()).status_code, 403)
        self.assertEqual(self.act(self.finance, "close", {"policy_applied": "on", "closure_note": "غير مسموح"}).status_code, 403)
        self.assertFalse(ViolationSettlement.objects.exists())

    def test_full_payment_receipt_and_technical_completion_required(self):
        self.assertEqual(self.act(self.director, "close", {"policy_applied": "on", "closure_note": "إغلاق قبل الدفع"}).status_code, 403)
        self.act(self.finance, "assess", self.assessment)
        for changes in ({"payment_amount": "1"}, {"payment_amount": "999999"}, {"payment_receipt": ""},
                        {"payment_date": (timezone.localdate() + timedelta(days=1)).isoformat()}):
            self.assertEqual(self.act(self.finance, "pay", {**self.payment(), **changes}).status_code, 400)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, Violation.Status.AWAITING_PAYMENT)
        self.act(self.finance, "pay", self.payment())
        self.assertEqual(self.act(self.director, "close", {"closure_note": "دون تأكيد المعالجة"}).status_code, 400)
        self.assertEqual(self.act(self.director, "close", {"policy_applied": "on", "closure_note": ""}).status_code, 400)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, Violation.Status.PAID)

    def test_retry_does_not_duplicate_or_replace_financial_evidence(self):
        self.act(self.finance, "assess", self.assessment)
        self.assertEqual(self.act(self.finance, "assess", self.assessment).status_code, 302)
        self.assertEqual(self.act(self.finance, "assess", {**self.assessment, "approved_units": "2"}).status_code, 409)
        self.act(self.finance, "pay", self.payment())
        self.assertEqual(self.act(self.finance, "pay", self.payment()).status_code, 302)
        self.assertEqual(self.act(self.finance, "pay", self.payment("1")).status_code, 409)
        closure = {"policy_applied": "on", "closure_note": "نتيجة معالجة اختبار"}
        self.act(self.director, "close", closure)
        self.assertEqual(self.act(self.director, "close", closure).status_code, 302)
        self.assertEqual(self.item.events.count(), 3)

    def test_no_financial_actions_before_field_results_and_private_details_stay_private(self):
        self.item.status = Violation.Status.FOLLOWUP
        self.item.save()
        self.assertEqual(self.act(self.finance, "assess", self.assessment).status_code, 403)
        self.item.status = Violation.Status.RESULTS_RETURNED
        self.item.save()
        self.act(self.finance, "assess", self.assessment)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse("violation_detail", args=[self.item.pk])).status_code, 403)
        self.client.force_login(self.finance)
        response = self.client.get(reverse("violation_detail", args=[self.item.pk]))
        self.assertContains(response, "تأكيد الدفع بواسطة المالية")
        self.assertNotContains(response, "إغلاق الملف بواسطة المدير الفني")
        self.assertEqual(response["Cache-Control"], "no-store, private")

    def test_financial_write_requires_csrf(self):
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.finance)
        self.assertEqual(strict.post(reverse("violation_settlement", args=[self.item.pk, "assess"]), self.assessment).status_code, 403)

    def test_memo_source_is_private_and_missing_source_is_explicit(self):
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            path = Path(media) / "policies/violation-fees-source.jpg"
            path.parent.mkdir()
            path.write_bytes(b"fictional-memo-image")
            route = reverse("violation_fee_memo")
            self.client.force_login(self.owner)
            self.assertEqual(self.client.get(route).status_code, 403)
            self.client.force_login(self.finance)
            response = self.client.get(route)
            self.assertEqual(response["Cache-Control"], "no-store, private")
            self.assertEqual(b"".join(response.streaming_content), b"fictional-memo-image")
            path.unlink()
            self.assertEqual(self.client.get(route).status_code, 404)
