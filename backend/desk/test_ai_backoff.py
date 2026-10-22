import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from .ai import candidate_page, claim, defer, publish, renew, ticket_ai_state
from .models import TicketAIRun, User
from .services import change_ticket_status, create_ticket


@override_settings(AI_TICKET_AUTOMATION_ENABLED=True, DINGTALK_ENABLED=False)
class TicketAIRetryTimingTests(TestCase):
    def setUp(self):
        self.started = timezone.now().replace(microsecond=0)
        clock_patch = patch('desk.ai.timezone.now', return_value=self.started)
        self.clock = clock_patch.start()
        self.addCleanup(clock_patch.stop)
        admin = User.objects.create_user(username='timing-admin', email='admin@timing.test', role='admin')
        owner = User.objects.create_user(username='timing-owner', email='owner@timing.test', role='consultant')
        ticket = create_ticket(owner, {
            'request_key': uuid.uuid4(), 'title': '重试时间测试', 'module': 'MM',
            'system': 'DS4', 'kind': '问题', 'description': '本地夹具，无真实 SAP 查询。',
        }, [])[0]
        self.ticket = change_ticket_status(admin, ticket.pk, {
            'version': ticket.version, 'action': 'start',
        })
        self.first = self.take()

    def at(self, minutes, seconds=0):
        self.clock.return_value = self.started + timedelta(minutes=minutes, seconds=seconds)

    def take(self):
        result = claim(self.ticket.pk, self.ticket.version)
        self.assertEqual(result['outcome'], 'claimed')
        self.assertEqual(result['deadline_at'], (self.started + timedelta(minutes=45)).isoformat())
        return result

    def block(self, lease, reason='sap_unavailable'):
        return defer(lease['run_id'], lease['lease_token'], reason)

    def test_three_attempts_fit_real_clock_and_stop_after_third_failure(self):
        self.at(1)
        self.assertFalse(self.block(self.first)['manual_required'])
        self.at(5, 59)
        self.assertEqual(candidate_page()['results'], [])
        self.assertEqual(claim(self.ticket.pk, self.ticket.version)['reason'], 'retry_wait')
        self.at(6)
        self.assertEqual([item['id'] for item in candidate_page()['results']], [self.ticket.pk])
        second = self.take()
        self.assertNotEqual(second['lease_token'], self.first['lease_token'])
        self.at(7)
        self.assertFalse(self.block(second)['manual_required'])
        self.at(16, 59)
        self.assertEqual(candidate_page()['results'], [])
        self.at(17)
        third = self.take()
        self.at(18)
        self.assertTrue(self.block(third)['manual_required'])
        run = TicketAIRun.objects.get(pk=third['run_id'])
        self.assertEqual((run.attempts, run.reason, run.retry_after), (3, 'sap_unavailable', None))
        self.assertEqual(ticket_ai_state(self.ticket)['state'], 'manual_required')
        self.assertEqual(candidate_page()['results'], [])
        self.assertEqual(claim(self.ticket.pk, self.ticket.version)['reason'], 'manual_required')

    def test_long_attempt_can_retry_and_late_backoff_leaves_time_to_reply(self):
        self.at(14)
        self.assertEqual(renew(self.first['run_id'], self.first['lease_token'])['outcome'], 'renewed')
        self.at(25)
        self.assertFalse(self.block(self.first)['manual_required'])
        self.at(30)
        second = self.take()
        self.at(37)
        self.assertFalse(self.block(second)['manual_required'])
        run = TicketAIRun.objects.get(pk=second['run_id'])
        self.assertEqual(run.retry_after, self.started + timedelta(minutes=39))
        self.at(38, 59)
        self.assertEqual(candidate_page()['results'], [])
        # The one-minute scanner may arrive after retry_after. Five minutes remain.
        self.at(40)
        third = self.take()
        self.at(41)
        result = publish(third['run_id'], third['lease_token'], {
            'category': 'clarification', 'body': '需要补充信息：请提供单号。', 'evidence': [],
        })
        self.assertEqual(result['outcome'], 'replied')
        self.assertEqual(self.ticket.events.filter(is_ai=True).count(), 1)

    def test_insufficient_retry_budget_immediately_requires_manual_handling(self):
        self.at(14)
        renew(self.first['run_id'], self.first['lease_token'])
        self.at(28)
        renew(self.first['run_id'], self.first['lease_token'])
        self.at(38, 1)
        result = self.block(self.first)
        self.assertEqual(result, {'outcome': 'blocked', 'reason': 'time_limit', 'manual_required': True})
        run = TicketAIRun.objects.get(pk=self.first['run_id'])
        self.assertIsNone(run.retry_after)
        self.assertEqual(run.deadline_at, self.started + timedelta(minutes=45))
        self.assertEqual(ticket_ai_state(self.ticket)['state'], 'manual_required')
        self.assertIn('剩余时间不足', ticket_ai_state(self.ticket)['message'])
        self.assertEqual(candidate_page()['results'], [])
        self.assertEqual(claim(self.ticket.pk, self.ticket.version)['reason'], 'manual_required')

    def test_minimum_wait_boundary_still_allows_one_retry(self):
        self.at(14)
        renew(self.first['run_id'], self.first['lease_token'])
        self.at(28)
        renew(self.first['run_id'], self.first['lease_token'])
        self.at(38)
        self.assertFalse(self.block(self.first)['manual_required'])
        run = TicketAIRun.objects.get(pk=self.first['run_id'])
        self.assertEqual(run.retry_after, self.started + timedelta(minutes=39))
        self.at(39)
        self.take()

    def test_explicit_time_limit_never_schedules_retry(self):
        self.at(1)
        self.assertTrue(self.block(self.first, 'time_limit')['manual_required'])
        self.assertIsNone(TicketAIRun.objects.get(pk=self.first['run_id']).retry_after)
        self.at(6)
        self.assertEqual(claim(self.ticket.pk, self.ticket.version)['reason'], 'manual_required')

    def test_stale_worker_cannot_reschedule_the_current_attempt(self):
        self.at(1)
        self.block(self.first)
        self.at(6)
        current = self.take()
        before = TicketAIRun.objects.filter(pk=current['run_id']).values().get()
        self.assertEqual(self.block(self.first), {'outcome': 'skipped', 'reason': 'run_changed'})
        self.assertEqual(TicketAIRun.objects.filter(pk=current['run_id']).values().get(), before)

    def test_retry_never_extends_deadline_or_allows_a_late_reply(self):
        self.at(1)
        self.block(self.first)
        self.at(6)
        second = self.take()
        self.at(20)
        renew(second['run_id'], second['lease_token'])
        self.at(34)
        renew(second['run_id'], second['lease_token'])
        self.at(45)
        self.assertEqual(candidate_page()['results'], [])
        self.assertEqual(claim(self.ticket.pk, self.ticket.version)['reason'], 'manual_required')
        result = publish(second['run_id'], second['lease_token'], {
            'category': 'clarification', 'body': '需要补充信息：请提供单号。', 'evidence': [],
        })
        self.assertEqual(result['reason'], 'time_limit')
        self.assertFalse(self.ticket.events.filter(is_ai=True).exists())
