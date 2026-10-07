"""One-time, non-destructive fictional fixture provisioning for the isolated demo."""
import json
from pathlib import Path
import uuid

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from portal.committee import ROLES
from portal.models import User, AccountEvent, Complaint, ComplaintEvent, Violation, ViolationEvent, ReportTemplate, ReportTemplateVersion, ReportTemplateEvent
from portal.template_engine import COMMON, DEFAULT_HEADER, FIELDS


class Command(BaseCommand):
    help = 'Prepare isolated fictional committee data once; never reset existing work.'

    def handle(self, *args, **options):
        if not getattr(settings, 'WASH_COMMITTEE_DEMO', False) or not settings.DATABASES['default']['NAME'].startswith('wash_committee'):
            raise CommandError('This command is allowed only in an isolated committee demo database.')
        marker = settings.WASH_COMMITTEE_STATE / 'committee.json'
        if marker.exists():
            data = json.loads(marker.read_text())
            if not data.get('financial_assignment_ready'):
                finance_id = data.get('users', {}).get('finance')
                User.objects.filter(pk=finance_id, role=User.Role.FINANCE, is_active=True).update(is_department_responsible=True)
                data['financial_assignment_ready'] = True
                temporary = marker.with_suffix('.tmp')
                temporary.write_text(json.dumps(data))
                temporary.chmod(0o600)
                temporary.replace(marker)
            self.stdout.write('Committee data already prepared; existing test work preserved.')
            return
        if User.objects.exists() or Complaint.objects.exists() or Violation.objects.exists() or ReportTemplate.objects.exists():
            raise CommandError('Database contains data without a committee marker. No records were changed.')
        with transaction.atomic():
            users = {}
            for code, label, _ in ROLES:
                user = User.objects.create_user('committee_' + code, first_name=label + ' — تجريبي', role=code)
                user.is_department_responsible = code == User.Role.FINANCE
                user.set_unusable_password()
                user.save(update_fields=['password', 'is_department_responsible'])
                users[code] = user
            director = users['director']
            for user in users.values():
                if user.is_employee and user != director:
                    AccountEvent.objects.create(actor=director, target=user)
            complaints = []
            for kind, description, department in [('leak', 'بلاغ تجريبي: تسريب بجوار معلم خيالي', 'technical'),
                ('high_bill', 'بلاغ تجريبي: طلب مراجعة فاتورة وهمية', 'finance')]:
                item = Complaint.objects.create(owner=users['citizen'], client_id=uuid.uuid4(), payload_digest='a'*64,
                    reporter_name='مستفيد خيالي للجنة', phone='000000000', complaint_type=kind, neighborhood='other',
                    other_neighborhood='حي تجريبي', address='عنوان خيالي للاختبار', landmark='معلم تجريبي', description=description, department=department)
                ComplaintEvent.objects.create(complaint=item, actor=None, action='routed', note='توجيه بلاغ تجريبي إلى القسم المختص')
                complaints.append(item.pk)
            violation = Violation.objects.create(reporter=users['technician'], client_id=uuid.uuid4(), payload_digest='b'*64,
                person_name='مخالف خيالي للجنة', address='عنوان ميداني تجريبي', area='منطقة خيالية',
                estimated_cubic_meters='12.50', estimate_days=30, activity='government', kind='random_connection', description='واقعة وهمية لتجربة كامل مسار المعالجة')
            ViolationEvent.objects.create(violation=violation, actor=None, action='received', note='وصلت المخالفة التجريبية لمسؤول النظام')
            for source, title in [('violation', 'تقرير ملف المخالفة'), ('complaint', 'تقرير متابعة البلاغ'), ('summary', 'ملخص تجربة الإدارة')]:
                template = ReportTemplate.objects.create(source=source, created_by=director)
                spec = {'header': DEFAULT_HEADER, 'title': title, 'body': 'تقرير تجريبي معد من بيانات النظام لاختبار اللجنة.',
                    'footer': 'أعد التقرير: {{معد التقرير}}\nالتوقيع: ................\nالختم: ................',
                    'custom_fields': [], 'table_fields': list(FIELDS[source]), 'orientation': 'portrait', 'font_size': 'regular',
                    'margin': 'normal', 'accent': 'water', 'logo': 'center', 'alignment': 'right'}
                version = ReportTemplateVersion.objects.create(template=template, number=1, name=title + ' — نموذج تجريبي', spec=spec,
                    created_by=director, approved_by=director, approved_at=timezone.now())
                template.active_version = version
                template.save(update_fields=['active_version'])
                for action in ('created', 'approved'):
                    ReportTemplateEvent.objects.create(template=template, version=version, actor=director, action=action)
            data = {'format': 1, 'users': {role: user.pk for role, user in users.items()}, 'complaints': complaints, 'violation': violation.pk,
                    'financial_assignment_ready': True}
        temporary = marker.with_suffix('.tmp')
        temporary.write_text(json.dumps(data))
        temporary.chmod(0o600)
        temporary.replace(marker)
        self.stdout.write(self.style.SUCCESS('Fictional committee data ready. No real data or fixed passwords included.'))
