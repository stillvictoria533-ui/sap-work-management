"""Monitor aggregates and trusted heartbeats without exposing ticket content."""
import uuid
import io
import json
import tempfile
from datetime import timedelta
from pathlib import Path

from django.test import TestCase, SimpleTestCase, override_settings
from django.utils import timezone
from django.core.management import call_command
from rest_framework.test import APIClient

from . import ai
from .ai_gateway import dispatch
from .ai_monitor import monitor_summary, record_executor_heartbeat
from .backup_status import backup_summary
from .models import Audit, Event, TicketAIExecutorState, TicketAIRun, User
from .services import create_ticket, change_ticket_status, reply_ticket


READY = {'model_cli': True, 'sap_tools': True, 'production_identity': True, 'proxy': True}


@override_settings(AI_TICKET_AUTOMATION_ENABLED=True, AI_HEARTBEAT_STALE_SECONDS=300,
                   AI_BACKLOG_ALERT_SECONDS=600, AI_FAILURE_ALERT_COUNT=3,
                   PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class AIMonitorTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='monitor-admin', email='admin@monitor.test',
                                              name='管理员', role='admin', must_change_password=False)
        self.owner = User.objects.create_user(username='monitor-owner', email='owner@monitor.test',
                                              name='顾问', must_change_password=False)

    def login(self, user):
        client = APIClient(); client.force_login(user)
        session = client.session; session['version'] = user.session_version; session.save()
        return client

    def ticket(self):
        ticket = create_ticket(self.owner, {'request_key': uuid.uuid4(), 'title': 'SENSITIVE_TICKET',
            'system': 'DS4', 'module': 'MM', 'kind': '问题', 'description': 'PRIVATE_CONTENT'}, [])[0]
        return change_ticket_status(self.admin, ticket.pk, {'action': 'start', 'version': 1})

    def test_only_admin_can_read_aggregate_health(self):
        self.ticket()
        record_executor_heartbeat(READY)
        response = self.login(self.admin).get('/api/ai-monitor')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['node']['status'], 'online')
        self.assertEqual(response.data['queue']['queued'], 1)
        self.assertIn('global_bytes', response.data['storage']['limits'])
        self.assertEqual(response.data['backup']['status'], 'unknown')
        self.assertNotIn('PRIVATE_CONTENT', str(response.data))
        self.assertNotIn('SENSITIVE_TICKET', str(response.data))
        self.assertEqual(self.login(self.owner).get('/api/ai-monitor').status_code, 403)
        self.assertEqual(APIClient().get('/api/ai-monitor').status_code, 403)

    def test_management_health_contains_only_operational_aggregates(self):
        self.ticket(); record_executor_heartbeat(READY)
        output = io.StringIO(); call_command('ai_tickets', 'health', stdout=output)
        data = json.loads(output.getvalue())
        self.assertEqual(data['queue']['queued'], 1)
        self.assertNotIn('PRIVATE_CONTENT', output.getvalue())

    def test_stale_node_and_degraded_capabilities_are_distinct(self):
        self.assertFalse(monitor_summary()['alerts']['offline'])
        self.assertEqual(monitor_summary()['node']['status'], 'awaiting')
        TicketAIExecutorState.objects.update(created_at=timezone.now()-timedelta(seconds=301))
        self.assertTrue(monitor_summary()['alerts']['offline'])
        record_executor_heartbeat({**READY, 'proxy': False})
        health = monitor_summary()
        self.assertEqual(health['node']['status'], 'online')
        self.assertTrue(health['alerts']['readiness'])
        TicketAIExecutorState.objects.update(last_seen_at=timezone.now()-timedelta(seconds=301))
        self.assertTrue(monitor_summary()['alerts']['offline'])
        with override_settings(AI_TICKET_AUTOMATION_ENABLED=False):
            self.assertFalse(monitor_summary()['alerts']['offline'])
            self.assertEqual(monitor_summary()['node']['status'], 'disabled')

    def test_only_known_boolean_capabilities_can_update_the_heartbeat(self):
        record_executor_heartbeat(READY)
        for invalid in [{**READY, 'token': 'secret'}, {**READY, 'proxy': 'true'}, {'proxy': True}]:
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                record_executor_heartbeat(invalid)
        self.assertEqual(TicketAIExecutorState.objects.get(pk='primary').capabilities, READY)

    def test_old_queue_and_consecutive_failure_alerts_clear_on_success(self):
        ticket = self.ticket()
        run = TicketAIRun.objects.get(ticket=ticket)
        Event.objects.filter(pk=run.trigger_id).update(created_at=timezone.now()-timedelta(seconds=720))
        record_executor_heartbeat(READY)
        for reason in ['sap_unavailable', 'sap_unavailable', 'runtime_error']:
            Audit.objects.create(actor=self.admin, action='ai_ticket_blocked', target=str(ticket.pk), detail={'reason': reason})
        health = monitor_summary()
        self.assertGreaterEqual(health['queue']['oldest_wait_seconds'], 720)
        self.assertTrue(health['alerts']['backlog'])
        self.assertEqual(health['failures']['consecutive'], 3)
        self.assertEqual(health['failures']['by_reason']['sap_unavailable'], 2)
        self.assertTrue(health['alerts']['failure_streak'])
        Audit.objects.create(actor=self.admin, action='ai_ticket_reply', target=str(ticket.pk), detail={})
        self.assertEqual(monitor_summary()['failures']['consecutive'], 0)

    def test_worker_renewal_keeps_a_long_running_node_online(self):
        ticket = self.ticket()
        record_executor_heartbeat(READY)
        old = timezone.now()-timedelta(minutes=8)
        TicketAIExecutorState.objects.update(last_seen_at=old, last_scan_at=old)
        lease = ai.claim(ticket.pk, ticket.version)
        result = dispatch({'operation': 'renew', 'run_id': lease['run_id'], 'lease_token': lease['lease_token']})
        self.assertEqual(result['outcome'], 'renewed')
        status = monitor_summary()
        self.assertEqual(status['node']['status'], 'online')
        self.assertEqual(status['node']['last_scan_at'], old)

    def test_clarification_waiting_count_clears_after_owner_followup(self):
        ticket = self.ticket(); lease = ai.claim(ticket.pk, ticket.version)
        ai.publish(lease['run_id'], lease['lease_token'], {'category': 'clarification', 'body': '请补充物料号。', 'evidence': []})
        self.assertEqual(monitor_summary()['queue']['waiting_for_information'], 1)
        ticket.refresh_from_db()
        reply_ticket(self.owner, ticket.pk, {'version': ticket.version, 'body': '物料编号 12345'}, [])
        self.assertEqual(monitor_summary()['queue']['waiting_for_information'], 0)
        self.assertEqual(monitor_summary()['queue']['queued'], 1)

    def test_retry_backlog_and_expired_manual_states_are_reported_without_mutation(self):
        ticket = self.ticket(); run = TicketAIRun.objects.get(ticket=ticket)
        now = timezone.now()
        run.status = 'blocked'; run.attempts = 1; run.retry_after = now - timedelta(seconds=1)
        run.deadline_at = now + timedelta(minutes=20); run.save()
        health = monitor_summary(now)
        self.assertEqual((health['queue']['queued'], health['queue']['retry_wait']), (1, 0))
        run.retry_after = now + timedelta(minutes=1); run.save(update_fields=['retry_after'])
        health = monitor_summary(now)
        self.assertEqual((health['queue']['queued'], health['queue']['retry_wait']), (0, 1))
        run.deadline_at = now - timedelta(seconds=1); run.save(update_fields=['deadline_at'])
        health = monitor_summary(now)
        self.assertEqual((health['queue']['queued'], health['queue']['manual_required']), (0, 1))
        self.assertTrue(health['alerts']['manual'])
        run.refresh_from_db()
        self.assertEqual(run.status, 'blocked')

    def test_inactive_acceptor_and_submitter_are_visible_to_administrators(self):
        ticket = self.ticket()
        self.admin.is_active = False; self.admin.save(update_fields=['is_active'])
        health = monitor_summary()
        self.assertEqual(health['queue']['needs_acceptance'], 1)
        self.assertTrue(health['alerts']['manual'])
        self.owner.is_active = False; self.owner.save(update_fields=['is_active'])
        health = monitor_summary()
        self.assertEqual(health['queue']['needs_acceptance'], 0)
        self.assertEqual(health['queue']['inactive_submitters'], 1)
        self.assertTrue(health['alerts']['manual'])


class BackupStatusTests(SimpleTestCase):
    def test_only_fresh_sanitized_backup_metadata_is_exposed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'backup.json'; now = timezone.now()
            payload = {'schema': 'sapdesk-backup-health/v1', 'generated_at': now.timestamp(), 'running': False,
                       'last_success_at': (now-timedelta(hours=2)).timestamp(), 'last_backup_ok': True,
                       'last_drill_at': (now-timedelta(days=1)).timestamp(), 'last_drill_ok': True,
                       'private_path': '/secret/backup', 'password': 'SECRET'}
            path.write_text(json.dumps(payload))
            with override_settings(OPS_BACKUP_HEALTH_PATH=str(path)):
                summary = backup_summary(now)
                self.assertEqual(summary['status'], 'healthy')
                self.assertTrue(summary['last_drill_ok'])
                self.assertNotIn('SECRET', str(summary))
                self.assertNotIn('/secret', str(summary))
                self.assertEqual(backup_summary(now+timedelta(seconds=181))['status'], 'unknown')
                payload['last_backup_ok'] = False; path.write_text(json.dumps(payload))
                self.assertEqual(backup_summary(now)['status'], 'failed')
                payload['last_backup_ok'] = 'false'; path.write_text(json.dumps(payload))
                self.assertEqual(backup_summary(now)['status'], 'unknown')
                payload['last_backup_ok'] = True; payload['generated_at'] = 10**1000
                path.write_text(json.dumps(payload))
                self.assertEqual(backup_summary(now)['status'], 'unknown')

    def test_symlinks_and_oversized_or_invalid_reports_are_unavailable(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); real = root/'real'; real.write_text('{}')
            link = root/'link'; link.symlink_to(real)
            with override_settings(OPS_BACKUP_HEALTH_PATH=str(link)):
                self.assertEqual(backup_summary()['status'], 'unknown')
            real.write_text('x'*4097)
            with override_settings(OPS_BACKUP_HEALTH_PATH=str(real)):
                self.assertEqual(backup_summary()['status'], 'unknown')
