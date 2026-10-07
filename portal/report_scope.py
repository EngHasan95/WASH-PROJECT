"""Validated, shared register filters for live and immutable administrative reports."""
from urllib.parse import urlencode

from django import forms

from .catalog import COMPLAINT_TYPES, NEIGHBORHOODS
from .models import Complaint, User, Violation


def choices(values):
    return [('', 'الكل'), *values]


class ReportScopeForm(forms.Form):
    date_from = forms.DateField(label='الاستلام من تاريخ', required=False,
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    date_to = forms.DateField(label='الاستلام حتى تاريخ', required=False,
        widget=forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d'))
    neighborhood = forms.ChoiceField(label='حي البلاغ', required=False, choices=choices(NEIGHBORHOODS))
    complaint_type = forms.ChoiceField(label='نوع البلاغ', required=False, choices=choices(COMPLAINT_TYPES))
    complaint_status = forms.ChoiceField(label='حالة البلاغ الحالية', required=False, choices=choices(Complaint.Status.choices))
    technician = forms.ModelChoiceField(label='الفني (المسند إليه للبلاغ / المبلغ للمخالفة)',
        required=False, queryset=User.objects.none(), empty_label='كل الفنيين')
    violation_area = forms.CharField(label='منطقة المخالفة', required=False, max_length=150,
        help_text='اسم المنطقة كما سُجل في الملف؛ لا يطابق حي البلاغ تلقائيًا.')
    violation_kind = forms.ChoiceField(label='نوع المخالفة', required=False, choices=choices(Violation.Kind.choices))
    violation_activity = forms.ChoiceField(label='نشاط المخالفة', required=False, choices=choices(Violation.Activity.choices))
    violation_status = forms.ChoiceField(label='حالة المخالفة الحالية', required=False, choices=choices(Violation.Status.choices))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Include inactive technicians so past work can still be reported.
        self.fields['technician'].queryset = User.objects.filter(role=User.Role.TECHNICIAN).order_by('first_name', 'username')
        self.fields['technician'].label_from_instance = lambda user: user.display_name

    def clean(self):
        data = super().clean()
        if data.get('date_from') and data.get('date_to') and data['date_from'] > data['date_to']:
            self.add_error('date_to', 'تاريخ نهاية الفترة يجب ألا يسبق بدايتها.')
        return data

    def registers(self):
        if not self.is_valid():
            raise ValueError('Report scope must be validated before reading records.')
        complaints, violations = Complaint.objects.all(), Violation.objects.all()
        for field, lookup in (('date_from', 'received_at__date__gte'), ('date_to', 'received_at__date__lte')):
            if self.cleaned_data[field]:
                complaints = complaints.filter(**{lookup: self.cleaned_data[field]})
                violations = violations.filter(**{lookup: self.cleaned_data[field]})
        for field, lookup in (('neighborhood', 'neighborhood'), ('complaint_type', 'complaint_type'),
                              ('complaint_status', 'status'), ('technician', 'assignee')):
            if self.cleaned_data[field]:
                complaints = complaints.filter(**{lookup: self.cleaned_data[field]})
        for field, lookup in (('violation_area', 'area'), ('violation_kind', 'kind'),
                              ('violation_activity', 'activity'), ('violation_status', 'status'), ('technician', 'reporter')):
            if self.cleaned_data[field]:
                violations = violations.filter(**{lookup: self.cleaned_data[field]})
        return complaints, violations

    def snapshot(self):
        if not self.is_valid():
            raise ValueError('Invalid report scope')
        filters, labels = {}, []
        for name, field in self.fields.items():
            value = self.cleaned_data[name]
            if value in ('', None):
                continue
            if name == 'technician':
                raw, label = str(value.pk), value.display_name
            elif isinstance(field, forms.DateField):
                raw = label = value.isoformat()
            else:
                raw = str(value)
                label = dict(field.choices).get(raw, raw) if isinstance(field, forms.ChoiceField) else raw
            filters[name] = raw
            labels.append((field.label, label))
        return {'filters': filters, 'labels': labels or [('النطاق', 'جميع السجلات')],
            'basis': 'الفترة حسب تاريخ الاستلام بتوقيت المؤسسة؛ الحالة والإسناد كما هما وقت إصدار التقرير. فلاتر البلاغ لا تقيد المخالفات، والعكس، إلا الفترة والفني.'}

    def query(self):
        return urlencode(self.snapshot()['filters'])
