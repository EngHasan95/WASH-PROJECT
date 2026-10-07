"""Read-only operational counts derived from current records."""
from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.shortcuts import render
from django.views.decorators.http import require_GET

from .models import Complaint, User, Violation
from .report_scope import ReportScopeForm


@login_required
@require_GET
def director_report(request):
    if request.user.role != User.Role.DIRECTOR:
        return render(request, "portal/forbidden.html", status=403)
    scope = ReportScopeForm(request.GET)
    valid = scope.is_valid()
    complaints, violations = scope.registers() if valid else (Complaint.objects.none(), Violation.objects.none())
    statuses = {row["status"]: row["total"] for row in complaints.values("status").annotate(total=Count("pk"))}
    departments = {row["department"]: row["total"] for row in complaints.values("department").annotate(total=Count("pk"))}
    violation_statuses = {row["status"]: row["total"] for row in violations.values("status").annotate(total=Count("pk"))}
    technicians = User.objects.filter(role=User.Role.TECHNICIAN).annotate(
        assigned=Count("assigned_complaints", filter=Q(assigned_complaints__in=complaints), distinct=True)).order_by("first_name", "username")
    if valid and scope.cleaned_data['technician']:
        technicians = technicians.filter(pk=scope.cleaned_data['technician'].pk)
    return render(request, "portal/reports.html", {
        "total": sum(statuses.values()), "statuses": [(label, statuses.get(code, 0)) for code, label in Complaint.Status.choices],
        "departments": [(label, departments.get(code, 0)) for code, label in Complaint.Department.choices],
        "violation_total": sum(violation_statuses.values()),
        "violation_statuses": [(label, violation_statuses.get(code, 0)) for code, label in Violation.Status.choices],
        "technicians": technicians,
        "scope_form": scope, "scope_query": scope.query() if valid else '',
        "scope_snapshot": scope.snapshot() if valid else None,
    }, status=200 if valid else 400)
