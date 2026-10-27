from collections import Counter
from datetime import date, datetime
from urllib.parse import urlencode, urlsplit
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from .models import Ticket, User


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
                   DINGTALK_ENABLED=False, AI_TICKET_AUTOMATION_ENABLED=False)
class CalendarIsolationTests(TestCase):
    """Exercise real session authentication and adversarial calendar query strings."""

    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(username='cal-admin', role='admin', must_change_password=False)
        cls.alice = User.objects.create_user(username='cal-alice', email='alice@cal-isolation.test', must_change_password=False)
        cls.bob = User.objects.create_user(username='cal-bob', email='bob@cal-isolation.test', must_change_password=False)
        cls.empty = User.objects.create_user(username='cal-empty', email='empty@cal-isolation.test', must_change_password=False)
        rows = []

        def add(owner, created, planned=None, **extra):
            rows.append(Ticket(owner=owner, title=f'owner-{owner.pk}-private-{len(rows)}', module='MM', kind='开发',
                               description='Private fixture body', created_at=datetime.fromisoformat(created),
                               estimated_completion_date=date.fromisoformat(planned) if planned else None, **extra))

        for owner, count in [(cls.alice, 41), (cls.bob, 43)]:
            for _ in range(count):
                add(owner, '2026-10-12T03:00:00+00:00', '2026-11-10', priority='urgent' if owner == cls.bob else 'medium')
        for created, planned in [
            ('2026-09-30T15:59:59+00:00', '2026-09-30'),
            ('2026-09-30T16:00:00+00:00', '2026-10-01'),
            ('2026-10-31T15:59:59+00:00', '2026-10-31'),
            ('2026-10-31T16:00:00+00:00', '2026-11-01'),
        ]:
            add(cls.alice, created, planned, status='done')
        add(cls.alice, '2026-09-25T03:00:00+00:00')
        add(cls.alice, '2026-12-10T03:00:00+00:00', status='in-progress')
        add(cls.alice, '2026-12-11T03:00:00+00:00', status='done')
        add(cls.bob, '2026-10-22T03:00:00+00:00', '2026-11-22', priority='urgent')
        add(cls.bob, '2026-08-08T03:00:00+00:00', '2027-01-08')
        add(cls.bob, '2026-12-20T03:00:00+00:00')
        cls.rows = Ticket.objects.bulk_create(rows)

    def setUp(self):
        self.client = self.login(self.alice)

    def login(self, user):
        client = APIClient()
        client.force_login(user)
        session = client.session
        session['version'] = user.session_version
        session.save()
        return client

    def assert_calendar(self, response, user, mode, month, selected, page=1):
        self.assertEqual(response.status_code, 200, response.data)
        owned = [row for row in self.rows if user.role == 'admin' or row.owner_id == user.pk]

        def day(row):
            return row.estimated_completion_date if mode == 'estimated' else row.created_at.astimezone(ZoneInfo('Asia/Shanghai')).date()

        monthly = [row for row in owned if day(row) and day(row).isoformat().startswith(month + '-')]
        totals = Counter(day(row) for row in monthly)
        urgent = Counter(day(row) for row in monthly if row.priority == 'urgent')
        daily = sorted([row for row in monthly if day(row).isoformat() == selected], key=lambda row: (row.created_at, row.pk))
        number = max(1, (len(daily) + 19) // 20) if page == 'last' else page
        data = response.data
        self.assertEqual((data['mode'], data['month'], data['date']), (mode, month, selected))
        self.assertEqual(data['month_count'], len(monthly))
        self.assertEqual(data['count'], len(daily))
        self.assertEqual(data['days'], [{'date': str(value), 'count': count, 'urgent_count': urgent[value]}
                                        for value, count in sorted(totals.items())])
        self.assertEqual(data['unassessed_count'], sum(row.status in ['pending', 'in-progress'] and row.estimated_completion_date is None for row in owned))
        expected = daily[(number - 1) * 20:number * 20]
        self.assertEqual([row['id'] for row in data['results']], [row.pk for row in expected])
        for actual, row in zip(data['results'], expected):
            self.assertEqual(actual['owner_id'], row.owner_id)
            self.assertEqual(actual['estimated_completion_date'], str(row.estimated_completion_date) if row.estimated_completion_date else None)
            self.assertNotIn('description', actual)

    def test_changing_month_and_date_keeps_every_summary_and_row_scoped(self):
        for mode in ['submitted', 'estimated']:
            for month in ['2026-08', '2026-09', '2026-10', '2026-11', '2026-12', '2027-01']:
                for day in ['01', '08', '10', '12', '22', '25', '30']:
                    selected = month + '-' + day
                    for user in [self.alice, self.empty]:
                        with self.subTest(mode=mode, month=month, date=selected, user=user.pk):
                            response = self.login(user).get('/api/calendar', {'mode': mode, 'month': month, 'date': selected})
                            self.assert_calendar(response, user, mode, month, selected)

    def test_each_page_and_following_next_links_stay_inside_the_same_owner(self):
        for mode, selected in [('submitted', '2026-10-12'), ('estimated', '2026-11-10')]:
            params = {'mode': mode, 'month': selected[:7], 'date': selected, 'owner': self.bob.pk, 'role': 'admin'}
            url = '/api/calendar?' + urlencode(params)
            for page in [1, 2, 3]:
                response = self.client.get(url)
                self.assert_calendar(response, self.alice, mode, selected[:7], selected, page)
                next_url = response.data['next']
                if page < 3:
                    self.assertIsNotNone(next_url)
                    parsed = urlsplit(next_url)
                    url = parsed.path + '?' + parsed.query
                else:
                    self.assertIsNone(next_url)
            last = self.client.get('/api/calendar', {**params, 'page': 'last'})
            self.assert_calendar(last, self.alice, mode, selected[:7], selected, 'last')
            self.assertEqual(len(last.data['results']), 1)
            # Other owners make enough rows for page 4 globally, but Alice has only three pages.
            fourth = self.client.get('/api/calendar', {**params, 'page': 4})
            self.assertEqual(fourth.status_code, 404)
            self.assertNotIn('results', fourth.data)

    def test_owner_role_and_page_size_query_parameters_cannot_expand_access(self):
        vectors = [
            {'owner': self.bob.pk}, {'owner_id': self.bob.pk}, {'user_id': self.bob.pk},
            {'owner__id': self.bob.pk}, {'role': 'admin'}, {'is_admin': 'true'},
            {'is_staff': 'true', 'is_superuser': 'true'}, {'scope': 'all'},
            {'page_size': 1000, 'limit': 1000, 'offset': 20},
            {'owner': self.bob.pk, 'owner_id': self.admin.pk, 'role': 'admin', 'scope': 'all', 'page_size': 1000},
        ]
        for mode, selected in [('submitted', '2026-10-12'), ('estimated', '2026-11-10')]:
            for vector in vectors:
                for page in [1, 2, 3]:
                    with self.subTest(mode=mode, vector=vector, page=page):
                        params = {'mode': mode, 'month': selected[:7], 'date': selected, 'page': page, **vector}
                        response = self.client.get('/api/calendar', params)
                        self.assert_calendar(response, self.alice, mode, selected[:7], selected, page)

    def test_duplicate_url_parameters_do_not_change_the_authenticated_owner(self):
        for mode, selected in [('submitted', '2026-10-12'), ('estimated', '2026-11-10')]:
            params = [('month', '2026-08'), ('month', selected[:7]), ('date', selected), ('mode', mode),
                      ('page', 1), ('page', 2), ('owner', self.alice.pk), ('owner', self.bob.pk),
                      ('role', 'consultant'), ('role', 'admin'), ('scope', 'all')]
            response = self.client.get('/api/calendar?' + urlencode(params))
            self.assert_calendar(response, self.alice, mode, selected[:7], selected, 2)

    @patch('desk.views.timezone.localdate', return_value=date(2026, 12, 15))
    def test_default_selected_date_never_comes_from_another_owners_ticket(self, _today):
        for mode, month, expected in [('submitted', '2026-08', '2026-08-01'),
                                      ('estimated', '2027-01', '2027-01-01'),
                                      ('submitted', '2026-10', '2026-10-01'),
                                      ('estimated', '2026-11', '2026-11-01')]:
            response = self.client.get('/api/calendar', {'mode': mode, 'month': month, 'owner': self.bob.pk})
            self.assert_calendar(response, self.alice, mode, month, expected)

    def test_invalid_urls_and_direct_other_ticket_links_do_not_return_private_data(self):
        for patch_params, expected in [
            ({'month': '2026-13'}, 400), ({'date': '2026-11-01'}, 400),
            ({'date': '2026-10-32'}, 400), ({'mode': 'admin'}, 400),
            ({'page': 0}, 404), ({'page': -1}, 404), ({'page': 'invalid'}, 404), ({'page': 99999}, 404),
        ]:
            response = self.client.get('/api/calendar', {'month': '2026-10', 'date': '2026-10-12',
                                                       'owner': self.bob.pk, 'role': 'admin', **patch_params})
            self.assertEqual(response.status_code, expected)
            self.assertNotIn('results', response.data)
        other = next(row for row in self.rows if row.owner_id == self.bob.pk)
        response = self.client.get(f'/api/tickets/{other.pk}', {'from': 'calendar', 'owner': self.bob.pk, 'role': 'admin'})
        self.assertEqual(response.status_code, 404)

    def test_administrator_control_still_sees_all_owners(self):
        for mode, selected in [('submitted', '2026-10-12'), ('estimated', '2026-11-10')]:
            response = self.login(self.admin).get('/api/calendar', {'mode': mode, 'month': selected[:7], 'date': selected})
            self.assert_calendar(response, self.admin, mode, selected[:7], selected)
            self.assertEqual(response.data['count'], 84)
