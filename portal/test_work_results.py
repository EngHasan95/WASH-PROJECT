import io
import json
import tempfile
import uuid

from PIL import Image
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from pathlib import Path
from unittest.mock import patch
from django.urls import reverse

from .models import Complaint, ComplaintEvent, ComplaintExecutionResult, ComplaintExecutionPhoto, User
from .work_results import assignment_epoch


class ExecutionResultsTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.settings = override_settings(MEDIA_ROOT=self.media.name)
        self.settings.enable()
        self.addCleanup(self.settings.disable)
        self.addCleanup(self.media.cleanup)
        self.owner = User.objects.create_user('result-owner', role=User.Role.CITIZEN)
        self.tech = User.objects.create_user('result-tech', role=User.Role.TECHNICIAN)
        self.other = User.objects.create_user('result-other', role=User.Role.TECHNICIAN)
        self.director = User.objects.create_user('result-director', role=User.Role.DIRECTOR)
        self.finance = User.objects.create_user('result-finance', role=User.Role.FINANCE)
        self.complaint = Complaint.objects.create(owner=self.owner, client_id=uuid.uuid4(), payload_digest='a'*64,
            reporter_name='مستفيد خيالي', phone='000000000', complaint_type='leak', neighborhood='n11',
            address='عنوان خيالي', landmark='معلم', description='بلاغ خيالي', assignee=self.tech, status=Complaint.Status.IN_PROGRESS)
        ComplaintEvent.objects.create(complaint=self.complaint, actor=self.director, action='assign')
        ComplaintEvent.objects.create(complaint=self.complaint, actor=self.tech, action='start')
        self.payload = dict(owner_id=self.tech.pk, client_id=str(uuid.uuid4()), complaint_id=self.complaint.pk,
                            assignment_event_id=assignment_epoch(self.complaint), note='أصلحنا التسريب الخيالي')

    def image(self):
        buffer = io.BytesIO()
        Image.new('RGB', (16, 16), 'blue').save(buffer, 'PNG')
        return SimpleUploadedFile('fake.png', buffer.getvalue(), content_type='image/png')

    def post(self, user=None, photos=None, payload=None):
        self.client.force_login(user or self.tech)
        fields = {'payload': json.dumps(payload or self.payload)}
        if photos is not None:
            fields['photos'] = photos
        return self.client.post(reverse('work_result_sync'), fields)

    def test_result_normalizes_photos_records_event_and_replay_after_closure(self):
        response = self.post(photos=[self.image()])
        self.assertEqual(response.status_code, 201, response.content)
        self.complaint.refresh_from_db()
        self.assertEqual(self.complaint.status, Complaint.Status.REVIEW)
        result = ComplaintExecutionResult.objects.get()
        self.assertEqual((result.event.action, result.event.note), ('submit', self.payload['note']))
        photo = ComplaintExecutionPhoto.objects.get()
        with photo.image.open('rb') as stream:
            image = Image.open(stream)
            self.assertEqual(image.format, 'JPEG')
        self.complaint.status = Complaint.Status.CLOSED
        self.complaint.save(update_fields=['status'])
        replay = self.post(photos=[self.image()])
        self.assertEqual(replay.status_code, 200, replay.content)
        self.assertEqual(replay.json()['result_id'], result.pk)
        self.assertEqual(ComplaintExecutionResult.objects.count(), 1)
        self.assertEqual(ComplaintEvent.objects.filter(action='submit').count(), 1)
        self.assertEqual(ComplaintExecutionPhoto.objects.count(), 1)

    def test_key_conflict_does_not_change_previous_result(self):
        self.assertEqual(self.post().status_code, 201)
        payload = {**self.payload, 'note': 'محتوى مختلف'}
        self.assertEqual(self.post(payload=payload).status_code, 409)
        self.assertEqual(ComplaintExecutionResult.objects.count(), 1)
        self.assertEqual(ComplaintEvent.objects.filter(action='submit').get().note, self.payload['note'])

    def test_changed_assignment_rejected_even_if_reassigned_back(self):
        ComplaintEvent.objects.create(complaint=self.complaint, actor=self.director, action='reassign')
        self.assertEqual(self.post().status_code, 409)
        self.assertFalse(ComplaintExecutionResult.objects.exists())
        self.complaint.refresh_from_db()
        self.assertEqual(self.complaint.status, Complaint.Status.IN_PROGRESS)

    def test_state_changes_and_other_department_are_not_bypassed(self):
        self.complaint.status = Complaint.Status.ASSIGNED
        self.complaint.save(update_fields=['status'])
        self.assertEqual(self.post().status_code, 409)
        self.complaint.status = Complaint.Status.IN_PROGRESS
        self.complaint.save(update_fields=['status'])
        self.assertEqual(self.post(self.finance, payload={**self.payload, 'owner_id': self.finance.pk}).status_code, 409)
        self.assertEqual(self.post(self.other, payload={**self.payload, 'owner_id': self.other.pk}).status_code, 409)
        self.assertFalse(ComplaintExecutionResult.objects.exists())

    def test_finance_result_and_second_round_after_return(self):
        self.complaint.department = Complaint.Department.FINANCE
        self.complaint.assignee = self.finance
        self.complaint.save(update_fields=['department', 'assignee'])
        payload = {**self.payload, 'owner_id': self.finance.pk}
        self.assertEqual(self.post(self.finance, payload=payload).status_code, 201)
        self.client.force_login(self.director)
        self.assertEqual(self.client.post(reverse('work_action', args=[self.complaint.pk]), {'action': 'return', 'note': 'راجع الحساب'}).status_code, 302)
        self.client.force_login(self.finance)
        self.assertEqual(self.client.post(reverse('work_action', args=[self.complaint.pk]), {'action': 'start'}).status_code, 302)
        payload.update(client_id=str(uuid.uuid4()), assignment_event_id=assignment_epoch(self.complaint), note='نتيجة الجولة الثانية')
        self.assertEqual(self.post(self.finance, payload=payload).status_code, 201)
        self.assertEqual(ComplaintExecutionResult.objects.count(), 2)

    def test_account_payload_and_invalid_images_do_not_create_result(self):
        self.assertEqual(self.post(payload={**self.payload, 'owner_id': self.other.pk}).status_code, 409)
        self.assertEqual(self.post(self.owner).status_code, 403)
        self.assertEqual(self.post(payload={**self.payload, 'note': ' '}).status_code, 400)
        invalid = SimpleUploadedFile('fake.png', b'not an image', content_type='image/png')
        self.assertEqual(self.post(photos=[invalid]).status_code, 400)
        self.assertEqual(self.post(photos=[self.image() for _ in range(4)]).status_code, 400)
        self.assertFalse(ComplaintExecutionResult.objects.exists())

    def test_private_photo_visibility_and_missing_login(self):
        self.post(photos=[self.image()])
        photo = ComplaintExecutionPhoto.objects.get()
        url = reverse('work_result_photo', args=[photo.pk])
        for user in (self.tech, self.director):
            self.client.force_login(user)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Cache-Control'], 'no-store, private')
            self.assertTrue(b"".join(response.streaming_content))
        for user in (self.other, self.owner, self.finance):
            self.client.force_login(user)
            self.assertEqual(self.client.get(url).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(url).status_code, 401)

    def test_task_preparation_does_not_include_beneficiary_identity(self):
        self.client.force_login(self.tech)
        response = self.client.get(reverse('work_detail', args=[self.complaint.pk]))
        card = response.context['work_task']
        self.assertEqual(card['owner_id'], self.tech.pk)
        self.assertNotIn('phone', card)
        self.assertNotIn('reporter_name', card)
        self.assertContains(response, 'work-task-card')
        self.assertContains(response, 'work-result-photos')
        shell = self.client.get('/app-shell/')
        self.assertNotContains(shell, self.payload['note'])
        self.assertNotContains(shell, 'work-task-card')

    def test_failed_photo_database_write_rolls_back_event_and_removes_file(self):
        with patch.object(ComplaintExecutionPhoto, 'save', side_effect=RuntimeError('simulated write failure')):
            with self.assertRaises(RuntimeError):
                self.post(photos=[self.image()])
        self.complaint.refresh_from_db()
        self.assertEqual(self.complaint.status, Complaint.Status.IN_PROGRESS)
        self.assertFalse(ComplaintExecutionResult.objects.exists())
        self.assertFalse(ComplaintEvent.objects.filter(action='submit').exists())
        self.assertFalse(any(path.is_file() for path in Path(self.media.name).rglob('*')))

    def test_stale_authenticated_user_cannot_send_after_account_disabled(self):
        from .work_results import sync_result
        request = RequestFactory().post(reverse('work_result_sync'), {'payload': json.dumps(self.payload)})
        request.user = self.tech  # Authentication was resolved before the account changed.
        User.objects.filter(pk=self.tech.pk).update(is_active=False)
        response = sync_result(request)
        self.assertEqual(response.status_code, 409)
        self.assertFalse(ComplaintExecutionResult.objects.exists())

    def test_finance_responsible_keeps_reassignment_alongside_result_form(self):
        self.finance.is_department_responsible = True
        self.finance.save(update_fields=['is_department_responsible'])
        self.complaint.department = Complaint.Department.FINANCE
        self.complaint.assignee = self.finance
        self.complaint.save(update_fields=['department', 'assignee'])
        self.client.force_login(self.finance)
        response = self.client.get(reverse('work_detail', args=[self.complaint.pk]))
        self.assertContains(response, 'data-work-result-form')
        self.assertContains(response, 'value="reassign"')
        self.assertContains(response, 'المختص المالي المسؤول')

    def test_out_of_range_identifiers_and_close_action_do_not_reach_database(self):
        self.assertEqual(self.post(payload={**self.payload, 'complaint_id': 2**80}).status_code, 400)
        self.assertEqual(self.post(payload={**self.payload, 'assignment_event_id': 2**80}).status_code, 400)
        self.assertEqual(self.post(payload={**self.payload, 'action': 'close'}).status_code, 400)
        self.complaint.refresh_from_db()
        self.assertEqual(self.complaint.status, Complaint.Status.IN_PROGRESS)
        self.assertFalse(ComplaintExecutionResult.objects.exists())
