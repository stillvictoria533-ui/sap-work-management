import uuid

from django.core.management import get_commands, call_command
from io import StringIO
import json
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import Event, Ticket, User


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class RetiredAIFeatureTests(TestCase):
    @override_settings(AI_TICKET_AUTOMATION_ENABLED=False)
    def test_automation_gateway_is_inert_until_enabled(self):
        self.assertIn('ai_tickets', get_commands())
        output = StringIO()
        call_command('ai_tickets', 'list', stdout=output)
        self.assertEqual(json.loads(output.getvalue())['results'], [])
        self.assertFalse(json.loads(output.getvalue())['enabled'])

    def test_historical_ai_reply_remains_readable_and_manual_reply_works(self):
        admin = User.objects.create_user(username='admin@example.com', email='admin@example.com', name='管理员', role='admin', must_change_password=False)
        owner = User.objects.create_user(username='owner@example.com', email='owner@example.com', name='顾问', role='consultant', must_change_password=False)
        ticket = Ticket.objects.create(owner=owner, title='既有工单', module='MM', kind='问题', description='历史沟通保留', status='in-progress', version=3)
        Event.objects.create(ticket=ticket, actor=admin, actor_name='AI · SAP 助手', actor_role='ai', kind='reply', body='AI：历史回答', is_ai=True, version=3)
        client = APIClient()
        client.force_login(owner)
        session = client.session
        session['version'] = owner.session_version
        session.save()
        detail = client.get(f'/api/tickets/{ticket.pk}')
        self.assertEqual(detail.status_code, 200)
        self.assertTrue(detail.data['events'][0]['is_ai'])
        self.assertEqual(detail.data['events'][0]['actor_role'], 'ai')
        response = client.post(f'/api/tickets/{ticket.pk}/reply', {'body': '继续人工沟通', 'version': 3}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'in-progress')
        self.assertEqual(ticket.events.filter(is_ai=True).count(), 1)
        data = {'request_key': str(uuid.uuid4()), 'title': '新工单', 'module': 'MM', 'kind': '查询', 'description': '退回旧类型列表'}
        self.assertEqual(client.post('/api/tickets', data, format='json').status_code, 400)
