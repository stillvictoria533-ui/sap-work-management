import json
import uuid
from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase,override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient
from .models import User,Ticket,Event,TicketAIRun,Audit,Delivery,Notification
from .services import create_ticket,reply_ticket,change_ticket_status
from .ai import candidate_page,claim,publish,defer,renew,ticket_ai_state,ticket_context
from .ai_test_support import receipt_payload


@override_settings(AI_TICKET_AUTOMATION_ENABLED=True,DINGTALK_ENABLED=False,
 PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class TicketAIFollowupTests(TestCase):
    def setUp(self):
        self.admin=self.user('admin','admin');self.consultant=self.user('consultant','consultant')

    def user(self,name,role):
        return User.objects.create_user(username=name+'@followup.test',email=name+'@followup.test',name=name,
            role=role,password='Test-password-only!',must_change_password=False)

    def ticket(self, kind='问题'):
        return create_ticket(self.consultant,{'request_key':uuid.uuid4(),'title':'MARA 只读排查测试','module':'MM',
            'system':'DS4','kind':kind,'description':'本地测试数据，不需要查询真实 SAP。'},[])[0]

    def accept(self,t):
        t.refresh_from_db()
        return change_ticket_status(self.admin,t.pk,{'version':t.version,'action':'start','reason':'DS4 只读分析测试'})

    def add(self,t,user,body='补充问题'):
        t.refresh_from_db();return reply_ticket(user,t.pk,{'version':t.version,'body':body},[])

    def take(self,t):
        t.refresh_from_db();r=claim(t.pk,t.version);self.assertEqual(r['outcome'],'claimed');return r

    def payload(self):
        return {'category':'query','body':'测试回答：依据测试证据说明。','evidence':[
            {'system':'DS4','client':'110','reference':'仅测试夹具，非真实 SAP 查询'}]}

    def send(self,r,p=None):
        return publish(r['run_id'],r['lease_token'],p if p is not None else receipt_payload(r))

    def test_no_analysis_before_admin_acceptance_or_direct_status_forgery(self):
        t=self.ticket();self.add(t,self.consultant)
        self.assertEqual(TicketAIRun.objects.count(),0)
        self.assertEqual(candidate_page()['results'],[])
        self.assertEqual(claim(t.pk,t.version)['reason'],'not_in_progress')
        t.status='in-progress';t.save()
        self.assertEqual(claim(t.pk,t.version)['reason'],'not_accepted')
        self.assertFalse(TicketAIRun.objects.exists())

    def test_only_problem_type_is_eligible_for_ai_reply(self):
        for kind in ['配置', '开发', '报表', '接口', '其他']:
            with self.subTest(kind=kind):
                t=self.accept(self.ticket(kind))
                self.assertEqual(ticket_ai_state(t)['state'],'unsupported_kind')
                self.assertFalse(TicketAIRun.objects.filter(ticket=t).exists())
                self.assertNotIn(t.pk,[item['id'] for item in candidate_page()['results']])
                self.assertEqual(claim(t.pk,t.version)['reason'],'unsupported_kind')
                self.add(t,self.consultant,'补充信息')
                self.assertFalse(TicketAIRun.objects.filter(ticket=t).exists())

    def test_changed_type_blocks_queued_and_inflight_ai_replies(self):
        queued=self.accept(self.ticket())
        queued.kind='开发';queued.save(update_fields=['kind'])
        self.assertNotIn(queued.pk,[item['id'] for item in candidate_page()['results']])
        self.assertEqual(claim(queued.pk,queued.version)['reason'],'unsupported_kind')
        running=self.accept(self.ticket());lease=self.take(running)
        running.kind='接口';running.save(update_fields=['kind'])
        self.assertEqual(self.send(lease)['reason'],'unsupported_kind')
        self.assertFalse(running.events.filter(is_ai=True).exists())

    def test_accept_first_reply_and_consultant_followup_create_two_rounds(self):
        t=self.accept(self.ticket())
        self.assertEqual(TicketAIRun.objects.get(ticket=t).status,'queued')
        self.assertEqual(ticket_ai_state(t)['state'],'queued')
        r=self.take(t)
        before=(t.status,t.next_actor)
        first=self.send(r);self.assertEqual(first['outcome'],'replied')
        t.refresh_from_db();self.assertEqual((t.status,t.next_actor),before)
        self.assertEqual(candidate_page()['results'],[])
        self.add(t,self.consultant,'新的补充')
        self.assertEqual(TicketAIRun.objects.filter(ticket=t,status='queued').count(),1)
        r2=self.take(t)
        self.assertNotEqual(r['run_id'],r2['run_id'])
        self.assertEqual(self.send(r2)['outcome'],'replied')
        self.assertEqual(t.events.filter(is_ai=True).count(),2)
        self.assertEqual(TicketAIRun.objects.filter(status='replied').count(),2)
        self.assertEqual(Audit.objects.filter(action='ai_ticket_reply').count(),2)
        self.assertEqual(Notification.objects.filter(event__is_ai=True,recipient=self.consultant).count(),2)
        self.assertEqual(Delivery.objects.filter(event__is_ai=True,channel='group_consultant').count(),2)

    def test_consultant_http_submission_queues_followup_in_the_response(self):
        t=self.accept(self.ticket())
        self.assertEqual(self.send(self.take(t))['outcome'],'replied')
        t.refresh_from_db()
        client=APIClient();client.force_login(self.consultant)
        session=client.session;session['version']=self.consultant.session_version;session.save()
        response=client.post(f'/api/tickets/{t.pk}/reply',
            {'version':t.version,'body':'新补充：请再核对 MTART'},format='json')
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.data['ai_followup']['state'],'queued')
        latest=TicketAIRun.objects.filter(ticket=t).order_by('-created_at').first()
        self.assertEqual((latest.status,latest.trigger.body,latest.attempts),
            ('queued','新补充：请再核对 MTART',0))

    def test_publish_retry_is_idempotent_and_run_trigger_unique(self):
        t=self.accept(self.ticket());r=self.take(t);first=self.send(r)
        self.assertEqual(self.send(r)['outcome'],'already_replied')
        self.assertEqual(t.events.filter(is_ai=True).count(),1)
        self.assertEqual(TicketAIRun.objects.get().reply_event_id,first['event_id'])
        run=TicketAIRun.objects.get()
        with self.assertRaises(IntegrityError),transaction.atomic():
            TicketAIRun.objects.create(ticket=t,trigger=run.trigger,accepted_event=run.accepted_event,
                context_version=t.version,lease_expires_at=timezone.now())

    def test_followups_coalesce_to_latest_and_ai_does_not_answer_itself(self):
        t=self.accept(self.ticket());self.send(self.take(t))
        self.add(t,self.consultant,'补充一');self.add(t,self.consultant,'补充二')
        self.assertEqual(len(candidate_page()['results']),1)
        self.assertEqual(TicketAIRun.objects.filter(ticket=t,status='queued').count(),1)
        self.assertEqual(TicketAIRun.objects.filter(ticket=t,status='superseded').count(),1)
        r=self.take(t);run=TicketAIRun.objects.get(pk=r['run_id'])
        self.assertEqual(run.trigger.body,'补充二')
        self.send(r);self.assertEqual(candidate_page()['results'],[])

    def test_human_answer_wins_but_later_consultant_question_restarts(self):
        t=self.accept(self.ticket());r=self.take(t)
        self.add(t,self.admin,'人工已回答')
        self.assertEqual(self.send(r)['reason'],'answered')
        self.assertEqual(candidate_page()['results'],[])
        self.add(t,self.consultant,'下一轮问题')
        self.assertEqual(self.send(self.take(t))['outcome'],'replied')

    def test_inflight_context_changes_cannot_publish_stale_answer(self):
        t=self.accept(self.ticket());r=self.take(t);self.add(t,self.consultant,'更新信息')
        self.assertEqual(TicketAIRun.objects.get(pk=r['run_id']).status,'superseded')
        self.assertEqual(self.send(r)['reason'],'version_changed')
        self.assertFalse(t.events.filter(is_ai=True).exists())
        self.assertEqual(self.send(self.take(t))['outcome'],'replied')

    def test_waiting_done_and_disabled_gate_block_inflight_publish(self):
        for action in ['submit','complete']:
            t=self.accept(self.ticket());r=self.take(t);t.refresh_from_db()
            change_ticket_status(self.admin,t.pk,{'action':action,'version':t.version,'reason':'人工状态变更'})
            self.assertEqual(TicketAIRun.objects.get(pk=r['run_id']).status,'superseded')
            self.assertEqual(self.send(r)['reason'],'not_in_progress')
        t=self.accept(self.ticket());r=self.take(t)
        with override_settings(AI_TICKET_AUTOMATION_ENABLED=False):
            self.assertEqual(candidate_page()['results'],[])
            self.assertEqual(self.send(r)['reason'],'disabled')

    def test_lease_reclaim_invalidates_old_worker(self):
        t=self.accept(self.ticket());old=self.take(t)
        self.assertEqual(claim(t.pk,t.version)['reason'],'running')
        TicketAIRun.objects.update(lease_expires_at=timezone.now()-timedelta(seconds=1))
        new=self.take(t)
        self.assertEqual(old['run_id'],new['run_id'])
        self.assertNotEqual(old['lease_token'],new['lease_token'])
        self.assertEqual(self.send(old)['reason'],'lease_changed')
        self.assertEqual(self.send(new)['outcome'],'replied')

    def test_renew_extends_current_lease_but_never_past_total_deadline(self):
        t=self.accept(self.ticket());lease=self.take(t)
        run=TicketAIRun.objects.get(pk=lease['run_id'])
        deadline=timezone.now()+timedelta(minutes=18)
        run.deadline_at=deadline;run.lease_expires_at=timezone.now()+timedelta(minutes=1)
        run.save(update_fields=['deadline_at','lease_expires_at'])
        self.assertEqual(renew(run.pk,uuid.uuid4())['reason'],'lease_changed')
        result=renew(run.pk,lease['lease_token'])
        self.assertEqual(result['outcome'],'renewed')
        run.refresh_from_db()
        self.assertGreater(run.lease_expires_at,timezone.now()+timedelta(minutes=14))
        self.assertLessEqual(run.lease_expires_at,deadline)
        run.lease_expires_at=timezone.now()-timedelta(seconds=1)
        run.save(update_fields=['lease_expires_at'])
        self.assertEqual(renew(run.pk,lease['lease_token'])['reason'],'lease_expired')

    def test_total_deadline_requires_manual_handling_without_reclaim(self):
        t=self.accept(self.ticket());lease=self.take(t)
        run=TicketAIRun.objects.get(pk=lease['run_id'])
        run.deadline_at=timezone.now()-timedelta(seconds=1)
        run.save(update_fields=['deadline_at'])
        result=renew(run.pk,lease['lease_token'])
        self.assertEqual((result['outcome'],result['reason']),('blocked','time_limit'))
        self.assertEqual(ticket_ai_state(t)['state'],'manual_required')
        self.assertIn('45 分钟上限',ticket_ai_state(t)['message'])
        self.assertEqual(candidate_page()['results'],[])
        self.assertEqual(claim(t.pk,t.version)['reason'],'manual_required')
        self.assertEqual(self.send(lease)['reason'],'time_limit')
        self.assertFalse(t.events.filter(is_ai=True).exists())

    def test_reply_after_deadline_is_rejected_even_without_renew(self):
        t=self.accept(self.ticket());lease=self.take(t)
        TicketAIRun.objects.filter(pk=lease['run_id']).update(deadline_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(self.send(lease)['reason'],'time_limit')
        self.assertEqual(ticket_ai_state(t)['state'],'manual_required')
        self.assertFalse(t.events.filter(is_ai=True).exists())

    def test_scan_marks_abandoned_overlong_run_for_manual_handling(self):
        t=self.accept(self.ticket());lease=self.take(t)
        TicketAIRun.objects.filter(pk=lease['run_id']).update(deadline_at=timezone.now()-timedelta(seconds=1))
        self.assertEqual(candidate_page()['results'],[])
        run=TicketAIRun.objects.get(pk=lease['run_id'])
        self.assertEqual((run.status,run.reason),('blocked','time_limit'))
        self.assertEqual(claim(t.pk,t.version)['reason'],'manual_required')

    def test_renew_management_command_reads_token_from_stdin(self):
        t=self.accept(self.ticket());lease=self.take(t)
        out=StringIO()
        with patch('desk.management.commands.ai_tickets.sys.stdin',StringIO(json.dumps({
            'lease_token':lease['lease_token']}))):
            call_command('ai_tickets','renew','--run-id',lease['run_id'],stdout=out)
        result=json.loads(out.getvalue())
        self.assertEqual(result['outcome'],'renewed')
        self.assertIn('deadline_at',result)

    def test_first_claim_of_preexisting_accepted_ticket_counts_as_attempt_one(self):
        with override_settings(AI_TICKET_AUTOMATION_ENABLED=False):
            t=self.accept(self.ticket())
        self.assertFalse(TicketAIRun.objects.filter(ticket=t).exists())
        result=self.take(t)
        run=TicketAIRun.objects.get(pk=result['run_id'])
        self.assertEqual(run.attempts,1)

    def test_blocked_retries_are_bounded_and_new_question_can_proceed(self):
        t=self.accept(self.ticket());r=self.take(t)
        defer(r['run_id'],r['lease_token'],'sap_unavailable')
        self.assertEqual(candidate_page()['results'],[])
        for attempt in range(2):
            TicketAIRun.objects.update(retry_after=timezone.now()-timedelta(seconds=1))
            r=self.take(t);defer(r['run_id'],r['lease_token'],'sap_unavailable')
        self.assertEqual(ticket_ai_state(t)['state'],'manual_required')
        self.add(t,self.consultant,'新的信息');self.assertEqual(self.take(t)['outcome'],'claimed')

    def test_clarification_can_request_missing_information_without_fabricating_evidence(self):
        t=self.accept(self.ticket());r=self.take(t)
        result=self.send(r,{'category':'clarification','body':'请补充目标系统和单号。','evidence':[]})
        self.assertEqual(result['outcome'],'replied')
        self.assertTrue(t.events.get(is_ai=True).body.startswith('AI：需要补充信息'))
        self.assertEqual(candidate_page()['results'],[])
        self.add(t,self.consultant,'补充 DS4 对象范围');self.assertEqual(len(candidate_page()['results']),1)

    def test_evidence_validation_and_public_api_cannot_forge_ai_identity(self):
        t=self.accept(self.ticket());r=self.take(t)
        for delta in [{'category':'write'},{'evidence':[]},{'evidence':[{'system':'PS4','client':'110','reference':'bad'}]}, {'body':'x'*10001}]:
            with self.assertRaises(ValidationError):self.send(r,{**self.payload(),**delta})
        client=APIClient();client.force_login(self.consultant);s=client.session;s['version']=self.consultant.session_version;s.save()
        response=client.post(f'/api/tickets/{t.pk}/reply',{'body':'普通消息','version':t.version,'is_ai':True,'actor_role':'ai'},format='json')
        self.assertEqual(response.status_code,200)
        self.assertFalse(t.events.filter(is_ai=True).exists())

    def test_old_ai_replies_are_preserved_and_only_new_followups_trigger(self):
        t=self.accept(self.ticket())
        Event.objects.create(ticket=t,actor=self.admin,actor_name='AI · SAP 助手',actor_role='ai',kind='reply',body='历史回复',is_ai=True,version=t.version)
        self.assertEqual(candidate_page()['results'],[])
        self.add(t,self.consultant,'恢复后的补充')
        self.assertEqual(self.send(self.take(t))['outcome'],'replied')
        self.assertEqual(t.events.filter(is_ai=True).count(),2)

    def test_scan_cursor_advances_past_skipped_tickets(self):
        t=self.accept(self.ticket());self.add(t,self.admin)
        second=self.accept(self.ticket())
        page=candidate_page(limit=1)
        self.assertEqual(page['results'],[]);self.assertTrue(page['has_more'])
        self.assertEqual(candidate_page(after=page['next_after_id'])['results'][0]['id'],second.pk)

    def test_cli_disabled_and_context_boundaries(self):
        out=StringIO()
        with override_settings(AI_TICKET_AUTOMATION_ENABLED=False):
            call_command('ai_tickets','list',stdout=out)
        self.assertFalse(json.loads(out.getvalue())['enabled'])
        t=self.accept(self.ticket())
        for i in range(4):self.add(t,self.consultant,str(i)*10000)
        context=ticket_context(t)
        self.assertEqual(context['system'],'DS4')
        self.assertTrue(context['incomplete_context'])
        self.assertLessEqual(sum(len(e['body']) for e in context['events']),30000)
        self.assertEqual(context['events'][-1]['body'],'3'*10000)
