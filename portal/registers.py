"""Read-only registers with separate beneficiary and director access paths."""
import re
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from django import forms
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_GET

from .catalog import COMPLAINT_TYPES, NEIGHBORHOODS
from .models import Complaint, ComplaintPhoto, User
from .views import director_required


class RegisterFilters(forms.Form):
    q = forms.CharField(label="رقم البلاغ، الاسم أو الهاتف", max_length=150, required=False,
                        widget=forms.TextInput(attrs={"placeholder": "ابحث برقم البلاغ أو بيانات مقدمه"}))
    complaint_type = forms.ChoiceField(label="نوع البلاغ", required=False, choices=[("", "كل الأنواع"), *COMPLAINT_TYPES])
    neighborhood = forms.ChoiceField(label="الحي", required=False, choices=[("", "كل الأحياء"), *NEIGHBORHOODS])
    status = forms.ChoiceField(label="الحالة", required=False, choices=[("", "كل الحالات"), *Complaint.Status.choices])
    date_from = forms.DateField(label="من تاريخ", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    date_to = forms.DateField(label="إلى تاريخ", required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        data = super().clean()
        if data.get("date_from") and data.get("date_to") and data["date_from"] > data["date_to"]:
            self.add_error("date_to", "تاريخ النهاية يجب أن يلي تاريخ البداية أو يساويه.")
        if data.get("date_to") and data["date_to"].year == 9999:
            self.add_error("date_to", "اختر تاريخًا قبل سنة 9999.")
        return data


def register(request, *, internal):
    records = Complaint.objects.all() if internal else request.user.complaints.all()
    form = RegisterFilters(request.GET)
    if form.is_valid():
        data = form.cleaned_data
        query = data["q"].translate(str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789"))
        if query:
            match = re.fullmatch(r"(?:MRB-)?(\d{1,18})", query, flags=re.IGNORECASE)
            criteria = Q(reporter_name__icontains=query) | Q(phone__icontains=query)
            if match:
                criteria |= Q(pk=int(match[1]))
            records = records.filter(criteria)
        for field in ("complaint_type", "neighborhood", "status"):
            if data[field]:
                records = records.filter(**{field: data[field]})
        # Inclusive calendar days in the institution's local time, including midnight boundaries.
        for field, lookup, increment in (("date_from", "received_at__gte", 0), ("date_to", "received_at__lt", 1)):
            if data[field]:
                boundary = datetime.combine(data[field] + timedelta(days=increment), time.min, tzinfo=ZoneInfo("Asia/Aden"))
                records = records.filter(**{lookup: boundary})
    else:
        records = records.none()
    records = records.annotate(photo_count=Count("photos")).order_by("-received_at", "-pk")
    page = Paginator(records, 20).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "portal/complaint_register.html", {
        "filter_form": form, "page": page, "filter_query": params.urlencode(), "internal": internal,
        "detail_route": "staff_complaint_detail" if internal else "complaint_detail",
        "register_route": "staff_complaints" if internal else "complaint_register",
    })


@login_required
@require_GET
def beneficiary_register(request):
    if request.user.role != User.Role.CITIZEN:
        return render(request, "portal/forbidden.html", status=403)
    return register(request, internal=False)


@director_required
@require_GET
def director_register(request):
    return register(request, internal=True)


def detail(request, complaint_id, *, internal):
    records = Complaint.objects.all() if internal else request.user.complaints.all()
    complaint = get_object_or_404(records.prefetch_related("photos", "events").select_related("assignee"), pk=complaint_id)
    photo_route = "staff_complaint_photo" if internal else "complaint_photo"
    return render(request, "portal/complaint_detail.html", {
        "complaint": complaint, "internal": internal,
        "photos": [{"url": reverse(photo_route, args=[photo.pk]), "position": photo.position + 1} for photo in complaint.photos.all()],
        "register_route": "staff_complaints" if internal else "complaint_register",
    })


@login_required
@require_GET
def beneficiary_detail(request, complaint_id):
    if request.user.role != User.Role.CITIZEN:
        return render(request, "portal/forbidden.html", status=403)
    return detail(request, complaint_id, internal=False)


@director_required
@require_GET
def director_detail(request, complaint_id):
    return detail(request, complaint_id, internal=True)


@require_GET
def director_photo(request, photo_id):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "login_required"}, status=401)
    if request.user.role != User.Role.DIRECTOR:
        return JsonResponse({"error": "role_forbidden"}, status=403)
    photo = get_object_or_404(ComplaintPhoto, pk=photo_id)
    try:
        image = photo.image.open("rb")
    except FileNotFoundError:
        return JsonResponse({"error": "photo_unavailable"}, status=404)
    response = FileResponse(image, content_type="image/jpeg", filename="complaint-photo.jpg")
    response["Cache-Control"] = "no-store, private"
    return response
