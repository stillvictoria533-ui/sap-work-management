import uuid
from datetime import timedelta
from unittest.mock import patch, Mock

from django.conf import settings
from django.test import TestCase, SimpleTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from desk.models import User, Ticket, Event, Delivery
from .models import Conversation, Turn, WorkerState
from .providers import ProviderError, Cancelled, codex_arguments, codex_environment, run_openai
from .services import provider_state
from .worker import process_one, build_context, recover_interrupted


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'], AI_PROVIDER='disabled')
class ChatTests(TestCase):
    def setUp(self):
        self.owner = self.user('owner')
        self.other = self.user('other')
        self.admin = self.user('admin', 'admin')
        self.client = self.login(self.owner)

    def user(self, name, role='admin'):
        return User.objects.create_user(username=f'{name}@local.test', email=f'{name}@local.test', name=name,
                                        role=role, password='Test-password-for-chat!', must_change_password=False)

    def login(self, user, csrf=False):
        client = APIClient(enforce_csrf_checks=csrf)
        client.force_login(user)
        session = client.session
        session['version'] = user.session_version
        session.save()
        return client

    def conversation(self, client=None, **extra):
        response = (client or self.client).post('/api/assistant/conversations',
                    {'request_key': str(uuid.uuid4()), 'system': 'DS4', **extra}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        return Conversation.objects.get(pk=response.data['id'])

    def send(self, conversation, **extra):
        return self.client.post(f'/api/assistant/conversations/{conversation.pk}/turns',
                               {'request_key': str(uuid.uuid4()), 'message': '解释这段 ABAP 代码。', **extra}, format='json')

    def ready(self):
        WorkerState.objects.create(name='chat', provider_kind='openai', provider_configured=True,
                                   provider_code='', provider_name='OpenAI API', provider_model='test-model')
        self.override = override_settings(AI_PROVIDER='openai', OPENAI_API_KEY='test-key', OPENAI_MODEL='test-model')
        self.override.enable()
        self.addCleanup(self.override.disable)

    def test_authentication_csrf_and_reset_session_are_enforced(self):
        self.assertEqual(APIClient().get('/api/assistant/status').status_code, 403)
        self.assertEqual(self.login(self.owner, True).post('/api/assistant/conversations', {}, format='json').status_code, 403)
        self.owner.session_version += 1
        self.owner.save()
        self.assertEqual(self.client.get('/api/assistant/conversations').status_code, 403)

    def test_conversations_are_private_even_from_other_admin(self):
        conversation = self.conversation()
        self.send(conversation)
        turn = conversation.turns.get()
        for user in [self.other, self.admin]:
            client = self.login(user)
            self.assertEqual(client.get('/api/assistant/conversations').data['count'], 0)
            root = f'/api/assistant/conversations/{conversation.pk}'
            self.assertEqual(client.get(root).status_code, 404)
            self.assertEqual(client.post(root + '/turns', {'message': '越权', 'request_key': str(uuid.uuid4())}, format='json').status_code, 404)
            self.assertEqual(client.get(root + f'/turns/{turn.pk}/stream').status_code, 404)
            for action in ['retry', 'cancel']:
                self.assertEqual(client.post(root + f'/turns/{turn.pk}/{action}', {'attempt': 0}, format='json').status_code, 404)

    def test_unconfigured_questions_are_saved_without_fabricated_answers_or_notifications(self):
        conversation = self.conversation()
        response = self.send(conversation)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data['status'], 'blocked')
        self.assertEqual(response.data['output_text'], '')
        self.assertIn('尚未配置', response.data['error_message'])
        detail = self.client.get(f'/api/assistant/conversations/{conversation.pk}').data
        self.assertEqual(detail['turns'][0]['input_text'], '解释这段 ABAP 代码。')
        self.assertEqual(detail['title'], '解释这段 ABAP 代码。')
        self.assertEqual(Event.objects.count(), 0)
        self.assertEqual(Delivery.objects.count(), 0)
        self.assertFalse(self.client.get('/api/assistant/status').data['sap']['connected'])

    def test_create_and_send_idempotency_and_changed_payload(self):
        request_key = str(uuid.uuid4())
        data = {'request_key': request_key, 'system': 'DS4'}
        first = self.client.post('/api/assistant/conversations', data, format='json')
        self.assertEqual(self.client.post('/api/assistant/conversations', data, format='json').data['id'], first.data['id'])
        self.assertEqual(self.client.post('/api/assistant/conversations', {**data, 'system': 'QS4'}, format='json').status_code, 409)
        item = Conversation.objects.get(pk=first.data['id'])
        self.send(item, request_key=request_key)
        self.assertEqual(self.send(item, request_key=request_key).status_code, 200)
        self.assertEqual(self.send(item, request_key=request_key, message='不同问题').status_code, 409)
        self.assertEqual(item.turns.count(), 1)

    def test_bad_inputs_and_production_system_are_rejected(self):
        self.assertEqual(self.client.post('/api/assistant/conversations', {'request_key': str(uuid.uuid4()), 'system': 'PS4'}, format='json').status_code, 400)
        item = self.conversation()
        for text in ['', '  ', 'a' * 12001]:
            self.assertEqual(self.send(item, message=text).status_code, 400)
        self.assertEqual(item.turns.count(), 0)

    def test_linked_ticket_is_scoped_and_hidden_after_role_change(self):
        ticket = Ticket.objects.create(owner=self.other, title='他人工单', module='MM', kind='问题', description='私有信息')
        consultant = self.user('consultant', 'consultant')
        response = self.login(consultant).post('/api/assistant/conversations', {'request_key': str(uuid.uuid4()), 'system': 'DS4', 'ticket_id': ticket.pk}, format='json')
        self.assertEqual(response.status_code, 403)
        admin_client = self.login(self.admin)
        item = self.conversation(admin_client, ticket_id=ticket.pk)
        self.admin.role = 'consultant'; self.admin.save()
        self.assertEqual(admin_client.get('/api/assistant/conversations').status_code, 403)
        self.assertEqual(admin_client.get(f'/api/assistant/conversations/{item.pk}').status_code, 403)

    def test_worker_stream_completion_and_context_stay_in_one_conversation(self):
        self.ready()
        item = self.conversation()
        other = self.conversation()
        Turn.objects.create(conversation=other, request_key=uuid.uuid4(), input_text='绝不能发送给此对话', output_text='其他会话', status='completed', session_version=1)
        self.assertEqual(self.send(item).data['status'], 'queued')
        seen = []
        def generate(messages, publish, alive):
            seen.extend(messages)
            self.assertTrue(alive())
            publish('根据你提供的代码分析。')
            return {'output_tokens': 10}
        with patch('assistant.worker.generate', side_effect=generate):
            self.assertTrue(process_one())
        turn = item.turns.get()
        self.assertEqual(turn.status, 'completed')
        self.assertNotIn('绝不能发送', str(seen))
        self.assertEqual(turn.output_text, '根据你提供的代码分析。')
        response = self.client.get(f'/api/assistant/conversations/{item.pk}/turns/{turn.pk}/stream', HTTP_ACCEPT='text/event-stream')
        self.assertEqual(response.status_code, 200)
        text = b''.join(response.streaming_content).decode()
        self.assertIn('event: snapshot', text)
        self.assertIn('event: end', text)
        self.assertIn('根据你提供的代码分析', text)
        self.assertEqual(response['X-Accel-Buffering'], 'no')

    def test_cancel_during_provider_call_does_not_resurrect_turn(self):
        self.ready()
        item = self.conversation(); self.send(item); turn = item.turns.get()
        def generate(messages, publish, alive):
            publish('部分回答')
            self.client.post(f'/api/assistant/conversations/{item.pk}/turns/{turn.pk}/cancel', {}, format='json')
            self.assertFalse(alive())
            publish('不应继续保存')
            return {}
        with patch('assistant.worker.generate', side_effect=generate):
            process_one()
        turn.refresh_from_db()
        self.assertEqual(turn.status, 'cancelled')
        self.assertEqual(turn.output_text, '部分回答')

    def test_expired_authorization_blocks_execution_and_stream(self):
        self.ready()
        item = self.conversation(); self.send(item); turn = item.turns.get()
        response = self.client.get(f'/api/assistant/conversations/{item.pk}/turns/{turn.pk}/stream')
        self.owner.session_version += 1; self.owner.save()
        with patch('assistant.worker.generate') as call:
            process_one()
        call.assert_not_called()
        turn.refresh_from_db()
        self.assertEqual(turn.error_code, 'access_changed')
        self.assertIn('event: revoked', b''.join(response.streaming_content).decode())

    def test_revocation_before_completion_is_checked_again(self):
        self.ready()
        item = self.conversation(); self.send(item)
        def generate(messages, publish, alive):
            alive(); publish('部分回答')
            User.objects.filter(pk=self.owner.pk).update(is_active=False)
            return {}
        with patch('assistant.worker.generate', side_effect=generate):
            process_one()
        self.assertEqual(item.turns.get().error_code, 'access_changed')

    def test_provider_exception_is_redacted_and_retry_is_explicit(self):
        self.ready()
        item = self.conversation(); self.send(item)
        with patch('assistant.worker.generate', side_effect=RuntimeError('secret-key-not-for-users')):
            process_one()
        turn = item.turns.get()
        self.assertEqual(turn.status, 'failed')
        detail = self.client.get(f'/api/assistant/conversations/{item.pk}').data
        self.assertNotIn('secret-key', str(detail))
        self.assertFalse(process_one())
        path = f'/api/assistant/conversations/{item.pk}/turns/{turn.pk}/retry'
        self.assertEqual(self.client.post(path, {'attempt': 1}, format='json').data['status'], 'queued')
        self.assertEqual(self.client.post(path, {'attempt': 1}, format='json').status_code, 409)

    def test_budget_and_busy_reject_extra_generation(self):
        self.ready()
        item = self.conversation(); self.send(item)
        self.assertEqual(self.send(item).status_code, 409)
        Turn.objects.filter(conversation=item).update(status='completed')
        with override_settings(AI_TURNS_PER_DAY=1):
            self.assertEqual(self.send(item).status_code, 429)

    def test_stale_worker_and_interrupted_jobs_have_honest_states(self):
        self.ready()
        WorkerState.objects.update(heartbeat_at=timezone.now() - timedelta(minutes=5))
        item = self.conversation()
        self.assertEqual(self.send(item).data['error_code'], 'worker_offline')
        Turn.objects.filter(conversation=item).update(status='running', updated_at=timezone.now()-timedelta(minutes=5))
        recover_interrupted()
        self.assertEqual(item.turns.get().error_code, 'interrupted')

    @override_settings(AI_PROVIDER='codex')
    def test_api_reads_worker_readiness_without_cli_or_auth_files(self):
        WorkerState.objects.create(name='chat', provider_kind='codex', provider_configured=True,
                                   provider_code='', provider_name='Codex CLI', provider_model='default')
        with patch('assistant.providers.codex_probe') as probe:
            self.assertTrue(provider_state()['ready'])
        probe.assert_not_called()
        WorkerState.objects.update(provider_kind='openai')
        self.assertFalse(provider_state()['ready'])


class ProviderTests(SimpleTestCase):
    def test_codex_uses_fixed_arguments_and_disables_execution_surfaces(self):
        with override_settings(AI_CODEX_BIN='codex', AI_CODEX_MODEL=''):
            args = codex_arguments('/private/tmp/test')
        self.assertIn('--ignore-user-config', args)
        self.assertIn('--ephemeral', args)
        self.assertIn('read-only', args)
        self.assertIn('approval_policy="never"', args)
        self.assertIn('web_search="disabled"', args)
        for feature in ['shell_tool', 'apps', 'plugins', 'hooks', 'computer_use', 'multi_agent', 'view_image']:
            index = args.index(feature)
            self.assertEqual(args[index-1], '--disable')
        self.assertEqual(args[-1], '-')
        with patch.dict('os.environ', {'SAP_PASSWORD': 'private', 'OPENAI_API_KEY': 'private', 'DJANGO_SECRET_KEY': 'private', 'CODEX_THREAD_ID': 'parent'}):
            self.assertNotIn('SAP_PASSWORD', codex_environment())
            self.assertNotIn('OPENAI_API_KEY', codex_environment())
            self.assertNotIn('CODEX_THREAD_ID', codex_environment())

    @override_settings(OPENAI_API_KEY='private-key', OPENAI_MODEL='test-model')
    def test_responses_stream_requires_terminal_success_and_handles_refusal(self):
        response = Mock(status_code=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_lines.return_value = [b'data: {"type":"response.refusal.delta","delta":"Cannot answer"}',
                                           b'data: {"type":"response.completed","response":{"status":"completed","usage":{"output_tokens":2}}}']
        output = []
        with patch('assistant.providers.requests.post', return_value=response) as request:
            result = run_openai([{'role':'user','content':'test'}], output.append, lambda: True)
        self.assertEqual(output, ['Cannot answer'])
        self.assertEqual(result, {'output_tokens': 2})
        payload = request.call_args.kwargs['json']
        self.assertFalse(payload['store'])
        self.assertNotIn('tools', payload)
        self.assertFalse(request.call_args.kwargs['allow_redirects'])
        response.iter_lines.return_value = [b'data: {"type":"response.output_text.delta","delta":"partial"}']
        with patch('assistant.providers.requests.post', return_value=response), self.assertRaises(ProviderError):
            run_openai([], lambda value: None, lambda: True)
