"""Private field violations and their documented institutional handoffs."""
import hashlib
import json

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from .models import User, Violation, ViolationEvent


class ViolationForm(forms.Form):
    owner_id = forms.IntegerField(min_value=1)
    client_id = forms.UUIDField()
    person_name = forms.CharField(max_length=150)
    address = forms.CharField(max_length=500)
    area = forms.CharField(max_length=150)
    estimated_cubic_meters = forms.DecimalField(min_value=0.01, max_digits=12, decimal_places=2)
    estimate_days = forms.IntegerField(min_value=1, max_value=3650)
    activity = forms.ChoiceField(choices=Violation.Activity.choices)
    kind = forms.ChoiceField(choices=Violation.Kind.choices)
    description = forms.CharField(max_length=3000, required=False)


class HandoffForm(forms.Form):
    action = forms.ChoiceField(choices=[("to_secretariat", "إحالة للسكرتارية"),
        ("to_followup", "إحالة للمتابعة"), ("return_results", "إعادة النتائج")])
    note = forms.CharField(max_length=3000, widget=forms.Textarea(attrs={"rows": 4}))
    recipient = forms.CharField(max_length=200, required=False)
    document_reference = forms.CharField(max_length=100, required=False)


def visible(user):
    returned = [Violation.Status.RESULTS_RETURNED, Violation.Status.AWAITING_PAYMENT, Violation.Status.PAID, Violation.Status.CLOSED]
    if user.role == User.Role.TECHNICIAN:
        return Violation.objects.filter(reporter=user)
    if user.role == User.Role.SYSTEM_MANAGER:
        return Violation.objects.all()
    if user.role == User.Role.SECRETARIAT:
        return Violation.objects.filter(status__in=[Violation.Status.SECRETARIAT, Violation.Status.FOLLOWUP, *returned])
    if user.role == User.Role.FOLLOWUP:
        return Violation.objects.filter(status__in=[Violation.Status.FOLLOWUP, *returned])
    if user.role in (User.Role.DIRECTOR, User.Role.FINANCE):
        return Violation.objects.filter(status__in=returned)
    return Violation.objects.none()


def next_action(user, item):
    return {
        (User.Role.SYSTEM_MANAGER, Violation.Status.SYSTEM_MANAGER): "to_secretariat",
        (User.Role.SECRETARIAT, Violation.Status.SECRETARIAT): "to_followup",
        (User.Role.FOLLOWUP, Violation.Status.FOLLOWUP): "return_results",
    }.get((user.role, item.status))


@login_required
@require_GET
def register(request):
    if not request.user.is_employee:
        return render(request, "portal/forbidden.html", status=403)
    items = visible(request.user).select_related("reporter").order_by("-received_at", "-pk")
    return render(request, "portal/violation_register.html", {"page": Paginator(items, 20).get_page(request.GET.get("page"))})


@login_required
@require_GET
def new(request):
    if request.user.role != User.Role.TECHNICIAN:
        return render(request, "portal/forbidden.html", status=403)
    return render(request, "portal/violation_new.html")


@login_required
@require_GET
def detail(request, violation_id):
    if not request.user.is_employee:
        return render(request, "portal/forbidden.html", status=403)
    item = get_object_or_404(visible(request.user).select_related("reporter").prefetch_related("events__actor"), pk=violation_id)
    from .settlements import context
    from .documents import can_edit
    latest_letter = item.documents.filter(status="issued", kind="letter").order_by("-issued_at", "-pk").first()
    initial = {"recipient": latest_letter.recipient, "document_reference": latest_letter.official_number} if latest_letter else {}
    return render(request, "portal/violation_detail.html", {"item": item, "events": item.events.all(),
        "documents": item.documents.all(), "can_create_document": can_edit(request.user, item),
        "next_action": next_action(request.user, item), "form": HandoffForm(initial=initial), **context(item, request.user)})


@require_POST
def sync(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "login_required"}, status=401)
    if request.user.role != User.Role.TECHNICIAN:
        return JsonResponse({"error": "role_forbidden"}, status=403)
    if request.content_type != "application/json":
        return JsonResponse({"error": "json_required"}, status=415)
    try:
        payload = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid_json"}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "invalid_payload"}, status=400)
    form = ViolationForm(payload)
    if not form.is_valid():
        return JsonResponse({"error": "invalid_payload", "fields": form.errors.get_json_data()}, status=400)
    data = form.cleaned_data.copy()
    if data.pop("owner_id") != request.user.pk:
        return JsonResponse({"error": "account_changed"}, status=409)
    client_id = data.pop("client_id")
    digest = hashlib.sha256(json.dumps(data, default=str, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    with transaction.atomic():
        item, created = Violation.objects.get_or_create(reporter=request.user, client_id=client_id,
            defaults={**data, "payload_digest": digest})
        if not created and item.payload_digest != digest:
            return JsonResponse({"error": "id_conflict"}, status=409)
        if created:
            ViolationEvent.objects.create(violation=item, actor=None, action="received", note="أُحيلت تلقائيًا إلى مسؤول النظام")
    return JsonResponse({"saved": True, "client_id": str(item.client_id), "reference": item.reference,
        "detail_url": f"/staff/violations/{item.pk}/", "status": item.status}, status=201 if created else 200)


@login_required
@require_POST
def handoff(request, violation_id):
    if not request.user.is_employee:
        return render(request, "portal/forbidden.html", status=403)
    form = HandoffForm(request.POST)
    if not form.is_valid():
        return render(request, "portal/forbidden.html", status=400)
    with transaction.atomic():
        item = get_object_or_404(visible(request.user).select_for_update(), pk=violation_id)
        action = form.cleaned_data["action"]
        if next_action(request.user, item) != action:
            return render(request, "portal/forbidden.html", status=403)
        note = form.cleaned_data["note"].strip()
        recipient = form.cleaned_data["recipient"].strip()
        document_reference = form.cleaned_data["document_reference"].strip()
        if action == "to_followup" and (not recipient or not document_reference):
            return render(request, "portal/violation_detail.html", {"item": item, "events": item.events.select_related("actor").all(),
                "documents": item.documents.all(), "can_create_document": True,
                "next_action": action, "form": form, "form_error": "أدخل الجهة المخاطبة ورقم المذكرة قبل الإحالة."}, status=400)
        new_status = {"to_secretariat": Violation.Status.SECRETARIAT, "to_followup": Violation.Status.FOLLOWUP,
                      "return_results": Violation.Status.RESULTS_RETURNED}[action]
        item.status = new_status
        item.save(update_fields=["status"])
        ViolationEvent.objects.create(violation=item, actor=request.user, action=action, note=note,
            recipient=recipient, document_reference=document_reference)
    messages.success(request, "حُفظت الإحالة في سجل المخالفة.")
    return redirect("violation_detail", violation_id=violation_id)
