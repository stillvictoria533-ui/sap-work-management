import uuid
from unittest.mock import patch
from django.test import TestCase, override_settings
from rest_framework.test import APIClient
from desk.models import User
from .models import Conversation, Turn
from .worker import process_one


@override_settings(AI_PROVIDER='disabled', PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AssistantAdminAccessTests(TestCase):
    def setUp(self):
        self.admin = self.user('admin', 'admin')
        self.consultant = self.user('consultant', 'consultant')

    def user(self, name, role):
        return User.objects.create_user(username=name+'@admin-access.test', email=name+'@admin-access.test',
            name=name, role=role, password='Test-only-password!', must_change_password=False)

    def login(self, user):
        client = APIClient(); client.force_login(user)
        session = client.session; session['version'] = user.session_version; session.save()
        return client

    def test_consultant_cannot_access_any_chat_api_even_own_old_conversation(self):
        conversation = Conversation.objects.create(owner=self.consultant, system='DS4')
        turn = Turn.objects.create(conversation=conversation, request_key=uuid.uuid4(), input_text='old question',
            status='completed', output_text='preserved', session_version=self.consultant.session_version)
        root=f'/api/assistant/conversations/{conversation.pk}'
        cases=[('get','/api/assistant/status'),('get','/api/assistant/conversations'),
            ('post','/api/assistant/conversations'),('get',root),('post',root+'/turns'),
            ('post',root+f'/turns/{turn.pk}/retry'),('post',root+f'/turns/{turn.pk}/cancel'),
            ('get',root+f'/turns/{turn.pk}/stream')]
        client=self.login(self.consultant)
        for method,url in cases:
            self.assertEqual(getattr(client,method)(url).status_code,403,url)
        self.assertTrue(Turn.objects.filter(pk=turn.pk,output_text='preserved').exists())
        self.assertEqual(client.get('/api/tickets').status_code,200)

    def test_admin_still_has_access_and_ownership_is_preserved(self):
        client=self.login(self.admin)
        self.assertEqual(client.get('/api/assistant/status').status_code,200)
        response=client.post('/api/assistant/conversations',{'request_key':str(uuid.uuid4()),'system':'DS4'},format='json')
        self.assertEqual(response.status_code,201)
        other=self.user('other-admin','admin')
        self.assertEqual(self.login(other).get('/api/assistant/conversations/'+str(response.data['id'])).status_code,404)

    def test_role_revocation_blocks_pending_model_execution(self):
        conversation=Conversation.objects.create(owner=self.consultant,system='DS4')
        turn=Turn.objects.create(conversation=conversation,request_key=uuid.uuid4(),input_text='pending',
            status='queued',session_version=self.consultant.session_version)
        with patch('assistant.worker.heartbeat'),patch('assistant.worker.generate') as generate:
            self.assertTrue(process_one())
            generate.assert_not_called()
        turn.refresh_from_db()
        self.assertEqual(turn.status,'failed')
        self.assertEqual(turn.error_code,'access_changed')

    def test_demoted_admin_is_denied_immediately_with_same_session(self):
        client=self.login(self.admin)
        self.assertEqual(client.get('/api/assistant/status').status_code,200)
        self.admin.role='consultant';self.admin.save(update_fields=['role'])
        self.assertEqual(client.get('/api/assistant/status').status_code,403)
