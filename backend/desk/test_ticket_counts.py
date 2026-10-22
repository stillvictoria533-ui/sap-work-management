from datetime import timedelta
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from .models import User, Ticket, Event, Notification


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class TicketCountTests(TestCase):
    def setUp(self):
        self.admin = self.user('admin', 'admin')
        self.owner = self.user('owner')
        self.other = self.user('other')
        self.client = self.login(self.admin)

    def user(self, name, role='consultant'):
        return User.objects.create_user(username=name, email=name+'@counts.test', role=role,
                                        name=name, must_change_password=False)

    def login(self, user):
        client = APIClient()
        client.force_login(user)
        session = client.session
        session['version'] = user.session_version
        session.save()
        return client

    def ticket(self, **fields):
        return Ticket.objects.create(**{'owner': self.owner, 'title': '需求样例', 'module': 'MM',
                                      'kind': '开发', 'description': '测试说明', **fields})

    def unread(self, ticket, recipient, body='补充说明'):
        item = Event.objects.create(ticket=ticket, actor=self.owner, actor_name='顾问',
                                    actor_role='consultant', kind='reply', body=body, version=1)
        return Notification.objects.create(event=item, recipient=recipient)

    def assert_counts(self, params, expected, client=None):
        for tab, key in [('all', 'total'), ('active', 'active'), ('unread', 'unread_tickets')]:
            response = (client or self.client).get('/api/tickets', {**params, 'tab': tab})
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data['tab_counts'], expected)
            self.assertEqual(response.data['count'], expected[key])

    def test_attention_reproduces_global_18_active_7_but_current_scope_6(self):
        for _ in range(5):
            self.ticket(status='pending', next_actor='admin')
        self.ticket(status='in-progress', next_actor='admin')
        self.ticket(status='waiting', next_actor='consultant')
        for _ in range(11):
            self.ticket(status='done', next_actor='none')
        global_stats = self.client.get('/api/stats').data
        self.assertEqual((global_stats['total'], global_stats['active'], global_stats['attention']), (18, 7, 6))
        self.assert_counts({'scope': 'attention'}, {'total': 6, 'active': 6, 'unread_tickets': 0})
        self.assert_counts({'scope': 'archive'}, {'total': 11, 'active': 0, 'unread_tickets': 0})
        self.assert_counts({}, {'total': 18, 'active': 7, 'unread_tickets': 0})

    def test_filters_search_distinct_and_schedule_apply_before_tab_counts(self):
        ticket = self.ticket(status='in-progress', next_actor='admin', priority='high',
                             estimated_completion_date=timezone.localdate()-timedelta(days=1))
        self.unread(ticket, self.admin, '联合检索词')
        self.unread(ticket, self.admin, '联合检索词重复出现')
        self.ticket(module='SD', status='in-progress', title='联合检索词')
        self.ticket(priority='low', status='in-progress', title='联合检索词')
        self.ticket(owner=self.other, priority='high', status='in-progress', title='联合检索词')
        params = {'scope': 'attention', 'q': '联合检索词', 'module': 'MM', 'priority': 'high',
                  'status': 'in-progress', 'owner': self.owner.pk, 'schedule': 'overdue', 'sort': 'estimate_desc'}
        self.assert_counts(params, {'total': 1, 'active': 1, 'unread_tickets': 1})
        self.assert_counts({**params, 'q': '无匹配词'}, {'total': 0, 'active': 0, 'unread_tickets': 0})
        self.assert_counts({**params, 'schedule': 'today'}, {'total': 0, 'active': 0, 'unread_tickets': 0})

    def test_counts_are_full_result_totals_not_current_page_lengths(self):
        for _ in range(25):
            self.ticket(status='in-progress', next_actor='admin')
        self.ticket(status='done', next_actor='none')
        response = self.client.get('/api/tickets', {'scope': 'attention', 'page': 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 5)
        self.assertEqual(response.data['count'], 25)
        self.assertEqual(response.data['tab_counts'], {'total': 25, 'active': 25, 'unread_tickets': 0})

    def test_unread_changes_and_scope_respect_current_user(self):
        waiting = self.ticket(status='waiting', next_actor='consultant')
        self.ticket(owner=self.other, status='waiting', next_actor='consultant')
        self.ticket(status='done', next_actor='none')
        self.unread(waiting, self.admin)
        notice = self.unread(waiting, self.owner)
        owner_client = self.login(self.owner)
        self.assert_counts({'scope': 'attention'}, {'total': 1, 'active': 1, 'unread_tickets': 1}, owner_client)
        # Forging the owner filter cannot reveal another consultant's tickets.
        self.assert_counts({'owner': self.other.pk}, {'total': 2, 'active': 1, 'unread_tickets': 1}, owner_client)
        owner_client.post(f'/api/tickets/{waiting.pk}/read', {'through_event': notice.event_id}, format='json')
        self.assert_counts({'scope': 'attention'}, {'total': 1, 'active': 1, 'unread_tickets': 0}, owner_client)
        self.assert_counts({'scope': 'attention'}, {'total': 1, 'active': 1, 'unread_tickets': 1})
