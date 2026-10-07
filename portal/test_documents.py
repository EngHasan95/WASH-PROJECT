import uuid
from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.templatetags.static import static
from django.utils import timezone

from .models import User, Violation, ViolationDocument


class DocumentTests(TestCase):
    def setUp(self):
        self.tech = User.objects.create_user('doc-tech', role='technician', first_name='فني خيالي')
        self.secretary = User.objects.create_user('doc-secretary', role='secretariat', first_name='سكرتير خيالي')
        self.item = Violation.objects.create(reporter=self.tech, client_id=uuid.uuid4(), payload_digest='a'*64,
            person_name='اسم تجريبي', address='عنوان خيالي', area='منطقة اختبار', estimated_cubic_meters='12.50',
            estimate_days=30, activity='government', kind='random_connection', status='secretariat')
        self.data = dict(client_id=str(uuid.uuid4()), revision='1', kind='letter', title='مذكرة اختبار',
            official_number='TEST-1', document_date=timezone.localdate().isoformat(), recipient='جهة خيالية',
            body='نرجو اتخاذ الإجراء وفق السياسة المعتمدة.\nهذه بيانات اختبارية.')
        self.client.force_login(self.secretary)

    def create(self, **changes):
        response = self.client.post(reverse('violation_document_new', args=[self.item.pk]), {**self.data, **changes})
        self.assertEqual(response.status_code, 302)
        return ViolationDocument.objects.get()

    def issue(self, doc):
        return self.client.post(reverse('violation_document_issue', args=[doc.pk]), {'revision': doc.revision})

    def test_create_retry_edit_and_issue_snapshot(self):
        doc = self.create()
        self.create()
        self.assertEqual(self.item.documents.count(), 1)
        self.assertEqual(self.item.events.count(), 1)
        self.assertEqual(self.client.post(reverse('violation_document_new', args=[self.item.pk]), {**self.data, 'body': 'تعارض'}).status_code, 409)
        edit = reverse('violation_document_edit', args=[doc.pk])
        self.assertEqual(self.client.post(edit, {**self.data, 'body': 'نص مراجع'}).status_code, 302)
        doc.refresh_from_db()
        self.assertEqual(doc.revision, 2)
        self.assertEqual(self.client.post(edit, {**self.data, 'body': 'تحرير من شاشة قديمة'}).status_code, 409)
        self.assertEqual(self.issue(doc).status_code, 302)
        self.assertEqual(self.issue(doc).status_code, 302)
        case_response = self.client.get(reverse('violation_detail', args=[self.item.pk]))
        self.assertEqual(case_response.context['form']['document_reference'].value(), 'TEST-1')
        self.assertEqual(case_response.context['form']['recipient'].value(), 'جهة خيالية')
        doc.refresh_from_db()
        self.assertEqual(doc.status, 'issued')
        self.assertEqual(doc.snapshot['activity'], 'حكومي')
        self.assertEqual(self.item.events.filter(action='document_issued').count(), 1)
        self.item.person_name = 'اسم تغيّر لاحقًا'
        self.item.save()
        self.secretary.first_name = 'اسم موظف جديد'
        self.secretary.save()
        response = self.client.get(reverse('violation_document_print', args=[doc.pk]))
        self.assertContains(response, 'اسم تجريبي')
        self.assertNotContains(response, 'اسم تغيّر لاحقًا')
        self.assertContains(response, 'سكرتير خيالي')
        self.assertNotContains(response, 'اسم موظف جديد')
        self.assertEqual(response['Cache-Control'], 'no-store, private')
        self.assertEqual(self.client.post(edit, {**self.data, 'revision': 2, 'body': 'تعديل صادر'}).status_code, 403)

    def test_issue_requires_official_number_recipient_and_nonfuture_date(self):
        doc = self.create(official_number='', recipient='')
        self.assertEqual(self.issue(doc).status_code, 400)
        doc.official_number = 'TEST-2'
        doc.recipient = 'جهة خيالية'
        doc.document_date = timezone.localdate() + timedelta(days=1)
        doc.save()
        self.assertEqual(self.issue(doc).status_code, 400)
        doc.document_date = timezone.localdate()
        doc.save()
        self.assertEqual(self.issue(doc).status_code, 302)

    def test_minutes_do_not_require_a_recipient_and_drafts_print_as_drafts(self):
        doc = self.create(kind='minutes', recipient='')
        response = self.client.get(reverse('violation_document_print', args=[doc.pk]))
        self.assertContains(response, 'مسودة للمراجعة')
        self.assertContains(response, static('brand/institution-logo.png'))
        self.assertEqual(self.issue(doc).status_code, 302)

    def test_document_roles_and_scope(self):
        doc = self.create()
        for role in User.Role.values:
            if role == 'secretariat':
                continue
            user = User.objects.create_user('doc-'+role, role=role)
            self.client.force_login(user)
            self.assertEqual(self.client.post(reverse('violation_document_new', args=[self.item.pk]), self.data).status_code, 403)
            self.assertEqual(self.issue(doc).status_code, 403)
        # Another technician cannot read the original technician's documents.
        other = User.objects.get(username='doc-technician')
        self.client.force_login(other)
        for route in ('violation_document', 'violation_document_print'):
            self.assertEqual(self.client.get(reverse(route, args=[doc.pk])).status_code, 404)
        self.client.force_login(User.objects.get(username='doc-citizen'))
        self.assertEqual(self.client.get(reverse('violation_document_print', args=[doc.pk])).status_code, 403)
        self.client.force_login(self.tech)
        self.assertEqual(self.client.get(reverse('violation_document', args=[doc.pk])).status_code, 200)

    def test_secretariat_cannot_edit_after_handoff_and_followup_can_print(self):
        doc = self.create()
        self.issue(doc)
        self.item.status = 'followup'
        self.item.save()
        self.assertEqual(self.client.get(reverse('violation_document_edit', args=[doc.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse('violation_document_new', args=[self.item.pk])).status_code, 403)
        followup = User.objects.create_user('doc-followup-worker', role='followup')
        self.client.force_login(followup)
        self.assertEqual(self.client.get(reverse('violation_document_print', args=[doc.pk])).status_code, 200)

    def test_escape_content_stale_issue_and_csrf(self):
        doc = self.create(body='<script>alert(1)</script>')
        response = self.client.get(reverse('violation_document_print', args=[doc.pk]))
        self.assertContains(response, '&lt;script&gt;')
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertEqual(self.client.post(reverse('violation_document_issue', args=[doc.pk]), {'revision': '999'}).status_code, 409)
        strict = Client(enforce_csrf_checks=True)
        strict.force_login(self.secretary)
        self.assertEqual(strict.post(reverse('violation_document_issue', args=[doc.pk]), {'revision': '1'}).status_code, 403)
