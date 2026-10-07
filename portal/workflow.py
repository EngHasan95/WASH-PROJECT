"""Role-scoped complaint work queue and auditable state transitions."""
from django import forms
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.paginator import Paginator
from django.db import transaction
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from .models import Complaint, ComplaintEvent, ComplaintPhoto, User


def visible_reports(user):
    if user.role == User.Role.DIRECTOR:
        return Complaint.objects.all()
    if user.role == User.Role.TECHNICIAN:
        return Complaint.objects.filter(assignee=user, department=Complaint.Department.TECHNICAL)
    if user.role == User.Role.FINANCE:
        reports = Complaint.objects.filter(department=Complaint.Department.FINANCE)
        return reports if user.is_department_responsible else reports.filter(assignee=user)
    return Complaint.objects.none()


class ActionForm(forms.Form):
    action = forms.ChoiceField(choices=[("assign", "إسناد"), ("reassign", "إعادة إسناد"), ("claim", "استلام قديم"),
        ("start", "بدء"), ("submit", "رفع للمراجعة"), ("return", "إعادة"), ("close", "إغلاق")])
    assignee = forms.ModelChoiceField(queryset=User.objects.none(), required=False)
    note = forms.CharField(max_length=3000, required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, complaint=None, **kwargs):
        super().__init__(*args, **kwargs)
        role = User.Role.FINANCE if complaint and complaint.department == Complaint.Department.FINANCE else User.Role.TECHNICIAN
        self.fields["assignee"].queryset = User.objects.filter(role=role, is_active=True).order_by("first_name", "username")


def permitted_actions(user, complaint):
    if not user.is_active:
        return []
    status = complaint.status
    responsible = (user.role == User.Role.DIRECTOR and complaint.department == Complaint.Department.TECHNICAL) or (
        user.role == User.Role.FINANCE and user.is_department_responsible and complaint.department == Complaint.Department.FINANCE)
    actions = []
    if responsible:
        if status in (Complaint.Status.RECEIVED, Complaint.Status.RETURNED):
            actions.append("assign")
        if complaint.assignee_id and status in (Complaint.Status.ASSIGNED, Complaint.Status.IN_PROGRESS):
            actions.append("reassign")
    if user.role == User.Role.DIRECTOR and status == Complaint.Status.REVIEW:
        actions.extend(["close", "return"])
    specialist = (user.role == User.Role.TECHNICIAN and complaint.department == Complaint.Department.TECHNICAL) or (
        user.role == User.Role.FINANCE and complaint.department == Complaint.Department.FINANCE)
    if specialist and complaint.assignee_id == user.pk:
        if status in (Complaint.Status.ASSIGNED, Complaint.Status.RETURNED):
            actions.append("start")
        if status == Complaint.Status.IN_PROGRESS:
            actions.append("submit")
    return actions


class ActionError(Exception):
    def __init__(self, errors, *, forbidden=False):
        self.errors = errors
        self.forbidden = forbidden
        super().__init__("Invalid complaint transition")


def apply_action(user, complaint, action, assignee=None, note=""):
    """Caller must hold the complaint row lock inside transaction.atomic()."""
    if action not in permitted_actions(user, complaint):
        raise ActionError({"action": ["هذا الإجراء غير متاح لحسابك أو لحالة البلاغ."]}, forbidden=True)
    note = note.strip()
    errors = {}
    previous = complaint.assignee
    if action in ("assign", "reassign"):
        expected_role = User.Role.FINANCE if complaint.department == Complaint.Department.FINANCE else User.Role.TECHNICIAN
        # Also lock the assignee: account updates must not deactivate during assignment.
        assignee = User.objects.select_for_update().filter(pk=assignee.pk if assignee else None, role=expected_role, is_active=True).first()
        if not assignee:
            errors["assignee"] = ["اختر مختصًا فعالًا من القسم المسؤول."]
        elif action == "reassign" and previous and previous.pk == assignee.pk:
            errors["assignee"] = ["اختر مختصًا آخر لإعادة الإسناد."]
        if action == "reassign" and not note:
            errors["note"] = ["دوّن سبب إعادة الإسناد؛ يبقى تاريخ العمل السابق محفوظًا."]
        if not errors:
            complaint.assignee = assignee
            complaint.status = Complaint.Status.ASSIGNED
    elif action == "start":
        complaint.status = Complaint.Status.IN_PROGRESS
    elif action == "submit":
        if not note:
            errors["note"] = ["دوّن نتيجة المعالجة قبل إرسالها للمراجعة."]
        else:
            complaint.status = Complaint.Status.REVIEW
    elif action == "return":
        if not note:
            errors["note"] = ["اشرح سبب إعادة البلاغ."]
        else:
            complaint.status = Complaint.Status.RETURNED
    elif action == "close":
        complaint.status = Complaint.Status.CLOSED
    if errors:
        raise ActionError(errors)
    complaint.save(update_fields=["status", "assignee"])
    return ComplaintEvent.objects.create(complaint=complaint, actor=user, action=action, note=note,
        previous_assignee=previous, new_assignee=complaint.assignee)


@login_required
@require_GET
def queue(request):
    if request.user.role not in (User.Role.DIRECTOR, User.Role.TECHNICIAN, User.Role.FINANCE):
        return render(request, "portal/forbidden.html", status=403)
    reports = visible_reports(request.user).select_related("assignee").order_by("-received_at", "-pk")
    return render(request, "portal/work_queue.html", {"page": Paginator(reports, 20).get_page(request.GET.get("page"))})


@login_required
@require_GET
def detail(request, complaint_id):
    from .work_results import task_card
    if request.user.role not in (User.Role.DIRECTOR, User.Role.TECHNICIAN, User.Role.FINANCE):
        return render(request, "portal/forbidden.html", status=403)
    complaint = get_object_or_404(visible_reports(request.user).select_related("assignee").prefetch_related("events__actor", "photos"), pk=complaint_id)
    return render(request, "portal/work_detail.html", {"complaint": complaint, "actions": permitted_actions(request.user, complaint),
        "form": ActionForm(complaint=complaint), "events": complaint.events.all(),
        "work_task": task_card(request.user, complaint)})


@require_GET
def photo(request, photo_id):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "login_required"}, status=401)
    if request.user.role not in (User.Role.DIRECTOR, User.Role.TECHNICIAN, User.Role.FINANCE):
        return JsonResponse({"error": "role_forbidden"}, status=403)
    image = get_object_or_404(ComplaintPhoto, pk=photo_id, complaint__in=visible_reports(request.user))
    try:
        file = image.image.open("rb")
    except FileNotFoundError:
        return JsonResponse({"error": "photo_unavailable"}, status=404)
    response = FileResponse(file, content_type="image/jpeg", filename="complaint-photo.jpg")
    response["Cache-Control"] = "no-store, private"
    return response


@login_required
@require_POST
def act(request, complaint_id):
    from .work_results import task_card
    if request.user.role not in (User.Role.DIRECTOR, User.Role.TECHNICIAN, User.Role.FINANCE):
        return render(request, "portal/forbidden.html", status=403)
    with transaction.atomic():
        # Account edits and offline results lock users before complaints. Lock
        # actor/new assignee in stable ID order to keep that order consistent.
        user_ids = {request.user.pk}
        try:
            submitted_assignee = int(request.POST.get("assignee", ""))
            if 0 < submitted_assignee < 2 ** 63:
                user_ids.add(submitted_assignee)
        except (ValueError, TypeError):
            pass
        locked_users = {user.pk: user for user in User.objects.select_for_update().filter(pk__in=user_ids).order_by("pk")}
        request.user = locked_users[request.user.pk]
        if not request.user.is_active or request.user.role not in (User.Role.DIRECTOR, User.Role.TECHNICIAN, User.Role.FINANCE):
            return render(request, "portal/forbidden.html", status=403)
        complaint = get_object_or_404(visible_reports(request.user).select_for_update(), pk=complaint_id)
        form = ActionForm(request.POST, complaint=complaint)
        if not form.is_valid():
            return render(request, "portal/work_detail.html", {"complaint": complaint, "actions": permitted_actions(request.user, complaint),
                "form": form, "events": complaint.events.select_related("actor").all(), "work_task": task_card(request.user, complaint)}, status=400)
        try:
            apply_action(request.user, complaint, form.cleaned_data["action"], form.cleaned_data["assignee"], form.cleaned_data["note"])
        except ActionError as error:
            if error.forbidden:
                return render(request, "portal/forbidden.html", status=403)
            for field, errors in error.errors.items():
                for message in errors:
                    form.add_error(field, message)
            return render(request, "portal/work_detail.html", {"complaint": complaint, "actions": permitted_actions(request.user, complaint),
                "form": form, "events": complaint.events.select_related("actor").all(), "work_task": task_card(request.user, complaint)}, status=400)
    messages.success(request, "حُفظ إجراء البلاغ في سجل المتابعة.")
    return redirect("work_detail", complaint_id=complaint_id)
