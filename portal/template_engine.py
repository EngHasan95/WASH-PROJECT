"""Plain-text placeholders and a finite layout vocabulary; no executable templates."""
import re
from collections import Counter

from django.utils import timezone
from django.db.models import Count

from .models import Complaint, Violation

COMMON = ('تاريخ التقرير', 'رقم المستند', 'الجهة المخاطبة', 'معد التقرير')
FIELDS = {
    'violation': ('رقم الملف', 'اسم المخالف', 'العنوان', 'المنطقة', 'نوع النشاط', 'نوع المخالفة', 'حالة الملف',
        'الاستهلاك التقديري', 'مدة التقدير', 'الفني المبلغ', 'ملاحظات الفني', 'الكمية المعتمدة', 'إجمالي الرسوم', 'رقم سند السداد'),
    'complaint': ('رقم البلاغ', 'اسم المستفيد', 'الهاتف', 'رقم الاشتراك', 'رقم العداد', 'نوع البلاغ', 'الحي',
        'العنوان', 'أقرب معلم', 'وصف البلاغ', 'حالة البلاغ', 'القسم', 'المختص', 'تاريخ الاستلام'),
    'summary': ('إجمالي البلاغات', 'البلاغات المغلقة', 'البلاغات المفتوحة', 'إجمالي المخالفات', 'المخالفات المغلقة'),
}
TOKEN = re.compile(r'\{\{([^{}]+)\}\}')
LABEL = re.compile(r'^[\w\u0600-\u06ff ()،\-/]{1,70}$')
DEFAULT_HEADER = 'الجمهورية اليمنية\nوزارة المياه والبيئة\nالمؤسسة العامة للمياه والصرف الصحي\nفرع مأرب'


def replace(text, values):
    return TOKEN.sub(lambda match: str(values.get(match.group(1).strip(), '—')), text)


def render_spec(spec, values):
    return {'header': replace(spec['header'], values).splitlines(), 'title': replace(spec['title'], values),
        'body': replace(spec['body'], values), 'footer': replace(spec['footer'], values).splitlines(),
        'rows': [(label, str(values.get(label, '—'))) for label in spec['table_fields']],
        'layout': {key: spec[key] for key in ('orientation', 'font_size', 'margin', 'accent', 'logo', 'alignment')},
        'official_number': values.get('رقم المستند', ''), 'date': values.get('تاريخ التقرير', ''),
        'recipient': values.get('الجهة المخاطبة', ''), 'prepared_by': values.get('معد التقرير', ''),
        'record_reference': values.get('رقم الملف', values.get('رقم البلاغ', 'تقرير إداري مجمع'))}


def record_values(source, record=None, registers=None):
    if source == 'violation':
        item = record
        values = dict(zip(FIELDS[source][:11], [item.reference, item.person_name, item.address, item.area,
            item.get_activity_display(), item.get_kind_display(), item.get_status_display(),
            f'{item.estimated_cubic_meters} متر مكعب', f'{item.estimate_days} يوم', item.reporter.display_name, item.description or '—']))
        try:
            settlement = item.settlement
        except Violation.settlement.RelatedObjectDoesNotExist:
            values.update({'الكمية المعتمدة': 'لم تعتمد بعد', 'إجمالي الرسوم': 'لم تعتمد بعد', 'رقم سند السداد': 'لم يؤكد بعد'})
        else:
            values.update({'الكمية المعتمدة': f'{settlement.approved_units} {settlement.unit_description}',
                'إجمالي الرسوم': f'{settlement.total} ريال يمني', 'رقم سند السداد': settlement.payment_receipt or 'لم يؤكد بعد'})
        return values
    if source == 'complaint':
        item = record
        return dict(zip(FIELDS[source], [item.reference, item.reporter_name, item.phone, item.subscription_number or '—',
            item.meter_number or '—', item.other_type or item.get_complaint_type_display(),
            item.other_neighborhood or item.get_neighborhood_display(), item.address, item.landmark, item.description,
            item.get_status_display(), item.get_department_display(), item.assignee.display_name if item.assignee else 'لم يسند بعد',
            timezone.localtime(item.received_at).strftime('%Y/%m/%d')]))
    # One query per register, rather than independent totals that could disagree.
    complaint_rows, violation_rows = registers if registers is not None else (Complaint.objects.all(), Violation.objects.all())
    complaints = Counter({row['status']: row['total'] for row in complaint_rows.values('status').annotate(total=Count('pk'))})
    violations = Counter({row['status']: row['total'] for row in violation_rows.values('status').annotate(total=Count('pk'))})
    total = sum(complaints.values())
    return dict(zip(FIELDS['summary'], [total, complaints['closed'], total - complaints['closed'], sum(violations.values()), violations['closed']]))


def sample_values(source, custom_fields):
    values = {label: 'بيانات تجريبية' for label in (*COMMON, *FIELDS[source], *custom_fields)}
    values.update({'تاريخ التقرير': '2026/01/01', 'رقم المستند': 'TEST-001', 'معد التقرير': 'موظف خيالي',
        'اسم المخالف': 'شخص خيالي', 'اسم المستفيد': 'مستفيد خيالي', 'الجهة المخاطبة': 'جهة خيالية'})
    return values
