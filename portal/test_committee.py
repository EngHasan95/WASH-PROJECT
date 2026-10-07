import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import Client, TestCase, override_settings

from portal.models import User, Complaint, Violation, ReportTemplate


class CommitteeTests(TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name)

    def seed(self, enabled=True, name='wash_committee_test'):
        # Override only the command's guard metadata, never the actual test DB connection.
        fake = SimpleNamespace(WASH_COMMITTEE_DEMO=enabled, WASH_COMMITTEE_STATE=self.state,
                               DATABASES={'default': {'NAME': name}})
        with patch('portal.management.commands.seed_committee.settings', fake):
            call_command('seed_committee', stdout=io.StringIO())
        return json.loads((self.state / 'committee.json').read_text())

    def enabled(self):
        return override_settings(WASH_COMMITTEE_DEMO=True, WASH_COMMITTEE_STATE=self.state,
                                 ALLOWED_HOSTS=['localhost', 'testserver'])

    def test_shortcuts_disabled_in_normal_configuration(self):
        self.assertEqual(self.client.get('/committee/').status_code, 404)
        self.assertEqual(self.client.post('/committee/enter/', {'role': 'director'}).status_code, 404)
        with self.assertRaises(CommandError):
            self.seed(enabled=False)
        with self.assertRaises(CommandError):
            self.seed(name='wash_development')

    def test_seed_is_fictional_and_restart_preserves_work(self):
        data = self.seed()
        self.assertEqual(User.objects.count(), 7)
        self.assertTrue(all(not u.has_usable_password() for u in User.objects.all()))
        self.assertEqual(Complaint.objects.count(), 2)
        self.assertEqual(ReportTemplate.objects.filter(active_version__isnull=False).count(), 3)
        violation = Violation.objects.get(pk=data['violation'])
        violation.description = 'نتائج تجربة محفوظة'
        violation.save()
        self.assertEqual(self.seed(), data)
        violation.refresh_from_db()
        self.assertEqual(violation.description, 'نتائج تجربة محفوظة')

    def test_seed_refuses_existing_unmarked_database(self):
        User.objects.create_user('existing-institution-user')
        with self.assertRaises(CommandError):
            self.seed()
        self.assertEqual(User.objects.count(), 1)
        self.assertFalse((self.state / 'committee.json').exists())

    def test_local_role_entry_and_guide_are_not_cached(self):
        data = self.seed()
        with self.enabled():
            response = self.client.get('/committee/', HTTP_HOST='localhost')
            self.assertEqual(response.status_code, 200)
            self.assertIn('no-store', response['Cache-Control'])
            for role, user_id in data['users'].items():
                response = self.client.post('/committee/enter/', {'role': role}, HTTP_HOST='localhost')
                self.assertEqual(response.status_code, 302)
                self.assertEqual(int(self.client.session['_auth_user_id']), user_id)
            self.assertEqual(self.client.get('/committee/', HTTP_HOST='testserver').status_code, 404)

    def test_csrf_and_real_account_guard(self):
        data = self.seed()
        with self.enabled():
            csrf = Client(enforce_csrf_checks=True)
            self.assertEqual(csrf.post('/committee/enter/', {'role': 'director'}, HTTP_HOST='localhost').status_code, 403)
            csrf.get('/committee/', HTTP_HOST='localhost')
            token = csrf.cookies['csrftoken'].value
            self.assertEqual(csrf.post('/committee/enter/', {'role': 'director', 'csrfmiddlewaretoken': token}, HTTP_HOST='localhost').status_code, 302)
            user = User.objects.get(pk=data['users']['director'])
            for change in ('password', 'inactive', 'role'):
                user.set_unusable_password()
                user.is_active = True
                user.role = 'director'
                if change == 'password': user.set_password('a-real-credentialed-account')
                if change == 'inactive': user.is_active = False
                if change == 'role': user.role = 'finance'
                user.save()
                self.assertEqual(self.client.post('/committee/enter/', {'role': 'director'}, HTTP_HOST='localhost').status_code, 404)
            self.assertEqual(self.client.post('/committee/enter/', {'role': 'unknown'}, HTTP_HOST='localhost').status_code, 404)
