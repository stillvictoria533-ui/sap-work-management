import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import Mock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from rest_framework.test import APIClient

from .errors import AttachmentCapacityExceeded, AttachmentStorageLow
from .models import Attachment, Event, Ticket, User
from .services import create_ticket, reply_ticket


class CapacityFixture:
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        override = override_settings(MEDIA_ROOT=self.media.name, ATTACHMENT_TICKET_LIMIT_BYTES=8,
            ATTACHMENT_OWNER_LIMIT_BYTES=12, ATTACHMENT_GLOBAL_LIMIT_BYTES=16,
            ATTACHMENT_MIN_FREE_BYTES=0, PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
        override.enable(); self.addCleanup(override.disable)
        self.owner = User.objects.create_user(username='capacity-owner', email='owner@capacity.test', name='顾问', must_change_password=False)
        self.other = User.objects.create_user(username='capacity-other', email='other@capacity.test', name='顾问2', must_change_password=False)
        self.admin = User.objects.create_user(username='capacity-admin', email='admin@capacity.test', name='管理员', role='admin', must_change_password=False)

    def data(self, key=None):
        return {'request_key': key or uuid.uuid4(), 'title': '容量测试', 'system': 'DS4',
                'module': 'MM', 'kind': '配置', 'description': '测试数据'}

    def file(self, data=b'1234'):
        return SimpleUploadedFile('note.txt', data, content_type='text/plain')


class AttachmentCapacityTests(CapacityFixture, TestCase):
    def test_ticket_limit_rejects_before_any_ticket_or_file_is_created(self):
        with self.assertRaises(AttachmentCapacityExceeded):
            create_ticket(self.owner, self.data(), [self.file(b'123456789')])
        self.assertEqual(Ticket.objects.count(), 0)
        self.assertEqual(Attachment.objects.count(), 0)
        self.assertFalse(any(path.is_file() for path in Path(self.media.name).rglob('*')))

    def test_reply_checks_ticket_and_owner_totals_without_changing_version(self):
        ticket, _ = create_ticket(self.owner, self.data(), [self.file(b'12345678')])
        with self.assertRaises(AttachmentCapacityExceeded):
            reply_ticket(self.admin, ticket.pk, {'version': 1, 'body': '补充'}, [self.file(b'x')])
        ticket.refresh_from_db()
        self.assertEqual(ticket.version, 1)
        self.assertEqual(Event.objects.filter(ticket=ticket).count(), 1)
        self.assertEqual(Attachment.objects.count(), 1)
        # The owner's quota includes administrator uploads on that owner's tickets.
        create_ticket(self.owner, self.data(), [self.file(b'1234')])
        with self.assertRaises(AttachmentCapacityExceeded):
            create_ticket(self.owner, self.data(), [self.file(b'x')])

    def test_global_quota_and_idempotent_replay(self):
        key = uuid.uuid4()
        ticket, created = create_ticket(self.owner, self.data(key), [self.file(b'12345678')])
        self.assertTrue(created)
        create_ticket(self.other, self.data(), [self.file(b'12345678')])
        with self.assertRaises(AttachmentCapacityExceeded):
            create_ticket(self.other, self.data(), [self.file(b'x')])
        repeated, created = create_ticket(self.owner, self.data(key), [self.file(b'12345678')])
        self.assertFalse(created)
        self.assertEqual(repeated.pk, ticket.pk)
        self.assertEqual(Attachment.objects.count(), 2)

    def test_disk_reserve_rejects_upload_but_allows_text_only_reply(self):
        ticket, _ = create_ticket(self.owner, self.data(), [])
        with override_settings(ATTACHMENT_MIN_FREE_BYTES=100), \
             patch('desk.uploads.shutil.disk_usage', return_value=Mock(free=103)):
            with self.assertRaises(AttachmentStorageLow):
                reply_ticket(self.owner, ticket.pk, {'version': 1, 'body': '上传'}, [self.file()])
            self.assertEqual(reply_ticket(self.owner, ticket.pk, {'version': 1, 'body': '仅文字'}, []).version, 2)

    def test_api_gives_a_clear_capacity_error(self):
        client = APIClient(); client.force_login(self.owner)
        session = client.session; session['version'] = self.owner.session_version; session.save()
        result = client.post('/api/tickets', {**self.data(), 'files': [self.file(b'123456789')]}, format='multipart')
        self.assertEqual(result.status_code, 413)
        self.assertIn('工单', str(result.data['detail']))


class AttachmentCapacityConcurrencyTests(CapacityFixture, TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_simultaneous_uploads_cannot_exceed_global_quota(self):
        barrier = Barrier(2)
        def upload(user_id):
            try:
                user = User.objects.get(pk=user_id)
                barrier.wait(timeout=10)
                try:
                    create_ticket(user, self.data(), [self.file(b'1234')])
                    return 'created'
                except AttachmentCapacityExceeded:
                    return 'limited'
            finally:
                connections.close_all()
        with override_settings(ATTACHMENT_GLOBAL_LIMIT_BYTES=5), ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(upload, [self.owner.pk, self.other.pk]))
        self.assertCountEqual(outcomes, ['created', 'limited'])
        self.assertEqual(Attachment.objects.count(), 1)
        self.assertEqual(Ticket.objects.count(), 1)
