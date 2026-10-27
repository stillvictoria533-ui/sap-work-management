import base64
import hashlib
import hmac
import tempfile
import uuid
from datetime import datetime, timezone as datetime_timezone
from pathlib import Path
from unittest.mock import Mock, patch
from django.test import TestCase, override_settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone
from rest_framework.test import APIClient
from .models import User, Ticket, Event, Attachment, Notification, Delivery, Audit
from .services import create_ticket, reply_ticket
from .dingtalk import DingTalkClient, DeliveryError, deliver_one, signed_parameters

PASSWORD = 'Secure-Testing-Password-2026!'

@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ApplicationTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.admin = self.make_user('admin', 'admin')
        self.alice = self.make_user('alice')
        self.bob = self.make_user('bob')
        self.client = self.as_user(self.alice)

    def make_user(self, name, role='consultant', must_change=False):
        return User.objects.create_user(username=f'{name}@example.com', email=f'{name}@example.com', name=name, role=role, password=PASSWORD, must_change_password=must_change)

    def as_user(self, user):
        client = APIClient()
        client.force_login(user)
        session = client.session
        session['version'] = user.session_version
        session.save()
        return client

    def data(self, **extra):
        return {'request_key': str(uuid.uuid4()), 'title': '采购审批调整', 'system': 'DS4', 'module': 'MM', 'kind': '配置', 'priority': 'high', 'description': '业务背景、期望结果与验收标准。', **extra}

    def ticket(self, **extra):
        response = self.client.post('/api/tickets', self.data(**extra), format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return Ticket.objects.get(pk=response.data['id'])

    def test_login_requires_csrf_and_creates_session(self):
        client = APIClient(enforce_csrf_checks=True)
        self.assertEqual(client.post('/api/auth/login', {'email': self.alice.email, 'password': PASSWORD}, format='json').status_code, 403)
        token = client.get('/api/auth/session').json()['csrf_token']
        response = client.post('/api/auth/login', {'email': self.alice.email, 'password': PASSWORD}, format='json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get('/api/tickets').status_code, 200)

    def test_login_throttles_bad_credentials(self):
        client = APIClient()
        for _ in range(10):
            self.assertEqual(client.post('/api/auth/login', {'email': self.alice.email, 'password': 'wrong'}, format='json').status_code, 401)
        self.assertEqual(client.post('/api/auth/login', {'email': self.alice.email, 'password': PASSWORD}, format='json').status_code, 429)

    def test_creation_is_idempotent_and_notifies_admin(self):
        data = self.data(owner_id=self.bob.pk, status='closed')
        first = self.client.post('/api/tickets', data, format='json')
        second = self.client.post('/api/tickets', data, format='json')
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(Ticket.objects.count(), 1)
        ticket = Ticket.objects.get()
        self.assertEqual(ticket.owner, self.alice)
        self.assertEqual(ticket.status, 'pending')
        self.assertEqual(Notification.objects.get().recipient, self.admin)
        self.assertEqual(Delivery.objects.get().channel, 'group_admin')

    def test_submission_requires_known_sap_system_and_preserves_historical_blank(self):
        for missing in [{}, {'system': ''}, {'system': 'OTHER'}]:
            data = self.data()
            data.update(missing)
            if 'system' not in missing:
                data.pop('system')
            response = self.client.post('/api/tickets', data, format='json')
            self.assertEqual(response.status_code, 400)
        self.assertEqual(Ticket.objects.count(), 0)
        for system in ['DS4', 'QS4', 'PS4']:
            response = self.client.post('/api/tickets', self.data(system=system), format='json')
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.data['system'], system)
            self.assertEqual(self.client.get(f"/api/tickets/{response.data['id']}").data['system'], system)
        historical = Ticket.objects.create(owner=self.alice, title='历史工单', module='MM', kind='问题', description='旧数据')
        self.assertEqual(self.client.get(f'/api/tickets/{historical.pk}').data['system'], '')

    def test_other_consultant_cannot_read_reply_download_or_export(self):
        data = self.data()
        data['files'] = [SimpleUploadedFile('说明.txt', '说明'.encode(), content_type='text/plain')]
        result = self.client.post('/api/tickets', data, format='multipart')
        pk = result.data['id']
        attachment = Attachment.objects.get()
        client = self.as_user(self.bob)
        self.assertEqual(client.get('/api/tickets').data['count'], 0)
        self.assertEqual(client.get(f'/api/tickets/{pk}').status_code, 404)
        self.assertEqual(client.post(f'/api/tickets/{pk}/reply', {'body': '越权', 'version': 1}, format='json').status_code, 404)
        self.assertEqual(client.get(f'/api/attachments/{attachment.pk}/download').status_code, 404)
        self.assertEqual(client.get('/api/tickets/export').status_code, 403)
        self.assertEqual(client.get('/api/accounts').status_code, 403)

    def action(self, client, ticket, action, version, reason=''):
        return client.post(f'/api/tickets/{ticket.pk}/status', {'action': action, 'version': version, 'reason': reason}, format='json')

    def test_four_state_workflow_reply_separation_and_archive(self):
        ticket = self.ticket()
        admin = self.as_user(self.admin)
        self.assertEqual(self.action(admin, ticket, 'start', 1).status_code, 200)
        reply = admin.post(f'/api/tickets/{ticket.pk}/reply', {'body': '普通反馈', 'version': 2}, format='json')
        self.assertEqual(reply.data['status'], 'in-progress')
        self.assertEqual(Event.objects.filter(ticket=ticket, kind='status').count(), 1)
        submitted = self.action(admin, ticket, 'submit', 3, '已处理，请验证')
        self.assertEqual(submitted.data['next_actor'], 'consultant')
        reply = self.client.post(f'/api/tickets/{ticket.pk}/reply', {'body': '补充问题', 'version': 4}, format='json')
        self.assertEqual(reply.data['status'], 'waiting')
        self.assertEqual(reply.data['next_actor'], 'consultant')
        self.assertEqual(self.action(self.client, ticket, 'reject', 5, '结果仍有偏差').data['status'], 'in-progress')
        self.assertEqual(self.action(admin, ticket, 'submit', 6, '已修复偏差').status_code, 200)
        confirmed = self.action(self.client, ticket, 'confirm', 7)
        self.assertEqual(confirmed.data['status'], 'done')
        self.assertEqual(confirmed.data['next_actor'], 'none')
        self.assertEqual(admin.get('/api/tickets', {'scope': 'archive'}).data['count'], 1)
        self.assertEqual(admin.get('/api/tickets', {'scope': 'attention'}).data['count'], 0)
        self.assertEqual(admin.get('/api/stats').data['active'], 0)
        self.assertEqual(self.client.post(f'/api/tickets/{ticket.pk}/reply', {'body': '补充', 'version': 8}, format='json').status_code, 400)
        reopened = self.action(self.client, ticket, 'reopen', 8, '发现新的验收问题')
        self.assertEqual(reopened.data['status'], 'in-progress')
        self.assertEqual(reopened.data['next_actor'], 'admin')
        self.assertEqual(Delivery.objects.count(), Event.objects.count())
        self.assertTrue(Event.objects.filter(kind='status', body__contains='新的验收问题', actor=self.alice).exists())

    def test_status_permissions_reasons_legacy_requests_and_conflicts(self):
        ticket = self.ticket()
        admin = self.as_user(self.admin)
        self.assertEqual(self.action(self.as_user(self.bob), ticket, 'reopen', 1, '越权').status_code, 404)
        self.assertEqual(self.action(self.client, ticket, 'start', 1).status_code, 403)
        self.assertEqual(self.action(self.client, ticket, 'confirm', 1).status_code, 400)
        self.assertEqual(self.action(admin, ticket, 'submit', 1, '跳步').status_code, 400)
        for reason in ['', '   ']:
            self.assertEqual(self.action(admin, ticket, 'complete', 1, reason).status_code, 400)
        for client in [admin, self.client]:
            self.assertEqual(client.post(f'/api/tickets/{ticket.pk}/reply', {'body': '旧页面', 'version': 1, 'status': 'closed'}, format='json').status_code, 400)
        self.assertEqual(Event.objects.count(), 1)
        done = self.action(admin, ticket, 'complete', 1, '已在线下验收')
        self.assertEqual(done.data['status'], 'done')
        count = Event.objects.count()
        self.assertEqual(self.action(admin, ticket, 'reopen', 1, '过期操作').status_code, 409)
        self.assertEqual(Event.objects.count(), count)
        self.assertEqual(self.action(admin, ticket, 'reopen', 2, '继续处理').status_code, 200)
        self.assertEqual(self.action(admin, ticket, 'confirm', 3).status_code, 403)
        self.assertEqual(self.action(admin, ticket, 'submit', 3, '请验收').status_code, 200)
        self.assertEqual(self.action(self.client, ticket, 'complete', 4, '绕过权限').status_code, 403)
        self.assertEqual(self.action(admin, ticket, 'complete', 4, '线下已确认').status_code, 200)
        event = Event.objects.filter(kind='status').last()
        self.assertEqual(Notification.objects.get(event=event).recipient, self.alice)
        self.assertEqual(Delivery.objects.get(event=event).channel, 'group_consultant')

    def test_closed_migration_preserves_history_and_does_not_notify(self):
        import importlib
        from django.apps import apps
        from django.db import connection
        ticket = self.ticket()
        Ticket.objects.filter(pk=ticket.pk).update(status='closed', version=4)
        original = list(Event.objects.values())
        deliveries = Delivery.objects.count()
        migration = importlib.import_module('desk.migrations.0005_four_status_workflow')
        from types import SimpleNamespace
        migration.merge_closed(apps, SimpleNamespace(connection=connection))
        ticket.refresh_from_db()
        self.assertEqual((ticket.status, ticket.version, ticket.next_actor), ('done', 5, 'none'))
        self.assertEqual(list(Event.objects.filter(pk__in=[e['id'] for e in original]).values()), original)
        item = Event.objects.last()
        self.assertEqual((item.actor_name, item.actor_role, item.from_status, item.to_status), ('系统', 'system', 'closed', 'done'))
        self.assertEqual(Delivery.objects.count(), deliveries)

    def test_filters_search_reply_and_csv_match_scope(self):
        self.ticket(title='=HYPERLINK("x")')
        t = self.ticket(module='FI', title='财务检查')
        self.client.post(f'/api/tickets/{t.pk}/reply', {'body': '独特的检索词', 'version': 1}, format='json')
        admin = self.as_user(self.admin)
        result = admin.get('/api/tickets', {'q': '独特的检索词', 'module': 'FI', 'priority': 'high', 'owner': self.alice.pk})
        self.assertEqual(result.data['count'], 1)
        export = admin.get('/api/tickets/export', {'module': 'FI'})
        self.assertEqual(export.status_code, 200)
        exported = b''.join(export.streaming_content).decode('utf-8-sig')
        self.assertIn('财务检查', exported)
        self.assertNotIn('HYPERLINK', exported)
        full = b''.join(admin.get('/api/tickets/export').streaming_content).decode('utf-8-sig')
        self.assertIn("'=HYPERLINK", full)
        self.assertTrue(Audit.objects.filter(action='export').exists())

    def test_development_calendar_requires_an_active_current_session(self):
        params = {'month': '2026-10'}
        for mode in ['submitted', 'estimated']:
            query = {**params, 'mode': mode}
            self.assertEqual(APIClient().get('/api/calendar', query).status_code, 403)
            for user in [self.admin, self.alice, self.bob]:
                with self.subTest(mode=mode, role=user.role, user=user.pk):
                    self.assertEqual(self.as_user(user).get('/api/calendar', query).status_code, 200)
                    for field, value in [('must_change_password', True), ('is_active', False)]:
                        previous = getattr(user, field)
                        setattr(user, field, value)
                        user.save()
                        self.assertEqual(self.as_user(user).get('/api/calendar', query).status_code, 403)
                        setattr(user, field, previous)
                        user.save()
                    client = self.as_user(user)
                    user.session_version += 1
                    user.save()
                    self.assertEqual(client.get('/api/calendar', query).status_code, 403)

    def test_consultant_calendar_scopes_rows_and_all_summaries_to_owner(self):
        def add(owner, created, planned=None, **extra):
            return Ticket.objects.create(owner=owner, title='日历权限测试', module='MM', kind='开发',
                                         description='不公开的工单正文',
                                         created_at=datetime.fromisoformat(created).replace(tzinfo=datetime_timezone.utc),
                                         estimated_completion_date=planned, **extra)
        mine = [add(self.alice, '2026-09-30T16:00:00', '2026-10-23'),
                add(self.alice, '2026-10-01T01:00:00', '2026-10-23', status='done')]
        add(self.alice, '2026-09-01T01:00:00')  # Own unassessed ticket outside this month.
        add(self.bob, '2026-09-30T16:00:00', '2026-10-23', priority='urgent')
        add(self.bob, '2026-10-02T01:00:00', '2026-10-24', priority='urgent')
        add(self.bob, '2026-10-02T01:00:00')
        for mode, selected, other_day, admin_count in [
            ('submitted', '2026-10-01', '2026-10-02', 5),
            ('estimated', '2026-10-23', '2026-10-24', 4),
        ]:
            with self.subTest(mode=mode):
                params = {'month': '2026-10', 'mode': mode, 'date': selected}
                # Changing query parameters cannot request another consultant's scope.
                result = self.client.get('/api/calendar', {**params, 'owner': self.bob.pk, 'scope': 'all', 'role': 'admin'})
                self.assertEqual(result.status_code, 200)
                data = result.data
                self.assertEqual((data['month_count'], data['count'], data['unassessed_count']), (2, 2, 1))
                self.assertEqual(data['days'], [{'date': selected, 'count': 2, 'urgent_count': 0}])
                self.assertEqual([row['id'] for row in data['results']], [ticket.pk for ticket in mine])
                for row in data['results']:
                    self.assertEqual(row['owner_id'], self.alice.pk)
                    self.assertEqual(row['estimated_completion_date'], '2026-10-23')
                    self.assertNotIn('description', row)
                empty = self.client.get('/api/calendar', {**params, 'date': other_day}).data
                self.assertEqual((empty['count'], empty['results']), (0, []))
                bob = self.as_user(self.bob).get('/api/calendar', params).data
                self.assertEqual(bob['count'], 1)
                self.assertEqual(bob['unassessed_count'], 1)
                self.assertEqual(bob['results'][0]['owner_id'], self.bob.pk)
                admin = self.as_user(self.admin).get('/api/calendar', params).data
                self.assertEqual((admin['month_count'], admin['count'], admin['unassessed_count']), (admin_count, 3, 2))

    def test_consultant_calendar_pagination_and_empty_scope_do_not_leak_others(self):
        created = datetime(2026, 10, 12, 3, tzinfo=datetime_timezone.utc)
        Ticket.objects.bulk_create([Ticket(owner=self.alice, title=f'我的工单 {i}', module='SD', kind='开发',
                                          description='背景', created_at=created, estimated_completion_date='2026-11-10') for i in range(21)])
        Ticket.objects.bulk_create([Ticket(owner=self.bob, title=f'其他顾问工单 {i}', module='SD', kind='开发',
                                          description='其他人的正文', created_at=created, priority='urgent',
                                          estimated_completion_date='2026-11-10') for i in range(3)])
        for mode, selected in [('submitted', '2026-10-12'), ('estimated', '2026-11-10')]:
            with self.subTest(mode=mode):
                params = {'mode': mode, 'month': selected[:7], 'date': selected}
                first = self.client.get('/api/calendar', params).data
                second = self.client.get('/api/calendar', {**params, 'page': 2}).data
                self.assertEqual((first['count'], first['month_count']), (21, 21))
                self.assertEqual((len(first['results']), len(second['results'])), (20, 1))
                self.assertEqual(first['days'], [{'date': selected, 'count': 21, 'urgent_count': 0}])
                self.assertEqual(second['month_count'], 21)
                for row in first['results'] + second['results']:
                    self.assertEqual(row['owner_id'], self.alice.pk)
                empty = self.as_user(self.make_user('empty-' + mode)).get('/api/calendar', params).data
                self.assertEqual((empty['count'], empty['month_count'], empty['unassessed_count']), (0, 0, 0))
                self.assertEqual((empty['days'], empty['results']), ([], []))

    def test_calendar_uses_submission_date_and_shanghai_month_boundaries(self):
        def add(created, **extra):
            return Ticket.objects.create(owner=self.alice, title='历史工单', module='MM', kind='开发', description='不包含在日历响应中的正文',
                                         created_at=datetime.fromisoformat(created).replace(tzinfo=datetime_timezone.utc),
                                         updated_at=datetime(2026, 11, 15, tzinfo=datetime_timezone.utc), **extra)
        outside = add('2026-09-30T15:59:59')
        first = add('2026-09-30T16:00:00', status='done')
        last = add('2026-10-31T15:59:59', priority='urgent')
        following = add('2026-10-31T16:00:00')
        client = self.as_user(self.admin)
        data = client.get('/api/calendar', {'month': '2026-10', 'date': '2026-10-31'}).data
        self.assertEqual(data['month_count'], 2)
        self.assertEqual(data['days'], [{'date': '2026-10-01', 'count': 1, 'urgent_count': 0}, {'date': '2026-10-31', 'count': 1, 'urgent_count': 1}])
        self.assertEqual([t['id'] for t in data['results']], [last.pk])
        self.assertNotIn('description', data['results'][0])
        archived = client.get('/api/calendar', {'month': '2026-10', 'date': '2026-10-01'}).data
        self.assertEqual([t['id'] for t in archived['results']], [first.pk])
        self.assertEqual(archived['results'][0]['status'], 'done')
        november = client.get('/api/calendar', {'month': '2026-11'}).data
        self.assertEqual(november['month_count'], 1)
        self.assertEqual([t['id'] for t in november['results']], [following.pk])
        self.assertNotIn(outside.pk, [t['id'] for t in archived['results']])

    def test_calendar_paginates_daily_tickets_without_truncating_month_counts(self):
        created = datetime(2025, 12, 15, 3, tzinfo=datetime_timezone.utc)
        Ticket.objects.bulk_create([Ticket(owner=self.alice, title=f'历史需求 {i}', module='SD', kind='问题', description='背景', created_at=created,
                                          priority='urgent' if i == 20 else 'medium') for i in range(21)])
        client = self.as_user(self.admin)
        params = {'month': '2025-12', 'date': '2025-12-15'}
        first = client.get('/api/calendar', params).data
        second = client.get('/api/calendar', {**params, 'page': 2}).data
        self.assertEqual(first['count'], 21)
        self.assertEqual(len(first['results']), 20)
        self.assertEqual(len(second['results']), 1)
        self.assertEqual(first['days'], [{'date': '2025-12-15', 'count': 21, 'urgent_count': 1}])
        self.assertEqual(second['month_count'], 21)
        self.assertEqual(second['results'][0]['title'], '历史需求 20')
        empty = client.get('/api/calendar', {'month': '2025-12', 'date': '2025-12-16'}).data
        self.assertEqual((empty['count'], empty['results'], empty['month_count']), (0, [], 21))

    def test_calendar_rejects_invalid_or_mismatched_dates(self):
        client = self.as_user(self.admin)
        for month in ['2026-13', '2026-00', '2026-1', '0000-01', '9999-12', '2026/10']:
            self.assertEqual(client.get('/api/calendar', {'month': month}).status_code, 400)
        for date in ['2026-09-30', '2026-10-32', '2026-10-1', 'invalid']:
            self.assertEqual(client.get('/api/calendar', {'month': '2026-10', 'date': date}).status_code, 400)
        empty = client.get('/api/calendar', {'month': '2024-02', 'date': '2024-02-29'})
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.data['month_count'], 0)

    def test_notification_read_watermark_does_not_consume_later_reply(self):
        ticket = self.ticket()
        initial = Event.objects.get(ticket=ticket)
        self.client.post(f'/api/tickets/{ticket.pk}/reply', {'body': '新的补充', 'version': 1}, format='json')
        admin = self.as_user(self.admin)
        response = admin.post(f'/api/tickets/{ticket.pk}/read', {'through_event': initial.pk}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Notification.objects.filter(recipient=self.admin, read_at__isnull=True).count(), 1)

    def test_spoofed_oversized_and_excess_files_are_rejected(self):
        for files in [[SimpleUploadedFile('fake.png', b'not an image')], [SimpleUploadedFile('huge.txt', b'a' * (20*1024*1024+1))], [SimpleUploadedFile(f'{i}.txt', b'ok') for i in range(6)]]:
            data = self.data()
            data['files'] = files
            response = self.client.post('/api/tickets', data, format='multipart')
            self.assertEqual(response.status_code, 400)
        self.assertEqual(Ticket.objects.count(), 0)

    def test_files_are_cleaned_if_database_transaction_fails(self):
        with patch('desk.services.notify_for_event', side_effect=RuntimeError('rollback')):
            with self.assertRaises(RuntimeError):
                create_ticket(self.alice, self.data(), [SimpleUploadedFile('doc.txt', b'hello')])
        self.assertEqual(Ticket.objects.count(), 0)
        self.assertEqual(Attachment.objects.count(), 0)
        self.assertFalse(any(p.is_file() for p in Path(self.media.name).rglob('*')))

    def test_download_requires_session_and_is_attachment(self):
        data = self.data(files=[SimpleUploadedFile('note.txt', b'hello')])
        response = self.client.post('/api/tickets', data, format='multipart')
        self.assertEqual(response.status_code, 201)
        url = f'/api/attachments/{Attachment.objects.get().pk}/download'
        self.assertEqual(APIClient().get(url).status_code, 403)
        result = self.client.get(url)
        self.assertEqual(result.status_code, 200)
        self.assertIn('attachment;', result['Content-Disposition'])
        self.assertEqual(b''.join(result.streaming_content), b'hello')

    def test_reset_password_revokes_existing_sessions_and_requires_change(self):
        admin = self.as_user(self.admin)
        result = admin.patch(f'/api/accounts/{self.alice.pk}', {'operation': 'reset'}, format='json')
        self.assertEqual(result.status_code, 200)
        password = result.data['temporary_password']
        self.assertEqual(self.client.get('/api/tickets').status_code, 403)
        fresh = APIClient()
        self.assertEqual(fresh.post('/api/auth/login', {'email': self.alice.email, 'password': password}, format='json').status_code, 200)
        self.assertEqual(fresh.get('/api/tickets').status_code, 403)
        self.assertEqual(fresh.post('/api/auth/password', {'password': 'Brand-new-Secure-Password-2026!'}, format='json').status_code, 200)
        self.assertEqual(fresh.get('/api/tickets').status_code, 200)
        self.assertNotIn(password, str(list(Audit.objects.values())))

    def test_disable_revokes_session_and_self_disable_is_forbidden(self):
        admin = self.as_user(self.admin)
        self.assertEqual(admin.patch(f'/api/accounts/{self.admin.pk}', {'operation': 'disable'}, format='json').status_code, 400)
        self.assertEqual(admin.patch(f'/api/accounts/{self.alice.pk}', {'operation': 'disable'}, format='json').status_code, 200)
        self.assertEqual(self.client.get('/api/tickets').status_code, 403)

    def test_no_public_signup_and_account_password_is_random(self):
        self.assertEqual(APIClient().post('/api/accounts', {}, format='json').status_code, 403)
        result = self.as_user(self.admin).post('/api/accounts', {'name': '新顾问', 'email': 'new@example.com', 'role': 'consultant'}, format='json')
        self.assertEqual(result.status_code, 201)
        self.assertTrue(User.objects.get(email='new@example.com').check_password(result.data['temporary_password']))
        self.assertNotIn('password', result.data['user'])

    def test_queue_is_blocked_when_unconfigured_and_retry_is_admin_only(self):
        self.ticket()
        self.assertTrue(deliver_one(DingTalkClient()))
        job = Delivery.objects.get()
        self.assertEqual(job.state, 'blocked')
        self.assertEqual(self.client.post(f'/api/deliveries/{job.pk}/retry').status_code, 403)
        self.assertEqual(self.as_user(self.admin).post(f'/api/deliveries/{job.pk}/retry').status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.state, 'pending')

    def test_queue_failure_backoff_and_acceptance(self):
        self.ticket()
        client = Mock()
        client.send.side_effect = DeliveryError('模拟超时')
        deliver_one(client)
        job = Delivery.objects.get()
        self.assertEqual(job.state, 'pending')
        self.assertGreater(job.next_attempt_at, timezone.now())
        self.assertFalse(deliver_one(client))
        Delivery.objects.filter(pk=job.pk).update(next_attempt_at=timezone.now())
        client.send.side_effect = None
        client.send.return_value = 'provider-reference'
        deliver_one(client)
        job.refresh_from_db()
        self.assertEqual(job.state, 'accepted')
        self.assertEqual(job.attempts, 2)

    @override_settings(DINGTALK_ENABLED=True, PUBLIC_BASE_URL='https://desk.example.com', DINGTALK_GROUP_TOKEN='test-token', DINGTALK_GROUP_SECRET='test-secret')
    def test_shared_group_mentions_only_intended_members_without_business_text(self):
        self.admin.dingtalk_mobile = '13800000001'
        self.admin.save()
        self.alice.dingtalk_mobile = '13800000002'
        self.alice.save()
        self.bob.dingtalk_mobile = '13800000003'
        self.bob.save()
        t = self.ticket()
        client = DingTalkClient()
        with patch.object(client, 'post', return_value={'errcode': 0}) as send:
            client.send(Delivery.objects.get())
            payload = send.call_args.kwargs['json']
            self.assertEqual(payload['at'], {'atMobiles': ['13800000001'], 'isAtAll': False})
            self.assertIn('@13800000001', payload['text']['content'])
            self.assertIn('紧急程度：高', payload['text']['content'])
            self.assertNotIn(t.title, payload['text']['content'])
            self.assertNotIn(t.description, payload['text']['content'])
            self.assertIn('sign', send.call_args.kwargs['params'])
        reply_ticket(self.admin, t.pk, {'body': '不要外发的回复内容', 'version': 1}, [])
        job = Delivery.objects.select_related('recipient', 'event__ticket').get(channel='group_consultant')
        with patch.object(client, 'post', return_value={'errcode': 0}) as send:
            client.send(job)
            self.assertEqual(send.call_count, 1)
            self.assertEqual(send.call_args.args[0], 'https://oapi.dingtalk.com/robot/send')
            payload = send.call_args.kwargs['json']
            self.assertEqual(payload['at'], {'atMobiles': ['13800000002'], 'isAtAll': False})
            self.assertIn('@13800000002', payload['text']['content'])
            self.assertIn('/#/tickets/', payload['text']['content'])
            self.assertIn('紧急程度：高', payload['text']['content'])
            self.assertNotIn('不要外发的回复内容', payload['text']['content'])
            self.assertNotIn('13800000003', str(payload))

    @override_settings(DINGTALK_ENABLED=True, PUBLIC_BASE_URL='https://desk.example.com', DINGTALK_GROUP_TOKEN='test-token', DINGTALK_GROUP_SECRET='test-secret')
    def test_group_message_contains_each_priority_label(self):
        self.admin.dingtalk_mobile = '13800000001'
        self.admin.save()
        client = DingTalkClient()
        for priority, label in [('low', '低'), ('medium', '中'), ('high', '高'), ('urgent', '紧急')]:
            t = self.ticket(priority=priority)
            with patch.object(client, 'post', return_value={'errcode': 0}) as send:
                client.send(Delivery.objects.get(event__ticket=t))
                text = send.call_args.kwargs['json']['text']['content']
                self.assertIn(f'紧急程度：{label}\n请登录查看：', text)
                self.assertNotIn(t.title, text)
                self.assertNotIn(t.description, text)

    @override_settings(DINGTALK_ENABLED=True, PUBLIC_BASE_URL='https://desk.example.com', DINGTALK_GROUP_TOKEN='test-token', DINGTALK_GROUP_SECRET='test-secret')
    def test_group_binding_failures_and_legacy_messages_never_send(self):
        t = self.ticket()
        client = DingTalkClient()
        with patch.object(client, 'post') as send:
            deliver_one(client)
            self.assertEqual(Delivery.objects.get().state, 'blocked')
            for channel in ['group', 'personal']:
                job = Delivery.objects.create(event=t.events.first(), channel=channel)
                with self.assertRaises(DeliveryError):
                    client.send(job)
            send.assert_not_called()
        self.admin.dingtalk_mobile = '13800000001'
        self.admin.save()
        disabled = self.make_user('disabled', 'admin')
        disabled.is_active = False
        disabled.save()
        with patch.object(client, 'post', return_value={'errcode': 0}) as send:
            client.send(Delivery.objects.get(channel='group_admin'))
            self.assertEqual(send.call_args.kwargs['json']['at']['atMobiles'], ['13800000001'])
        reply_ticket(self.admin, t.pk, {'body': '更新', 'version': 1}, [])
        job = Delivery.objects.get(channel='group_consultant')
        self.alice.dingtalk_mobile = '13800000002'
        self.alice.is_active = False
        self.alice.save()
        with patch.object(client, 'post') as send:
            with self.assertRaises(DeliveryError):
                client.send(job)
            job.recipient = self.bob
            with self.assertRaises(DeliveryError):
                client.send(job)
            send.assert_not_called()

    def test_mobile_binding_validation_unique_preserved_and_private(self):
        client = self.as_user(self.admin)
        data = {'name': self.alice.name, 'email': self.alice.email, 'role': 'consultant'}
        for mobile in ['123', '13800000001@all', '+8613800000001']:
            self.assertEqual(client.patch(f'/api/accounts/{self.alice.pk}', {**data, 'dingtalk_mobile': mobile}, format='json').status_code, 400)
        self.assertEqual(client.patch(f'/api/accounts/{self.alice.pk}', {**data, 'dingtalk_mobile': '13800000001'}, format='json').status_code, 200)
        # An older client that omits this new field must not clear it.
        self.assertEqual(client.patch(f'/api/accounts/{self.alice.pk}', data, format='json').status_code, 200)
        self.alice.refresh_from_db()
        self.assertEqual(self.alice.dingtalk_mobile, '13800000001')
        self.assertNotIn('13800000001', str(list(Audit.objects.values_list('detail', flat=True))))
        other = {'name': self.bob.name, 'email': self.bob.email, 'role': 'consultant', 'dingtalk_mobile': '13800000001'}
        self.assertEqual(client.patch(f'/api/accounts/{self.bob.pk}', other, format='json').status_code, 400)
        self.assertEqual(client.post('/api/accounts', {**other, 'email': 'new@example.com'}, format='json').status_code, 400)
        self.assertEqual(self.as_user(self.bob).get('/api/accounts').status_code, 403)
        t = self.ticket()
        self.assertNotIn('13800000001', str(client.get(f'/api/tickets/{t.pk}').data))

    def test_signature_matches_protocol(self):
        timestamp = 1700000000000
        expected = base64.b64encode(hmac.new(b'secret', b'1700000000000\nsecret', hashlib.sha256).digest()).decode()
        self.assertEqual(signed_parameters('token', 'secret', timestamp)['sign'], expected)

    def test_duplicate_dingtalk_binding_is_rejected(self):
        self.alice.dingtalk_user_id = 'same-user'
        self.alice.save()
        admin = self.as_user(self.admin)
        result = admin.patch(f'/api/accounts/{self.bob.pk}', {'name': self.bob.name, 'email': self.bob.email, 'role': 'consultant', 'dingtalk_user_id': 'same-user'}, format='json')
        self.assertEqual(result.status_code, 400)
        self.bob.refresh_from_db()
        self.assertEqual(self.bob.dingtalk_user_id, '')

    def test_role_change_revokes_session_and_hides_foreign_notifications(self):
        other_admin = self.make_user('otheradmin', 'admin')
        old_session = self.as_user(other_admin)
        self.ticket()
        self.assertEqual(old_session.get('/api/notifications').data['count'], 1)
        result = self.as_user(self.admin).patch(f'/api/accounts/{other_admin.pk}', {'name': other_admin.name, 'email': other_admin.email, 'role': 'consultant'}, format='json')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(old_session.get('/api/tickets').status_code, 403)
        other_admin.refresh_from_db()
        fresh = self.as_user(other_admin)
        self.assertEqual(fresh.get('/api/notifications').data['count'], 0)
        self.assertEqual(fresh.get('/api/tickets').data['count'], 0)

    def test_authenticated_ticket_write_also_requires_csrf(self):
        client = APIClient(enforce_csrf_checks=True)
        token = client.get('/api/auth/session').json()['csrf_token']
        result = client.post('/api/auth/login', {'email': self.alice.email, 'password': PASSWORD}, format='json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(client.post('/api/tickets', self.data(), format='json').status_code, 403)
        self.assertEqual(client.post('/api/tickets', self.data(), format='json', HTTP_X_CSRFTOKEN=result.json()['csrf_token']).status_code, 201)

    @override_settings(ALLOWED_HOSTS=['43.130.36.105', 'testserver'], CSRF_TRUSTED_ORIGINS=['https://43.130.36.105'], SESSION_COOKIE_SECURE=True, CSRF_COOKIE_SECURE=True, PUBLIC_BASE_URL='https://43.130.36.105')
    def test_https_ip_login_csrf_and_notification_address(self):
        from .dingtalk import configuration_status
        self.assertTrue(configuration_status()['public_url_ready'])
        client = APIClient(enforce_csrf_checks=True)
        host = {'HTTP_HOST': '43.130.36.105', 'secure': True}
        token = client.get('/api/auth/session', **host).json()['csrf_token']
        result = client.post('/api/auth/login', {'email': self.alice.email, 'password': PASSWORD}, format='json', HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN='https://43.130.36.105', **host)
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.cookies['sessionid']['secure'])
        self.assertTrue(result.cookies['sessionid']['httponly'])
        response = client.post('/api/tickets', self.data(), format='json', HTTP_X_CSRFTOKEN=result.json()['csrf_token'], HTTP_ORIGIN='https://43.130.36.105', **host)
        self.assertEqual(response.status_code, 201)
        forbidden = client.post('/api/tickets', self.data(), format='json', HTTP_X_CSRFTOKEN=result.json()['csrf_token'], HTTP_ORIGIN='https://evil.example', **host)
        self.assertEqual(forbidden.status_code, 403)
