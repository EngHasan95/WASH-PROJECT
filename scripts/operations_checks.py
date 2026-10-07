"""Exercise production serving and snapshot restoration using clearly fictional fixtures."""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django
django.setup()
from django.conf import settings
from django.utils import timezone
from portal.models import User, Violation, ViolationDocument, ReportTemplate, ReportTemplateVersion, SavedReport
from portal.template_engine import render_spec, record_values
from verify_backup import verify


def main():
    tag = uuid.uuid4().hex
    tech = User.objects.create_user('restore-tech-' + tag, role='technician')
    secretary = User.objects.create_user('restore-secretary-' + tag, role='secretariat')
    director = User.objects.create_user('restore-director-' + tag, role='director')
    item = None
    template = None
    media = Path(settings.MEDIA_ROOT) / 'restore-fixtures' / (tag + '.txt')
    server = None
    try:
        item = Violation.objects.create(reporter=tech, client_id=uuid.uuid4(), payload_digest='a'*64,
            person_name='شخص خيالي لاختبار الاستعادة', address='عنوان اختبار', area='منطقة اختبار',
            estimated_cubic_meters='5', estimate_days=30, activity='residential', kind='random_connection', status='secretariat')
        doc = ViolationDocument.objects.create(violation=item, client_id=uuid.uuid4(), kind='minutes',
            title='محضر تجريبي للاستعادة', official_number='RESTORE-TEST', document_date=timezone.localdate(),
            body='نص محضر خيالي لاختبار سلامة النسخة الاحتياطية', created_by=secretary)
        template = ReportTemplate.objects.create(source='violation', created_by=secretary)
        spec = {'header': 'نموذج تجريبي للاستعادة', 'title': 'تقرير {{رقم الملف}}', 'body': 'واقعة {{اسم المخالف}}',
            'footer': 'توقيع المسؤول: ....', 'custom_fields': [], 'table_fields': ['رقم الملف'], 'orientation': 'portrait',
            'font_size': 'regular', 'margin': 'normal', 'accent': 'water', 'logo': 'center', 'alignment': 'right'}
        version = ReportTemplateVersion.objects.create(template=template, number=1, name='نموذج خيالي محفوظ', spec=spec,
            created_by=secretary, approved_by=director, approved_at=timezone.now())
        template.active_version = version
        template.save(update_fields=['active_version'])
        saved = SavedReport.objects.create(template_version=version, violation=item, client_id=uuid.uuid4(),
            payload_digest='c'*64, content=render_spec(spec, record_values('violation', item)), created_by=tech)
        media.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        media.write_bytes(b'WASH fictional private attachment for restoration test')
        media.chmod(0o600)
        with tempfile.TemporaryDirectory(prefix='wash-backup-check-', dir=ROOT / '.local') as temporary:
            destination = Path(temporary)
            subprocess.run([sys.executable, str(ROOT / 'scripts/backup.py'), temporary], check=True, stdout=subprocess.DEVNULL)
            backup = next(destination.glob('wash-*'))
            result = verify(backup, {'client_id': doc.client_id, 'body': doc.body,
                'template_version_client_id': version.client_id, 'template_spec': spec, 'report_client_id': saved.client_id,
                'report_content': json.loads(json.dumps(saved.content)),
                'media_path': str(media.relative_to(settings.MEDIA_ROOT)), 'media_bytes': media.read_bytes()})
            # Checksums must reject damage before any restoration is attempted.
            with (backup / 'media.tar.gz').open('ab') as file:
                file.write(b'tampered')
            try:
                verify(backup)
            except ValueError:
                result['corrupt_archive_rejected'] = True
            else:
                raise AssertionError('Corrupt archive accepted')
        environment = os.environ.copy()
        database = settings.DATABASES['default']
        if not environment.get('DATABASE_URL'):
            environment['DATABASE_URL'] = 'postgresql://' + quote(database['USER'], safe='') + '@' + quote(database['HOST'], safe='') + ':' + str(database['PORT']) + '/' + database['NAME']
            environment['PGSSLMODE'] = 'disable'
        environment.update(DJANGO_DEBUG='0', DJANGO_SECRET_KEY=secrets.token_urlsafe(64),
            DJANGO_ALLOWED_HOSTS='institution.example', WASH_TRUST_PROXY='1')
        subprocess.run([sys.executable, 'manage.py', 'check', '--deploy', '--fail-level', 'WARNING'],
            cwd=ROOT, env=environment, check=True)
        server = subprocess.Popen(['bash', 'scripts/start_production.sh'], cwd=ROOT, env=environment,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        def get(path, **headers):
            return urlopen(Request('http://127.0.0.1:8080' + path, headers={'Host': 'institution.example',
                'X-Forwarded-Proto': 'https', **headers}), timeout=3)
        for _ in range(100):
            if server.poll() is not None:
                raise RuntimeError('Production server exited before readiness')
            try:
                if get('/health/').status == 200:
                    break
            except Exception:
                time.sleep(.1)
        else:
            raise RuntimeError('Production server did not become ready')
        home = get('/')
        assert home.status == 200 and home.headers['Strict-Transport-Security']
        assert get('/static/css/documents.css').status == 200
        try:
            get('/', Host='unknown.example')
        except HTTPError as error:
            assert error.code == 400
        else:
            raise AssertionError('Untrusted host accepted')
        # The public URL must redirect to HTTPS when the proxy has not marked HTTPS.
        import http.client
        client = http.client.HTTPConnection('127.0.0.1', 8080, timeout=3)
        client.request('GET', '/', headers={'Host': 'institution.example'})
        response = client.getresponse()
        assert response.status == 301 and response.getheader('Location') == 'https://institution.example/'
        client.close()
        print(json.dumps({'backup': result, 'production': 'Gunicorn health, static assets, HSTS, host rejection and HTTPS redirect passed'}))
    finally:
        if server:
            server.terminate()
            server.wait(timeout=15)
        media.unlink(missing_ok=True)
        if item:
            item.delete()
        if template:
            template.delete()
        User.objects.filter(pk__in=[tech.pk, secretary.pk, director.pk]).delete()


if __name__ == '__main__':
    main()
