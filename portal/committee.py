"""Guided fictional role testing, enabled only by the dedicated loopback demo settings."""
import json
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import login
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET, require_POST

from .models import User, Complaint, Violation

ROLES = [('citizen', 'المستفيد', 'تقديم بلاغ ومتابعة وصوله وحالته'),
    ('director', 'المدير الفني', 'الإسناد والمراجعة والإغلاق واعتماد النماذج'),
    ('technician', 'الفني', 'تنفيذ مهمة وتسجيل مخالفة ميدانية'),
    ('system_manager', 'مسؤول النظام', 'استقبال المخالفة وإحالتها للسكرتارية'),
    ('secretariat', 'السكرتارية', 'تحرير المحاضر والمذكرات وإحالتها للمتابعة'),
    ('followup', 'قسم المتابعة', 'تسجيل الإجراءات الميدانية وإعادة النتائج'),
    ('finance', 'الإدارة المالية', 'اعتماد الكمية والرسوم وتأكيد سند السداد')]


def context(request):
    return {'committee_demo': getattr(settings, 'WASH_COMMITTEE_DEMO', False)}


def manifest(request):
    if not getattr(settings, 'WASH_COMMITTEE_DEMO', False) or urlsplit('//' + request.get_host()).hostname not in ('localhost', '127.0.0.1', '::1'):
        raise Http404
    try:
        data = json.loads((settings.WASH_COMMITTEE_STATE / 'committee.json').read_text())
    except (FileNotFoundError, ValueError):
        raise Http404
    return data


@require_GET
def guide(request):
    data = manifest(request)
    complaints = Complaint.objects.filter(pk__in=data['complaints']).order_by('pk')
    violation = Violation.objects.filter(pk=data['violation']).first()
    roles = [{'code': code, 'label': label, 'description': description} for code, label, description in ROLES]
    response = render(request, 'portal/committee.html', {'roles': roles, 'complaints': complaints, 'violation': violation})
    response['Cache-Control'] = 'no-store, private'
    return response


@require_POST
def enter(request):
    data = manifest(request)
    role = request.POST.get('role')
    if role not in data['users'] or role not in dict((code, label) for code, label, _ in ROLES):
        raise Http404
    user = User.objects.filter(pk=data['users'][role], username='committee_' + role, role=role, is_active=True).first()
    if not user or user.has_usable_password():
        raise Http404
    # These accounts have unusable passwords. This shortcut cannot authenticate
    # real accounts and the normal configuration always returns 404 here.
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    return redirect('workspace' if role == 'citizen' else 'staff_workspace')
