"""Secretariat drafting and immutable issue snapshots for printable correspondence."""
import uuid

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .models import User, Violation, ViolationDocument, ViolationEvent
from .violations import visible

FIELDS = ("kind", "title", "official_number", "document_date", "recipient", "body")


class DocumentForm(forms.ModelForm):
    client_id = forms.UUIDField(widget=forms.HiddenInput)
    revision = forms.IntegerField(min_value=1, widget=forms.HiddenInput)

    class Meta:
        model = ViolationDocument
        fields = FIELDS
        labels = {"kind": "نوع المستند", "title": "الموضوع", "official_number": "رقم الصادر أو المحضر",
            "document_date": "تاريخ المستند", "recipient": "الجهة المخاطبة", "body": "نص المحضر أو المذكرة"}
        widgets = {"document_date": forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
                   "body": forms.Textarea(attrs={"rows": 12})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["client_id"].initial = self.instance.client_id if self.instance.pk else uuid.uuid4()
        self.fields["revision"].initial = self.instance.revision if self.instance.pk else 1


def snapshot(item):
    return {"reference": item.reference, "person_name": item.person_name, "address": item.address, "area": item.area,
        "activity": item.get_activity_display(), "kind": item.get_kind_display(),
        "estimated_cubic_meters": str(item.estimated_cubic_meters), "estimate_days": item.estimate_days,
        "reporter": item.reporter.display_name}


def can_edit(user, item, document=None):
    return user.role == User.Role.SECRETARIAT and item.status == Violation.Status.SECRETARIAT and (
        document is None or document.status == ViolationDocument.Status.DRAFT)


@login_required
@require_http_methods(["GET", "POST"])
def compose(request, violation_id):
    if request.user.role != User.Role.SECRETARIAT:
        return render(request, "portal/forbidden.html", status=403)
    item = get_object_or_404(visible(request.user), pk=violation_id)
    if not can_edit(request.user, item):
        return render(request, "portal/forbidden.html", status=403)
    form = DocumentForm(request.POST if request.method == "POST" else None, initial={"document_date": timezone.localdate()})
    if request.method == "POST" and form.is_valid():
        data = {field: form.cleaned_data[field] for field in FIELDS}
        with transaction.atomic():
            item = Violation.objects.select_for_update().get(pk=item.pk)
            if not can_edit(request.user, item):
                return render(request, "portal/forbidden.html", status=403)
            document, created = ViolationDocument.objects.get_or_create(violation=item, client_id=form.cleaned_data["client_id"],
                defaults={**data, "created_by": request.user})
            if not created and any(getattr(document, field) != value for field, value in data.items()):
                return HttpResponse("سبق حفظ هذا الطلب بمحتوى مختلف. أعد فتح نموذج مستند جديد.", status=409)
            if created:
                ViolationEvent.objects.create(violation=item, actor=request.user, action="document_created",
                    note=f"أُنشئت مسودة {document.get_kind_display()}: {document.title}", document_reference=document.reference)
        return redirect("violation_document", document_id=document.pk)
    return render(request, "portal/document_form.html", {"item": item, "form": form})


def permitted_document(request, document_id):
    return get_object_or_404(ViolationDocument.objects.select_related("violation__reporter", "created_by", "issued_by"),
        pk=document_id, violation__in=visible(request.user))


@login_required
@require_GET
def detail(request, document_id):
    if not request.user.is_employee:
        return render(request, "portal/forbidden.html", status=403)
    document = permitted_document(request, document_id)
    return render(request, "portal/document_detail.html", {"document": document, "item": document.violation,
        "can_edit": can_edit(request.user, document.violation, document)})


@login_required
@require_http_methods(["GET", "POST"])
def edit(request, document_id):
    if request.user.role != User.Role.SECRETARIAT:
        return render(request, "portal/forbidden.html", status=403)
    document = permitted_document(request, document_id)
    if not can_edit(request.user, document.violation, document):
        return render(request, "portal/forbidden.html", status=403)
    # Bind a copy; ModelForm validation must not alter the document used for revision checks.
    form = DocumentForm(request.POST if request.method == "POST" else None, instance=ViolationDocument.objects.get(pk=document.pk))
    if request.method == "POST" and form.is_valid():
        data = {field: form.cleaned_data[field] for field in FIELDS}
        with transaction.atomic():
            item = Violation.objects.select_for_update().get(pk=document.violation_id)
            document = ViolationDocument.objects.select_for_update().get(pk=document.pk)
            if not can_edit(request.user, item, document):
                return render(request, "portal/forbidden.html", status=403)
            if form.cleaned_data["client_id"] != document.client_id:
                return HttpResponse("معرف المستند لا يطابق الملف المفتوح.", status=409)
            if all(getattr(document, field) == value for field, value in data.items()):
                return redirect("violation_document", document_id=document.pk)
            if form.cleaned_data["revision"] != document.revision:
                form.add_error(None, "عدّل موظف آخر هذه المسودة. افتح أحدث نسخة قبل إعادة التحرير.")
                return render(request, "portal/document_form.html", {"item": item, "document": document, "form": form}, status=409)
            for field, value in data.items():
                setattr(document, field, value)
            document.revision += 1
            document.save(update_fields=[*FIELDS, "revision", "updated_at"])
            ViolationEvent.objects.create(violation=item, actor=request.user, action="document_updated",
                note=f"حُررت المسودة، إصدار {document.revision}", document_reference=document.reference)
        return redirect("violation_document", document_id=document.pk)
    return render(request, "portal/document_form.html", {"item": document.violation, "document": document, "form": form})


@login_required
@require_POST
def issue(request, document_id):
    if request.user.role != User.Role.SECRETARIAT:
        return render(request, "portal/forbidden.html", status=403)
    with transaction.atomic():
        document = permitted_document(request, document_id)
        item = Violation.objects.select_for_update().get(pk=document.violation_id)
        document = ViolationDocument.objects.select_for_update().get(pk=document.pk)
        if document.status == ViolationDocument.Status.ISSUED:
            return redirect("violation_document", document_id=document.pk)
        if not can_edit(request.user, item, document):
            return render(request, "portal/forbidden.html", status=403)
        if request.POST.get("revision") != str(document.revision):
            return HttpResponse("افتح أحدث نسخة من المستند قبل الإصدار.", status=409)
        if not document.official_number or (document.kind == ViolationDocument.Kind.LETTER and not document.recipient) or document.document_date > timezone.localdate():
            return render(request, "portal/document_detail.html", {"document": document, "item": item, "can_edit": True,
                "issue_error": "أكمل رقم الصادر، والجهة المخاطبة للمذكرة، واختر تاريخًا غير مستقبلي قبل الإصدار."}, status=400)
        document.snapshot = {**snapshot(item), "prepared_by": request.user.display_name}
        document.status = ViolationDocument.Status.ISSUED
        document.issued_by = request.user
        document.issued_at = timezone.now()
        document.save(update_fields=["snapshot", "status", "issued_by", "issued_at", "updated_at"])
        ViolationEvent.objects.create(violation=item, actor=request.user, action="document_issued",
            note=f"صدر {document.get_kind_display()}: {document.title}", document_reference=document.official_number,
            recipient=document.recipient)
    messages.success(request, "حُفظت النسخة الصادرة. يمكن طباعتها لاستكمال التوقيع والختم.")
    return redirect("violation_document", document_id=document.pk)


@login_required
@require_GET
def printable(request, document_id):
    if not request.user.is_employee:
        return render(request, "portal/forbidden.html", status=403)
    document = permitted_document(request, document_id)
    return render(request, "portal/document_print.html", {"document": document,
        "case": document.snapshot if document.status == ViolationDocument.Status.ISSUED else snapshot(document.violation)})
