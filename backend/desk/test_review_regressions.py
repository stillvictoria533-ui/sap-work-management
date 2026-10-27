"""Regressions from the 2026-10-27 review; no live SAP or provider calls."""
import uuid
import tempfile
from unittest.mock import patch

from django.test import TestCase, SimpleTestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.core.files.uploadedfile import SimpleUploadedFile
from rest_framework.test import APIClient

from .ai_policy import PolicyError, cli_arguments, freeze_scope, validate_plan
from .models import Audit, User, Ticket, Attachment, Event
from .serializers import PasswordInput


class QueryScopeRegressionTests(SimpleTestCase):
    def test_required_filters_cannot_be_omitted_in_any_system(self):
        cases = [
            ('MARC', '物料 12345 工厂 1000', {'MATNR': '12345', 'WERKS': '1000'}),
            ('BKPF', '会计凭证 12345 公司代码 1000 会计年度 2026',
             {'BELNR': '12345', 'BUKRS': '1000', 'GJAHR': '2026'}),
            ('EKKO', '采购订单 4500012345 公司代码 1000', {'EBELN': '4500012345', 'BUKRS': '1000'}),
            ('MAKT', '物料 12345 语言代码 ZH', {'MATNR': '12345', 'SPRAS': 'ZH'}),
        ]
        for system in ['DS4', 'QS4', 'PS4']:
            for table, text, keys in cases:
                scope = freeze_scope(system, table, text, '开始处理', 1)
                plan = {'command': 'data', 'object': table, 'fields': list(keys),
                        'filters': [{'field': field, 'value': value} for field, value in keys.items()]}
                args = cli_arguments(plan, scope)
                for field, value in keys.items():
                    self.assertIn(field + '=' + value, args)
                    if len(keys) > 1:
                        incomplete = {**plan, 'filters': [f for f in plan['filters'] if f['field'] != field]}
                        with self.subTest(system=system, table=table, omitted=field):
                            with self.assertRaises(PolicyError):
                                validate_plan(incomplete, scope)
                            with self.assertRaises(PolicyError):
                                cli_arguments(incomplete, scope)

    def test_missing_composite_keys_require_clarification_even_for_old_scopes(self):
        for table, text, field in [('MARC', '物料 12345', 'MATNR'), ('BKPF', '会计凭证 12345', 'BELNR')]:
            scope = freeze_scope('DS4', table, text, '开始处理', 1)
            # Already accepted v1 scopes predate the stronger composite-key metadata.
            scope['data_keys'][table] = field
            with self.subTest(table=table), self.assertRaises(PolicyError):
                validate_plan({'command': 'data', 'object': table, 'fields': [field],
                               'filters': [{'field': field, 'value': '12345'}]}, scope)


class PasswordFixture:
    old_password = 'Original-password-2026!'
    new_password = 'Replacement-password-2026!'

    def setUp(self):
        self.admin = self.make_user('admin', 'admin')
        self.owner = self.make_user('owner', 'consultant')
        self.client = self.login(self.owner)
        self.admin_client = self.login(self.admin)

    def make_user(self, name, role):
        return User.objects.create_user(username=name, email=name + '@review.test', name=name, role=role,
                                       password=self.old_password, must_change_password=False)

    def login(self, user):
        client = APIClient()
        client.force_login(user)
        session = client.session
        session['version'] = user.session_version
        session.save()
        return client


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class PasswordRegressionTests(PasswordFixture, TestCase):

    def test_inflight_old_password_change_cannot_undo_admin_reset(self):
        validate = PasswordInput.is_valid
        reset_result = {}

        def reset_after_authentication(serializer, *args, **kwargs):
            result = validate(serializer, *args, **kwargs)
            response = self.admin_client.patch(f'/api/accounts/{self.owner.pk}', {'operation': 'reset'}, format='json')
            self.assertEqual(response.status_code, 200)
            reset_result.update(response.data)
            return result

        with patch.object(PasswordInput, 'is_valid', reset_after_authentication):
            response = self.client.post('/api/auth/password', {
                'current_password': self.old_password, 'password': self.new_password}, format='json')
        self.assertEqual(response.status_code, 403)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(reset_result['temporary_password']))
        self.assertTrue(self.owner.must_change_password)
        self.assertFalse(Audit.objects.filter(action='password_changed').exists())
        self.assertEqual(self.client.get('/api/tickets').status_code, 403)

    def test_current_password_retains_leading_and_trailing_spaces(self):
        padded = '  Password-with-spaces-2026!  '
        self.owner.set_password(padded)
        self.owner.save(update_fields=['password'])
        client = self.login(self.owner)
        response = client.post('/api/auth/password', {
            'current_password': padded, 'password': self.new_password}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(client.get('/api/tickets').status_code, 200)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class PasswordConcurrencyRegressionTests(PasswordFixture, TransactionTestCase):
    @skipUnlessDBFeature('has_select_for_update')
    def test_reset_waits_for_password_write_then_revokes_that_session(self):
        from concurrent.futures import ThreadPoolExecutor, TimeoutError
        from threading import Event as ThreadEvent
        from django.db import close_old_connections, connections

        locked, release, reset_started = ThreadEvent(), ThreadEvent(), ThreadEvent()
        set_password = User.set_password

        def pause_inside_password_write(user, password):
            if user.pk == self.owner.pk and password == self.new_password:
                locked.set()
                if not release.wait(5):
                    raise AssertionError('password write was not released')
            return set_password(user, password)

        def change():
            close_old_connections()
            try:
                return self.client.post('/api/auth/password', {
                    'current_password': self.old_password, 'password': self.new_password}, format='json')
            finally:
                connections.close_all()

        def reset():
            close_old_connections()
            reset_started.set()
            try:
                return self.admin_client.patch(f'/api/accounts/{self.owner.pk}', {'operation': 'reset'}, format='json')
            finally:
                connections.close_all()

        with patch.object(User, 'set_password', pause_inside_password_write), ThreadPoolExecutor(max_workers=2) as pool:
            changing = pool.submit(change)
            try:
                self.assertTrue(locked.wait(3))
                resetting = pool.submit(reset)
                self.assertTrue(reset_started.wait(3))
                with self.assertRaises(TimeoutError):
                    resetting.result(timeout=0.2)
            finally:
                release.set()
            self.assertEqual(changing.result(timeout=5).status_code, 200)
            response = resetting.result(timeout=5)
            self.assertEqual(response.status_code, 200, response.data)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(response.data['temporary_password']))
        self.assertTrue(self.owner.must_change_password)
        self.assertEqual(self.owner.session_version, 3)
        self.assertEqual(self.client.get('/api/tickets').status_code, 403)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class SubmissionAndParameterRegressionTests(TestCase):
    def setUp(self):
        media = tempfile.TemporaryDirectory()
        self.addCleanup(media.cleanup)
        settings = override_settings(MEDIA_ROOT=media.name)
        settings.enable()
        self.addCleanup(settings.disable)
        self.owner = User.objects.create_user(username='owner', email='owner@review.test', name='owner',
                                             must_change_password=False)
        self.admin = User.objects.create_user(username='admin', email='admin@review.test', name='admin',
                                             role='admin', must_change_password=False)
        self.client = self.login(self.owner)
        self.data = {'request_key': str(uuid.uuid4()), 'title': 'Unicode ² search', 'description': 'original',
                     'system': 'DS4', 'module': 'MM', 'kind': '问题'}

    def login(self, user):
        client = APIClient()
        client.force_login(user)
        session = client.session
        session['version'] = user.session_version
        session.save()
        return client

    def test_same_key_changed_content_conflicts_and_preserves_original(self):
        first = self.client.post('/api/tickets', self.data, format='json')
        self.assertEqual(first.status_code, 201)
        for change in [{'title': 'changed'}, {'description': 'changed'}, {'priority': 'high'},
                       {'system': 'PS4'}, {'module': 'FI'}, {'kind': '配置'}, {'due_date': '2099-01-01'}]:
            response = self.client.post('/api/tickets', {**self.data, **change}, format='json')
            self.assertEqual(response.status_code, 409, response.data)
            self.assertEqual(str(response.data['ticket_id']), str(first.data['id']))
        same = self.client.post('/api/tickets', {**self.data, 'priority': 'medium', 'due_date': None}, format='json')
        self.assertEqual(same.status_code, 200)
        self.assertEqual(Ticket.objects.get().description, 'original')
        self.assertEqual(Event.objects.count(), 1)

    def test_same_name_and_size_but_changed_attachment_bytes_conflicts(self):
        def send(content):
            return self.client.post('/api/tickets', {**self.data, 'files': [SimpleUploadedFile('note.txt', content)]}, format='multipart')
        self.assertEqual(send(b'original').status_code, 201)
        self.assertEqual(send(b'original').status_code, 200)
        self.assertEqual(send(b'modified').status_code, 409)
        self.assertEqual(Attachment.objects.count(), 1)
        with Attachment.objects.get().file.open('rb') as file:
            self.assertEqual(file.read(), b'original')

    def test_legacy_key_without_fingerprint_directs_to_original_instead_of_guessing(self):
        self.client.post('/api/tickets', self.data, format='json')
        Ticket.objects.update(submission_fingerprint='')
        response = self.client.post('/api/tickets', self.data, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(Ticket.objects.count(), 1)

    def test_unicode_and_oversized_ids_do_not_crash_search_or_filters(self):
        self.client.post('/api/tickets', self.data, format='json')
        client = self.login(self.admin)
        for value in ['²', '１２', '9' * 200, '-1', '0', '9223372036854775808']:
            for endpoint, key in [('/api/tickets', 'owner'), ('/api/scheduling/queue', 'focus_ticket')]:
                with self.subTest(value=value, key=key):
                    self.assertEqual(client.get(endpoint, {key: value}).status_code, 400)
            for endpoint in ['/api/tickets', '/api/scheduling/queue']:
                with self.subTest(value=value, endpoint=endpoint):
                    self.assertEqual(client.get(endpoint, {'q': value}).status_code, 200)
        self.assertEqual(client.get('/api/tickets', {'q': '²'}).data['count'], 1)

    def test_list_omits_long_description_and_version_endpoint_preserves_scope(self):
        response = self.client.post('/api/tickets', self.data, format='json')
        pk = response.data['id']
        self.assertNotIn('description', self.client.get('/api/tickets').data['results'][0])
        self.assertEqual(self.client.get(f'/api/tickets/{pk}').data['description'], 'original')
        endpoint = f'/api/tickets/{pk}/version'
        self.assertEqual(self.client.get(endpoint).data, {'id': pk, 'version': 1})
        other = User.objects.create_user(username='other', name='other', email='other@review.test', must_change_password=False)
        self.assertEqual(self.login(other).get(endpoint).status_code, 404)
        self.assertEqual(APIClient().get(endpoint).status_code, 403)

    def test_csv_stream_keeps_utf8_bom_and_formula_escaping(self):
        self.client.post('/api/tickets', {**self.data, 'title': '=SUM(1,2)'}, format='json')
        response = self.login(self.admin).get('/api/tickets/export')
        self.assertTrue(response.streaming)
        content = iter(response.streaming_content)
        self.assertEqual(next(content), b'\xef\xbb\xbf')
        self.assertIn("'=SUM(1,2)", b''.join(content).decode())
