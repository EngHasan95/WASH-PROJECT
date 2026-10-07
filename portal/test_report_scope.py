import hashlib
import io
import json
import struct
import uuid
import zipfile
from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Complaint, ReportTemplate, ReportTemplateVersion, SavedReport, User, Violation
from .report_scope import ReportScopeForm
from .template_forms import import_docx
from .test_report_templates import template_data, word_file


class AdministrativeScopeTests(TestCase):
    def setUp(self):
        self.director = User.objects.create_user('scope-director', role='director')
        self.owner = User.objects.create_user('scope-owner', role='citizen')
        self.tech = User.objects.create_user('scope-tech', role='technician', first_name='فني خيالي')
        self.other = User.objects.create_user('scope-other', role='technician')
        self.inactive = User.objects.create_user('scope-inactive', role='technician', is_active=False)
        self.complaints = []
        for tech, kind, status, district, hour in ((self.tech, 'leak', 'closed', 'n11', 23),
                (self.other, 'leak', 'received', 'n11', 12), (self.tech, 'high_bill', 'closed', 'other', 12)):
            item = Complaint.objects.create(owner=self.owner, client_id=uuid.uuid4(), payload_digest='a'*64,
                reporter_name='خيالي', phone='000000000', complaint_type=kind, neighborhood=district,
                address='عنوان خيالي', landmark='معلم خيالي', description='اختبار', status=status, assignee=tech)
            Complaint.objects.filter(pk=item.pk).update(received_at=datetime(2026, 1, 20, hour, 59, tzinfo=ZoneInfo('Asia/Aden')))
            self.complaints.append(item)
        self.violation = Violation.objects.create(reporter=self.tech, client_id=uuid.uuid4(), payload_digest='b'*64,
            person_name='خيالي', address='عنوان', area='منطقة اختبار', estimated_cubic_meters=1, estimate_days=1,
            activity='government', kind='random_connection', status='closed')
        Violation.objects.filter(pk=self.violation.pk).update(received_at=datetime(2026, 1, 20, 0, 1, tzinfo=ZoneInfo('Asia/Aden')))
        self.filters = {'date_from': '2026-01-20', 'date_to': '2026-01-20', 'neighborhood': 'n11',
            'complaint_type': 'leak', 'complaint_status': 'closed', 'technician': str(self.tech.pk),
            'violation_area': 'منطقة اختبار', 'violation_kind': 'random_connection',
            'violation_activity': 'government', 'violation_status': 'closed'}
        spec = template_data()
        spec.update(title='تقرير خيالي', body='{{إجمالي البلاغات}} / {{إجمالي المخالفات}}',
            custom_fields=[], table_fields=['البلاغات المغلقة'])
        for key in ('name', 'source', 'client_id', 'expected_revision'):
            spec.pop(key)
        self.root = ReportTemplate.objects.create(created_by=self.director, source='summary')
        self.version = ReportTemplateVersion.objects.create(template=self.root, number=1, name='ملخص اختبار',
            spec=spec, created_by=self.director, approved_by=self.director, approved_at=timezone.now())
        self.root.active_version = self.version
        self.root.save()
        self.client.force_login(self.director)

    def test_filters_apply_to_both_registers_and_institution_day_is_inclusive(self):
        response = self.client.get(reverse('director_report'), self.filters)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['total'], 1)
        self.assertEqual(response.context['violation_total'], 1)
        self.assertEqual(response.context['technicians'].get().assigned, 1)
        self.assertContains(response, 'date_from=2026-01-20')
        # Complaint-only filters must not silently exclude otherwise matching violations.
        response = self.client.get(reverse('director_report'), {**self.filters, 'complaint_type': 'high_bill'})
        self.assertEqual(response.context['total'], 0)
        self.assertEqual(response.context['violation_total'], 1)
        response = self.client.get(reverse('director_report'), {'date_to': '2026-01-19'})
        self.assertEqual(response.context['total'], 0)
        self.assertEqual(response.context['violation_total'], 0)

    def test_invalid_scope_never_falls_back_to_all_records(self):
        for scope in ({'date_from': 'bad'}, {'date_from': '2026-02-01', 'date_to': '2026-01-01'},
                      {'technician': str(self.owner.pk)}, {'complaint_status': 'bogus'}):
            response = self.client.get(reverse('director_report'), scope)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.context['total'], 0)
            self.assertEqual(self.client.get(reverse('report_template_choose', args=['summary', 0]), scope).status_code, 400)
            self.assertEqual(self.client.get(reverse('report_template_generate', args=[self.version.pk, 0]), scope).status_code, 400)
        self.assertFalse(SavedReport.objects.exists())

    def test_inactive_technician_remains_selectable_for_historical_reports(self):
        Complaint.objects.filter(pk=self.complaints[0].pk).update(assignee=self.inactive)
        scope = ReportScopeForm({'technician': str(self.inactive.pk)})
        self.assertTrue(scope.is_valid())
        self.assertEqual(scope.registers()[0].count(), 1)

    def test_summary_carries_scope_and_immutable_results_into_saved_print(self):
        route = reverse('report_template_generate', args=[self.version.pk, 0])
        data = {'client_id': str(uuid.uuid4()), 'document_date': '2026-01-20', **self.filters}
        self.assertEqual(self.client.post(route, data).status_code, 302)
        saved = SavedReport.objects.get()
        self.assertEqual(saved.content['body'], '1 / 1')
        self.assertEqual(saved.content['scope']['filters'], self.filters)
        content = deepcopy(saved.content)
        Complaint.objects.filter(pk=self.complaints[0].pk).update(status='received')
        self.tech.first_name = 'اسم تغير لاحقًا'
        self.tech.save()
        self.assertEqual(self.client.post(route, data).status_code, 302)
        self.assertEqual(SavedReport.objects.count(), 1)
        saved.refresh_from_db()
        self.assertEqual(saved.content, content)
        self.assertEqual(self.client.post(route, {**data, 'date_to': '2026-01-21'}).status_code, 409)
        response = self.client.get(reverse('saved_report_print', args=[saved.pk]))
        self.assertContains(response, 'نطاق التقرير وقت الإصدار')
        self.assertContains(response, 'فني خيالي')
        self.assertNotContains(response, 'اسم تغير لاحقًا')
        self.assertEqual(response['Cache-Control'], 'no-store, private')
        self.client.force_login(self.tech)
        self.assertEqual(self.client.get(route).status_code, 404)
        self.assertEqual(self.client.get(reverse('saved_report_print', args=[saved.pk])).status_code, 404)
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(reverse('director_report')).status_code, 403)

    def test_generation_rejects_invalid_post_scope_without_saving(self):
        route = reverse('report_template_generate', args=[self.version.pk, 0])
        response = self.client.post(route, {'client_id': str(uuid.uuid4()), 'document_date': '2026-01-20',
            'date_to': 'invalid'})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(SavedReport.objects.exists())

    def test_saved_report_pagination_preserves_scope_for_new_generation(self):
        for _ in range(21):
            SavedReport.objects.create(template_version=self.version, created_by=self.director,
                client_id=uuid.uuid4(), payload_digest='f'*64, content={'title': 'محفوظ خيالي'})
        response = self.client.get(reverse('report_template_choose', args=['summary', 0]),
            {'technician': str(self.tech.pk), 'date_from': '2026-01-20'})
        self.assertContains(response, f'technician={self.tech.pk}&amp;page=2')
        self.assertEqual(response.context['page'].paginator.count, 21)

    def test_old_unscoped_snapshot_and_retry_remain_unchanged(self):
        data = {'client_id': uuid.uuid4(), 'document_date': timezone.localdate(), 'official_number': '', 'recipient': ''}
        payload = {**data, 'version_id': self.version.pk, 'source': 'summary', 'record_id': 0}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()
        content = {'title': 'قديم', 'body': 'بيانات تاريخية', 'rows': [], 'layout': self.version.spec,
            'header': [], 'footer': []}
        old = SavedReport.objects.create(template_version=self.version, created_by=self.director,
            client_id=data['client_id'], payload_digest=digest, content=content)
        response = self.client.post(reverse('report_template_generate', args=[self.version.pk, 0]), data)
        self.assertEqual(response.status_code, 302)
        old.refresh_from_db()
        self.assertEqual(old.content, content)
        self.assertEqual(SavedReport.objects.count(), 1)


class CorruptDocxTests(TestCase):
    def setUp(self):
        self.editor = User.objects.create_user('docx-secretary', role='secretariat')
        self.client.force_login(self.editor)

    def upload(self, payload):
        return SimpleUploadedFile('damaged.docx', payload)

    def zip_payload(self, document, compression=zipfile.ZIP_DEFLATED):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', compression=compression) as archive:
            archive.writestr('word/document.xml', document)
        return buffer.getvalue()

    def test_actual_broken_deflate_and_crc_are_form_errors_with_preserved_inputs(self):
        valid = self.zip_payload(b'<document>fictional</document>')
        # Overwrite deflate header with BTYPE=3 (reserved), keeping ZIP directory valid.
        name_len, extra_len = struct.unpack_from('<HH', valid, 26)
        offset = 30 + name_len + extra_len
        broken_deflate = bytearray(valid)
        broken_deflate[offset] = (broken_deflate[offset] & ~7) | 7
        bad_crc = bytearray(valid)
        directory = bad_crc.index(b'PK\x01\x02')
        bad_crc[directory + 16] ^= 0xFF
        for payload in (bytes(broken_deflate), bytes(bad_crc), b'not a zip'):
            data = {**template_data(), 'body': 'نص محفوظ في المحرر', 'word_file': self.upload(payload)}
            response = self.client.post(reverse('report_template_new'), data)
            self.assertEqual(response.status_code, 400)
            self.assertContains(response, 'تعذر قراءة نص النموذج', status_code=400)
            self.assertContains(response, 'نص محفوظ في المحرر', status_code=400)
        self.assertFalse(ReportTemplate.objects.exists())

    def test_pressure_unsupported_compression_and_oversized_xml_rejected(self):
        for payload in (self.zip_payload(b'x' * (2 * 1024 * 1024 + 1)),
                        self.zip_payload(b'x' * (1024 * 1024)),
                        self.zip_payload(b'<document/>', zipfile.ZIP_BZIP2)):
            with self.assertRaisesMessage(ValidationError, 'تعذر قراءة نص النموذج'):
                import_docx(self.upload(payload))
        self.assertEqual(import_docx(word_file('نص خيالي {{اسم المخالف}}')), 'نص خيالي {{اسم المخالف}}')

    def test_utf16_entity_declarations_and_duplicate_document_rejected(self):
        declaration = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE doc [<!ENTITY test "expanded">]><doc>&test;</doc>'
        with self.assertRaises(ValidationError):
            import_docx(self.upload(self.zip_payload(declaration.encode('utf-16'))))
        import warnings
        stream = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(stream, 'w') as archive:
                archive.writestr('word/document.xml', '<document/>')
                archive.writestr('word/document.xml', '<document/>')
        with self.assertRaises(ValidationError):
            import_docx(self.upload(stream.getvalue()))
