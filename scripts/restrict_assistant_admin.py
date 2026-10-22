"""Apply the reviewed administrator-only guard to an explicit source tree.

Used on the local tree and a separate copy of current production sources, so
unpublished connector work is not included in this narrow release.
"""
from pathlib import Path
import re
import sys


def apply(root):
    root = Path(root)
    changed = []
    def edit(name, transform):
        path = root / name
        old = path.read_text()
        new = transform(old)
        if old == new:
            raise RuntimeError(f'Expected an unpatched baseline: {name}')
        path.write_text(new)
        changed.append(name)

    def once(text, old, new):
        if text.count(old) != 1:
            raise RuntimeError('Source baseline does not match the reviewed patch')
        return text.replace(old, new, 1)

    edit('frontend/src/App.vue', lambda s: once(s, '<NavLink to="/assistant">', '<NavLink v-if="user?.role === \'admin\'" to="/assistant">'))
    edit('frontend/src/router.ts', lambda s: once(s, 'component: () => import("./pages/Assistant.vue"),', 'component: () => import("./pages/Assistant.vue"),\n      meta: { admin: true },'))
    def views(s):
        s = once(s, 'from desk.models import User', 'from desk.models import User\nfrom desk.permissions import Admin')
        s = re.sub(r'class (\w+)\(APIView\):', r'class \1(AssistantAdminView):', s)
        s = once(s, 'class ConversationInput', 'class AssistantAdminView(APIView):\n    permission_classes = [Admin]\n\n\nclass ConversationInput')
        s = once(s, 'pk=user_id, is_active=True, must_change_password=False, session_version=version',
                 "pk=user_id, role='admin', is_active=True, must_change_password=False, session_version=version")
        return s
    edit('backend/assistant/views.py', views)
    edit('backend/assistant/services.py', lambda s: once(s, 'def scoped_conversations(user):\n',
        "def scoped_conversations(user):\n    if user.role != 'admin':\n        return Conversation.objects.none()\n"))
    edit('backend/assistant/worker.py', lambda s: once(s, 'pk=turn.conversation.owner_id, is_active=True, must_change_password=False,',
        "pk=turn.conversation.owner_id, role='admin', is_active=True, must_change_password=False,"))
    # Existing positive-path fixtures represent eligible administrators after this change.
    edit('backend/assistant/tests.py', lambda s: once(once(s, "def user(self, name, role='consultant'):", "def user(self, name, role='admin'):"),
        "self.assertEqual(admin_client.get(f'/api/assistant/conversations/{item.pk}').status_code, 404)",
        "self.assertEqual(admin_client.get(f'/api/assistant/conversations/{item.pk}').status_code, 403)"))
    path = root / 'backend/assistant/tests.py'
    s = path.read_text()
    s = once(s, "response = self.client.post('/api/assistant/conversations', {'request_key': str(uuid.uuid4()), 'system': 'DS4', 'ticket_id': ticket.pk}, format='json')\n        self.assertEqual(response.status_code, 404)", "consultant = self.user('consultant', 'consultant')\n        response = self.login(consultant).post('/api/assistant/conversations', {'request_key': str(uuid.uuid4()), 'system': 'DS4', 'ticket_id': ticket.pk}, format='json')\n        self.assertEqual(response.status_code, 403)")
    s = once(s, "self.assertEqual(admin_client.get('/api/assistant/conversations').data['count'], 0)", "self.assertEqual(admin_client.get('/api/assistant/conversations').status_code, 403)")
    path.write_text(s)
    return changed


if __name__ == '__main__':
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python restrict_assistant_admin.py SOURCE_ROOT')
    print('\n'.join(apply(sys.argv[1])))
