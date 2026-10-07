"""Versioned template library, director approval and private generated report snapshots."""
import hashlib
import json
import uuid

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import F, OuterRef, Subquery
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .models import ReportTemplate, ReportTemplateVersion, ReportTemplateEvent, SavedReport, User, Violation, Complaint
from .template_engine import COMMON, FIELDS, render_spec, record_values, sample_values
from .template_forms import GenerateReportForm, TemplateForm
from .report_scope import ReportScopeForm
from .violations import visible as visible_violations
from .workflow import visible_reports as visible_complaints

EDIT_ROLES = (User.Role.DIRECTOR, User.Role.SECRETARIAT)


def denied(request):
    return render(request, 'portal/forbidden.html', status=403)


def available(source):
    return ReportTemplateVersion.objects.filter(template__enabled=True, template__source=source,
        pk=F('template__active_version_id'), approved_at__isnull=False).select_related('template').order_by('name', 'pk')


def editor_context(form, version=None):
    return {'form': form, 'version': version, 'field_groups': [(source, [(label, '{{' + label + '}}') for label in (*COMMON, *labels)]) for source, labels in FIELDS.items()]}


def unsaved_preview(request, form):
    content = render_spec(form.spec(), sample_values(form.cleaned_data['source'], form.cleaned_data['custom_fields']))
    return render(request, 'portal/custom_report_print.html', {'content': content, 'preview': True,
        'version': {'name': form.cleaned_data['name']}})


@login_required
@require_GET
def library(request):
    if not request.user.is_employee:
        return denied(request)
    latest_name = ReportTemplateVersion.objects.filter(template=OuterRef('pk')).order_by('-number').values('name')[:1]
    roots = ReportTemplate.objects.select_related('active_version').annotate(latest_name=Subquery(latest_name)).order_by('-pk')
    editor = request.user.role in EDIT_ROLES
    if not editor:
        roots = roots.filter(enabled=True, active_version__isnull=False)
    page = Paginator(roots, 20).get_page(request.GET.get('page'))
    for root in page:
        root.latest_version = root.versions.first() if editor else root.active_version
        root.latest_name = root.latest_version.name
    return render(request, 'portal/template_library.html', {'page': page, 'can_edit': editor})


@login_required
@require_http_methods(['GET', 'POST'])
def create(request):
    if request.user.role not in EDIT_ROLES:
        return denied(request)
    form = TemplateForm(request.POST if request.method == 'POST' else None, request.FILES or None,
        initial={'client_id': uuid.uuid4()})
    if request.method == 'POST' and form.is_valid():
        if request.POST.get('action') == 'preview':
            return unsaved_preview(request, form)
        data = form.cleaned_data
        spec = form.spec()
        with transaction.atomic():
            root, created = ReportTemplate.objects.get_or_create(created_by=request.user, client_id=data['client_id'],
                defaults={'source': data['source']})
            if created:
                version = ReportTemplateVersion.objects.create(template=root, client_id=data['client_id'], number=1,
                    name=data['name'], spec=spec, created_by=request.user)
                ReportTemplateEvent.objects.create(template=root, version=version, actor=request.user, action='created')
            else:
                version = root.versions.get(number=1)
                if root.source != data['source'] or version.name != data['name'] or version.spec != spec:
                    return HttpResponse('حُفظ الطلب سابقًا بمحتوى مختلف. افتح نموذجًا جديدًا.', status=409)
        return redirect('report_template_detail', template_id=root.pk)
    return render(request, 'portal/template_form.html', editor_context(form), status=400 if request.method == 'POST' else 200)


@login_required
@require_GET
def detail(request, template_id):
    if not request.user.is_employee:
        return denied(request)
    root = get_object_or_404(ReportTemplate.objects.select_related('active_version'), pk=template_id)
    editor = request.user.role in EDIT_ROLES
    if not editor and (not root.enabled or not root.active_version_id):
        return denied(request)
    versions = root.versions.select_related('created_by', 'approved_by') if editor else root.versions.filter(pk=root.active_version_id)
    return render(request, 'portal/template_detail.html', {'root': root, 'versions': versions,
        'events': root.events.select_related('actor', 'version') if editor else [], 'can_edit': editor,
        'can_approve': request.user.role == User.Role.DIRECTOR})


@login_required
@require_http_methods(['GET', 'POST'])
def edit(request, version_id):
    if request.user.role not in EDIT_ROLES:
        return denied(request)
    version = get_object_or_404(ReportTemplateVersion.objects.select_related('template'), pk=version_id)
    initial = {**version.spec, 'name': version.name, 'source': version.template.source,
        'expected_revision': version.template.revision, 'client_id': uuid.uuid4()}
    for key in ('table_fields', 'custom_fields'):
        initial[key] = '\n'.join(initial[key])
    form = TemplateForm(request.POST if request.method == 'POST' else None, request.FILES or None,
        source=version.template.source, initial=initial)
    if request.method == 'POST' and form.is_valid():
        if request.POST.get('action') == 'preview':
            return unsaved_preview(request, form)
        data = form.cleaned_data
        spec = form.spec()
        with transaction.atomic():
            root = ReportTemplate.objects.select_for_update().get(pk=version.template_id)
            existing = root.versions.filter(client_id=data['client_id']).first()
            if existing:
                if existing.name != data['name'] or existing.spec != spec:
                    return HttpResponse('حُفظ طلب التحرير سابقًا بمحتوى مختلف.', status=409)
                return redirect('report_template_detail', template_id=root.pk)
            if data['expected_revision'] != root.revision:
                form.add_error(None, 'أُضيف إصدار أحدث أثناء التحرير. افتح أحدث إصدار ثم أعد التعديل.')
                return render(request, 'portal/template_form.html', editor_context(form, version), status=409)
            root.revision += 1
            root.save(update_fields=['revision'])
            new = ReportTemplateVersion.objects.create(template=root, number=root.revision, name=data['name'], spec=spec,
                created_by=request.user, client_id=data['client_id'])
            ReportTemplateEvent.objects.create(template=root, version=new, actor=request.user, action='revised')
        messages.success(request, 'حُفظ إصدار جديد للمراجعة. يستمر استخدام الإصدار المعتمد حتى اعتماد الجديد.')
        return redirect('report_template_detail', template_id=root.pk)
    return render(request, 'portal/template_form.html', editor_context(form, version), status=400 if request.method == 'POST' else 200)


@login_required
@require_POST
def approve(request, version_id):
    if request.user.role != User.Role.DIRECTOR:
        return denied(request)
    version = get_object_or_404(ReportTemplateVersion, pk=version_id)
    with transaction.atomic():
        root = ReportTemplate.objects.select_for_update().get(pk=version.template_id)
        if root.active_version_id == version.pk and root.enabled:
            return redirect('report_template_detail', template_id=root.pk)
        if version.approved_at:
            return HttpResponse('هذا إصدار معتمد سابقًا. احفظ إصدارًا جديدًا ثم اعتمده لإعادة تفعيل النموذج.', status=409)
        if version.number != root.revision or request.POST.get('expected_revision') != str(root.revision):
            return HttpResponse('ظهرت نسخة أحدث. راجع آخر إصدار قبل الاعتماد.', status=409)
        if not version.approved_at:
            version.approved_by = request.user
            version.approved_at = timezone.now()
            version.save(update_fields=['approved_by', 'approved_at'])
        root.active_version = version
        root.enabled = True
        root.save(update_fields=['active_version', 'enabled'])
        ReportTemplateEvent.objects.create(template=root, version=version, actor=request.user, action='approved')
    messages.success(request, 'اعتُمد النموذج وأصبح متاحًا لإنشاء التقارير الجديدة.')
    return redirect('report_template_detail', template_id=root.pk)


@login_required
@require_POST
def retire(request, template_id):
    if request.user.role != User.Role.DIRECTOR:
        return denied(request)
    with transaction.atomic():
        root = get_object_or_404(ReportTemplate.objects.select_for_update(), pk=template_id)
        if request.POST.get('expected_revision') != str(root.revision):
            return HttpResponse('افتح أحدث نسخة قبل إيقاف النموذج.', status=409)
        if request.POST.get('expected_active_version') != str(root.active_version_id):
            return HttpResponse('تغير الإصدار المعتمد. راجع النموذج قبل إيقافه.', status=409)
        if root.enabled:
            root.enabled = False
            root.save(update_fields=['enabled'])
            ReportTemplateEvent.objects.create(template=root, version=root.active_version, actor=request.user, action='retired')
    return redirect('report_template_detail', template_id=root.pk)


@login_required
@require_GET
def preview(request, version_id):
    if not request.user.is_employee:
        return denied(request)
    version = get_object_or_404(ReportTemplateVersion.objects.select_related('template'), pk=version_id)
    if request.user.role not in EDIT_ROLES and (not version.template.enabled or version.template.active_version_id != version.pk):
        return denied(request)
    content = render_spec(version.spec, sample_values(version.template.source, version.spec['custom_fields']))
    return render(request, 'portal/custom_report_print.html', {'content': content, 'version': version, 'preview': True})


def scoped_record(user, source, record_id):
    if source == 'violation':
        return get_object_or_404(visible_violations(user).select_related('reporter'), pk=record_id)
    if source == 'complaint':
        return get_object_or_404(visible_complaints(user).select_related('assignee'), pk=record_id)
    if source == 'summary' and user.role == User.Role.DIRECTOR and record_id == 0:
        return None
    from django.http import Http404
    raise Http404


@login_required
@require_GET
def choose(request, source, record_id):
    if not request.user.is_employee:
        return denied(request)
    item = scoped_record(request.user, source, record_id)
    scope = ReportScopeForm(request.GET) if source == 'summary' else None
    if scope is not None and not scope.is_valid():
        return render(request, 'portal/report_choose.html', {'item': item, 'source': source, 'record_id': record_id,
            'scope_form': scope, 'versions': [], 'page': []}, status=400)
    reports = SavedReport.objects.filter(**({'violation': item} if source == 'violation' else {'complaint': item} if source == 'complaint'
        else {'complaint__isnull': True, 'violation__isnull': True})).select_related('template_version')
    return render(request, 'portal/report_choose.html', {'item': item, 'source': source, 'record_id': record_id,
        'versions': available(source), 'page': Paginator(reports, 20).get_page(request.GET.get('page')),
        'scope_form': scope, 'scope_query': scope.query() if scope is not None else '',
        'scope_snapshot': scope.snapshot() if scope is not None else None})


@login_required
@require_http_methods(['GET', 'POST'])
def generate(request, version_id, record_id):
    if not request.user.is_employee:
        return denied(request)
    version = get_object_or_404(ReportTemplateVersion.objects.select_related('template'), pk=version_id, approved_at__isnull=False)
    item = scoped_record(request.user, version.template.source, record_id)
    form = GenerateReportForm(version.spec, request.POST if request.method == 'POST' else None,
        initial={'client_id': uuid.uuid4()})
    scope = ReportScopeForm(request.POST if request.method == 'POST' else request.GET) if version.template.source == 'summary' else None
    scope_valid = scope is None or scope.is_valid()
    if request.method == 'POST' and form.is_valid() and scope_valid:
        data = form.cleaned_data
        request_payload = {**data, 'version_id': version.pk, 'source': version.template.source, 'record_id': record_id}
        if scope is not None and scope.snapshot()['filters']:
            request_payload['scope'] = scope.snapshot()['filters']
        digest = hashlib.sha256(json.dumps(request_payload, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()
        with transaction.atomic():
            # Serialize generations with template activation/retirement.
            root = ReportTemplate.objects.select_for_update().get(pk=version.template_id)
            User.objects.select_for_update(no_key=True).only('pk').get(pk=request.user.pk)
            existing = SavedReport.objects.filter(created_by=request.user, client_id=data['client_id']).first()
            if existing:
                if existing.payload_digest != digest:
                    return HttpResponse('حُفظ الطلب نفسه ببيانات مختلفة.', status=409)
                return redirect('saved_report_print', report_id=existing.pk)
            if not root.enabled or root.active_version_id != version.pk:
                return HttpResponse('هذا الإصدار غير متاح للتقارير الجديدة. اختر النموذج المعتمد الحالي.', status=409)
            # Lock a record while reading its financial and workflow snapshot.
            if version.template.source == 'violation':
                item = get_object_or_404(visible_violations(request.user).select_for_update(), pk=item.pk)
            elif version.template.source == 'complaint':
                item = get_object_or_404(visible_complaints(request.user).select_for_update(), pk=item.pk)
            values = record_values(root.source, item, registers=scope.registers() if scope is not None else None)
            values.update({'تاريخ التقرير': data['document_date'].strftime('%Y/%m/%d'), 'رقم المستند': data['official_number'],
                'الجهة المخاطبة': data['recipient'], 'معد التقرير': request.user.display_name})
            values.update({label: data[key] for key, label in form.custom_map.items()})
            content = render_spec(version.spec, values)
            if scope is not None:
                content['scope'] = scope.snapshot()
            saved = SavedReport.objects.create(template_version=version, created_by=request.user, client_id=data['client_id'],
                payload_digest=digest, content=content, violation=item if root.source == 'violation' else None,
                complaint=item if root.source == 'complaint' else None)
        return redirect('saved_report_print', report_id=saved.pk)
    if not version.template.enabled or version.template.active_version_id != version.pk:
        return HttpResponse('اختر الإصدار المعتمد الحالي من مكتبة النماذج.', status=409)
    return render(request, 'portal/report_generate.html', {'version': version, 'form': form, 'item': item,
        'source': version.template.source, 'record_id': record_id,
        'scope_form': scope, 'scope_query': scope.query() if scope is not None and scope_valid else ''},
        status=400 if not scope_valid else 200)


@login_required
@require_GET
def saved_print(request, report_id):
    if not request.user.is_employee:
        return denied(request)
    report = get_object_or_404(SavedReport.objects.select_related('template_version__template'), pk=report_id)
    record_id = report.violation_id or report.complaint_id or 0
    scoped_record(request.user, report.template_version.template.source, record_id)
    return render(request, 'portal/custom_report_print.html', {'content': report.content, 'report': report,
        'version': report.template_version, 'record_id': record_id})
