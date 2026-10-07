"""Institutional fee snapshot, finance payment confirmation and technical closure."""
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.core.files.storage import default_storage
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from .models import User, Violation, ViolationEvent, ViolationSettlement

POLICY_CODE = "institution-memo-412262-v2"
WATER_UNIT = "متر مكعب"
MEMO_PATH = "policies/violation-fees-source.jpg"
# Government follows commercial by the user's clarification; the memo covers the other two.
TARIFFS = {Violation.Activity.RESIDENTIAL: (Decimal("225000.00"), Decimal("300.00")),
           Violation.Activity.COMMERCIAL: (Decimal("325000.00"), Decimal("400.00")),
           Violation.Activity.GOVERNMENT: (Decimal("325000.00"), Decimal("400.00"))}


class AssessmentForm(forms.Form):
    approved_units = forms.DecimalField(label="كمية المياه المعتمدة للاحتساب (متر مكعب)", min_value=0, max_digits=12, decimal_places=2)
    assessment_basis = forms.CharField(label="مرجع اعتماد الكمية وأساس التسوية", max_length=3000, widget=forms.Textarea(attrs={"rows": 3}))


class PaymentForm(forms.Form):
    payment_amount = forms.DecimalField(label="المبلغ المسدد (ريال يمني)", min_value=Decimal("0.01"), max_digits=16, decimal_places=2)
    payment_receipt = forms.CharField(label="رقم سند التحصيل", max_length=100)
    payment_date = forms.DateField(label="تاريخ السداد", widget=forms.DateInput(attrs={"type": "date"}))

    def clean_payment_date(self):
        value = self.cleaned_data["payment_date"]
        if value > timezone.localdate():
            raise forms.ValidationError("تاريخ السداد لا يمكن أن يكون في المستقبل.")
        return value


class ClosureForm(forms.Form):
    policy_applied = forms.BooleanField(label="أؤكد استكمال معالجة المخالفة وتطبيق سياسة المؤسسة")
    closure_note = forms.CharField(label="نتيجة المعالجة وأساس إغلاق الملف", max_length=3000, widget=forms.Textarea(attrs={"rows": 3}))


def context(item, user):
    settlement = ViolationSettlement.objects.filter(violation=item).select_related("assessed_by", "payment_confirmed_by", "closed_by").first()
    tariff = TARIFFS.get(item.activity)
    return {"settlement": settlement, "tariff": tariff, "memo_available": default_storage.exists(MEMO_PATH),
        "assessment_form": AssessmentForm(), "payment_form": PaymentForm(), "closure_form": ClosureForm(),
        "can_assess": user.role == User.Role.FINANCE and item.status == Violation.Status.RESULTS_RETURNED and tariff is not None and settlement is None,
        "can_pay": user.role == User.Role.FINANCE and item.status == Violation.Status.AWAITING_PAYMENT,
        "can_close": user.role == User.Role.DIRECTOR and item.status == Violation.Status.PAID}


@login_required
@require_GET
def memo(request):
    if not request.user.is_employee:
        return render(request, "portal/forbidden.html", status=403)
    try:
        source = default_storage.open(MEMO_PATH, "rb")
    except FileNotFoundError:
        return HttpResponse("صورة المذكرة المرجعية غير متاحة في تخزين هذه النسخة.", status=404)
    response = FileResponse(source, content_type="image/jpeg", filename="institution-fee-memo.jpg")
    response["Cache-Control"] = "no-store, private"
    return response


def invalid(request, item, form_name, form):
    data = {"item": item, "events": item.events.select_related("actor").all(), "next_action": None}
    data.update(context(item, request.user))
    data[form_name] = form
    return render(request, "portal/violation_detail.html", data, status=400)


@login_required
@require_POST
def act(request, violation_id, action):
    required_role = User.Role.DIRECTOR if action == "close" else User.Role.FINANCE
    if action not in ("assess", "pay", "close") or request.user.role != required_role:
        return render(request, "portal/forbidden.html", status=403)
    form_class, form_name = {"assess": (AssessmentForm, "assessment_form"),
        "pay": (PaymentForm, "payment_form"), "close": (ClosureForm, "closure_form")}[action]
    with transaction.atomic():
        item = get_object_or_404(Violation.objects.select_for_update(), pk=violation_id)
        allowed_states = (Violation.Status.RESULTS_RETURNED, Violation.Status.AWAITING_PAYMENT, Violation.Status.PAID, Violation.Status.CLOSED)
        if item.status not in allowed_states:
            return render(request, "portal/forbidden.html", status=403)
        form = form_class(request.POST)
        if not form.is_valid():
            return invalid(request, item, form_name, form)
        data = form.cleaned_data
        settlement = ViolationSettlement.objects.filter(violation=item).first()
        if action == "assess":
            if item.activity not in TARIFFS:
                return HttpResponse("لا توجد تعرفة معتمدة لهذا النشاط.", status=409)
            if settlement:
                if all(getattr(settlement, field) == value for field, value in data.items()):
                    return redirect("violation_detail", violation_id=item.pk)
                return HttpResponse("سبق اعتماد رسوم هذا الملف؛ لا يمكن استبدال السجل المالي.", status=409)
            if item.status != Violation.Status.RESULTS_RETURNED:
                return render(request, "portal/forbidden.html", status=403)
            fixed_fee, unit_rate = TARIFFS[item.activity]
            settlement = ViolationSettlement.objects.create(violation=item, policy_code=POLICY_CODE,
                fixed_fee=fixed_fee, unit_rate=unit_rate, unit_description=WATER_UNIT, assessed_by=request.user, **data)
            item.status = Violation.Status.AWAITING_PAYMENT
            note = f"اعتمدت المالية الرسوم: {settlement.total} ريال. {settlement.assessment_basis}"
        elif action == "pay":
            if settlement is None:
                return render(request, "portal/forbidden.html", status=403)
            if settlement.payment_confirmed_at:
                if all(getattr(settlement, field) == value for field, value in data.items()):
                    return redirect("violation_detail", violation_id=item.pk)
                return HttpResponse("سبق تأكيد السداد لهذا الملف؛ لا يمكن استبدال سند السداد.", status=409)
            if item.status != Violation.Status.AWAITING_PAYMENT:
                return render(request, "portal/forbidden.html", status=403)
            if data["payment_amount"] != settlement.total:
                form.add_error("payment_amount", "تأكيد التسوية يتطلب سندًا بالمبلغ المستحق كاملًا.")
                return invalid(request, item, form_name, form)
            for field, value in data.items():
                setattr(settlement, field, value)
            settlement.payment_confirmed_by = request.user
            settlement.payment_confirmed_at = timezone.now()
            settlement.save(update_fields=[*data, "payment_confirmed_by", "payment_confirmed_at"])
            item.status = Violation.Status.PAID
            note = f"أكدت المالية سداد {settlement.payment_amount} ريال، سند {settlement.payment_receipt}."
        else:
            if settlement is None or not settlement.payment_confirmed_at or settlement.payment_amount != settlement.total:
                return render(request, "portal/forbidden.html", status=403)
            if settlement.closed_at:
                if settlement.closure_note == data["closure_note"]:
                    return redirect("violation_detail", violation_id=item.pk)
                return HttpResponse("ملف المخالفة مغلق بالفعل.", status=409)
            if item.status != Violation.Status.PAID:
                return render(request, "portal/forbidden.html", status=403)
            settlement.closure_note = data["closure_note"]
            settlement.closed_by = request.user
            settlement.closed_at = timezone.now()
            settlement.save(update_fields=["closure_note", "closed_by", "closed_at"])
            item.status = Violation.Status.CLOSED
            note = settlement.closure_note
        item.save(update_fields=["status"])
        ViolationEvent.objects.create(violation=item, actor=request.user, action=action, note=note)
    messages.success(request, "حُفظ إجراء التسوية وسُجل المسؤول عنه.")
    return redirect("violation_detail", violation_id=item.pk)
