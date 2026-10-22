import json
import uuid
from datetime import timedelta
from io import StringIO
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from .ai import candidate_page, claim, publish, request_retry, renew
from .models import Audit, Delivery, Notification, TicketAIRun
from .services import create_ticket, reply_ticket, change_ticket_status
from .models import User


@override_settings(AI_TICKET_AUTOMATION_ENABLED=True, DINGTALK_ENABLED=False,
    PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ExplicitRetryTests(TestCase):
    def setUp(self):
        self.admin=User.objects.create_user(username='retry-admin',email='admin@retry.test',name='admin',role='admin')
        self.owner=User.objects.create_user(username='retry-owner',email='owner@retry.test',name='owner',role='consultant')
        self.ticket=create_ticket(self.owner,{'request_key':uuid.uuid4(),'title':'本地测试',
            'system':'PS4','kind':'问题','module':'MM','description':'本地夹具，禁止真实 SAP 查询'},[])[0]
        self.ticket=change_ticket_status(self.admin,self.ticket.pk,{'version':self.ticket.version,'action':'start'})
        first=claim(self.ticket.pk,self.ticket.version)
        publish(first['run_id'],first['lease_token'],{'category':'clarification','body':'需要补充信息：本地夹具','evidence':[]})
        self.ticket.refresh_from_db()
        self.old_run=TicketAIRun.objects.get(pk=first['run_id'])

    def request(self):
        self.ticket.refresh_from_db()
        return request_retry(self.ticket.pk,self.ticket.version)

    def take(self):
        self.ticket.refresh_from_db()
        return claim(self.ticket.pk,self.ticket.version)

    def reply(self,lease):
        return publish(lease['run_id'],lease['lease_token'],{'category':'query','body':'本地测试回答',
            'evidence':[{'system':'PS4','client':'100','reference':'仅本地夹具，无真实 SAP 数据'}]})

    def add(self,actor):
        self.ticket.refresh_from_db()
        reply_ticket(actor,self.ticket.pk,{'version':self.ticket.version,'body':'新消息'},[])

    def test_retry_preserves_history_and_finishes_once_through_normal_gateway(self):
        before=TicketAIRun.objects.filter(pk=self.old_run.pk).values().get()
        event_ids=list(self.ticket.events.values_list('pk',flat=True))
        counts=(Notification.objects.count(),Delivery.objects.count())
        request=self.request();self.assertEqual(request['outcome'],'queued')
        self.assertEqual(list(self.ticket.events.values_list('pk',flat=True)),event_ids)
        self.assertEqual((Notification.objects.count(),Delivery.objects.count()),counts)
        self.assertEqual(TicketAIRun.objects.filter(pk=self.old_run.pk).values().get(),before)
        self.assertEqual(candidate_page()['results'][0]['trigger_event_id'],self.old_run.reply_event_id)
        lease=self.take();self.assertTrue(lease['manual_retry'])
        self.assertEqual(lease['acceptance_note'],'开始处理')
        self.assertEqual(renew(lease['run_id'],lease['lease_token'])['outcome'],'renewed')
        self.assertEqual(self.reply(lease)['outcome'],'replied')
        self.assertEqual(self.reply(lease)['outcome'],'already_replied')
        self.assertEqual(candidate_page()['results'],[])
        self.assertEqual(self.ticket.events.filter(is_ai=True).count(),2)
        self.assertEqual(Audit.objects.filter(action='ai_ticket_retry_requested').count(),1)
        self.assertEqual(Notification.objects.count(),counts[0]+1)
        self.assertEqual(Delivery.objects.count(),counts[1]+1)

    def test_repeated_request_does_not_reset_deadline_attempts_or_lease(self):
        queued=self.request();lease=self.take()
        run=TicketAIRun.objects.get(pk=queued['run_id'])
        run.status='blocked';run.reason='time_limit';run.deadline_at=timezone.now()-timedelta(seconds=1);run.attempts=3;run.save()
        before=TicketAIRun.objects.filter(pk=run.pk).values().get()
        self.assertEqual(self.request()['outcome'],'already_requested')
        self.assertEqual(TicketAIRun.objects.filter(pk=run.pk).values().get(),before)
        self.assertEqual(self.take()['reason'],'manual_required')

    def test_stale_version_cannot_request_or_publish(self):
        self.assertEqual(request_retry(self.ticket.pk,self.ticket.version-1)['reason'],'version_changed')
        self.request();lease=self.take();self.add(self.owner)
        self.assertEqual(self.reply(lease)['outcome'],'skipped')
        self.assertFalse(self.take()['manual_retry'])

    def test_human_answer_preempts_retry(self):
        self.request();lease=self.take();self.add(self.admin)
        self.assertEqual(self.reply(lease)['outcome'],'skipped')
        self.assertEqual(candidate_page()['results'],[])
        self.assertEqual(self.request()['reason'],'no_ai_answer')

    def test_no_self_trigger_without_explicit_audit(self):
        TicketAIRun.objects.create(ticket=self.ticket,trigger=self.old_run.reply_event,
            accepted_event=self.old_run.accepted_event,context_version=self.ticket.version,status='queued')
        self.assertEqual(candidate_page()['results'],[])
        self.assertEqual(self.take()['reason'],'answered')

    def test_pending_consultant_followup_is_not_replaced(self):
        self.add(self.owner)
        self.assertEqual(self.request()['reason'],'already_actionable')
        self.assertFalse(self.take()['manual_retry'])

    def test_status_change_and_disabled_gate_preserved(self):
        self.request();lease=self.take()
        with override_settings(AI_TICKET_AUTOMATION_ENABLED=False):
            self.assertEqual(self.request()['reason'],'disabled')
            self.assertEqual(self.reply(lease)['reason'],'disabled')
        self.ticket.refresh_from_db()
        change_ticket_status(self.admin,self.ticket.pk,{'version':self.ticket.version,'action':'complete','reason':'本地测试'})
        self.assertEqual(self.request()['reason'],'not_in_progress')
        self.assertEqual(self.reply(lease)['outcome'],'skipped')

    def test_inactive_acceptor_and_non_problem_ticket_cannot_retry(self):
        self.admin.is_active=False;self.admin.save()
        self.assertEqual(self.request()['reason'],'not_accepted')
        self.admin.is_active=True;self.admin.save()
        self.ticket.kind='开发';self.ticket.save()
        self.assertEqual(self.request()['reason'],'unsupported_kind')

    def test_old_token_and_expired_lease_cannot_publish(self):
        self.request();lease=self.take()
        self.assertEqual(publish(lease['run_id'],uuid.uuid4(),{'category':'clarification','body':'本地测试','evidence':[]})['reason'],'lease_changed')
        TicketAIRun.objects.filter(pk=lease['run_id']).update(lease_expires_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.reply(lease)['reason'],'lease_expired')

    def test_management_command_queues_only_once(self):
        out=StringIO()
        call_command('ai_tickets','retry','--ticket-id',str(self.ticket.pk),'--version',str(self.ticket.version),stdout=out)
        self.assertEqual(json.loads(out.getvalue())['outcome'],'queued')
        out=StringIO()
        call_command('ai_tickets','retry','--ticket-id',str(self.ticket.pk),'--version',str(self.ticket.version),stdout=out)
        self.assertEqual(json.loads(out.getvalue())['outcome'],'already_requested')
