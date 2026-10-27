import csv
import io
import uuid
from datetime import date, datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from .models import User, Ticket, Event, Notification, Delivery, TicketAIRun
from .services import change_ticket_status, change_ticket_schedule
from .ai import claim, publish, candidate_page, defer


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'],
                   DINGTALK_ENABLED=False, AI_TICKET_AUTOMATION_ENABLED=False)
class ScheduleTests(TestCase):
    def setUp(self):
        self.admin = self.user('admin', 'admin')
        self.owner = self.user('owner')
        self.other = self.user('other')
        self.client = self.login(self.admin)
        self.today = timezone.localdate()
        self.future = self.today + timedelta(days=5)
        self.ticket = self.make_ticket()

    def user(self, name, role='consultant'):
        return User.objects.create_user(username=name, email=name+'@schedule.test', name=name,
                                        role=role, must_change_password=False)

    def login(self, user):
        client = APIClient()
        client.force_login(user)
        session = client.session
        session['version'] = user.session_version
        session.save()
        return client

    def make_ticket(self, **values):
        return Ticket.objects.create(**{'owner': self.owner, 'title': '开发排期测试', 'system': 'DS4',
                                       'module': 'MM', 'kind': '开发', 'description': '测试数据', **values})

    def save(self, value, reason='', version=None):
        self.ticket.refresh_from_db()
        return self.client.post(f'/api/tickets/{self.ticket.pk}/schedule', {
            'estimated_completion_date': value.isoformat() if value else None,
            'reason': reason, 'version': version or self.ticket.version,
        }, format='json')

    def action(self, action, client=None, **extra):
        self.ticket.refresh_from_db()
        return (client or self.client).post(f'/api/tickets/{self.ticket.pk}/status', {
            'action': action, 'version': self.ticket.version, 'reason': '测试处理结果', **extra,
        }, format='json')

    def test_set_change_clear_records_history_and_notifies_without_changing_workflow(self):
        result = self.save(self.future)
        self.assertEqual(result.status_code, 200, result.data)
        self.assertEqual((result.data['status'], result.data['next_actor']), ('pending', 'admin'))
        self.assertEqual(result.data['version'], 2)
        first = Event.objects.get()
        self.assertEqual((first.kind, first.previous_estimated_completion_date, first.estimated_completion_date),
                         ('schedule', None, self.future))
        self.assertEqual(Notification.objects.get().recipient, self.owner)
        self.assertEqual(Delivery.objects.get().channel, 'group_consultant')
        changed = self.future + timedelta(days=2)
        self.assertEqual(self.save(changed, '等待接口联调').status_code, 200)
        self.assertEqual(self.save(None, '范围变更，重新评估').status_code, 200)
        self.assertEqual(list(Event.objects.values_list('previous_estimated_completion_date', 'estimated_completion_date')),
                         [(None, self.future), (self.future, changed), (changed, None)])
        self.assertEqual(Notification.objects.count(), 3)
        self.assertEqual(Delivery.objects.count(), 3)
        self.assertIn('范围变更', Event.objects.last().body)
        self.assertIsNone(self.login(self.owner).get(f'/api/tickets/{self.ticket.pk}').data['estimated_completion_date'])

    def test_permissions_and_creation_cannot_inject_estimate(self):
        for user in [self.owner, self.other]:
            client = self.login(user)
            self.assertEqual(client.post(f'/api/tickets/{self.ticket.pk}/schedule', {
                'estimated_completion_date': str(self.future), 'version': 1,
            }, format='json').status_code, 403)
        response = self.login(self.owner).post('/api/tickets', {
            'request_key': str(uuid.uuid4()), 'title': '新需求', 'system': 'DS4', 'module': 'MM',
            'kind': '接口', 'description': '接口需求', 'estimated_completion_date': str(self.future),
        }, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(response.data['estimated_completion_date'])
        self.assertEqual(self.login(self.other).get(f'/api/tickets/{self.ticket.pk}').status_code, 404)

    def test_dates_reasons_conflicts_and_noop_do_not_write(self):
        self.assertEqual(self.save(self.today-timedelta(days=1)).status_code, 400)
        self.assertEqual(self.save(self.future).status_code, 200)
        for value in [None, self.future+timedelta(days=1)]:
            self.assertEqual(self.save(value, '   ').status_code, 400)
        self.assertEqual(self.save(self.future+timedelta(days=1), '计划调整', version=1).status_code, 409)
        self.assertEqual(self.save(self.future).status_code, 200)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.version, 2)
        self.assertEqual(Event.objects.count(), 1)
        for data in [{'version': 2}, {'version': 2, 'estimated_completion_date': '2026-02-30'}]:
            self.assertEqual(self.client.post(f'/api/tickets/{self.ticket.pk}/schedule', data, format='json').status_code, 400)

    def test_start_with_estimate_is_atomic_and_sends_one_notification(self):
        response = self.action('start', estimated_completion_date=str(self.future))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['estimated_completion_date'], str(self.future))
        self.assertEqual(Event.objects.count(), 1)
        item = Event.objects.get()
        self.assertEqual(item.kind, 'status')
        self.assertEqual(item.estimated_completion_date, self.future)
        self.assertIn(str(self.future), item.body)
        self.assertEqual(Delivery.objects.count(), 1)
        self.assertEqual(self.action('submit', estimated_completion_date=str(self.future)).status_code, 400)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, 'in-progress')

    def test_invalid_start_date_rolls_back_acceptance(self):
        response = self.action('start', estimated_completion_date=str(self.today-timedelta(days=1)))
        self.assertEqual(response.status_code, 400)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.status, 'pending')
        self.assertEqual(Event.objects.count(), 0)
        self.assertEqual(Delivery.objects.count(), 0)

    def test_waiting_done_and_reopened_estimates(self):
        self.action('start', estimated_completion_date=str(self.future))
        self.assertEqual(self.action('submit').status_code, 200)
        self.assertEqual(self.action('reject', self.login(self.owner)).status_code, 200)
        self.ticket.refresh_from_db()
        self.assertTrue(self.ticket.estimate_needs_review)
        self.assertEqual(self.ticket.estimated_completion_date, self.future)
        self.assertEqual(self.save(self.future).status_code, 400)
        self.assertEqual(self.save(self.future, '评估后仍可按原日期完成').status_code, 200)
        self.ticket.refresh_from_db()
        self.assertFalse(self.ticket.estimate_needs_review)
        self.action('complete')
        self.assertEqual(self.save(None, '试图清空').status_code, 400)
        self.action('reopen', self.login(self.owner))
        self.ticket.refresh_from_db()
        self.assertTrue(self.ticket.estimate_needs_review)
        self.assertEqual(self.ticket.estimated_completion_date, self.future)

    def test_filters_sort_null_last_and_owner_scope(self):
        yesterday = self.today-timedelta(days=1)
        overdue = self.make_ticket(status='in-progress', estimated_completion_date=yesterday)
        due = self.make_ticket(status='in-progress', estimated_completion_date=self.today)
        self.make_ticket(status='waiting', estimated_completion_date=yesterday)
        self.make_ticket(status='done', estimated_completion_date=self.today)
        self.make_ticket(owner=self.other, status='in-progress', estimated_completion_date=yesterday)
        client = self.login(self.owner)
        expected = {'overdue': [overdue.pk], 'today': [due.pk], 'unassessed': [self.ticket.pk]}
        for value, ids in expected.items():
            self.assertEqual([t['id'] for t in client.get('/api/tickets', {'schedule': value}).data['results']], ids)
        for direction in ['estimate_asc', 'estimate_desc']:
            rows = client.get('/api/tickets', {'sort': direction}).data['results']
            self.assertEqual(rows[-1]['id'], self.ticket.pk)
            dates = [t['estimated_completion_date'] for t in rows[:-1]]
            self.assertEqual(dates, sorted(dates, reverse=direction=='estimate_desc'))
        for params in [{'sort': 'oops'}, {'schedule': 'oops'}]:
            self.assertEqual(client.get('/api/tickets', params).status_code, 400)

    def test_beijing_date_boundary_is_used_for_filter_and_validation(self):
        instant = datetime(2026, 10, 21, 16, 1, tzinfo=dt_timezone.utc)
        self.ticket.status = 'in-progress'
        self.ticket.estimated_completion_date = date(2026, 10, 21)
        self.ticket.save()
        with patch('django.utils.timezone.now', return_value=instant), timezone.override('UTC'):
            self.assertEqual(self.client.get('/api/tickets', {'schedule': 'overdue'}).data['count'], 1)
            another = self.make_ticket()
            response = self.client.post(f'/api/tickets/{another.pk}/schedule', {
                'estimated_completion_date': '2026-10-21', 'version': 1,
            }, format='json')
            self.assertEqual(response.status_code, 400)

    def test_calendar_modes_month_boundaries_pagination_and_unassessed(self):
        self.ticket.created_at = datetime(2026, 9, 30, 16, 0, tzinfo=dt_timezone.utc)
        self.ticket.estimated_completion_date = date(2026, 11, 1)
        self.ticket.save()
        self.make_ticket(status='done', estimated_completion_date=date(2026, 11, 1))
        self.make_ticket(estimated_completion_date=date(2026, 12, 1))
        self.make_ticket()  # global, unassessed
        submitted = self.client.get('/api/calendar', {'month': '2026-10', 'date': '2026-10-01'})
        self.assertEqual(submitted.data['mode'], 'submitted')
        self.assertEqual([t['id'] for t in submitted.data['results']], [self.ticket.pk])
        for _ in range(21):
            self.make_ticket(estimated_completion_date=date(2026, 11, 1))
        estimated = self.client.get('/api/calendar', {'mode': 'estimated', 'month': '2026-11', 'date': '2026-11-01'})
        self.assertEqual(estimated.data['mode'], 'estimated')
        self.assertEqual(estimated.data['month_count'], 23)
        self.assertEqual(estimated.data['count'], 23)
        self.assertEqual(estimated.data['days'], [{'date': '2026-11-01', 'count': 23, 'urgent_count': 0}])
        self.assertEqual(estimated.data['unassessed_count'], 1)
        self.assertEqual(len(estimated.data['results']), 20)
        second = self.client.get('/api/calendar', {'mode': 'estimated', 'month': '2026-11', 'date': '2026-11-01', 'page': 2})
        self.assertEqual(len(second.data['results']), 3)
        self.assertEqual(self.client.get('/api/calendar', {'mode': 'wrong'}).status_code, 400)
        self.assertEqual(self.login(self.owner).get('/api/calendar', {'mode': 'estimated'}).status_code, 200)

    def test_consultant_calendar_reflects_admin_schedule_changes_and_remains_read_only(self):
        consultant = self.login(self.owner)
        def on_day(day):
            response = consultant.get('/api/calendar', {'mode': 'estimated', 'month': day.strftime('%Y-%m'), 'date': day.isoformat()})
            self.assertEqual(response.status_code, 200)
            return response.data
        self.assertEqual(self.save(self.future).status_code, 200)
        initial = on_day(self.future)
        self.assertEqual([row['id'] for row in initial['results']], [self.ticket.pk])
        self.assertEqual(initial['results'][0]['estimated_completion_date'], self.future.isoformat())
        later = self.future + timedelta(days=3)
        self.assertEqual(self.save(later, '管理员调整计划').status_code, 200)
        self.assertEqual(on_day(self.future)['count'], 0)
        self.assertEqual(on_day(later)['results'][0]['estimated_completion_date'], later.isoformat())
        self.ticket.refresh_from_db()
        self.assertEqual(consultant.post(f'/api/tickets/{self.ticket.pk}/schedule', {
            'version': self.ticket.version, 'estimated_completion_date': self.future.isoformat(),
        }, format='json').status_code, 403)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.estimated_completion_date, later)
        self.assertEqual(self.save(None, '重新评估').status_code, 200)
        cleared = on_day(later)
        self.assertEqual(cleared['count'], 0)
        self.assertEqual(cleared['unassessed_count'], 1)

    def test_csv_uses_same_filter_and_keeps_both_dates_and_schedule_history(self):
        self.ticket.due_date = self.future-timedelta(days=1)
        self.ticket.save()
        self.save(self.future, '评估结果')
        response = self.client.get('/api/tickets/export', {'mode': 'tickets', 'sort': 'estimate_asc'})
        rows = list(csv.DictReader(io.StringIO(response.content.decode('utf-8-sig'))))
        self.assertEqual(rows[0]['期望完成日期'], str(self.ticket.due_date))
        self.assertEqual(rows[0]['开发预计完成日期'], str(self.future))
        filtered = self.client.get('/api/tickets/export', {'mode': 'tickets', 'schedule': 'overdue'})
        self.assertEqual(len(list(csv.DictReader(io.StringIO(filtered.content.decode('utf-8-sig'))))), 0)
        history = self.client.get('/api/tickets/export', {'mode': 'events'})
        self.assertIn('开发排期', history.content.decode('utf-8-sig'))
        self.assertIn(str(self.future), history.content.decode('utf-8-sig'))

    @override_settings(AI_TICKET_AUTOMATION_ENABLED=True)
    def test_schedule_requeues_existing_ai_lease_without_new_authorization_or_budget(self):
        self.ticket.kind = '问题'
        self.ticket.save()
        self.action('start')
        self.ticket.refresh_from_db()
        lease = claim(self.ticket.pk, self.ticket.version)
        self.assertEqual(lease['outcome'], 'claimed')
        run = TicketAIRun.objects.get()
        deadline, accepted, trigger, attempts = run.deadline_at, run.accepted_event_id, run.trigger_id, run.attempts
        self.save(self.future)
        result = publish(lease['run_id'], lease['lease_token'], {'category': 'clarification', 'body': '补充单据号', 'evidence': []})
        self.assertEqual(result['reason'], 'lease_changed')
        run.refresh_from_db()
        self.assertEqual((run.deadline_at, run.accepted_event_id, run.trigger_id, run.attempts),
                         (deadline, accepted, trigger, attempts))
        self.assertEqual(candidate_page()['results'][0]['id'], self.ticket.pk)
        self.ticket.refresh_from_db()
        retry = claim(self.ticket.pk, self.ticket.version)
        self.assertEqual(retry['outcome'], 'claimed')
        result = publish(retry['run_id'], retry['lease_token'], {'category': 'clarification', 'body': '补充单据号', 'evidence': []})
        self.assertEqual(result['outcome'], 'replied')
        self.save(self.future+timedelta(days=1), '新排期')
        self.assertEqual(candidate_page()['results'], [])
        self.assertEqual(TicketAIRun.objects.count(), 1)

    @override_settings(AI_TICKET_AUTOMATION_ENABLED=True)
    def test_schedule_preserves_ai_blocked_retry_limit_and_deadline(self):
        self.ticket.kind = '问题'
        self.ticket.save()
        self.action('start')
        self.ticket.refresh_from_db()
        lease = claim(self.ticket.pk, self.ticket.version)
        defer(lease['run_id'], lease['lease_token'], 'runtime_error')
        run = TicketAIRun.objects.get()
        run.attempts = 3
        run.save()
        deadline = run.deadline_at
        self.save(self.future)
        run.refresh_from_db()
        self.assertEqual((run.status, run.attempts, run.deadline_at), ('blocked', 3, deadline))
        self.assertEqual(candidate_page()['results'], [])
