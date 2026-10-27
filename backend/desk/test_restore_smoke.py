import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from .models import User
from .services import create_ticket

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts'))
import restore_drill


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'], ATTACHMENT_MIN_FREE_BYTES=0)
class RestoreSmokeTests(TestCase):
    def test_smoke_logs_in_and_downloads_a_restored_attachment(self):
        with tempfile.TemporaryDirectory() as media, override_settings(MEDIA_ROOT=media):
            owner = User.objects.create_user(username='owner@drill.test', email='owner@drill.test',
                                             name='顾问', role='consultant', must_change_password=False)
            create_ticket(owner, {'request_key': uuid.uuid4(), 'title': '合成工单', 'system': 'DS4',
                'module': 'MM', 'kind': '配置', 'description': '合成数据'},
                [SimpleUploadedFile('evidence.txt', b'synthetic-restore-data')])
            expected = {'tickets': 1, 'attachments': 1, 'attachment_bytes': len(b'synthetic-restore-data')}
            env = {'DRILL_EXPECTED_COUNTS': json.dumps(expected), 'DRILL_SMOKE_PASSWORD': 'Synthetic-drill-password-2026!'}
            with patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()) as output:
                exec(restore_drill.SMOKE, {})
            line = next(line for line in output.getvalue().splitlines() if line.startswith('DRILL_RESULT='))
            result = json.loads(line.partition('=')[2])
            self.assertTrue(result['login'])
            self.assertTrue(result['attachment_download'])
            self.assertTrue(result['expected_counts_match'])
