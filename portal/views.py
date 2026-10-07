import json
import hashlib
from functools import wraps

from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import PasswordChangeView
from django.db import connection, transaction
from django.db.models import Q
from django.http import HttpResponse, JsonResponse
from django.middleware.csrf import get_token
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from .forms import CitizenRegistrationForm, DraftForm, EmployeeCreationForm, EmployeeManagementForm
from .models import AccountEvent, Complaint, Draft, User
from .catalog import COMPLAINT_TYPES, NEIGHBORHOODS


def employee_required(view):
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_employee:
            return render(request, "portal/forbidden.html", status=403)
        return view(request, *args, **kwargs)
    return wrapped


def director_required(view):
    @wraps(view)
    @employee_required
    def wrapped(request, *args, **kwargs):
        if request.user.role != User.Role.DIRECTOR:
            return render(request, "portal/forbidden.html", status=403)
        return view(request, *args, **kwargs)
    return wrapped


@require_GET
def home(request):
    return render(request, "portal/home.html")


@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect("workspace")
    if request.method == "POST":
        from .auth_views import reserve_attempt, block_response
        delay = reserve_attempt(request, "registration")
        if delay:
            return block_response(request, CitizenRegistrationForm(), "registration/register.html", delay)
    form = CitizenRegistrationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user)
        return redirect("workspace")
    return render(request, "registration/register.html", {"form": form})


@login_required
@require_GET
def workspace(request):
    return render(request, "portal/workspace.html", {"complaint_types": COMPLAINT_TYPES, "neighborhoods": NEIGHBORHOODS})


@employee_required
@require_GET
def staff_workspace(request):
    return render(request, "portal/staff.html")


@director_required
def employees(request):
    form = EmployeeCreationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            actor = User.objects.select_for_update().get(pk=request.user.pk)
            if not actor.is_active or actor.role != User.Role.DIRECTOR:
                return render(request, "portal/forbidden.html", status=403)
            employee = form.save()
            AccountEvent.objects.create(actor=actor, target=employee, new_role=employee.role)
        messages.success(request, "تم إنشاء حساب الموظف. عليه تغيير كلمة المرور عند أول دخول.")
        return redirect("employees")
    return render(request, "portal/employees.html", {
        "form": form, "employees": User.objects.exclude(role=User.Role.CITIZEN).order_by("first_name", "username"),
    })


@director_required
def employee_manage(request, employee_id):
    with transaction.atomic():
        # Lock target and responsible users in stable ID order. This shares the
        # users-before-complaints order used by assignment/result submissions.
        user_ids = set(User.objects.filter(Q(role=User.Role.DIRECTOR, is_active=True) |
            Q(role=User.Role.FINANCE, is_active=True, is_department_responsible=True)).values_list("pk", flat=True))
        user_ids.update((request.user.pk, employee_id))
        locked_users = {user.pk: user for user in User.objects.select_for_update().filter(pk__in=user_ids).order_by("pk")}
        actor = locked_users.get(request.user.pk)
        if not actor or not actor.is_active or actor.role != User.Role.DIRECTOR:
            return render(request, "portal/forbidden.html", status=403)
        request.user = actor
        employee = locked_users.get(employee_id)
        if not employee or employee.role == User.Role.CITIZEN:
            from django.http import Http404
            raise Http404

        old_role, old_active, old_responsible = employee.role, employee.is_active, employee.is_department_responsible
        old_name, old_phone = employee.first_name, employee.phone
        form = EmployeeManagementForm(request.POST or None, instance=employee)
        if request.method == "POST" and form.is_valid():
            changing_role = form.cleaned_data["role"] != old_role
            deactivating = old_active and not form.cleaned_data["is_active"]
            removing_responsibility = old_responsible and (not form.cleaned_data["is_department_responsible"] or changing_role or deactivating)
            if employee.pk == request.user.pk and (changing_role or deactivating):
                form.add_error(None, "لا يمكنك إيقاف حسابك الحالي أو تغيير دوره؛ استخدم حساب مدير فني آخر.")
            if (changing_role or deactivating) and Complaint.objects.filter(assignee=employee).exclude(status=Complaint.Status.CLOSED).exists():
                form.add_error(None, "لهذا الموظف بلاغات قائمة. انقل البلاغات إلى مختص آخر قبل إيقافه أو تغيير دوره؛ لا تُحذف سجلاته.")
            if old_role == User.Role.DIRECTOR and (changing_role or deactivating) and not User.objects.filter(role=User.Role.DIRECTOR, is_active=True).exclude(pk=employee.pk).exists():
                form.add_error(None, "يجب إبقاء مدير فني فعال لإدارة الحسابات ومراجعة الإغلاق.")
            if removing_responsibility and Complaint.objects.filter(department=Complaint.Department.FINANCE).exclude(status=Complaint.Status.CLOSED).exists() and not User.objects.filter(role=User.Role.FINANCE, is_active=True, is_department_responsible=True).exclude(pk=employee.pk).exists():
                form.add_error(None, "عيّن مسؤول إسناد مالي آخر قبل إزالة آخر مسؤول مع وجود بلاغات مالية قائمة.")
            if not form.errors:
                updated = form.save(commit=False)
                if form.cleaned_data.get("password1"):
                    updated.set_password(form.cleaned_data["password1"])
                    updated.must_change_password = True
                updated.save()
                if deactivating or changing_role or form.cleaned_data.get("password1"):
                    from .account_security import revoke_sessions
                    revoke_sessions(updated.pk)
                changes = []
                if old_name != updated.first_name:
                    changes.append("تعديل الاسم: " + old_name + " ← " + updated.first_name)
                if old_phone != updated.phone:
                    changes.append("تعديل رقم الهاتف: " + old_phone + " ← " + updated.phone)
                if changing_role:
                    changes.append("تغيير الدور: " + old_role + " ← " + updated.role)
                if old_active != updated.is_active:
                    changes.append("تفعيل الحساب" if updated.is_active else "إيقاف الحساب")
                if old_responsible != updated.is_department_responsible:
                    changes.append("تعديل مسؤولية الإسناد المالي")
                if form.cleaned_data.get("password1"):
                    changes.append("إعادة تعيين كلمة المرور المؤقتة وإلزام تغييرها")
                AccountEvent.objects.create(actor=request.user, target=updated, action="updated", previous_role=old_role, new_role=updated.role, note="؛ ".join(changes) + "\nالسبب: " + form.cleaned_data["reason"])
                messages.success(request, "حُفظ تعديل الحساب في سجل التدقيق. كلمات المرور الجديدة مؤقتة وتتطلب تغييرًا عند الدخول.")
                return redirect("employee_manage", employee_id=employee.pk)
        return render(request, "portal/employee_manage.html", {"form": form, "employee": employee,
            "account_events": employee.account_events.select_related("actor").order_by("-created_at", "-pk")})


class ChangePasswordView(PasswordChangeView):
    template_name = "registration/password_change.html"
    success_url = reverse_lazy("workspace")

    def form_valid(self, form):
        result = super().form_valid(form)
        self.request.user.must_change_password = False
        self.request.user.save(update_fields=["must_change_password"])
        messages.success(self.request, "تم تغيير كلمة المرور.")
        return result


@require_GET
def app_shell(request):
    # A public copy of the regular workspace, used only as a navigation fallback.
    # Never cache employee HTML, tokens or messages from an authenticated session.
    return render(request, "portal/shell.html", {"user": AnonymousUser(), "messages": [], "is_shell": True,
                                               "complaint_types": COMPLAINT_TYPES, "neighborhoods": NEIGHBORHOODS})


@require_GET
def service_worker(request):
    from django.conf import settings
    source = (settings.BASE_DIR / "static" / "sw.js").read_text()
    assets = list(sorted((settings.BASE_DIR / "templates").rglob("*.html")))
    assets.append(settings.BASE_DIR / "portal" / "catalog.py")
    assets.extend(sorted((settings.BASE_DIR / "static").rglob("*")))
    fingerprint = hashlib.sha256(b"".join(path.read_bytes() for path in assets if path.is_file())).hexdigest()[:16]
    response = HttpResponse(source.replace("__SHELL_VERSION__", fingerprint), content_type="application/javascript")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    return response


@require_GET
def session_info(request):
    if not request.user.is_authenticated:
        return JsonResponse({"error": "login_required"}, status=401)
    return JsonResponse({
        "id": request.user.pk, "name": request.user.display_name, "phone": request.user.phone,
        "role": request.user.role, "csrf_token": get_token(request),
    })


@require_GET
def health(request):
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()
    return JsonResponse({"status": "ok", "database": "postgresql"})


def api_authenticated(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"error": "login_required"}, status=401)
        return view(request, *args, **kwargs)
    return wrapped


@api_authenticated
@require_POST
def sync_draft(request):
    if request.content_type != "application/json":
        return JsonResponse({"error": "json_required"}, status=415)
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return JsonResponse({"error": "invalid_json"}, status=400)
    if not isinstance(data, dict):
        return JsonResponse({"error": "invalid_payload"}, status=400)
    form = DraftForm(data)
    if not form.is_valid():
        return JsonResponse({"error": "invalid_payload", "fields": form.errors.get_json_data()}, status=400)
    if form.cleaned_data["owner_id"] != request.user.pk:
        return JsonResponse({"error": "account_changed"}, status=409)
    expected = Draft.Kind.VIOLATION if request.user.is_employee else Draft.Kind.COMPLAINT
    if form.cleaned_data["kind"] != expected:
        return JsonResponse({"error": "kind_forbidden"}, status=403)
    # Owner comes only from the authenticated session, never from the supplied JSON.
    with transaction.atomic():
        draft, created = Draft.objects.get_or_create(
            owner=request.user, client_id=form.cleaned_data["client_id"],
            defaults={key: form.cleaned_data[key] for key in ("kind", "title", "description")},
        )
        if not created and any(getattr(draft, field) != form.cleaned_data[field] for field in ("kind", "title", "description")):
            return JsonResponse({"error": "id_conflict"}, status=409)
    return JsonResponse({"client_id": str(draft.client_id), "saved": True}, status=201 if created else 200)


@api_authenticated
@require_GET
def my_drafts(request):
    drafts = request.user.drafts.values("client_id", "kind", "title", "description", "received_at")
    return JsonResponse({"drafts": list(drafts)})
