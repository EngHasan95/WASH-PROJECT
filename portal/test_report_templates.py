import io
import uuid
import zipfile
from copy import deepcopy

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import User, Complaint, Violation, ReportTemplate, ReportTemplateVersion, SavedReport
from .template_forms import TemplateForm


def template_data(source='violation', **changes):
    return dict(client_id=str(uuid.uuid4()), expected_revision=0, name='نموذج اختبار جديد', source=source,
        header='المؤسسة العامة للمياه\nفرع مأرب', title='تقرير {{رقم الملف}}', body='الاسم: {{اسم المخالف}}\nسبب الإجراء: {{سبب الإجراء}}',
        custom_fields='سبب الإجراء', table_fields='المنطقة\nالاستهلاك التقديري\nسبب الإجراء',
        footer='أعد التقرير: {{معد التقرير}}\nالتوقيع: ........', orientation='portrait', font_size='regular', margin='normal',
        accent='water', logo='center', alignment='right', **changes)


def word_file(text, name='new-template.docx'):
    from xml.sax.saxutils import escape
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('word/document.xml', '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>' + escape(text) + '</w:t></w:r></w:p></w:body></w:document>')
    return SimpleUploadedFile(name, stream.getvalue(), content_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document')


class ReportTemplateTests(TestCase):
    def setUp(self):
        self.director = User.objects.create_user('template-director', role='director', first_name='مدير خيالي')
        self.secretary = User.objects.create_user('template-secretary', role='secretariat')
        self.tech = User.objects.create_user('template-tech', role='technician')
        self.other = User.objects.create_user('template-other-tech', role='technician')
        self.beneficiary = User.objects.create_user('template-beneficiary', role='citizen')
        self.item = Violation.objects.create(reporter=self.tech, client_id=uuid.uuid4(), payload_digest='a'*64,
            person_name='شخص خيالي', address='عنوان اختبار', area='منطقة خيالية', estimated_cubic_meters='10.5',
            estimate_days=30, activity='government', kind='random_connection', status='secretariat')
        self.client.force_login(self.secretary)
        self.data = template_data()

    def create(self, data=None):
        response = self.client.post(reverse('report_template_new'), data or self.data)
        self.assertEqual(response.status_code, 302)
        return ReportTemplate.objects.get(client_id=(data or self.data)['client_id']).versions.first()

    def approve(self, version):
        self.client.force_login(self.director)
        return self.client.post(reverse('report_template_approve', args=[version.pk]), {'expected_revision': version.number})

    def generate_data(self):
        return dict(client_id=str(uuid.uuid4()), official_number='TEST-001', document_date=timezone.localdate().isoformat(),
            recipient='جهة خيالية', extra_0='إجراء ميداني تجريبي')

    def generate(self, version, data=None, record=None):
        self.client.force_login(self.tech)
        return self.client.post(reverse('report_template_generate', args=[version.pk, (record or self.item).pk]), data or self.generate_data())

    def test_unknown_new_model_can_be_created_approved_and_used(self):
        version = self.create()
        self.assertEqual(self.client.get(reverse('report_template_preview', args=[version.pk])).status_code, 200)
        self.assertEqual(self.generate(version).status_code, 404)
        self.assertEqual(self.approve(version).status_code, 302)
        self.assertEqual(self.generate(version).status_code, 302)
        saved = SavedReport.objects.get()
        self.assertIn('شخص خيالي', saved.content['body'])
        self.assertIn('إجراء ميداني تجريبي', saved.content['body'])
        self.assertIn('10.50 متر مكعب', str(saved.content['rows']))
        self.assertEqual(saved.content['layout']['logo'], 'center')
        self.assertEqual(saved.template_version, version)
        response = self.client.get(reverse('saved_report_print', args=[saved.pk]))
        self.assertContains(response, saved.reference)
        self.assertEqual(response['Cache-Control'], 'no-store, private')

    def test_revision_does_not_change_approved_model_or_saved_report(self):
        version = self.create()
        self.approve(version)
        data = self.generate_data()
        self.generate(version, data)
        saved = SavedReport.objects.get()
        content = deepcopy(saved.content)
        self.client.force_login(self.secretary)
        revision_data = {**self.data, 'client_id': str(uuid.uuid4()), 'expected_revision': 1, 'body': 'نص الإصدار الجديد {{اسم المخالف}}'}
        edit = reverse('report_template_edit', args=[version.pk])
        self.assertEqual(self.client.post(edit, revision_data).status_code, 302)
        self.assertEqual(self.client.post(edit, revision_data).status_code, 302)
        self.assertEqual(version.template.versions.count(), 2)
        newest = version.template.versions.first()
        version.refresh_from_db()
        self.assertEqual(version.spec['body'], self.data['body'])
        version.template.refresh_from_db()
        self.assertEqual(version.template.active_version_id, version.pk)
        self.assertEqual(self.approve(newest).status_code, 302)
        self.item.person_name = 'اسم تغيّر لاحقًا'
        self.item.save()
        saved.refresh_from_db()
        self.assertEqual(saved.content, content)
        # Lost acknowledgment still resolves to the original snapshot after activation changes.
        self.assertEqual(self.generate(version, data).status_code, 302)
        self.assertEqual(SavedReport.objects.count(), 1)
        self.assertEqual(self.generate(version).status_code, 409)
        self.assertEqual(self.generate(newest).status_code, 302)
        self.assertIn('نص الإصدار الجديد اسم تغيّر لاحقًا', SavedReport.objects.first().content['body'])

    def test_stale_edits_and_stale_approval_cannot_replace_newer_version(self):
        version = self.create()
        data = {**self.data, 'client_id': str(uuid.uuid4()), 'expected_revision': 1, 'name': 'نسخة ثانية'}
        route = reverse('report_template_edit', args=[version.pk])
        self.assertEqual(self.client.post(route, data).status_code, 302)
        self.assertEqual(self.client.post(route, {**data, 'client_id': str(uuid.uuid4()), 'name': 'نسخة من شاشة قديمة'}).status_code, 409)
        self.assertEqual(self.approve(version).status_code, 409)
        self.assertIsNone(ReportTemplate.objects.get().active_version)

    def test_create_retry_and_generation_conflict_do_not_duplicate(self):
        version = self.create()
        self.create()
        self.assertEqual(ReportTemplate.objects.count(), 1)
        self.assertEqual(self.client.post(reverse('report_template_new'), {**self.data, 'name': 'تعارض'}).status_code, 409)
        self.approve(version)
        data = self.generate_data()
        self.generate(version, data)
        self.assertEqual(self.generate(version, data).status_code, 302)
        self.assertEqual(self.generate(version, {**data, 'extra_0': 'محتوى متعارض'}).status_code, 409)
        self.assertEqual(SavedReport.objects.count(), 1)

    def test_only_director_approves_and_retires_editor_access_is_scoped(self):
        version = self.create()
        for role in User.Role.values:
            if role == 'director':
                continue
            user = User.objects.create_user('template-role-' + role, role=role)
            self.client.force_login(user)
            self.assertEqual(self.client.post(reverse('report_template_approve', args=[version.pk]), {'expected_revision': 1}).status_code, 403)
            self.assertEqual(self.client.post(reverse('report_template_retire', args=[version.template_id]), {'expected_revision': 1}).status_code, 403)
            if role != 'secretariat':
                self.assertEqual(self.client.get(reverse('report_template_edit', args=[version.pk])).status_code, 403)
                self.assertEqual(self.client.get(reverse('report_template_new')).status_code, 403)
        self.assertEqual(self.client.get(reverse('report_template_library')).status_code, 200)
        self.client.force_login(self.beneficiary)
        self.assertEqual(self.client.get(reverse('report_template_library')).status_code, 403)
        self.assertEqual(self.client.get(reverse('report_template_detail', args=[version.template_id])).status_code, 403)

    def test_drafts_are_hidden_from_other_staff_and_saved_reports_follow_record_scope(self):
        version = self.create()
        self.client.force_login(self.tech)
        self.assertNotContains(self.client.get(reverse('report_template_library')), version.name)
        self.assertEqual(self.client.get(reverse('report_template_preview', args=[version.pk])).status_code, 403)
        self.approve(version)
        self.generate(version)
        saved = SavedReport.objects.get()
        for user, status in ((self.other, 404), (self.beneficiary, 403)):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse('saved_report_print', args=[saved.pk])).status_code, status)
            self.assertEqual(self.client.post(reverse('report_template_generate', args=[version.pk, self.item.pk]), self.generate_data()).status_code, status)

    def test_retirement_stops_new_reports_and_keeps_historical_prints(self):
        version = self.create()
        self.approve(version)
        self.generate(version)
        saved = SavedReport.objects.get()
        self.client.force_login(self.director)
        self.assertEqual(self.client.post(reverse('report_template_retire', args=[version.template_id]), {'expected_revision': 1, 'expected_active_version': version.pk}).status_code, 302)
        self.assertEqual(self.approve(version).status_code, 409)
        self.assertEqual(self.generate(version).status_code, 409)
        self.assertEqual(self.client.get(reverse('saved_report_print', args=[saved.pk])).status_code, 200)
        self.assertNotContains(self.client.get(reverse('report_template_library')), version.name)

    def test_stale_retirement_does_not_disable_a_newly_approved_version(self):
        version = self.create()
        self.approve(version)
        self.client.force_login(self.secretary)
        self.client.post(reverse('report_template_edit', args=[version.pk]),
            {**self.data, 'client_id': str(uuid.uuid4()), 'expected_revision': 1, 'name': 'نسخة جديدة'})
        newest = version.template.versions.first()
        self.approve(newest)
        response = self.client.post(reverse('report_template_retire', args=[version.template_id]),
            {'expected_revision': 2, 'expected_active_version': version.pk})
        self.assertEqual(response.status_code, 409)
        version.template.refresh_from_db()
        self.assertTrue(version.template.enabled)

    def test_unknown_tokens_invalid_layout_duplicate_fields_and_executable_syntax_rejected(self):
        for changes in ({'body': '{{حقل غير معروف}}'}, {'body': '{{ user.password }}'}, {'body': '{{اسم المخالف'},
            {'custom_fields': 'سبب الإجراء\nسبب الإجراء'}, {'custom_fields': 'اسم المخالف'}, {'orientation': 'malicious'},
            {'table_fields': 'رقم سند غير معروف'}):
            form = TemplateForm({**self.data, **changes})
            self.assertFalse(form.is_valid(), changes)
        self.assertFalse(ReportTemplate.objects.exists())

    def test_plain_text_is_escaped_without_interpreting_code_or_nested_placeholders(self):
        version = self.create({**self.data, 'body': '<script>alert(1)</script> {{اسم المخالف}} {{سبب الإجراء}}'})
        self.approve(version)
        self.generate(version, {**self.generate_data(), 'extra_0': '<img src=x onerror=alert(2)> {{رقم سند السداد}}'})
        saved = SavedReport.objects.get()
        response = self.client.get(reverse('saved_report_print', args=[saved.pk]))
        self.assertContains(response, '&lt;script&gt;alert(1)&lt;/script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertContains(response, '{{رقم سند السداد}}')

    def test_word_import_and_preview_do_not_publish_a_template(self):
        imported = {**self.data, 'body': '', 'word_file': word_file('نص Word جديد {{اسم المخالف}}')}
        response = self.client.post(reverse('report_template_new'), {**imported, 'action': 'preview'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'نص Word جديد شخص خيالي')
        self.assertFalse(ReportTemplate.objects.exists())
        imported['word_file'] = word_file('نص Word جديد {{اسم المخالف}}')
        version = self.create(imported)
        self.assertEqual(version.spec['body'], 'نص Word جديد {{اسم المخالف}}')
        self.assertIsNone(version.approved_at)

    def test_bad_word_files_and_xml_entities_rejected(self):
        for upload in (SimpleUploadedFile('fake.docx', b'not a zip'), word_file('hello', 'template.pdf'),
                       SimpleUploadedFile('huge.docx', b'x'*(2*1024*1024+1))):
            form = TemplateForm(self.data, {'word_file': upload})
            self.assertFalse(form.is_valid())
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('word/document.xml', '<!DOCTYPE doc [<!ENTITY private SYSTEM "file:///etc/passwd">]><doc>&private;</doc>')
        form = TemplateForm(self.data, {'word_file': SimpleUploadedFile('entities.docx', buffer.getvalue())})
        self.assertFalse(form.is_valid())

    def test_financial_values_use_assessment_not_technician_estimate(self):
        from .models import ViolationSettlement
        finance = User.objects.create_user('template-finance', role='finance')
        ViolationSettlement.objects.create(violation=self.item, policy_code='test', fixed_fee='325000', unit_rate='400',
            approved_units='3', unit_description='متر مكعب', assessment_basis='مرجع خيالي', assessed_by=finance)
        data = {**self.data, 'body': '{{الكمية المعتمدة}} / {{إجمالي الرسوم}} / {{رقم سند السداد}}'}
        version = self.create(data)
        self.approve(version)
        self.generate(version)
        self.assertEqual(SavedReport.objects.get().content['body'], '3.00 متر مكعب / 326200.00 ريال يمني / لم يؤكد بعد')

    def test_complaint_and_summary_templates_use_their_own_data_and_permissions(self):
        item = Complaint.objects.create(owner=self.beneficiary, client_id=uuid.uuid4(), payload_digest='a'*64,
            reporter_name='مستفيد اختبار', phone='000000000', complaint_type='leak', neighborhood='other',
            other_neighborhood='حي خيالي', address='عنوان خيالي', landmark='معلم اختبار', description='تسريب تجريبي', assignee=self.tech)
        complaint_data = {**self.data, 'client_id': str(uuid.uuid4()), 'source': 'complaint', 'title': 'تقرير {{رقم البلاغ}}',
            'body': '{{اسم المستفيد}}: {{وصف البلاغ}}', 'custom_fields': '', 'table_fields': 'الحي\nالمختص'}
        version = self.create(complaint_data)
        self.approve(version)
        self.assertEqual(self.generate(version, record=item).status_code, 302)
        self.assertEqual(SavedReport.objects.first().content['body'], 'مستفيد اختبار: تسريب تجريبي')
        self.client.force_login(self.director)
        summary_data = {**self.data, 'client_id': str(uuid.uuid4()), 'source': 'summary', 'title': 'تقرير الإدارة',
            'body': 'عدد البلاغات: {{إجمالي البلاغات}}', 'custom_fields': '', 'table_fields': 'البلاغات المفتوحة\nإجمالي المخالفات'}
        version = self.create(summary_data)
        self.approve(version)
        route = reverse('report_template_generate', args=[version.pk, 0])
        self.assertEqual(self.client.post(route, self.generate_data()).status_code, 302)
        self.assertEqual(SavedReport.objects.first().content['body'], 'عدد البلاغات: 1')
        self.client.force_login(self.tech)
        self.assertEqual(self.client.get(route).status_code, 404)

    def test_custom_fields_required_and_post_requires_csrf(self):
        version = self.create()
        self.approve(version)
        self.assertEqual(self.generate(version, {**self.generate_data(), 'extra_0': ''}).status_code, 200)
        self.assertFalse(SavedReport.objects.exists())
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.director)
        self.assertEqual(strict.post(reverse('report_template_approve', args=[version.pk]), {'expected_revision': 1}).status_code, 403)
        self.assertEqual(strict.post(reverse('report_template_new'), self.data).status_code, 403)
